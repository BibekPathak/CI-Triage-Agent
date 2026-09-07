"""GitHub tools (Phase 2 stub).

Full GitHub API integration lands in Phase 7. For now these tools exist so the
registry surface is stable and the orchestrator can be tested end-to-end with a
shared interface. The stub returns a ``not_implemented`` style result, but
write tools still declare their REMOTE_WRITE action class for policy gating.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.models.domain import ActionClass, ToolResult
from app.tools.base import Tool


class GitHubStubMixin:
    def _stub(self, name: str) -> ToolResult:
        return ToolResult(
            tool=name,
            ok=False,
            exit_code=1,
            error=f"GitHub tool '{name}' not implemented yet (Phase 7)",
        )


class GetWorkflowRunParams(BaseModel):
    repository: str
    workflow_run_id: str


class GetWorkflowRunTool(GitHubStubMixin, Tool):
    name = "get_workflow_run"
    description = "Fetch metadata for a GitHub Actions workflow run."
    params = GetWorkflowRunParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict) -> ToolResult:
        return self._stub(self.name)


class GetWorkflowLogsParams(BaseModel):
    repository: str
    workflow_run_id: str


class GetWorkflowLogsTool(GitHubStubMixin, Tool):
    name = "get_workflow_logs"
    description = "Retrieve the live log output of a GitHub Actions workflow run."
    params = GetWorkflowLogsParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict) -> ToolResult:
        return self._stub(self.name)


class GetRepositoryParams(BaseModel):
    repository: str


class GetRepositoryTool(GitHubStubMixin, Tool):
    name = "get_repository"
    description = "Inspect a GitHub repository (metadata, default branch, languages)."
    params = GetRepositoryParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict) -> ToolResult:
        return self._stub(self.name)


class GetCommitParams(BaseModel):
    repository: str
    sha: str


class GetCommitTool(GitHubStubMixin, Tool):
    name = "get_commit"
    description = "Inspect a commit in a GitHub repository."
    params = GetCommitParams
    action_class = ActionClass.READ_ONLY

    def run(self, args: dict) -> ToolResult:
        return self._stub(self.name)


class CreateBranchParams(BaseModel):
    repository: str
    branch: str
    base_branch: str = "main"


class CreateBranchTool(GitHubStubMixin, Tool):
    name = "create_branch"
    description = "Create a branch on a repository. Requires approval (REMOTE_WRITE)."
    params = CreateBranchParams
    action_class = ActionClass.REMOTE_WRITE

    def run(self, args: dict) -> ToolResult:
        return self._stub(self.name)


class CreatePullRequestParams(BaseModel):
    repository: str
    title: str
    head: str
    base: str = "main"
    body: str = ""


class CreatePullRequestTool(GitHubStubMixin, Tool):
    name = "create_pull_request"
    description = "Open a pull request. Requires approval (REMOTE_WRITE)."
    params = CreatePullRequestParams
    action_class = ActionClass.REMOTE_WRITE

    def run(self, args: dict) -> ToolResult:
        return self._stub(self.name)
