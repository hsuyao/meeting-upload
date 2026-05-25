#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meeting Worker — 定時 polling Render API，下載音頻 → Mac 處理 → Notion
"""

import os
import sys
import json
import time
import subprocess
import shutil
import requests
import re
from pathlib import Path
from datetime import datetime

# ==========================================
# 設定
# ==========================================

# 將 Hermes scripts 目錄加入路徑（給 speaker_learning_only, post_srt_to_notion 等用）
SCRIPTS_DIR = Path("/home/eric/.hermes/profiles/meeting-note/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))

RENDER_BASE_URL = os.environ.get("RENDER_BASE_URL", "https://meeting-upload-xxx.onrender.com")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
POLL_INTERVAL = 120  # 秒
TEMP_DIR = Path("/home/eric/meeting-upload/worker/tmp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

def auth_headers():
    if APP_PASSWORD:
        return {"X-App-Password": APP_PASSWORD}
    return {}

# Mac SSH 設定（從既有設定）
MAC_HOST = "erichy_tsai@10.90.72.155"
MAC_INPUT_DIR = "/Users/erichy_tsai/whisperx/input"
MAC_OUTPUT_DIR = "/Users/erichy_tsai/whisperx/output"
MAC_INTEGRATED_SCRIPT = "/Users/erichy_tsai/whisperx/integrated_pipeline.py"

# 本地目錄
SRT_OUTPUT_DIR = Path("/home/eric/.hermes/profiles/meeting-note/srt_output")
MEETING_NOTE_DIR = Path("/home/eric/.hermes/profiles/meeting-note/meeting_notes")

# 發送 Telegram 通知（用既有 Bot）
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_HOME_CHANNEL", "7602397392")

# ==========================================
# 工具
# ==========================================

def send_telegram(text: str):
    """發送 Telegram 通知"""
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
    except Exception as e:
        print(f"[telegram] 發送失敗: {e}")

def scp_upload(local_path: Path, remote_path: str):
    """SCP 上傳到 Mac"""
    cmd = [
        "scp", "-o", "StrictHostKeyChecking=no",
        str(local_path), f"{MAC_HOST}:{remote_path}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"SCP 上傳失敗: {result.stderr}")
    return True

def ssh_execute(command: str) -> str:
    """SSH 遠端執行"""
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", MAC_HOST, command],
        capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH 執行失敗: {result.stderr}")
    return result.stdout

def rsync_upload(local_path: Path, remote_path: str):
    """rsync 大檔上傳（優先）"""
    cmd = [
        "rsync", "-e", "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20",
        "--timeout=300", str(local_path), f"{MAC_HOST}:{remote_path}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=330)
    if result.returncode != 0:
        return scp_upload(local_path, remote_path)
    return True

# ==========================================
# Render API
# ==========================================

def get_pending_jobs() -> list:
    try:
        r = requests.get(f"{RENDER_BASE_URL}/api/jobs?status=pending",
                         headers=auth_headers(), timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[api] 取得 pending jobs 失敗: {e}")
    return []

def get_job(job_id: str) -> dict:
    try:
        r = requests.get(f"{RENDER_BASE_URL}/api/jobs/{job_id}",
                         headers=auth_headers(), timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[api] 取得 job {job_id} 失敗: {e}")
    return {}

def mark_processing(job_id: str):
    try:
        requests.post(f"{RENDER_BASE_URL}/api/jobs/{job_id}/processing",
                      headers=auth_headers(),
                      json={"status": "processing"}, timeout=10)
    except:
        pass

def mark_complete(job_id: str, notion_url: str = "", error: str = "", status: str = "completed"):
    try:
        requests.post(
            f"{RENDER_BASE_URL}/api/jobs/{job_id}/complete",
            headers=auth_headers(),
            json={"notion_url": notion_url, "error": error, "status": status},
            timeout=10
        )
    except Exception as e:
        print(f"[api] 標記完成失敗: {e}")

def mark_fail(job_id: str, error: str):
    try:
        requests.post(f"{RENDER_BASE_URL}/api/jobs/{job_id}/fail",
                      headers=auth_headers(),
                      json={"error": error}, timeout=10)
    except Exception as e:
        print(f"[api] 標記失敗失敗: {e}")

def download_audio(job_id: str, filename: str) -> Path:
    url = f"{RENDER_BASE_URL}/api/jobs/{job_id}/audio"
    local_path = TEMP_DIR / filename
    response = requests.get(url, headers=auth_headers(), timeout=120, stream=True)
    if response.status_code != 200:
        raise RuntimeError(f"下載失敗: HTTP {response.status_code}")
    with open(local_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return local_path

# ==========================================
# Mac 處理
# ==========================================

def process_on_mac(audio_path: Path, job_id: str) -> Path:
    filename = audio_path.name
    mac_input_path = f"/tmp/{job_id}_{filename}"
    mac_output_srt = f"/Users/erichy_tsai/whisperx/output/{job_id}.srt"

    # Step 1: 上傳到 Mac
    print(f"[worker] 上傳 {audio_path} → Mac {mac_input_path}")
    rsync_upload(audio_path, mac_input_path)

    # Step 2: 執行轉換
    print(f"[worker] 執行 mlx_whisper + pyannote...")
    ssh_cmd = (
        f"PYTHONPATH='' PATH=$HOME/homebrew/bin:$PATH "
        f"$HOME/whisperx/bin/python {MAC_INTEGRATED_SCRIPT} "
        f"--input {mac_input_path} "
        f"--output {mac_output_srt}"
    )
    print(f"[worker] SSH command: {ssh_cmd}")
    output = ssh_execute(ssh_cmd)
    print(f"[worker] Mac 輸出長度: {len(output)}")

    # Step 3: 下載 SRT + _speakers.json + WAV
    local_srt = SRT_OUTPUT_DIR / f"{job_id}.srt"
    local_speakers_json = SRT_OUTPUT_DIR / f"{job_id}_speakers.json"
    local_wav = SRT_OUTPUT_DIR / f"{job_id}_16k.wav"
    mac_speakers_json = mac_output_srt.replace('.srt', '_speakers.json')
    mac_wav = f"/tmp/{job_id}_{filename.replace('.', '_')}._16k.wav"

    for remote, local in [(mac_output_srt, local_srt), (mac_speakers_json, local_speakers_json)]:
        subprocess.run([
            "scp", "-o", "StrictHostKeyChecking=no",
            f"{MAC_HOST}:{remote}", str(local)
        ], capture_output=True, timeout=60)

    # 下載 WAV（用於 voice hash）
    subprocess.run([
        "scp", "-o", "StrictHostKeyChecking=no",
        f"{MAC_HOST}:{mac_wav}", str(local_wav)
    ], capture_output=True, timeout=60)

    if not local_srt.exists():
        raise RuntimeError(f"SRT 未生成: {mac_output_srt}")

    return local_srt, local_speakers_json, local_wav

# ==========================================
# 後處理：post_srt_to_notion.py
# ==========================================

def post_process(srt_path: Path, original_name: str, job_id: str, speakers_json_path: Path = None, wav_path: Path = None):
    script = Path("/home/eric/.hermes/profiles/meeting-note/scripts/post_srt_to_notion.py")
    cmd = [
        "python3", str(script),
        str(srt_path),
        original_name,
    ]
    if speakers_json_path:
        cmd.append(str(speakers_json_path))
    if wav_path:
        cmd.append(str(wav_path))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print(f"[post_srt] stdout: {result.stdout[:500]}")
    if result.returncode != 0:
        print(f"[post_srt] stderr: {result.stderr[:300]}")
        raise RuntimeError(f"post_srt_to_notion.py 失敗: {result.stderr}")

    notion_url = ""
    for line in result.stdout.split('\n'):
        urls = re.findall(r'https://www\.notion\.so/[^\s]+', line)
        if urls:
            notion_url = urls[0]
            break

    return notion_url

# ==========================================
# 主循環
# ==========================================

def poll_speaker_named_jobs():
    """輪詢等待 speaker naming 完成的 jobs（從 Render jobs API）"""
    import requests as req

    try:
        r = req.get(f"{RENDER_BASE_URL}/api/jobs?status=speaker_naming_submitted", timeout=10)
        if r.status_code != 200:
            return []
        return r.json()
    except Exception as e:
        print(f"[worker] 查詢 speaker_naming_submitted jobs 失敗: {e}")
        return []

def complete_with_speaker_naming(job_id: str, original_name: str, speaker_assignments: str):
    """處理 speaker naming 完成後的 Notion 上傳"""
    pending_file = Path(f"/home/eric/.hermes/profiles/meeting-note/pending_speaker_naming/{job_id}.json")

    srt_path = None
    if pending_file.exists():
        data = json.loads(pending_file.read_text())
        srt_path = Path(data["srt_path"])

    if not srt_path or not srt_path.exists():
        print(f"[worker] ❌ job {job_id}找不到 SRT，skip")
        return

    speakers_json = srt_path.parent / f"{srt_path.stem}_speakers.json"
    wav_path = srt_path.parent / f"{srt_path.stem}_16k.wav"

    # re-run speaker learning with assignments (更新 cache)
    if speakers_json.exists() and wav_path.exists():
        script_speaker = Path("/home/eric/.hermes/profiles/meeting-note/scripts/speaker_learning_only.py")
        if script_speaker.exists():
            subprocess.run(["python3", str(script_speaker), str(srt_path), str(speakers_json), str(wav_path)],
                           capture_output=True, timeout=60)

    # 讀取 SRT + 生成 meeting note（呼叫 Hermes 的 post_srt_to_notion.py）
    script_notion = Path("/home/eric/.hermes/profiles/meeting-note/scripts/post_srt_to_notion.py")
    result = subprocess.run(
        ["python3", str(script_notion), str(srt_path), original_name],
        capture_output=True, text=True, timeout=300
    )
    notion_url = ""
    for line in result.stdout.split('\n'):
        urls = re.findall(r'https://www\.notion\.so/[^\s]+', line)
        if urls:
            notion_url = urls[0]
            break

    # 更新 pending 檔案
    if pending_file.exists():
        data = json.loads(pending_file.read_text())
        data["notion_url"] = notion_url
        data["speaker_assignments"] = speaker_assignments
        pending_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # 更新 Render job 狀態為 completed（用現有 endpoint）
    mark_complete(job_id, notion_url=notion_url, status="completed")

    print(f"[worker] ✅ job {job_id} 完成，Notion: {notion_url}")
    send_telegram(f"✅ 會議記錄已完成\n{original_name}\n📓 {notion_url}")


def main():
    print(f"[worker] 啟動，poll interval={POLL_INTERVAL}s")
    print(f"[worker] Render URL: {RENDER_BASE_URL}")

    while True:
        try:
            # Phase 1: 處理 pending jobs（音頻下載、Mac 處理、等待 speaker naming）
            jobs = get_pending_jobs()
            if not jobs:
                # Phase 2: 檢查是否有 speaker naming 完成的 job
                named_jobs = poll_speaker_named_jobs()
                for job in named_jobs:
                    job_id = job["id"]
                    assignments = job.get("speaker_assignments", "")
                    if not assignments:
                        continue
                    print(f"\n[worker] Phase2: 處理 speaker naming 完成 {job_id}")
                    try:
                        complete_with_speaker_naming(job_id, job.get("original_name", ""), assignments)
                    except Exception as e:
                        print(f"[worker] Phase2 job {job_id} 失敗: {e}")
                if named_jobs:
                    print(f"[worker] {datetime.now().strftime('%H:%M:%S')} — Phase2 处理 {len(named_jobs)} jobs")
                time.sleep(POLL_INTERVAL)
                continue

            for job in jobs:
                job_id = job["id"]
                filename = job.get("filename", "")
                original_name = job.get("original_name", filename)
                print(f"\n[worker] Phase1 处理 job {job_id}: {original_name}")

                try:
                    mark_processing(job_id)
                    print(f"[worker] 下載音頻...")
                    audio_path = download_audio(job_id, filename)
                    print(f"[worker] 送到 Mac 處理...")
                    srt_path, speakers_json_path, wav_path = process_on_mac(audio_path, job_id)

                    # 只做 learning，不上 Notion
                    from speaker_learning_only import speaker_learning_only
                    speaker_learning_only(srt_path, speakers_json_path, wav_path)

                    # 寫入 pending 檔案，等你回覆 speaker 名字
                    pending_file = Path(f"/home/eric/.hermes/profiles/meeting-note/pending_speaker_naming/{job_id}.json")
                    pending_file.parent.mkdir(parents=True, exist_ok=True)
                    pending_file.write_text(json.dumps({
                        "job_id": job_id,
                        "original_name": original_name,
                        "srt_path": str(srt_path),
                        "speakers_json_path": str(speakers_json_path),
                        "wav_path": str(wav_path),
                        "notion_url": "",  # 等待命名後補上
                    }, ensure_ascii=False), encoding="utf-8")

                    mark_complete(job_id, notion_url="", status="awaiting_speaker_naming")
                    audio_path.unlink(missing_ok=True)
                    print(f"[worker] ✅ job {job_id} 完成（等待 speaker naming）")
                    send_telegram(
                        f"🎙️ 會議處理完成\n{original_name}\n"
                        f"請回覆發言者名字，格式：\n"
                        f"A=小明 B=阿輝"
                    )

                except Exception as e:
                    print(f"[worker] ❌ job {job_id} 失敗: {e}")
                    mark_fail(job_id, str(e))
                    send_telegram(f"❌ 會議處理失敗\n{original_name}\n{str(e)}")

        except KeyboardInterrupt:
            print("\n[worker] 收到 Ctrl+C，停止")
            break
        except Exception as e:
            print(f"[worker] 輪詢錯誤: {e}")
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()