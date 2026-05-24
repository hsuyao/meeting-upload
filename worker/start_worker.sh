#!/bin/bash
export APP_PASSWORD="REDACTED"
export RENDER_BASE_URL="https://meeting-upload.onrender.com"
# TELEGRAM_BOT_TOKEN 繼承系統環境變數（不要写在文件裡）

cd /home/eric/meeting-upload/worker
python3 meeting_worker.py