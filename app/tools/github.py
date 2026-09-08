"""GitHub tools backed by the ``gh`` CLI.

Read-only tools (workflow run, logs, repo, commit) are READ_ONLY.
Write tools (branch, PR) are REMOTE_WRITE and gated by PolicyEngine.
"""

from __future__ import annotations

from pydantic import BaseModel, PrivateAttr

from app.github.client import GitHubClient, GitHubError
from app.models.domain import ActionClass, ToolResult
from app.tools.base import Tool


def _gh_client() -> GitHubClient:
    """Lazy import to avoid circular dependency at module load."""
    return GitHubClient()


class GetWorkflowRunParams(BaseModel):
    repository: str
    workflow_run_id: str


class GetWorkflowRunTool(Tool):
    name = "get_workflow_run"
    description = "Fetch metadata for a GitHub Actions workflow run."
    params = GetWorkflowRunParams
    action_class = ActionClass.READ_ONLY

    _client: GitHubClient = PrivateAttr(default_factory=_gh_client)

    def run(self, args: dict) -> ToolResult:
        try:
            data = self._client.get_workflow_run(
                args["repository"], args["workflow_run_id"]
            )
            return ToolResult(
                tool=self.name,
                ok=True,
                exit_code=0,
                data=data,
            )
        except GitHubError as exc:
            return ToolResult(tool=self.name, ok=False, exit_code=1, error=str(exc))


class GetWorkflowLogsParams(BaseModel):
    repository: str
    workflow_run_id: str


class GetWorkflowLogsTool(Tool):
    name = "get_workflow_logs"
    description = "Retrieve the log output of a GitHub Actions workflow run."
    params = GetWorkflowLogsParams
    action_class = ActionClass.READ_ONLY

    _client: GitHubClient = PrivateAttr(default_factory=_gh_client)

    def run(self, args: dict) -> ToolResult:
        try:
            logs = self._client.get_workflow_logs(
                args["repository"], args["workflow_run_id"]
            )
            return ToolResult(
                tool=self.name,
                ok=True,
                exit_code=0,
                stdout=logs,
            )
        except GitHubError as exc:
            return ToolResult(tool=self.name, ok=False, exit_code=1, error=str(exc))


class GetRepositoryParams(BaseModel):
    repository: str


class GetRepositoryTool(Tool):
    name = "get_repository"
    description = "Inspect a GitHub repository (metadata, default branch, languages)."
    params = GetRepositoryParams
    action_class = ActionClass.READ_ONLY

    _client: GitHubClient = PrivateAttr(default_factory=_gh_client)

    def run(self, args: dict) -> ToolResult:
        try:
            data = self._client.get_repository(args["repository"])
            return ToolResult(
                tool=self.name,
                ok=True,
                exit_code=0,
                data=data,
            )
        except GitHubError as exc:
            return ToolResult(tool=self.name, ok=False, exit_code=1, error=str(exc))


class GetCommitParams(BaseModel):
    repository: str
    sha: str


class GetCommitTool(Tool):
    name = "get_commit"
    description = "Inspect a commit in a GitHub repository."
    params = GetCommitParams
    action_class = ActionClass.READ_ONLY

    _client: GitHubClient = PrivateAttr(default_factory=_gh_client)

    def run(self, args: dict) -> ToolResult:
        try:
            data = self._client.get_commit(args["repository"], args["sha"])
            return ToolResult(
                tool=self.name,
                ok=True,
                exit_code=0,
                data=data,
            )
        except GitHubError as exc:
            return ToolResult(tool=self.name, ok=False, exit_code=1, error=str(exc))


class CreateBranchParams(BaseModel):
    repository: str
    branch: str
    base_branch: str = "main"
    base_sha: str | None = None


class CreateBranchTool(Tool):
    name = "create_branch"
    description = "Create a branch on a repository. Requires approval (REMOTE_WRITE)."
    params = CreateBranchParams
    action_class = ActionClass.REMOTE_WRITE

    _client: GitHubClient = PrivateAttr(default_factory=_gh_client)

    def run(self, args: dict) -> ToolResult:
        try:
            data = self._client.create_branch(
                repo=args["repository"],
                branch=args["branch"],
                base=args["base_branch"],
                base_sha=args.get("base_sha"),
            )
            return ToolResult(
                tool=self.name,
                ok=True,
                exit_code=0,
                data=data,
            )
        except GitHubError as exc:
            return ToolResult(tool=self.name, ok=False, exit_code=1, error=str(exc))


class CreatePullRequestParams(BaseModel):
    repository: str
    title: str
    head: str
    base: str = "main"
    body: str = ""


class CreatePullRequestTool(Tool):
    name = "create_pull_request"
    description = "Open a pull request. Requires approval (REMOTE_WRITE)."
    params = CreatePullRequestParams
    action_class = ActionClass.REMOTE_WRITE

    _client: GitHubClient = PrivateAttr(default_factory=_gh_client)

    def run(self, args: dict) -> ToolResult:
        try:
            data = self._client.create_pull_request(
                repo=args["repository"],
                title=args["title"],
                head=args["head"],
                base=args["base"],
                body=args.get("body", ""),
            )
            return ToolResult(
                tool=self.name,
                ok=True,
                exit_code=0,
                data=data,
            )
        except GitHubError as exc:
            return ToolResult(tool=self.name, ok=False, exit_code=1, error=str(exc))
