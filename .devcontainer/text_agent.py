from pathlib import Path

from backend.agent import ProjectStore


def test_project_store_writes_and_reads(tmp_path: Path):
    store = ProjectStore(tmp_path)
    project_id = "test_project"

    state = store.load_state(project_id)
    assert state["project_id"] == project_id

    store.write(project_id, "index.html", "<h1>Hello</h1>")
    files = store.list_files(project_id)

    assert any(item["path"] == "index.html" for item in files)


def test_state_roundtrip(tmp_path: Path):
    store = ProjectStore(tmp_path)
    project_id = "roundtrip"

    state = store.load_state(project_id)
    state["project_name"] = "Test App"
    store.save_state(project_id, state)

    loaded = store.load_state(project_id)
    assert loaded["project_name"] == "Test App"
