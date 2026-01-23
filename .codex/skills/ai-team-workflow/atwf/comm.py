from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import policy as policy_mod
from . import registry as registry_mod
from . import resolve


def _is_direct_parent_child(data: dict[str, Any], a_full: str, b_full: str) -> bool:
    ma = registry_mod._resolve_member(data, a_full)
    mb = registry_mod._resolve_member(data, b_full)
    if not ma or not mb:
        return False
    pa = str(ma.get("parent", "")).strip() if isinstance(ma.get("parent"), str) else ""
    pb = str(mb.get("parent", "")).strip() if isinstance(mb.get("parent"), str) else ""
    return pa == b_full or pb == a_full


def _permit_allows(data: dict[str, Any], *, a_base: str, b_base: str) -> bool:
    permits = data.get("permits")
    if not isinstance(permits, list):
        return False
    a_base = a_base.strip()
    b_base = b_base.strip()
    if not a_base or not b_base:
        return False

    now = datetime.now()
    for p in permits:
        if not isinstance(p, dict):
            continue
        a = str(p.get("a", "")).strip()
        b = str(p.get("b", "")).strip()
        if not a or not b:
            continue
        if not ((a == a_base and b == b_base) or (a == b_base and b == a_base)):
            continue
        exp = str(p.get("expires_at", "")).strip()
        if exp:
            try:
                if datetime.fromisoformat(exp) <= now:
                    continue
            except Exception:
                pass
        return True
    return False


def _add_handoff_permit(
    data: dict[str, Any],
    *,
    a_base: str,
    b_base: str,
    created_by: str,
    created_by_role: str,
    reason: str,
    ttl_seconds: int | None,
) -> dict[str, Any]:
    permits = data.get("permits")
    if not isinstance(permits, list):
        permits = []
        data["permits"] = permits

    a_base = a_base.strip()
    b_base = b_base.strip()
    if not a_base or not b_base:
        raise SystemExit("❌ invalid handoff endpoints (missing base)")
    if a_base == b_base:
        raise SystemExit("❌ handoff endpoints must be different")

    now = datetime.now()
    created_at = now.isoformat(timespec="seconds")
    expires_at = ""
    if isinstance(ttl_seconds, int) and ttl_seconds > 0:
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")

    permit_id = f"handoff-{now.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{len(permits) + 1}"
    payload: dict[str, Any] = {
        "id": permit_id,
        "a": a_base,
        "b": b_base,
        "created_by": created_by,
        "created_by_role": created_by_role,
        "created_at": created_at,
    }
    if expires_at:
        payload["expires_at"] = expires_at
    if reason.strip():
        payload["reason"] = reason.strip()
    permits.append(payload)
    return payload


def _comm_allowed(
    policy: policy_mod.TeamPolicy,
    data: dict[str, Any],
    *,
    actor_full: str,
    target_full: str,
) -> tuple[bool, str]:
    if actor_full == target_full:
        return True, "self"

    actor_m = registry_mod._resolve_member(data, actor_full)
    target_m = registry_mod._resolve_member(data, target_full)
    if not actor_m:
        return False, f"actor not registered: {actor_full}"
    if not target_m:
        return False, f"target not registered: {target_full}"

    actor_role = resolve._member_role(actor_m)
    target_role = resolve._member_role(target_m)
    if actor_role not in policy.enabled_roles:
        return False, f"actor role not enabled: {actor_role or '(missing)'}"
    if target_role not in policy.enabled_roles:
        return False, f"target role not enabled: {target_role or '(missing)'}"

    if policy.comm_allow_parent_child and _is_direct_parent_child(data, actor_full, target_full):
        return True, "parent-child"

    allowed_roles = policy.comm_direct_allow.get(actor_role, frozenset())
    if target_role in allowed_roles:
        return True, "direct-allow"

    if not policy.comm_require_handoff:
        return True, "handoff-not-required"

    actor_base = resolve._member_base(actor_m)
    target_base = resolve._member_base(target_m)
    if _permit_allows(data, a_base=actor_base, b_base=target_base):
        return True, "handoff-permit"

    return False, f"handoff required for {actor_role}->{target_role} (no permit)"


def _require_comm_allowed(
    policy: policy_mod.TeamPolicy,
    data: dict[str, Any],
    *,
    actor_full: str,
    target_full: str,
) -> None:
    ok, reason = _comm_allowed(policy, data, actor_full=actor_full, target_full=target_full)
    if ok:
        return
    root = policy.root_role
    raise SystemExit(
        "❌ communication not permitted by policy.\n"
        f"   actor:  {actor_full}\n"
        f"   target: {target_full}\n"
        f"   reason: {reason}\n"
        f"   hint: request a handoff via `{root}` (or run: atwf handoff --as {root} <from> <to> --reason \"...\")"
    )

