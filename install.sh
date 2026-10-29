#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/almakano/filesystem-mcp.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/mcpserver}"
SERVICE_FILE="/etc/init.d/mcpserver"
DEFAULT_FILE="/etc/default/mcpserver"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo bash install.sh" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "This installer currently supports Debian/Ubuntu (apt-get)." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3 python3-venv python3-pip

if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
    if ! apt-get install -y chromium; then
        apt-get install -y chromium-browser
    fi
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
elif [[ ! -e "$INSTALL_DIR" ]]; then
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
else
    echo "Install directory exists but is not a Git repository: $INSTALL_DIR" >&2
    exit 1
fi

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

if [[ ! -x "$(command -v chromium || command -v chromium-browser || true)" ]]; then
    echo "Chromium executable was not found after installation." >&2
    exit 1
fi

install -D -m 755 "$INSTALL_DIR/service/mcpserver" "$SERVICE_FILE"
if [[ ! -f "$DEFAULT_FILE" ]]; then
    install -D -m 644 "$INSTALL_DIR/service/mcpserver.default" "$DEFAULT_FILE"
    INSTALL_DIR_SED="${INSTALL_DIR//\/\\}"
    INSTALL_DIR_SED="${INSTALL_DIR_SED//&/\&}"
    INSTALL_DIR_SED="${INSTALL_DIR_SED//#/\#}"
    sed -i "s#^MCP_INSTALL_DIR=.*#MCP_INSTALL_DIR=\"$INSTALL_DIR_SED\"#" "$DEFAULT_FILE"
fi

if command -v update-rc.d >/dev/null 2>&1; then
    update-rc.d mcpserver defaults
fi

echo
echo "Installation completed."
echo "Configure: $DEFAULT_FILE"
echo "Start:     service mcpserver start"
echo "Status:    service mcpserver status"
echo "Logs:      tail -f /var/log/mcpserver.log"
