#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meeting Upload Web — Render Flask App
瀏覽器錄音/上傳 → 存到本地磁碟 → Linux Worker 負責處理
"""

import os
import uuid
import json
import bcrypt
from datetime import datetime
from pathlib import Path

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

APP_PASSWORD_HASH = os.environ.get("APP_PASSWORD_HASH", "$2b$12$VaoszwYALGCG28kPHTiDquhX3MgIcPNsS3ufbEWgtb0vxB5w.sobu")  # bcrypt hash，不設定就無保護

# ==========================================
# 簡易密碼驗證（bcrypt）
# ==========================================

def check_password():
    """檢查 X-App-Password header，支援 bcrypt hash"""
    if not APP_PASSWORD_HASH:
        return True  # 沒設定密碼就放行
    pwd = request.headers.get("X-App-Password", "")
    return bcrypt.checkpw(pwd.encode(), APP_PASSWORD_HASH.encode())

def require_password():
    """若密碼錯誤回傳 401"""
    if not check_password():
        return jsonify({"error": "需要密碼"}), 401

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
  .record-btn:disabled { background: #555; cursor: not-allowed; transform: none; }

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
  .progress-bar { width: 100%; max-width: 360px; height: 6px; background: #333; border-radius: 3px; margin-top: 16px; display: none; }
  .progress-bar .fill { height: 100%; background: #4fc3f7; border-radius: 3px; width: 0%; transition: width 0.3s; }

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

<!-- 密碼鎖 -->
<div id="lockScreen">
  <h2>🔒 輸入密碼</h2>
  <input type="password" id="pwdInput" placeholder="密碼" onkeydown="if(event.key==='Enter')tryLogin()">
  <button onclick="tryLogin()">進入</button>
  <p id="lockError"></p>
</div>

<div id="appContent" style="display:none;"></div>

<script>
let APP_PASSWORD = '';
let UNLOCKED = false;

function tryLogin() {
  const pwd = document.getElementById('pwdInput').value;
  if (!pwd) return;
  fetch('/api/jobs?limit=1', {
    headers: { 'X-App-Password': pwd }
  }).then(r => {
    if (r.ok) {
      APP_PASSWORD = pwd;
      localStorage.setItem('app_pwd', pwd);
      UNLOCKED = true;
      document.getElementById('lockScreen').style.display = 'none';
      initApp();
    } else {
      document.getElementById('lockError').textContent = '密碼錯誤';
    }
  }).catch(() => {
    document.getElementById('lockError').textContent = '連線失敗';
  });
}

function initApp() {
  // 啟動輪詢等
  loadHistory();
  setInterval(loadHistory, 15000);
}

const savedPwd = localStorage.getItem('app_pwd');
if (savedPwd) {
  document.getElementById('pwdInput').value = savedPwd;
  // 先驗證存過的密碼
  fetch('/api/jobs?limit=1', { headers: { 'X-App-Password': savedPwd } })
    .then(r => { if (r.ok) { APP_PASSWORD = savedPwd; UNLOCKED = true; document.getElementById('lockScreen').style.display = 'none'; initApp(); } });
}
</script>

<script>
const _fetch = window.fetch;
window.fetch = function(url, opts) {
  if (APP_PASSWORD && url.startsWith('/api/')) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers['X-App-Password'] = APP_PASSWORD;
  }
  return _fetch.apply(this, arguments);
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
    </div>

    <!-- 狀態卡片 -->
    <div style="text-align:center; margin-top: 32px;" id="statusCard" class="status-card" style="display:none;">
      <h3>上傳成功</h3>
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
        <h3>上傳成功</h3>
        <div class="status" id="jobStatusD">處理中...</div>
        <div class="time" id="jobTimeD"></div>
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
let mediaRecorder = null;
let audioChunks = [];
let startTime = null;
let timerInterval = null;
let isRecording = false;

async function toggleRecording() {
  const btn = document.getElementById('recBtn') || document.getElementById('recBtnD');
  const timer = document.getElementById('timer') || document.getElementById('timerD');
  const hint = document.getElementById('recHint') || document.getElementById('recHintD');

  if (!isRecording) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      audioChunks = [];

      mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
      mediaRecorder.onstop = async () => {
        const blob = new Blob(audioChunks, { type: 'audio/webm' });
        stream.getTracks().forEach(t => t.stop());
        await uploadAudio(blob, 'recorded.webm');
      };

      mediaRecorder.start();
      isRecording = true;
      btn.classList.add('recording');
      btn.textContent = '⏹ 停止';
      timer.classList.add('recording');
      hint.textContent = '錄音中...';
      startTime = Date.now();

      timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
        const s = String(elapsed % 60).padStart(2, '0');
        timer.textContent = `${m}:${s}`;
      }, 1000);

      // 視覺化波形
      setupWaveform(stream);
    } catch (e) {
      alert('無法取得麥克風權限：' + e.message);
    }
  } else {
    mediaRecorder.stop();
    isRecording = false;
    btn.classList.remove('recording');
    btn.textContent = '🎤 開始錄音';
    timer.classList.remove('recording');
    hint.textContent = '處理中...';
    clearInterval(timerInterval);
  }
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
  const formData = new FormData();
  formData.append('audio', blob, originalName);
  formData.append('original_name', originalName);

  const pbId = 'progressBar' + (window.innerWidth > 640 ? 'D' : '');
  const fillId = 'progressFill' + (window.innerWidth > 640 ? 'D' : '');

  document.getElementById(pbId).style.display = 'block';

  try {
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        document.getElementById(fillId).style.width = pct + '%';
      }
    };
    xhr.onload = () => {
      document.getElementById(pbId).style.display = 'none';
      document.getElementById(fillId).style.width = '0%';
      if (xhr.status === 200) {
        const job = JSON.parse(xhr.responseText);
        showStatus(job);
      } else {
        alert('上傳失敗：' + xhr.statusText);
      }
    };
    xhr.open('POST', '/api/jobs');
    xhr.send(formData);
  } catch (e) {
    alert('上傳失敗：' + e.message);
  }
}

async function handleFile(file) {
  if (!file) return;
  const pbId = 'progressBar' + (window.innerWidth > 640 ? 'D' : '');
  const fillId = 'progressFill' + (window.innerWidth > 640 ? 'D' : '');
  document.getElementById(pbId).style.display = 'block';

  const formData = new FormData();
  formData.append('audio', file);
  formData.append('original_name', file.name);

  const xhr = new XMLHttpRequest();
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      document.getElementById(fillId).style.width = pct + '%';
    }
  };
  xhr.onload = () => {
    document.getElementById(pbId).style.display = 'none';
    document.getElementById(fillId).style.width = '0%';
    if (xhr.status === 200) {
      const job = JSON.parse(xhr.responseText);
      showStatus(job);
    }
  };
  xhr.open('POST', '/api/jobs');
  xhr.send(formData);
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
// 狀態顯示
// ==========================================
function showStatus(job) {
  const cardId = window.innerWidth > 640 ? 'statusCardD' : 'statusCard';
  const statusId = window.innerWidth > 640 ? 'jobStatusD' : 'jobStatus';
  const timeId = window.innerWidth > 640 ? 'jobTimeD' : 'jobTime';
  const card = document.getElementById(cardId);
  card.style.display = 'block';
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

loadHistory();
setInterval(loadHistory, 15000);
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

@app.route("/api/jobs", methods=["POST"])
def api_create_job():
    """新建 job，接收音頻檔案"""
    if not check_password():
        return jsonify({"error": "需要密碼"}), 401
    if 'audio' not in request.files:
        return jsonify({"error": "no audio"}), 400

    audio = request.files['audio']
    ext = audio.filename.split('.')[-1] if '.' in audio.filename else 'webm'
    job_id = str(uuid.uuid4())[:8]
    saved_name = f"{job_id}.{ext}"
    save_path = UPLOAD_DIR / saved_name
    audio.save(save_path)

    job = create_job(saved_name, source="upload")
    job["original_name"] = request.form.get("original_name", audio.filename)
    # 立即更新 jobs.json
    jobs = load_jobs()
    for j in jobs:
        if j["id"] == job["id"]:
            j["original_name"] = job["original_name"]
            break
    save_jobs(jobs)

    return jsonify(job), 200

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
        status="completed",
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

@app.route("/api/jobs/<job_id>/reset", methods=["POST"])
def api_reset_job(job_id):
    """重置 job 為 pending，讓 worker 可以重新處理"""
    update_job(job_id, status="pending", error=None)
    return jsonify({"ok": True})

@app.route("/api/jobs/<job_id>/audio", methods=["GET"])
def api_download_audio(job_id):
    """下載音頻（Worker 用）"""
    if not check_password():
        return jsonify({"error": "需要密碼"}), 401
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