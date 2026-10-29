#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-}"
KEEP_CONFIG=0

while (($#)); do
    case "$1" in
        --keep-config) KEEP_CONFIG=1 ;;
        -h|--help)
            echo "Usage: uninstall.sh [--keep-config]"
            echo "Removes the MCP service and application. Configuration is removed unless --keep-config is set."
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo bash uninstall.sh" >&2
    exit 1
fi

DEFAULT_FILE="/etc/default/mcpserver"
SERVICE_FILE="/etc/init.d/mcpserver"
PID_FILE="/run/mcpserver.pid"
LOG_FILE="/var/log/mcpserver.log"

if [[ -z "$INSTALL_DIR" && -r "$DEFAULT_FILE" ]]; then
    # shellcheck disable=SC1090
    . "$DEFAULT_FILE"
    INSTALL_DIR="${MCP_INSTALL_DIR:-/opt/mcpserver}"
fi
INSTALL_DIR="${INSTALL_DIR:-/opt/mcpserver}"

if [[ "$INSTALL_DIR" == "/" || -z "$INSTALL_DIR" ]]; then
    echo "Refusing to remove unsafe install directory: '$INSTALL_DIR'" >&2
    exit 1
fi

if [[ -x "$SERVICE_FILE" ]]; then
    "$SERVICE_FILE" stop || true
elif command -v service >/dev/null 2>&1; then
    service mcpserver stop >/dev/null 2>&1 || true
fi

if command -v update-rc.d >/dev/null 2>&1; then
    update-rc.d -f mcpserver remove >/dev/null 2>&1 || true
fi

rm -f "$SERVICE_FILE" "$PID_FILE"
if ((KEEP_CONFIG == 0)); then
    rm -f "$DEFAULT_FILE"
fi
rm -rf -- "$INSTALL_DIR"

echo "MCP server uninstalled."
if ((KEEP_CONFIG)); then
    echo "Configuration preserved: $DEFAULT_FILE"
fi
echo "Log preserved: $LOG_FILE"
echo "System packages (Python, Chromium) were not removed."
