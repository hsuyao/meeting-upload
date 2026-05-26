#!/bin/bash
# 從 Hermes .env 讀取 Worker 環境變數
set -a
source /home/eric/.hermes/.env
set +a

export RENDER_BASE_URL="https://meeting-upload.onrender.com"
# APP_PASSWORD 已在 .env 中設定（set -a 自動匯出）
# TELEGRAM_BOT_TOKEN 已在 .env 中設定

cd /home/eric/meeting-upload/worker
python3 meeting_worker.py