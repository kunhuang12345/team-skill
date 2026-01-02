#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SUPPORTED_ROLES = ("pm", "arch", "prod", "dev", "qa", "ops", "coord", "liaison")
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


def _resolve_twf_state_dir(twf: Path) -> Path:
    # Mirror twf's state dir resolution (subset):
    # - env override: TWF_STATE_DIR
    # - config: scripts/twf_config.yaml (auto/global/manual)
    override = os.environ.get("TWF_STATE_DIR", "").strip()
    if override:
        return _expand_path(override)

    tmux_skill_dir = twf.resolve().parents[1]
    cfg_path = tmux_skill_dir / "scripts" / "twf_config.yaml"
    cfg = _read_simple_yaml_kv(cfg_path)

    mode = (cfg.get("twf_state_dir_mode", "") or "auto").strip().lower()
    if mode not in {"auto", "global", "manual"}:
        mode = "auto"

    if mode == "global":
        return Path.home() / ".twf"

    if mode == "manual":
        raw = (cfg.get("twf_state_dir", "") or "").strip()
        if not raw:
            raise SystemExit(f"❌ twf_state_dir_mode=manual but twf_state_dir is empty in: {cfg_path}")
        return _expand_path(raw)

    return tmux_skill_dir / ".twf"


def _rm_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError:
        # Best effort.
        return


def _tmux_kill_session(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    subprocess.run(["tmux", "kill-session", "-t", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_reset(args: argparse.Namespace) -> int:
    """
    Reset current environment by deleting all local temp/state artifacts:
    - ai-team-workflow share dir (registry/task/design)
    - tmux-workflow workers for this project (tmux sessions, state files, worker homes)
    - codex-load-balancer local state.json
    """
    expected_root = _expected_project_root()
    twf = _resolve_twf()

    # 1) Stop/remove tmux-workflow workers for this project.
    state_dir = _resolve_twf_state_dir(twf)
    worker_candidates: list[tuple[Path, dict[str, Any]]] = []
    if state_dir.is_dir():
        for p in sorted(state_dir.glob("*.json")):
            try:
                data = _read_json(p)
            except SystemExit:
                continue
            if not data:
                continue
            if _state_file_matches_project(p, expected_root):
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
        team_dir = _default_team_dir()
        print(f"ai_team_share_dir: {team_dir}")
        lb_state = (_skill_dir().parent / "codex-load-balancer" / "share" / "state.json").resolve()
        print(f"clb_state: {lb_state}")
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
    team_dir = _default_team_dir()
    _rm_tree(team_dir)

    # 3) Remove local codex-load-balancer state (per-project).
    clb_share = _skill_dir().parent / "codex-load-balancer" / "share"
    clb_state = clb_share / "state.json"
    clb_lock = clb_share / "state.json.lock"
    try:
        clb_state.unlink()
    except Exception:
        pass
    try:
        clb_lock.unlink()
    except Exception:
        pass
    # If share becomes empty, remove it.
    try:
        if clb_share.is_dir() and not any(clb_share.iterdir()):
            clb_share.rmdir()
    except Exception:
        pass

    _eprint("✅ reset complete")
    return 0


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


def _member_state_file(m: dict[str, Any]) -> Path | None:
    raw = m.get("state_file")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return _expand_path(raw)
    except Exception:
        return None


def _expected_project_root() -> Path:
    try:
        return _git_root()
    except SystemExit:
        return Path.cwd().resolve()


def _state_file_matches_project(state_file: Path, expected_root: Path) -> bool:
    data = _read_json(state_file)
    if not data:
        return False

    work_dir_norm = data.get("work_dir_norm")
    if isinstance(work_dir_norm, str) and work_dir_norm.strip():
        actual = Path(work_dir_norm.strip()).resolve()
    else:
        work_dir = data.get("work_dir")
        if not isinstance(work_dir, str) or not work_dir.strip():
            return False
        actual = Path(work_dir.strip()).resolve()

    expected = expected_root.resolve()
    return actual == expected or expected in actual.parents


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = text if text.endswith("\n") else text + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _task_path(team_dir: Path) -> Path:
    return team_dir / "task.md"


def _design_dir(team_dir: Path) -> Path:
    return team_dir / "design"


def _design_summary_path(team_dir: Path) -> Path:
    return team_dir / "design.md"


def _ops_dir(team_dir: Path) -> Path:
    return team_dir / "ops"


def _ops_env_notes_path(team_dir: Path) -> Path:
    return _ops_dir(team_dir) / "env.md"


def _ops_host_deps_path(team_dir: Path) -> Path:
    return _ops_dir(team_dir) / "host-deps.md"


def _design_member_path(team_dir: Path, full: str) -> Path:
    safe = full.strip()
    if not safe:
        raise ValueError("full is required")
    return _design_dir(team_dir) / f"{safe}.md"


def _extract_task_file_from_text(task: str) -> str | None:
    raw = task.strip()
    if not raw:
        return None

    candidates = []
    if raw.startswith("任务描述：") or raw.startswith("任务描述:"):
        candidates.append(raw.split(":", 1)[1] if ":" in raw else raw.split("：", 1)[1])
    candidates.append(raw)

    for cand in candidates:
        p = cand.strip().strip('"').strip("'")
        if not p:
            continue
        if not p.startswith("/"):
            continue
        try:
            path = _expand_path(p)
        except Exception:
            continue
        if path.is_file():
            return str(path)
    return None


def _read_task_content(args: argparse.Namespace) -> tuple[str | None, str | None]:
    task_file = str(getattr(args, "task_file", "") or "").strip()
    task_text = str(getattr(args, "task", "") or "").strip()

    if not task_file and task_text:
        guessed = _extract_task_file_from_text(task_text)
        if guessed:
            task_file = guessed
            task_text = ""

    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()

    if task_file:
        path = _expand_path(task_file)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise SystemExit(f"❌ failed to read task file: {path} ({e})")
        return content, str(path)

    if task_text:
        return task_text, None
    if stdin_text:
        return stdin_text, None
    return None, None


def _ensure_share_layout(team_dir: Path) -> None:
    team_dir.mkdir(parents=True, exist_ok=True)
    _design_dir(team_dir).mkdir(parents=True, exist_ok=True)
    _ops_dir(team_dir).mkdir(parents=True, exist_ok=True)


def _ensure_task_and_design_files(team_dir: Path, *, task_content: str | None, task_source: str | None) -> Path | None:
    _ensure_share_layout(team_dir)

    task_path = _task_path(team_dir)
    if task_content is not None:
        header = ""
        if task_source:
            header = (
                f"<!-- AITWF_TASK_SOURCE: {task_source} -->\n"
                f"<!-- AITWF_TASK_SAVED_AT: {_now()} -->\n\n"
            )
        _write_text_atomic(task_path, header + task_content.strip() + "\n")

    summary_path = _design_summary_path(team_dir)
    if not summary_path.exists():
        seed = "# Consolidated Design\n\n"
        if task_path.exists():
            seed += f"- Task: `{task_path}`\n\n"
        seed += "PM should consolidate module/team designs into this file.\n"
        _write_text_atomic(summary_path, seed)

    env_notes_path = _ops_env_notes_path(team_dir)
    if not env_notes_path.exists():
        seed = (
            "# Ops: Environment Notes\n\n"
            "## Policy (development-time)\n"
            "- Ops manages the project environment.\n"
            "- Ops can only operate local Docker (no remote hosts).\n"
            "- For a single project, all services must live in a single `docker-compose` file.\n"
            "- If anything must be installed on the host (e.g. `apt`, `brew`, `curl` download/unpack), it must be recorded in:\n"
            f"  - `{_ops_host_deps_path(team_dir)}`\n\n"
            "## Docker Compose\n"
            "- Keep one compose file for the whole project (commonly repo root `docker-compose.yml` or `compose.yaml`).\n"
            "- Prefer bind mounts + named volumes; avoid undocumented host paths.\n"
            "- Put secrets in `.env` (not committed) and document required keys.\n\n"
            "## Change Log\n"
            "- Record noteworthy environment changes here (date + what + why).\n"
        )
        _write_text_atomic(env_notes_path, seed)

    host_deps_path = _ops_host_deps_path(team_dir)
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
        _write_text_atomic(host_deps_path, seed)

    return task_path if task_path.exists() else None


def _design_seed(*, member: dict[str, Any], full: str, team_dir: Path) -> str:
    role = str(member.get("role", "")).strip()
    base = str(member.get("base", "")).strip()
    scope = str(member.get("scope", "")).strip()
    task_path = _task_path(team_dir)

    lines = [
        f"# Design: {full}",
        "",
        f"- role: `{role or '?'}`",
        f"- base: `{base or full}`",
        f"- scope: {scope or '(fill in)'}",
        f"- task: `{task_path}`" if task_path.exists() else "- task: (missing)",
        f"- created_at: {_now()}",
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

    task_content, task_source = _read_task_content(args)
    task_path = _ensure_task_and_design_files(team_dir, task_content=task_content, task_source=task_source)

    if args.registry_only:
        if task_path:
            _eprint(f"✅ shared task saved: {task_path}")
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

    if task_path:
        msg = "[TASK]\n" f"Shared task file: {task_path}\n" "Please read it and proceed.\n"
        res = _run_twf(twf, ["ask", pm_full, msg])
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        return res.returncode

    _eprint("   next: atwf init \"任务描述：...\" (or: atwf init --task-file /abs/path).")
    return 0


def _init_trio(
    *,
    twf: Path,
    registry: Path,
    team_dir: Path,
    force_new: bool,
    no_bootstrap: bool,
) -> dict[str, str]:
    expected_root = _expected_project_root()

    out: dict[str, str] = {}
    for role, label, scope in INITIAL_TRIO:
        base = _base_name(role, label)

        existing_full: str | None = None
        if not force_new:
            data = _load_registry(registry)
            m = _find_latest_member_by(data, role=role, base=base)
            if m:
                candidate = str(m.get("full", "")).strip() or None
                state_file = _member_state_file(m)
                if (
                    candidate
                    and state_file
                    and state_file.is_file()
                    and _state_file_matches_project(state_file, expected_root)
                ):
                    if not _tmux_running(candidate):
                        # Best effort: resume if the state file exists but tmux session is stopped.
                        _run_twf(twf, ["resume", candidate, "--no-tree"])
                    if _tmux_running(candidate):
                        existing_full = candidate

        if existing_full:
            # Keep registry clean: remove duplicates for the same role/base.
            lock = team_dir / ".lock"
            with _locked(lock):
                data = _load_registry(registry)
                _prune_members_by(data, role=role, base=base, keep_full=existing_full)
                _write_json_atomic(registry, data)
            out[role] = existing_full
            continue

        # Remove stale role/base entries before creating a new worker.
        lock = team_dir / ".lock"
        with _locked(lock):
            data = _load_registry(registry)
            _prune_members_by(data, role=role, base=base, keep_full=None)
            _write_json_atomic(registry, data)

        full, session_path = _start_worker(twf, base=base, up_args=[])

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
    pieces: list[str] = []

    rules_path = _templates_dir() / "command_rules.md"
    if rules_path.is_file():
        rules_raw = rules_path.read_text(encoding="utf-8")
        pieces.append(_render_template(rules_raw, role=role, full=full, base=base, registry=registry, team_dir=team_dir).strip())

    template_path = _template_for_role(role)
    raw = template_path.read_text(encoding="utf-8")
    pieces.append(_render_template(raw, role=role, full=full, base=base, registry=registry, team_dir=team_dir).strip())

    msg = "\n\n---\n\n".join(pieces).strip() + "\n"
    # Bootstrap should never block on a reply.
    res = _run_twf(twf, ["send", name, msg])
    if res.returncode != 0:
        _eprint(res.stderr.strip() or f"⚠️ twf send failed (code {res.returncode})")


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


def _tmux_running(session: str) -> bool:
    if not session.strip():
        return False
    res = subprocess.run(["tmux", "has-session", "-t", session], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0


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

    # Merge explicit children lists (if present).
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

    # Dedup + stable sort.
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

    # Stable-ish order by updated_at desc when available.
    def key(full: str) -> str:
        mm = _resolve_member(data, full)
        return str(mm.get("updated_at", "")) if mm else ""

    roots.sort(key=key, reverse=True)
    return roots


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


def _git_root() -> Path:
    res = _run(["git", "rev-parse", "--show-toplevel"])
    if res.returncode != 0:
        raise SystemExit("❌ not a git repository (needed for worktree commands)")
    root = res.stdout.strip()
    if not root:
        raise SystemExit("❌ failed to detect git root")
    return Path(root).resolve()


def _worktrees_dir(git_root: Path) -> Path:
    return git_root / "worktree"


def _worktree_path(git_root: Path, full: str) -> Path:
    return _worktrees_dir(git_root) / full.strip()


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

    git_root = _git_root()
    wt_dir = _worktrees_dir(git_root)
    wt_dir.mkdir(parents=True, exist_ok=True)

    path = _worktree_path(git_root, full)
    base = (args.base or "HEAD").strip() or "HEAD"
    branch = (args.branch or full).strip() or full

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
    ns = argparse.Namespace(target=full, base=args.base, branch=args.branch)
    return cmd_worktree_create(ns)


def cmd_worktree_check_self(_: argparse.Namespace) -> int:
    res = _run(["tmux", "display-message", "-p", "#S"])
    if res.returncode != 0:
        raise SystemExit("❌ worktree-check-self must run inside tmux")
    full = res.stdout.strip()
    if not full:
        raise SystemExit("❌ failed to detect current tmux session name")

    git_root = _git_root()
    expected = _worktree_path(git_root, full).resolve()
    cwd = Path.cwd().resolve()

    if expected == cwd or expected in cwd.parents:
        print("OK")
        return 0

    _eprint("❌ not in your dedicated worktree")
    _eprint(f"   expected: {expected}")
    _eprint(f"   cwd:      {cwd}")
    _eprint(f"   fix:      bash .codex/skills/ai-team-workflow/scripts/atwf worktree-create-self && cd {expected}")
    return 1


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
        root = _resolve_target_full(data, subtree)
        if not root:
            raise SystemExit(f"❌ subtree root not found in registry: {subtree}")
        return _subtree_fulls(data, root)

    raw_targets = targets or []
    if raw_targets:
        resolved: list[str] = []
        for t in raw_targets:
            full = _resolve_target_full(data, str(t))
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


def cmd_stop(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

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


def cmd_resume(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

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


def cmd_broadcast(args: argparse.Namespace) -> int:
    twf = _resolve_twf()
    team_dir = _default_team_dir()
    registry = _registry_path(team_dir)
    data = _load_registry(registry)

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
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    failures: list[str] = []
    # Broadcast is fire-and-forget: fan out sends in parallel and do not wait for replies/confirmations.
    max_workers = min(16, max(1, len(uniq)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_twf, twf, ["send", full, msg]): full for full in uniq}
        for fut in as_completed(futures):
            full = futures[fut]
            sys.stdout.write(f"--- {full} ---\n")
            try:
                res = fut.result()
            except Exception as exc:
                sys.stderr.write(f"❌ broadcast failed: {full}: {exc}\n")
                failures.append(full)
                continue
            sys.stdout.write(res.stdout)
            sys.stderr.write(res.stderr)
            if res.returncode != 0:
                failures.append(full)

    if failures:
        _eprint(f"❌ broadcast failures: {len(failures)} targets")
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
    init.add_argument("task", nargs="?", help="task description (saved to share/task.md); or pipe via stdin")
    init.add_argument("--task-file", help="task file path to copy into share/task.md")
    init.add_argument("--registry-only", action="store_true", help="only create registry, do not start workers")
    init.add_argument("--force-new", action="store_true", help="always start a fresh trio (even if one exists)")
    init.add_argument("--no-bootstrap", action="store_true", help="skip sending role templates on creation")

    reset = sub.add_parser("reset", help="reset local environment (delete worker state + share + lb state)")
    reset.add_argument("--dry-run", action="store_true", help="print what would be deleted, without deleting")
    reset.add_argument("--force", action="store_true", help="also delete codex_home paths outside ~/.codex-workers (dangerous)")

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
    rto.add_argument("target", help="full|base|role (role: pm|arch|prod|dev|qa|ops|coord|liaison)")
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

    wtc = sub.add_parser("worktree-create", help="create a dedicated git worktree under <git-root>/worktree/<full>")
    wtc.add_argument("target", help="full|base|role")
    wtc.add_argument("--base", default="HEAD", help="base ref/branch/commit (default: HEAD)")
    wtc.add_argument("--branch", default="", help="branch name to create for the worktree (default: <full>)")

    wtcs = sub.add_parser("worktree-create-self", help="create a dedicated git worktree for the current tmux worker")
    wtcs.add_argument("--base", default="HEAD", help="base ref/branch/commit (default: HEAD)")
    wtcs.add_argument("--branch", default="", help="branch name to create for the worktree (default: <full>)")

    sub.add_parser("worktree-check-self", help="ensure you are working inside your dedicated worktree (inside tmux)")

    stop = sub.add_parser("stop", help="stop Codex tmux workers (default: whole team)")
    stop.add_argument("targets", nargs="*", help="optional targets (full|base|role)")
    stop.add_argument("--role", choices=SUPPORTED_ROLES, help="stop all members of a role")
    stop.add_argument("--subtree", help="stop all members under a root (full|base|role)")
    stop.add_argument("--dry-run", action="store_true", help="print what would be stopped")

    resume = sub.add_parser("resume", help="resume Codex tmux workers (default: whole team)")
    resume.add_argument("targets", nargs="*", help="optional targets (full|base|role)")
    resume.add_argument("--role", choices=SUPPORTED_ROLES, help="resume all members of a role")
    resume.add_argument("--subtree", help="resume all members under a root (full|base|role)")
    resume.add_argument("--dry-run", action="store_true", help="print what would be resumed")

    bc = sub.add_parser("broadcast", help="send the same message to multiple workers (sequential)")
    bc.add_argument("targets", nargs="*", help="targets (full|base|role). Ignored when --role/--subtree is used.")
    bc.add_argument("--role", choices=SUPPORTED_ROLES, help="broadcast to all members of a role")
    bc.add_argument("--subtree", help="broadcast to all members under a root (full|base|role)")
    bc.add_argument("--message", default=None, help="message text (if omitted, read stdin)")

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

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
    if args.cmd == "broadcast":
        return cmd_broadcast(args)
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

    raise SystemExit("❌ unreachable")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
