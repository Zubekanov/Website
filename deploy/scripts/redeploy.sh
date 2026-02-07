#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/zubekanov/Repositories/Website_Dev"
SERVICE_NAME="website.service"
BRANCH="main"

cd "$APP_DIR"

echo "[1/5] Fetching latest code..."
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "[2/5] Installing Python dependencies..."
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/src/requirements.txt"

echo "[3/5] Checking Gunicorn config..."
"$APP_DIR/.venv/bin/gunicorn" --check-config -c "$APP_DIR/deploy/gunicorn.conf.py" wsgi:app --chdir "$APP_DIR/src"

echo "[4/5] Restarting service..."
sudo systemctl restart "$SERVICE_NAME"

echo "[5/5] Service status"
sudo systemctl --no-pager --full status "$SERVICE_NAME"
