#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SUPPORTED_ROLES = ("pm", "arch", "prod", "dev", "qa", "coord", "liaison")
INITIAL_TRIO = (
    ("coord", "main", "internal routing + escalation triage"),
    ("liaison", "main", "user communication + clarifications"),
    ("pm", "main", "overall delivery / milestone planning"),
)
FULL_NAME_RE = re.compile(r"^.+-[0-9]{8}-[0-9]{6}-[0-9]+$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


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


def _config_file() -> Path:
    return Path(__file__).resolve().with_name("atwf_config.yaml")


def _read_simple_yaml_kv(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    out: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if not value:
            out[key] = ""
            continue
        if value[0] in {"'", '"'}:
            q = value[0]
            if value.endswith(q) and len(value) >= 2:
                out[key] = value[1:-1]
            else:
                out[key] = value[1:]
            continue
        if "#" in value:
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1].isspace()):
                    value = value[:i].strip()
                    break
        out[key] = value.strip()
    return out


def _default_team_dir() -> Path:
    env_dir = os.environ.get("AITWF_DIR", "").strip()
    if env_dir:
        return _expand_path(env_dir)

    skill_dir = _skill_dir()
    cfg = _read_simple_yaml_kv(_config_file())
    share_dir = cfg.get("share_dir", "").strip()
    if share_dir:
        return _expand_path_from(skill_dir, share_dir)

    return skill_dir / "share"


def _registry_path(team_dir: Path) -> Path:
    override = os.environ.get("AITWF_REGISTRY", "").strip()
    return _expand_path(override) if override else team_dir / "registry.json"


def _skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _templates_dir() -> Path:
    return _skill_dir() / "templates"


def _resolve_twf() -> Path:
    override = os.environ.get("AITWF_TWF", "").strip()
    if override:
        p = _expand_path(override)
        if p.is_file():
            return p
        raise SystemExit(f"❌ AITWF_TWF points to missing file: {p}")

    skills_dir = _skill_dir().parent
    sibling = skills_dir / "tmux-workflow" / "scripts" / "twf"
    if sibling.is_file():
        return sibling

    global_path = Path.home() / ".codex" / "skills" / "tmux-workflow" / "scripts" / "twf"
    if global_path.is_file():
        return global_path

    raise SystemExit(
        "❌ tmux-workflow not found.\n"
        "   Expected `tmux-workflow/scripts/twf` next to this skill, or set AITWF_TWF=/path/to/twf."
    )


def _run(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _run_twf(twf: Path, args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(twf), *args], input_text=input_text)


def _require_role(role: str) -> str:
    r = role.strip().lower()
    if r not in SUPPORTED_ROLES:
        raise SystemExit(f"❌ unsupported role: {role} (supported: {', '.join(SUPPORTED_ROLES)})")
    return r


def _require_full_name(name: str) -> str:
    n = name.strip()
    if not FULL_NAME_RE.match(n):
        raise SystemExit("❌ remove requires a full worker name like: <base>-YYYYmmdd-HHMMSS-<pid>")
    return n


def _base_name(role: str, label: str | None) -> str:
    if not label:
        return role
    clean = label.strip().replace(" ", "-")
    clean = "-".join([seg for seg in clean.split("-") if seg])
    if not clean:
        return role
    return f"{role}-{clean}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise SystemExit(f"❌ failed to read: {path} ({e})")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"❌ invalid JSON: {path} ({e})")
    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@contextmanager
def _locked(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "w", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


def _load_registry(registry: Path) -> dict[str, Any]:
    data = _read_json(registry)
    if not data:
        return {"version": 1, "created_at": _now(), "updated_at": _now(), "members": []}
    if not isinstance(data.get("members"), list):
        data["members"] = []
    if not isinstance(data.get("version"), int):
        data["version"] = 1
    if not isinstance(data.get("created_at"), str):
        data["created_at"] = _now()
    data["updated_at"] = _now()
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
            "created_at": _now(),
            "updated_at": _now(),
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
    m["updated_at"] = _now()
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
    parent["updated_at"] = _now()


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


def _template_for_role(role: str) -> Path:
    role = _require_role(role)
    p = _templates_dir() / f"{role}.md"
    if not p.is_file():
        raise SystemExit(f"❌ missing template for role={role}: {p}")
    return p


def _render_template(raw: str, *, role: str, full: str, base: str, registry: Path, team_dir: Path) -> str:
    return (
        raw.replace("{{ROLE}}", role)
        .replace("{{FULL_NAME}}", full)
        .replace("{{BASE_NAME}}", base)
        .replace("{{REGISTRY_PATH}}", str(registry))
        .replace("{{TEAM_DIR}}", str(team_dir))
    )


def _ensure_registry_file(registry: Path, team_dir: Path) -> None:
    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)
        _write_json_atomic(registry, data)
    _eprint(f"✅ registry ready: {registry}")


def _resolve_target_full(data: dict[str, Any], target: str) -> str | None:
    target = target.strip()
    if not target:
        return None

    m = _resolve_member(data, target)
    if m:
        full = str(m.get("full", "")).strip()
        return full or None

    if target in SUPPORTED_ROLES:
        m2 = _resolve_latest_by_role(data, target)
        if m2:
            full = str(m2.get("full", "")).strip()
            return full or None

    if FULL_NAME_RE.match(target):
        return target

    return None


def cmd_init(args: argparse.Namespace) -> int:
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    _ensure_registry_file(registry, team_dir)
    if args.registry_only:
        return 0

    twf = _resolve_twf()
    trio = _init_trio(
        twf=twf,
        registry=registry,
        team_dir=team_dir,
        force_new=bool(args.force_new),
        no_bootstrap=bool(args.no_bootstrap),
    )

    pm_full = trio.get("pm", "")
    if not pm_full:
        raise SystemExit("❌ failed to resolve PM worker")

    coord_full = trio.get("coord", "")
    liaison_full = trio.get("liaison", "")
    _eprint("✅ initial trio ready:")
    if pm_full:
        _eprint(f"   pm:      {pm_full}")
    if coord_full:
        _eprint(f"   coord:   {coord_full}")
    if liaison_full:
        _eprint(f"   liaison: {liaison_full}")
    _eprint("   tip: enter a role via: atwf attach pm|coord|liaison")

    task_parts: list[str] = []
    if getattr(args, "task_file", None):
        task_file = str(args.task_file).strip()
        if task_file:
            task_parts.append(f"任务描述文件：{task_file}")
    if getattr(args, "task", None):
        task = str(args.task).strip()
        if task:
            task_parts.append(task)

    if not task_parts and not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()
        if stdin_text:
            task_parts.append(stdin_text)

    if task_parts:
        msg = "[TASK]\n" + "\n".join(task_parts).strip()
        res = _run_twf(twf, ["ask", pm_full, msg])
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        return res.returncode

    _eprint("   next: atwf ask pm \"任务描述：...\" (or pass task to `atwf init ...`).")
    return 0


def _init_trio(
    *,
    twf: Path,
    registry: Path,
    team_dir: Path,
    force_new: bool,
    no_bootstrap: bool,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for role, label, scope in INITIAL_TRIO:
        base = _base_name(role, label)

        existing_full: str | None = None
        if not force_new:
            data = _load_registry(registry)
            m = _find_latest_member_by(data, role=role, base=base)
            if m:
                existing_full = str(m.get("full", "")).strip() or None

        if existing_full:
            out[role] = existing_full
            continue

        full, session_path = _start_worker(twf, base=base, up_args=[])

        lock = team_dir / ".lock"
        with _locked(lock):
            data = _load_registry(registry)
            _ensure_member(
                data,
                full=full,
                base=base,
                role=role,
                scope=scope,
                parent=None,
                state_file=str(session_path),
            )
            _write_json_atomic(registry, data)

        if not no_bootstrap:
            _bootstrap_worker(twf, name=full, role=role, full=full, base=base, registry=registry, team_dir=team_dir)

        out[role] = full

    return out


def _start_worker(twf: Path, *, base: str, up_args: list[str]) -> tuple[str, Path]:
    res = _run_twf(twf, ["up", base, *up_args])
    if res.returncode != 0:
        raise SystemExit(res.stderr.strip() or f"❌ twf up failed (code {res.returncode})")
    session_file = res.stdout.strip()
    if not session_file:
        raise SystemExit("❌ twf up returned empty session file path")
    session_path = _expand_path(session_file)
    full = session_path.stem
    return full, session_path


def _spawn_worker(twf: Path, *, parent_full: str, child_base: str, up_args: list[str]) -> tuple[str, Path]:
    res = _run_twf(twf, ["spawn", parent_full, child_base, *up_args])
    if res.returncode != 0:
        raise SystemExit(res.stderr.strip() or f"❌ twf spawn failed (code {res.returncode})")
    session_file = res.stdout.strip()
    if not session_file:
        raise SystemExit("❌ twf spawn returned empty session file path")
    session_path = _expand_path(session_file)
    full = session_path.stem
    return full, session_path


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
    template_path = _template_for_role(role)
    raw = template_path.read_text(encoding="utf-8")
    msg = _render_template(raw, role=role, full=full, base=base, registry=registry, team_dir=team_dir)
    res = _run_twf(twf, ["ask", name, msg])
    if res.returncode != 0:
        _eprint(res.stderr.strip() or f"⚠️ twf ask failed (code {res.returncode})")


def cmd_up(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    role = _require_role(args.role)
    base = _base_name(role, args.label)

    full, session_path = _start_worker(twf, base=base, up_args=[])

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

    print(full)
    return 0


def cmd_spawn(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    parent_full = args.parent_full.strip()
    if not parent_full:
        raise SystemExit("❌ parent-full is required")

    role = _require_role(args.role)
    base = _base_name(role, args.label)

    full, session_path = _spawn_worker(twf, parent_full=parent_full, child_base=base, up_args=[])

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
        no_bootstrap=args.no_bootstrap,
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
    twf = _resolve_twf()
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
        raise SystemExit("❌ no parent recorded for this worker (root). Use report-to <coord|liaison|name> instead.")

    body = _read_report_body(args.message)
    msg = _format_report(sender=sender, to_full=parent_full, body=body)

    res2 = _run_twf(twf, ["ask", parent_full, msg])
    sys.stdout.write(res2.stdout)
    sys.stderr.write(res2.stderr)
    return res2.returncode


def cmd_report_to(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)

    target = args.target.strip()
    if not target:
        raise SystemExit("❌ target is required")

    data = _load_registry(registry)

    m = _resolve_member(data, target)
    if not m and target in SUPPORTED_ROLES:
        m = _resolve_latest_by_role(data, target)
    if not m:
        raise SystemExit(f"❌ target not found in registry: {target}")

    to_full = str(m.get("full", "")).strip()
    if not to_full:
        raise SystemExit(f"❌ invalid target entry (missing full): {target}")

    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ report-to must run inside tmux")
    self_name = res.stdout.strip()

    sender = _resolve_member(data, self_name) or {"full": self_name, "role": "", "base": self_name, "scope": ""}
    body = _read_report_body(args.message)
    msg = _format_report(sender=sender, to_full=to_full, body=body)

    res2 = _run_twf(twf, ["ask", to_full, msg])
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

    lock = team_dir / ".lock"
    with _locked(lock):
        data = _load_registry(registry)
        _ensure_member(
            data,
            full=full,
            base=base,
            role=role,
            scope=args.scope,
            parent=args.parent,
            state_file=args.state_file,
        )
        if args.parent:
            _add_child(data, parent_full=args.parent, child_full=full)
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


@dataclass(frozen=True)
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
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

    target = args.name.strip()
    if not target:
        raise SystemExit("❌ name is required")
    full = _resolve_target_full(data, target)
    if not full:
        raise SystemExit(f"❌ name not found in registry: {target} (use `atwf list` or `atwf up/spawn`)")
    msg = args.message
    if msg is None:
        msg = _forward_stdin()
    if msg is None:
        raise SystemExit("❌ message missing (provide as arg or via stdin)")
    res = _run_twf(twf, ["ask", full, msg])
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.returncode


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
    p = argparse.ArgumentParser(prog="atwf", add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="init registry and start initial PM/Coordinator/Liaison trio")
    init.add_argument("task", nargs="?", help="task description text to send to PM (or pipe via stdin)")
    init.add_argument("--task-file", help="task file path to include in the PM message")
    init.add_argument("--registry-only", action="store_true", help="only create registry, do not start workers")
    init.add_argument("--force-new", action="store_true", help="always start a fresh trio (even if one exists)")
    init.add_argument("--no-bootstrap", action="store_true", help="skip sending role templates on creation")

    up = sub.add_parser("up", help="start a new role worker (twf up) + register + bootstrap")
    up.add_argument("role")
    up.add_argument("label", nargs="?")
    up.add_argument("--scope", default="")
    up.add_argument("--no-bootstrap", action="store_true")

    sp = sub.add_parser("spawn", help="spawn a child worker (twf spawn) + register + bootstrap")
    sp.add_argument("parent_full")
    sp.add_argument("role")
    sp.add_argument("label", nargs="?")
    sp.add_argument("--scope", default="")
    sp.add_argument("--no-bootstrap", action="store_true")

    sps = sub.add_parser("spawn-self", help="spawn a child worker from the current tmux session")
    sps.add_argument("role")
    sps.add_argument("label", nargs="?")
    sps.add_argument("--scope", default="")
    sps.add_argument("--no-bootstrap", action="store_true")

    parent = sub.add_parser("parent", help="print a member's parent (lookup by full or base)")
    parent.add_argument("name")

    sub.add_parser("parent-self", help="print current worker's parent (inside tmux)")

    children = sub.add_parser("children", help="print a member's children (lookup by full or base)")
    children.add_argument("name")

    sub.add_parser("children-self", help="print current worker's children (inside tmux)")

    rup = sub.add_parser("report-up", help="send a completion/progress report to your parent (inside tmux)")
    rup.add_argument("message", nargs="?")

    rto = sub.add_parser("report-to", help="send a report to a target member or role (inside tmux)")
    rto.add_argument("target", help="full|base|role (role: pm|arch|prod|dev|qa|coord|liaison)")
    rto.add_argument("message", nargs="?")

    sub.add_parser("self", help="print current tmux session name")

    reg = sub.add_parser("register", help="upsert a member into registry.json")
    reg.add_argument("full")
    reg.add_argument("--role", choices=SUPPORTED_ROLES)
    reg.add_argument("--base")
    reg.add_argument("--scope")
    reg.add_argument("--parent")
    reg.add_argument("--state-file")

    regself = sub.add_parser("register-self", help="register current tmux session into registry.json")
    regself.add_argument("--role", required=True, choices=SUPPORTED_ROLES)
    regself.add_argument("--base")
    regself.add_argument("--scope")
    regself.add_argument("--parent")
    regself.add_argument("--state-file")

    ss = sub.add_parser("set-scope", help="update scope for a member (lookup by full or base)")
    ss.add_argument("name")
    ss.add_argument("scope")

    sss = sub.add_parser("set-scope-self", help="update scope for current tmux session")
    sss.add_argument("scope")

    sub.add_parser("list", help="print registry table")

    sub.add_parser("where", help="print resolved shared dirs (team_dir + registry)")

    resolve = sub.add_parser("resolve", help="resolve a target to full tmux session name (full|base|role)")
    resolve.add_argument("target")

    attach = sub.add_parser("attach", help="enter a worker tmux session (full|base|role)")
    attach.add_argument("target")

    route = sub.add_parser("route", help="find best owner(s) for a query")
    route.add_argument("query")
    route.add_argument("--role", choices=SUPPORTED_ROLES)
    route.add_argument("--limit", type=int, default=5)

    ask = sub.add_parser("ask", help="twf ask wrapper (supports stdin)")
    ask.add_argument("name")
    ask.add_argument("message", nargs="?")

    pend = sub.add_parser("pend", help="twf pend wrapper")
    pend.add_argument("name")
    pend.add_argument("n", nargs="?", type=int)

    ping = sub.add_parser("ping", help="twf ping wrapper")
    ping.add_argument("name")

    boot = sub.add_parser("bootstrap", help="send the role prompt template to a worker")
    boot.add_argument("name")
    boot.add_argument("role", choices=SUPPORTED_ROLES)

    rm = sub.add_parser("remove", help="disband the whole team by removing the PM (and all recorded members)")
    rm.add_argument("pm_full", help="PM full name: pm-...-YYYYmmdd-HHMMSS-<pid>")
    rm.add_argument("--dry-run", action="store_true", help="print what would be removed")

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "init":
        return cmd_init(args)
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
    if args.cmd == "resolve":
        return cmd_resolve(args)
    if args.cmd == "attach":
        return cmd_attach(args)
    if args.cmd == "route":
        return cmd_route(args)
    if args.cmd == "ask":
        return cmd_ask(args)
    if args.cmd == "pend":
        return cmd_pend(args)
    if args.cmd == "ping":
        return cmd_ping(args)
    if args.cmd == "bootstrap":
        return cmd_bootstrap(args)
    if args.cmd == "remove":
        return cmd_remove(args)

    raise SystemExit("❌ unreachable")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
