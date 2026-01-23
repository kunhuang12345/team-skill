from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..core import config as config_mod
from . import io as io_mod
from ..core import runtime


def _resolve_twf() -> Path:
    override = os.environ.get("AITWF_TWF", "").strip()
    if override:
        p = runtime._expand_path(override)
        if p.is_file():
            return p
        raise SystemExit(f"❌ AITWF_TWF points to missing file: {p}")

    bundled = runtime._skill_dir() / "deps" / "tmux-workflow" / "scripts" / "twf"
    if bundled.is_file():
        return bundled

    skills_dir = runtime._skill_dir().parent
    sibling = skills_dir / "tmux-workflow" / "scripts" / "twf"
    if sibling.is_file():
        return sibling

    global_path = Path.home() / ".codex" / "skills" / "tmux-workflow" / "scripts" / "twf"
    if global_path.is_file():
        return global_path

    raise SystemExit(
        "❌ tmux-workflow not found.\n"
        "   Expected bundled `deps/tmux-workflow/scripts/twf` under ai-team-workflow, or set AITWF_TWF=/path/to/twf."
    )


def _resolve_twf_config_path(twf: Path) -> Path | None:
    tmux_skill_dir = twf.resolve().parents[1]
    cfg_override = os.environ.get("TWF_CODEX_CMD_CONFIG", "").strip()
    if cfg_override:
        cfg_path = runtime._expand_path(cfg_override)
    else:
        cfg_path = tmux_skill_dir / "scripts" / "twf_config.yaml"
        if not cfg_path.is_file():
            json_fallback = tmux_skill_dir / "scripts" / "twf_config.json"
            if json_fallback.is_file():
                cfg_path = json_fallback

    return cfg_path if cfg_path.is_file() else None


def _resolve_twf_state_dir(twf: Path) -> Path:
    override = os.environ.get("TWF_STATE_DIR", "").strip()
    if override:
        return runtime._expand_path(override)

    tmux_skill_dir = twf.resolve().parents[1]
    cfg_path = _resolve_twf_config_path(twf)
    cfg = config_mod._read_yaml_or_json(cfg_path) if cfg_path else {}

    mode = (config_mod._cfg_get_str(cfg, ("twf", "state_dir", "mode"), ("twf_state_dir_mode",), default="auto") or "auto").lower()
    if mode not in {"auto", "global", "manual"}:
        mode = "auto"

    if mode == "global":
        return Path.home() / ".twf"

    if mode == "manual":
        raw = config_mod._cfg_get_str(cfg, ("twf", "state_dir", "dir"), ("twf_state_dir",))
        if not raw:
            raise SystemExit(f"❌ twf_state_dir_mode=manual but twf_state_dir is empty in: {cfg_path}")
        return runtime._expand_path(raw)

    return tmux_skill_dir / ".twf"


def _run_twf(twf: Path, args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return io_mod._run(["bash", str(twf), *args], input_text=input_text)
