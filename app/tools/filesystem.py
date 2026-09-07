"""Filesystem / repository-inspection tools.

These operate on a local workspace directory (typically the sandbox's
bind-mounted repo). They are READ_ONLY. All paths are resolved relative to the
provided ``root`` and prevented from escaping above it.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.models.domain import ActionClass, ToolResult
from app.tools.base import Tool

MAX_READ_BYTES = 256 * 1024  # 256 KiB


def _resolve(root: str, rel: str) -> Path:
    base = Path(root).resolve()
    p = (base / rel).resolve()
    if not p.is_relative_to(base):
        raise ValueError(f"Path escapes root: {rel!r}")
    return p


def _safe_run(tool: str, fn) -> ToolResult:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - report any tool error structurally
        return ToolResult(tool=tool, ok=False, exit_code=1, error=str(exc))


# --------------------------------------------------------------------------- #
# list_files
# --------------------------------------------------------------------------- #
class ListFilesParams(BaseModel):
    root: str = Field(description="Workspace root directory")
    path: str = Field(default=".", description="Directory to list, relative to root")
    max_depth: int = Field(default=3, description="Maximum recursion depth")


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files in the repository, optionally recursive."
    params = ListFilesParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict[str, str]) -> ToolResult:
        def _run() -> ToolResult:
            start = _resolve(args["root"], args["path"])
            if not start.is_dir():
                err = f"Not a directory: {start}"
                return ToolResult(tool=self.name, ok=False, exit_code=1, error=err)
            max_depth = int(args.get("max_depth", 3))
            entries: list[str] = []
            candidates = (
                sorted(start.rglob("*"))
                if max_depth and max_depth > 1
                else sorted(start.iterdir())
            )
            for p in candidates:
                rel = p.relative_to(start)
                if args["path"] == ".":
                    rel = Path(".") / rel
                depth = len(rel.parts)
                if max_depth and depth > max_depth:
                    continue
                if ".git" in rel.parts:
                    continue
                entries.append((rel.as_posix() + "/") if p.is_dir() else rel.as_posix())
            return ToolResult(tool=self.name, data={"path": args["path"], "files": entries})

        return _safe_run(self.name, _run)


# --------------------------------------------------------------------------- #
# read_file
# --------------------------------------------------------------------------- #
class ReadFileParams(BaseModel):
    root: str
    path: str = Field(description="Relative path of the file to read")


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file in the repository."
    params = ReadFileParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict[str, str]) -> ToolResult:
        def _run() -> ToolResult:
            p = _resolve(args["root"], args["path"])
            if not p.is_file():
                return ToolResult(tool=self.name, ok=False, exit_code=1, error=f"Not a file: {p}")
            data = p.read_bytes()
            truncated = len(data) > MAX_READ_BYTES
            if truncated:
                data = data[:MAX_READ_BYTES]
            text = data.decode("utf-8", errors="replace")
            return ToolResult(
                tool=self.name,
                data={"path": args["path"], "content": text, "truncated": truncated},
            )

        return _safe_run(self.name, _run)


# --------------------------------------------------------------------------- #
# search_code
# --------------------------------------------------------------------------- #
class SearchCodeParams(BaseModel):
    root: str
    pattern: str = Field(description="Substring or regex pattern to search for")
    path: str = Field(default=".", description="Directory to search, relative to root")
    file_pattern: str = Field(default="*.py", description="Glob of files to search")


class SearchCodeTool(Tool):
    name = "search_code"
    description = "Search for a pattern across repository files (simple substring match)."
    params = SearchCodeParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict[str, str]) -> ToolResult:
        def _run() -> ToolResult:
            import re

            start = _resolve(args["root"], args["path"])
            pattern = args["pattern"]
            file_pattern = args.get("file_pattern", "*.py")
            matches: list[dict] = []
            for p in start.rglob(file_pattern):
                if ".git" in p.parts or not p.is_file():
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if re.search(pattern, line):
                        rel_path = str(p.relative_to(start))
                        matches.append(
                            {"file": rel_path, "line": lineno, "text": line.strip()}
                        )
            return ToolResult(tool=self.name, data={"matches": matches})

        return _safe_run(self.name, _run)


# --------------------------------------------------------------------------- #
# get_diff
# --------------------------------------------------------------------------- #
class GetDiffParams(BaseModel):
    root: str
    path: str = Field(default=".", description="File or repo root to diff against git HEAD")


class GetDiffTool(Tool):
    name = "get_diff"
    description = "Get a git diff of uncommitted working-tree changes (read-only)."
    params = GetDiffParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict[str, str]) -> ToolResult:
        def _run() -> ToolResult:
            base = Path(args["root"]).resolve()
            # Run against the local git repo; the sandbox shares the filesystem.
            from app.tools.shell import ShellTool

            shell = ShellTool()
            res = shell.run(
                {"command": f"git -C {base} diff -- {args['path']}", "root": str(base)}
            )
            return ToolResult(
                tool=self.name,
                data={"diff": res.stdout, "exit_code": res.exit_code},
                ok=res.ok,
            )

        return _safe_run(self.name, _run)
