# Meeting Upload — Render + Worker 架構

## 組件

```
meeting-upload/
├── render_app/          # Render Flask Web
│   ├── app.py           # Flask 應用（含 responsive UI）
│   ├── requirements.txt # flask, gunicorn
│   └── render.yaml      # Render Blueprint
└── worker/              # Linux Worker（你的機器）
    └── meeting_worker.py
```

## 部署步驟

### 1. Render Flask App

1. 將 `render_app/` 目錄推送到 GitHub
2. 登入 [Render Dashboard](https://dashboard.render.com)
3. New → Blueprint → 選 GitHub repo
4. Render 自動偵測 `render.yaml`，部署完成後 URL 例如：
   `https://meeting-upload.onrender.com`

**環境變數（Render Dashboard 設定）：**
- `RENDER_BASE_URL`：你的 Render URL（例如 `https://meeting-upload-xxx.onrender.com`）
- `APP_PASSWORD`：網頁存取密碼（不填則無保護）

### 2. Linux Worker（你的機器）

在 `/home/eric/meeting-upload/worker/`：

```bash
# 設定環境變數
export RENDER_BASE_URL="https://meeting-upload-xxx.onrender.com"
export APP_PASSWORD="your_password"
export TELEGRAM_BOT_TOKEN="你的bot_token"

# 啟動 worker（nohup 後台運行）
nohup python3 meeting_worker.py &
```

### 3. Keep Render awake（你的 Linux 每 2 分鐘 ping）

在 Worker 的 polling 迴圈已經包含對 Render 的輪詢，不需要另外 ping。

---

## API 端點

| 方法 | 路由 | 用途 |
|------|------|------|
| GET | `/` | 首頁（responsive UI） |
| POST | `/api/jobs` | 新建 job（接收音頻） |
| GET | `/api/jobs?status=pending` | Worker 輪詢 pending jobs |
| GET | `/api/jobs/<id>` | 查 job 狀態 |
| GET | `/api/jobs/<id>/audio` | Worker 下載音頻 |
| POST | `/api/jobs/<id>/processing` | Worker 標記 processing |
| POST | `/api/jobs/<id>/complete` | Worker 標記完成（帶 Notion URL）|
| POST | `/api/jobs/<id>/fail` | Worker 標記失敗 |
| GET | `/health` | 健康檢查 |

---

## 流程

```
瀏覽器錄音/上傳
       ↓
   POST /api/jobs
       ↓
   存檔 → jobs.json 記錄
       ↓
 Worker 每 120s 輪詢 /api/jobs?status=pending
       ↓
 發現新 job → GET /api/jobs/<id>/audio（下載音頻）
       ↓
  SCP → Mac（mlx_whisper + pyannote）→ SRT
       ↓
  本地 SRT → post_srt_to_notion.py
       ↓
  MiniMax-M2.7 → Notion Page + Telegram 通知
       ↓
  POST /api/jobs/<id>/complete（帶 Notion URL）
       ↓
 前端輪詢 → 狀態更新為 ✅ 完成
```

---

## 已知限制

1. **Render Free Plan**：15 分鐘無流量會 sleep。Worker 每 2 分鐘 polling + 下載可保持 awake。若流量仍不足，考慮 Hobby Plan（$7/月）。
2. **Ephemeral Disk**：檔案在重部署時會清空。但音頻處理完就刪，無需持久儲存。
3. **檔案大小**：Flask 預設 16MB limit（可調）。

---

## 本地測試（Deploy 前）

```bash
# Flask 本地測試
cd render_app
pip install flask gunicorn
python app.py

# Worker 本地測試（需要先有一個測試 Render instance）
export RENDER_BASE_URL="http://localhost:5000"
python3 meeting_worker.py
```