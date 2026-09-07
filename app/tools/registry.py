"""Assemble the default tool registry."""

from __future__ import annotations

from app.tools.base import Tool, ToolRegistry
from app.tools.filesystem import GetDiffTool, ListFilesTool, ReadFileTool, SearchCodeTool
from app.tools.github import (
    CreateBranchTool,
    CreatePullRequestTool,
    GetCommitTool,
    GetRepositoryTool,
    GetWorkflowLogsTool,
    GetWorkflowRunTool,
)
from app.tools.shell import ShellTool
from app.tools.tests import RunTestsTool


def default_tools() -> list[Tool]:
    return [
        # filesystem / repository
        ListFilesTool(),
        ReadFileTool(),
        SearchCodeTool(),
        GetDiffTool(),
        # execution
        ShellTool(),
        RunTestsTool(),
        # github (stubs until Phase 7)
        GetWorkflowRunTool(),
        GetWorkflowLogsTool(),
        GetRepositoryTool(),
        GetCommitTool(),
        CreateBranchTool(),
        CreatePullRequestTool(),
    ]


def default_registry() -> ToolRegistry:
    return ToolRegistry(default_tools())
