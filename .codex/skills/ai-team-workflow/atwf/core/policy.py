from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import config as config_mod
from . import constants as C
from . import runtime


@dataclass(frozen=True)
class TeamPolicy:
    root_role: str
    enabled_roles: frozenset[str]
    can_hire: dict[str, frozenset[str]]

    broadcast_allowed_roles: frozenset[str]
    broadcast_exclude_roles: frozenset[str]

    comm_allow_parent_child: bool
    comm_direct_allow: dict[str, frozenset[str]]
    comm_require_handoff: bool
    comm_handoff_creators: frozenset[str]


def _norm_role(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


def _role_set(raw: Any) -> set[str]:
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
        return {p.lower() for p in parts if p}
    if isinstance(raw, list):
        out: set[str] = set()
        for item in raw:
            r = _norm_role(item)
            if r:
                out.add(r)
        return out
    return set()


def _available_template_roles() -> set[str]:
    td = runtime._templates_dir()
    if not td.is_dir():
        return set()
    roles: set[str] = set()
    for p in td.glob("*.md"):
        if p.name == "command_rules.md":
            continue
        stem = p.stem.strip().lower()
        if stem:
            roles.add(stem)
    return roles


def _role_map(raw: Any) -> dict[str, set[str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, set[str]] = {}
    for k, v in raw.items():
        key = _norm_role(k)
        if not key:
            continue
        out[key] = _role_set(v)
    return out


@lru_cache(maxsize=1)
def _policy() -> TeamPolicy:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())

    templates = _available_template_roles()
    default_enabled = set(C.DEFAULT_ROLES) & templates if templates else set(C.DEFAULT_ROLES)

    enabled = _role_set(config_mod._cfg_get(cfg, ("team", "policy", "enabled_roles")))
    if not enabled:
        enabled = set(default_enabled)

    root_role = _norm_role(config_mod._cfg_get_str(cfg, ("team", "policy", "root_role"), default="coord")) or "coord"

    if enabled and root_role not in enabled:
        raise SystemExit(f"❌ policy.root_role={root_role!r} is not in enabled_roles")

    if templates:
        missing_templates = sorted(r for r in enabled if (td := (runtime._templates_dir() / f"{r}.md")) and not td.is_file())
        if missing_templates:
            raise SystemExit(f"❌ enabled_roles missing templates/*.md: {', '.join(missing_templates)}")

    can_hire_raw = _role_map(config_mod._cfg_get(cfg, ("team", "policy", "can_hire")))
    can_hire: dict[str, frozenset[str]] = {}
    for parent_role, children in can_hire_raw.items():
        if parent_role not in enabled:
            continue
        filtered = {c for c in children if c in enabled}
        can_hire[parent_role] = frozenset(sorted(filtered))

    bc_allowed = _role_set(config_mod._cfg_get(cfg, ("team", "policy", "broadcast", "allowed_roles")))
    if not bc_allowed:
        bc_allowed = {root_role}
    bc_allowed = {r for r in bc_allowed if r in enabled}

    bc_exclude = _role_set(config_mod._cfg_get(cfg, ("team", "policy", "broadcast", "exclude_roles")))
    bc_exclude = {r for r in bc_exclude if r in enabled}

    comm_allow_parent_child = config_mod._cfg_get(cfg, ("team", "policy", "comm", "allow_parent_child"))
    if not isinstance(comm_allow_parent_child, bool):
        comm_allow_parent_child = True

    comm_require_handoff = config_mod._cfg_get(cfg, ("team", "policy", "comm", "require_handoff"))
    if not isinstance(comm_require_handoff, bool):
        comm_require_handoff = True

    handoff_creators = _role_set(config_mod._cfg_get(cfg, ("team", "policy", "comm", "handoff_creators")))
    if not handoff_creators:
        handoff_creators = {root_role}
    handoff_creators = {r for r in handoff_creators if r in enabled}

    direct_allow_raw = _role_map(config_mod._cfg_get(cfg, ("team", "policy", "comm", "direct_allow")))
    direct_allow: dict[str, set[str]] = {r: set() for r in enabled}
    for a, bs in direct_allow_raw.items():
        if a not in enabled:
            continue
        for b in bs:
            if b not in enabled:
                continue
            direct_allow.setdefault(a, set()).add(b)
            direct_allow.setdefault(b, set()).add(a)

    pairs_raw = config_mod._cfg_get(cfg, ("team", "policy", "comm", "direct_allow_pairs"))
    if isinstance(pairs_raw, list):
        for item in pairs_raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            a = _norm_role(item[0])
            b = _norm_role(item[1])
            if not a or not b or a not in enabled or b not in enabled:
                continue
            direct_allow.setdefault(a, set()).add(b)
            direct_allow.setdefault(b, set()).add(a)

    direct_allow_frozen: dict[str, frozenset[str]] = {}
    for r in enabled:
        direct_allow_frozen[r] = frozenset(sorted(direct_allow.get(r, set())))

    return TeamPolicy(
        root_role=root_role,
        enabled_roles=frozenset(sorted(enabled)),
        can_hire=can_hire,
        broadcast_allowed_roles=frozenset(sorted(bc_allowed)),
        broadcast_exclude_roles=frozenset(sorted(bc_exclude)),
        comm_allow_parent_child=bool(comm_allow_parent_child),
        comm_direct_allow=direct_allow_frozen,
        comm_require_handoff=bool(comm_require_handoff),
        comm_handoff_creators=frozenset(sorted(handoff_creators)),
    )


def _require_role(role: str) -> str:
    r = (role or "").strip().lower()
    enabled = _policy().enabled_roles
    if r not in enabled:
        raise SystemExit(f"❌ unsupported role: {role} (enabled: {', '.join(sorted(enabled))})")
    return r
