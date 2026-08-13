from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(cleaned[start:end + 1])
        if isinstance(value, dict):
            return value

    raise ValueError("AI 응답에서 유효한 JSON 객체를 찾지 못했습니다.")


@dataclass
class Action:
    type: str
    path: str | None = None
    content: str | None = None
    command: list[str] | None = None
    reason: str = ""


class GeminiClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def generate_json(self, system: str, prompt: str) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.15,
            },
        }

        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=120) as client:
            for attempt in range(3):
                try:
                    response = await client.post(
                        url,
                        params={"key": self.api_key},
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                    text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    if not text:
                        raise RuntimeError("Gemini 응답이 비어 있습니다.")
                    return safe_json(text)
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(1 + attempt)

        raise RuntimeError(f"Gemini 호출 실패: {last_error}") from last_error


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", project_id):
            raise ValueError("잘못된 project_id입니다.")
        path = (self.root / project_id).resolve()
        path.relative_to(self.root)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def state_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / ".vibe" / "state.json"

    def load_state(self, project_id: str) -> dict[str, Any]:
        path = self.state_path(project_id)
        if not path.exists():
            state = {
                "project_id": project_id,
                "project_name": "새 프로젝트",
                "stage": "prompt",
                "app_configuration": {},
                "app_features": {},
                "implementation_plan": {},
                "files": [],
                "tests": [],
                "reviews": [],
                "errors": [],
                "history": [],
                "updated_at": now(),
            }
            self.save_state(project_id, state)
            return state
        return json.loads(path.read_text(encoding="utf-8"))

    def save_state(self, project_id: str, state: dict[str, Any]) -> None:
        path = self.state_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = now()
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def safe_file(self, project_id: str, relative: str) -> Path:
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("허용되지 않는 파일 경로입니다.")
        path = (self.project_dir(project_id) / relative).resolve()
        path.relative_to(self.project_dir(project_id))
        if ".vibe" in path.parts:
            raise ValueError(".vibe 내부 파일은 Agent가 직접 수정할 수 없습니다.")
        return path

    def list_files(self, project_id: str) -> list[dict[str, Any]]:
        base = self.project_dir(project_id)
        result: list[dict[str, Any]] = []
        ignored = {".vibe", "__pycache__", "node_modules", ".git"}

        for path in base.rglob("*"):
            if not path.is_file() or any(part in ignored for part in path.parts):
                continue
            rel = path.relative_to(base).as_posix()
            result.append({
                "path": rel,
                "size": path.stat().st_size,
            })
        return sorted(result, key=lambda item: item["path"])

    def read_files(self, project_id: str, max_chars: int = 120_000) -> list[dict[str, str]]:
        base = self.project_dir(project_id)
        result: list[dict[str, str]] = []
        total = 0
        for item in self.list_files(project_id):
            path = base / item["path"]
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if total + len(content) > max_chars:
                break
            result.append({"path": item["path"], "content": content})
            total += len(content)
        return result

    def write(self, project_id: str, relative: str, content: str) -> None:
        path = self.safe_file(project_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def delete(self, project_id: str, relative: str) -> None:
        path = self.safe_file(project_id, relative)
        if path.exists():
            if path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)

    def backup(self, project_id: str, relative: str) -> Path | None:
        source = self.safe_file(project_id, relative)
        if not source.exists():
            return None
        backup_root = (
            self.project_dir(project_id)
            / ".vibe"
            / "backups"
            / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        )
        destination = backup_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination


class CodingAgent:
    def __init__(self, store: ProjectStore, llm: GeminiClient | None) -> None:
        self.store = store
        self.llm = llm

    async def analyze_idea(self, idea: str) -> dict[str, Any]:
        if not self.llm:
            raise RuntimeError("Gemini API가 연결되지 않았습니다.")
        system = """
당신은 Vibe Coding의 앱 구성 AI입니다.
코드를 작성하지 말고 앱 구조를 설계합니다.
추측은 최소화하고 사용자의 의도를 명확하게 보존합니다.
반드시 JSON만 반환합니다.

{
  "projectName": "...",
  "summary": "...",
  "targetAudience": [],
  "coreFeatures": [],
  "screens": [],
  "dataModels": [],
  "userFlow": [],
  "openQuestions": [],
  "compiledPrompt": "개발 가능한 상세 명세"
}
"""
        return await self.llm.generate_json(system, f"사용자 아이디어:\n{idea}")

    async def design_features(
        self,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.llm:
            raise RuntimeError("Gemini API가 연결되지 않았습니다.")
        system = """
당신은 Vibe Coding의 앱 기능 AI입니다.
앱 구성 결과를 실제 구현 가능한 기능 명세로 변환합니다.
반드시 JSON만 반환합니다.

{
  "features": [
    {
      "id": "...",
      "name": "...",
      "trigger": "...",
      "inputs": [],
      "outputs": [],
      "process": [],
      "stateChanges": [],
      "exceptions": [],
      "dependencies": []
    }
  ],
  "globalRules": [],
  "acceptanceCriteria": []
}
"""
        return await self.llm.generate_json(
            system,
            json.dumps(configuration, ensure_ascii=False, indent=2),
        )

    async def create_plan(
        self,
        project_id: str,
        request: str,
    ) -> dict[str, Any]:
        state = self.store.load_state(project_id)
        files = self.store.read_files(project_id)
        system = """
당신은 Vibe Coding의 수석 개발 에이전트입니다.
기존 프로젝트를 최대한 보존합니다.
무조건 재작성하지 않습니다.
필요한 파일만 수정/생성합니다.
구조 변경이 필요하면 requiresStructureChange=true로 표시합니다.
반드시 JSON만 반환합니다.

{
  "requiresStructureChange": false,
  "structureProblem": "",
  "recommendedStage": "",
  "changedFiles": [],
  "createdFiles": [],
  "deletedFiles": [],
  "plan": [],
  "impact": ""
}
"""
        prompt = f"""
사용자 요청:
{request}

공유 프로젝트 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

현재 파일:
{json.dumps(files, ensure_ascii=False, indent=2)}
"""
        return await self.llm.generate_json(system, prompt) if self.llm else {}

    async def generate_actions(
        self,
        project_id: str,
        request: str,
        plan: dict[str, Any],
    ) -> list[Action]:
        if not self.llm:
            raise RuntimeError("Gemini API가 연결되지 않았습니다.")
        state = self.store.load_state(project_id)
        files = self.store.read_files(project_id)
        system = """
당신은 Vibe Coding의 실제 구현 Agent입니다.
다음 작업만 사용합니다.

create_file
modify_file
delete_file
run
test

중요 규칙:
- project_root 밖 경로 금지
- .vibe 수정 금지
- 기존 기능 삭제 금지
- 필요하지 않은 파일 수정 금지
- shell 문자열 금지
- command는 배열
- 파일 수정은 전체 파일 내용을 반환
- 모든 작업에 reason을 넣음

반드시 JSON:
{
  "actions": [
    {
      "type": "create_file",
      "path": "index.html",
      "content": "...",
      "reason": "..."
    }
  ]
}
"""
        prompt = f"""
사용자 요청:
{request}

작업 계획:
{json.dumps(plan, ensure_ascii=False, indent=2)}

공유 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

현재 파일:
{json.dumps(files, ensure_ascii=False, indent=2)}
"""
        data = await self.llm.generate_json(system, prompt)
        actions = []
        for raw in data.get("actions", []):
            if not isinstance(raw, dict):
                continue
            actions.append(
                Action(
                    type=raw.get("type", ""),
                    path=raw.get("path"),
                    content=raw.get("content"),
                    command=raw.get("command"),
                    reason=raw.get("reason", ""),
                )
            )
        return actions

    def validate_actions(
        self,
        project_id: str,
        actions: list[Action],
    ) -> list[str]:
        allowed = {"create_file", "modify_file", "delete_file", "run", "test"}
        errors: list[str] = []
        for i, action in enumerate(actions, 1):
            if action.type not in allowed:
                errors.append(f"Action {i}: 지원하지 않는 type")
            if action.type in {"create_file", "modify_file", "delete_file"}:
                if not action.path:
                    errors.append(f"Action {i}: path가 없습니다.")
                else:
                    try:
                        self.store.safe_file(project_id, action.path)
                    except Exception as exc:
                        errors.append(f"Action {i}: {exc}")
            if action.type in {"run", "test"}:
                if not action.command:
                    errors.append(f"Action {i}: command가 없습니다.")
                elif not isinstance(action.command, list) or not all(
                    isinstance(x, str) for x in action.command
                ):
                    errors.append(f"Action {i}: command는 문자열 배열이어야 합니다.")
        return errors

    async def apply_actions(
        self,
        project_id: str,
        actions: list[Action],
    ) -> list[dict[str, Any]]:
        errors = self.validate_actions(project_id, actions)
        if errors:
            raise ValueError("\n".join(errors))

        results: list[dict[str, Any]] = []
        state = self.store.load_state(project_id)

        for action in actions:
            if action.type in {"create_file", "modify_file"}:
                if action.path and action.type == "modify_file":
                    self.store.backup(project_id, action.path)
                self.store.write(project_id, action.path or "", action.content or "")
                results.append({
                    "type": action.type,
                    "path": action.path,
                    "ok": True,
                })

            elif action.type == "delete_file":
                self.store.backup(project_id, action.path or "")
                self.store.delete(project_id, action.path or "")
                results.append({
                    "type": action.type,
                    "path": action.path,
                    "ok": True,
                })

            elif action.type in {"run", "test"}:
                command = action.command or []
                result = await self._run_command(project_id, command)
                results.append({
                    "type": action.type,
                    "command": command,
                    **result,
                })

        state["files"] = self.store.list_files(project_id)
        state["history"].append({
            "timestamp": now(),
            "actions": [asdict(action) for action in actions],
        })
        self.store.save_state(project_id, state)
        return results

    async def _run_command(
        self,
        project_id: str,
        command: list[str],
        timeout: int = 60,
    ) -> dict[str, Any]:
        base = self.store.project_dir(project_id)

        allowed_exec = {
            "python",
            "python3",
            "pytest",
        }
        executable = Path(command[0]).name.lower()

        if executable not in allowed_exec:
            return {
                "ok": False,
                "returnCode": -1,
                "stdout": "",
                "stderr": (
                    "보안상 현재 기본 Agent는 "
                    "python/python3/pytest 실행만 허용합니다."
                ),
            }

        process = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=base,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )

        return {
            "ok": process.returncode == 0,
            "returnCode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }

    async def run_test_suite(self, project_id: str) -> dict[str, Any]:
        base = self.store.project_dir(project_id)
        python_files = [p for p in base.rglob("*.py") if ".vibe" not in p.parts]

        checks = []
        for path in python_files:
            rel = path.relative_to(base).as_posix()
            checks.append(
                await self._run_command(
                    project_id,
                    [sys.executable, "-m", "py_compile", rel],
                )
            )

        tests_dir = base / "tests"
        if tests_dir.exists():
            checks.append(
                await self._run_command(
                    project_id,
                    [sys.executable, "-m", "pytest", "-q"],
                    timeout=120,
                )
            )

        passed = all(item["ok"] for item in checks) if checks else True
        result = {
            "passed": passed,
            "checks": checks,
            "timestamp": now(),
        }

        state = self.store.load_state(project_id)
        state["tests"].append(result)
        self.store.save_state(project_id, state)
        return result

    async def review(
        self,
        project_id: str,
        original_request: str,
    ) -> dict[str, Any]:
        if not self.llm:
            raise RuntimeError("Gemini API가 연결되지 않았습니다.")
        state = self.store.load_state(project_id)
        files = self.store.read_files(project_id)

        system = """
당신은 완성된 웹앱을 평가하는 QA/코드 리뷰 AI입니다.
기능, 요구사항 충족, UI 안정성, 반응형, 오류 처리, 코드 구조, 성능을 평가합니다.
실제로 확인할 수 없는 내용은 추측하지 말고 '확인 필요'로 표시합니다.
반드시 JSON만 반환합니다.

{
  "score": 0,
  "categories": {
    "functionality": {"score": 0, "status": "pass|warn|fail", "issues": []},
    "requirements": {"score": 0, "status": "pass|warn|fail", "issues": []},
    "ui": {"score": 0, "status": "pass|warn|fail", "issues": []},
    "responsive": {"score": 0, "status": "pass|warn|fail", "issues": []},
    "errorHandling": {"score": 0, "status": "pass|warn|fail", "issues": []},
    "codeStructure": {"score": 0, "status": "pass|warn|fail", "issues": []},
    "performance": {"score": 0, "status": "pass|warn|fail", "issues": []}
  },
  "recommendedFixes": []
}
"""
        prompt = f"""
원래 요구사항:
{original_request}

상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

현재 파일:
{json.dumps(files, ensure_ascii=False, indent=2)}
"""
        result = await self.llm.generate_json(system, prompt)
        state["reviews"].append({
            "timestamp": now(),
            "result": result,
        })
        self.store.save_state(project_id, state)
        return result

    async def repair(
        self,
        project_id: str,
        request: str,
        issue: str,
    ) -> dict[str, Any]:
        plan = await self.create_plan(
            project_id,
            f"""
원래 요청:
{request}

검수에서 발견된 문제:
{issue}

이 문제를 최소 변경으로 수정해줘.
""",
        )

        if plan.get("requiresStructureChange"):
            return {
                "requiresStructureChange": True,
                "plan": plan,
                "actions": [],
            }

        actions = await self.generate_actions(
            project_id,
            f"{request}\n\n검수 문제:\n{issue}",
            plan,
        )
        results = await self.apply_actions(project_id, actions)
        tests = await self.run_test_suite(project_id)
        return {
            "requiresStructureChange": False,
            "plan": plan,
            "actions": [asdict(action) for action in actions],
            "results": results,
            "tests": tests,
        }


def make_project_id() -> str:
    return uuid.uuid4().hex[:10]
