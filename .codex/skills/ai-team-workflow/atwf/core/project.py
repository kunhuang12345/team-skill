from __future__ import annotations

import os
from pathlib import Path

from ..infra import io as io_mod
from . import runtime
from ..workflows import worktree as worktree_mod


def _expected_project_root() -> Path:
    override = os.environ.get("AITWF_PROJECT_ROOT", "").strip()
    if override:
        return runtime._expand_path(override)

    # Prefer a stable "project root" derived from the install location:
    #   <project>/.codex/skills/ai-team-workflow
    # This makes watcher/session naming stable even when you run atwf from
    # different cwd/worktrees.
    skill_dir = runtime._skill_dir().resolve()
    home_codex = (Path.home() / ".codex").resolve()
    for p in [skill_dir, *skill_dir.parents]:
        if p.name != ".codex":
            continue
        # Ignore global install (~/.codex); fallback to cwd/git-root so global
        # skills can be reused across many repos.
        if p.resolve() == home_codex:
            break
        return p.parent.resolve()

    try:
        return worktree_mod._git_root()
    except SystemExit:
        return Path.cwd().resolve()


def _state_file_matches_project(state_file: Path, expected_root: Path) -> bool:
    data = io_mod._read_json(state_file)
    if not data:
        return False

    work_dir_norm = data.get("work_dir_norm")
    if isinstance(work_dir_norm, str) and work_dir_norm.strip():
        actual = Path(work_dir_norm.strip()).resolve()
    else:
        work_dir = data.get("work_dir")
        if not isinstance(work_dir, str) or not work_dir.strip():
            return False
        actual = Path(work_dir.strip()).resolve()

    expected = expected_root.resolve()
    return actual == expected or expected in actual.parents
