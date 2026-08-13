from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import CodingAgent, GeminiClient, ProjectStore, make_project_id

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
PROJECTS_DIR = BASE_DIR / "projects"

app = FastAPI(
    title="Vibe Coding Platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = ProjectStore(PROJECTS_DIR)

api_key = os.getenv("GEMINI_API_KEY", "")
model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

llm = GeminiClient(api_key, model) if api_key else None
agent = CodingAgent(store, llm)


class ProjectCreate(BaseModel):
    name: str = Field(default="새 프로젝트", min_length=1, max_length=80)


class IdeaRequest(BaseModel):
    project_id: str
    idea: str = Field(min_length=1, max_length=20_000)


class StructureRequest(BaseModel):
    project_id: str
    configuration: dict[str, Any]


class FeatureRequest(BaseModel):
    project_id: str
    configuration: dict[str, Any]


class ChangeRequest(BaseModel):
    project_id: str
    request: str = Field(min_length=1, max_length=20_000)


class RepairRequest(BaseModel):
    project_id: str
    original_request: str
    issue: str


class ActionRequest(BaseModel):
    project_id: str
    actions: list[dict[str, Any]]


def ensure_project(project_id: str) -> dict[str, Any]:
    try:
        return store.load_state(project_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "aiConfigured": bool(llm),
        "model": model,
    }


@app.post("/api/projects")
async def create_project(payload: ProjectCreate) -> dict[str, Any]:
    project_id = make_project_id()
    state = store.load_state(project_id)
    state["project_name"] = payload.name
    state["stage"] = "prompt"
    store.save_state(project_id, state)
    return state


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    state = ensure_project(project_id)
    state["files"] = store.list_files(project_id)
    return state


@app.get("/api/projects/{project_id}/files")
async def list_files(project_id: str) -> list[dict[str, Any]]:
    ensure_project(project_id)
    return store.list_files(project_id)


@app.get("/api/projects/{project_id}/file")
async def read_file(project_id: str, path: str) -> dict[str, str]:
    ensure_project(project_id)
    try:
        safe = store.safe_file(project_id, path)
        if not safe.exists() or not safe.is_file():
            raise HTTPException(status_code=404, detail="파일이 없습니다.")
        return {
            "path": path,
            "content": safe.read_text(encoding="utf-8"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyze")
async def analyze(payload: IdeaRequest) -> dict[str, Any]:
    state = ensure_project(payload.project_id)
    try:
        result = await agent.analyze_idea(payload.idea)
        state["app_configuration"] = result
        state["project_name"] = result.get("projectName") or state["project_name"]
        state["stage"] = "structure"
        store.save_state(payload.project_id, state)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/features")
async def features(payload: FeatureRequest) -> dict[str, Any]:
    state = ensure_project(payload.project_id)
    try:
        result = await agent.design_features(payload.configuration)
        state["app_features"] = result
        state["stage"] = "coding"
        store.save_state(payload.project_id, state)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/code")
async def code(payload: ChangeRequest) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        plan = await agent.create_plan(
            payload.project_id,
            payload.request,
        )
        if plan.get("requiresStructureChange"):
            return {
                "status": "structure_change_required",
                "plan": plan,
            }

        actions = await agent.generate_actions(
            payload.project_id,
            payload.request,
            plan,
        )
        results = await agent.apply_actions(
            payload.project_id,
            actions,
        )
        tests = await agent.run_test_suite(payload.project_id)

        state = ensure_project(payload.project_id)
        state["implementation_plan"] = plan
        state["stage"] = "test"
        store.save_state(payload.project_id, state)

        return {
            "status": "completed",
            "plan": plan,
            "actions": [action.__dict__ for action in actions],
            "results": results,
            "tests": tests,
        }
    except Exception as exc:
        state = ensure_project(payload.project_id)
        state["errors"].append(str(exc))
        store.save_state(payload.project_id, state)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/change")
async def change(payload: ChangeRequest) -> dict[str, Any]:
    return await code(payload)


@app.post("/api/test")
async def test(project_id: str) -> dict[str, Any]:
    ensure_project(project_id)
    try:
        result = await agent.run_test_suite(project_id)
        state = ensure_project(project_id)
        state["stage"] = "review"
        store.save_state(project_id, state)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/review")
async def review(project_id: str, original_request: str) -> dict[str, Any]:
    ensure_project(project_id)
    try:
        return await agent.review(project_id, original_request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/repair")
async def repair(payload: RepairRequest) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return await agent.repair(
            payload.project_id,
            payload.original_request,
            payload.issue,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/actions")
async def actions(payload: ActionRequest) -> dict[str, Any]:
    ensure_project(payload.project_id)

    parsed = []
    for item in payload.actions:
        from .agent import Action
        parsed.append(
            Action(
                type=item.get("type", ""),
                path=item.get("path"),
                content=item.get("content"),
                command=item.get("command"),
                reason=item.get("reason", ""),
            )
        )

    try:
        results = await agent.apply_actions(
            payload.project_id,
            parsed,
        )
        return {
            "results": results,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# frontend
app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIR),
    name="assets",
)


@app.get("/{full_path:path}")
async def frontend(full_path: str) -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
