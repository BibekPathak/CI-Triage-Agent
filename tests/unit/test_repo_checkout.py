"""Tests for repository checkout at an exact revision.

Uses a real local git repository (no network) to exercise the actual
clone/checkout path, and verifies the security guarantees around credentials.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.repo.checkout import (
    RepoCheckoutError,
    _checkout_url,
    _sanitize,
    checkout_repository,
)
from app.repo.workspace import Workspace, prepare_workspace


def _git(repo: str, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def origin_repo(tmp_path) -> str:
    """Create a local git repository with two commits and a tag."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(str(repo), "init", "-b", "main")
    (repo / "file.txt").write_text("v1\n")
    _git(str(repo), "add", ".")
    _git(str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "first")
    shas = []
    # Commit 2.
    (repo / "file.txt").write_text("v2\n")
    _git(str(repo), "add", ".")
    _git(str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "second")
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    shas.append(out.stdout.strip())
    # Go back to commit 1 to get its SHA too.
    out2 = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        cwd=repo, capture_output=True, text=True,
    )
    shas.insert(0, out2.stdout.strip())
    return str(repo)


class TestCheckoutUrl:
    def test_https_url(self):
        assert _checkout_url("acme/payments") == "https://github.com/acme/payments.git"

    def test_preserves_full_url(self):
        assert _checkout_url("https://host/x.git") == "https://host/x.git"

    def test_local_path_url(self):
        p = "/tmp/repo-xyz"
        assert _checkout_url(p) == p


class TestSanitize:
    def test_strips_token_from_message(self):
        msg = "fatal: https://x-access-token:secret@github failed"
        assert "secret" not in _sanitize(msg)
        assert "***" in _sanitize(msg)

    def test_truncates_long_messages(self):
        assert len(_sanitize("e" * 2000)) <= 500


class TestCheckoutRepository:
    def test_clones_at_exact_sha(self, origin_repo, tmp_path):
        dest = tmp_path / "work"
        dest.mkdir()
        result = checkout_repository(origin_repo, sha="HEAD", destination=str(dest))
        # HEAD of origin (one commit, since we copy) — verify content present.
        assert (Path(result.path) / "file.txt").exists()

    def test_checkout_pins_correct_commit(self, origin_repo, tmp_path):
        dest = tmp_path / "work"
        dest.mkdir()
        # Check out the FIRST commit (v1).
        first_sha = subprocess.run(
            ["git", "rev-parse", "HEAD~1"], cwd=origin_repo,
            capture_output=True, text=True,
        ).stdout.strip()
        result = checkout_repository(origin_repo, sha=first_sha, destination=str(dest))
        content = (Path(result.path) / "file.txt").read_text()
        assert content == "v1\n"
        assert result.head_sha == first_sha

    def test_idempotent_reuse(self, origin_repo, tmp_path):
        dest = tmp_path / "work"
        dest.mkdir()
        r1 = checkout_repository(origin_repo, destination=str(dest))
        r2 = checkout_repository(origin_repo, destination=str(dest))
        assert r1.path == r2.path


class TestPrepareWorkspace:
    def test_creates_owned_workspace(self, origin_repo):
        ws = prepare_workspace(origin_repo)
        assert ws.root
        assert isinstance(ws, Workspace)
        # The workspace root IS the checked-out repository root.
        assert (Path(ws.root) / "file.txt").exists()

    def test_cleanup_removes_owned_root(self, origin_repo):
        ws = prepare_workspace(origin_repo)
        root = ws.root
        ws.cleanup()
        assert not Path(root).exists()

    def test_external_root_preserved(self, origin_repo, tmp_path):
        ext_root = tmp_path / "external"
        ws = prepare_workspace(origin_repo, root=str(ext_root))
        assert ws.owns_root is False
        ws.cleanup()  # should NOT delete external root
        assert Path(ext_root).exists()


class TestSecurity:
    def test_token_not_in_clone_args(self, origin_repo, tmp_path, monkeypatch):
        """Token must not appear in any subprocess command line."""
        token = "ghp_SECRET_TOKEN_123"
        monkeypatch.setenv("GH_TOKEN", token)
        seen_by_git: list[list[str]] = []

        original = subprocess.run

        # Wrap to capture command lines (before exec).
        def spy(args, **kwargs):
            seen_by_git.append(list(args) if isinstance(args, (list, tuple)) else [])
            return original(args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        dest = tmp_path / "w"
        dest.mkdir()
        result = checkout_repository(origin_repo, destination=str(dest), token=token)
        assert result
        # Ensure the secret never appeared in any argv.
        for argv in seen_by_git:
            assert all(token not in str(a) for a in argv)

    def test_error_message_does_not_leak_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_leaky_zzz")
        dest = tmp_path / "w"
        dest.mkdir()
        # Point at a repo that doesn't exist remotely; simulate by bad local path.
        with pytest.raises(RepoCheckoutError) as exc:
            checkout_repository("/nonexistent/path/repo", destination=str(dest))
        assert "ghp_leaky_zzz" not in str(exc.value)
