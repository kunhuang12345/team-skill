#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _config_path() -> Path:
    return Path(__file__).resolve().with_name("clb_config.yaml")


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


def _expand_path_from(base: Path, path: str) -> Path:
    p = Path(os.path.expanduser(path.strip()))
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def _parse_sources(raw: str) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []

    if s.lstrip().startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                out: list[str] = []
                for v in parsed:
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip())
                return out
        except json.JSONDecodeError:
            pass

    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]


@dataclass(frozen=True)
class _Config:
    sources: list[Path]
    strategy: str
    state_file: Path
    auth_team_dir: Path | None
    auth_team_glob: str


def _load_config() -> _Config:
    skill_dir = _skill_dir()
    cfg = _read_simple_yaml_kv(_config_path())

    sources_raw = os.environ.get("CLB_SOURCES", "").strip() or cfg.get("sources", "").strip()
    strategy = os.environ.get("CLB_STRATEGY", "").strip() or cfg.get("strategy", "round_robin").strip()
    state_raw = os.environ.get("CLB_STATE_FILE", "").strip() or cfg.get("state_file", "share/state.json").strip()
    auth_team_dir_raw = (
        os.environ.get("CLB_AUTH_TEAM_DIR", "").strip()
        or os.environ.get("AUTH_TEAM_DIR", "").strip()
        or cfg.get("auth_team_dir", "").strip()
    )
    auth_team_glob = os.environ.get("CLB_AUTH_TEAM_GLOB", "").strip() or cfg.get("auth_team_glob", "*").strip()

    strategy = (strategy or "round_robin").strip().lower()
    if strategy not in {"round_robin", "hash"}:
        raise SystemExit(f"❌ unsupported strategy: {strategy!r} (use: round_robin|hash)")

    sources_in = _parse_sources(sources_raw)
    sources: list[Path] = []
    for s in sources_in:
        try:
            p = _expand_path_from(skill_dir, s)
        except Exception:
            continue
        if p.is_dir():
            sources.append(p)

    if not sources:
        default = Path.home() / ".codex"
        if default.is_dir():
            sources = [default]
        else:
            raise SystemExit(
                "❌ no valid sources configured.\n"
                "   Set `sources:` in scripts/clb_config.yaml (or env CLB_SOURCES).\n"
                f"   Default fallback missing: {default}"
            )

    # Dedup while preserving order.
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in sources:
        k = str(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    sources = uniq

    state_file = _expand_path_from(skill_dir, state_raw or "share/state.json")

    auth_team_dir: Path | None = None
    if auth_team_dir_raw:
        auth_team_dir = _expand_path_from(skill_dir, auth_team_dir_raw)

    auth_team_glob = auth_team_glob.strip() or "*"
    return _Config(
        sources=sources,
        strategy=strategy,
        state_file=state_file,
        auth_team_dir=auth_team_dir,
        auth_team_glob=auth_team_glob,
    )


def _lock_path(state_file: Path) -> Path:
    return state_file.with_suffix(state_file.suffix + ".lock")


def _read_state(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write_state_atomic(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _b64url_decode(s: str) -> bytes:
    raw = s.encode("utf-8")
    pad = b"=" * ((4 - (len(raw) % 4)) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _jwt_payload(token: str) -> dict[str, object] | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = _b64url_decode(parts[1]).decode("utf-8", errors="replace")
        data = json.loads(payload)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _auth_meta(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, str] = {}

    api_key = data.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        out["auth_type"] = "api_key"
        return out

    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return out

    account_id = tokens.get("account_id")
    if isinstance(account_id, str) and account_id.strip():
        out["account_id"] = account_id.strip()

    id_token = tokens.get("id_token")
    if not isinstance(id_token, str) or not id_token.strip():
        return out

    payload = _jwt_payload(id_token)
    if not payload:
        return out

    email = payload.get("email")
    if isinstance(email, str) and email.strip():
        out["email"] = email.strip()

    auth = payload.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        plan = auth.get("chatgpt_plan_type")
        if isinstance(plan, str) and plan.strip():
            out["plan"] = plan.strip()

    return out


def _pick_round_robin(*, cfg: _Config) -> Path:
    lock_path = _lock_path(cfg.state_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as f:
        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
        except Exception:
            pass

        state = _read_state(cfg.state_file)
        counter = state.get("counter", 0)
        try:
            counter_i = int(counter) if counter is not None else 0
        except Exception:
            counter_i = 0

        idx = counter_i % len(cfg.sources)
        counter_i += 1

        state["counter"] = counter_i
        state["updated_at"] = _now()
        _write_state_atomic(cfg.state_file, state)

        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass

    return cfg.sources[idx]


def _pick_hash(*, cfg: _Config, worker: str) -> Path:
    h = hashlib.sha256(worker.encode("utf-8")).digest()
    n = int.from_bytes(h[:8], "big")
    idx = n % len(cfg.sources)
    return cfg.sources[idx]

def _sync_codex_home(src_root: Path, dst_root: Path) -> None:
    # Minimal copy of a CODEX_HOME template, excluding worker-local roots.
    exclude = {"sessions", "log", "history.jsonl"}
    src_root = src_root.expanduser()
    dst_root = dst_root.expanduser()
    if not src_root.is_dir():
        raise SystemExit(f"❌ source CODEX_HOME not found: {src_root}")
    dst_root.mkdir(parents=True, exist_ok=True)

    def safe_unlink(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
        except FileNotFoundError:
            return

    def is_same_filetype(a: Path, b: Path) -> bool:
        try:
            if a.is_symlink() or b.is_symlink():
                return a.is_symlink() and b.is_symlink()
            if a.is_dir() and b.is_dir():
                return True
            if a.is_file() and b.is_file():
                return True
        except OSError:
            return False
        return False

    def sync_entry(src: Path, dst: Path) -> None:
        if dst.exists() and not is_same_filetype(src, dst):
            safe_unlink(dst)

        if src.is_symlink():
            try:
                target = os.readlink(src)
            except OSError:
                return
            if dst.exists():
                safe_unlink(dst)
            dst.symlink_to(target)
            return

        if src.is_dir():
            sync_dir(src, dst)
            return

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def sync_dir(src_dir: Path, dst_dir: Path) -> None:
        dst_dir.mkdir(parents=True, exist_ok=True)
        try:
            src_children = {p.name: p for p in src_dir.iterdir()}
        except OSError:
            return

        for name, src_child in src_children.items():
            sync_entry(src_child, dst_dir / name)

        try:
            for dst_child in dst_dir.iterdir():
                if dst_child.name not in src_children:
                    safe_unlink(dst_child)
        except OSError:
            return

    try:
        src_entries = {p.name: p for p in src_root.iterdir() if p.name not in exclude}
    except OSError as exc:
        raise SystemExit(f"❌ failed to list source CODEX_HOME: {exc}") from exc

    for name, src_entry in src_entries.items():
        sync_entry(src_entry, dst_root / name)

    # Delete extras in dst, but preserve excluded root items (worker-local)
    try:
        for dst_entry in dst_root.iterdir():
            if dst_entry.name in exclude:
                continue
            if dst_entry.name not in src_entries:
                safe_unlink(dst_entry)
    except OSError:
        pass

    (dst_root / "sessions").mkdir(parents=True, exist_ok=True)
    (dst_root / "log").mkdir(parents=True, exist_ok=True)


def _auth_candidates(cfg: _Config) -> list[Path]:
    if cfg.auth_team_dir is None:
        raise SystemExit(
            "❌ AUTH_TEAM directory is required for auth balancing.\n"
            "   Set `auth_team_dir:` in codex-load-balancer/scripts/clb_config.yaml,\n"
            "   or export CLB_AUTH_TEAM_DIR=/abs/path/to/AUTH_TEAM (or AUTH_TEAM_DIR=...)."
        )
    if not cfg.auth_team_dir.is_dir():
        raise SystemExit(f"❌ auth_team_dir not found: {cfg.auth_team_dir}")

    pattern = cfg.auth_team_glob.strip() or "*"
    out: list[Path] = []
    state_file = cfg.state_file.resolve()
    try:
        for p in cfg.auth_team_dir.glob(pattern):
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            try:
                if p.resolve() == state_file:
                    continue
            except OSError:
                pass
            if p.name.endswith(".lock") or p.name.endswith(".tmp"):
                continue
            if p.name in {".DS_Store"}:
                continue
            out.append(p.resolve())
    except OSError:
        pass

    if not out:
        raise SystemExit(f"❌ no auth candidates found under {cfg.auth_team_dir} (glob={pattern!r})")
    out.sort(key=lambda p: str(p))
    return out


def _pick_auth_balanced(*, cfg: _Config) -> Path:
    candidates = _auth_candidates(cfg)

    lock_path = _lock_path(cfg.state_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as f:
        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
        except Exception:
            pass

        state = _read_state(cfg.state_file)
        raw_counts = state.get("auth_counts")
        counts_in: dict[str, int] = {}
        if isinstance(raw_counts, dict):
            for k, v in raw_counts.items():
                if not isinstance(k, str) or not k.strip():
                    continue
                try:
                    counts_in[k] = int(v) if v is not None else 0
                except Exception:
                    counts_in[k] = 0

        # Prune + normalize to current candidates only.
        counts: dict[str, int] = {}
        for p in candidates:
            key = str(p)
            counts[key] = max(0, counts_in.get(key, 0))

        min_count = min(counts.values())
        least_used = [p for p in candidates if counts[str(p)] == min_count]
        picked = secrets.choice(least_used)

        counts[str(picked)] = counts[str(picked)] + 1
        state["auth_counts"] = counts
        state["updated_at"] = _now()
        state["auth_updated_at"] = state["updated_at"]
        _write_state_atomic(cfg.state_file, state)

        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass

    return picked


def _dotted_path_segment(name: str) -> str | None:
    # Codex's `-c key=value` dotted-path parser does NOT support quoted segments.
    # That means keys that contain "." cannot be addressed safely.
    # Hyphens (e.g. fetch-md) are OK.
    if not name:
        return None
    if "." in name or "=" in name:
        return None
    if any(ch.isspace() for ch in name):
        return None
    return name


def _detect_mcp_server_names() -> list[str]:
    try:
        res = subprocess.run(
            ["codex", "mcp", "list"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return []

    if res.returncode != 0 or not res.stdout.strip():
        return []

    lines = res.stdout.splitlines()
    names: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("Name ") or s.startswith("Name\t"):
            continue
        if set(s) <= {"-"}:
            continue
        # Expect first column is name.
        parts = s.split()
        if not parts:
            continue
        name = parts[0].strip()
        if name and name != "Name":
            names.append(name)

    # Dedup preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _build_codex_cmd_args() -> list[str]:
    # For `clb status`, we want startup to be as close to a normal user
    # invocation as possible. MCP is disabled by editing the temporary
    # CODEX_HOME config.toml, not by passing many `-c ...` flags.
    return ["codex"]


def _strip_mcp_servers_from_config(config_path: Path) -> bool:
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        return False

    lines = raw.splitlines(keepends=True)
    out: list[str] = []
    changed = False

    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            # Section header like: [mcp_servers.fetch-md]
            header = stripped[1:-1].strip()
            if header == "mcp_servers" or header.startswith("mcp_servers."):
                skipping = True
                changed = True
                continue
            skipping = False
        if skipping:
            changed = True
            continue
        out.append(line)

    if not changed:
        return False

    try:
        config_path.write_text("".join(out), encoding="utf-8")
        return True
    except OSError:
        return False


def _tmux_has(session: str) -> bool:
    res = subprocess.run(["tmux", "has-session", "-t", session], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0


def _tmux_kill(session: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", session], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _tmux_capture(target: str, *, lines: int = 200) -> str:
    start = f"-{max(50, lines)}"
    res = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", target, "-S", start],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return res.stdout or ""


def _tmux_send_keys(target: str, *keys: str) -> None:
    subprocess.run(
        ["tmux", "send-keys", "-t", target, *keys],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _tmux_type_line(target: str, text: str) -> None:
    _tmux_send_keys(target, text)


def _tmux_press_enter(target: str) -> None:
    _tmux_send_keys(target, "Enter")


def _strip_status_line(line: str) -> str:
    s = line.strip()
    if s.startswith("│"):
        s = s[1:]
    if s.endswith("│"):
        s = s[:-1]
    return s.strip()


def _parse_status(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = _strip_status_line(raw)
        if not line:
            continue
        for key in ("Model:", "Directory:", "Approval:", "Sandbox:", "Agents.md:", "Account:", "Session:", "5h limit:", "Weekly limit:", "Context window:"):
            if key in line:
                val = line.split(key, 1)[1].strip()
                out[key[:-1].lower().replace(" ", "_")] = val
                break
    return out


def _extract_status_block(text: str) -> str:
    # Best-effort: find the last rendered /status "usage/status" card box and return it.
    #
    # Important: do NOT match the startup banner box (it contains Model/Directory too).
    lines = text.splitlines()
    anchors = (
        "Token usage",
        "Weekly limit:",
        "5h limit:",
        "Context window:",
        "Visit https://chatgpt.com/codex/settings/usage",
        "Account:",
    )
    last_idx: int | None = None
    for i, raw in enumerate(lines):
        if any(a in raw for a in anchors):
            last_idx = i
    if last_idx is None:
        return ""

    # Expand to nearest box borders.
    top = last_idx
    while top > 0 and not (lines[top].lstrip().startswith("╭") or lines[top].lstrip().startswith("┌")):
        top -= 1
    bot = last_idx
    while bot < len(lines) - 1 and not (lines[bot].lstrip().startswith("╰") or lines[bot].lstrip().startswith("└")):
        bot += 1
    return "\n".join(lines[top : bot + 1]).strip()


def _extract_status_tail(text: str) -> str:
    # Return the output starting from the most recent "/status" invocation
    # (including the command line itself), so we can ignore the startup banner.
    lines = text.splitlines()
    last_idx: int | None = None
    for i, raw in enumerate(lines):
        if "/status" in raw:
            last_idx = i
    if last_idx is None:
        return ""
    return "\n".join(lines[last_idx:]).rstrip()


def _status_block_score(block: str) -> int:
    # Prefer "richer" cards that include limits/bars. This is used to pick the
    # best snapshot if the UI is updating while we poll.
    score = 0
    for needle in (
        "Visit https://chatgpt.com/codex/settings/usage",
        "Model:",
        "Directory:",
        "Approval:",
        "Sandbox:",
        "Agents.md:",
        "Account:",
        "Session:",
        "Context window:",
        "5h limit:",
        "Weekly limit:",
    ):
        if needle in block:
            score += 1
    # Penalize the placeholder.
    if "data not available yet" in block.lower():
        score -= 2
    return score


def _status_block_complete(block: str) -> bool:
    # The user wants the real limits lines (bars).
    return "5h limit:" in block and "Weekly limit:" in block


def _find_codex_exit_code(text: str) -> int | None:
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("[CODEX_EXIT]"):
            tail = s.split("[CODEX_EXIT]", 1)[1].strip()
            try:
                return int(tail.split()[0])
            except Exception:
                return None
    return None


def cmd_status(args: argparse.Namespace) -> int:
    team_dir = Path(os.path.expanduser(args.team_dir)).resolve()
    if not team_dir.is_dir():
        raise SystemExit(f"❌ team dir not found: {team_dir}")

    glob_pat = (args.glob or "").strip() or "auth.json*"
    try:
        candidates = [p.resolve() for p in team_dir.glob(glob_pat) if p.is_file()]
    except OSError:
        candidates = []

    # Filter common junk.
    candidates = [p for p in candidates if not (p.name.endswith(".lock") or p.name.endswith(".tmp") or p.name == ".DS_Store")]
    candidates.sort(key=lambda p: p.name)
    if not candidates:
        raise SystemExit(f"❌ no auth files found under {team_dir} (glob={glob_pat!r})")

    cfg = _load_config()
    home_src = cfg.sources[0]
    state = _read_state(cfg.state_file)
    raw_counts = state.get("auth_counts")
    counts: dict[str, int] = {}
    if isinstance(raw_counts, dict):
        for k, v in raw_counts.items():
            if not isinstance(k, str) or not k.strip():
                continue
            try:
                counts[k] = int(v) if v is not None else 0
            except Exception:
                counts[k] = 0

    codex_args = _build_codex_cmd_args()
    codex_cmd = " ".join(shlex.quote(a) for a in codex_args)

    results: list[tuple[Path, dict[str, str], str, str, str, int | None]] = []

    with tempfile.TemporaryDirectory(prefix="clb-status-") as tmp:
        tmp_root = Path(tmp).resolve()
        home = tmp_root / "home"
        _sync_codex_home(home_src, home)
        if bool(args.disable_mcp):
            _strip_mcp_servers_from_config(home / "config.toml")

        for i, auth_file in enumerate(candidates, start=1):
            # Install selected auth into the temp home.
            shutil.copy2(auth_file, home / "auth.json")
            try:
                os.chmod(home / "auth.json", 0o600)
            except OSError:
                pass
            try:
                (home / ".auth_current_name").unlink(missing_ok=True)  # py>=3.8
            except TypeError:
                try:
                    if (home / ".auth_current_name").exists():
                        (home / ".auth_current_name").unlink()
                except OSError:
                    pass
            except OSError:
                pass

            session = f"clb-status-{os.getpid()}-{i}"
            target = f"{session}:0.0"
            try:
                if _tmux_has(session):
                    _tmux_kill(session)

                # Keep the tmux session alive even if Codex exits quickly, so we can
                # capture errors without the tmux server disappearing mid-loop.
                inner = (
                    f"export CODEX_HOME={shlex.quote(str(home))}; "
                    f"{codex_cmd}; "
                    'echo "[CODEX_EXIT] $?"; '
                    "sleep 3600"
                )
                launch = f"bash -lc {shlex.quote(inner)}"
                res = subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", str(Path.cwd()), launch], check=False)
                if res.returncode != 0:
                    raise SystemExit("❌ failed to start tmux session for status (is tmux installed?)")

                # Mimic a human: wait for UI to settle, type /status, wait a bit,
                # then press Enter.
                time.sleep(float(args.pre_send_wait))
                before = _tmux_capture(target, lines=500)
                _tmux_type_line(target, "/status")
                time.sleep(float(args.enter_delay))
                _tmux_press_enter(target)

                # Poll until we get the richest /status card we can.
                captured = ""
                best_block = ""
                best_tail = ""
                best_score = -10_000
                deadline = time.time() + float(args.timeout)
                while time.time() < deadline:
                    captured = _tmux_capture(target, lines=800)
                    tail_now = _extract_status_tail(captured)
                    if tail_now.strip():
                        best_tail = tail_now

                    block_now = _extract_status_block(tail_now)
                    if block_now:
                        score = _status_block_score(block_now)
                        if score > best_score:
                            best_score = score
                            best_block = block_now
                        if _status_block_complete(block_now):
                            break
                    if _find_codex_exit_code(captured) is not None:
                        break
                    time.sleep(0.2)

                # Prefer the best status card we saw; fall back to the /status tail.
                tail = best_tail or _extract_status_tail(captured)
                block = best_block or _extract_status_block(tail) or ""
                parsed = _parse_status(block or tail or captured)
                results.append((auth_file, parsed, block or "", tail or "", captured or "", _find_codex_exit_code(captured or "")))

                _tmux_type_line(target, "/exit")
                _tmux_press_enter(target)
                # Give Codex a moment to quit.
                time.sleep(0.5)
            finally:
                _tmux_kill(session)

    # Print results.
    for auth_file, parsed, block, tail, captured, exit_code in results:
        # Delimiter per auth (the status card itself does not include the auth file name).
        print(f"--- {auth_file.name} ---")
        if block.strip():
            print(block.rstrip())
        elif tail.strip():
            # Minimal /status output (e.g. "100% context left")
            print(tail.rstrip())
        elif args.print_raw and (captured or "").strip():
            print(captured.rstrip())
        else:
            print("(no /status output captured; increase --timeout / --pre-send-wait)")
        if exit_code is not None and exit_code != 0:
            print(f"[codex_exit={exit_code}]")
        print()

    return 0


def cmd_where(_: argparse.Namespace) -> int:
    cfg = _load_config()
    print(f"skill_dir:  {str(_skill_dir())}")
    print(f"config:     {str(_config_path())}")
    print(f"strategy:   {cfg.strategy}")
    print(f"state_file: {str(cfg.state_file)}")
    print(f"auth_team_dir: {str(cfg.auth_team_dir) if cfg.auth_team_dir else ''}")
    print(f"auth_team_glob: {cfg.auth_team_glob}")
    print("sources:")
    for p in cfg.sources:
        print(f"  - {p}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    cfg = _load_config()
    for p in cfg.sources:
        print(str(p))
    return 0


def cmd_pick(args: argparse.Namespace) -> int:
    cfg = _load_config()
    worker = (args.worker or "").strip()
    base = (args.base or "").strip()
    if not worker:
        raise SystemExit("❌ --worker is required")
    if not base:
        raise SystemExit("❌ --base is required")

    if cfg.strategy == "round_robin":
        picked = _pick_round_robin(cfg=cfg)
    else:
        picked = _pick_hash(cfg=cfg, worker=worker)

    if os.environ.get("CLB_DEBUG", "").strip():
        _eprint(f"ℹ️ pick strategy={cfg.strategy} worker={worker} base={base} -> {picked}")
    print(str(picked))
    return 0

def cmd_pick_auth(args: argparse.Namespace) -> int:
    cfg = _load_config()
    worker = (args.worker or "").strip()
    base = (args.base or "").strip()
    if not worker:
        raise SystemExit("❌ --worker is required")
    if not base:
        raise SystemExit("❌ --base is required")

    picked = _pick_auth_balanced(cfg=cfg)
    if os.environ.get("CLB_DEBUG", "").strip():
        _eprint(f"ℹ️ pick-auth worker={worker} base={base} -> {picked}")
    print(str(picked))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clb", add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("where", help="print resolved config + sources")
    sub.add_parser("list", help="list source directories (one per line)")

    pick = sub.add_parser("pick", help="pick a CODEX_HOME source directory for a worker")
    pick.add_argument("--worker", required=True)
    pick.add_argument("--base", required=True)

    pick_auth = sub.add_parser("pick-auth", help="pick an auth file from AUTH_TEAM (balanced by least-used)")
    pick_auth.add_argument("--worker", required=True)
    pick_auth.add_argument("--base", required=True)

    status = sub.add_parser("status", help="inspect /status for every auth file under a team dir (tmux required)")
    status.add_argument("team_dir", help="AUTH_TEAM directory containing auth files (names unrestricted)")
    status.add_argument("--glob", default="", help="file glob inside team_dir (default: auth.json*)")
    status.add_argument("--timeout", default="12", help="seconds to wait per account (default: 12)")
    status.add_argument("--pre-send-wait", default="5.0", help="seconds to wait after starting Codex before typing /status (default: 5.0)")
    status.add_argument("--enter-delay", default="0.5", help="seconds to wait after typing /status before pressing Enter (default: 0.5)")
    status.add_argument(
        "--disable-mcp",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="disable all MCP servers for faster startup by stripping mcp_servers from temp config.toml (default: enabled)",
    )
    status.add_argument("--print-raw", action="store_true", help="also print the raw /status box for each account")
    return p


def main(argv: list[str]) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if args.cmd == "where":
        return cmd_where(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "pick":
        return cmd_pick(args)
    if args.cmd == "pick-auth":
        return cmd_pick_auth(args)
    if args.cmd == "status":
        return cmd_status(args)
    raise SystemExit("unreachable")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
