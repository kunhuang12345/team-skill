from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core import constants as C
from ..infra import io as io_mod
from ..core import policy as policy_mod
from . import registry as registry_mod
from ..core import runtime
from ..infra import tmux as tmux_mod


def _member_state_file(m: dict[str, Any]) -> Path | None:
    raw = m.get("state_file")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return runtime._expand_path(raw)
    except Exception:
        return None


def _member_work_dir(m: dict[str, Any]) -> Path | None:
    state_file = _member_state_file(m)
    if not (state_file and state_file.is_file()):
        return None
    state = io_mod._read_json(state_file)
    raw = state.get("work_dir_norm") or state.get("work_dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Path(os.path.expanduser(raw.strip())).resolve()
    except Exception:
        return None


def _resolve_target_full(data: dict[str, Any], target: str) -> str | None:
    target = target.strip()
    if not target:
        return None

    m = registry_mod._resolve_member(data, target)
    if m:
        full = str(m.get("full", "")).strip()
        return full or None

    maybe_role = target.lower()
    if maybe_role in policy_mod._policy().enabled_roles:
        m2 = registry_mod._resolve_latest_by_role(data, maybe_role)
        if m2:
            full = str(m2.get("full", "")).strip()
            return full or None

    if C.FULL_NAME_RE.match(target):
        return target

    return None


def _tmux_self_full() -> str | None:
    return tmux_mod._tmux_self_full()


def _resolve_actor_full(data: dict[str, Any], *, as_target: str | None) -> str:
    if as_target:
        full = _resolve_target_full(data, as_target)
        if not full:
            raise SystemExit(f"❌ --as target not found in registry: {as_target}")
        return full

    full = _tmux_self_full()
    if full:
        return full
    raise SystemExit("❌ this command must run inside tmux or pass --as <full|base|role>")


def _member_role(m: dict[str, Any] | None) -> str:
    if not isinstance(m, dict):
        return ""
    return str(m.get("role", "")).strip()


def _member_base(m: dict[str, Any] | None) -> str:
    if not isinstance(m, dict):
        return ""
    base = str(m.get("base", "")).strip()
    full = str(m.get("full", "")).strip()
    return base or full
