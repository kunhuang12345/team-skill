from __future__ import annotations

import os
from pathlib import Path

from . import config as config_mod
from . import runtime


def _apply_deps_env_defaults() -> None:
    """
    Make ai-team-workflow self-contained by defaulting dependency configs to this
    skill's `scripts/atwf_config.yaml`.

    Users may still override via env vars (highest priority).
    """
    cfg = config_mod._read_yaml_or_json(runtime._config_file())

    if not os.environ.get("TWF_CODEX_CMD_CONFIG", "").strip():
        os.environ["TWF_CODEX_CMD_CONFIG"] = str(runtime._config_file())

    if not os.environ.get("CAP_SOURCES", "").strip():
        sources = config_mod._cfg_get_str(cfg, ("cap", "sources"), default="")
        if sources:
            os.environ["CAP_SOURCES"] = sources

    if not os.environ.get("CAP_STRATEGY", "").strip():
        strategy = config_mod._cfg_get_str(cfg, ("cap", "strategy"), default="")
        if strategy:
            os.environ["CAP_STRATEGY"] = strategy

    if not os.environ.get("CAP_STATE_FILE", "").strip():
        state_file = config_mod._cfg_get_str(cfg, ("cap", "state_file"), default="")
        if state_file:
            os.environ["CAP_STATE_FILE"] = str(runtime._expand_path_from(runtime._skill_dir(), state_file))


def _cap_state_file_path() -> Path:
    raw = os.environ.get("CAP_STATE_FILE", "").strip()
    if raw:
        return runtime._expand_path(raw)

    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    state_file = config_mod._cfg_get_str(cfg, ("cap", "state_file"), default="")
    if state_file:
        return runtime._expand_path_from(runtime._skill_dir(), state_file)

    return (runtime._skill_dir() / "deps" / "codex-account-pool" / "share" / "state.json").resolve()

