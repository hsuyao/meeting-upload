"""
Notion Jobs Storage - child pages under Meeting Jobs (36b9342f-4b33-81a3-8c67-cc0552aca952)
Each job = one child page. Job data stored as JSON code block in page content.
"""
import json
import os
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error

NOTION_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_VERSION = "2025-09-03"
JOBS_PARENT_ID = "36b9342f-4b33-81a3-8c67-cc0552aca952"  # Meeting Jobs page

LOCAL_JOBS_FILE = Path("./jobs.json")

def _notion_api(method, path, data=None):
    """Call Notion API"""
    url = f"https://api.notion.com/v1/{path}"
    payload = json.dumps(data).encode('utf-8') if data else None
    req_args = {
        "headers": {
            "Authorization": f"Bearer {NOTION_KEY}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"
        }
    }
    if method in ("POST", "PATCH"):
        req_args["data"] = payload
        req_args["method"] = method
    else:
        req_args["method"] = method
    req = urllib.request.Request(url, **req_args)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    except Exception as e:
        return {"object": "error", "code": "network", "message": str(e)}

def _safe_get(data, *keys, default=None):
    for k in keys:
        if isinstance(data, dict):
            data = data.get(k, default)
        elif isinstance(data, list) and isinstance(k, int) and 0 <= k < len(data):
            data = data[k]
        else:
            return default
    return data

# ==========================================
# Job schema
# ==========================================
# {
#   "id": "uuid",
#   "filename": "audio.webm",
#   "original_name": "original name",
#   "status": "pending|processing|awaiting_speaker_naming|completed|error",
#   "created_at": "ISO timestamp",
#   "notion_url": "",
#   "error": "",
#   "speaker_assignments": "",
#   "audio_url": ""
# }

def _page_to_job(block, page_data=None):
    """Convert a child_page block + its page data to job dict"""
    # Try to get title from block's child_page.title or page.title
    title = block.get("child_page", {}).get("title", "")
    page_id = block.get("id")

    # Try to parse JSON from page content (code blocks)
    job_json = None
    if page_data:
        for child in page_data.get("results", []):
            if child.get("type") == "code":
                content = _safe_get(child, "code", "rich_text", 0, "text", "content", default="")
                if content:
                    try:
                        job_json = json.loads(content)
                        break
                    except Exception:
                        pass

    # Fallback: title IS the job_id (for older pages)
    if not job_json:
        job_id = title  # title = job_id for new pages
        return {
            "id": job_id,
            "filename": job_id,  # unknown
            "original_name": job_id,
            "status": "unknown",
            "created_at": block.get("created_time", ""),
            "notion_url": f"https://www.notion.so/{page_id.replace('-','')}" if page_id else "",
            "error": "",
            "speaker_assignments": "",
            "audio_url": ""
        }

    job_json["notion_url"] = f"https://www.notion.so/{page_id.replace('-','')}" if page_id else ""
    return job_json

def _build_page_blocks(job):
    """Build blocks for a job page (title + JSON code block)"""
    job_id = job.get("id", "untitled")
    return [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"text": {"content": f"Job: {job_id}"}}]
            }
        },
        {
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": [{"text": {"content": json.dumps(job, ensure_ascii=False)}}],
                "language": "json"
            }
        }
    ]

# ==========================================
# CRUD operations
# ==========================================

def load_jobs(limit=None):
    """Load all jobs as child pages of Meeting Jobs, sorted newest first"""
    if not NOTION_KEY:
        return _load_local()

    jobs = []
    cursor = None
    while True:
        path = f"blocks/{JOBS_PARENT_ID}/children"
        if cursor:
            path += f"?start_cursor={cursor}"
        result = _notion_api("GET", path)
        if result.get("object") == "error":
            print(f"[notion_jobs] load failed: {result.get('message')}, using local")
            return _load_local()

        for block in result.get("results", []):
            if block.get("type") == "child_page":
                page_id = block.get("id")
                # Get page properties
                page_result = _notion_api("GET", f"pages/{page_id}")
                # Get page content (children blocks)
                blocks_result = _notion_api("GET", f"blocks/{page_id}/children")
                job = _page_to_job(block, blocks_result)
                jobs.append(job)

        has_more = result.get("has_more", False)
        cursor = result.get("next_cursor")
        if not has_more or not cursor:
            break

    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    if limit:
        jobs = jobs[:limit]
    return jobs

def get_job(job_id):
    """Get single job by job_id"""
    if not NOTION_KEY:
        return _get_local(job_id)

    all_jobs = load_jobs(limit=500)
    return next((j for j in all_jobs if j["id"] == job_id), None)

def create_job(job):
    """Create new job as child page of Meeting Jobs"""
    if not NOTION_KEY:
        return _create_local(job)

    job_id = job.get("id", "untitled")
    page_result = _notion_api("POST", "pages", {
        "parent": {"page_id": JOBS_PARENT_ID},
        "properties": {
            "title": {"title": [{"text": {"content": job_id}}]}
        },
        "children": _build_page_blocks(job)
    })

    if page_result.get("object") == "error":
        print(f"[notion_jobs] create failed: {page_result.get('message')}, using local")
        return _create_local(job)

    page_id = page_result.get("id", "")
    job["notion_url"] = f"https://www.notion.so/{page_id.replace('-','')}"
    return job

def update_job(job_id, **kwargs):
    """Update job fields (status, notion_url, error, etc)"""
    if not NOTION_KEY:
        return _update_local(job_id, **kwargs)

    # Find existing job page
    all_jobs = load_jobs(limit=500)
    match = next((j for j in all_jobs if j["id"] == job_id), None)
    if not match:
        print(f"[notion_jobs] Job {job_id} not found, using local")
        return _update_local(job_id, **kwargs)

    # Find page ID from notion_url
    notion_url = match.get("notion_url", "")
    if not notion_url:
        return _update_local(job_id, **kwargs)

    # Extract page ID from notion URL
    # https://www.notion.so/{slug}-{page_id_32}
    parts = notion_url.rsplit("-", 1)
    if len(parts) == 2:
        page_id_candidate = parts[1]
        if len(page_id_candidate) == 32:
            page_id = page_id_candidate
            # Reconstruct with dashes
            page_id = f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}"
        else:
            page_id = None
    else:
        page_id = None

    if not page_id:
        return _update_local(job_id, **kwargs)

    # Fetch current page to get existing content
    page_result = _notion_api("GET", f"pages/{page_id}")
    if page_result.get("object") == "error":
        return _update_local(job_id, **kwargs)

    # Update the job dict
    match.update(kwargs)
    job = match

    # Build updated blocks (preserve title block, update code block)
    blocks = []
    for child in page_result.get("results", []):
        block_type = child.get("type")
        if block_type == "heading_2":
            blocks.append(child)  # keep title
        elif block_type == "code":
            # Update JSON
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{"text": {"content": json.dumps(job, ensure_ascii=False)}}],
                    "language": "json"
                }
            })

    # If no existing code block, append new one
    has_code = any(b.get("type") == "code" for b in blocks)
    if not has_code:
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": [{"text": {"content": json.dumps(job, ensure_ascii=False)}}],
                "language": "json"
            }
        })

    # Append new blocks
    append_result = _notion_api("PATCH", f"blocks/{page_id}/children", {
        "children": [_build_page_blocks(job)[1]]  # just the code block
    })
    if append_result.get("object") == "error":
        print(f"[notion_jobs] update failed: {append_result.get('message')}, using local")
        return _update_local(job_id, **kwargs)

# ==========================================
# Local fallback
# ==========================================

def _load_local():
    if LOCAL_JOBS_FILE.exists():
        try:
            return json.loads(LOCAL_JOBS_FILE.read_text())
        except Exception:
            return []
    return []

def _get_local(job_id):
    jobs = _load_local()
    return next((j for j in jobs if j["id"] == job_id), None)

def _create_local(job):
    jobs = _load_local()
    jobs.append(job)
    LOCAL_JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    return job

def _update_local(job_id, **kwargs):
    jobs = _load_local()
    for job in jobs:
        if job["id"] == job_id:
            job.update(kwargs)
            break
    LOCAL_JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))

def _delete_local(job_id):
    jobs = _load_local()
    jobs = [j for j in jobs if j["id"] != job_id]
    LOCAL_JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))