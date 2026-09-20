#!/usr/bin/env bash
#
# Interaktiv PDF Reader - Webapp Launcher
#
# Launches Interaktiv in standalone desktop webapp mode (isolated profile,
# borderless app window, native window classification). When the window is
# closed, the backend server automatically shuts down cleanly.
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# --- Runtime & Lock Directories ------------------------------------------------
if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "${XDG_RUNTIME_DIR}" ]; then
    RUNTIME_DIR="${XDG_RUNTIME_DIR}/interaktiv"
else
    RUNTIME_DIR="/tmp/interaktiv-$(id -u)"
fi
mkdir -m 700 -p "${RUNTIME_DIR}" 2>/dev/null || true

LOCK_DIR="${RUNTIME_DIR}/app.lock"
RUN_FILE="${RUNTIME_DIR}/app.run"
LOG_FILE="${RUNTIME_DIR}/server.log"

PROFILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/interaktiv/chrome-profile"
mkdir -p "${PROFILE_DIR}"

# --- Helpers ------------------------------------------------------------------
port_in_use() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q .
    else
        (cat </dev/null >/dev/tcp/127.0.0.1/"$1") 2>/dev/null
    fi
}

find_free_port() {
    local base="${1:-8080}" p
    for ((p = base; p < base + 50; p++)); do
        if ! port_in_use "$p"; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

url_is_live() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS -o /dev/null --connect-timeout 0.8 --max-time 1.5 "$1" 2>/dev/null
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O /dev/null --timeout=1 --tries=1 "$1" 2>/dev/null
    else
        local port="${1##*:}"
        port_in_use "${port%%/*}"
    fi
}

pick_browser() {
    if [ -n "${BROWSER:-}" ] && command -v "${BROWSER}" >/dev/null 2>&1; then
        echo "${BROWSER}"
        return 0
    fi
    local b
    for b in google-chrome google-chrome-stable chromium chromium-browser \
             brave-browser microsoft-edge vivaldi-stable; do
        if command -v "$b" >/dev/null 2>&1; then
            echo "$b"
            return 0
        fi
    done
    if command -v firefox >/dev/null 2>&1; then
        echo "firefox"
        return 0
    fi
    return 1
}

# --- Check Python -------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
    PYTHON_EXEC="python3"
else
    echo "Error: Python 3 not found on PATH." >&2
    exit 1
fi

BROWSER="$(pick_browser)" || {
    echo "Error: No supported browser found (Chrome, Chromium, Brave, Edge, Firefox)." >&2
    exit 1
}

# --- Handle Already Running Instance ------------------------------------------
if [ -f "${RUN_FILE}" ]; then
    SAVED_URL=""
    SAVED_PID=""
    while IFS='=' read -r key val; do
        case "$key" in
            APP_URL)    SAVED_URL="$val" ;;
            SERVER_PID) SAVED_PID="$val" ;;
        esac
    done < "${RUN_FILE}"

    if [ -n "${SAVED_PID}" ] && kill -0 "${SAVED_PID}" 2>/dev/null && [ -n "${SAVED_URL}" ]; then
        SAVED_URL="${SAVED_URL%/}"
        if url_is_live "${SAVED_URL}/api/status"; then
            echo "Interaktiv is already running at ${SAVED_URL}. Focusing window..."
            if [ "$BROWSER" != "firefox" ]; then
                "$BROWSER" \
                    "--app=${SAVED_URL}/" \
                    "--user-data-dir=${PROFILE_DIR}" \
                    "--disk-cache-dir=${RUNTIME_DIR}/chrome-cache" \
                    "--disk-cache-size=1" \
                    "--media-cache-size=1" \
                    "--class=interaktiv-pdf" \
                    "--name=interaktiv-pdf" \
                    "--app-id=interaktiv-pdf" \
                    "--no-first-run" \
                    "--no-default-browser-check" >/dev/null 2>&1 || true
            else
                firefox --new-window "${SAVED_URL}" >/dev/null 2>&1 || true
            fi
            exit 0
        fi
    fi
    # Stale run file from a dead server
    rm -f "${RUN_FILE}"
fi

rm -rf "${LOCK_DIR}"

# Clean up any orphaned interaktiv-pdf browser processes and profile locks
# to prevent the browser from delegating to a dead session
if ! pgrep -f "main.py.*--auto-shutdown" >/dev/null 2>&1; then
    pkill -f "interaktiv-pdf" 2>/dev/null || true
    rm -f "${PROFILE_DIR}/Singleton"* 2>/dev/null || true
fi

# Purge Chromium cache in both config dir, XDG_CACHE_HOME, and runtime dir
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/interaktiv"
rm -rf "${CACHE_DIR}" "${RUNTIME_DIR}/chrome-cache" \
       "${PROFILE_DIR}/Default/Cache" "${PROFILE_DIR}/Default/Code Cache" \
       "${PROFILE_DIR}/Default/GPUCache" "${PROFILE_DIR}/Default/Service Worker" 2>/dev/null || true

# --- Acquire Lock & Select Port -----------------------------------------------
mkdir "${LOCK_DIR}" 2>/dev/null || {
    # Lock held, wait briefly to see if another process is booting
    sleep 1
}

PORT="$(find_free_port "${PORT:-8080}")" || {
    echo "Error: Could not find an available port." >&2
    rm -rf "${LOCK_DIR}"
    exit 1
}
APP_URL="http://127.0.0.1:${PORT}"

# --- Start Server -------------------------------------------------------------
echo "Starting Interaktiv PDF server on ${APP_URL}..."
: > "${LOG_FILE}"

"${PYTHON_EXEC}" main.py --no-browser --port "${PORT}" --auto-shutdown "$@" >> "${LOG_FILE}" 2>&1 &
SERVER_PID=$!

printf 'APP_URL=%s\nSERVER_PID=%s\n' "${APP_URL}" "${SERVER_PID}" > "${RUN_FILE}"

# Cleanup routine on script exit
cleanup() {
    trap - EXIT INT TERM
    echo "Interaktiv: window closed. Shutting down server (PID ${SERVER_PID:-})..."
    if [ -n "${SERVER_PID:-}" ] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        kill -TERM "${SERVER_PID}" 2>/dev/null || true
        for _ in {1..30}; do
            kill -0 "${SERVER_PID}" 2>/dev/null || break
            sleep 0.1
        done
        kill -9 "${SERVER_PID}" 2>/dev/null || true
    fi
    rm -rf "${LOCK_DIR}" "${RUN_FILE}"
}
trap cleanup EXIT INT TERM

# Wait for server to become responsive
READY=0
for _ in {1..40}; do
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        echo "Error: Interaktiv server exited unexpectedly during startup. Log:" >&2
        tail -n 20 "${LOG_FILE}" >&2
        exit 1
    fi
    if url_is_live "${APP_URL}/api/status"; then
        READY=1
        break
    fi
    sleep 0.1
done

if [ "$READY" -ne 1 ]; then
    echo "Error: Server failed to respond at ${APP_URL} within 4 seconds." >&2
    exit 1
fi

echo "Interaktiv is ready. Opening webapp window..."

# --- Launch Browser -----------------------------------------------------------
START_BROWSER_TIME=$(date +%s)
if [ "$BROWSER" = "firefox" ]; then
    # Firefox does not offer a standalone minimal --app frame like Chromium,
    # but the background watchdog handles shutdown when tab/window closes.
    firefox --new-window "${APP_URL}" >> "${LOG_FILE}" 2>&1
else
    "$BROWSER" \
        "--app=${APP_URL}/" \
        "--user-data-dir=${PROFILE_DIR}" \
        "--disk-cache-dir=${RUNTIME_DIR}/chrome-cache" \
        "--disk-cache-size=1" \
        "--media-cache-size=1" \
        "--class=interaktiv-pdf" \
        "--name=interaktiv-pdf" \
        "--app-id=interaktiv-pdf" \
        "--no-first-run" \
        "--no-default-browser-check" \
        >> "${LOG_FILE}" 2>&1
fi
END_BROWSER_TIME=$(date +%s)
BROWSER_DURATION=$(( END_BROWSER_TIME - START_BROWSER_TIME ))

# If browser command exited in < 1s (e.g. delegated to an existing background process),
# let the server's session watchdog manage the lifecycle rather than killing it instantly.
if [ "$BROWSER_DURATION" -lt 1 ] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    wait "${SERVER_PID}" 2>/dev/null || true
fi

echo "App window closed."
