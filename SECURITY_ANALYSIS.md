# Meeting-Upload 系統資訊安全分析報告

**分析日期：** 2026-05-25  
**分析範圍：** `/home/eric/meeting-upload/`（Render Flask 前端）與 Worker 系統

---

## 一、目前的安全機制說明

### 1. 前端（Render Flask）認證機制

#### 認證流程（render_app/app.py）

系統採用兩套認證機制並存：

**新格式（多帳號）：**
- Header：`X-Username` + `X-App-Password`
- 帳號密碼 Hash 儲存於環境變數 `USERS_JSON`（JSON 格式）
- 密碼比對：`bcrypt.checkpw(password.encode(), user_hash.encode())`

**舊格式（單一帳號）：**
- Header：僅 `X-App-Password`（空白帳號）
- 密碼 Hash 儲存於環境變數 `APP_PASSWORD_HASH`
- 回溯相容新格式的單一帳號模式

#### 密碼驗證（bcrypt）

```python
# render_app/app.py 第 85 行
return bcrypt.checkpw(password.encode(), expected.encode())
```

- 使用 bcrypt 單向雜湊，不可逆
- 比較時採用 timing-safe 函式（`bcrypt.checkpw`）

#### 端點認證覆蓋

| 端點 | 需認證 | 說明 |
|------|--------|------|
| `GET /` | ✅ | 登入頁，密碼錯誤顯示鎖屏 |
| `POST /api/jobs` | ✅ | 上傳音檔 |
| `GET /api/jobs` | ✅ | 列出 jobs |
| `GET /api/jobs/<id>` | ✅ | 查詢單一 job |
| `POST /api/jobs/<id>/processing` | ✅ | Worker 開始處理 |
| `POST /api/jobs/<id>/complete` | ✅ | Worker 完成 |
| `POST /api/jobs/<id>/fail` | ✅ | Worker 失敗 |
| `GET /api/jobs/<id>/audio` | ❌ | **無認證**，Worker 下載音檔 |
| `GET /health` | ❌ | **無認證**，健康檢查 |

#### 密碼儲存於瀏覽器

前端將密碼以**明文**儲存於 `localStorage`：

```javascript
// render_app/app.py 第 315 行
localStorage.setItem('app_cred', JSON.stringify({u: username, p: pwd}))
```

舊版（app.py）同樣明文儲存：
```javascript
// app.py 第 245 行
localStorage.setItem('app_pwd', pwd);
```

---

### 2. Worker（Mac/Linux Polling）安全機制

#### Worker 存取 Render API

位於 `worker/meeting_worker.py`，每 120 秒 polling：

```python
# 第 22-31 行
RENDER_BASE_URL = os.environ.get("RENDER_BASE_URL", "...")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

def auth_headers():
    if APP_PASSWORD:
        return {"X-App-Password": APP_PASSWORD}
    return {}
```

攜帶認證資訊：`X-App-Password: <APP_PASSWORD>`

#### Worker SSH 存取 Mac

```python
# 第 34 行
MAC_HOST = "erichy_tsai@10.90.72.155"  # 硬編碼

# 第 61-70 行 SCP / 第 72-80 行 SSH
cmd = ["scp", "-o", "StrictHostKeyChecking=no", str(local_path), f"{MAC_HOST}:{remote_path}"]
```

SSH 使用密碼認證（**非金鑰**），密碼硬編碼於原始碼。

---

### 3. 跨機器通訊安全性

| 通訊路徑 | 認證方式 | 傳輸加密 |
|----------|----------|----------|
| 瀏覽器 → Render | X-App-Password（Header） | HTTPS（TLS） |
| Worker → Render | X-App-Password（Header） | HTTPS（TLS） |
| Worker → Mac | SSH 密碼 | SSH（已加密 channel） |
| Worker → Notion | Integration Token | HTTPS |

---

## 二、識別的風險點

### 🔴 嚴重（Critical）

#### R-1：明文密碼儲存於瀏覽器 localStorage

**說明：** 前端直接將帳號密碼以明文儲存於 localStorage

```javascript
// render_app/app.py
localStorage.setItem('app_cred', JSON.stringify({u: username, p: pwd}))
```

**風險：**
- XSS 攻擊可直接竊取帳密
- 本機共用電腦可讀取密碼
- 無論傳輸多安全，客戶端等同於明文

#### R-2：Worker 的 SSH 密碼硬編碼於原始碼

```python
# meeting_worker.py 第 34 行
MAC_HOST = "erichy_tsai@10.90.72.155"
```

**風險：**
- 原始碼泄漏（Git commit、備份）即直接暴露 SSH 密碼
- 無法輪換密碼（需要改 code + redeploy）
- 若密碼被竊，攻擊者可完全控制 Mac

#### R-3：音檔下載端點無認證保護

```python
# render_app/app.py 第 937 行
@app.route("/api/jobs/<job_id>/audio", methods=["GET"])
def api_download_audio(job_id):
    """下載音頻（Worker 用，不需要密碼驗證）"""
    # ... 無任何認證檢查
```

**風險：**
- 任誰知道 job_id 即可下載任何音檔
- 會議錄音外洩無需任何特殊權限

---

### 🟠 高（High）

#### R-4：SSH 使用密碼認證而非金鑰

```python
# meeting_worker.py 第 63-66 行
cmd = ["scp", "-o", "StrictHostKeyChecking=no", str(local_path), f"{MAC_HOST}:{remote_path}"]
```

**風險：**
- 密碼在網路傳輸（即使是 SSH channel，理論上仍有風險）
- 無法防範重放攻擊（若密碼被截獲）
- 建議改用 SSH 金鑰對

#### R-5：`StrictHostKeyChecking=no` 繞過主機驗證

```python
# meeting_worker.py 第 64、75、85 行
"scp", "-o", "StrictHostKeyChecking=no", ...
```

**風險：**
- 易受中間人（MITM）攻擊
- 攻擊者可偽造 Mac 伺服器截獲音檔

#### R-6：舊版密碼存在於程式碼（fallback）

```python
# render_app/app.py 第 89-93 行
if APP_PASSWORD_HASH:
    try:
        return bcrypt.checkpw(password.encode(), APP_PASSWORD_HASH.encode())
```

舊版 `APP_PASSWORD_HASH` 可能還在環境變數中，存在向下相容風險。

---

### 🟡 中（Medium）

#### R-7：密碼在 HTTP Header 傳輸（即使 HTTPS）

```javascript
// render_app/app.py 第 311 行
headers: { 'X-Username': username, 'X-App-Password': pwd }
```

**說明：** 即使使用 HTTPS，密碼仍在 Header 中傳輸，可能被 logs、proxies 記錄。

**建議：** 改用短期 token 或 OAuth 2.0。

#### R-8：無登入失敗鎖定機制

**說明：** 無論嘗試多少次，系統不回應任何 lockout。

**風險：** 暴力破解（Brute Force）可行。

#### R-9：無速率限制（Rate Limiting）

**說明：** `/api/jobs` 等端點無請求頻率限制。

**風險：** DoS 攻擊、自動化暴力破解。

#### R-10：密碼輪換困難

- SSH 密碼硬編碼，需要 code change + redeploy
- APP_PASSWORD 環境變數需重新設定所有 Worker

---

## 三、改進建議

### 短期（1-2 週）

| 編號 | 動作 | 說明 |
|------|------|------|
| S-1 | **移除明文密碼儲存** | 改用 Session Token 或 HttpOnly Cookie |
| S-2 | **強制音檔下載需認證** | 為 `/api/jobs/<id>/audio` 加入 Worker token 驗證 |
| S-3 | **更新 SSH 密碼** | 立即更換硬編碼的 SSH 密碼，改用環境變數 |

### 中期（1-2 個月）

| 編號 | 動作 | 說明 |
|------|------|------|
| M-1 | **採用 SSH 金鑰認證** | 移除密碼，改用 Ed25519/RSA 金鑰對 |
| M-2 | **加入 rate limiting** | 使用 Flask-Limiter 限制 API 請求頻率 |
| M-3 | **啟用 HTTPS only** | 確認 Render 強制 HTTPS，HSTS Header |
| M-4 | **加入登入失敗鎖定** | 5 次失敗鎖定 15 分鐘 |
| M-5 | **StrictHostKeyChecking 改為 ask** | 或事先登錄 known_hosts |

### 長期（3-6 個月）

| 編號 | 動作 | 說明 |
|------|------|------|
| L-1 | **改用 OAuth 2.0 / JWT** | 廢除靜態密碼，改用短期 access token |
| L-2 | **密碼管理機制** | 使用 Vault（如 HashiCorp Vault）集中管理密鑰 |
| L-3 | **Worker 使用專用 API Token** | 而非共享 APP_PASSWORD，增加可追溯性 |
| L-4 | **將音檔改用預簽名 URL** | S3/GCS 預簽名 URL，過期後不可存取 |
| L-5 | **安全稽核日誌** | 記錄所有認證事件、檔案存取 |

---

## 四、架構總結

```
┌─────────────┐     HTTPS + Header      ┌──────────────┐
│   瀏覽器    │ ── X-App-Password ──▶  │   Render     │
│  (localStorage存密碼) │               │   Flask      │
└─────────────┘                         │  /api/jobs   │
                                        │  /health ❌  │
                                        └──────┬───────┘
                                               │ poll (120s)
                                               ▼
                   HTTPS + X-App-Password     ┌──────────┐
                 ──────────────────────────▶  │  Worker  │
                                              └────┬─────┘
                                                   │ SSH + 密碼
                                                   ▼
                                              ┌──────────┐
                                              │   Mac    │
                                              │ (port 2222)
                                              └────┬─────┘
                                                   │ SCP/rsync
                                                   ▼
                                              ┌──────────┐
                                              │ 音檔儲存 │
                                              └──────────┘
```

---

## 五、關鍵原始碼位置

| 檔案 | 風險點 |
|------|--------|
| `render_app/app.py:315` | localStorage 明文儲存 |
| `render_app/app.py:937-946` | 音檔下載無認證 |
| `worker/meeting_worker.py:34` | SSH 主機/密碼硬編碼 |
| `worker/meeting_worker.py:64,75` | StrictHostKeyChecking=no |
| `app.py:245` | 舊版 localStorage 明文儲存 |
| `app.py:51-52` | 舊版密碼 fallback |

---

**報告生成：** 2026-05-25  by Hermes Agent