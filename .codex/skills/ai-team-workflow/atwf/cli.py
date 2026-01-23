from __future__ import annotations

import argparse
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .constants import *
from .comm import _add_handoff_permit, _comm_allowed, _permit_allows, _require_comm_allowed
from .config import (
    _cfg_get,
    _cfg_get_boolish,
    _cfg_get_floatish,
    _cfg_get_intish,
    _cfg_get_str,
    _cfg_get_str_list,
    _read_yaml_or_json,
)
from .deps import _apply_deps_env_defaults, _cap_state_file_path
from .drive import _drive_message_body, _drive_message_summary
from .inbox import (
    _find_inbox_message_file,
    _inbox_enforce_unread_limit_unlocked,
    _inbox_list_msgs,
    _inbox_member_dir,
    _inbox_message_created_at,
    _inbox_message_path,
    _inbox_notice,
    _inbox_pending_min_id,
    _inbox_thread_dir,
    _inbox_unread_stats,
    _mark_inbox_read,
    _next_msg_id,
    _write_inbox_message,
    _write_inbox_message_unlocked,
    _wrap_team_message,
)
from .io import _locked, _read_json, _rm_tree, _run, _write_json_atomic, _write_text_atomic
from .org import _all_member_fulls, _members_by_role, _select_targets_for_team_op, _subtree_fulls, _tree_children, _tree_roots
from .policy import TeamPolicy, _norm_role, _policy, _require_role
from .project import _expected_project_root, _state_file_matches_project
from .registry import (
    _add_child,
    _ensure_member,
    _ensure_registry_file,
    _find_latest_member_by,
    _load_registry,
    _prune_members_by,
    _resolve_latest_by_role,
    _resolve_member,
)
from .requests import (
    _finalize_request,
    _list_request_ids,
    _load_request_meta,
    _render_request_result,
    _request_all_replied,
    _request_dir,
    _request_response_path,
    _request_meta_path,
    _request_responses_dir,
    _resolve_request_id,
    _scan_reply_requests,
    _update_request_meta,
)
from .resolve import (
    _member_base,
    _member_role,
    _member_state_file,
    _member_work_dir,
    _resolve_actor_full,
    _resolve_target_full,
)
from .runtime import (
    _atwf_cmd,
    _atwf_wrapper_path,
    _clear_paused,
    _config_file,
    _default_team_dir,
    _expand_path,
    _expand_path_from,
    _paused_marker_path,
    _registry_path,
    _set_paused,
    _skill_dir,
    _substitute_atwf_paths,
    _templates_dir,
)
from .settings import (
    _drive_backup_role,
    _drive_cooldown_s,
    _drive_driver_role,
    _drive_mode_config_default,
    _drive_mode_config_hot,
    _drive_unit_role,
    _inbox_max_unread_per_thread,
    _request_block_snooze_default_s,
    _request_deadline_s,
    _state_active_grace_period_s,
    _state_activity_capture_lines,
    _state_activity_window_s,
    _state_auto_enter_cooldown_s,
    _state_auto_enter_enabled,
    _state_auto_enter_patterns,
    _state_auto_enter_tail_window_lines,
    _state_idle_wake_delay_s,
    _state_inbox_check_interval_s,
    _state_reply_wake_message,
    _state_wake_message,
    _state_watch_interval_s,
    _state_working_alert_cooldown_s,
    _state_working_stale_threshold_s,
)
from .state_store import (
    _agent_state_path,
    _default_agent_state,
    _design_dir,
    _design_member_path,
    _design_summary_path,
    _drive_state_path,
    _drive_subtree_state_path,
    _ensure_share_layout,
    _load_agent_state_unlocked,
    _load_drive_state_unlocked,
    _load_drive_subtree_state_unlocked,
    _load_reply_drive_state_unlocked,
    _normalize_agent_status,
    _ops_dir,
    _ops_env_notes_path,
    _ops_host_deps_path,
    _parse_duration_seconds,
    _reply_drive_state_path,
    _set_drive_mode_config,
    _set_drive_subtree_status,
    _state_lock_path,
    _state_root,
    _task_path,
    _update_agent_state,
    _write_agent_state,
    _write_drive_state,
    _write_drive_subtree_state,
    _write_reply_drive_state,
)
from .task_io import _read_task_content
from .team import (
    _base_name,
    _bootstrap_worker,
    _design_seed,
    _ensure_task_and_design_files,
    _init_task_to_role,
    _init_team,
    _normalize_provider,
    _provider_from_state_file,
    _spawn_worker,
    _start_worker,
)
from .tmux import _tmux_capture_tail, _tmux_kill_session, _tmux_running, _tmux_send_enter, _tmux_self_full
from .twf import _resolve_twf, _resolve_twf_config_path, _resolve_twf_state_dir, _run_twf
from .util import _eprint, _now, _parse_iso_dt, _slugify, _text_digest
from .watchers import (
    _cap_watch_session_name,
    _ensure_cap_watch_team,
    _ensure_watch_idle_team,
    _restart_watch_idle_team,
    _watch_idle_session_name,
)
from .worktree import _git_root, _git_root_from, _worktree_path, _worktrees_dir
from .templates import _render_template, _template_for_role, _template_lint_issues, _validate_templates_or_die


def _read_optional_message(args: argparse.Namespace, *, attr: str) -> str:
    msg = str(getattr(args, attr, "") or "").strip()
    if msg:
        return msg
    stdin_msg = _forward_stdin()
    return (stdin_msg or "").strip()

def cmd_reset(args: argparse.Namespace) -> int:
    """
    Reset current environment by deleting all local temp/state artifacts:
    - ai-team-workflow share dir (registry/task/design)
    - tmux-workflow workers for this project (tmux sessions, state files, worker homes)
    - codex-account-pool local state.json (optional)
    """
    expected_root = _expected_project_root()
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    reg = _load_registry(registry)
    # Stop the account-pool watcher early to avoid races during reset.
    watch_session = _cap_watch_session_name(expected_root)
    _tmux_kill_session(watch_session)
    _tmux_kill_session(f"{watch_session}-status")
    _tmux_kill_session(_watch_idle_session_name(expected_root, team_dir=team_dir))

    # 1) Stop/remove tmux-workflow workers for this project.
    state_dir = _resolve_twf_state_dir(twf)
    member_state_paths: list[Path] = []
    members = reg.get("members")
    if isinstance(members, list):
        for m in members:
            if not isinstance(m, dict):
                continue
            full = str(m.get("full", "") or "").strip()
            state_file = _member_state_file(m)
            if not state_file and full:
                state_file = state_dir / f"{full}.json"
            if not state_file:
                continue
            try:
                member_state_paths.append(state_file.resolve())
            except Exception:
                member_state_paths.append(state_file)

    # Best-effort fallback: also remove any state files that match the legacy
    # "project root" heuristic, for users who have state not recorded in registry.
    if state_dir.is_dir():
        for p in sorted(state_dir.glob("*.json")):
            try:
                p_res = p.resolve()
            except Exception:
                p_res = p
            if p_res in member_state_paths:
                continue
            try:
                if _state_file_matches_project(p, expected_root):
                    member_state_paths.append(p_res)
            except SystemExit:
                continue

    seen_state: set[Path] = set()
    worker_candidates: list[tuple[Path, dict[str, Any]]] = []
    for p in member_state_paths:
        if p in seen_state:
            continue
        seen_state.add(p)
        if not p.is_file():
            continue
        try:
            data = _read_json(p)
        except SystemExit:
            continue
        if not data:
            continue
        worker_candidates.append((p, data))

    codex_workers_root = Path(os.environ.get("TWF_WORKERS_DIR", "") or (Path.home() / ".codex-workers")).expanduser().resolve()

    if args.dry_run:
        print(f"project_root: {expected_root}")
        print(f"twf_state_dir: {state_dir}")
        print(f"workers_matched: {len(worker_candidates)}")
        for p, data in worker_candidates:
            print(f"- state: {p}")
            tmux_session = str(data.get("tmux_session") or "").strip()
            codex_home = str(data.get("codex_home") or "").strip()
            if tmux_session:
                print(f"  tmux_session: {tmux_session}")
            if codex_home:
                print(f"  codex_home: {codex_home}")
        print(f"ai_team_share_dir: {team_dir}")
        pool_state = _cap_state_file_path()
        print(f"cap_watch_session: {_cap_watch_session_name(expected_root)}")
        if getattr(args, "wipe_account_pool", False):
            print(f"cap_state: {pool_state}")
        else:
            print(f"cap_state: {pool_state} (preserved; pass --wipe-account-pool to delete)")
        return 0

    for p, data in worker_candidates:
        tmux_session = str(data.get("tmux_session") or "").strip()
        if tmux_session:
            _tmux_kill_session(tmux_session)

        codex_home_raw = str(data.get("codex_home") or "").strip()
        if codex_home_raw:
            try:
                codex_home = Path(codex_home_raw).expanduser().resolve()
                # Safety: only remove under codex_workers_root unless --force.
                if codex_workers_root == codex_home or codex_workers_root in codex_home.parents:
                    _rm_tree(codex_home)
                elif args.force:
                    _rm_tree(codex_home)
                else:
                    _eprint(f"⚠️ skip removing codex_home outside {codex_workers_root}: {codex_home}")
            except Exception:
                pass

        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    # Remove stale lock file if present.
    try:
        (state_dir / ".lock").unlink()
    except Exception:
        pass

    # 2) Remove ai-team-workflow share dir (registry/task/design).
    _rm_tree(team_dir)

    # 3) Optionally wipe local codex-account-pool state (per-project).
    if getattr(args, "wipe_account_pool", False):
        cap_state = _cap_state_file_path()
        cap_lock = cap_state.with_suffix(cap_state.suffix + ".lock")
        try:
            cap_state.unlink()
        except Exception:
            pass
        try:
            cap_lock.unlink()
        except Exception:
            pass

    _eprint("✅ reset complete")
    return 0

def _require_full_name(name: str) -> str:
    n = name.strip()
    if not FULL_NAME_RE.match(n):
        raise SystemExit("❌ remove requires a full worker name like: <base>-YYYYmmdd-HHMMSS-<pid>")
    return n

def cmd_init(args: argparse.Namespace) -> int:
    if not bool(getattr(args, "registry_only", False)) and not bool(getattr(args, "no_bootstrap", False)):
        _validate_templates_or_die()

    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    _ensure_registry_file(registry, team_dir)

    task_content, task_source = _read_task_content(args)
    task_path = _ensure_task_and_design_files(team_dir, task_content=task_content, task_source=task_source)

    if args.registry_only:
        if task_path:
            _eprint(f"✅ shared task saved: {task_path}")
        return 0

    twf = _resolve_twf()
    members = _init_team(
        twf=twf,
        registry=registry,
        team_dir=team_dir,
        force_new=bool(args.force_new),
        no_bootstrap=bool(args.no_bootstrap),
        root_only=bool(getattr(args, "root_only", False)),
    )

    policy = _policy()
    root_role = policy.root_role
    root_full = ""
    for m in members:
        if m.role == root_role:
            root_full = m.full
            break
    _eprint("✅ initial team ready:")
    for m in members:
        _eprint(f"   {m.role}: {m.full}")
    if root_full:
        _eprint(f"   tip: enter root via: atwf attach {root_role}  (or: atwf attach {root_full})")
    else:
        _eprint("   tip: inspect via: atwf list / atwf tree")

    # If account_pool is enabled in twf_config and team_cycle is selected,
    # start a background watcher that rotates the whole team when limits are hit.
    _ensure_cap_watch_team(twf=twf, team_dir=team_dir, registry=registry)
    _ensure_watch_idle_team(twf=twf, team_dir=team_dir, registry=registry)

    if task_path:
        cfg = _read_yaml_or_json(_config_file())
        desired_role = str(getattr(args, "task_to", "") or "").strip()
        if not desired_role:
            desired_role = _init_task_to_role(cfg)
        if bool(getattr(args, "no_task_notify", False)):
            desired_role = ""

        target_full = ""
        target_role = ""
        if desired_role:
            desired_norm = _norm_role(desired_role)
            for m in members:
                if m.role == desired_norm:
                    target_full = m.full
                    target_role = m.role
                    break
        if not target_full and desired_role:
            target_full = root_full.strip()
            target_role = root_role if target_full else ""

        if target_full and target_role:
            msg = "[TASK]\n" f"Shared task file: {task_path}\n" "Please read it and proceed.\n"
            sender_full = root_full.strip() or "atwf-init"
            sender_m = _resolve_member(_load_registry(registry), sender_full) or {}
            from_role = _member_role(sender_m) or root_role
            from_base = _member_base(sender_m) or sender_full

            target_m = _resolve_member(_load_registry(registry), target_full) or {}
            to_role = _member_role(target_m) or target_role
            to_base = _member_base(target_m) or target_full

            msg_id = _next_msg_id(team_dir)
            _write_inbox_message(
                team_dir,
                msg_id=msg_id,
                kind="task",
                from_full=sender_full,
                from_base=from_base,
                from_role=from_role,
                to_full=target_full,
                to_base=to_base,
                to_role=to_role,
                body=msg,
            )
            notice = _inbox_notice(msg_id)
            wrapped = _wrap_team_message(
                team_dir,
                kind="task",
                sender_full=sender_full,
                sender_role=from_role or None,
                to_full=target_full,
                body=notice,
                msg_id=msg_id,
            )

            # Back-compat: when PM exists, keep waiting for the initial PM reply.
            twf_cmd = "ask" if target_role == "pm" else "send"
            res = _run_twf(twf, [twf_cmd, target_full, wrapped])
            sys.stdout.write(res.stdout)
            sys.stderr.write(res.stderr)
            return res.returncode

        _eprint(f"✅ shared task saved: {task_path}")
        return 0

    _eprint("   next: atwf init \"任务描述：...\" (or: atwf init --task-file /abs/path).")
    return 0

def cmd_up(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    role = _require_role(args.role)
    if not bool(getattr(args, "no_bootstrap", False)):
        _validate_templates_or_die()
    policy = _policy()
    if role != policy.root_role:
        raise SystemExit(f"❌ up only allowed for root_role={policy.root_role}. Use `atwf spawn` / `atwf spawn-self`.")

    data0 = _load_registry(registry)
    existing_roots = []
    for m in data0.get("members", []) if isinstance(data0.get("members"), list) else []:
        if not isinstance(m, dict):
            continue
        if str(m.get("role", "")).strip() != policy.root_role:
            continue
        parent = m.get("parent")
        parent_s = str(parent).strip() if isinstance(parent, str) else ""
        if not parent_s:
            existing_roots.append(str(m.get("full", "")).strip())
    if existing_roots:
        raise SystemExit(f"❌ root already exists in registry (use `atwf init` / `atwf resume`): {existing_roots[0]}")

    base = _base_name(role, args.label)

    up_args: list[str] = []
    provider = _normalize_provider(getattr(args, "provider", "codex"), default="codex")
    up_args += ["--provider", provider]
    work_dir = str(getattr(args, "work_dir", "") or "").strip()
    if work_dir:
        up_args += ["--work-dir", work_dir]

    full, session_path = _start_worker(twf, base=base, up_args=up_args)

    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)
        _ensure_member(
            data,
            full=full,
            base=base,
            role=role,
            scope=args.scope or "",
            parent=None,
            state_file=str(session_path),
        )
        _write_json_atomic(registry, data)

    if not args.no_bootstrap:
        _bootstrap_worker(
            twf,
            name=full,
            role=role,
            full=full,
            base=base,
            registry=registry,
            team_dir=team_dir,
        )

    _ensure_cap_watch_team(twf=twf, team_dir=team_dir, registry=registry)
    _ensure_watch_idle_team(twf=twf, team_dir=team_dir, registry=registry)

    print(full)
    return 0

def cmd_spawn(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    if not bool(getattr(args, "no_bootstrap", False)):
        _validate_templates_or_die()

    parent_raw = args.parent_full.strip()
    if not parent_raw:
        raise SystemExit("❌ parent-full is required")

    data0 = _load_registry(registry)
    parent_full = _resolve_target_full(data0, parent_raw)
    if not parent_full:
        raise SystemExit(f"❌ parent not found in registry: {parent_raw}")

    role = _require_role(args.role)
    base = _base_name(role, args.label)

    parent_m = _resolve_member(data0, parent_full)
    if not parent_m:
        raise SystemExit(f"❌ parent not found in registry: {parent_full}")
    parent_role = str(parent_m.get("role", "")).strip()
    if not parent_role:
        raise SystemExit(f"❌ parent has no role recorded: {parent_full}")
    policy = _policy()
    allowed = policy.can_hire.get(parent_role, frozenset())
    if role not in allowed:
        raise SystemExit(
            f"❌ policy.can_hire: {parent_role} cannot hire {role}. "
            f"Allowed: {', '.join(sorted(allowed)) or '(none)'}"
        )

    up_args: list[str] = []
    requested_provider = _normalize_provider(getattr(args, "provider", ""), default="")
    if requested_provider:
        provider = requested_provider
    else:
        parent_state_file_raw = str(parent_m.get("state_file") or "").strip() if isinstance(parent_m, dict) else ""
        parent_state_file = _expand_path(parent_state_file_raw) if parent_state_file_raw else None
        provider = _provider_from_state_file(parent_state_file)
    up_args += ["--provider", provider]
    work_dir = str(getattr(args, "work_dir", "") or "").strip()
    if work_dir:
        up_args += ["--work-dir", work_dir]

    full, session_path = _spawn_worker(twf, parent_full=parent_full, child_base=base, up_args=up_args)

    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)
        _ensure_member(
            data,
            full=full,
            base=base,
            role=role,
            scope=args.scope or "",
            parent=parent_full,
            state_file=str(session_path),
        )
        _add_child(data, parent_full=parent_full, child_full=full)
        _write_json_atomic(registry, data)

    if not args.no_bootstrap:
        _bootstrap_worker(
            twf,
            name=full,
            role=role,
            full=full,
            base=base,
            registry=registry,
            team_dir=team_dir,
        )

    _ensure_cap_watch_team(twf=twf, team_dir=team_dir, registry=registry)
    _ensure_watch_idle_team(twf=twf, team_dir=team_dir, registry=registry)

    print(full)
    return 0

def cmd_spawn_self(args: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ spawn-self must run inside tmux")
    parent_full = res.stdout.strip()
    if not parent_full:
        raise SystemExit("❌ failed to detect current tmux session name")

    ns = argparse.Namespace(
        parent_full=parent_full,
        role=args.role,
        label=args.label,
        scope=args.scope,
        provider=getattr(args, "provider", ""),
        no_bootstrap=args.no_bootstrap,
        work_dir=getattr(args, "work_dir", ""),
    )
    return cmd_spawn(ns)

def cmd_parent(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    name = args.name.strip()
    if not name:
        raise SystemExit("❌ name is required")

    m = _resolve_member(data, name)
    if not m:
        raise SystemExit(f"❌ member not found in registry: {name}")

    parent = m.get("parent")
    parent_s = str(parent).strip() if isinstance(parent, str) else ""
    print(parent_s if parent_s else "(none)")
    return 0

def cmd_parent_self(_: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ parent-self must run inside tmux")
    name = res.stdout.strip()
    ns = argparse.Namespace(name=name)
    return cmd_parent(ns)

def cmd_children(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    name = args.name.strip()
    if not name:
        raise SystemExit("❌ name is required")

    m = _resolve_member(data, name)
    if not m:
        raise SystemExit(f"❌ member not found in registry: {name}")

    children = m.get("children")
    if not isinstance(children, list):
        children = []
    out = [str(c).strip() for c in children if isinstance(c, str) and c.strip()]
    if not out:
        print("(none)")
        return 0
    print("\n".join(out))
    return 0

def cmd_children_self(_: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ children-self must run inside tmux")
    name = res.stdout.strip()
    ns = argparse.Namespace(name=name)
    return cmd_children(ns)

def _read_report_body(args_message: str | None) -> str:
    msg = args_message
    if msg is None:
        msg = _forward_stdin()
    msg = (msg or "").strip()
    if not msg:
        raise SystemExit("❌ report message missing (provide as arg or via stdin)")
    return msg

def _format_report(*, sender: dict[str, Any], to_full: str, body: str) -> str:
    role = str(sender.get("role", "")).strip()
    base = str(sender.get("base", "")).strip()
    full = str(sender.get("full", "")).strip()
    scope = str(sender.get("scope", "")).strip()
    ts = _now()

    header = [
        f"[REPORT] {ts}",
        f"from: {full} (role={role} base={base})",
        f"to:   {to_full}",
    ]
    if scope:
        header.append(f"scope: {scope}")
    header.append("---")
    return "\n".join(header) + "\n" + body.strip() + "\n"

def cmd_report_up(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ report-up must run inside tmux")
    self_name = res.stdout.strip()
    if not self_name:
        raise SystemExit("❌ failed to detect current tmux session name")

    data = _load_registry(registry)
    sender = _resolve_member(data, self_name)
    if not sender:
        raise SystemExit(f"❌ current worker not found in registry: {self_name} (run: atwf register-self ...)")

    parent = sender.get("parent")
    parent_full = str(parent).strip() if isinstance(parent, str) else ""
    if not parent_full:
        raise SystemExit("❌ no parent recorded for this worker (root). Use report-to <role|name> instead.")

    parent_m = _resolve_member(data, parent_full) or {}
    to_role = _member_role(parent_m)
    to_base = _member_base(parent_m) or parent_full

    body = _read_report_body(args.message)
    msg = _format_report(sender=sender, to_full=parent_full, body=body)

    from_role = str(sender.get("role", "")).strip()
    from_base = _member_base(sender) or self_name
    msg_id = _next_msg_id(team_dir)
    _write_inbox_message(
        team_dir,
        msg_id=msg_id,
        kind="report-up",
        from_full=self_name,
        from_base=from_base,
        from_role=from_role,
        to_full=parent_full,
        to_base=to_base,
        to_role=to_role,
        body=msg,
    )
    notice = _inbox_notice(msg_id)
    wrapped = _wrap_team_message(
        team_dir,
        kind="report-up",
        sender_full=self_name,
        sender_role=from_role or None,
        to_full=parent_full,
        body=notice,
        msg_id=msg_id,
    )

    # Default: inbox-only delivery (no CLI injection). This avoids consuming the
    # recipient's Codex context. Recipients must poll inbox while working.
    if not bool(getattr(args, "wait", False)):
        print(msg_id)
        return 0

    # Exceptional: allow an explicit blocking request (CLI injection) when the
    # operator intentionally chooses to wait for a reply.
    twf = _resolve_twf()
    res2 = _run_twf(twf, ["ask", parent_full, wrapped])
    sys.stdout.write(res2.stdout)
    sys.stderr.write(res2.stderr)
    return res2.returncode

def cmd_report_to(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    data = _load_registry(registry)

    to_full = _resolve_target_full(data, target)
    if not to_full:
        raise SystemExit(f"❌ target not found in registry: {target}")

    self_name = _tmux_self_full()
    if not self_name:
        raise SystemExit("❌ report-to must run inside tmux")

    sender = _resolve_member(data, self_name)
    if not sender:
        raise SystemExit(f"❌ current worker not found in registry: {self_name} (run: atwf register-self ...)")

    policy = _policy()
    _require_comm_allowed(policy, data, actor_full=self_name, target_full=to_full)

    target_m = _resolve_member(data, to_full) or {}
    to_role = _member_role(target_m)
    to_base = _member_base(target_m) or to_full

    body = _read_report_body(args.message)
    msg = _format_report(sender=sender, to_full=to_full, body=body)

    from_role = str(sender.get("role", "")).strip()
    from_base = _member_base(sender) or self_name
    msg_id = _next_msg_id(team_dir)
    _write_inbox_message(
        team_dir,
        msg_id=msg_id,
        kind="report-to",
        from_full=self_name,
        from_base=from_base,
        from_role=from_role,
        to_full=to_full,
        to_base=to_base,
        to_role=to_role,
        body=msg,
    )
    notice = _inbox_notice(msg_id)
    wrapped = _wrap_team_message(
        team_dir,
        kind="report-to",
        sender_full=self_name,
        sender_role=from_role or None,
        to_full=to_full,
        body=notice,
        msg_id=msg_id,
    )

    # Default: inbox-only delivery (no CLI injection).
    if not bool(getattr(args, "wait", False)):
        print(msg_id)
        return 0

    # Exceptional: blocking request (CLI injection) when --wait is used.
    twf = _resolve_twf()
    res2 = _run_twf(twf, ["ask", to_full, wrapped])
    sys.stdout.write(res2.stdout)
    sys.stderr.write(res2.stderr)
    return res2.returncode

def cmd_self(_: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ not inside tmux (or tmux unavailable)")
    print(res.stdout.strip())
    return 0

def cmd_register(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    full = args.full.strip()
    if not full:
        raise SystemExit("❌ full name is required")

    base = args.base.strip() if args.base else None
    role = _require_role(args.role) if args.role else None
    policy = _policy()

    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)
        existing = _resolve_member(data, full)
        existing_role = str(existing.get("role", "")).strip() if isinstance(existing, dict) else ""
        existing_parent = existing.get("parent") if isinstance(existing, dict) else None
        existing_parent_s = str(existing_parent).strip() if isinstance(existing_parent, str) else ""

        resolved_parent: str | None = None
        if args.parent is not None:
            parent_raw = str(args.parent).strip()
            if parent_raw:
                resolved_parent = _resolve_target_full(data, parent_raw) or (parent_raw if FULL_NAME_RE.match(parent_raw) else None)
                if not resolved_parent:
                    raise SystemExit(f"❌ parent not found in registry: {parent_raw}")
            else:
                resolved_parent = ""

        final_role = role or existing_role
        final_parent = existing_parent_s if args.parent is None else (resolved_parent or "")
        force = bool(getattr(args, "force", False))
        if final_role:
            if final_role == policy.root_role:
                if final_parent and not force:
                    raise SystemExit(f"❌ root_role={policy.root_role} cannot have a parent (use --force to override)")
            else:
                if not final_parent and not force:
                    raise SystemExit(
                        f"❌ non-root roles must have a parent (root_role={policy.root_role}). "
                        f"Use `atwf spawn`/`spawn-self` or pass --parent/--force."
                    )
                parent_m = _resolve_member(data, final_parent) if final_parent else None
                parent_role = _member_role(parent_m)
                allowed = policy.can_hire.get(parent_role, frozenset())
                if final_parent and (not parent_m or final_role not in allowed) and not force:
                    raise SystemExit(
                        f"❌ policy.can_hire: {parent_role or '(missing)'} cannot hire {final_role}. "
                        f"Allowed: {', '.join(sorted(allowed)) or '(none)'}"
                    )

        _ensure_member(
            data,
            full=full,
            base=base,
            role=role,
            scope=args.scope,
            parent=(resolved_parent if args.parent is not None else None),
            state_file=args.state_file,
        )
        if args.parent is not None and resolved_parent:
            _add_child(data, parent_full=resolved_parent, child_full=full)
        _write_json_atomic(registry, data)
    _eprint(f"✅ registered: {full}")
    return 0

def cmd_register_self(args: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ register-self must run inside tmux")
    full = res.stdout.strip()

    ns = argparse.Namespace(
        full=full,
        role=args.role,
        base=args.base,
        scope=args.scope,
        parent=args.parent,
        state_file=args.state_file,
        force=getattr(args, "force", False),
    )
    return cmd_register(ns)

def cmd_set_scope(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    name = args.name.strip()
    if not name:
        raise SystemExit("❌ name is required")
    scope = args.scope

    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)
        m = _resolve_member(data, name)
        if not m:
            raise SystemExit(f"❌ member not found in registry: {name}")
        full = str(m.get("full", "")).strip() or name
        _ensure_member(data, full=full, scope=scope)
        _write_json_atomic(registry, data)
    _eprint(f"✅ scope updated: {name}")
    return 0

def cmd_set_scope_self(args: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ set-scope-self must run inside tmux")
    name = res.stdout.strip()
    ns = argparse.Namespace(name=name, scope=args.scope)
    return cmd_set_scope(ns)

def cmd_list(_: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    members = data.get("members", [])
    if not isinstance(members, list) or not members:
        print("(empty)")
        return 0

    def cell(v: Any) -> str:
        s = str(v) if v is not None else ""
        return " ".join(s.split())

    rows = []
    for m in members:
        if not isinstance(m, dict):
            continue
        rows.append(
            (
                cell(m.get("role", "")),
                cell(m.get("base", "")),
                cell(m.get("full", "")),
                cell(m.get("parent", "")),
                cell(m.get("scope", "")),
            )
        )

    widths = [0, 0, 0, 0, 0]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    header = ("role", "base", "full", "parent", "scope")
    widths = [max(widths[i], len(header[i])) for i in range(5)]

    def fmt(r: tuple[str, str, str, str, str]) -> str:
        return "  ".join(r[i].ljust(widths[i]) for i in range(5))

    print(fmt(header))
    print(fmt(tuple("-" * w for w in widths)))  # type: ignore[arg-type]
    for r in rows:
        print(fmt(r))
    return 0

def cmd_where(_: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    print(str(team_dir))
    print(str(registry))
    return 0

def cmd_templates_check(_: argparse.Namespace) -> int:
    issues = _template_lint_issues()
    if issues:
        for s in issues:
            _eprint(s)
        return 1
    print("OK")
    return 0

def cmd_policy(_: argparse.Namespace) -> int:
    policy = _policy()
    print(f"config: {_config_file()}")
    print(f"root_role: {policy.root_role}")
    print(f"enabled_roles: {', '.join(sorted(policy.enabled_roles))}")

    print("can_hire:")
    for parent in sorted(policy.can_hire):
        kids = sorted(policy.can_hire.get(parent, frozenset()))
        print(f"  {parent}: {', '.join(kids) if kids else '(none)'}")

    print(f"broadcast.allowed_roles: {', '.join(sorted(policy.broadcast_allowed_roles)) or '(none)'}")
    print(f"broadcast.exclude_roles: {', '.join(sorted(policy.broadcast_exclude_roles)) or '(none)'}")

    print(f"comm.allow_parent_child: {policy.comm_allow_parent_child}")
    print(f"comm.require_handoff: {policy.comm_require_handoff}")
    print(f"comm.handoff_creators: {', '.join(sorted(policy.comm_handoff_creators)) or '(none)'}")

    print("comm.direct_allow:")
    for role in sorted(policy.enabled_roles):
        allowed = sorted(policy.comm_direct_allow.get(role, frozenset()))
        if not allowed:
            continue
        print(f"  {role}: {', '.join(allowed)}")
    return 0

def cmd_perms_self(_: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    policy = _policy()

    self_full = _tmux_self_full()
    if not self_full:
        raise SystemExit("❌ perms-self must run inside tmux")

    self_m = _resolve_member(data, self_full)
    if not self_m:
        raise SystemExit(f"❌ current worker not found in registry: {self_full} (run: atwf register-self ...)")

    role = _member_role(self_m)
    base = _member_base(self_m)
    parent = str(self_m.get("parent", "")).strip() if isinstance(self_m.get("parent"), str) else ""

    print(f"full: {self_full}")
    print(f"role: {role or '(missing)'}")
    print(f"base: {base}")
    print(f"parent: {parent or '(none)'}")
    print(f"can_broadcast: {role in policy.broadcast_allowed_roles}")
    print(f"can_create_handoff: {role in policy.comm_handoff_creators}")
    print(f"can_hire: {', '.join(sorted(policy.can_hire.get(role, frozenset()))) or '(none)'}")
    print(f"direct_allow_roles: {', '.join(sorted(policy.comm_direct_allow.get(role, frozenset()))) or '(none)'}")

    peers: list[str] = []
    permits = data.get("permits")
    if isinstance(permits, list):
        for p in permits:
            if not isinstance(p, dict):
                continue
            a = str(p.get("a", "")).strip()
            b = str(p.get("b", "")).strip()
            if not a or not b:
                continue
            other = ""
            if a == base:
                other = b
            elif b == base:
                other = a
            if not other:
                continue
            exp = str(p.get("expires_at", "")).strip()
            if exp:
                try:
                    if datetime.fromisoformat(exp) <= datetime.now():
                        continue
                except Exception:
                    pass
            peers.append(other)
    uniq_peers = sorted({p for p in peers if p})
    print(f"active_handoff_peers: {', '.join(uniq_peers) if uniq_peers else '(none)'}")
    return 0

def cmd_handoff(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    policy = _policy()

    lock = team_dir / ".lock"
    dry_run = bool(getattr(args, "dry_run", False))
    permit: dict[str, Any] | None = None
    permit_exists = False
    with _locked(lock):
        data = _load_registry(registry)
        actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
        actor_m = _resolve_member(data, actor_full)
        if not actor_m:
            raise SystemExit(f"❌ actor not found in registry: {actor_full}")
        actor_role = _member_role(actor_m)
        if actor_role not in policy.comm_handoff_creators:
            raise SystemExit(
                "❌ handoff not permitted by policy.\n"
                f"   actor: {actor_full} (role={actor_role or '?'})\n"
                f"   allowed_roles: {', '.join(sorted(policy.comm_handoff_creators)) or '(none)'}"
            )

        a_full = _resolve_target_full(data, args.a)
        if not a_full:
            raise SystemExit(f"❌ member not found in registry: {args.a}")
        b_full = _resolve_target_full(data, args.b)
        if not b_full:
            raise SystemExit(f"❌ member not found in registry: {args.b}")
        a_m = _resolve_member(data, a_full)
        b_m = _resolve_member(data, b_full)
        if not a_m or not b_m:
            raise SystemExit("❌ handoff endpoints must be registered members")

        a_base = _member_base(a_m)
        b_base = _member_base(b_m)
        permit_exists = _permit_allows(data, a_base=a_base, b_base=b_base)
        if not permit_exists and not dry_run:
            permit = _add_handoff_permit(
                data,
                a_base=a_base,
                b_base=b_base,
                created_by=actor_full,
                created_by_role=actor_role,
                reason=str(getattr(args, "reason", "") or ""),
                ttl_seconds=(int(args.ttl) if getattr(args, "ttl", None) is not None else None),
            )
            _write_json_atomic(registry, data)

    if dry_run:
        print("dry_run: true")
        print(f"actor: {actor_full}")
        print(f"a: {a_full}")
        print(f"b: {b_full}")
        print(f"permit_exists: {str(permit_exists).lower()}")
        if not permit_exists:
            print("permit_id: (would-create)")
        return 0

    exp_s = ""
    if permit:
        exp_s = str(permit.get("expires_at", "")).strip()
    reason = str(getattr(args, "reason", "") or "").strip()
    reason_line = f"reason: {reason}\n" if reason else ""
    exp_line = f"expires_at: {exp_s}\n" if exp_s else ""

    msg_a = (
        "[HANDOFF]\n"
        f"creator: {actor_full} (role={actor_role or '?'})\n"
        f"peer: {b_base} ({b_full})\n"
        f"{reason_line}{exp_line}"
        "You are permitted to talk directly. Use:\n"
        f"- atwf send {b_base} \"...\"  # inbox-only by default; peer must poll inbox while working\n"
    )
    msg_b = (
        "[HANDOFF]\n"
        f"creator: {actor_full} (role={actor_role or '?'})\n"
        f"peer: {a_base} ({a_full})\n"
        f"{reason_line}{exp_line}"
        "Please reply directly to the requester (avoid relaying via coord).\n"
        "Use:\n"
        f"- atwf send {a_base} \"...\"  # inbox-only by default; peer must poll inbox while working\n"
    )

    handoff_id = _next_msg_id(team_dir)
    notice = _inbox_notice(handoff_id)

    _write_inbox_message(
        team_dir,
        msg_id=handoff_id,
        kind="handoff",
        from_full=actor_full,
        from_base=_member_base(actor_m) or actor_full,
        from_role=actor_role or "?",
        to_full=a_full,
        to_base=a_base,
        to_role=_member_role(a_m) or "?",
        body=msg_a,
    )
    _write_inbox_message(
        team_dir,
        msg_id=handoff_id,
        kind="handoff",
        from_full=actor_full,
        from_base=_member_base(actor_m) or actor_full,
        from_role=actor_role or "?",
        to_full=b_full,
        to_base=b_base,
        to_role=_member_role(b_m) or "?",
        body=msg_b,
    )

    wrapped_a = _wrap_team_message(
        team_dir,
        kind="handoff",
        sender_full=actor_full,
        sender_role=actor_role or None,
        to_full=a_full,
        body=notice,
        msg_id=handoff_id,
    )
    wrapped_b = _wrap_team_message(
        team_dir,
        kind="handoff",
        sender_full=actor_full,
        sender_role=actor_role or None,
        to_full=b_full,
        body=notice,
        msg_id=handoff_id,
    )

    if bool(getattr(args, "notify", False)):
        twf = _resolve_twf()
        res_a = _run_twf(twf, ["send", a_full, wrapped_a])
        if res_a.returncode != 0:
            _eprint(res_a.stderr.strip() or f"⚠️ notify failed: {a_full}")
        res_b = _run_twf(twf, ["send", b_full, wrapped_b])
        if res_b.returncode != 0:
            _eprint(res_b.stderr.strip() or f"⚠️ notify failed: {b_full}")

    print(str(permit.get("id", "")) if permit else "(existing)")
    return 0

def cmd_tree(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    members = data.get("members", [])
    if not isinstance(members, list) or not members:
        print("(empty)")
        return 0

    root_fulls: list[str]
    if getattr(args, "root", None):
        target = str(args.root).strip()
        if not target:
            raise SystemExit("❌ root is required")
        full = _resolve_target_full(data, target)
        if not full:
            raise SystemExit(f"❌ target not found in registry: {target}")
        root_fulls = [full]
    else:
        root_fulls = _tree_roots(data)

    children_map = _tree_children(data)
    visited: set[str] = set()

    def label(full: str) -> str:
        m = _resolve_member(data, full) or {}
        role = str(m.get("role", "")).strip()
        scope = str(m.get("scope", "")).strip()
        status = "running" if _tmux_running(full) else "stopped"
        parts = [f"[{role or '?'}]", full, f"({status})"]
        if scope:
            parts.append(f"- {scope}")
        return " ".join(parts)

    def walk(full: str, indent: str) -> None:
        if full in visited:
            print(f"{indent}{label(full)}  (cycle)")
            return
        visited.add(full)
        print(f"{indent}{label(full)}")
        for child in children_map.get(full, []):
            walk(child, indent + "  ")

    for i, root in enumerate(root_fulls):
        if i > 0:
            print("")
        walk(root, "")
    return 0

def cmd_design_path(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ target not found in registry: {target}")

    print(str(_design_member_path(team_dir, full)))
    return 0

def cmd_design_init(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ target not found in registry: {target}")

    _ensure_share_layout(team_dir)
    path = _design_member_path(team_dir, full)
    if path.exists() and not args.force:
        print(str(path))
        return 0

    m = _resolve_member(data, full) or {}
    _write_text_atomic(path, _design_seed(member=m, full=full, team_dir=team_dir))
    print(str(path))
    return 0

def cmd_design_init_self(args: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ design-init-self must run inside tmux")
    full = res.stdout.strip()
    if not full:
        raise SystemExit("❌ failed to detect current tmux session name")
    ns = argparse.Namespace(target=full, force=bool(args.force))
    return cmd_design_init(ns)

def cmd_worktree_path(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ target not found in registry: {target}")

    repo_raw = str(getattr(args, "repo", "") or "").strip()
    dest_root_raw = str(getattr(args, "dest_root", "") or "").strip()
    name_raw = str(getattr(args, "name", "") or "").strip()

    if repo_raw:
        repo_dir = _expand_path(repo_raw)
        if repo_dir.is_file():
            repo_dir = repo_dir.parent
        if not repo_dir.is_dir():
            raise SystemExit(f"❌ --repo is not a directory: {repo_dir}")
        repo_root = _git_root_from(repo_dir)

        m = _resolve_member(data, full) or {}
        dest_root = _expand_path(dest_root_raw) if dest_root_raw else _member_work_dir(m)
        if dest_root:
            name = name_raw or repo_root.name
            if not name.strip():
                raise SystemExit("❌ --name resolved to empty (pass --name explicitly)")
            print(str(dest_root / name.strip()))
            return 0

        # Fallback: per-repo dedicated path (legacy style).
        print(str(_worktree_path(repo_root, full)))
        return 0

    git_root = _git_root()
    print(str(_worktree_path(git_root, full)))
    return 0

def cmd_worktree_create(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ target not found in registry: {target}")

    base = (args.base or "HEAD").strip() or "HEAD"
    branch = (args.branch or full).strip() or full

    repo_raw = str(getattr(args, "repo", "") or "").strip()
    dest_root_raw = str(getattr(args, "dest_root", "") or "").strip()
    name_raw = str(getattr(args, "name", "") or "").strip()

    if repo_raw:
        repo_dir = _expand_path(repo_raw)
        if repo_dir.is_file():
            repo_dir = repo_dir.parent
        if not repo_dir.is_dir():
            raise SystemExit(f"❌ --repo is not a directory: {repo_dir}")
        repo_root = _git_root_from(repo_dir)

        dest_root: Path | None = None
        if dest_root_raw:
            dest_root = _expand_path(dest_root_raw)
        else:
            m = _resolve_member(data, full) or {}
            dest_root = _member_work_dir(m)

        if dest_root:
            dest_root.mkdir(parents=True, exist_ok=True)
            name = name_raw or repo_root.name
            name = name.strip()
            if not name:
                raise SystemExit("❌ --name resolved to empty (pass --name explicitly)")
            path = dest_root / name
        else:
            wt_dir = _worktrees_dir(repo_root)
            wt_dir.mkdir(parents=True, exist_ok=True)
            path = _worktree_path(repo_root, full)

        if path.exists():
            print(str(path))
            return 0

        path.parent.mkdir(parents=True, exist_ok=True)
        res = _run(["git", "-C", str(repo_dir), "worktree", "add", "-b", branch, str(path), base])
        if res.returncode != 0:
            err = (res.stderr or "").strip()
            raise SystemExit(err or f"❌ git worktree add failed (code {res.returncode})")

        print(str(path))
        return 0

    git_root = _git_root()
    wt_dir = _worktrees_dir(git_root)
    wt_dir.mkdir(parents=True, exist_ok=True)

    path = _worktree_path(git_root, full)
    if path.exists():
        print(str(path))
        return 0

    res = _run(["git", "worktree", "add", "-b", branch, str(path), base])
    if res.returncode != 0:
        err = (res.stderr or "").strip()
        raise SystemExit(err or f"❌ git worktree add failed (code {res.returncode})")

    print(str(path))
    return 0

def cmd_worktree_create_self(args: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ worktree-create-self must run inside tmux")
    full = res.stdout.strip()
    if not full:
        raise SystemExit("❌ failed to detect current tmux session name")
    ns = argparse.Namespace(
        target=full,
        base=args.base,
        branch=args.branch,
        repo=getattr(args, "repo", ""),
        dest_root=getattr(args, "dest_root", ""),
        name=getattr(args, "name", ""),
    )
    return cmd_worktree_create(ns)

def cmd_worktree_check_self(_: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ worktree-check-self must run inside tmux")
    full = res.stdout.strip()
    if not full:
        raise SystemExit("❌ failed to detect current tmux session name")

    expected: Path | None = None
    try:
        twf = _resolve_twf()
        state_dir = _resolve_twf_state_dir(twf)
        state_path = (state_dir / f"{full}.json").resolve()
        if state_path.is_file():
            state = _read_json(state_path)
            raw = state.get("work_dir_norm") or state.get("work_dir")
            if isinstance(raw, str) and raw.strip():
                expected = Path(os.path.expanduser(raw.strip())).resolve()
    except Exception:
        expected = None

    if expected is None:
        git_root = _git_root()
        expected = _worktree_path(git_root, full).resolve()
    cwd = Path.cwd().resolve()

    if expected == cwd or expected in cwd.parents:
        print("OK")
        return 0

    _eprint("❌ not in your dedicated worktree")
    _eprint(f"   expected: {expected}")
    _eprint(f"   cwd:      {cwd}")
    _eprint(f"   fix:      {_atwf_cmd()} worktree-create-self && cd {expected}")
    return 1

def cmd_stop(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    subtree_base_for_drive = ""
    subtree_arg = getattr(args, "subtree", None)
    if subtree_arg:
        root_full = _resolve_target_full(data, str(subtree_arg))
        if root_full:
            root_m = _resolve_member(data, root_full)
            if _member_role(root_m) == "admin":
                subtree_base_for_drive = _member_base(root_m) or root_full

    targets = _select_targets_for_team_op(
        data,
        targets=getattr(args, "targets", None),
        role=getattr(args, "role", None),
        subtree=getattr(args, "subtree", None),
    )
    if not targets:
        print("(no targets)")
        return 0

    # Prefer stopping leaves first if a subtree is selected.
    if getattr(args, "subtree", None):
        targets = list(reversed(targets))

    if getattr(args, "dry_run", False):
        print("\n".join(targets))
        return 0

    if subtree_base_for_drive:
        _set_drive_subtree_status(
            team_dir,
            base=subtree_base_for_drive,
            status=_DRIVE_SUBTREE_STATUS_STOPPED,
            reason="stopped_via_atwf_stop",
        )

    failures: list[str] = []
    for full in targets:
        sys.stdout.write(f"--- stop {full} ---\n")
        res = _run_twf(twf, ["stop", full])
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        if res.returncode != 0:
            failures.append(full)

    if failures:
        _eprint(f"❌ stop failures: {len(failures)} targets")
        return 1
    return 0

def cmd_pause(args: argparse.Namespace) -> int:
    """
    Human-facing pause:
    - writes a shared marker to disable watcher actions (`share/.paused`)
    - stops workers (same selection rules as `stop`)
    """
    team_dir = _default_team_dir()
    reason = _read_optional_message(args, attr="reason")
    _set_paused(team_dir, reason=reason)
    _eprint(f"⏸️ paused: {_paused_marker_path(team_dir)}")

    # Stop the watcher session so future updates are picked up on unpause.
    if not bool(getattr(args, "dry_run", False)):
        _tmux_kill_session(_watch_idle_session_name(_expected_project_root(), team_dir=team_dir))
    return cmd_stop(
        argparse.Namespace(
            targets=getattr(args, "targets", None),
            role=getattr(args, "role", None),
            subtree=getattr(args, "subtree", None),
            dry_run=getattr(args, "dry_run", False),
        )
    )

def cmd_resume(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    _ensure_cap_watch_team(twf=twf, team_dir=team_dir, registry=registry)
    _ensure_watch_idle_team(twf=twf, team_dir=team_dir, registry=registry)

    subtree_base_for_drive = ""
    subtree_arg = getattr(args, "subtree", None)
    if subtree_arg:
        root_full = _resolve_target_full(data, str(subtree_arg))
        if root_full:
            root_m = _resolve_member(data, root_full)
            if _member_role(root_m) == "admin":
                subtree_base_for_drive = _member_base(root_m) or root_full

    targets = _select_targets_for_team_op(
        data,
        targets=getattr(args, "targets", None),
        role=getattr(args, "role", None),
        subtree=getattr(args, "subtree", None),
    )
    if not targets:
        print("(no targets)")
        return 0

    if getattr(args, "dry_run", False):
        print("\n".join(targets))
        return 0

    if subtree_base_for_drive:
        _set_drive_subtree_status(
            team_dir,
            base=subtree_base_for_drive,
            status=_DRIVE_SUBTREE_STATUS_ACTIVE,
            reason="",
        )

    failures: list[str] = []
    for full in targets:
        sys.stdout.write(f"--- resume {full} ---\n")
        res = _run_twf(twf, ["resume", full, "--no-tree"])
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        if res.returncode != 0:
            failures.append(full)

    if failures:
        _eprint(f"❌ resume failures: {len(failures)} targets")
        return 1
    return 0

def cmd_unpause(args: argparse.Namespace) -> int:
    """
    Human-facing unpause:
    - clears the pause marker (`share/.paused`)
    - resumes workers (same selection rules as `resume`)
    - restarts watcher processes so updates take effect
    """
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    _clear_paused(team_dir)
    _eprint(f"▶️ unpaused: {_paused_marker_path(team_dir)}")

    if bool(getattr(args, "dry_run", False)):
        # In dry-run, do not change watcher state.
        pass
    else:
        _restart_watch_idle_team(twf=twf, team_dir=team_dir, registry=registry)

    targets = _select_targets_for_team_op(
        data,
        targets=getattr(args, "targets", None),
        role=getattr(args, "role", None),
        subtree=getattr(args, "subtree", None),
    )
    if not targets:
        print("(no targets)")
        return 0

    if getattr(args, "dry_run", False):
        print("\n".join(targets))
        return 0

    failures: list[str] = []
    for full in targets:
        sys.stdout.write(f"--- resume {full} ---\n")
        res = _run_twf(twf, ["resume", full, "--no-tree"])
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        if res.returncode != 0:
            failures.append(full)

    if failures:
        _eprint(f"❌ resume failures: {len(failures)} targets")
        return 1
    return 0

def _delete_drive_subtree_entries(team_dir: Path, *, bases: list[str]) -> None:
    bases = [str(b or "").strip() for b in (bases or [])]
    bases = [b for b in bases if b]
    if not bases:
        return

    lock = _state_lock_path(team_dir)
    with _locked(lock):
        _ensure_share_layout(team_dir)
        data = _load_drive_subtree_state_unlocked(team_dir, mode_default=_drive_mode_config_hot())
        subs = data.get("subtrees")
        if isinstance(subs, dict):
            for base in bases:
                subs.pop(base, None)
        data["updated_at"] = _now()
        _write_json_atomic(_drive_subtree_state_path(team_dir), data)

def cmd_remove_subtree(args: argparse.Namespace) -> int:
    """
    Remove a request subtree (typically an `admin-<REQ-ID>` chain):
    - delete workers via twf (best-effort)
    - prune members from registry.json
    - clean atwf per-agent state files
    - optionally purge inbox dirs
    """
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    policy = _policy()

    root = str(getattr(args, "root", "") or "").strip()
    if not root:
        raise SystemExit("❌ root is required (use full or base name, e.g. admin-REQ-001)")
    if root in set(policy.enabled_roles):
        raise SystemExit(f"❌ root must be a specific member (full|base), not a role name: {root}")

    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)

        root_full = _resolve_target_full(data, root)
        if not root_full:
            raise SystemExit(f"❌ subtree root not found in registry: {root}")
        root_m = _resolve_member(data, root_full) or {}
        root_role = _member_role(root_m) or "?"
        root_base = _member_base(root_m) or root_full

        expected_role = (_drive_unit_role() or "").strip() or "admin"
        if not bool(getattr(args, "force", False)) and expected_role and root_role != expected_role:
            raise SystemExit(
                "❌ remove-subtree refused.\n"
                f"   expected subtree root role={expected_role!r} (config team.drive.unit_role)\n"
                f"   got: role={root_role!r} full={root_full} base={root_base}\n"
                "   If you really want this, pass: --force"
            )

        subtree = _subtree_fulls(data, root_full)
        if not subtree:
            raise SystemExit(f"❌ empty subtree: {root_full}")

        base_by_full: dict[str, str] = {}
        for full in subtree:
            m = _resolve_member(data, full) or {}
            base_by_full[full] = _member_base(m) or full

    ordered = list(reversed(subtree))

    if bool(getattr(args, "dry_run", False)):
        print("\n".join(ordered))
        return 0

    failures: list[str] = []
    for full in ordered:
        res = _run_twf(twf, ["remove", full, "--no-recursive"])
        if res.returncode != 0:
            failures.append(full)
            err = (res.stderr or "").strip() or (res.stdout or "").strip()
            _eprint(f"⚠️ twf remove failed for {full}: {err or 'unknown error'}")

    with _locked(lock):
        data2 = _load_registry(registry)
        members = data2.get("members")
        if not isinstance(members, list):
            members = []

        removed = set(subtree)
        kept: list[Any] = []
        for m in members:
            if not isinstance(m, dict):
                kept.append(m)
                continue
            full = str(m.get("full", "")).strip()
            if full in removed:
                continue
            kept.append(m)

        for m in kept:
            if not isinstance(m, dict):
                continue
            parent = m.get("parent")
            if isinstance(parent, str) and parent in removed:
                m["parent"] = None
            children = m.get("children")
            if isinstance(children, list):
                m["children"] = [c for c in children if isinstance(c, str) and c not in removed]
        data2["members"] = kept
        data2["updated_at"] = _now()
        _write_json_atomic(registry, data2)

    for full in subtree:
        p = _agent_state_path(team_dir, full=full)
        try:
            if p.is_file():
                p.unlink()
        except Exception:
            pass

    _delete_drive_subtree_entries(team_dir, bases=[root_base])

    if bool(getattr(args, "purge_inbox", False)):
        uniq_bases: list[str] = []
        seen: set[str] = set()
        for full in subtree:
            b = str(base_by_full.get(full, "")).strip()
            if b and b not in seen:
                seen.add(b)
                uniq_bases.append(b)
        for base in uniq_bases:
            p = _inbox_member_dir(team_dir, base=base)
            try:
                if p.is_dir():
                    shutil.rmtree(p)
            except Exception:
                pass

    if failures:
        _eprint(f"❌ remove-subtree completed with failures: {len(failures)} workers (registry pruned anyway)")
        return 1
    _eprint(f"✅ subtree removed: {root_full} ({len(subtree)} workers)")
    return 0

def cmd_broadcast(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    policy = _policy()

    # Hard constraint: require explicit intent via `atwf notice` / `atwf action`.
    # Members running inside their worker tmux sessions must not use legacy broadcast.
    self_full = _tmux_self_full()
    if self_full and _resolve_member(data, self_full):
        raise SystemExit("❌ use `atwf notice` or `atwf action` (legacy `broadcast` is disabled for team members)")

    actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
    actor_m = _resolve_member(data, actor_full)
    if not actor_m:
        raise SystemExit(f"❌ actor not found in registry: {actor_full}")
    actor_role = _member_role(actor_m)
    actor_base = _member_base(actor_m) or actor_full
    if actor_role not in policy.broadcast_allowed_roles:
        raise SystemExit(
            "❌ broadcast not permitted by policy.\n"
            f"   actor: {actor_full} (role={actor_role or '?'})\n"
            f"   allowed_roles: {', '.join(sorted(policy.broadcast_allowed_roles)) or '(none)'}"
        )

    msg = args.message
    if msg is None:
        msg = _forward_stdin()
    if msg is None:
        raise SystemExit("❌ message missing (use --message or pipe via stdin)")
    msg = msg.strip()
    if not msg:
        raise SystemExit("❌ empty message")

    targets: list[str] = []
    if args.role:
        targets = _members_by_role(data, args.role)
    elif args.subtree:
        root = _resolve_target_full(data, args.subtree)
        if not root:
            raise SystemExit(f"❌ subtree root not found in registry: {args.subtree}")
        targets = _subtree_fulls(data, root)
        if not bool(getattr(args, "include_excluded", False)) and policy.broadcast_exclude_roles:
            filtered: list[str] = []
            for full in targets:
                m = _resolve_member(data, full)
                if _member_role(m) in policy.broadcast_exclude_roles:
                    continue
                filtered.append(full)
            targets = filtered
    else:
        raw_targets = getattr(args, "targets", []) or []
        if not isinstance(raw_targets, list) or not raw_targets:
            raise SystemExit("❌ targets are required (or use --role/--subtree)")
        for t in raw_targets:
            full = _resolve_target_full(data, str(t))
            if not full:
                raise SystemExit(f"❌ target not found in registry: {t}")
            targets.append(full)

    if not targets:
        raise SystemExit("❌ no targets matched")

    uniq: list[str] = []
    seen: set[str] = set()
    for t in targets:
        if t == actor_full:
            continue
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    bc_id = _next_msg_id(team_dir)
    notice = _inbox_notice(bc_id)

    lock = team_dir / ".lock"
    with _locked(lock):
        _ensure_share_layout(team_dir)
        max_unread = _inbox_max_unread_per_thread()
        for full in uniq:
            m = _resolve_member(data, full) or {}
            to_role = _member_role(m)
            to_base = _member_base(m) or full
            _write_inbox_message_unlocked(
                team_dir,
                msg_id=bc_id,
                kind="broadcast",
                from_full=actor_full,
                from_base=actor_base,
                from_role=actor_role or "?",
                to_full=full,
                to_base=to_base,
                to_role=to_role or "?",
                body=msg,
            )
            _inbox_enforce_unread_limit_unlocked(
                team_dir,
                to_base=to_base,
                from_base=actor_base,
                max_unread=max_unread,
            )

    # Default: inbox-only delivery. We still write inbox entries for all
    # recipients, but we do NOT inject into their Codex CLIs unless explicitly
    # requested.
    if not bool(getattr(args, "notify", False)):
        print(bc_id)
        return 0

    twf = _resolve_twf()
    failures2: list[str] = []
    max_workers2 = min(16, max(1, len(uniq)))
    with ThreadPoolExecutor(max_workers=max_workers2) as pool:
        futures = {
            pool.submit(
                _run_twf,
                twf,
                [
                    "send",
                    full,
                    _wrap_team_message(
                        team_dir,
                        kind="broadcast",
                        sender_full=actor_full,
                        sender_role=actor_role or None,
                        to_full=full,
                        body=notice,
                        msg_id=bc_id,
                    ),
                ],
            ): full
            for full in uniq
        }
        for fut in as_completed(futures):
            full = futures[fut]
            sys.stdout.write(f"--- {full} ---\n")
            try:
                res = fut.result()
            except Exception as exc:
                sys.stderr.write(f"❌ broadcast notify failed: {full}: {exc}\n")
                failures2.append(full)
                continue
            sys.stdout.write(res.stdout)
            sys.stderr.write(res.stderr)
            if res.returncode != 0:
                failures2.append(full)

    if failures2:
        _eprint(f"❌ broadcast notify failures: {len(failures2)} targets")
        return 1
    return 0

def cmd_resolve(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ target not found in registry: {target}")
    print(full)
    return 0

def cmd_attach(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ target not found in registry: {target}")

    probe = subprocess.run(["tmux", "has-session", "-t", full], check=False)
    if probe.returncode != 0:
        raise SystemExit(f"❌ tmux session not found: {full} (maybe stopped; try: twf resume {full})")

    if os.environ.get("TMUX"):
        return subprocess.run(["tmux", "switch-client", "-t", full], check=False).returncode
    return subprocess.run(["tmux", "attach-session", "-t", full], check=False).returncode

class _RouteHit:
    score: int
    role: str
    base: str
    full: str
    scope: str

def cmd_route(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    members = data.get("members", [])
    if not isinstance(members, list):
        members = []

    query = (args.query or "").strip().lower()
    if not query:
        raise SystemExit("❌ query is required")

    role_filter = _require_role(args.role) if args.role else None

    hits: list[_RouteHit] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip()
        if role_filter and role != role_filter:
            continue
        base = str(m.get("base", "")).strip()
        full = str(m.get("full", "")).strip()
        scope = str(m.get("scope", "")).strip()

        hay_base = base.lower()
        hay_scope = scope.lower()
        score = 0
        if query == hay_base:
            score += 100
        if query in hay_base:
            score += 30
        if query in hay_scope:
            score += 20
        if query == role:
            score += 10
        if score <= 0:
            continue
        hits.append(_RouteHit(score=score, role=role, base=base, full=full, scope=scope))

    hits.sort(key=lambda h: (h.score, h.role, h.base, h.full), reverse=True)
    if not hits:
        print("(no match)")
        return 1

    for h in hits[: (args.limit or 5)]:
        print(f"{h.full}\t{h.role}\t{h.base}\t{h.scope}")
    return 0

def _forward_stdin() -> str | None:
    if sys.stdin.isatty():
        return None
    return sys.stdin.read()

def cmd_ask(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    policy = _policy()

    actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
    actor_m = _resolve_member(data, actor_full)
    if not actor_m:
        raise SystemExit(f"❌ actor not found in registry: {actor_full} (run: atwf register-self ...)")
    actor_role = _member_role(actor_m)
    actor_base = _member_base(actor_m) or actor_full

    target = args.name.strip()
    if not target:
        raise SystemExit("❌ name is required")
    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ name not found in registry: {target} (use `atwf list` or `atwf up/spawn`)")

    target_m = _resolve_member(data, full) or {}
    to_role = _member_role(target_m)
    to_base = _member_base(target_m) or full

    _require_comm_allowed(policy, data, actor_full=actor_full, target_full=full)

    msg = args.message
    if msg is None:
        msg = _forward_stdin()
    if msg is None:
        raise SystemExit("❌ message missing (provide as arg or via stdin)")

    msg_id = _next_msg_id(team_dir)
    _write_inbox_message(
        team_dir,
        msg_id=msg_id,
        kind="ask",
        from_full=actor_full,
        from_base=actor_base,
        from_role=actor_role,
        to_full=full,
        to_base=to_base,
        to_role=to_role,
        body=msg,
    )
    notice = _inbox_notice(msg_id)
    wrapped = _wrap_team_message(
        team_dir,
        kind="ask",
        sender_full=actor_full,
        sender_role=actor_role or None,
        to_full=full,
        body=notice,
        msg_id=msg_id,
    )

    # Default: inbox-only delivery (no CLI injection). Recipients must poll
    # inbox while working. This avoids consuming their Codex context.
    notify = bool(getattr(args, "notify", False))
    wait = bool(getattr(args, "wait", False))
    if wait and not notify:
        raise SystemExit("❌ --wait requires --notify (CLI injection)")
    if not notify:
        print(msg_id)
        return 0

    twf = _resolve_twf()
    twf_subcmd = "ask" if wait else "send"
    res = _run_twf(twf, [twf_subcmd, full, wrapped])
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.returncode

def cmd_send(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    policy = _policy()

    # Hard constraint: require explicit intent via `atwf notice` / `atwf action`.
    # Members running inside their worker tmux sessions must not use legacy send.
    self_full = _tmux_self_full()
    if self_full and _resolve_member(data, self_full):
        raise SystemExit("❌ use `atwf notice <target>` or `atwf action <target>` (legacy `send` is disabled for team members)")

    actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
    actor_m = _resolve_member(data, actor_full)
    if not actor_m:
        raise SystemExit(f"❌ actor not found in registry: {actor_full} (run: atwf register-self ...)")
    actor_role = _member_role(actor_m)
    actor_base = _member_base(actor_m) or actor_full

    target = args.name.strip()
    if not target:
        raise SystemExit("❌ name is required")
    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ name not found in registry: {target} (use `atwf list` or `atwf up/spawn`)")

    target_m = _resolve_member(data, full) or {}
    to_role = _member_role(target_m)
    to_base = _member_base(target_m) or full

    _require_comm_allowed(policy, data, actor_full=actor_full, target_full=full)

    msg = args.message
    if msg is None:
        msg = _forward_stdin()
    if msg is None:
        raise SystemExit("❌ message missing (provide as arg or via stdin)")
    msg = msg.strip()
    if not msg:
        raise SystemExit("❌ empty message")

    msg_id = _next_msg_id(team_dir)
    _write_inbox_message(
        team_dir,
        msg_id=msg_id,
        kind="send",
        from_full=actor_full,
        from_base=actor_base,
        from_role=actor_role,
        to_full=full,
        to_base=to_base,
        to_role=to_role,
        body=msg,
    )
    notice = _inbox_notice(msg_id)
    wrapped = _wrap_team_message(
        team_dir,
        kind="send",
        sender_full=actor_full,
        sender_role=actor_role or None,
        to_full=full,
        body=notice,
        msg_id=msg_id,
    )

    # Default: inbox-only delivery (no CLI injection).
    if not bool(getattr(args, "notify", False)):
        print(msg_id)
        return 0

    twf = _resolve_twf()
    res = _run_twf(twf, ["send", full, wrapped])
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.returncode

def _resolve_intent_targets(
    *,
    data: dict[str, Any],
    policy: TeamPolicy,
    actor_full: str,
    targets: list[str] | None,
    role: str | None,
    subtree: str | None,
    include_excluded: bool,
) -> tuple[list[str], bool]:
    """
    Resolve targets and whether this is a broadcast-style delivery.

    - For --role/--subtree: broadcast-style (policy.broadcast applies)
    - For explicit targets:
      - 1 target => direct (comm policy applies)
      - 2+ targets => broadcast-style (policy.broadcast applies)
    """
    resolved: list[str] = []

    if role:
        resolved = _members_by_role(data, role)
        is_broadcast = True
    elif subtree:
        root = _resolve_target_full(data, subtree)
        if not root:
            raise SystemExit(f"❌ subtree root not found in registry: {subtree}")
        resolved = _subtree_fulls(data, root)
        is_broadcast = True
        if not include_excluded and policy.broadcast_exclude_roles:
            filtered: list[str] = []
            for full in resolved:
                m = _resolve_member(data, full)
                if _member_role(m) in policy.broadcast_exclude_roles:
                    continue
                filtered.append(full)
            resolved = filtered
    else:
        raw = targets or []
        if not raw:
            raise SystemExit("❌ targets are required (or use --role/--subtree)")
        for t in raw:
            full = _resolve_target_full(data, str(t))
            if not full:
                raise SystemExit(f"❌ target not found in registry: {t}")
            resolved.append(full)
        is_broadcast = len({t for t in resolved if t}) > 1

    # De-dupe + drop self for broadcast-style deliveries.
    uniq: list[str] = []
    seen: set[str] = set()
    for full in resolved:
        if not full:
            continue
        if is_broadcast and full == actor_full:
            continue
        if full not in seen:
            seen.add(full)
            uniq.append(full)
    return uniq, is_broadcast

def _cmd_intent_message(args: argparse.Namespace, *, kind: str) -> int:
    """
    Deliver an inbox-backed message with an explicit intent kind:
    - kind=notice: FYI; recipients must not reply/ACK upward (use receipts to confirm read)
    - kind=action: instruction; no immediate ACK required; report-up only when done
    """
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    policy = _policy()

    actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
    actor_m = _resolve_member(data, actor_full)
    if not actor_m:
        raise SystemExit(f"❌ actor not found in registry: {actor_full} (run: atwf register-self ...)")
    actor_role = _member_role(actor_m)
    actor_base = _member_base(actor_m) or actor_full

    msg = getattr(args, "message", None)
    if msg is None:
        msg = _forward_stdin()
    if msg is None:
        raise SystemExit("❌ message missing (use --message or pipe via stdin)")
    msg = str(msg).strip()
    if not msg:
        raise SystemExit("❌ empty message")

    targets, is_broadcast = _resolve_intent_targets(
        data=data,
        policy=policy,
        actor_full=actor_full,
        targets=getattr(args, "targets", None),
        role=getattr(args, "role", None),
        subtree=getattr(args, "subtree", None),
        include_excluded=bool(getattr(args, "include_excluded", False)),
    )
    if not targets:
        raise SystemExit("❌ no targets matched")

    if is_broadcast:
        if actor_role not in policy.broadcast_allowed_roles:
            raise SystemExit(
                "❌ broadcast not permitted by policy.\n"
                f"   actor: {actor_full} (role={actor_role or '?'})\n"
                f"   allowed_roles: {', '.join(sorted(policy.broadcast_allowed_roles)) or '(none)'}"
            )
    else:
        # Direct: enforce comm governance.
        _require_comm_allowed(policy, data, actor_full=actor_full, target_full=targets[0])

    msg_id = _next_msg_id(team_dir)
    inbox_notice = _inbox_notice(msg_id)

    lock = team_dir / ".lock"
    with _locked(lock):
        _ensure_share_layout(team_dir)
        max_unread = _inbox_max_unread_per_thread()
        for full in targets:
            m = _resolve_member(data, full) or {}
            to_role = _member_role(m) or "?"
            to_base = _member_base(m) or full
            _write_inbox_message_unlocked(
                team_dir,
                msg_id=msg_id,
                kind=kind,
                from_full=actor_full,
                from_base=actor_base,
                from_role=actor_role or "?",
                to_full=full,
                to_base=to_base,
                to_role=to_role,
                body=msg,
            )
            _inbox_enforce_unread_limit_unlocked(
                team_dir,
                to_base=to_base,
                from_base=actor_base,
                max_unread=max_unread,
            )

    # Default: inbox-only delivery. CLI injection is discouraged.
    if not bool(getattr(args, "notify", False)):
        print(msg_id)
        return 0

    twf = _resolve_twf()
    if len(targets) == 1:
        wrapped = _wrap_team_message(
            team_dir,
            kind=kind,
            sender_full=actor_full,
            sender_role=actor_role or None,
            to_full=targets[0],
            body=inbox_notice,
            msg_id=msg_id,
        )
        res = _run_twf(twf, ["send", targets[0], wrapped])
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        return res.returncode

    failures: list[str] = []
    max_workers = min(16, max(1, len(targets)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _run_twf,
                twf,
                [
                    "send",
                    full,
                    _wrap_team_message(
                        team_dir,
                        kind=kind,
                        sender_full=actor_full,
                        sender_role=actor_role or None,
                        to_full=full,
                        body=inbox_notice,
                        msg_id=msg_id,
                    ),
                ],
            ): full
            for full in targets
        }
        for fut in as_completed(futures):
            full = futures[fut]
            try:
                r = fut.result()
            except Exception:
                failures.append(full)
                continue
            if r.returncode != 0:
                failures.append(full)

    if failures:
        raise SystemExit(f"❌ notify failures: {len(failures)} targets")
    print(msg_id)
    return 0

def cmd_notice(args: argparse.Namespace) -> int:
    return _cmd_intent_message(args, kind="notice")

def cmd_action(args: argparse.Namespace) -> int:
    return _cmd_intent_message(args, kind="action")

def cmd_receipts(args: argparse.Namespace) -> int:
    """
    Query read receipts for a message id across recipients.

    Statuses:
    - unread: present under inbox/<to>/unread
    - overflow: present under inbox/<to>/overflow
    - read: present under inbox/<to>/read
    - missing: not present under inbox/<to> at all (not a recipient, or pruned)
    """
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    msg_id = str(getattr(args, "msg_id", "") or "").strip()
    if not msg_id:
        raise SystemExit("❌ msg_id is required")

    targets = _select_targets_for_team_op(
        data,
        targets=getattr(args, "targets", None),
        role=getattr(args, "role", None),
        subtree=getattr(args, "subtree", None),
    )
    if not targets:
        print("(no targets)")
        return 0

    rows: list[tuple[str, str, str, str]] = []
    for full in targets:
        m = _resolve_member(data, full) or {}
        base = _member_base(m) or full
        role = _member_role(m) or "?"
        hit = _find_inbox_message_file(team_dir, to_base=base, msg_id=msg_id)
        status = "missing"
        if hit:
            state, _from_base, _path = hit
            status = state if state in {_INBOX_UNREAD_DIR, _INBOX_OVERFLOW_DIR, _INBOX_READ_DIR} else "missing"
        rows.append((status, role, base, full))

    order = {_INBOX_UNREAD_DIR: 0, _INBOX_OVERFLOW_DIR: 1, _INBOX_READ_DIR: 2, "missing": 3}
    rows.sort(key=lambda r: (order.get(r[0], 99), r[1], r[2], r[3]))
    for status, role, base, full in rows:
        print("\t".join([status, role, base, full]).rstrip())
    return 0

def cmd_gather(args: argparse.Namespace) -> int:
    """
    Create a reply-needed request to multiple targets.

    The request body is delivered to each target inbox; targets must use `atwf respond`
    (or `atwf respond --blocked`) to record their reply.
    The initiator only receives a single consolidated result when all replies arrive
    (or when the request times out).
    """
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    policy = _policy()

    actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
    actor_m = _resolve_member(data, actor_full)
    if not actor_m:
        raise SystemExit(f"❌ actor not found in registry: {actor_full} (run: atwf register-self ...)")
    actor_role = _member_role(actor_m)
    actor_base = _member_base(actor_m) or actor_full

    targets_raw = [str(t).strip() for t in (getattr(args, "targets", None) or []) if str(t).strip()]
    if not targets_raw:
        raise SystemExit("❌ gather requires at least one target")

    msg = getattr(args, "message", None)
    if msg is None:
        msg = _forward_stdin()
    if msg is None:
        raise SystemExit("❌ message missing (provide as arg or via stdin)")
    msg = str(msg).rstrip()
    if not msg.strip():
        raise SystemExit("❌ empty message")

    topic = str(getattr(args, "topic", "") or "").strip()
    if not topic:
        # Use the first non-empty line as a default topic.
        topic = _inbox_summary(msg) or "reply-needed"

    deadline_s = _request_deadline_s()
    raw_deadline = str(getattr(args, "deadline", "") or "").strip()
    if raw_deadline:
        deadline_s = _parse_duration_seconds(raw_deadline, default_s=deadline_s)
    if deadline_s < 60:
        deadline_s = 60.0
    if deadline_s > 86400:
        deadline_s = 86400.0

    # Reserve IDs up front (avoid re-entrant locks).
    req_seq = _next_msg_id(team_dir)
    request_id = f"req-{req_seq}"

    resolved_targets: list[tuple[str, str, str]] = []
    seen_bases: set[str] = set()
    for raw in targets_raw:
        full = _resolve_target_full(data, raw)
        if not full:
            raise SystemExit(f"❌ target not found in registry: {raw} (use `atwf list`)")
        _require_comm_allowed(policy, data, actor_full=actor_full, target_full=full)
        m = _resolve_member(data, full) or {}
        base = _member_base(m) or full
        role = _member_role(m)
        if base == actor_base:
            continue
        if base in seen_bases:
            continue
        seen_bases.add(base)
        resolved_targets.append((full, base, role))

    if not resolved_targets:
        raise SystemExit("❌ gather has no valid targets after resolution/dedupe")

    notify_ids = [_next_msg_id(team_dir) for _full, _base, _role in resolved_targets]

    now_dt = datetime.now()
    created_at = now_dt.isoformat(timespec="seconds")
    deadline_at = (now_dt + timedelta(seconds=float(deadline_s))).isoformat(timespec="seconds")

    meta: dict[str, Any] = {
        "version": 1,
        "id": request_id,
        "created_at": created_at,
        "updated_at": created_at,
        "status": _REQUEST_STATUS_OPEN,
        "topic": topic,
        "message": msg,
        "deadline_s": float(deadline_s),
        "deadline_at": deadline_at,
        "from": {"full": actor_full, "base": actor_base, "role": actor_role},
        "targets": {},
        "finalized_at": "",
        "final_msg_id": "",
    }

    targets_meta: dict[str, Any] = {}
    for (full, base, role), notify_id in zip(resolved_targets, notify_ids, strict=True):
        targets_meta[base] = {
            "full": full,
            "base": base,
            "role": role,
            "status": _REQUEST_TARGET_STATUS_PENDING,
            "requested_at": created_at,
            "notify_msg_id": notify_id,
            "blocked_until": "",
            "blocked_reason": "",
            "waiting_on": "",
            "responded_at": "",
            "response_file": "",
        }
    meta["targets"] = targets_meta

    lock = team_dir / ".lock"
    with _locked(lock):
        _ensure_share_layout(team_dir)
        req_dir = _request_dir(team_dir, request_id=request_id)
        req_dir.mkdir(parents=True, exist_ok=True)
        _request_responses_dir(team_dir, request_id=request_id).mkdir(parents=True, exist_ok=True)

        _write_json_atomic(_request_meta_path(team_dir, request_id=request_id), meta)

        atwf_cmd = _atwf_cmd()
        for (full, base, role), notify_id in zip(resolved_targets, notify_ids, strict=True):
            body = (
                f"[REPLY-NEEDED] request_id={request_id}\n"
                f"- topic: {topic}\n"
                f"- from: {actor_base} (role={actor_role or '?'})\n"
                f"- created_at: {created_at}\n"
                f"- deadline_at: {deadline_at}\n"
                "\n"
                "Respond (required):\n"
                f"- {atwf_cmd} respond {request_id} \"<your reply>\"\n"
                "\n"
                "If blocked, snooze reminders (default 15m):\n"
                f"- {atwf_cmd} respond {request_id} --blocked --snooze 15m --waiting-on <base> \"why blocked\"\n"
                "\n"
                "View pending reply-needed:\n"
                f"- {atwf_cmd} reply-needed\n"
                "\n"
                "Message:\n"
                f"{msg.rstrip()}\n"
            )
            _write_inbox_message_unlocked(
                team_dir,
                msg_id=notify_id,
                kind="reply-needed",
                from_full=actor_full,
                from_base=actor_base,
                from_role=actor_role,
                to_full=full,
                to_base=base,
                to_role=role,
                body=body,
            )
            _inbox_enforce_unread_limit_unlocked(
                team_dir,
                to_base=base,
                from_base=actor_base,
                max_unread=_inbox_max_unread_per_thread(),
            )

    print(request_id)
    return 0

def cmd_respond(args: argparse.Namespace) -> int:
    """
    Record a reply-needed response for the current worker (or --as).

    - Normal reply: marks replied and writes a response file under requests/<id>/responses/.
    - --blocked: acknowledges the request but snoozes system reminders for a duration.
    """
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
    actor_m = _resolve_member(data, actor_full)
    if not actor_m:
        raise SystemExit(f"❌ actor not found in registry: {actor_full} (run: atwf register-self ...)")
    actor_role = _member_role(actor_m)
    actor_base = _member_base(actor_m) or actor_full

    request_id = _resolve_request_id(team_dir, str(getattr(args, "request_id", "") or ""))
    meta_path = _request_meta_path(team_dir, request_id=request_id)
    if not meta_path.is_file():
        raise SystemExit(f"❌ request not found: {request_id}")

    msg = getattr(args, "message", None)
    if msg is None:
        msg = _forward_stdin()
    msg = "" if msg is None else str(msg).rstrip()

    blocked = bool(getattr(args, "blocked", False))
    waiting_on = str(getattr(args, "waiting_on", "") or "").strip()
    raw_snooze = str(getattr(args, "snooze", "") or "").strip()
    snooze_s = _request_block_snooze_default_s()
    if raw_snooze:
        snooze_s = _parse_duration_seconds(raw_snooze, default_s=snooze_s)
    if snooze_s < 30:
        snooze_s = 30.0
    if snooze_s > 86400:
        snooze_s = 86400.0

    now_dt = datetime.now()
    now_iso = now_dt.isoformat(timespec="seconds")

    # Reserve a delivery msg id in case we need to finalize this request (avoid re-entrant locks).
    delivery_msg_id = _next_msg_id(team_dir)

    notify_msg_id = ""
    did_finalize = False
    blocked_until_out = ""

    lock = team_dir / ".lock"
    with _locked(lock):
        meta = _load_request_meta(team_dir, request_id=request_id)
        if str(meta.get("status", "")).strip() in {_REQUEST_STATUS_DONE, _REQUEST_STATUS_TIMED_OUT}:
            raise SystemExit(f"❌ request already finalized: {request_id} ({meta.get('status')})")

        targets = meta.get("targets")
        if not isinstance(targets, dict) or not targets:
            raise SystemExit(f"❌ request has no targets: {request_id}")

        key: str | None = None
        if actor_base in targets:
            key = actor_base
        else:
            for k, t in targets.items():
                if isinstance(t, dict) and str(t.get("full", "")).strip() == actor_full:
                    key = str(k)
                    break
        if not key or key not in targets or not isinstance(targets.get(key), dict):
            raise SystemExit(f"❌ you are not a target of request {request_id} (base={actor_base})")

        t = targets[key]
        notify_msg_id = str(t.get("notify_msg_id", "") or "").strip()

        if blocked:
            reason = msg.strip() or "(blocked)"
            blocked_until = (now_dt + timedelta(seconds=float(snooze_s))).isoformat(timespec="seconds")
            blocked_until_out = blocked_until
            t["status"] = _REQUEST_TARGET_STATUS_BLOCKED
            t["blocked_until"] = blocked_until
            t["blocked_reason"] = reason
            t["waiting_on"] = waiting_on
            t["responded_at"] = ""
            t["response_file"] = ""
        else:
            if not msg.strip():
                raise SystemExit("❌ reply body missing (provide as arg or via stdin)")
            resp_dir = _request_responses_dir(team_dir, request_id=request_id)
            resp_dir.mkdir(parents=True, exist_ok=True)
            resp_path = _request_response_path(team_dir, request_id=request_id, target_base=actor_base)
            payload = (
                f"# ATWF Reply-Needed Response\n\n"
                f"- request_id: `{request_id}`\n"
                f"- from: `{actor_full}` (base `{actor_base}` role `{actor_role or '?'}`)\n"
                f"- created_at: {now_iso}\n\n"
                "---\n\n"
                f"{msg.rstrip()}\n"
            )
            _write_text_atomic(resp_path, payload)

            rel = ""
            try:
                rel = str(resp_path.relative_to(team_dir))
            except Exception:
                rel = str(resp_path)

            t["status"] = _REQUEST_TARGET_STATUS_REPLIED
            t["responded_at"] = now_iso
            t["response_file"] = rel
            t["blocked_until"] = ""
            t["blocked_reason"] = ""
            t["waiting_on"] = ""

        meta["targets"] = targets
        meta["updated_at"] = now_iso

        # Finalize (single consolidated delivery) when complete or timed out.
        if not str(meta.get("final_msg_id", "") or "").strip():
            all_replied = _request_all_replied(meta)
            deadline_dt = _parse_iso_dt(str(meta.get("deadline_at", "") or ""))
            timed_out = (deadline_dt is not None and now_dt >= deadline_dt and not all_replied)
            if all_replied or timed_out:
                final_status = _REQUEST_STATUS_DONE if all_replied else _REQUEST_STATUS_TIMED_OUT

                from_info = meta.get("from") if isinstance(meta.get("from"), dict) else {}
                to_base = str(from_info.get("base", "") or "").strip() or str(from_info.get("full", "") or "").strip()
                to_full = str(from_info.get("full", "") or "").strip()
                to_role = str(from_info.get("role", "") or "").strip() or "?"
                # Prefer current registry for to_full/to_role if base exists.
                if to_base:
                    m = _resolve_member(data, to_base) or {}
                    to_full = str(m.get("full", "")).strip() or to_full or to_base
                    to_role = _member_role(m) or to_role

                if to_base:
                    body = _render_request_result(team_dir, meta, final_status=final_status)
                    _write_inbox_message_unlocked(
                        team_dir,
                        msg_id=delivery_msg_id,
                        kind="reply-needed-result",
                        from_full="atwf-reply",
                        from_base="atwf-reply",
                        from_role="system",
                        to_full=to_full or to_base,
                        to_base=to_base,
                        to_role=to_role,
                        body=body,
                    )
                    _inbox_enforce_unread_limit_unlocked(
                        team_dir,
                        to_base=to_base,
                        from_base="atwf-reply",
                        max_unread=_inbox_max_unread_per_thread(),
                    )
                    meta["status"] = final_status
                    meta["finalized_at"] = now_iso
                    meta["final_msg_id"] = delivery_msg_id
                    did_finalize = True

        _write_json_atomic(meta_path, meta)

    # Ack the original reply-needed notice (do this outside lock; it has its own lock).
    if notify_msg_id:
        _mark_inbox_read(team_dir, to_base=actor_base, msg_id=notify_msg_id)

    if blocked:
        print(f"{request_id}\tblocked\tuntil={blocked_until_out}".rstrip())
        return 0

    if did_finalize:
        print(f"{request_id}\treplied\tfinalized={delivery_msg_id}")
        return 0
    print(f"{request_id}\treplied")
    return 0

def cmd_reply_needed(args: argparse.Namespace) -> int:
    """
    List pending reply-needed requests for a target (self by default).
    """
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = str(getattr(args, "target", "") or "").strip()
    if target:
        full = _resolve_target_full(data, target)
        if not full:
            raise SystemExit(f"❌ target not found in registry: {target}")
        m = _resolve_member(data, full) or {}
        to_base = _member_base(m) or full
    else:
        self_full = _tmux_self_full()
        if not self_full:
            raise SystemExit("❌ reply-needed must run inside tmux (or use: reply-needed --target <full|base|role>)")
        m = _resolve_member(data, self_full)
        if not m:
            raise SystemExit(f"❌ current worker not found in registry: {self_full}")
        to_base = _member_base(m) or self_full

    now_dt = datetime.now()
    rows: list[tuple[str, str, str, str, str]] = []

    for req_id in _list_request_ids(team_dir):
        meta_path = _request_meta_path(team_dir, request_id=req_id)
        if not meta_path.is_file():
            continue
        meta = _read_json(meta_path)
        if not isinstance(meta, dict) or not meta:
            continue
        if str(meta.get("status", "")).strip() != _REQUEST_STATUS_OPEN:
            continue
        targets = meta.get("targets")
        if not isinstance(targets, dict):
            continue
        t = targets.get(to_base)
        if not isinstance(t, dict):
            continue
        st = str(t.get("status", "") or "").strip() or _REQUEST_TARGET_STATUS_PENDING
        if st == _REQUEST_TARGET_STATUS_REPLIED:
            continue
        blocked_until = str(t.get("blocked_until", "") or "").strip()
        blocked_dt = _parse_iso_dt(blocked_until)
        if blocked_dt is not None and now_dt < blocked_dt:
            st = f"{st}(snoozed)"
        topic = str(meta.get("topic", "") or "").strip()
        from_info = meta.get("from") if isinstance(meta.get("from"), dict) else {}
        from_base = str(from_info.get("base", "") or "").strip()
        deadline_at = str(meta.get("deadline_at", "") or "").strip()
        rows.append((req_id, st, topic, from_base, deadline_at))

    if not rows:
        print("(none)")
        return 0

    rows.sort(key=lambda r: r[0])
    for req_id, st, topic, from_base, deadline_at in rows:
        print("\t".join([req_id, st, topic, from_base, deadline_at]).rstrip())
    return 0

def cmd_request(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    request_id = _resolve_request_id(team_dir, str(getattr(args, "request_id", "") or ""))
    meta = _load_request_meta(team_dir, request_id=request_id)
    print(_render_request_result(team_dir, meta, final_status=str(meta.get("status", "") or _REQUEST_STATUS_OPEN)))
    return 0

def cmd_pend(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.name.strip()
    if not target:
        raise SystemExit("❌ name is required")
    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ name not found in registry: {target} (use `atwf list`)")
    extra = [str(args.n)] if args.n is not None else []
    res = _run_twf(twf, ["pend", full, *extra])
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.returncode

def cmd_ping(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.name.strip()
    if not target:
        raise SystemExit("❌ name is required")
    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ name not found in registry: {target} (use `atwf list`)")
    res = _run_twf(twf, ["ping", full])
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.returncode

def cmd_state(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = str(getattr(args, "target", "") or "").strip()
    members = data.get("members")
    if not isinstance(members, list):
        members = []

    def print_row(*cols: str) -> None:
        sys.stdout.write("\t".join(c or "" for c in cols) + "\n")

    if target:
        full = _resolve_target_full(data, target)
        if not full:
            raise SystemExit(f"❌ target not found in registry: {target}")
        m = _resolve_member(data, full) or {}
        base = _member_base(m) or full
        role = _member_role(m)
        path = _agent_state_path(team_dir, full=full)
        st = _read_json(path) if path.is_file() else {}
        status = _normalize_agent_status(str(st.get("status", ""))) if st else _STATE_STATUS_WORKING
        if status not in _STATE_STATUSES:
            status = _STATE_STATUS_WORKING
        updated_at = str(st.get("updated_at", "") or "")
        due_at = str(st.get("wakeup_due_at", "") or "")
        print_row(full, role, base, status, updated_at, due_at)
        return 0

    # Table for all members (stable ordering: role then updated_at then full)
    rows: list[tuple[str, str, str, str, str, str]] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        full = str(m.get("full", "")).strip()
        if not full:
            continue
        base = _member_base(m) or full
        role = _member_role(m)
        path = _agent_state_path(team_dir, full=full)
        st = _read_json(path) if path.is_file() else {}
        status = _normalize_agent_status(str(st.get("status", ""))) if st else _STATE_STATUS_WORKING
        if status not in _STATE_STATUSES:
            status = _STATE_STATUS_WORKING
        updated_at = str(st.get("updated_at", "") or "")
        due_at = str(st.get("wakeup_due_at", "") or "")
        rows.append((role, updated_at, full, base, status, due_at))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    print_row("full", "role", "base", "status", "updated_at", "wakeup_due_at")
    for role, updated_at, full, base, status, due_at in rows:
        print_row(full, role, base, status, updated_at, due_at)
    return 0

def cmd_state_self(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    self_full = _tmux_self_full()
    if not self_full:
        raise SystemExit("❌ state-self must run inside tmux")
    m = _resolve_member(data, self_full)
    if not m:
        raise SystemExit(f"❌ current worker not found in registry: {self_full}")
    base = _member_base(m) or self_full
    role = _member_role(m)

    st = _update_agent_state(team_dir, full=self_full, base=base, role=role, updater=lambda _d: None)
    status = str(st.get("status", ""))
    updated_at = str(st.get("updated_at", ""))
    due_at = str(st.get("wakeup_due_at", ""))
    print("\t".join([self_full, role, base, status, updated_at, due_at]).rstrip())
    return 0

def cmd_state_set_self(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    status_raw = str(args.status or "").strip()
    if not status_raw:
        raise SystemExit("❌ status is required")
    desired = _normalize_agent_status(status_raw)
    if desired not in _STATE_STATUSES:
        raise SystemExit(f"❌ invalid status: {status_raw} (allowed: working|draining|idle)")

    self_full = _tmux_self_full()
    if not self_full:
        raise SystemExit("❌ state-set-self must run inside tmux")
    m = _resolve_member(data, self_full)
    if not m:
        raise SystemExit(f"❌ current worker not found in registry: {self_full}")
    base = _member_base(m) or self_full
    role = _member_role(m)

    def set_status(state: dict[str, Any]) -> None:
        cur = _normalize_agent_status(str(state.get("status", ""))) or _STATE_STATUS_WORKING
        if cur not in _STATE_STATUSES:
            cur = _STATE_STATUS_WORKING
        now = _now()

        if desired == _STATE_STATUS_IDLE:
            if cur != _STATE_STATUS_DRAINING:
                raise SystemExit("❌ must set state to 'draining' before 'idle'")
            unread, overflow, ids = _inbox_unread_stats(team_dir, to_base=base)
            if unread or overflow:
                preview = ", ".join(ids[:10]) if ids else ""
                hint = f" ids: {preview}" if preview else ""
                raise SystemExit(
                    f"❌ inbox not empty (unread={unread} overflow={overflow}){hint} "
                    f"(run: {_atwf_cmd()} inbox)"
                )
            state["idle_since"] = now
            state["idle_inbox_empty_at"] = now
        elif desired == _STATE_STATUS_DRAINING:
            state["idle_since"] = ""
            state["idle_inbox_empty_at"] = ""
        elif desired == _STATE_STATUS_WORKING:
            state["idle_since"] = ""
            state["idle_inbox_empty_at"] = ""

        # Clear any pending wake scheduling when state changes.
        state["wakeup_scheduled_at"] = ""
        state["wakeup_due_at"] = ""
        state["wakeup_reason"] = ""
        if desired != _STATE_STATUS_WORKING:
            # keep historical wake_sent_at for debugging
            pass
        state["status"] = desired

    st = _update_agent_state(team_dir, full=self_full, base=base, role=role, updater=set_status)
    print(str(st.get("status", "")))
    return 0

def cmd_state_set(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = str(getattr(args, "target", "") or "").strip()
    if not target:
        raise SystemExit("❌ target is required")
    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ target not found in registry: {target}")
    m = _resolve_member(data, full) or {}
    base = _member_base(m) or full
    role = _member_role(m)

    status_raw = str(args.status or "").strip()
    if not status_raw:
        raise SystemExit("❌ status is required")
    desired = _normalize_agent_status(status_raw)
    if desired not in _STATE_STATUSES:
        raise SystemExit(f"❌ invalid status: {status_raw} (allowed: working|draining|idle)")

    force = bool(getattr(args, "force", False))
    if desired in {_STATE_STATUS_IDLE, _STATE_STATUS_DRAINING} and not force:
        raise SystemExit("❌ only the worker can set draining/idle (use --force for operator override)")

    st = _update_agent_state(team_dir, full=full, base=base, role=role, updater=lambda s: s.__setitem__("status", desired))
    print(str(st.get("status", "")))
    return 0

def cmd_drive(args: argparse.Namespace) -> int:
    """
    Drive loop mode (human-controlled):
    - running: watcher treats "idle + inbox-empty" as an abnormal stall and wakes the driver
      - if team.drive.unit_role is enabled (default: admin), this is evaluated per subtree rooted at that role
      - otherwise, this is evaluated for the whole team
    - standby: allow the whole team to be idle with empty inbox (no drive nudge)
    """
    team_dir = _default_team_dir()

    mode_raw = str(getattr(args, "mode", "") or "").strip()
    if not mode_raw:
        mode = _drive_mode_config_hot()
        lock = _state_lock_path(team_dir)
        with _locked(lock):
            _ensure_share_layout(team_dir)
            data = _load_drive_state_unlocked(team_dir, mode_default=mode)
        print("\t".join([mode, str(data.get("last_triggered_at", "")), str(data.get("last_msg_id", ""))]).rstrip())
        return 0

    mode = _normalize_drive_mode(mode_raw)
    if mode not in _DRIVE_MODES:
        raise SystemExit(f"❌ invalid drive mode: {mode_raw!r} (allowed: running|standby)")

    # Config is authoritative; prevent team members from switching mode in-worker.
    registry = _registry_path(team_dir)
    data = _load_registry(registry)
    self_full = _tmux_self_full()
    if self_full and _resolve_member(data, self_full):
        raise SystemExit(
            "❌ drive mode is user/operator-only.\n"
            f"   workers must NOT edit: {_config_file()}\n"
            "   workers must NOT change drive mode."
        )

    print(_set_drive_mode_config(mode))
    return 0

def cmd_watch_idle(args: argparse.Namespace) -> int:
    """
    Operator-side watcher:
    - polls inbox + derives working/idle from tmux pane activity (output hash)
    - if a member is `idle` and has pending inbox, schedule a wakeup
    - when due, inject a short wake message and record wakeup_sent_at (grace keeps it active)
    """
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    interval_s = float(getattr(args, "interval", None) or _state_watch_interval_s())
    delay_s = float(getattr(args, "delay", None) or _state_idle_wake_delay_s())
    activity_window_s = _state_activity_window_s()
    grace_s = _state_active_grace_period_s()
    capture_lines = _state_activity_capture_lines()
    auto_enter_enabled = _state_auto_enter_enabled()
    auto_enter_cooldown_s = _state_auto_enter_cooldown_s()
    auto_enter_tail_lines = _state_auto_enter_tail_window_lines()
    auto_enter_patterns = _state_auto_enter_patterns() if auto_enter_enabled else []
    message = str(getattr(args, "message", "") or "").strip() or _state_wake_message()
    reply_message = _state_reply_wake_message()
    stale_s = float(getattr(args, "working_stale", None) or _state_working_stale_threshold_s())
    cooldown_s = float(getattr(args, "alert_cooldown", None) or _state_working_alert_cooldown_s())
    once = bool(getattr(args, "once", False))
    dry_run = bool(getattr(args, "dry_run", False))

    def parse_iso(raw: str) -> datetime | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def iso(dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    def parse_dt(raw: str) -> datetime | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    while True:
        if _paused_marker_path(team_dir).is_file():
            if once:
                return 0
            time_sleep = max(1.0, interval_s)
            time.sleep(time_sleep)
            continue

        data = _load_registry(registry)
        members = data.get("members")
        if not isinstance(members, list):
            members = []

        now_dt = datetime.now()
        now_iso_tick = iso(now_dt)
        drive_mode = _drive_mode_config_hot()
        policy = _policy()
        coord_m = _resolve_latest_by_role(data, policy.root_role)
        coord_full = str(coord_m.get("full", "")).strip() if isinstance(coord_m, dict) else ""
        coord_base = _member_base(coord_m) if isinstance(coord_m, dict) else ""

        member_count = 0
        all_idle = True
        any_pending = False

        member_status: dict[str, str] = {}
        member_pending_by_full: dict[str, int] = {}
        member_tmux_running: dict[str, bool] = {}
        member_base_by_full: dict[str, str] = {}
        member_role_by_full: dict[str, str] = {}

        for m in members:
            if not isinstance(m, dict):
                continue
            full = str(m.get("full", "")).strip()
            if not full:
                continue
            base = _member_base(m) or full
            role = _member_role(m)

            # Read (or create) state lazily.
            path = _agent_state_path(team_dir, full=full)
            if path.is_file():
                st = _read_json(path)
            else:
                st = _write_agent_state(team_dir, full=full, base=base, role=role, update={})

            prev_status = _normalize_agent_status(str(st.get("status", ""))) or _STATE_STATUS_WORKING
            if prev_status not in _STATE_STATUSES:
                prev_status = _STATE_STATUS_WORKING

            unread, overflow, ids = _inbox_unread_stats(team_dir, to_base=base)
            pending = unread + overflow

            # Derive working/idle from tmux pane activity + grace after wake injection.
            now_iso = now_iso_tick
            last_output_change_dt = parse_dt(str(st.get("last_output_change_at", "") or ""))
            output_update: dict[str, Any] = {}
            auto_update: dict[str, Any] = {}
            tmux_running = _tmux_running(full)
            member_tmux_running[full] = tmux_running
            if tmux_running:
                tail = _tmux_capture_tail(full, lines=capture_lines)
                if tail is not None:
                    digest = _text_digest(tail)
                    prev_digest = str(st.get("last_output_hash", "") or "")
                    if digest != prev_digest or last_output_change_dt is None:
                        last_output_change_dt = now_dt
                    output_update = {
                        "last_output_hash": digest,
                        "last_output_capture_at": now_iso,
                        "last_output_change_at": iso(last_output_change_dt),
                    }
                    if auto_enter_enabled and auto_enter_patterns and not dry_run:
                        tail_lines = tail.splitlines()
                        window = "\n".join(tail_lines[-max(1, int(auto_enter_tail_lines)) :])
                        matched = ""
                        for pat in auto_enter_patterns:
                            if pat and pat in window:
                                matched = pat
                                break
                        if matched:
                            last_sent_dt = parse_dt(str(st.get("auto_enter_last_sent_at", "") or ""))
                            age_s = (now_dt - last_sent_dt).total_seconds() if last_sent_dt else None
                            if age_s is None or age_s >= max(0.0, auto_enter_cooldown_s):
                                if _tmux_send_enter(full):
                                    auto_update = {
                                        "auto_enter_last_sent_at": now_iso,
                                        "auto_enter_last_reason": matched,
                                        "auto_enter_count": int(st.get("auto_enter_count", 0) or 0) + 1,
                                    }
                                else:
                                    auto_update = {
                                        "auto_enter_last_sent_at": now_iso,
                                        "auto_enter_last_reason": matched,
                                    }
            wake_dt = parse_dt(str(st.get("wakeup_sent_at", "") or ""))
            active = False
            if last_output_change_dt is not None:
                active = (now_dt - last_output_change_dt).total_seconds() <= max(0.0, activity_window_s)
            if not active and wake_dt is not None and grace_s > 0:
                active = (now_dt - wake_dt).total_seconds() <= max(0.0, grace_s)

            status = _STATE_STATUS_WORKING if active else _STATE_STATUS_IDLE
            status_update: dict[str, Any] = {
                "status": status,
                "status_source": "watch",
                "last_inbox_unread": unread,
                "last_inbox_overflow": overflow,
            }
            if status == _STATE_STATUS_IDLE:
                if prev_status != _STATE_STATUS_IDLE:
                    status_update["idle_since"] = now_iso
                status_update["idle_inbox_empty_at"] = now_iso if pending == 0 else ""
            else:
                status_update["idle_since"] = ""
                status_update["idle_inbox_empty_at"] = ""
                status_update["wakeup_scheduled_at"] = ""
                status_update["wakeup_due_at"] = ""
                status_update["wakeup_reason"] = ""

            if not dry_run:
                st = _write_agent_state(team_dir, full=full, base=base, role=role, update={**output_update, **auto_update, **status_update})
            else:
                st = {**st, **output_update, **auto_update, **status_update}

            member_count += 1
            if pending > 0:
                any_pending = True
            if status != _STATE_STATUS_IDLE:
                all_idle = False

            member_status[full] = status
            member_pending_by_full[full] = int(pending)
            member_base_by_full[full] = base
            member_role_by_full[full] = role

            # Working stale inbox governance:
            # If the worker is working and has pending inbox messages older than N seconds,
            # write an inbox-only alert to coord (cooldown applies).
            if (
                coord_full
                and coord_base
                and status == _STATE_STATUS_WORKING
                and not dry_run
                and full != coord_full
            ):
                if pending > 0:
                    _min_n, min_id = _inbox_pending_min_id(team_dir, to_base=base)
                    created = _inbox_message_created_at(team_dir, to_base=base, msg_id=min_id) if min_id else None
                    if created is not None:
                        age_s = (now_dt - created).total_seconds()
                        last_check_dt = parse_dt(str(st.get("last_inbox_check_at", "") or ""))
                        check_age_s = (now_dt - last_check_dt).total_seconds() if last_check_dt else None

                        last_alert_dt = parse_dt(str(st.get("stale_alert_sent_at", "") or ""))
                        alert_age_s = (now_dt - last_alert_dt).total_seconds() if last_alert_dt else None
                        should_alert = age_s >= max(1.0, stale_s)
                        # If we just woke the worker, give them a grace period before alerting.
                        if should_alert and wake_dt is not None and grace_s > 0:
                            wake_age_s = (now_dt - wake_dt).total_seconds()
                            if wake_age_s < max(1.0, grace_s):
                                should_alert = False
                        if should_alert and check_age_s is not None:
                            should_alert = should_alert and check_age_s >= max(1.0, stale_s)
                        if should_alert and alert_age_s is not None:
                            should_alert = should_alert and alert_age_s >= max(1.0, cooldown_s)

                        if should_alert:
                            msg_id = _next_msg_id(team_dir)
                            body = (
                                "[ALERT] stale inbox while working\n"
                                f"- worker: {full} (role={role or '?'}, base={base})\n"
                                f"- status: working\n"
                                f"- pending: unread={unread} overflow={overflow}\n"
                                f"- oldest_id: {min_id} age_s={int(age_s)}\n"
                                f"- last_inbox_check_at: {str(st.get('last_inbox_check_at','') or '(never)')}\n"
                                "Suggested action:\n"
                                f"- Ask the worker to run: {_atwf_cmd()} inbox\n"
                                "- If they are stuck, re-scope or pause/unpause that worker.\n"
                            )
                            _write_inbox_message(
                                team_dir,
                                msg_id=msg_id,
                                kind="alert-stale-inbox",
                                from_full="atwf-watch",
                                from_base="atwf-watch",
                                from_role="system",
                                to_full=coord_full,
                                to_base=coord_base,
                                to_role=policy.root_role,
                                body=body,
                            )
                            # Also inject a short notice into coord's CLI so the coordinator
                            # sees governance alerts even if they aren't polling inbox.
                            short = (
                                "[ALERT] stale inbox while working\n"
                                f"worker={base} role={role or '?'} pending={unread}+{overflow} "
                                f"oldest={min_id} age_s={int(age_s)}\n"
                                f"inbox id={msg_id} (run: atwf inbox-open {msg_id} --target coord)\n"
                            )
                            wrapped = _wrap_team_message(
                                team_dir,
                                kind="alert-stale-inbox",
                                sender_full="atwf-watch",
                                sender_role="system",
                                to_full=coord_full,
                                body=short,
                                msg_id=msg_id,
                            )
                            _run_twf(twf, ["send", coord_full, wrapped])
                            _write_agent_state(
                                team_dir,
                                full=full,
                                base=base,
                                role=role,
                                update={
                                    "stale_alert_sent_at": _now(),
                                    "stale_alert_msg_id": msg_id,
                                    "stale_alert_reason": f"pending:{unread}+{overflow} oldest:{min_id} age_s:{int(age_s)}",
                                },
                            )

            # Clear stale wake scheduling when not idle.
            if status != _STATE_STATUS_IDLE:
                if st.get("wakeup_due_at") or st.get("wakeup_scheduled_at") or st.get("wakeup_reason"):
                    if not dry_run:
                        _write_agent_state(
                            team_dir,
                            full=full,
                            base=base,
                            role=role,
                            update={"wakeup_scheduled_at": "", "wakeup_due_at": "", "wakeup_reason": ""},
                        )
                continue

            unread, overflow, ids = _inbox_unread_stats(team_dir, to_base=base)
            pending = unread + overflow
            if pending == 0:
                if st.get("wakeup_due_at") or st.get("wakeup_scheduled_at") or st.get("wakeup_reason"):
                    if not dry_run:
                        _write_agent_state(
                            team_dir,
                            full=full,
                            base=base,
                            role=role,
                            update={"wakeup_scheduled_at": "", "wakeup_due_at": "", "wakeup_reason": ""},
                        )
                continue

            due_dt = parse_iso(str(st.get("wakeup_due_at", "")))
            if due_dt is None:
                due_dt = now_dt + timedelta(seconds=max(1.0, delay_s))
                if not dry_run:
                    _write_agent_state(
                        team_dir,
                        full=full,
                        base=base,
                        role=role,
                        update={
                            "wakeup_scheduled_at": iso(now_dt),
                            "wakeup_due_at": iso(due_dt),
                            "wakeup_reason": f"inbox_pending:{unread}+{overflow}",
                        },
                    )
                continue

            if now_dt < due_dt:
                continue

            # Due: re-check state + inbox before sending.
            st2 = _read_json(path) if path.is_file() else {}
            status2 = _normalize_agent_status(str(st2.get("status", ""))) or _STATE_STATUS_WORKING
            if status2 != _STATE_STATUS_IDLE:
                continue
            unread2, overflow2, _ids2 = _inbox_unread_stats(team_dir, to_base=base)
            pending2 = unread2 + overflow2
            if pending2 == 0:
                continue

            if not _tmux_running(full):
                # Keep due; we'll try again on next tick.
                continue

            if not dry_run:
                _write_agent_state(
                    team_dir,
                    full=full,
                    base=base,
                    role=role,
                    update={
                        "status": _STATE_STATUS_WORKING,
                        "wakeup_sent_at": _now(),
                        "wakeup_scheduled_at": "",
                        "wakeup_due_at": "",
                        "wakeup_reason": f"inbox_pending:{unread2}+{overflow2}",
                        "idle_since": "",
                        "idle_inbox_empty_at": "",
                        "last_inbox_unread": unread2,
                        "last_inbox_overflow": overflow2,
                    },
                )
                # Minimal wake: no body, just a reminder to read inbox.
                _run_twf(twf, ["send", full, message])

        # Auto-finalize reply-needed requests (single consolidated delivery).
        if not dry_run:
            finalizable, _has_pending_replies, _due_targets, _waiters = _scan_reply_requests(team_dir, now_dt=now_dt)
            for req_id, final_status in finalizable:
                msg_id = _next_msg_id(team_dir)
                if _finalize_request(
                    team_dir,
                    data,
                    request_id=req_id,
                    msg_id=msg_id,
                    final_status=final_status,
                    now_iso=now_iso_tick,
                ):
                    # We wrote a new inbox message; treat as pending to avoid drive on this tick.
                    any_pending = True

        # Reply-drive: if the whole team is idle and all inboxes are empty, but reply-needed
        # requests are pending, wake the best "debtor" instead of driving coord.
        suppress_drive = False
        if (
            member_count > 0
            and all_idle
            and not any_pending
            and not dry_run
            and drive_mode == _DRIVE_MODE_RUNNING
        ):
            _finalizable2, has_pending_replies, due_targets, waiters = _scan_reply_requests(team_dir, now_dt=now_dt)
            if has_pending_replies:
                # If nothing is due (everyone snoozed), suppress drive (standby-like behavior).
                if not due_targets:
                    suppress_drive = True
                else:
                    running: list[tuple[int, str, str, str]] = []
                    # (priority, request_id, base, full)
                    for req_id, base, role, st in due_targets:
                        m = _resolve_member(data, base) or {}
                        full = str(m.get("full", "")).strip()
                        if full and _tmux_running(full):
                            prio = int(waiters.get(base, 0) or 0)
                            running.append((prio, req_id, base, full))
                    # If at least one due target is runnable, suppress drive and let reply-drive handle it.
                    if running:
                        suppress_drive = True

                        cooldown_drive_s = _drive_cooldown_s()
                        lock2 = _state_lock_path(team_dir)
                        with _locked(lock2):
                            _ensure_share_layout(team_dir)
                            reply_state = _load_reply_drive_state_unlocked(team_dir)
                        last_reply_dt = parse_dt(str(reply_state.get("last_triggered_at", "") or ""))
                        allow = last_reply_dt is None or (now_dt - last_reply_dt).total_seconds() >= max(0.0, cooldown_drive_s)

                        if allow:
                            running.sort(key=lambda t: (-t[0], t[1], t[2]))
                            _prio, rid, base, full = running[0]
                            role = _member_role(_resolve_member(data, full) or {}) or "?"
                            if full and _tmux_running(full):
                                if not dry_run:
                                    _write_agent_state(
                                        team_dir,
                                        full=full,
                                        base=base,
                                        role=role,
                                        update={
                                            "status": _STATE_STATUS_WORKING,
                                            "wakeup_sent_at": now_iso_tick,
                                            "wakeup_reason": f"reply-needed:{rid}",
                                            "idle_since": "",
                                            "idle_inbox_empty_at": "",
                                        },
                                    )
                                _run_twf(twf, ["send", full, reply_message])
                                _write_reply_drive_state(
                                    team_dir,
                                    update={
                                        "last_triggered_at": now_iso_tick,
                                        "last_reason": "all_idle_inbox_empty_reply_pending",
                                        "last_request_id": rid,
                                        "last_target_base": base,
                                        "last_target_full": full,
                                    },
                                )
                    else:
                        # Due replies exist but no runnable tmux target; allow normal drive to intervene.
                        suppress_drive = False

        # Drive loop (anti-stall):
        # Prefer per-subtree drive (unit_role) when configured/enabled (default: admin),
        # otherwise fall back to the legacy whole-team drive.
        #
        # Important: only scan "active" subtrees (at least one tmux session is running
        # within the subtree). This prevents DONE/BLOCKED (parked) chains with no
        # running tmux from repeatedly triggering drive.
        if not dry_run and drive_mode == _DRIVE_MODE_RUNNING and not suppress_drive:
            cooldown_drive_s = _drive_cooldown_s()
            driver_role = _drive_driver_role()
            backup_role = _drive_backup_role()

            driver_m = _resolve_latest_by_role(data, driver_role)
            driver_full = str(driver_m.get("full", "")).strip() if isinstance(driver_m, dict) else ""
            driver_base = _member_base(driver_m) if isinstance(driver_m, dict) else ""
            driver_base = driver_base or driver_full

            target_full = driver_full
            target_role = driver_role
            target_base = driver_base
            if target_full and not _tmux_running(target_full):
                backup_m = _resolve_latest_by_role(data, backup_role)
                backup_full = str(backup_m.get("full", "")).strip() if isinstance(backup_m, dict) else ""
                backup_base = _member_base(backup_m) if isinstance(backup_m, dict) else ""
                if backup_full and _tmux_running(backup_full):
                    target_full = backup_full
                    target_role = backup_role
                    target_base = backup_base or backup_full

            unit_role = _drive_unit_role()
            if unit_role and target_full:
                roots = _members_by_role(data, unit_role)
                if roots:
                    lock = _state_lock_path(team_dir)
                    with _locked(lock):
                        _ensure_share_layout(team_dir)
                        subtree_state = _load_drive_subtree_state_unlocked(team_dir, mode_default=drive_mode)
                    subs = subtree_state.get("subtrees") if isinstance(subtree_state, dict) else None
                    subs = subs if isinstance(subs, dict) else {}

                    stalled: list[dict[str, Any]] = []
                    for root_full in roots:
                        root_m = _resolve_member(data, root_full) or {}
                        root_base = _member_base(root_m) or root_full
                        entry = subs.get(root_base) if isinstance(subs, dict) else {}
                        status = str(entry.get("status", "") or "").strip().lower() if isinstance(entry, dict) else ""
                        if status == _DRIVE_SUBTREE_STATUS_STOPPED:
                            continue

                        subtree_fulls = _subtree_fulls(data, root_full)
                        if not subtree_fulls:
                            continue

                        sub_all_idle = True
                        sub_any_pending = False
                        missing_tmux: list[str] = []
                        running_n = 0
                        for f in subtree_fulls:
                            st = member_status.get(f, "")
                            if st and st != _STATE_STATUS_IDLE:
                                sub_all_idle = False
                            if int(member_pending_by_full.get(f, 0) or 0) > 0:
                                sub_any_pending = True
                            if bool(member_tmux_running.get(f, False)):
                                running_n += 1
                            else:
                                missing_tmux.append(f)
                        if running_n <= 0:
                            continue
                        if not sub_all_idle or sub_any_pending:
                            continue

                        last_drive_dt = parse_dt(str(entry.get("last_triggered_at", "") or "")) if isinstance(entry, dict) else None
                        if last_drive_dt is not None and (now_dt - last_drive_dt).total_seconds() < max(0.0, cooldown_drive_s):
                            continue

                        stalled.append(
                            {
                                "root_full": root_full,
                                "root_base": root_base,
                                "members": subtree_fulls,
                                "tmux_running": running_n,
                                "missing_tmux": missing_tmux,
                            }
                        )

                    if stalled:
                        msg_id = _next_msg_id(team_dir)
                        bases = [str(s.get("root_base") or "").strip() for s in stalled if str(s.get("root_base") or "").strip()]
                        bases_short = ", ".join(bases[:5]) + (", ..." if len(bases) > 5 else "")

                        def fmt_missing(fulls: list[str]) -> str:
                            parts: list[str] = []
                            for ff in fulls[:6]:
                                b = member_base_by_full.get(ff, "") or ff
                                r = member_role_by_full.get(ff, "") or "?"
                                parts.append(f"{b}({r})")
                            if len(fulls) > 6:
                                parts.append("...")
                            return ", ".join(parts)

                        lines: list[str] = []
                        for s in stalled:
                            base = str(s.get("root_base") or "").strip()
                            full = str(s.get("root_full") or "").strip()
                            members_list = s.get("members") if isinstance(s.get("members"), list) else []
                            running_n = int(s.get("tmux_running") or 0)
                            missing_list = s.get("missing_tmux") if isinstance(s.get("missing_tmux"), list) else []
                            members_n = len(members_list)
                            miss_n = len(missing_list)
                            tail = f" missing=[{fmt_missing(missing_list)}]" if miss_n else ""
                            lines.append(f"- {base}: root={full} members={members_n} tmux_running={running_n} tmux_missing={miss_n}{tail}")
                        subtree_lines = "\n".join(lines).rstrip() + "\n"

                        extra = {
                            "count": str(len(stalled)),
                            "unit_role": unit_role,
                            "subtree_bases": bases_short,
                            "subtree_lines": subtree_lines.rstrip(),
                        }
                        body = _drive_message_body(iso_ts=now_iso_tick, msg_id=msg_id, extra=extra)
                        _write_inbox_message(
                            team_dir,
                            msg_id=msg_id,
                            kind="drive",
                            from_full="atwf-drive",
                            from_base="atwf-drive",
                            from_role="system",
                            to_full=target_full,
                            to_base=target_base,
                            to_role=target_role,
                            body=body,
                        )
                        short = _drive_message_summary(iso_ts=now_iso_tick, msg_id=msg_id, extra=extra)
                        wrapped = _wrap_team_message(
                            team_dir,
                            kind="drive",
                            sender_full="atwf-drive",
                            sender_role="system",
                            to_full=target_full,
                            body=short,
                            msg_id=msg_id,
                        )
                        _run_twf(twf, ["send", target_full, wrapped])

                        updates: dict[str, dict[str, Any]] = {}
                        for s in stalled:
                            base = str(s.get("root_base") or "").strip()
                            if not base:
                                continue
                            updates[base] = {
                                "last_triggered_at": now_iso_tick,
                                "last_msg_id": msg_id,
                                "last_reason": "subtree_all_idle_inbox_empty",
                            }
                        if updates:
                            _write_drive_subtree_state(team_dir, updates=updates)

            # Legacy whole-team drive: only when no unit_role drive is active.
            if (
                (not unit_role)
                and target_full
                and member_count > 0
                and all_idle
                and not any_pending
            ):
                lock = _state_lock_path(team_dir)
                with _locked(lock):
                    _ensure_share_layout(team_dir)
                    drive_state = _load_drive_state_unlocked(team_dir, mode_default=drive_mode)
                last_drive_dt = parse_dt(str(drive_state.get("last_triggered_at", "") or ""))
                if last_drive_dt is None or (now_dt - last_drive_dt).total_seconds() >= max(0.0, cooldown_drive_s):
                    msg_id = _next_msg_id(team_dir)
                    body = _drive_message_body(iso_ts=now_iso_tick, msg_id=msg_id)
                    _write_inbox_message(
                        team_dir,
                        msg_id=msg_id,
                        kind="drive",
                        from_full="atwf-drive",
                        from_base="atwf-drive",
                        from_role="system",
                        to_full=target_full,
                        to_base=target_base,
                        to_role=target_role,
                        body=body,
                    )
                    short = _drive_message_summary(iso_ts=now_iso_tick, msg_id=msg_id)
                    wrapped = _wrap_team_message(
                        team_dir,
                        kind="drive",
                        sender_full="atwf-drive",
                        sender_role="system",
                        to_full=target_full,
                        body=short,
                        msg_id=msg_id,
                    )
                    _run_twf(twf, ["send", target_full, wrapped])
                    _write_drive_state(
                        team_dir,
                        update={
                            "last_triggered_at": now_iso_tick,
                            "last_msg_id": msg_id,
                            "last_reason": "all_idle_inbox_empty",
                            "last_driver_full": target_full,
                        },
                    )

        if once:
            return 0
        time_sleep = max(1.0, interval_s)
        time.sleep(time_sleep)

def cmd_inbox(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = str(getattr(args, "target", "") or "").strip()
    is_self = False
    if target:
        full = _resolve_target_full(data, target)
        if not full:
            raise SystemExit(f"❌ target not found in registry: {target}")
        m = _resolve_member(data, full) or {}
        to_base = _member_base(m) or full
    else:
        self_full = _tmux_self_full()
        if not self_full:
            raise SystemExit("❌ inbox must run inside tmux (or use: inbox --target <full|base|role>)")
        m = _resolve_member(data, self_full)
        if not m:
            raise SystemExit(f"❌ current worker not found in registry: {self_full}")
        to_base = _member_base(m) or self_full
        is_self = True

    base_dir = _inbox_member_dir(team_dir, base=to_base)
    unread_root = base_dir / _INBOX_UNREAD_DIR
    overflow_root = base_dir / _INBOX_OVERFLOW_DIR

    rows: list[tuple[int, str, str, str, str, str]] = []

    def parse_meta(path: Path) -> tuple[str, str]:
        kind = ""
        summary = ""
        try:
            head = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:40]
        except Exception:
            return kind, summary
        for line in head:
            s = line.strip()
            if s.startswith("- kind:"):
                kind = s.split(":", 1)[1].strip().strip("`")
            elif s.startswith("- summary:"):
                summary = s.split(":", 1)[1].strip()
        return kind, summary

    if unread_root.is_dir():
        for from_dir in sorted([p for p in unread_root.glob("from-*") if p.is_dir()]):
            from_base = from_dir.name[len("from-") :]
            for n, stem, p in _inbox_list_msgs(from_dir):
                kind, summary = parse_meta(p)
                rows.append((n, stem, from_base, kind, summary, _INBOX_UNREAD_DIR))

    if overflow_root.is_dir():
        for from_dir in sorted([p for p in overflow_root.glob("from-*") if p.is_dir()]):
            from_base = from_dir.name[len("from-") :]
            for n, stem, p in _inbox_list_msgs(from_dir):
                kind, summary = parse_meta(p)
                rows.append((n, stem, from_base, kind, summary, _INBOX_OVERFLOW_DIR))

    rows.sort(key=lambda r: r[0])
    if not rows:
        print("(empty)")
        return 0

    for _n, msg_id, from_base, kind, summary, state in rows:
        parts = [msg_id, from_base]
        parts.append(kind or "?")
        if summary:
            parts.append(summary)
        if state != _INBOX_UNREAD_DIR:
            parts.append(state)
        print("\t".join(parts))

    if is_self:
        self_full = _tmux_self_full() or ""
        if self_full and isinstance(m, dict):
            base = _member_base(m) or self_full
            role = _member_role(m)
            unread, overflow, _ids = _inbox_unread_stats(team_dir, to_base=base)
            _update_agent_state(
                team_dir,
                full=self_full,
                base=base,
                role=role,
                updater=lambda s: (
                    s.__setitem__("last_inbox_check_at", _now()),
                    s.__setitem__("last_inbox_unread", unread),
                    s.__setitem__("last_inbox_overflow", overflow),
                ),
            )
    return 0

def cmd_inbox_open(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    msg_id = str(args.msg_id or "").strip()
    if not msg_id:
        raise SystemExit("❌ msg_id is required")

    target = str(getattr(args, "target", "") or "").strip()
    if target:
        full = _resolve_target_full(data, target)
        if not full:
            raise SystemExit(f"❌ target not found in registry: {target}")
        m = _resolve_member(data, full) or {}
        to_base = _member_base(m) or full
    else:
        self_full = _tmux_self_full()
        if not self_full:
            raise SystemExit("❌ inbox-open must run inside tmux (or use: inbox-open --target <full|base|role> <id>)")
        m = _resolve_member(data, self_full)
        if not m:
            raise SystemExit(f"❌ current worker not found in registry: {self_full}")
        to_base = _member_base(m) or self_full

    hit = _find_inbox_message_file(team_dir, to_base=to_base, msg_id=msg_id)
    if not hit:
        raise SystemExit(f"❌ message not found in inbox: {msg_id}")
    _state, _from_base, path = hit
    content = path.read_text(encoding="utf-8", errors="ignore")
    sys.stdout.write(content)
    if content and not content.endswith("\n"):
        sys.stdout.write("\n")

    # Update "last_inbox_check_at" for self only.
    if not target:
        self_full = _tmux_self_full() or ""
        if self_full and isinstance(m, dict):
            base = _member_base(m) or self_full
            role = _member_role(m)
            unread, overflow, _ids = _inbox_unread_stats(team_dir, to_base=base)
            _update_agent_state(
                team_dir,
                full=self_full,
                base=base,
                role=role,
                updater=lambda s: (
                    s.__setitem__("last_inbox_check_at", _now()),
                    s.__setitem__("last_inbox_unread", unread),
                    s.__setitem__("last_inbox_overflow", overflow),
                ),
            )
    return 0

def cmd_inbox_ack(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    msg_id = str(args.msg_id or "").strip()
    if not msg_id:
        raise SystemExit("❌ msg_id is required")

    self_full = _tmux_self_full()
    if not self_full:
        raise SystemExit("❌ inbox-ack must run inside tmux")
    m = _resolve_member(data, self_full)
    if not m:
        raise SystemExit(f"❌ current worker not found in registry: {self_full}")
    to_base = _member_base(m) or self_full

    moved = _mark_inbox_read(team_dir, to_base=to_base, msg_id=msg_id)
    if not moved:
        raise SystemExit(f"❌ message not found: {msg_id}")
    print("OK")

    unread, overflow, _ids = _inbox_unread_stats(team_dir, to_base=to_base)
    _update_agent_state(
        team_dir,
        full=self_full,
        base=to_base,
        role=_member_role(m),
        updater=lambda s: (
            s.__setitem__("last_inbox_check_at", _now()),
            s.__setitem__("last_inbox_unread", unread),
            s.__setitem__("last_inbox_overflow", overflow),
        ),
    )
    return 0

def cmd_inbox_pending(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    actor_full = _resolve_actor_full(data, as_target=getattr(args, "as_target", None))
    actor_m = _resolve_member(data, actor_full)
    if not actor_m:
        raise SystemExit(f"❌ actor not found in registry: {actor_full}")
    from_base = _member_base(actor_m) or actor_full

    target = str(args.target or "").strip()
    if not target:
        raise SystemExit("❌ target is required")
    target_full = _resolve_target_full(data, target)
    if not target_full:
        raise SystemExit(f"❌ target not found in registry: {target}")
    target_m = _resolve_member(data, target_full) or {}
    to_base = _member_base(target_m) or target_full

    unread_dir = _inbox_thread_dir(team_dir, to_base=to_base, from_base=from_base, state=_INBOX_UNREAD_DIR)
    overflow_dir = _inbox_thread_dir(team_dir, to_base=to_base, from_base=from_base, state=_INBOX_OVERFLOW_DIR)

    unread = len(_inbox_list_msgs(unread_dir))
    overflow = len(_inbox_list_msgs(overflow_dir))
    print(f"unread={unread} overflow={overflow}")
    return 0

def cmd_bootstrap(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    role = _require_role(args.role)

    target = args.name.strip()
    if not target:
        raise SystemExit("❌ name is required")

    data = _load_registry(registry)
    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ name not found in registry: {target} (use `atwf list`)")
    m = _resolve_member(data, full)
    base = str(m.get("base") or "").strip() if m else ""
    base = base or full

    _bootstrap_worker(twf, name=full, role=role, full=full, base=base, registry=registry, team_dir=team_dir)
    _eprint(f"✅ bootstrapped: {full} as {role}")
    return 0

def cmd_remove(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    pm_full = _require_full_name(args.pm_full)

    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)
        pm = _resolve_member(data, pm_full)
        if not pm:
            raise SystemExit(f"❌ pm not found in registry: {pm_full}")
        role = str(pm.get("role", "")).strip()
        if role != "pm":
            raise SystemExit(f"❌ remove only supports PM. Provided worker role={role!r} full={pm_full}")

        members = data.get("members", [])
        if not isinstance(members, list) or not members:
            _eprint("ℹ️ registry has no members; nothing to remove")
            return 0

        to_remove: list[str] = []
        for m in members:
            if not isinstance(m, dict):
                continue
            full = str(m.get("full", "")).strip()
            if not full or not FULL_NAME_RE.match(full):
                continue
            to_remove.append(full)

    # Remove everything recorded in the registry (team disband), with PM last.
    uniq: list[str] = []
    seen: set[str] = set()
    for full in to_remove:
        if full not in seen:
            seen.add(full)
            uniq.append(full)

    uniq_no_pm = [n for n in uniq if n != pm_full]
    ordered = uniq_no_pm + [pm_full] if pm_full in seen else uniq_no_pm

    if args.dry_run:
        print("\n".join(ordered))
        return 0

    failed: list[str] = []
    for full in ordered:
        res = _run_twf(twf, ["remove", full, "--no-recursive"])
        if res.returncode != 0:
            failed.append(full)
            err = (res.stderr or "").strip()
            _eprint(f"⚠️ twf remove failed for {full}: {err or res.stdout.strip()}")

    with _locked(lock):
        data = _load_registry(registry)
        data["members"] = []
        data["updated_at"] = _now()
        _write_json_atomic(registry, data)

    if failed:
        _eprint(f"❌ team disband completed with failures: {len(failed)} workers (see stderr)")
        return 1
    _eprint("✅ team disbanded (registry cleared)")
    return 0

def build_parser() -> argparse.ArgumentParser:
    enabled_roles = sorted(_policy().enabled_roles)
    p = argparse.ArgumentParser(prog="atwf", add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="init registry and start initial team (root_role + configured children)")
    init.add_argument("task", nargs="?", help="task description (saved to share/task.md); or pipe via stdin")
    init.add_argument("--task-file", help="task file path to copy into share/task.md")
    init.add_argument("--registry-only", action="store_true", help="only create registry, do not start workers")
    init.add_argument("--force-new", action="store_true", help="always start a fresh initial team (even if one exists)")
    init.add_argument("--root-only", action="store_true", help="start only root_role (skip init children)")
    init.add_argument("--task-to", default="", help="role to notify with initial task (overrides config team.init.task_to_role)")
    init.add_argument("--no-task-notify", action="store_true", help="do not notify anyone with the initial task")
    init.add_argument("--no-bootstrap", action="store_true", help="skip sending role templates on creation")

    reset = sub.add_parser("reset", help="reset local environment (delete worker state + share; preserve account pool by default)")
    reset.add_argument("--dry-run", action="store_true", help="print what would be deleted, without deleting")
    reset.add_argument("--force", action="store_true", help="also delete codex_home paths outside ~/.codex-workers (dangerous)")
    reset.add_argument("--wipe-account-pool", action="store_true", help="also delete local account pool state.json (resets auth ordering/pointer)")

    up = sub.add_parser("up", help="start a new worker (root_role only; twf up) + register + bootstrap")
    up.add_argument("role")
    up.add_argument("label", nargs="?")
    up.add_argument("--scope", default="")
    up.add_argument("--provider", choices=("codex", "claude"), default="codex", help="worker provider (default: codex)")
    up.add_argument("--work-dir", default="", help="start worker in this directory (passed to twf/codex_up_tmux.sh)")
    up.add_argument("--no-bootstrap", action="store_true")

    sp = sub.add_parser("spawn", help="spawn a child worker (twf spawn) + register + bootstrap")
    sp.add_argument("parent_full")
    sp.add_argument("role")
    sp.add_argument("label", nargs="?")
    sp.add_argument("--scope", default="")
    sp.add_argument("--provider", choices=("codex", "claude"), default="", help="worker provider (default: inherit from parent)")
    sp.add_argument("--work-dir", default="", help="start worker in this directory (passed to twf/codex_up_tmux.sh)")
    sp.add_argument("--no-bootstrap", action="store_true")

    sps = sub.add_parser("spawn-self", help="spawn a child worker from the current tmux session")
    sps.add_argument("role")
    sps.add_argument("label", nargs="?")
    sps.add_argument("--scope", default="")
    sps.add_argument("--provider", choices=("codex", "claude"), default="", help="worker provider (default: inherit from parent)")
    sps.add_argument("--work-dir", default="", help="start worker in this directory (passed to twf/codex_up_tmux.sh)")
    sps.add_argument("--no-bootstrap", action="store_true")

    parent = sub.add_parser("parent", help="print a member's parent (lookup by full or base)")
    parent.add_argument("name")

    sub.add_parser("parent-self", help="print current worker's parent (inside tmux)")

    children = sub.add_parser("children", help="print a member's children (lookup by full or base)")
    children.add_argument("name")

    sub.add_parser("children-self", help="print current worker's children (inside tmux)")

    rup = sub.add_parser("report-up", help="send a completion/progress report to your parent (inside tmux)")
    rup.add_argument("message", nargs="?")
    rup.add_argument("--wait", action="store_true", help="wait for a reply (uses twf ask)")

    rto = sub.add_parser("report-to", help="send a report to a target member or role (inside tmux)")
    rto.add_argument("target", help="full|base|role (see `atwf policy` for enabled roles)")
    rto.add_argument("message", nargs="?")
    rto.add_argument("--wait", action="store_true", help="wait for a reply (uses twf ask)")

    sub.add_parser("self", help="print current tmux session name")

    reg = sub.add_parser("register", help="upsert a member into registry.json")
    reg.add_argument("full")
    reg.add_argument("--role", choices=enabled_roles)
    reg.add_argument("--base")
    reg.add_argument("--scope")
    reg.add_argument("--parent")
    reg.add_argument("--state-file")
    reg.add_argument("--force", action="store_true", help="bypass root/parent policy checks (registry repair)")

    regself = sub.add_parser("register-self", help="register current tmux session into registry.json")
    regself.add_argument("--role", required=True, choices=enabled_roles)
    regself.add_argument("--base")
    regself.add_argument("--scope")
    regself.add_argument("--parent")
    regself.add_argument("--state-file")
    regself.add_argument("--force", action="store_true", help="bypass root/parent policy checks (registry repair)")

    ss = sub.add_parser("set-scope", help="update scope for a member (lookup by full or base)")
    ss.add_argument("name")
    ss.add_argument("scope")

    sss = sub.add_parser("set-scope-self", help="update scope for current tmux session")
    sss.add_argument("scope")

    sub.add_parser("list", help="print registry table")

    sub.add_parser("where", help="print resolved shared dirs (team_dir + registry)")

    sub.add_parser("templates-check", help="validate templates/config portability ({{ATWF_CMD}}/{{ATWF_CONFIG}})")

    sub.add_parser("policy", help="print resolved team policy (hard constraints)")

    sub.add_parser("perms-self", help="print current worker permissions (inside tmux)")

    tree = sub.add_parser("tree", help="print org tree from registry (parent/children)")
    tree.add_argument("root", nargs="?", help="optional root: full|base|role")

    dpath = sub.add_parser("design-path", help="print the per-member design doc path under share/design/")
    dpath.add_argument("target", help="full|base|role")

    dinit = sub.add_parser("design-init", help="create a design doc stub under share/design/ (non-destructive by default)")
    dinit.add_argument("target", help="full|base|role")
    dinit.add_argument("--force", action="store_true", help="overwrite if exists")

    dself = sub.add_parser("design-init-self", help="create a design doc stub for the current tmux worker")
    dself.add_argument("--force", action="store_true", help="overwrite if exists")

    wtp = sub.add_parser("worktree-path", help="print the dedicated git worktree path for a worker")
    wtp.add_argument("target", help="full|base|role")
    wtp.add_argument("--repo", default="", help="git repo/worktree path (optional; enables multi-repo mode)")
    wtp.add_argument("--dest-root", default="", help="destination root directory (optional; multi-repo mode)")
    wtp.add_argument("--name", default="", help="destination subdir name under dest-root (default: repo basename)")

    wtc = sub.add_parser(
        "worktree-create",
        help="create a dedicated git worktree (default: <git-root>/worktree/<full>; multi-repo mode: <dest-root>/<name>)",
    )
    wtc.add_argument("target", help="full|base|role")
    wtc.add_argument("--base", default="HEAD", help="base ref/branch/commit (default: HEAD)")
    wtc.add_argument("--branch", default="", help="branch name to create for the worktree (default: <full>)")
    wtc.add_argument("--repo", default="", help="git repo/worktree path (optional; enables multi-repo mode)")
    wtc.add_argument("--dest-root", default="", help="destination root directory (optional; multi-repo mode)")
    wtc.add_argument("--name", default="", help="destination subdir name under dest-root (default: repo basename)")

    wtcs = sub.add_parser("worktree-create-self", help="create a dedicated git worktree for the current tmux worker")
    wtcs.add_argument("--base", default="HEAD", help="base ref/branch/commit (default: HEAD)")
    wtcs.add_argument("--branch", default="", help="branch name to create for the worktree (default: <full>)")
    wtcs.add_argument("--repo", default="", help="git repo/worktree path (optional; enables multi-repo mode)")
    wtcs.add_argument("--dest-root", default="", help="destination root directory (optional; multi-repo mode)")
    wtcs.add_argument("--name", default="", help="destination subdir name under dest-root (default: repo basename)")

    sub.add_parser("worktree-check-self", help="ensure you are working inside your dedicated worktree (inside tmux)")

    stop = sub.add_parser("stop", help="stop Codex tmux workers (default: whole team)")
    stop.add_argument("targets", nargs="*", help="optional targets (full|base|role)")
    stop.add_argument("--role", choices=enabled_roles, help="stop all members of a role")
    stop.add_argument("--subtree", help="stop all members under a root (full|base|role)")
    stop.add_argument("--dry-run", action="store_true", help="print what would be stopped")

    resume = sub.add_parser("resume", help="resume Codex tmux workers (default: whole team)")
    resume.add_argument("targets", nargs="*", help="optional targets (full|base|role)")
    resume.add_argument("--role", choices=enabled_roles, help="resume all members of a role")
    resume.add_argument("--subtree", help="resume all members under a root (full|base|role)")
    resume.add_argument("--dry-run", action="store_true", help="print what would be resumed")

    rmst = sub.add_parser("remove-subtree", help="remove a subtree (stop + delete workers + prune registry)")
    rmst.add_argument("root", help="subtree root (full|base). Recommended: admin-<REQ-ID>")
    rmst.add_argument("--dry-run", action="store_true", help="print what would be removed (full names)")
    rmst.add_argument("--purge-inbox", action="store_true", help="also delete inbox dirs for removed bases (irreversible)")
    rmst.add_argument("--force", action="store_true", help="allow removing subtrees not rooted at the configured unit_role")

    pause = sub.add_parser("pause", help="pause workers and disable watcher actions (recommended for humans)")
    pause.add_argument("targets", nargs="*", help="optional targets (full|base|role)")
    pause.add_argument("--role", choices=enabled_roles, help="pause all members of a role")
    pause.add_argument("--subtree", help="pause all members under a root (full|base|role)")
    pause.add_argument("--dry-run", action="store_true", help="print what would be paused (still writes marker)")
    pause.add_argument("--reason", default="", help="optional pause reason (if omitted, read stdin)")

    unpause = sub.add_parser("unpause", help="unpause workers (resume) without restarting watcher")
    unpause.add_argument("targets", nargs="*", help="optional targets (full|base|role)")
    unpause.add_argument("--role", choices=enabled_roles, help="unpause all members of a role")
    unpause.add_argument("--subtree", help="unpause all members under a root (full|base|role)")
    unpause.add_argument("--dry-run", action="store_true", help="print what would be resumed")

    bc = sub.add_parser("broadcast", help="send the same message to multiple workers (sequential)")
    bc.add_argument("targets", nargs="*", help="targets (full|base|role). Ignored when --role/--subtree is used.")
    bc.add_argument("--role", choices=enabled_roles, help="broadcast to all members of a role")
    bc.add_argument("--subtree", help="broadcast to all members under a root (full|base|role)")
    bc.add_argument("--message", default=None, help="message text (if omitted, read stdin)")
    bc.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")
    bc.add_argument("--include-excluded", action="store_true", help="include excluded roles when using --subtree")
    bc.add_argument("--notify", action="store_true", help="also inject a short inbox notice into recipients' CLIs (discouraged)")

    notice = sub.add_parser("notice", help="send a notice (FYI, no reply expected; supports stdin)")
    notice.add_argument("targets", nargs="*", help="targets (full|base|role). Ignored when --role/--subtree is used.")
    notice.add_argument("--role", choices=enabled_roles, help="notice all members of a role")
    notice.add_argument("--subtree", help="notice all members under a root (full|base|role)")
    notice.add_argument("--message", default=None, help="message text (if omitted, read stdin)")
    notice.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")
    notice.add_argument("--include-excluded", action="store_true", help="include excluded roles when using --subtree")
    notice.add_argument("--notify", action="store_true", help="also inject inbox notice into recipient CLIs (discouraged)")

    action = sub.add_parser("action", help="send an action/instruction (no immediate ACK; supports stdin)")
    action.add_argument("targets", nargs="*", help="targets (full|base|role). Ignored when --role/--subtree is used.")
    action.add_argument("--role", choices=enabled_roles, help="send an action to all members of a role")
    action.add_argument("--subtree", help="send an action to all members under a root (full|base|role)")
    action.add_argument("--message", default=None, help="message text (if omitted, read stdin)")
    action.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")
    action.add_argument("--include-excluded", action="store_true", help="include excluded roles when using --subtree")
    action.add_argument("--notify", action="store_true", help="also inject inbox notice into recipient CLIs (discouraged)")

    resolve = sub.add_parser("resolve", help="resolve a target to full tmux session name (full|base|role)")
    resolve.add_argument("target")

    attach = sub.add_parser("attach", help="enter a worker tmux session (full|base|role)")
    attach.add_argument("target")

    route = sub.add_parser("route", help="find best owner(s) for a query")
    route.add_argument("query")
    route.add_argument("--role", choices=enabled_roles)
    route.add_argument("--limit", type=int, default=5)

    ask = sub.add_parser("ask", help="twf ask wrapper (supports stdin)")
    ask.add_argument("name")
    ask.add_argument("message", nargs="?")
    ask.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")
    ask.add_argument("--notify", action="store_true", help="inject inbox notice into recipient CLI (discouraged)")
    ask.add_argument("--wait", action="store_true", help="wait for reply (implies CLI injection; requires --notify)")

    send = sub.add_parser("send", help="twf send wrapper with policy checks (supports stdin)")
    send.add_argument("name")
    send.add_argument("message", nargs="?")
    send.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")
    send.add_argument("--notify", action="store_true", help="also inject inbox notice into recipient CLI (discouraged)")

    gather = sub.add_parser("gather", help="create a reply-needed request to multiple targets (supports stdin)")
    gather.add_argument("targets", nargs="+", help="targets (full|base|role)")
    gather.add_argument("--message", default=None, help="message text (if omitted, read stdin)")
    gather.add_argument("--topic", default="", help="request topic/title (default: first non-empty line)")
    gather.add_argument("--deadline", default="", help="deadline duration (default: config; e.g. 1h, 30m, 900s)")
    gather.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")

    respond = sub.add_parser("respond", help="reply to a reply-needed request (supports stdin)")
    respond.add_argument("request_id", help="request id (e.g. req-000123)")
    respond.add_argument("message", nargs="?")
    respond.add_argument("--blocked", action="store_true", help="acknowledge but block/snooze reminders")
    respond.add_argument("--snooze", default="", help="snooze duration for --blocked (default: config; e.g. 15m)")
    respond.add_argument("--waiting-on", default="", help="who you are waiting on (base name, optional)")
    respond.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")

    reply_needed = sub.add_parser("reply-needed", help="list pending reply-needed requests (self by default)")
    reply_needed.add_argument("--target", default="", help="optional target to inspect (full|base|role)")

    request = sub.add_parser("request", help="show request status/paths")
    request.add_argument("request_id")

    receipts = sub.add_parser("receipts", help="query read receipts for a msg id across recipients")
    receipts.add_argument("msg_id")
    receipts.add_argument("targets", nargs="*", help="optional targets (full|base|role); default: all members")
    receipts.add_argument("--role", choices=enabled_roles, help="limit to a role")
    receipts.add_argument("--subtree", help="limit to a subtree under a root (full|base|role)")

    handoff = sub.add_parser("handoff", help="create a handoff/permit so two members can talk directly")
    handoff.add_argument("a", help="member A (full|base|role)")
    handoff.add_argument("b", help="member B (full|base|role)")
    handoff.add_argument("--as", dest="as_target", default=None, help="creator (full|base|role); required outside tmux")
    handoff.add_argument("--reason", default="", help="handoff reason (optional)")
    handoff.add_argument("--ttl", type=int, default=None, help="permit ttl in seconds (optional)")
    handoff.add_argument("--dry-run", action="store_true", help="do not write/send; print what would happen")
    handoff.add_argument("--notify", action="store_true", help="also inject inbox notice into both CLIs (discouraged)")

    pend = sub.add_parser("pend", help="twf pend wrapper")
    pend.add_argument("name")
    pend.add_argument("n", nargs="?", type=int)

    ping = sub.add_parser("ping", help="twf ping wrapper")
    ping.add_argument("name")

    drive = sub.add_parser("drive", help="get/set drive mode (running|standby)")
    drive.add_argument("mode", nargs="?", default="", help="running|standby")

    state = sub.add_parser("state", help="print agent state table (or one target)")
    state.add_argument("target", nargs="?", default="", help="optional target (full|base|role)")

    sub.add_parser("state-self", help="print current worker state (inside tmux)")

    state_set_self = sub.add_parser("state-set-self", help="set current worker state (inside tmux)")
    state_set_self.add_argument("status", help="working|draining|idle")

    state_set = sub.add_parser("state-set", help="operator: set a worker state (use --force for draining/idle)")
    state_set.add_argument("target", help="target member (full|base|role)")
    state_set.add_argument("status", help="working|draining|idle")
    state_set.add_argument("--force", action="store_true", help="allow setting draining/idle for other workers")

    watch_idle = sub.add_parser("watch-idle", help="operator: wake idle workers when inbox has unread messages")
    watch_idle.add_argument("--interval", type=float, default=None, help="poll interval seconds (default: config)")
    watch_idle.add_argument("--delay", type=float, default=None, help="wake delay seconds (default: config)")
    watch_idle.add_argument("--message", default="", help="wake message injected into Codex TUI (default: config)")
    watch_idle.add_argument("--working-stale", type=float, default=None, help="alert coord if a working worker has pending inbox older than N seconds (default: config)")
    watch_idle.add_argument("--alert-cooldown", type=float, default=None, help="minimum seconds between alerts per worker (default: config)")
    watch_idle.add_argument("--once", action="store_true", help="run one tick then exit")
    watch_idle.add_argument("--dry-run", action="store_true", help="do not write/send; print nothing, but still polls")

    inbox = sub.add_parser("inbox", help="list unread inbox messages (self by default)")
    inbox.add_argument("--target", default="", help="optional target inbox to inspect (full|base|role)")

    inbox_open = sub.add_parser("inbox-open", help="print a message body from inbox by id (self by default)")
    inbox_open.add_argument("msg_id")
    inbox_open.add_argument("--target", default="", help="optional target inbox to inspect (full|base|role)")

    inbox_ack = sub.add_parser("inbox-ack", help="mark an inbox message as read (self only)")
    inbox_ack.add_argument("msg_id")

    inbox_pending = sub.add_parser("inbox-pending", help="count pending messages you sent to a target")
    inbox_pending.add_argument("target", help="target member (full|base|role)")
    inbox_pending.add_argument("--as", dest="as_target", default=None, help="actor (full|base|role); required outside tmux")

    boot = sub.add_parser("bootstrap", help="send the role prompt template to a worker")
    boot.add_argument("name")
    boot.add_argument("role", choices=enabled_roles)

    return p

def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_deps_env_defaults()

    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "reset":
        return cmd_reset(args)
    if args.cmd == "up":
        return cmd_up(args)
    if args.cmd == "spawn":
        return cmd_spawn(args)
    if args.cmd == "spawn-self":
        return cmd_spawn_self(args)
    if args.cmd == "parent":
        return cmd_parent(args)
    if args.cmd == "parent-self":
        return cmd_parent_self(args)
    if args.cmd == "children":
        return cmd_children(args)
    if args.cmd == "children-self":
        return cmd_children_self(args)
    if args.cmd == "report-up":
        return cmd_report_up(args)
    if args.cmd == "report-to":
        return cmd_report_to(args)
    if args.cmd == "self":
        return cmd_self(args)
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "register-self":
        return cmd_register_self(args)
    if args.cmd == "set-scope":
        return cmd_set_scope(args)
    if args.cmd == "set-scope-self":
        return cmd_set_scope_self(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "where":
        return cmd_where(args)
    if args.cmd == "templates-check":
        return cmd_templates_check(args)
    if args.cmd == "policy":
        return cmd_policy(args)
    if args.cmd == "perms-self":
        return cmd_perms_self(args)
    if args.cmd == "tree":
        return cmd_tree(args)
    if args.cmd == "design-path":
        return cmd_design_path(args)
    if args.cmd == "design-init":
        return cmd_design_init(args)
    if args.cmd == "design-init-self":
        return cmd_design_init_self(args)
    if args.cmd == "worktree-path":
        return cmd_worktree_path(args)
    if args.cmd == "worktree-create":
        return cmd_worktree_create(args)
    if args.cmd == "worktree-create-self":
        return cmd_worktree_create_self(args)
    if args.cmd == "worktree-check-self":
        return cmd_worktree_check_self(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "resume":
        return cmd_resume(args)
    if args.cmd == "remove-subtree":
        return cmd_remove_subtree(args)
    if args.cmd == "pause":
        return cmd_pause(args)
    if args.cmd == "unpause":
        return cmd_unpause(args)
    if args.cmd == "broadcast":
        return cmd_broadcast(args)
    if args.cmd == "notice":
        return cmd_notice(args)
    if args.cmd == "action":
        return cmd_action(args)
    if args.cmd == "resolve":
        return cmd_resolve(args)
    if args.cmd == "attach":
        return cmd_attach(args)
    if args.cmd == "route":
        return cmd_route(args)
    if args.cmd == "ask":
        return cmd_ask(args)
    if args.cmd == "send":
        return cmd_send(args)
    if args.cmd == "gather":
        return cmd_gather(args)
    if args.cmd == "respond":
        return cmd_respond(args)
    if args.cmd == "reply-needed":
        return cmd_reply_needed(args)
    if args.cmd == "request":
        return cmd_request(args)
    if args.cmd == "receipts":
        return cmd_receipts(args)
    if args.cmd == "handoff":
        return cmd_handoff(args)
    if args.cmd == "pend":
        return cmd_pend(args)
    if args.cmd == "ping":
        return cmd_ping(args)
    if args.cmd == "drive":
        return cmd_drive(args)
    if args.cmd == "state":
        return cmd_state(args)
    if args.cmd == "state-self":
        return cmd_state_self(args)
    if args.cmd == "state-set-self":
        return cmd_state_set_self(args)
    if args.cmd == "state-set":
        return cmd_state_set(args)
    if args.cmd == "watch-idle":
        return cmd_watch_idle(args)
    if args.cmd == "inbox":
        return cmd_inbox(args)
    if args.cmd == "inbox-open":
        return cmd_inbox_open(args)
    if args.cmd == "inbox-ack":
        return cmd_inbox_ack(args)
    if args.cmd == "inbox-pending":
        return cmd_inbox_pending(args)
    if args.cmd == "bootstrap":
        return cmd_bootstrap(args)

    raise SystemExit("❌ unreachable")
