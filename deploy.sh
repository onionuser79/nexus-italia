#!/usr/bin/env bash
set -euo pipefail

# Deploy nexus-italia via macmini relay -> iw2ohx2.
# Usage: bash nexus-italia/deploy.sh [macmini-lan|macmini-ext]
#   macmini-lan  (default) — when on LAN
#   macmini-ext            — when on internet/mobile

RELAY="${1:-macmini-lan}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE=/tmp/nexus-italia-deploy.tar.gz

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
