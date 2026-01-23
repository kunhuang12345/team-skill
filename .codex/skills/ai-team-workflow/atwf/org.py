from __future__ import annotations

from typing import Any

from . import registry as registry_mod
from . import resolve


def _tree_children(data: dict[str, Any]) -> dict[str, list[str]]:
    members = data.get("members", [])
    if not isinstance(members, list):
        return {}

    out: dict[str, list[str]] = {}

    for m in members:
        if not isinstance(m, dict):
            continue
        full = str(m.get("full", "")).strip()
        if not full:
            continue
        parent = m.get("parent")
        parent_full = str(parent).strip() if isinstance(parent, str) else ""
        if parent_full:
            out.setdefault(parent_full, []).append(full)

    for m in members:
        if not isinstance(m, dict):
            continue
        full = str(m.get("full", "")).strip()
        if not full:
            continue
        children = m.get("children")
        if not isinstance(children, list):
            continue
        for c in children:
            child = str(c).strip() if isinstance(c, str) else ""
            if not child:
                continue
            out.setdefault(full, []).append(child)

    for k, v in list(out.items()):
        uniq: list[str] = []
        seen: set[str] = set()
        for child in v:
            if child not in seen:
                seen.add(child)
                uniq.append(child)
        out[k] = sorted(uniq)

    return out


def _tree_roots(data: dict[str, Any]) -> list[str]:
    members = data.get("members", [])
    if not isinstance(members, list):
        return []

    fulls = []
    for m in members:
        if not isinstance(m, dict):
            continue
        full = str(m.get("full", "")).strip()
        if full:
            fulls.append(full)

    known = set(fulls)
    roots = []
    for m in members:
        if not isinstance(m, dict):
            continue
        full = str(m.get("full", "")).strip()
        if not full:
            continue
        parent = m.get("parent")
        parent_full = str(parent).strip() if isinstance(parent, str) else ""
        if not parent_full or parent_full not in known:
            roots.append(full)

    def key(full: str) -> str:
        mm = registry_mod._resolve_member(data, full)
        return str(mm.get("updated_at", "")) if mm else ""

    roots.sort(key=key, reverse=True)
    return roots


def _members_by_role(data: dict[str, Any], role: str) -> list[str]:
    role = role.strip()
    members = data.get("members", [])
    if not isinstance(members, list):
        return []
    out = []
    for m in members:
        if not isinstance(m, dict):
            continue
        if str(m.get("role", "")).strip() != role:
            continue
        full = str(m.get("full", "")).strip()
        if full:
            out.append(full)
    return sorted(set(out))


def _subtree_fulls(data: dict[str, Any], root_full: str) -> list[str]:
    root_full = root_full.strip()
    if not root_full:
        return []
    children_map = _tree_children(data)
    out: list[str] = []
    seen: set[str] = set()
    stack = [root_full]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        for child in children_map.get(cur, []):
            if child not in seen:
                stack.append(child)
    return out


def _all_member_fulls(data: dict[str, Any]) -> list[str]:
    members = data.get("members", [])
    if not isinstance(members, list):
        return []
    out: list[str] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        full = str(m.get("full", "")).strip()
        if full:
            out.append(full)
    uniq: list[str] = []
    seen: set[str] = set()
    for full in out:
        if full not in seen:
            seen.add(full)
            uniq.append(full)
    return uniq


def _select_targets_for_team_op(
    data: dict[str, Any],
    *,
    targets: list[str] | None,
    role: str | None,
    subtree: str | None,
) -> list[str]:
    if role:
        return _members_by_role(data, role)

    if subtree:
        root = resolve._resolve_target_full(data, subtree)
        if not root:
            raise SystemExit(f"❌ subtree root not found in registry: {subtree}")
        return _subtree_fulls(data, root)

    raw_targets = targets or []
    if raw_targets:
        resolved: list[str] = []
        for t in raw_targets:
            full = resolve._resolve_target_full(data, str(t))
            if not full:
                raise SystemExit(f"❌ target not found in registry: {t}")
            resolved.append(full)
        uniq: list[str] = []
        seen: set[str] = set()
        for full in resolved:
            if full not in seen:
                seen.add(full)
                uniq.append(full)
        return uniq

    return _all_member_fulls(data)

