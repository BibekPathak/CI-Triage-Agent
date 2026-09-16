"""Deterministic local benchmark scenarios.

Ten reproducible sign-bug scenarios, each materializable as a real local git
repo (``src/<module>.py`` + ``tests/test_<module>.py`` + ``conftest.py``).  The
deterministic fixer solves them by flipping ``return a - b`` to ``return a + b``
so the benchmark runs fully offline.
"""

from __future__ import annotations

from app.evaluation.models import BenchmarkScenario


def _make_scenario(
    name: str,
    module: str,
    func: str,
    expected: str,
) -> BenchmarkScenario:
    src = (
        f"def {func}(a, b):\n    return a - b\n"
    )
    test = (
        f"from src.{module} import {func}\n\n\n"
        f"def test_{func}():\n    assert {func}(2, 2) == 4\n"
    )
    return BenchmarkScenario(
        name=name,
        repository=f"bench/{module}",
        failing_command=f"pytest -q tests/test_{module}.py::test_{func}",
        expected_root_cause=expected,
        expected_files=[f"src/{module}.py"],
        validation_command="pytest -q",
        files={
            "conftest.py": "",
            ".gitignore": "__pycache__/\n.pytest_cache/\n*.pyc\n*.pyo\n",
            f"src/{module}.py": src,
            f"tests/test_{module}.py": test,
        },
        fix_file=f"src/{module}.py",
        fix_original=f"def {func}(a, b):\n    return a - b",
        fix_replacement=f"def {func}(a, b):\n    return a + b",
    )


def all_scenarios() -> list[BenchmarkScenario]:
    """Return the ten deterministic benchmark scenarios."""
    mods = [
        ("add", "calc", "add"),
        ("subtract-not", "calc", "sub"),
        ("multiply", "math", "mul"),
        ("payments-total", "payment", "total"),
        ("discount", "pricing", "discount"),
        ("balance", "account", "balance"),
        ("distance", "geo", "distance"),
        ("profit", "finance", "profit"),
        ("score", "game", "score"),
        ("shipping", "fulfillment", "shipping"),
    ]
    return [
        _make_scenario(name, module, func, f"sign bug in {func}")
        for name, module, func in mods
    ]


def write_repo_files(scenario: BenchmarkScenario, root: str) -> None:
    """Materialize the scenario's broken repo template into ``root``."""
    from pathlib import Path

    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    for rel, content in scenario.files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
