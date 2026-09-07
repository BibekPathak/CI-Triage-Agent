"""Unit tests for the tool registry and filesystem tools."""

import pytest

from app.tools.base import ToolRegistry
from app.tools.filesystem import ReadFileTool
from app.tools.registry import default_registry


@pytest.fixture()
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "utils.py").write_text("def add(a, b):\n    return a + b\n")
    content = "from src.utils import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    (tmp_path / "test_utils.py").write_text(content)
    return tmp_path


def test_registry_registers_all_tools():
    reg = default_registry()
    assert "read_file" in reg.names()
    assert "run_command" in reg.names()
    assert "create_pull_request" in reg.names()


def test_register_duplicate_ok_but_overwrites():
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(ReadFileTool())
    assert reg.names() == ["read_file"]


def test_registry_unknown_tool_raises():
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.execute("nope", {})


def test_tool_definitions_include_schema():
    defs = default_registry().definitions()
    by_name = {d["name"]: d for d in defs}
    assert by_name["read_file"]["action_class"] == "read_only"
    assert "parameters" in by_name["read_file"]


def test_list_files(repo):
    reg = default_registry()
    res = reg.execute("list_files", {"root": str(repo), "max_depth": 2})
    assert res.ok
    names = set(res.data["files"])
    assert "src/utils.py" in names
    assert "test_utils.py" in names


def test_read_file(repo):
    reg = default_registry()
    res = reg.execute("read_file", {"root": str(repo), "path": "src/utils.py"})
    assert res.ok
    assert "def add" in res.data["content"]


def test_read_file_rejects_escape(repo):
    reg = default_registry()
    res = reg.execute("read_file", {"root": str(repo), "path": "../../etc/passwd"})
    assert res.ok is False
    assert "escapes root" in (res.error or "")


def test_search_code(repo):
    reg = default_registry()
    res = reg.execute(
        "search_code",
        {"root": str(repo), "pattern": "def add", "file_pattern": "*.py"},
    )
    assert res.ok
    files = {m["file"] for m in res.data["matches"]}
    assert "src/utils.py" in files
