#!/usr/bin/env bash
# learnMe console: ./run.sh start|stop|restart|status|logs|doctor|demo
set -euo pipefail

cd "$(dirname "$0")"
HOST="${LEARNME_HOST:-127.0.0.1}"
PORT="${LEARNME_PORT:-8770}"
DATA="${LEARNME_DATA_DIR:-data}"
PID_FILE="$DATA/server.pid"
LOG_FILE="$DATA/server.log"
mkdir -p "$DATA"
export LEARNME_HOST="$HOST" LEARNME_PORT="$PORT"

if [[ -x .venv/bin/python ]]; then PY=(.venv/bin/python); else PY=(uv run --extra slack python); fi

running() { [[ -f $PID_FILE ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

clean_env() {
  # Don't let task sessions inherit the IDE / agent session that launched us.
  local v
  for v in $(compgen -e | grep -E '^(CLAUDE|VSCODE_|MCP_CONNECTION|CODEX_THREAD|CODEX_SANDBOX)' || true); do unset "$v"; done
  # Node version managers (fnm, nvm) put per-shell PATH entries that vanish with the login
  # session; point at the real node install instead so tasks keep finding node/npm/codex.
  if command -v node >/dev/null; then
    local node_bin
    node_bin="$(dirname "$(readlink -f "$(command -v node)")")"
    PATH="$node_bin:$(tr ':' '\n' <<<"$PATH" | grep -v fnm_multishells | paste -sd: -)"
    export PATH
  fi
}

start() {
  if running; then echo "already running (pid $(cat "$PID_FILE")) → http://$HOST:$PORT"; return; fi
  clean_env
  setsid nohup "${PY[@]}" -m uvicorn server.main:app --host "$HOST" --port "$PORT" \
    --log-level info >>"$LOG_FILE" 2>&1 </dev/null &
  echo $! >"$PID_FILE"
  for _ in $(seq 60); do
    if curl -fs "http://127.0.0.1:$PORT/api/state" >/dev/null 2>&1; then
      echo "started (pid $(cat "$PID_FILE")) → http://$HOST:$PORT"
      return
    fi
    if ! running; then break; fi
    sleep 0.5
  done
  echo "failed to start; last log lines:" >&2
  tail -n 30 "$LOG_FILE" >&2
  exit 1
}

stop() {
  if ! running; then echo "not running"; rm -f "$PID_FILE"; return; fi
  local busy
  busy="$(curl -fs "http://127.0.0.1:$PORT/api/state" 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
ts = [d["manager"]] + d["tasks"]
print(", ".join(t["title"] for t in ts if t["status"] in ("running", "waiting")))' 2>/dev/null || true)"
  [[ -n $busy ]] && echo "note: in-flight turns will resume automatically after restart: $busy"
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid"
  for _ in $(seq 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  rm -f "$PID_FILE"
  echo "stopped"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) if running; then echo "running (pid $(cat "$PID_FILE")) → http://$HOST:$PORT"; else echo "stopped"; fi ;;
  logs) tail -n 100 -f "$LOG_FILE" ;;
  doctor) "${PY[@]}" -m server.doctor ;;
  demo)  # scripted agents, no key; foreground, Ctrl+C to stop. Without a .venv it needs only uv (no agent SDK download).
    clean_env
    if [[ -x .venv/bin/python ]]; then exec .venv/bin/python -m server.demo; fi
    exec uv run --no-project --python '>=3.11' --with 'fastapi>=0.115' --with 'uvicorn[standard]>=0.30' python -m server.demo ;;
  *) echo "usage: $0 start|stop|restart|status|logs|doctor|demo" >&2; exit 2 ;;
esac
