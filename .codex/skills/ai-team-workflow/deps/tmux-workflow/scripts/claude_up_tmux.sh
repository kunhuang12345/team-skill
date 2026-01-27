#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE' >&2
Usage:
  claude_up_tmux.sh [--session NAME] [--session-file PATH] [--work-dir DIR] [--claude-session-id UUID] [--resume] [--cmd "claude ..."] [--attach]

Defaults:
  - session-file: ./.claude-tmux-session.json
  - session name: claude-<sha1(realpath(cwd))[:10]>
  - cmd:         claude
  - work-dir:    current directory

Notes:
  - This script starts a long-running Claude Code CLI inside tmux.
  - Each worker should have a unique Claude session id. For new sessions, use:
      claude --session-id <uuid>
    For resuming an existing session, use:
      claude --resume <uuid>

Environment:
  TWF_SESSION_FILE       Override session file path
  TWF_TMUX_SESSION       Override tmux session name
  TWF_CLAUDE_CMD         Override base Claude command (without --session-id/--resume)
  TWF_CODEX_CMD_CONFIG   YAML/JSON config path (default: scripts/twf_config.yaml; reads optional claude.cmd/claude.args)
  TWF_WORK_DIR           Override starting work directory for the tmux session
  CLAUDE_CONFIG_DIR      Optional Claude config dir (affects where sessions are stored; default is ~/.claude)
USAGE
}

config_file="${TWF_CODEX_CMD_CONFIG:-$script_dir/twf_config.yaml}"
if [[ -z "${TWF_CODEX_CMD_CONFIG:-}" && ! -f "$config_file" && -f "$script_dir/twf_config.json" ]]; then
  config_file="$script_dir/twf_config.json"
fi

build_default_claude_cmd() {
  python3 - "$config_file" <<'PY'
import json
import shlex
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1]).expanduser()
raw = ""
try:
    if cfg_path.exists():
        raw = cfg_path.read_text(encoding="utf-8")
except Exception:
    raw = ""

def parse_yaml(text: str) -> dict:
    out: dict[str, str] = {}
    for line in text.splitlines():
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

def load_cfg(text: str) -> dict:
    raw_s = text.strip()
    if not raw_s:
        return {}

    if raw_s.startswith("{"):
        try:
            data = json.loads(raw_s)
        except Exception:
            data = None
        if isinstance(data, dict):
            return data

    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return parse_yaml(text)

cfg = load_cfg(raw)

def get_path(data: dict, path: list[str]):
    cur = data
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

def get_str(*paths: list[str], default: str = "") -> str:
    for p in paths:
        v = get_path(cfg, p)
        if isinstance(v, str):
            return v.strip()
    return default

cmd = get_str(["claude", "cmd"], ["claude_cmd"], default="claude").strip() or "claude"
args = get_str(["claude", "args"], ["claude_args"], default="").strip()

parts: list[str] = []
try:
    parts.extend(shlex.split(cmd))
except Exception:
    parts.append(cmd)
if args:
    try:
        parts.extend(shlex.split(args))
    except Exception:
        parts.append(args)

print(" ".join(shlex.quote(p) for p in parts))
PY
}

session_file="${TWF_SESSION_FILE:-.claude-tmux-session.json}"
tmux_session="${TWF_TMUX_SESSION:-}"
claude_cmd="${TWF_CLAUDE_CMD:-}"
work_dir="${TWF_WORK_DIR:-$PWD}"
claude_session_id=""
resume=0
attach=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session|-s)
      tmux_session="${2:-}"; shift 2 ;;
    --session-file|-f)
      session_file="${2:-}"; shift 2 ;;
    --cmd)
      claude_cmd="${2:-}"; shift 2 ;;
    --work-dir|-C)
      work_dir="${2:-}"; shift 2 ;;
    --claude-session-id)
      claude_session_id="${2:-}"; shift 2 ;;
    --resume)
      resume=1; shift ;;
    --attach)
      attach=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$claude_cmd" ]]; then
  claude_cmd="$(build_default_claude_cmd)"
fi

if [[ -z "$work_dir" ]]; then
  echo "❌ --work-dir is empty" >&2
  exit 1
fi
work_dir="$(python3 - "$work_dir" <<'PY'
import os
import sys
from pathlib import Path

raw = sys.argv[1]
p = Path(os.path.expanduser(raw))
if not p.is_absolute():
    p = (Path.cwd() / p).resolve()
print(str(p))
PY
)"
if [[ ! -d "$work_dir" ]]; then
  echo "❌ --work-dir is not a directory: $work_dir" >&2
  exit 1
fi
work_dir_norm="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$work_dir")"

if [[ -z "$tmux_session" ]]; then
  hash="$(python3 -c 'import hashlib,sys; print(hashlib.sha1(sys.argv[1].encode()).hexdigest()[:10])' "$work_dir_norm")"
  tmux_session="claude-$hash"
fi

if [[ -z "$claude_session_id" ]]; then
  echo "❌ missing --claude-session-id" >&2
  exit 1
fi

final_cmd="$(
  python3 - "$claude_cmd" "$claude_session_id" "$resume" <<'PY'
import shlex
import sys
import uuid

base_cmd = sys.argv[1]
sid_raw = sys.argv[2].strip()
resume = sys.argv[3].strip() == "1"

try:
    sid = str(uuid.UUID(sid_raw))
except Exception:
    raise SystemExit(f"❌ invalid Claude session id (must be UUID): {sid_raw!r}")

try:
    parts = shlex.split(base_cmd)
except Exception:
    parts = [base_cmd]

reserved = {"--session-id", "--resume", "-r", "-c", "--continue"}
for p in parts:
    if p in reserved:
        raise SystemExit("❌ do not include --session-id/--resume/--continue in TWF_CLAUDE_CMD or claude.cmd; twf manages session selection")

if resume:
    parts.extend(["--resume", sid])
else:
    parts.extend(["--session-id", sid])

print(" ".join(shlex.quote(p) for p in parts))
PY
)"

claude_config_dir_raw="${CLAUDE_CONFIG_DIR:-}"
claude_config_dir_resolved="$(
  python3 - "$claude_config_dir_raw" <<'PY'
import os
import sys
from pathlib import Path

raw = (sys.argv[1] or "").strip()
if not raw:
    print("")
    raise SystemExit(0)
p = Path(os.path.expanduser(raw))
if not p.is_absolute():
    p = (Path.cwd() / p).resolve()
print(str(p))
PY
)"

aitwf_dir_raw="${AITWF_DIR:-}"
aitwf_dir_resolved="$(
  python3 - "$aitwf_dir_raw" <<'PY'
import os
import sys
from pathlib import Path

raw = (sys.argv[1] or "").strip()
if not raw:
    print("")
    raise SystemExit(0)
p = Path(os.path.expanduser(raw))
if not p.is_absolute():
    p = (Path.cwd() / p).resolve()
print(str(p))
PY
)"

tmux_target="${tmux_session}:0.0"

if tmux has-session -t "$tmux_session" >/dev/null 2>&1; then
  echo "ℹ️  Reusing tmux session: $tmux_session" >&2
else
  echo "🚀 Starting tmux session: $tmux_session" >&2
  # Clear cross-project env leakage from long-lived tmux server, then re-inject
  # the current values explicitly.
  tmux_env=(env -u AITWF_DIR -u CLAUDE_CONFIG_DIR)
  if [[ -n "$aitwf_dir_resolved" ]]; then
    tmux_env+=("AITWF_DIR=$aitwf_dir_resolved")
  fi
  if [[ -n "$claude_config_dir_resolved" ]]; then
    tmux_env+=("CLAUDE_CONFIG_DIR=$claude_config_dir_resolved")
  fi

  if [[ ${#tmux_env[@]} -gt 1 ]]; then
    tmux new-session -d -s "$tmux_session" -c "$work_dir" "${tmux_env[@]}" bash -c "$final_cmd"
  else
    tmux new-session -d -s "$tmux_session" -c "$work_dir" bash -c "$final_cmd"
  fi
fi

if [[ -n "$claude_config_dir_resolved" ]]; then
  tmux set-environment -t "$tmux_session" CLAUDE_CONFIG_DIR "$claude_config_dir_resolved" >/dev/null 2>&1 || true
else
  tmux set-environment -t "$tmux_session" -u CLAUDE_CONFIG_DIR >/dev/null 2>&1 || true
fi

if [[ -n "$aitwf_dir_resolved" ]]; then
  tmux set-environment -t "$tmux_session" AITWF_DIR "$aitwf_dir_resolved" >/dev/null 2>&1 || true
else
  tmux set-environment -t "$tmux_session" -u AITWF_DIR >/dev/null 2>&1 || true
fi

pane_id="$(tmux display-message -p -t "$tmux_target" '#{pane_id}' 2>/dev/null || true)"

python3 - "$session_file" "$tmux_session" "$tmux_target" "$pane_id" "$work_dir" "$work_dir_norm" "$final_cmd" "$claude_session_id" "$claude_config_dir_resolved" "$resume" <<'PY'
import json
import re
import sys
from datetime import datetime
from pathlib import Path

session_file = Path(sys.argv[1]).expanduser()
tmux_session = sys.argv[2]
tmux_target = sys.argv[3]
pane_id = sys.argv[4] or None
work_dir = sys.argv[5]
work_dir_norm = sys.argv[6]
start_cmd = sys.argv[7]
claude_session_id = sys.argv[8]
claude_config_dir = sys.argv[9].strip()
resumed = sys.argv[10].strip() == "1"

def load_json(path: Path) -> dict:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}

def project_key_for_path(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)

data = load_json(session_file)

cfg_dir = Path(claude_config_dir).expanduser() if claude_config_dir else (Path.home() / ".claude")
projects_root = cfg_dir / "projects"
project_key = project_key_for_path(work_dir_norm)
project_dir = projects_root / project_key
session_path = project_dir / f"{claude_session_id}.jsonl"

data.update(
    {
        "provider": "claude",
        "terminal": "tmux",
        "tmux_session": tmux_session,
        "tmux_target": tmux_target,
        "pane_id": pane_id,
        "work_dir": work_dir,
        "work_dir_norm": work_dir_norm,
        "active": True,
        "claude_start_cmd": start_cmd,
        "claude_session_id": claude_session_id,
        "claude_config_dir": str(cfg_dir),
        "claude_projects_root": str(projects_root),
        "claude_project_key": project_key,
        "claude_project_dir": str(project_dir),
        "claude_session_path": str(session_path),
    }
)

key = "resumed_at" if resumed else "started_at"
data[key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

tmp = session_file.with_suffix(session_file.suffix + ".tmp")
tmp.parent.mkdir(parents=True, exist_ok=True)
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(session_file)
print(json.dumps({"tmux_session": tmux_session, "tmux_target": tmux_target}))
PY

if [[ "$attach" -eq 1 ]]; then
  tmux attach -t "$tmux_session"
fi
