#!/usr/bin/env bash
set -euo pipefail

# Deploy nexus-italia via macmini relay -> iw2ohx2.
# Usage: bash nexus-italia/deploy.sh [macmini-lan|macmini-ext]
#   macmini-lan  (default) — when on LAN
#   macmini-ext            — when on internet/mobile

RELAY="${1:-auto}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE=/tmp/nexus-italia-deploy.tar.gz

# Run directly when already on macmini instead of SSHing to ourselves.
# "auto" (default) detects; pass macmini-lan/macmini-ext to force the relay,
# or "local" to force direct deployment.
#
# Detection is by macmini's static LAN address (en9 wired .127 / en1 Wi-Fi
# .128), not by hostname: the machine reports a managed asset name
# (DEL-02-0481-DT), so a hostname test silently falls through to the relay.
if [[ "$RELAY" == "auto" ]]; then
    RELAY="macmini-lan"
    if [[ "$(uname -s)" == "Darwin" ]]; then
        for ip in 192.168.1.127 192.168.1.128; do
            if ifconfig 2>/dev/null | grep -q "inet $ip\b"; then
                RELAY="local"
                break
            fi
        done
    fi
fi

if [[ "$RELAY" == "local" ]]; then
    echo "==> nexus-italia deploy (local macmini) -> iw2ohx2"
    echo "  >> backing up current deployment..."
    ssh iw2ohx2 'TS=$(date +%Y%m%d-%H%M%S); \
        sudo cp -a /opt/nexus-gateway-v2/nexus_gateway /opt/nexus-gateway-v2/nexus_gateway.bak-$TS; \
        sudo cp -a /opt/nexus-gateway-v2/config.yaml /opt/nexus-gateway-v2/config.yaml.bak-$TS; \
        echo "     backup tag: $TS"'

    echo "  >> syncing to iw2ohx2..."
    rsync -az --delete --exclude='__pycache__' --exclude='*.pyc' \
        "$LOCAL_DIR/nexus_gateway/" iw2ohx2:~/nexus-italia-v2/nexus_gateway/
    rsync -az --delete --exclude='__pycache__' --exclude='*.pyc' \
        "$LOCAL_DIR/tests/" iw2ohx2:~/nexus-italia-v2/tests/
    rsync -az "$LOCAL_DIR/requirements.txt" iw2ohx2:~/nexus-italia-v2/

    echo "  >> installing to /opt/nexus-gateway-v2..."
    ssh iw2ohx2 'sudo rsync -a --delete --exclude=__pycache__ \
            ~/nexus-italia-v2/nexus_gateway/ /opt/nexus-gateway-v2/nexus_gateway/ \
        && sudo rsync -a --delete ~/nexus-italia-v2/tests/ /opt/nexus-gateway-v2/tests/ \
        && sudo cp ~/nexus-italia-v2/requirements.txt /opt/nexus-gateway-v2/'

    echo "  >> running test suite on target..."
    ssh iw2ohx2 'cd /opt/nexus-gateway-v2 && \
        ./.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3'

    echo "==> done — service NOT restarted"
    echo "    to apply: ssh iw2ohx2 sudo systemctl restart nexus-gateway-v2"
    exit 0
fi

echo "==> nexus-italia deploy via $RELAY -> iw2ohx2"

echo "  >> packaging..."
tar -czf "$ARCHIVE" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='deploy.sh' \
    -C "$(dirname "$LOCAL_DIR")" nexus-italia/

echo "  >> staging to $RELAY..."
ssh "$RELAY" 'mkdir -p ~/deploy-staging'
scp -q "$ARCHIVE" "$RELAY:~/deploy-staging/nexus-italia-deploy.tar.gz"

echo "  >> $RELAY -> iw2ohx2..."
ssh "$RELAY" 'bash -s' << 'REMOTE'
set -euo pipefail
mkdir -p ~/deploy-staging/nexus-italia
tar -xzf ~/deploy-staging/nexus-italia-deploy.tar.gz -C ~/deploy-staging/
rsync -az --delete \
    --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude 'deploy.sh' \
    ~/deploy-staging/nexus-italia/ iw2ohx2:~/nexus-italia-v2/
ssh iw2ohx2 "sudo cp -r ~/nexus-italia-v2/nexus_gateway /opt/nexus-gateway-v2/ \
    && sudo cp ~/nexus-italia-v2/requirements.txt /opt/nexus-gateway-v2/"
REMOTE

echo "==> done — service not restarted"
echo "    to apply: ssh macmini-lan 'ssh iw2ohx2 sudo systemctl restart nexus-gateway-v2'"
