from __future__ import annotations

from pathlib import Path

from . import io as io_mod


def _worktrees_dir(git_root: Path) -> Path:
    return git_root / "worktree"


def _worktree_path(git_root: Path, full: str) -> Path:
    return _worktrees_dir(git_root) / full.strip()


def _git_root() -> Path:
    # Prefer the "common" git dir so worktree commands behave consistently even
    # when invoked from inside a linked worktree (where --show-toplevel returns
    # the worktree path, not the project root).
    res = io_mod._run(["git", "rev-parse", "--git-common-dir"])
    if res.returncode == 0:
        raw = res.stdout.strip()
        if raw:
            common_dir = Path(raw)
            if not common_dir.is_absolute():
                common_dir = (Path.cwd() / common_dir).resolve()
            else:
                common_dir = common_dir.resolve()
            root = common_dir.parent.resolve()
            if root.is_dir():
                return root

    res = io_mod._run(["git", "rev-parse", "--show-toplevel"])
    if res.returncode != 0:
        raise SystemExit("❌ not a git repository (needed for worktree commands)")
    root = res.stdout.strip()
    if not root:
        raise SystemExit("❌ failed to detect git root")
    return Path(root).resolve()


def _git_root_from(cwd: Path) -> Path:
    cwd = cwd.resolve()
    res = io_mod._run(["git", "-C", str(cwd), "rev-parse", "--git-common-dir"])
    if res.returncode == 0:
        raw = res.stdout.strip()
        if raw:
            common_dir = Path(raw)
            if not common_dir.is_absolute():
                common_dir = (cwd / common_dir).resolve()
            else:
                common_dir = common_dir.resolve()
            root = common_dir.parent.resolve()
            if root.is_dir():
                return root

    res = io_mod._run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"])
    if res.returncode != 0:
        raise SystemExit(f"❌ not a git repository: {cwd}")
    root = res.stdout.strip()
    if not root:
        raise SystemExit(f"❌ failed to detect git root: {cwd}")
    return Path(root).resolve()
