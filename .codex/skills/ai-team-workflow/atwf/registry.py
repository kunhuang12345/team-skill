from __future__ import annotations

from pathlib import Path
from typing import Any

from . import io as io_mod
from . import util


def _load_registry(registry: Path) -> dict[str, Any]:
    data = io_mod._read_json(registry)
    if not data:
        return {
            "version": 1,
            "created_at": util._now(),
            "updated_at": util._now(),
            "members": [],
            "permits": [],
        }
    if not isinstance(data.get("members"), list):
        data["members"] = []
    if not isinstance(data.get("permits"), list):
        data["permits"] = []
    if not isinstance(data.get("version"), int):
        data["version"] = 1
    if not isinstance(data.get("created_at"), str):
        data["created_at"] = util._now()
    data["updated_at"] = util._now()
    return data


def _find_member_index(data: dict[str, Any], full: str) -> int | None:
    members = data.get("members")
    if not isinstance(members, list):
        return None
    for i, m in enumerate(members):
        if isinstance(m, dict) and m.get("full") == full:
            return i
    return None


def _find_latest_member_by(data: dict[str, Any], *, role: str, base: str) -> dict[str, Any] | None:
    members = data.get("members")
    if not isinstance(members, list):
        return None
    matches = []
    for m in members:
        if not isinstance(m, dict):
            continue
        if str(m.get("role", "")).strip() != role:
            continue
        if str(m.get("base", "")).strip() != base:
            continue
        matches.append(m)
    if not matches:
        return None
    matches.sort(key=lambda m: str(m.get("updated_at", "")), reverse=True)
    return matches[0]


def _ensure_member(
    data: dict[str, Any],
    *,
    full: str,
    base: str | None = None,
    role: str | None = None,
    scope: str | None = None,
    parent: str | None = None,
    state_file: str | None = None,
) -> dict[str, Any]:
    members = data["members"]
    assert isinstance(members, list)

    idx = _find_member_index(data, full)
    if idx is None:
        m: dict[str, Any] = {
            "full": full,
            "base": base or "",
            "role": role or "",
            "scope": scope or "",
            "parent": parent,
            "children": [],
            "state_file": state_file or "",
            "created_at": util._now(),
            "updated_at": util._now(),
        }
        members.append(m)
        return m

    m = members[idx]
    if not isinstance(m, dict):
        m = {"full": full}
        members[idx] = m
    if base is not None:
        m["base"] = base
    if role is not None:
        m["role"] = role
    if scope is not None:
        m["scope"] = scope
    if parent is not None:
        m["parent"] = parent
    if state_file is not None:
        m["state_file"] = state_file
    if not isinstance(m.get("children"), list):
        m["children"] = []
    m["updated_at"] = util._now()
    return m


def _add_child(data: dict[str, Any], *, parent_full: str, child_full: str) -> None:
    parent = _ensure_member(data, full=parent_full)
    children = parent.get("children")
    if not isinstance(children, list):
        children = []
    children = [c for c in children if isinstance(c, str) and c.strip()]
    if child_full not in children:
        children.append(child_full)
    parent["children"] = children
    parent["updated_at"] = util._now()


def _resolve_member(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    name = name.strip()
    members = data.get("members")
    if not isinstance(members, list):
        return None

    exact = [m for m in members if isinstance(m, dict) and m.get("full") == name]
    if exact:
        return exact[0]

    base_matches = [m for m in members if isinstance(m, dict) and m.get("base") == name]
    if not base_matches:
        return None
    base_matches.sort(key=lambda m: str(m.get("updated_at", "")), reverse=True)
    return base_matches[0]


def _resolve_latest_by_role(data: dict[str, Any], role: str) -> dict[str, Any] | None:
    role = role.strip()
    members = data.get("members")
    if not isinstance(members, list):
        return None
    matches = [m for m in members if isinstance(m, dict) and str(m.get("role", "")).strip() == role]
    if not matches:
        return None
    matches.sort(key=lambda m: str(m.get("updated_at", "")), reverse=True)
    return matches[0]


def _ensure_registry_file(registry: Path, team_dir: Path) -> None:
    lock = team_dir / ".lock"
    with io_mod._locked(lock):
        data = _load_registry(registry)
        io_mod._write_json_atomic(registry, data)
    util._eprint(f"✅ registry ready: {registry}")


def _prune_members_by(data: dict[str, Any], *, role: str, base: str, keep_full: str | None = None) -> None:
    members = data.get("members")
    if not isinstance(members, list) or not members:
        return

    role = role.strip()
    base = base.strip()
    keep = keep_full.strip() if isinstance(keep_full, str) and keep_full.strip() else None

    kept: list[Any] = []
    for m in members:
        if not isinstance(m, dict):
            kept.append(m)
            continue
        m_role = str(m.get("role", "")).strip()
        m_base = str(m.get("base", "")).strip()
        m_full = str(m.get("full", "")).strip()
        if m_role == role and m_base == base:
            if keep and m_full == keep:
                kept.append(m)
            continue
        kept.append(m)
    data["members"] = kept
