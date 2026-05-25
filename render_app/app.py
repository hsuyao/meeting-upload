#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meeting Upload Web — Render Flask App
瀏覽器錄音/上傳 → 存到本地磁碟 → Linux Worker 負責處理
"""

import os
import uuid
import json
import logging
import shutil
import bcrypt
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, render_template_string

app = Flask(__name__)

# ==========================================
# 設定
# ==========================================

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/app/uploads"))
try:
    UPLOAD_DIR.mkdir(exist_ok=True)
except FileNotFoundError:
    # Render free plan 可能沒有 /app，改用本地目錄
    UPLOAD_DIR = Path("./uploads")
    UPLOAD_DIR.mkdir(exist_ok=True)

JOBS_FILE = Path(os.environ.get("JOBS_FILE", "/app/jobs.json"))
try:
    if not JOBS_FILE.parent.exists():
        JOBS_FILE = Path("./jobs.json")
except FileNotFoundError:
    JOBS_FILE = Path("./jobs.json")

APP_PASSWORD_HASH = os.environ.get("APP_PASSWORD_HASH", "")

# ==========================================
# 密碼驗證（bcrypt）+ 裝飾器
# ==========================================

# 不需要密碼的路徑（純健康檢查）
APP_PASSWORD_HASH = os.environ.get("APP_PASSWORD_HASH", "")

# 多帳號系統：{ "eric": "bcrypt_hash", "bob": "bcrypt_hash" }
# 環境變數 USERS_JSON 是 JSON 字串
def load_users():
    """從環境變數載入帳號列表"""
    users_raw = os.environ.get("USERS_JSON", "")
    if not users_raw:
        # 找不到 USERS_JSON，回兼單一帳號舊格式
        if APP_PASSWORD_HASH:
            return {"": APP_PASSWORD_HASH}
        return {}
    import json
    try:
        return json.loads(users_raw)
    except Exception:
        return {}

USERS = load_users()

# 不需要密碼的路徑
PUBLIC_PATHS = {"/health", "/api/jobs", "/"}

def check_password():
    """檢查 X-App-Password header，比對 bcrypt hash"""
    if request.path in PUBLIC_PATHS:
        return True  # 公開路徑不需要密碼
    if not USERS:
        return True  # 沒設定帳號就放行
    # 支援兩種 header 格式：
    #  X-App-Password: password（舊格式，空白帳號）
    #  X-Username: user\nX-App-Password: password（新格式）
    username = request.headers.get("X-Username", "")
    password = request.headers.get("X-App-Password", "")
    # 舊格式：只有 X-App-Password，對空白帳號
    if not username and password:
        expected = USERS.get("", USERS.get("_default", ""))
        if expected:
            try:
                return bcrypt.checkpw(password.encode(), expected.encode())
            except Exception:
                pass
        # 向下兼容：直接把 password 當成單一密碼比對
        if APP_PASSWORD_HASH:
            try:
                return bcrypt.checkpw(password.encode(), APP_PASSWORD_HASH.encode())
            except Exception:
                pass
        return False
    # 新格式
    user_hash = USERS.get(username, "")
    if not user_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), user_hash.encode())
    except Exception:
        return False

def require_password(f):
    """裝飾器：若密碼錯誤，回傳 401 兼 JSON（讓前端知道要跳出登入頁）"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_password():
            return jsonify({"error": "需要密碼", "code": "auth_required"}), 401
        return f(*args, **kwargs)
    return decorated

@app.before_request
def enforce_password():
    """所有請求都要檢查密碼，401 时回 JSON（不是 HTML）"""
    if request.method == "OPTIONS":
        return  # 讓 CORS 通過
    if request.path in PUBLIC_PATHS:
        return  # 公開路徑
    if not check_password():
        resp = jsonify({"error": "需要密碼", "code": "auth_required"})
        resp.status_code = 401
        return resp

# ==========================================
# Job 管理
# ==========================================

def load_jobs():
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text())
        except Exception:
            return []
    return []

def save_jobs(jobs):
    JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))

def create_job(audio_filename: str, source: str = "upload") -> dict:
    """建立新 job，回傳 job dict"""
    job_id = str(uuid.uuid4())[:8]
    job = {
        "id": job_id,
        "filename": audio_filename,
        "source": source,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "notion_url": None,
        "error": None,
        "original_name": request.form.get("original_name", audio_filename),
    }
    jobs = load_jobs()
    jobs.append(job)
    save_jobs(jobs)
    return job

def update_job(job_id: str, **kwargs):
    """更新 job 狀態/欄位"""
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job.update(kwargs)
            break
    save_jobs(jobs)

# ==========================================
# HTML 介面
# ==========================================

INDEX_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>會議錄音</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0f0f0f; color: #fff; min-height: 100vh; }

  /* 密碼鎖 */
  #lockScreen { position: fixed; inset: 0; background: #0f0f0f; z-index: 999;
                display: flex; flex-direction: column; align-items: center;
                justify-content: center; }
  #lockScreen h2 { color: #4fc3f7; font-size: 20px; margin-bottom: 16px; }
  #lockScreen input { background: #1a1a1a; border: 1px solid #333; color: #fff;
                     padding: 12px 16px; font-size: 16px; border-radius: 8px;
                     width: 280px; text-align: center; }
  #lockScreen button { margin-top: 12px; background: #4fc3f7; border: none;
                      color: #000; padding: 10px 32px; font-size: 14px;
                      border-radius: 6px; cursor: pointer; }
  #lockScreen p { color: #f44336; font-size: 12px; margin-top: 8px; height: 16px; }

  /* 檢測：手機 vs 電腦 */
  @media (max-width: 640px) {
    .desktop-only { display: none !important; }
    .mobile-container { padding: 20px; }
    .record-btn {
      width: 180px; height: 180px; border-radius: 50%;
      font-size: 22px; margin: 60px auto;
    }
  }
  @media (min-width: 641px) {
    .mobile-only { display: none !important; }
    .desktop-container { display: flex; height: 100vh; }
    .panel-left { flex: 1; padding: 40px; display: flex; flex-direction: column;
                  align-items: center; justify-content: center; border-right: 1px solid #333; }
    .panel-right { flex: 1; padding: 40px; display: flex; flex-direction: column;
                   align-items: center; justify-content: center; }
    .record-btn { width: 160px; height: 160px; border-radius: 50%; font-size: 18px; }
    .drop-zone { min-height: 200px; }
  }

  .header { padding: 20px; text-align: center; border-bottom: 1px solid #333; }
  .header h1 { font-size: 20px; color: #4fc3f7; }
  .header p { font-size: 13px; color: #888; margin-top: 6px; }

  /* 錄音按鈕 */
  .record-btn {
    background: #e53935; border: none; color: #fff;
    cursor: pointer; transition: all 0.2s;
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; box-shadow: 0 4px 20px rgba(229,57,53,0.4);
  }
  .record-btn:hover { transform: scale(1.05); background: #f44336; }
  .record-btn.recording { background: #555; box-shadow: 0 0 0 8px rgba(229,57,53,0.3); animation: pulse 1s infinite; }
  .record-btn.recording:hover { transform: scale(1.05); background: #666; }
  .record-btn:disabled { background: #555; cursor: not-allowed; transform: none; }
  .record-btn.paused { background: #ff9800; animation: none; box-shadow: 0 4px 20px rgba(255,152,0,0.4); }

  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 8px rgba(229,57,53,0.3); }
    50% { box-shadow: 0 0 0 16px rgba(229,57,53,0.1); }
  }

  /* 波形 */
  #waveform { width: 100%; height: 60px; background: #1a1a1a; border-radius: 8px; margin-top: 16px; }

  /* 計時器 */
  .timer { font-size: 48px; font-weight: 200; color: #4fc3f7; margin-top: 20px; font-variant-numeric: tabular-nums; }
  .timer.recording { color: #e53935; }

  /* 檔案上傳 */
  .drop-zone { border: 2px dashed #444; border-radius: 12px; padding: 32px; text-align: center;
               width: 100%; max-width: 360px; transition: border-color 0.2s; cursor: pointer; }
  .drop-zone:hover, .drop-zone.dragover { border-color: #4fc3f7; }
  .drop-zone input[type=file] { display: none; }
  .drop-zone p { color: #888; font-size: 14px; margin-top: 10px; }
  .drop-zone .icon { font-size: 40px; }

  /* 上傳進度 */
  .progress-bar { width: 100%; max-width: 360px; height: 6px; background: #333; border-radius: 3px; margin-top: 16px; display: none; position: relative; }
  .progress-bar .fill { height: 100%; background: #4fc3f7; border-radius: 3px; width: 0%; transition: width 0.3s; }
  .progress-info { display: flex; align-items: center; justify-content: space-between; margin-top: 16px; width: 100%; max-width: 360px; }
  .progress-info .cancel-btn { background: #e53935; border: none; color: #fff; padding: 6px 16px; font-size: 12px; border-radius: 4px; cursor: pointer; display: none; }
  .progress-info .cancel-btn:hover { background: #f44336; }
  .progress-info .filename { font-size: 12px; color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .progress-info .pct { font-size: 12px; color: #4fc3f7; margin-left: 8px; }

  /* 狀態卡片 */
  .status-card { background: #1a1a1a; border-radius: 12px; padding: 20px; margin-top: 24px; width: 100%; max-width: 360px; text-align: center; }
  .status-card h3 { font-size: 16px; margin-bottom: 8px; color: #4fc3f7; }
  .status-card .status { font-size: 28px; font-weight: bold; }
  .status-card .time { font-size: 12px; color: #888; margin-top: 6px; }
  .status-card.notion-link a { color: #4fc3f7; word-break: break-all; }

  /* 歷史列表 */
  .history { width: 100%; max-width: 600px; margin-top: 32px; }
  .history h2 { font-size: 14px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }
  .job-item { background: #1a1a1a; border-radius: 8px; padding: 14px 16px; margin-bottom: 8px;
              display: flex; align-items: center; gap: 12px; }
  .job-item .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .job-item .dot.pending { background: #ff9800; }
  .job-item .dot.processing { background: #2196f3; }
  .job-item .dot.completed { background: #4caf50; }
  .job-item .dot.failed { background: #f44336; }
  .job-item .info { flex: 1; min-width: 0; }
  .job-item .name { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .job-item .time { font-size: 12px; color: #888; }
  .job-item .link { font-size: 12px; color: #4fc3f7; text-decoration: none; }

  /* 提示 */
  .hint { color: #888; font-size: 12px; text-align: center; margin-top: 12px; }
</style>
</head>
<body>

<!-- 帳號登入 -->
<div id="lockScreen">
  <h2>🔒 會議錄音系統</h2>
  <input type="text" id="usernameInput" placeholder="帳號" onkeydown="if(event.key==='Tab'){event.preventDefault();document.getElementById('pwdInput').focus()}">
  <input type="password" id="pwdInput" placeholder="密碼" onkeydown="if(event.key==='Enter')tryLogin()">
  <button onclick="tryLogin()">進入</button>
  <p id="lockError"></p>
</div>

<div id="appContent" style="display:none;"></div>

<script>
let APP_USERNAME = '';
let APP_PASSWORD = '';
let UNLOCKED = false;

function tryLogin() {
  const username = document.getElementById('usernameInput').value.trim();
  const pwd = document.getElementById('pwdInput').value;
  if (!username || !pwd) return;
  fetch('/api/jobs?limit=1', {
    headers: { 'X-Username': username, 'X-App-Password': pwd }
  }).then(r => {
    if (r.ok) {
      APP_USERNAME = username;
      APP_PASSWORD = pwd;
      localStorage.setItem('app_cred', JSON.stringify({u: username, p: pwd}));
      UNLOCKED = true;
      document.getElementById('lockScreen').style.display = 'none';
      initApp();
    } else {
      document.getElementById('lockError').textContent = '帳號或密碼錯誤';
    }
  }).catch(() => {
    document.getElementById('lockError').textContent = '連線失敗';
  });
}

// ==========================================
// 強制的密碼檢查：每次頁面可見或 API 401 都重新驗證
// ==========================================

function doLock() {
  APP_USERNAME = '';
  APP_PASSWORD = '';
  UNLOCKED = false;
  localStorage.removeItem('app_cred');
  document.getElementById('lockScreen').style.display = 'flex';
  document.getElementById('appContent').style.display = 'none';
  document.getElementById('pwdInput').value = '';
  document.getElementById('lockError').textContent = '請重新登入';
}

function verifyPassword(username, pwd) {
  return fetch('/api/jobs?limit=1', { headers: { 'X-Username': username, 'X-App-Password': pwd } })
    .then(r => r.ok);
}

// 每次 fetch 回來 401 都重新鎖屏
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !UNLOCKED) {
    document.getElementById('lockScreen').style.display = 'flex';
  }
  if (document.visibilityState === 'visible' && APP_PASSWORD) {
    verifyPassword(APP_USERNAME, APP_PASSWORD).then(ok => {
      if (!ok) doLock();
    });
  }
});

// 載入時若 localStorage 有帳密，先驗證再解鎖
const saved = localStorage.getItem('app_cred');
if (saved) {
  try {
    const {u, p} = JSON.parse(saved);
    document.getElementById('usernameInput').value = u;
    document.getElementById('pwdInput').value = p;
    verifyPassword(u, p).then(ok => {
      if (ok) {
        APP_USERNAME = u;
        APP_PASSWORD = p;
        UNLOCKED = true;
        document.getElementById('lockScreen').style.display = 'none';
        initApp();
      } else {
        localStorage.removeItem('app_cred');
        document.getElementById('pwdInput').value = '';
        document.getElementById('lockError').textContent = '請重新登入';
      }
    });
  } catch(e) {
    localStorage.removeItem('app_cred');
  }
}
</script>

<!-- 自動登入（略過鎖屏） -->
<script>
const _fetch = window.fetch;
window.fetch = function(url, opts = {}) {
  if (APP_PASSWORD && url.startsWith('/api/')) {
    opts.headers = Object.assign({}, opts.headers || {}, {
      'X-Username': APP_USERNAME,
      'X-App-Password': APP_PASSWORD
    });
  }
  return _fetch.call(this, url, opts);
};
</script>

<div class="mobile-only">
  <div class="mobile-container">
    <div class="header">
      <h1>🎙️ 會議錄音</h1>
      <p>隨時錄製，自動轉文字</p>
    </div>

    <!-- 錄音區 -->
    <div style="text-align:center; margin-top: 32px;">
      <button class="record-btn" id="recBtn" onclick="toggleRecording()">🎤 開始錄音</button>
      <button class="record-btn" id="stopBtn" onclick="stopRecording()" style="display:none; background:#555; margin-top:12px;">⏹ 結束錄音</button>
      <div class="timer" id="timer">00:00</div>
      <canvas id="waveform"></canvas>
      <p class="hint" id="recHint">點擊開始錄音</p>
    </div>

    <!-- 上傳區 -->
    <div style="text-align:center; margin-top: 40px;">
      <div class="drop-zone" onclick="document.getElementById('fileInput').click()" id="dropZone">
        <div class="icon">📁</div>
        <p>拖放音檔或點擊上傳</p>
        <p style="font-size:11px; color:#666; margin-top:4px;">MP3, M4A, WAV, OGG</p>
      </div>
      <input type="file" id="fileInput" accept=".mp3,.m4a,.wav,.ogg,audio/*" onchange="handleFile(this.files[0])">
      <div class="progress-bar" id="progressBar"><div class="fill" id="progressFill"></div></div>
      <div class="progress-info" id="progressInfo">
        <span class="filename" id="progressFile"></span>
        <span class="pct" id="progressPct"></span>
        <button class="cancel-btn" id="cancelBtn" onclick="cancelUpload()">取消</button>
      </div>
    </div>

    <!-- 狀態卡片 -->
    <div style="text-align:center; margin-top: 32px;" id="statusCard" class="status-card" style="display:none;">
      <h3 id="statusTitle">等待上傳...</h3>
      <div class="status" id="jobStatus">處理中...</div>
      <div class="time" id="jobTime"></div>
    </div>

    <!-- 歷史 -->
    <div class="history" style="margin: 32px auto;">
      <h2>最近錄製</h2>
      <div id="historyList"></div>
    </div>
  </div>
</div>

<div class="desktop-only">
  <div class="desktop-container">
    <div class="panel-left">
      <h2 style="color:#4fc3f7; font-weight:300; font-size:24px;">🎤 錄音</h2>
      <button class="record-btn" id="recBtnD" onclick="toggleRecording()">🎤 開始錄音</button>
      <button class="record-btn" id="stopBtnD" onclick="stopRecording()" style="display:none; background:#555; margin-top:12px;">⏹ 結束錄音</button>
      <div class="timer" id="timerD">00:00</div>
      <canvas id="waveformD"></canvas>
      <p class="hint" id="recHintD">點擊開始錄音</p>
    </div>
    <div class="panel-right">
      <h2 style="color:#4fc3f7; font-weight:300; font-size:24px;">📁 上傳音檔</h2>
      <div class="drop-zone" onclick="document.getElementById('fileInputD').click()" id="dropZoneD">
        <div class="icon">📁</div>
        <p>拖放音檔或點擊上傳</p>
        <p style="font-size:11px; color:#666; margin-top:4px;">MP3, M4A, WAV, OGG</p>
      </div>
      <input type="file" id="fileInputD" accept=".mp3,.m4a,.wav,.ogg,audio/*" onchange="handleFile(this.files[0])">
      <div class="progress-bar" id="progressBarD"><div class="fill" id="progressFillD"></div></div>
      <div id="statusCardD" class="status-card" style="display:none;">
        <h3 id="statusTitleD">等待上傳...</h3>
        <div class="status" id="jobStatusD">處理中...</div>
        <div class="time" id="jobTimeD"></div>
      </div>
      <div class="progress-info" id="progressInfoD">
        <span class="filename" id="progressFileD"></span>
        <span class="pct" id="progressPctD"></span>
        <button class="cancel-btn" id="cancelBtnD" onclick="cancelUpload()">取消</button>
      </div>
    </div>
  </div>

  <div id="speakerNamingModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.8); z-index:1000; align-items:center; justify-content:center;">
  <div style="background:#1a1a1a; border:1px solid #333; border-radius:16px; padding:32px; max-width:480px; width:90%; color:#e0e0e0;">
    <h2 style="margin:0 0 8px; color:#4fc3f7; font-weight:300; font-size:22px;">🎙️ 確認發言者</h2>
    <p id="speakerNamingHint" style="color:#888; margin:0 0 24px; font-size:14px;">請為各發言者命名</p>
    <div id="speakerNamingFields" style="margin-bottom:24px;"></div>
    <div style="display:flex; gap:12px; justify-content:flex-end;">
      <button onclick="closeSpeakerNamingModal()" style="background:#444; color:#ccc; border:none; padding:10px 20px; border-radius:8px; cursor:pointer;">取消</button>
      <button onclick="submitSpeakerNaming()" style="background:#4fc3f7; color:#000; border:none; padding:10px 20px; border-radius:8px; cursor:pointer; font-weight:600;">確認</button>
    </div>
  </div>
</div>

  <div style="position:fixed; bottom:20px; right:20px; width:280px;" id="historyPanel">
    <h2 style="font-size:12px; color:#666; text-transform:uppercase; margin-bottom:8px;">最近錄製</h2>
    <div id="historyListD"></div>
  </div>
</div>

<script>
// ==========================================
// 錄音
// ==========================================
let isRecording = false;
let isPaused = false;
let mediaRecorder = null;
let audioChunks = [];
let startTime = null;
let timerInterval = null;
let pausedElapsed = 0; // 總暫停時間（毫秒）
let pauseStartTime = null;

async function toggleRecording() {
  const suffix = window.innerWidth > 640 ? 'D' : '';
  const btn = document.getElementById('recBtn' + suffix);
  const timer = document.getElementById('timer' + suffix);
  const hint = document.getElementById('recHint' + suffix);

  if (!isRecording) {
    // 開始錄音
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      audioChunks = [];

      mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
      mediaRecorder.onstop = async () => {
        const blob = new Blob(audioChunks, { type: 'audio/webm' });
        stream.getTracks().forEach(t => t.stop());
        const totalMs = Date.now() - startTime - pausedElapsed;
        const duration = Math.floor(totalMs / 1000);
        if (duration < 60) {
          const msg = '錄音太短（' + duration + '秒），請至少錄製 1 分鐘';
          alert(msg);
          if (hint) hint.textContent = '錄音太短';
          return;
        }
        await uploadAudio(blob, 'recorded.webm');
      };

      mediaRecorder.start();
      isRecording = true;
      isPaused = false;
      pausedElapsed = 0;
      startTime = Date.now();
      btn.classList.add('recording');
      btn.textContent = '⏸ 暫停';
      document.getElementById('stopBtn' + suffix).style.display = 'inline-block';
      timer.classList.add('recording');
      if (hint) hint.textContent = '錄音中...';

      timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime - pausedElapsed) / 1000);
        const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
        const s = String(elapsed % 60).padStart(2, '0');
        timer.textContent = `${m}:${s}`;
      }, 1000);

      setupWaveform(stream);
    } catch (e) {
      alert('無法取得麥克風權限：' + e.message);
    }
  } else if (!isPaused) {
    // 暫停
    mediaRecorder.pause();
    isPaused = true;
    pauseStartTime = Date.now();
    btn.textContent = '▶ 繼續';
    btn.classList.add('paused');
    if (hint) hint.textContent = '已暫停';
    timer.classList.remove('recording');
    clearInterval(timerInterval);
  } else {
    // 繼續
    mediaRecorder.resume();
    pausedElapsed += Date.now() - pauseStartTime;
    isPaused = false;
    btn.textContent = '⏸ 暫停';
    btn.classList.remove('paused');
    if (hint) hint.textContent = '錄音中...';
    timer.classList.add('recording');

    timerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startTime - pausedElapsed) / 1000);
      const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const s = String(elapsed % 60).padStart(2, '0');
      timer.textContent = `${m}:${s}`;
    }, 1000);
  }
}

function stopRecording() {
  if (!isRecording) return;
  if (isPaused) {
    // 從暫停狀態直接停止
    pausedElapsed += Date.now() - pauseStartTime;
  }
  isRecording = false;
  isPaused = false;
  clearInterval(timerInterval);
  mediaRecorder.stop();

  const suffix = window.innerWidth > 640 ? 'D' : '';
  const btn = document.getElementById('recBtn' + suffix);
  btn.classList.remove('paused');
  const timer = document.getElementById('timer' + suffix);
  const hint = document.getElementById('recHint' + suffix);
  btn.classList.remove('recording');
  btn.textContent = '🎤 開始錄音';
  document.getElementById('stopBtn' + suffix).style.display = 'none';
  timer.classList.remove('recording');
  if (hint) hint.textContent = '處理中...';
}

function setupWaveform(stream) {
  const canvas = document.getElementById('waveform') || document.getElementById('waveformD');
  const ctx = canvas.getContext('2d');
  const audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  const source = audioCtx.createMediaStreamSource(stream);
  source.connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);

  function draw() {
    if (!isRecording) return;
    requestAnimationFrame(draw);
    analyser.getByteFrequencyData(data);
    ctx.fillStyle = '#1a1a1a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const barWidth = canvas.width / data.length;
    data.forEach((v, i) => {
      ctx.fillStyle = `hsl(${v * 1.5}, 80%, 60%)`;
      ctx.fillRect(barWidth * i, canvas.height - v, barWidth - 1, v);
    });
  }
  draw();
}

// ==========================================
// 上傳
// ==========================================
async function uploadAudio(blob, originalName) {
  const suffix = window.innerWidth > 640 ? 'D' : '';
  const pbId = 'progressBar' + suffix;
  const fillId = 'progressFill' + suffix;

  document.getElementById(pbId).style.display = 'block';
  document.getElementById(fillId).style.width = '0%';
  showProgressInfo(suffix, originalName);

  const formData = new FormData();
  formData.append('audio', blob, originalName);
  formData.append('original_name', originalName);

  const xhr = new XMLHttpRequest();
  currentXhr = xhr;

  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      document.getElementById(fillId).style.width = pct + '%';
      updateProgress(suffix, pct);
    }
  };
  xhr.onload = () => {
    resetUploadUI();
    if (xhr.status === 200) {
      const job = JSON.parse(xhr.responseText);
      currentJobId = job.id;
      showStatus(job);
    } else if (xhr.status === 401 || xhr.status === 403) {
      checkAndLock();
    } else if (xhr.status === 0) {
      // aborted
    } else {
      showUploadError('上傳失敗（' + xhr.status + '）');
    }
  };
  xhr.onerror = () => { resetUploadUI(); showUploadError('網路錯誤，請檢查連線'); };
  xhr.ontimeout = () => { resetUploadUI(); showUploadError('上傳逾時'); };
  xhr.open('POST', '/api/jobs');
  xhr.timeout = 120000;
  if (APP_PASSWORD) xhr.setRequestHeader('X-App-Password', APP_PASSWORD);
  xhr.send(formData);
}

let currentXhr = null;
let currentJobId = null;

function cancelUpload() {
  if (currentXhr) {
    currentXhr.abort();
    currentXhr = null;
    resetUploadUI();
  }
}

function resetUploadUI() {
  ['progressBar', 'progressBarD'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.style.display = 'none'; el.querySelector('.fill').style.width = '0%'; }
  });
  ['progressInfo', 'progressInfoD'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  ['cancelBtn', 'cancelBtnD'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  currentXhr = null;
}

function showProgressInfo(suffix, filename) {
  const infoId = 'progressInfo' + suffix;
  const fileId = 'progressFile' + suffix;
  const pctId = 'progressPct' + suffix;
  const cancelId = 'cancelBtn' + suffix;
  const el = document.getElementById(infoId);
  if (el) el.style.display = 'flex';
  const fileEl = document.getElementById(fileId);
  if (fileEl) fileEl.textContent = filename;
  const pctEl = document.getElementById(pctId);
  if (pctEl) pctEl.textContent = '0%';
  const cancelEl = document.getElementById(cancelId);
  if (cancelEl) cancelEl.style.display = 'inline-block';
}

function updateProgress(suffix, pct) {
  const pctEl = document.getElementById('progressPct' + suffix);
  if (pctEl) pctEl.textContent = pct + '%';
}

async function handleFile(file) {
  if (!file) return;
  const suffix = window.innerWidth > 640 ? 'D' : '';
  const pbId = 'progressBar' + suffix;
  const fillId = 'progressFill' + suffix;

  document.getElementById(pbId).style.display = 'block';
  document.getElementById(fillId).style.width = '0%';
  showProgressInfo(suffix, file.name);

  const formData = new FormData();
  formData.append('audio', file);
  formData.append('original_name', file.name);

  const xhr = new XMLHttpRequest();
  currentXhr = xhr;

  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      document.getElementById(fillId).style.width = pct + '%';
      updateProgress(suffix, pct);
    }
  };
  xhr.onload = () => {
    resetUploadUI();
    if (xhr.status === 200) {
      const job = JSON.parse(xhr.responseText);
      currentJobId = job.id;
      showStatus(job);
    } else if (xhr.status === 401 || xhr.status === 403) {
      checkAndLock();
    } else if (xhr.status === 0) {
      // aborted
    } else {
      showUploadError('上傳失敗（' + xhr.status + '）');
    }
  };
  xhr.onerror = () => {
    resetUploadUI();
    showUploadError('網路錯誤，請檢查連線');
  };
  xhr.ontimeout = () => {
    resetUploadUI();
    showUploadError('上傳逾時');
  };
  xhr.open('POST', '/api/jobs');
  if (APP_PASSWORD) xhr.setRequestHeader('X-App-Password', APP_PASSWORD);
  xhr.timeout = 120000;
  xhr.send(formData);
}

function showUploadError(msg) {
  const suffix = window.innerWidth > 640 ? 'D' : '';
  const cardId = 'statusCard' + suffix;
  const statusId = 'jobStatus' + suffix;
  const timeId = 'jobTime' + suffix;
  const titleId = 'statusTitle' + suffix;
  const card = document.getElementById(cardId);
  if (card) {
    card.style.display = 'block';
    const titleEl = document.getElementById(titleId);
    if (titleEl) titleEl.textContent = '上傳失敗';
    const el = document.getElementById(statusId);
    if (el) el.textContent = '❌ ' + msg;
    const timeEl = document.getElementById(timeId);
    if (timeEl) timeEl.textContent = '';
  }
}

// 拖放
['dropZone', 'dropZoneD'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('dragover'); });
  el.addEventListener('dragleave', () => el.classList.remove('dragover'));
  el.addEventListener('drop', e => {
    e.preventDefault();
    el.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
  });
});

// ==========================================
// Speaker Naming Modal
// ==========================================
let currentNamingJobId = null;

async function showSpeakerNamingModal(jobId, filename) {
  currentNamingJobId = jobId;
  const modal = document.getElementById('speakerNamingModal');
  const hint = document.getElementById('speakerNamingHint');
  const fields = document.getElementById('speakerNamingFields');

  hint.textContent = filename;
  fields.innerHTML = '<p style="color:#888; font-size:14px;">發言者命名載入中...</p>';
  modal.style.display = 'flex';

  // 抓 pending job 的 speakers 資訊
  try {
    const r = await fetch(`/api/jobs/${jobId}`);
    if (!r.ok) throw new Error('Failed to fetch job');
    const job = await r.json();

    // 嘗試從 Workers 端讀取 _speakers.json
    let speakers = null;
    try {
      const spec = await fetch(`/api/jobs/${jobId}/speakers`);
      if (spec.ok) speakers = await spec.json();
    } catch {}

    if (speakers && speakers.length) {
      fields.innerHTML = speakers.map(s => `
        <div style="margin-bottom:16px;">
          <label style="color:#4fc3f7; font-size:13px; display:block; margin-bottom:6px;">發言者 ${s.label}</label>
          <input type="text" id="spk_${s.label}" placeholder="例如：${s.label === 'A' ? '小明' : '阿輝'}" value="${s.label === 'A' ? '小明' : ''}" style="width:100%; background:#2a2a2a; border:1px solid #444; color:#e0e0e0; padding:10px 12px; border-radius:8px; box-sizing:border-box;">
        </div>
      `).join('');
    } else {
      // 抓到沒有 speaker 資料，給預設 A/B
      fields.innerHTML = `
        <div style="margin-bottom:16px;">
          <label style="color:#4fc3f7; font-size:13px; display:block; margin-bottom:6px;">發言者 A</label>
          <input type="text" id="spk_A" placeholder="例如：小明" value="" style="width:100%; background:#2a2a2a; border:1px solid #444; color:#e0e0e0; padding:10px 12px; border-radius:8px; box-sizing:border-box;">
        </div>
        <div style="margin-bottom:16px;">
          <label style="color:#4fc3f7; font-size:13px; display:block; margin-bottom:6px;">發言者 B</label>
          <input type="text" id="spk_B" placeholder="例如：阿輝" value="" style="width:100%; background:#2a2a2a; border:1px solid #444; color:#e0e0e0; padding:10px 12px; border-radius:8px; box-sizing:border-box;">
        </div>
      `;
    }
  } catch (e) {
    // 网络错误，用默认字段
    fields.innerHTML = `
      <div style="margin-bottom:16px;">
        <label style="color:#4fc3f7; font-size:13px; display:block; margin-bottom:6px;">發言者 A</label>
        <input type="text" id="spk_A" placeholder="例如：小明" value="" style="width:100%; background:#2a2a2a; border:1px solid #444; color:#e0e0e0; padding:10px 12px; border-radius:8px; box-sizing:border-box;">
      </div>
      <div style="margin-bottom:16px;">
        <label style="color:#4fc3f7; font-size:13px; display:block; margin-bottom:6px;">發言者 B</label>
        <input type="text" id="spk_B" placeholder="例如：阿輝" value="" style="width:100%; background:#2a2a2a; border:1px solid #444; color:#e0e0e0; padding:10px 12px; border-radius:8px; box-sizing:border-box;">
      </div>
    `;
  }
}

function closeSpeakerNamingModal() {
  document.getElementById('speakerNamingModal').style.display = 'none';
  currentNamingJobId = null;
}

async function submitSpeakerNaming() {
  const inputs = document.querySelectorAll('#speakerNamingFields input');
  const assignments = [];
  inputs.forEach(inp => {
    const label = inp.id.replace('spk_', '');
    if (inp.value.trim()) assignments.push(`${label}=${inp.value.trim()}`);
  });
  if (!assignments.length) { alert('請至少填入一個名字'); return; }

  const jobId = currentNamingJobId; // capture before closing
  closeSpeakerNamingModal();

  // 送到 Hermes 處理（在本頁 call API 由後端轉發）
  try {
    await fetch(`/api/jobs/${jobId}/speakers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assignments: assignments.join(' ') }),
    });
    // 重新 polling，會在 completed 時顯示 Notion 連結
    pollStatus(jobId);
  } catch (e) {
    alert('送出失敗，請重試');
  }
}

// ==========================================
// 狀態顯示
// ==========================================
function showStatus(job) {
  const suffix = window.innerWidth > 640 ? 'D' : '';
  const cardId = 'statusCard' + suffix;
  const statusId = 'jobStatus' + suffix;
  const timeId = 'jobTime' + suffix;
  const titleId = 'statusTitle' + suffix;
  const card = document.getElementById(cardId);
  if (card) card.style.display = 'block';
  const titleEl = document.getElementById(titleId);
  if (titleEl) titleEl.textContent = '上傳成功';
  document.getElementById(statusId).textContent = '處理中...';
  document.getElementById(timeId).textContent = '';
  currentJobId = job.id;
  pollStatus(job.id);
}

async function pollStatus(jobId) {
  const cardId = window.innerWidth > 640 ? 'statusCardD' : 'statusCard';
  const statusId = window.innerWidth > 640 ? 'jobStatusD' : 'jobStatus';
  const timeId = window.innerWidth > 640 ? 'jobTimeD' : 'jobTime';

  async function tick() {
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      if (!r.ok) return;
      const job = await r.json();
      const el = document.getElementById(statusId);
      if (job.status === 'pending') el.textContent = '⏳ 等待處理';
      else if (job.status === 'processing') el.textContent = '🔄 轉換中';
      else if (job.status === 'completed') {
        el.textContent = '✅ 完成';
        const timeEl = document.getElementById(timeId);
        timeEl.innerHTML = job.notion_url
          ? `<a href="${job.notion_url}" target="_blank" class="link">📓 打開會議記錄</a>`
          : '完成';
        return;
      } else if (job.status === 'awaiting_speaker_naming') {
        el.textContent = '🎙️ 請確認發言者';
        const timeEl = document.getElementById(timeId);
        timeEl.innerHTML = `<span style="color:#aaa; font-size:13px;">語音辨識完成，請</span> <button onclick="showSpeakerNamingModal('${job.id}', '${(job.original_name || job.filename).replace(/'/g, "\\'")}')" style="background:#4fc3f7; border:none; color:#111; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:600;">✏️ 設定發言者</button>`;
        return;
      } else if (job.status === 'failed') {
        el.textContent = '❌ 失敗';
        return;
      }
      setTimeout(tick, 3000);
    } catch (e) {}
  }
  tick();
}

// ==========================================
// 歷史
// ==========================================
async function loadHistory() {
  try {
    const r = await fetch('/api/jobs?limit=10');
    if (!r.ok) return;
    const jobs = await r.json();
    const lists = [document.getElementById('historyList'), document.getElementById('historyListD')];
    lists.forEach(list => {
      if (!list) return;
      list.innerHTML = jobs.map(job => `
        <div class="job-item">
          <div class="dot ${job.status}"></div>
          <div class="info">
            <div class="name">${job.original_name || job.filename}</div>
            <div class="time">${new Date(job.created_at).toLocaleString('zh-TW')}</div>
          </div>
          ${job.notion_url ? `<a href="${job.notion_url}" class="link" target="_blank">📓</a>` : ''}
        </div>
      `).join('');
    });
  } catch (e) {}
}

function initApp() {
  loadHistory();
  setInterval(loadHistory, 15000);
}
</script>
</div><!-- /appContent -->
</body>
</html>
"""

# ==========================================
# 路由
# ==========================================

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

import logging, time
logger = logging.getLogger(__name__)

@app.route("/api/jobs", methods=["POST"])
def api_create_job():
    """新建 job，接收音頻檔案"""
    try:
        t0 = time.time()
        logger.info(f"[UPLOAD] request started")
        logger.info(f"[UPLOAD] headers: {dict(request.headers)}")
        if not check_password():
            return jsonify({"error": "需要密碼"}), 401
        if 'audio' not in request.files:
            return jsonify({"error": "no audio"}), 400

        logger.info(f"[UPLOAD] auth passed")
        audio = request.files['audio']
        ext = audio.filename.split('.')[-1] if '.' in audio.filename else 'webm'
        job_id = str(uuid.uuid4())[:8]
        saved_name = f"{job_id}.{ext}"
        save_path = UPLOAD_DIR / saved_name
        tmp_path = save_path.with_suffix('.tmp')
        logger.info(f"[UPLOAD] filename={saved_name}, size_hint={audio.content_length}")
        try:
            audio.stream.seek(0)
            file_data = audio.stream.read()
            with open(tmp_path, 'wb', buffering=131072) as f:
                f.write(file_data)
            shutil.move(str(tmp_path), str(save_path))
            logger.info(f"[UPLOAD] write done, moved ({len(file_data)} bytes)")
        except Exception as e:
            import traceback
            logger.error(f"[UPLOAD] write failed: {e}\n{traceback.format_exc()}")
            return jsonify({"error": "上傳失敗", "detail": str(type(e).__name__)}), 500
        logger.info(f"[UPLOAD] write complete ({(time.time()-t0)*1000:.0f}ms total)")

        try:
            job = create_job(saved_name, source="upload")
            job["original_name"] = request.form.get("original_name", audio.filename)
            jobs = load_jobs()
            for j in jobs:
                if j["id"] == job["id"]:
                    j["original_name"] = job["original_name"]
                    break
            save_jobs(jobs)
        except Exception as e:
            import traceback
            logger.error(f"[UPLOAD] job/create/save failed: {e}\n{traceback.format_exc()}")
            try:
                if save_path.exists():
                    save_path.unlink()
            except:
                pass
            return jsonify({"error": "上傳失敗", "detail": str(type(e).__name__)}), 500

        return jsonify(job), 200
    except Exception as e:
        import traceback
        logger.error(f"[UPLOAD] top-level exception: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "上傳失敗", "detail": str(type(e).__name__)}), 500

@app.route("/api/jobs", methods=["GET"])
def api_list_jobs():
    """列出 jobs（供 Worker 和前端輪詢）"""
    jobs = load_jobs()
    limit = request.args.get("limit", 50, type=int)
    status = request.args.get("status", None)
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    return jsonify(jobs[-limit:])

@app.route("/api/jobs/<job_id>", methods=["GET"])
def api_get_job(job_id):
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)

@app.route("/api/jobs/<job_id>/processing", methods=["POST"])
def api_processing_job(job_id):
    """Worker 開始處理，標記為 processing"""
    update_job(job_id, status="processing")
    return jsonify({"ok": True})

@app.route("/api/jobs/<job_id>/complete", methods=["POST"])
def api_complete_job(job_id):
    """Worker 完成後呼叫，標記完成"""
    data = request.get_json() or {}
    update_job(job_id,
        status=data.get("status", "completed"),
        completed_at=datetime.now().isoformat(),
        notion_url=data.get("notion_url"),
        error=data.get("error")
    )
    return jsonify({"ok": True})

@app.route("/api/jobs/<job_id>/fail", methods=["POST"])
def api_fail_job(job_id):
    data = request.get_json() or {}
    update_job(job_id, status="failed", error=data.get("error"))
    return jsonify({"ok": True})

@app.route("/api/jobs/<job_id>/speakers", methods=["GET"])
def api_get_job_speakers(job_id):
    """Hermes 讀取 job 的 speaker 資訊（for pending_speaker_naming 讀取）"""
    # Render 上的 pending speaker naming 檔案目前存在 Worker 端
    # 所以這個 endpoint 代理到 Worker 的檔案（如果有）
    # 但實際上 Hermes 有自己的 pending_speaker_naming/ 目錄，
    # 當 Worker 完成後会寫入 Hermes 本機，
    # 所以 Hermes 直接讀本機的 pending_speaker_naming/ 即可。
    # 這裡只回傳 job 本身資訊，讓 Hermes 自己對照
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)

@app.route("/api/jobs/<job_id>/speakers", methods=["POST"])
def api_submit_job_speakers(job_id):
    """用戶在網頁 Modal 填完 speaker 名字後 POST 回來"""
    data = request.get_json() or {}
    assignments = data.get("assignments", "")  # "A=小明 B=阿輝"

    # 更新 job 狀態，並把 assignments 寫入 job 資料
    jobs = load_jobs()
    idx = next((i for i, j in enumerate(jobs) if j["id"] == job_id), None)
    if idx is None:
        return jsonify({"error": "not found"}), 404

    jobs[idx]["speaker_assignments"] = assignments
    jobs[idx]["status"] = "speaker_naming_submitted"
    save_jobs(jobs)

    return jsonify({"ok": True, "assignments": assignments})

@app.route("/api/jobs/<job_id>/reset", methods=["POST"])
def api_reset_job(job_id):
    """重置 job 為 pending，讓 worker 可以重新處理"""
    update_job(job_id, status="pending", error=None)
    return jsonify({"ok": True})

@app.route("/api/jobs/<job_id>/audio", methods=["GET"])
def api_download_audio(job_id):
    """下載音頻（Worker 用，不需要密碼驗證）"""
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "not found"}), 404
    audio_path = UPLOAD_DIR / job["filename"]
    if not audio_path.exists():
        return jsonify({"error": "file not found"}), 404
    return send_from_directory(str(UPLOAD_DIR), job["filename"], as_attachment=True)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "jobs": len(load_jobs())})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)