from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import constants as C
from . import inbox
from . import io as io_mod
from . import policy as policy_mod
from . import registry as registry_mod
from . import resolve
from . import runtime
from . import state_store
from . import templates
from . import tmux as tmux_mod
from . import twf as twf_mod
from . import util


def _base_name(role: str, label: str | None) -> str:
    if not label:
        return role
    clean = label.strip().replace(" ", "-")
    clean = "-".join([seg for seg in clean.split("-") if seg])
    if not clean:
        return role
    return f"{role}-{clean}"


@dataclass(frozen=True)
class _InitMember:
    role: str
    base: str
    full: str


@dataclass(frozen=True)
class _InitChildSpec:
    role: str
    base: str
    scope: str


def _init_root_base(cfg: dict[str, Any], *, root_role: str) -> str:
    raw = config_mod._cfg_get(cfg, ("team", "init", "root_label"))
    label = raw.strip() if isinstance(raw, str) else "main"
    return _base_name(root_role, label)


def _init_children_specs(
    cfg: dict[str, Any],
    *,
    policy: policy_mod.TeamPolicy,
    root_base: str,
    root_only: bool,
) -> list[_InitChildSpec]:
    if root_only:
        return []

    raw_children = config_mod._cfg_get(cfg, ("team", "init", "children"))
    explicit = raw_children is not None

    specs: list[_InitChildSpec] = []

    def add_child(*, role: str, base: str, scope: str) -> None:
        role_norm = policy_mod._norm_role(role)
        if not role_norm or role_norm == policy.root_role:
            return
        if role_norm not in policy.enabled_roles:
            if explicit:
                raise SystemExit(f"❌ team.init.children includes unsupported role: {role_norm}")
            return
        base_s = base.strip()
        if not base_s:
            raise SystemExit(f"❌ team.init.children has empty base for role={role_norm}")
        if base_s == root_base.strip():
            raise SystemExit(f"❌ team.init.children base collides with root base: {base_s}")
        allowed = policy.can_hire.get(policy.root_role, frozenset())
        if role_norm not in allowed:
            raise SystemExit(
                f"❌ policy.can_hire: {policy.root_role} cannot hire {role_norm} (init needs it). "
                f"Allowed: {', '.join(sorted(allowed)) or '(none)'}"
            )
        scope_s = scope.strip() or C.DEFAULT_ROLE_SCOPES.get(role_norm, "")
        specs.append(_InitChildSpec(role=role_norm, base=base_s, scope=scope_s))

    if not isinstance(raw_children, list):
        add_child(role="pm", base=_base_name("pm", "main"), scope=C.DEFAULT_ROLE_SCOPES.get("pm", ""))
        add_child(role="liaison", base=_base_name("liaison", "main"), scope=C.DEFAULT_ROLE_SCOPES.get("liaison", ""))
        return specs

    for item in raw_children:
        if isinstance(item, str):
            role = item
            base = _base_name(policy_mod._norm_role(role), "main")
            add_child(role=role, base=base, scope="")
            continue

        if not isinstance(item, dict):
            continue

        role_raw = item.get("role")
        role = role_raw if isinstance(role_raw, str) else str(role_raw or "")

        base = ""
        if "base" in item:
            base_raw = item.get("base")
            base = base_raw if isinstance(base_raw, str) else str(base_raw or "")

        if not base.strip():
            if "label" in item:
                label_raw = item.get("label")
                label = label_raw if isinstance(label_raw, str) else str(label_raw or "")
                label = label.strip()
            else:
                label = "main"
            base = _base_name(policy_mod._norm_role(role), label)

        scope_raw = item.get("scope")
        scope = scope_raw if isinstance(scope_raw, str) else str(scope_raw or "")

        add_child(role=role, base=base, scope=scope)

    return specs


def _init_task_to_role(cfg: dict[str, Any]) -> str:
    raw = config_mod._cfg_get(cfg, ("team", "init", "task_to_role"))
    if isinstance(raw, str):
        return raw.strip()
    return "pm"


def _start_worker(twf: Path, *, base: str, up_args: list[str]) -> tuple[str, Path]:
    res = twf_mod._run_twf(twf, ["up", base, *up_args])
    if res.returncode != 0:
        raise SystemExit(res.stderr.strip() or f"❌ twf up failed (code {res.returncode})")
    session_file = res.stdout.strip()
    if not session_file:
        raise SystemExit("❌ twf up returned empty session file path")
    session_path = runtime._expand_path(session_file)
    full = session_path.stem
    return full, session_path


def _spawn_worker(twf: Path, *, parent_full: str, child_base: str, up_args: list[str]) -> tuple[str, Path]:
    res = twf_mod._run_twf(twf, ["spawn", parent_full, child_base, *up_args])
    if res.returncode != 0:
        raise SystemExit(res.stderr.strip() or f"❌ twf spawn failed (code {res.returncode})")
    session_file = res.stdout.strip()
    if not session_file:
        raise SystemExit("❌ twf spawn returned empty session file path")
    session_path = runtime._expand_path(session_file)
    full = session_path.stem
    return full, session_path


def _normalize_provider(raw: Any, *, default: str = "") -> str:
    v = str(raw or "").strip().lower()
    if not v:
        v = str(default or "").strip().lower()
    if not v:
        return ""
    if v not in {"codex", "claude"}:
        raise SystemExit(f"❌ unknown provider: {v} (expected: codex|claude)")
    return v


def _provider_from_state_file(state_file: Path | None) -> str:
    if not state_file:
        return "codex"
    try:
        data = io_mod._read_json(state_file)
    except SystemExit as e:
        util._eprint(f"⚠️ failed to read provider from state_file: {state_file} ({e})")
        return "codex"
    v = data.get("provider")
    provider = str(v).strip().lower() if isinstance(v, str) else ""
    return provider if provider in {"codex", "claude"} else "codex"


def _bootstrap_worker(
    twf: Path,
    *,
    name: str,
    role: str,
    full: str,
    base: str,
    registry: Path,
    team_dir: Path,
) -> None:
    pieces: list[str] = []

    rules_path = runtime._templates_dir() / "command_rules.md"
    if rules_path.is_file():
        rules_raw = rules_path.read_text(encoding="utf-8")
        pieces.append(
            templates._render_template(rules_raw, role=role, full=full, base=base, registry=registry, team_dir=team_dir).strip()
        )

    template_path = templates._template_for_role(role)
    raw = template_path.read_text(encoding="utf-8")
    pieces.append(templates._render_template(raw, role=role, full=full, base=base, registry=registry, team_dir=team_dir).strip())

    msg = "\n\n---\n\n".join(pieces).strip() + "\n"
    msg_id = inbox._next_msg_id(team_dir)
    inbox._write_inbox_message(
        team_dir,
        msg_id=msg_id,
        kind="bootstrap",
        from_full="atwf-bootstrap",
        from_base="atwf",
        from_role="system",
        to_full=name,
        to_base=base,
        to_role=role,
        body=msg,
    )
    atwf_cmd = runtime._atwf_cmd()
    notice = (
        f"[BOOTSTRAP-INBOX] id={msg_id}\n"
        f"open: {atwf_cmd} inbox-open {msg_id}\n"
        f"ack:  {atwf_cmd} inbox-ack {msg_id}\n"
    )
    wrapped = inbox._wrap_team_message(
        team_dir,
        kind="bootstrap",
        sender_full="atwf-bootstrap",
        sender_role=None,
        to_full=name,
        body=notice,
        msg_id=msg_id,
    )
    res = twf_mod._run_twf(twf, ["send", name, wrapped])
    if res.returncode != 0:
        util._eprint(res.stderr.strip() or f"⚠️ twf send failed (code {res.returncode})")


def _init_team(
    *,
    twf: Path,
    registry: Path,
    team_dir: Path,
    force_new: bool,
    no_bootstrap: bool,
    root_only: bool,
) -> list[_InitMember]:
    state_dir = twf_mod._resolve_twf_state_dir(twf)
    cfg = config_mod._read_yaml_or_json(runtime._config_file())

    policy = policy_mod._policy()
    root_role = policy.root_role

    base_root = _init_root_base(cfg, root_role=root_role)
    init_children = _init_children_specs(cfg, policy=policy, root_base=base_root, root_only=bool(root_only))

    out: list[_InitMember] = []

    def reuse_full(*, role: str, base: str) -> str | None:
        data0 = registry_mod._load_registry(registry)
        m0 = registry_mod._find_latest_member_by(data0, role=role, base=base)
        if not m0:
            return None
        candidate = str(m0.get("full", "")).strip() or None
        if not candidate:
            return None
        state_file = resolve._member_state_file(m0)
        if not (state_file and state_file.is_file()):
            state_file = (state_dir / f"{candidate}.json").resolve()
        if not state_file.is_file():
            return None
        if not tmux_mod._tmux_running(candidate):
            twf_mod._run_twf(twf, ["resume", candidate, "--no-tree"])
        return candidate if tmux_mod._tmux_running(candidate) else None

    def prune_role_base(*, role: str, base: str, keep_full: str | None) -> None:
        lock = team_dir / ".lock"
        with io_mod._locked(lock):
            data1 = registry_mod._load_registry(registry)
            registry_mod._prune_members_by(data1, role=role, base=base, keep_full=keep_full)
            io_mod._write_json_atomic(registry, data1)

    def up_root(*, role: str, base: str, scope: str) -> tuple[str, Path]:
        prune_role_base(role=role, base=base, keep_full=None)
        full, session_path = _start_worker(twf, base=base, up_args=[])
        lock = team_dir / ".lock"
        with io_mod._locked(lock):
            data2 = registry_mod._load_registry(registry)
            registry_mod._ensure_member(
                data2,
                full=full,
                base=base,
                role=role,
                scope=scope,
                parent=None,
                state_file=str(session_path),
            )
            io_mod._write_json_atomic(registry, data2)
        if not no_bootstrap:
            _bootstrap_worker(twf, name=full, role=role, full=full, base=base, registry=registry, team_dir=team_dir)
        return full, session_path

    def spawn_child(*, parent_full: str, role: str, base: str, scope: str) -> tuple[str, Path]:
        prune_role_base(role=role, base=base, keep_full=None)
        full, session_path = _spawn_worker(twf, parent_full=parent_full, child_base=base, up_args=[])
        lock = team_dir / ".lock"
        with io_mod._locked(lock):
            data3 = registry_mod._load_registry(registry)
            registry_mod._ensure_member(
                data3,
                full=full,
                base=base,
                role=role,
                scope=scope,
                parent=parent_full,
                state_file=str(session_path),
            )
            registry_mod._add_child(data3, parent_full=parent_full, child_full=full)
            io_mod._write_json_atomic(registry, data3)
        if not no_bootstrap:
            _bootstrap_worker(twf, name=full, role=role, full=full, base=base, registry=registry, team_dir=team_dir)
        return full, session_path

    root_full: str | None = None
    if not force_new:
        root_full = reuse_full(role=root_role, base=base_root)
    if root_full:
        prune_role_base(role=root_role, base=base_root, keep_full=root_full)
        lock = team_dir / ".lock"
        with io_mod._locked(lock):
            data_root = registry_mod._load_registry(registry)
            registry_mod._ensure_member(
                data_root,
                full=root_full,
                base=base_root,
                role=root_role,
                scope=C.DEFAULT_ROLE_SCOPES.get(root_role, ""),
                parent=None,
            )
            io_mod._write_json_atomic(registry, data_root)
        out.append(_InitMember(role=root_role, base=base_root, full=root_full))
    else:
        root_full, _ = up_root(role=root_role, base=base_root, scope=C.DEFAULT_ROLE_SCOPES.get(root_role, ""))
        out.append(_InitMember(role=root_role, base=base_root, full=root_full))

    for child in init_children:
        role = child.role
        base = child.base
        scope = child.scope
        child_full: str | None = None
        if not force_new:
            child_full = reuse_full(role=role, base=base)
        if child_full:
            prune_role_base(role=role, base=base, keep_full=child_full)
            lock = team_dir / ".lock"
            with io_mod._locked(lock):
                data_child = registry_mod._load_registry(registry)
                registry_mod._ensure_member(data_child, full=child_full, base=base, role=role, scope=scope, parent=root_full)
                registry_mod._add_child(data_child, parent_full=root_full, child_full=child_full)
                io_mod._write_json_atomic(registry, data_child)
            out.append(_InitMember(role=role, base=base, full=child_full))
            continue

        child_full, _ = spawn_child(parent_full=root_full, role=role, base=base, scope=scope)
        out.append(_InitMember(role=role, base=base, full=child_full))

    return out


def _ensure_task_and_design_files(team_dir: Path, *, task_content: str | None, task_source: str | None) -> Path | None:
    state_store._ensure_share_layout(team_dir)

    task_path = state_store._task_path(team_dir)
    if task_content is not None:
        header = ""
        if task_source:
            header = f"<!-- AITWF_TASK_SOURCE: {task_source} -->\n" f"<!-- AITWF_TASK_SAVED_AT: {util._now()} -->\n\n"
        io_mod._write_text_atomic(task_path, header + task_content.strip() + "\n")

    summary_path = state_store._design_summary_path(team_dir)
    if not summary_path.exists():
        seed = "# Consolidated Design\n\n"
        if task_path.exists():
            seed += f"- Task: `{task_path}`\n\n"
        seed += "PM should consolidate module/team designs into this file.\n"
        io_mod._write_text_atomic(summary_path, seed)

    env_notes_path = state_store._ops_env_notes_path(team_dir)
    if not env_notes_path.exists():
        seed = (
            "# Ops: Environment Notes\n\n"
            "## Policy (development-time)\n"
            "- Ops manages the project environment.\n"
            "- Ops can only operate local Docker (no remote hosts).\n"
            "- For a single project, all services must live in a single `docker-compose` file.\n"
            "- If anything must be installed on the host (e.g. `apt`, `brew`, `curl` download/unpack), it must be recorded in:\n"
            f"  - `{state_store._ops_host_deps_path(team_dir)}`\n\n"
            "## Docker Compose\n"
            "- Keep one compose file for the whole project (commonly repo root `docker-compose.yml` or `compose.yaml`).\n"
            "- Prefer bind mounts + named volumes; avoid undocumented host paths.\n"
            "- Put secrets in `.env` (not committed) and document required keys.\n\n"
            "## Change Log\n"
            "- Record noteworthy environment changes here (date + what + why).\n"
        )
        io_mod._write_text_atomic(env_notes_path, seed)

    host_deps_path = state_store._ops_host_deps_path(team_dir)
    if not host_deps_path.exists():
        seed = (
            "# Ops: Host Dependencies (must document)\n\n"
            "Record any dependencies installed outside Docker (OS-level installs, downloaded binaries, etc.).\n\n"
            "## APT (Linux)\n"
            "- (date) `sudo apt-get install ...`  # why\n\n"
            "## Brew (macOS)\n"
            "- (date) `brew install ...`  # why\n\n"
            "## Downloaded binaries\n"
            "- (date) url: ...  dest: ...  sha256: ...  # why\n"
        )
        io_mod._write_text_atomic(host_deps_path, seed)

    to_user_path = team_dir / "to_user.md"
    if not to_user_path.exists():
        seed = (
            "# User-facing Log\n\n"
            "Coordinator appends short user-facing entries here (append-only).\n"
            "Separate entries with `---`.\n"
        )
        io_mod._write_text_atomic(to_user_path, seed)

    return task_path if task_path.exists() else None


def _design_seed(*, member: dict[str, Any], full: str, team_dir: Path) -> str:
    role = str(member.get("role", "")).strip()
    base = str(member.get("base", "")).strip()
    scope = str(member.get("scope", "")).strip()
    task_path = state_store._task_path(team_dir)

    lines = [
        f"# Design: {full}",
        "",
        f"- role: `{role or '?'}`",
        f"- base: `{base or full}`",
        f"- scope: {scope or '(fill in)'}",
        f"- task: `{task_path}`" if task_path.exists() else "- task: (missing)",
        f"- created_at: {util._now()}",
        "",
        "## Goal",
        "",
        "## Scope / Non-goals",
        "",
        "## Approach",
        "",
        "## Plan (steps)",
        "",
        "## Interfaces / Data",
        "",
        "## Testing / Verification",
        "",
        "## Risks / Rollback",
        "",
        "## Open Questions",
        "",
    ]
    return "\n".join(lines)

