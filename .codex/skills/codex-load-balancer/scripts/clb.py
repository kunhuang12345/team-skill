#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
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
    raise SystemExit("unreachable")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
