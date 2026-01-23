from __future__ import annotations

import os
import shlex
from functools import lru_cache
from pathlib import Path


def _expand_path(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    return p


def _expand_path_from(base: Path, path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


@lru_cache(maxsize=1)
def _skill_dir() -> Path:
    # Locate the skill root (directory containing SKILL.md), even if the atwf
    # package is nested (e.g. <skill_root>/atwf/core/runtime.py).
    here = Path(__file__).resolve()
    for p in (here.parent, *here.parents):
        if (p / "SKILL.md").is_file():
            return p
    # Fallback: assume <skill_root>/atwf/... layout.
    return here.parents[2]


@lru_cache(maxsize=1)
def _config_file() -> Path:
    return _skill_dir() / "scripts" / "atwf_config.yaml"


@lru_cache(maxsize=1)
def _templates_dir() -> Path:
    return _skill_dir() / "templates"


def _registry_path(team_dir: Path) -> Path:
    override = os.environ.get("AITWF_REGISTRY", "").strip()
    return _expand_path(override) if override else team_dir / "registry.json"


def _default_team_dir() -> Path:
    env_dir = os.environ.get("AITWF_DIR", "").strip()
    if env_dir:
        return _expand_path(env_dir)

    from . import config as config_mod

    skill_dir = _skill_dir()
    cfg = config_mod._read_yaml_or_json(_config_file())
    share_dir = config_mod._cfg_get_str(cfg, ("share", "dir"), ("share_dir",))
    if share_dir:
        return _expand_path_from(skill_dir, share_dir)

    return skill_dir / "share"


def _paused_marker_path(team_dir: Path) -> Path:
    return team_dir / ".paused"


def _set_paused(team_dir: Path, *, reason: str) -> None:
    from ..infra import io as io_mod
    from . import util

    team_dir.mkdir(parents=True, exist_ok=True)
    content = f"paused_at: {util._now()}\n"
    reason = reason.strip()
    if reason:
        content += f"reason: {reason}\n"
    io_mod._write_text_atomic(_paused_marker_path(team_dir), content)


def _clear_paused(team_dir: Path) -> None:
    try:
        _paused_marker_path(team_dir).unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _atwf_wrapper_path() -> Path:
    return _skill_dir() / "scripts" / "atwf"


def _atwf_py_entry_path() -> Path:
    return _skill_dir() / "scripts" / "atwf.py"


def _atwf_cmd() -> str:
    wrapper = _atwf_wrapper_path()
    if wrapper.is_file():
        return f"bash {shlex.quote(str(wrapper))}"
    entry = _atwf_py_entry_path()
    if entry.is_file():
        return f"python3 {shlex.quote(str(entry))}"
    return "python3 -m atwf"


def _substitute_atwf_paths(text: str) -> str:
    s = text or ""
    s = s.replace("{{ATWF_CMD}}", _atwf_cmd())
    s = s.replace("{{ATWF_CONFIG}}", str(_config_file()))
    s = s.replace("bash .codex/skills/ai-team-workflow/scripts/atwf", _atwf_cmd())
    s = s.replace(".codex/skills/ai-team-workflow/scripts/atwf_config.yaml", str(_config_file()))
    return s
