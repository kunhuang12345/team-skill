#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE' >&2
Usage:
  codex_up_tmux.sh [--session NAME] [--session-file PATH] [--cmd "codex ..."] [--attach]

Defaults:
  - session-file: ./.ccb-codex-session.json
  - session name: codex-<sha1(realpath(cwd))[:10]>
  - cmd:         codex -c disable_paste_burst=true

Environment:
  CCB_CODEX_SESSION_FILE  Override session file path
  CCB_TMUX_SESSION        Override tmux session name
  CCB_CODEX_CMD           Override codex command
USAGE
}

session_file="${CCB_CODEX_SESSION_FILE:-.ccb-codex-session.json}"
tmux_session="${CCB_TMUX_SESSION:-}"
codex_cmd="${CCB_CODEX_CMD:-codex -c disable_paste_burst=true}"
attach=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session|-s)
      tmux_session="${2:-}"; shift 2 ;;
    --session-file|-f)
      session_file="${2:-}"; shift 2 ;;
    --cmd)
      codex_cmd="${2:-}"; shift 2 ;;
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

work_dir="$PWD"
work_dir_norm="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$work_dir")"

if [[ -z "$tmux_session" ]]; then
  hash="$(python3 -c 'import hashlib,sys; print(hashlib.sha1(sys.argv[1].encode()).hexdigest()[:10])' "$work_dir_norm")"
  tmux_session="codex-$hash"
fi

tmux_target="${tmux_session}:0.0"

if tmux has-session -t "$tmux_session" >/dev/null 2>&1; then
  echo "ℹ️  Reusing tmux session: $tmux_session" >&2
else
  echo "🚀 Starting tmux session: $tmux_session" >&2
  tmux new-session -d -s "$tmux_session" -c "$work_dir" "$codex_cmd"
fi

pane_id="$(tmux display-message -p -t "$tmux_target" '#{pane_id}' 2>/dev/null || true)"

python3 - "$session_file" "$tmux_session" "$tmux_target" "$pane_id" "$work_dir" "$work_dir_norm" "$codex_cmd" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

session_file = Path(sys.argv[1]).expanduser()
tmux_session = sys.argv[2]
tmux_target = sys.argv[3]
pane_id = sys.argv[4] or None
work_dir = sys.argv[5]
work_dir_norm = sys.argv[6]
codex_cmd = sys.argv[7]

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

def sessions_root() -> Path:
    import os

    if os.environ.get("CCB_CODEX_SESSION_ROOT"):
        return Path(os.environ["CCB_CODEX_SESSION_ROOT"]).expanduser()
    if os.environ.get("CODEX_SESSION_ROOT"):
        return Path(os.environ["CODEX_SESSION_ROOT"]).expanduser()
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]).expanduser() / "sessions"
    return Path.home() / ".codex" / "sessions"

def find_log_for_cwd(expected_norm: str):
    root = sessions_root()
    if not root.exists():
        return None, None

    best_path = None
    best_mtime = -1.0
    best_id = None

    for p in root.glob("**/*.jsonl"):
        if not p.is_file():
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < best_mtime:
            continue
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                head = f.readline().strip()
        except OSError:
            continue
        if not head:
            continue
        try:
            entry = json.loads(head)
        except Exception:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "session_meta":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        cwd = payload.get("cwd")
        if not isinstance(cwd, str):
            continue
        try:
            cwd_norm = str(Path(cwd).expanduser().resolve())
        except Exception:
            cwd_norm = cwd
        if cwd_norm != expected_norm:
            continue

        best_path = p
        best_mtime = mtime
        sid = payload.get("id")
        best_id = sid if isinstance(sid, str) else None

    return (str(best_path) if best_path else None, best_id)

data = load_json(session_file)

data.update(
    {
        "terminal": "tmux",
        "tmux_session": tmux_session,
        "tmux_target": tmux_target,
        "pane_id": pane_id,
        "work_dir": work_dir,
        "work_dir_norm": work_dir_norm,
        "active": True,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "codex_start_cmd": codex_cmd,
    }
)

log_path, session_id = find_log_for_cwd(work_dir_norm)
if log_path:
    data["codex_session_path"] = log_path
if session_id:
    data["codex_session_id"] = session_id

tmp = session_file.with_suffix(session_file.suffix + ".tmp")
tmp.parent.mkdir(parents=True, exist_ok=True)
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(session_file)
print(json.dumps({"tmux_session": data["tmux_session"], "tmux_target": data["tmux_target"]}))
PY

if [[ "$attach" -eq 1 ]]; then
  tmux attach -t "$tmux_session"
fi
