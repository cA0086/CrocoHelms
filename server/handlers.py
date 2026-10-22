#!/usr/bin/env python3
import json
import uuid
import time
import signal
import os
import io
import zipfile
import mimetypes
import re
import subprocess
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote
import urllib.request

import config
import state
from core.scanner import build_fs_tree, list_folder
from core.downloader import extract_filename, run_download
from core.uploader import handle_upload

# Track installation status globally
install_status = {"status": "idle", "progress": 0, "error": None}
UPDATE_LOCK = threading.Lock()
UPDATE_JOBS = {}
VERSION_CACHE = {"fetched_at": 0, "data": None}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = Path(BASE_DIR).parent
MODELS_DIR = os.path.join(BASE_DIR, "vm_manager", "models")
MODEL_PATH = os.path.join(MODELS_DIR, "qwen2.5-0.5b-instruct-q4_k_m.gguf")
MODEL_URL = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")


def parse_semver(tag):
    if not tag:
        return None
    tag = tag.strip()
    match = SEMVER_RE.match(tag)
    if not match:
        return None
    major, minor, patch, prerelease, build = match.groups()
    return (int(major), int(minor), int(patch), prerelease or "", build or "")


def compare_versions(left, right):
    left_v = parse_semver(left)
    right_v = parse_semver(right)
    if left_v is None and right_v is None:
        return 0
    if left_v is None:
        return -1
    if right_v is None:
        return 1
    if left_v[:3] != right_v[:3]:
        return (left_v[:3] > right_v[:3]) - (left_v[:3] < right_v[:3])
    if left_v[3] == right_v[3]:
        return 0
    if left_v[3] == "":
        return 1
    if right_v[3] == "":
        return -1
    return (left_v[3] > right_v[3]) - (left_v[3] < right_v[3])


def get_current_git_version():
    try:
        tag = subprocess.check_output(["git", "describe", "--tags", "--abbrev=0"], cwd=REPO_DIR, stderr=subprocess.DEVNULL, text=True).strip()
        if tag:
            return tag
    except subprocess.CalledProcessError:
        pass
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_DIR, text=True).strip()
        if sha:
            return sha
    except Exception:
        return "unknown"
    return "unknown"


def get_github_release_data():
    global VERSION_CACHE
    now = time.time()
    if VERSION_CACHE["data"] is not None and (now - VERSION_CACHE["fetched_at"]) < config.UPDATE_CACHE_SECONDS:
        return VERSION_CACHE["data"]

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CrocoHelms-Updater"}
    url = f"https://api.github.com/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}/releases/latest"
    latest = {"tag_name": None, "error": None}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            latest["tag_name"] = payload.get("tag_name")
    except Exception as exc:
        latest["error"] = str(exc)

    if not latest["tag_name"]:
        url = f"https://api.github.com/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}/tags"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            tags = [item.get("name") for item in payload if isinstance(item, dict) and item.get("name")]
            if tags:
                latest["tag_name"] = sorted(tags, key=lambda x: parse_semver(x) or (0, 0, 0, "", ""), reverse=True)[0]
        except Exception as exc:
            latest["error"] = str(exc)

    VERSION_CACHE["data"] = latest
    VERSION_CACHE["fetched_at"] = now
    return latest


def get_version_payload():
    current = get_current_git_version()
    release = get_github_release_data()
    latest = release.get("tag_name") or current
    update_available = False
    if latest and current and compare_versions(latest, current) > 0:
        update_available = True
    return {
        "current": current,
        "latest": latest,
        "updateAvailable": update_available,
    }


def run_command(args, cwd=REPO_DIR, env=None):
    proc = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def restart_service():
    cmd = ["systemd-run", "--no-block", "systemctl", "restart", config.SERVICE_NAME]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        raise RuntimeError(f"Could not schedule service restart: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"Could not schedule service restart: {detail}")


def update_worker(job_id):
    job = UPDATE_JOBS[job_id]
    try:
        job["state"] = "running"
        job["step"] = "recording state"
        previous_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_DIR, text=True).strip()
        job["previous_commit"] = previous_commit

        job["step"] = "checking worktree"
        status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
        if status.stdout.strip():
            raise RuntimeError("Working tree is not clean; commit or stash the changes before updating.")

        job["step"] = "fetching latest release"
        release_data = get_github_release_data()
        latest = release_data.get("tag_name")
        if not latest:
            lookup_error = release_data.get("error")
            message = "No GitHub release or tag was available to update from."
            if lookup_error:
                message = f"{message} GitHub lookup error: {lookup_error}"
            raise RuntimeError(message)
        job["latest_tag"] = latest

        job["step"] = "fetching git refs"
        subprocess.run(["git", "fetch", "--tags", config.GITHUB_REPO_URL], cwd=REPO_DIR, check=True, capture_output=True, text=True)
        subprocess.run(["git", "checkout", "--detach", latest], cwd=REPO_DIR, check=True, capture_output=True, text=True)

        job["step"] = "installing dependencies"
        if os.path.exists(os.path.join(REPO_DIR, "requirements.txt")):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=REPO_DIR, check=True, capture_output=True, text=True)
        elif os.path.exists(os.path.join(REPO_DIR, "package.json")):
            subprocess.run(["npm", "ci"], cwd=REPO_DIR, check=True, capture_output=True, text=True)

        if os.path.exists(os.path.join(REPO_DIR, "package.json")):
            job["step"] = "building frontend"
            proc = subprocess.run(["npm", "run", "build"], cwd=REPO_DIR, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Build step failed")

        job["step"] = "sanity check"
        if os.path.exists(os.path.join(REPO_DIR, "requirements.txt")):
            proc = subprocess.run([sys.executable, "-m", "compileall", "."], cwd=REPO_DIR, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Sanity check failed")

        job["step"] = "restarting service"
        restart_service()
        job["state"] = "success"
        job["step"] = "restarted"
    except Exception as exc:
        job["state"] = "failed"
        job["step"] = job.get("step", "failed")
        error_message = str(exc).strip() or repr(exc)
        if isinstance(exc, subprocess.CalledProcessError):
            command_output = (exc.stderr or exc.stdout or "").strip()
            if command_output and command_output not in error_message:
                error_message = f"{error_message}\n{command_output}"
        job["error"] = error_message
        print(f"[CrocoHelms updater] Failed during {job['step']}: {error_message}", file=sys.stderr, flush=True)
        try:
            subprocess.run(["git", "checkout", "--detach", previous_commit], cwd=REPO_DIR, check=True, capture_output=True, text=True)
        except Exception as rollback_error:
            rollback_message = str(rollback_error).strip() or repr(rollback_error)
            if isinstance(rollback_error, subprocess.CalledProcessError):
                command_output = (rollback_error.stderr or rollback_error.stdout or "").strip()
                if command_output and command_output not in rollback_message:
                    rollback_message = f"{rollback_message}\n{command_output}"
            print(f"[CrocoHelms updater] Rollback failed: {rollback_message}", file=sys.stderr, flush=True)
    finally:
        job["updated_at"] = time.time()
        UPDATE_LOCK.release()


def is_local_request(handler):
    ip = handler.client_address[0]
    if ip in ("127.0.0.1", "::1"):
        return True
    host = (handler.headers.get("Host") or "").lower()
    return host.startswith("localhost:") or host.startswith("127.0.0.1:") or host.startswith("[::1]:") or host.startswith("0.0.0.0:")


def has_valid_update_token(handler):
    expected = (config.UPDATE_TOKEN or "").strip()
    if not expected:
        return False
    token = (handler.headers.get("X-Update-Token") or handler.headers.get("X-CSRF-Token") or "").strip()
    return token == expected


def get_update_job(job_id):
    if job_id not in UPDATE_JOBS:
        return None
    job = UPDATE_JOBS[job_id]
    result = {"state": job.get("state", "idle"), "step": job.get("step", "idle"), "log": job.get("log", []), "error": job.get("error")}
    if "latest_tag" in job:
        result["latest_tag"] = job["latest_tag"]
    return result

def get_tree(force=False):
    """Thread-safe filesystem tree retrieval with caching."""
    with state.tree_lock:
        if state.cached_tree is None or force:
            state.cached_tree = build_fs_tree()
        return state.cached_tree

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        """Suppress default HTTP logging to keep console clean."""
        pass

    def _send(self, code, ctype, body):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, code=200):
        self._send(code, "application/json", json.dumps(data))

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        for h, v in [
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type,X-Update-Token,X-CSRF-Token")
        ]:
            self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        raw_path = self.path
        p = raw_path.split("?")[0]
        qs = raw_path[len(p)+1:] if "?" in raw_path else ""
        params = parse_qs(qs)

        # ── SERVE STATIC FRONTEND ASSETS ──
        if p in ("/", "/index.html"):
            try:
                with open("static/index.html", "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            except FileNotFoundError:
                self._send(404, "text/plain", "Static index file not found.")

        elif p == "/app.css":
            try:
                with open("static/app.css", "rb") as f:
                    self._send(200, "text/css", f.read())
            except FileNotFoundError:
                self._send(404, "text/plain", "Asset stylesheet not found.")

        elif p == "/croco.png":
            try:
                with open("static/croco.png", "rb") as f:
                    self._send(200, "image/png", f.read())
            except FileNotFoundError:
                self._send(404, "text/plain", "Logo asset not found.")

        # ── API ENDPOINTS ──
        elif p == "/api/version":
            self._json(get_version_payload())

        elif p == "/api/update-status":
            job_id = params.get("job", [None])[0]
            if job_id:
                payload = get_update_job(job_id)
                if payload is None:
                    self._json({"state": "idle", "step": "unknown", "log": [], "error": "No such job."}, 404)
                    return
                self._json(payload)
                return

            if not (is_local_request(self) or has_valid_update_token(self)):
                self._json({"message": "Updates require a valid update token."}, 403)
            else:
                self._json({"state": "idle", "step": "idle", "log": [], "error": None})

        elif p == "/api/config":
            self._json({
                "downloader": config.DOWNLOADER or "none",
                "update_token_required": bool((config.UPDATE_TOKEN or "").strip())
            })

        elif p == "/api/scan":
            self._json({"tree": get_tree()})

        elif p == "/api/storage":
            from core.storage import analyze_storage
            
            # Default to the current workspace root, but allow specific path checks
            target = unquote(params.get("path", ["."])[0])
            stats = analyze_storage(target)
            
            if "error" in stats:
                self._json(stats, 500)
            else:
                self._json(stats, 200)
            return

        elif p == "/api/status":
            with state.dl_lock:
                # Snap state snapshot while excluding internal active subprocess objects
                snap = {k: {kk: vv for kk, vv in v.items() if kk != "process"} for k, v in state.downloads.items()}
            self._json({"downloads": snap})

        elif p == "/api/ls":
            path = unquote(params.get("path", [""])[0])
            self._json(list_folder(path))

        elif p == "/api/file":
            path = unquote(params.get("path", [""])[0])
            fp = Path(path)
            if not fp.is_file():
                self._send(404, "text/plain", "Not found")
                return
            mime, _ = mimetypes.guess_type(str(fp))
            mime = mime or "application/octet-stream"
            try:
                data = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{fp.name}"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._send(500, "text/plain", f"Server stream error: {str(e)}")

        elif p == "/api/folder/zip":
            # Extract the directory target from the incoming URL query parameter string
            path_target = unquote(params.get("path", [""])[0])
            
            from core.zipper import zip_directory_buffer
            data_payload, archive_name, error = zip_directory_buffer(path_target)
            
            if error:
                self._json({"error": error}, 400)
                return
                
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(data_payload)))
                # Forces the browser to save it as a physical file download rather than rendering text characters
                self.send_header("Content-Disposition", f'attachment; filename="{archive_name}"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data_payload)
            except Exception as e:
                self._send(500, "text/plain", f"Server output wire execution failure: {str(e)}")
            return

        elif p == "/api/hardware":
            from core.hardware import get_system_specs
            self._json(get_system_specs(), 200)
            return

        elif p == "/api/install-status":
            global install_status
            if install_status["status"] == "idle" and os.path.exists(MODEL_PATH):
                self._json({"status": "completed", "progress": 100, "error": None})
            else:
                self._json(install_status)
            return

        else:
            self._send(404, "text/plain", "Not found")

    def do_POST(self):
        import threading
        p = self.path.split("?")[0]

        if p == "/api/update":
            if not (is_local_request(self) or has_valid_update_token(self)):
                self._json({"error": "Updates require a valid update token."}, 403)
                return
            if not UPDATE_LOCK.acquire(blocking=False):
                self._json({"error": "An update is already in progress."}, 409)
                return

            job_id = str(uuid.uuid4())
            UPDATE_JOBS[job_id] = {"state": "queued", "step": "queued", "log": [], "error": None}
            threading.Thread(target=update_worker, args=(job_id,), daemon=True).start()
            self._json({"jobId": job_id, "ok": True}, 202)
            return

        # 1. Multi-part form-data bypasses standard JSON reading loops
        if p == "/api/upload":
            result, code = handle_upload(self)
            self._json(result, code)
            return

        # 2. Extract and load the incoming network stream into 'body' first
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json({"error": f"Malformed JSON request payload: {str(e)}"}, 400)
            return

        # 3. Now it is safe to query 'body' for any of these targets
        if p == "/api/edit/read":
            path = body.get("path", "").strip()
            if not path:
                self._json({"error": "No path provided."}, 400)
                return
            fp = Path(path)
            if not fp.is_file():
                self._json({"error": "Target is not a valid file or does not exist."}, 404)
                return
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                self._json({"ok": True, "content": content})
            except Exception as e:
                self._json({"error": f"Failed to read file: {str(e)}"}, 500)
            return

        elif p == "/api/edit/save":
            path = body.get("path", "").strip()
            content = body.get("content", "")
            if not path:
                self._json({"error": "No path provided."}, 400)
                return
            fp = Path(path)
            if not fp.parent.exists():
                self._json({"error": "Target directory structure no longer exists."}, 404)
                return
            try:
                fp.write_text(content, encoding="utf-8")
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": f"Failed to save changes: {str(e)}"}, 500)
            return

        elif p == "/api/zip":
            paths = body.get("paths", [])
            zip_name = body.get("name", "archive.zip")
            if not paths:
                self._json({"error": "No targets requested for archival"}, 400)
                return
            try:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fp_item in paths:
                        fpath = Path(fp_item)
                        if fpath.is_file():
                            zf.write(str(fpath), fpath.name)
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{zip_name}"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._json({"error": f"Archival stream runtime exception: {str(e)}"}, 500)
            return

        elif p == "/api/download":
            url = body.get("url", "").strip()
            dest_path = body.get("dest_path", "/tmp").strip() or "/tmp"
            if not url.startswith("http"):
                self._json({"error": "Invalid URL format protocol"}, 400)
                return
            dl_id = str(uuid.uuid4())[:8]
            filename = extract_filename(url)
            meta = {
                "id": dl_id,
                "url": url,
                "dest_path": dest_path,
                "filename": filename,
                "status": "queued",
                "progress": 0,
                "last_log": "",
                "tool": config.DOWNLOADER or "none",
                "added": time.time()
            }
            with state.dl_lock:
                state.downloads[dl_id] = meta
            threading.Thread(target=run_download, args=(dl_id, url, dest_path, filename), daemon=True).start()
            self._json(meta)

        elif p == "/api/cancel":
            dl_id = body.get("id", "")
            with state.dl_lock:
                dl = state.downloads.pop(dl_id, None)
            if dl and dl.get("pid"):
                try:
                    os.kill(dl["pid"], signal.SIGTERM)
                except Exception:
                    pass
            self._json({"ok": True})

        elif p == "/api/scan":
            self._json({"tree": get_tree(force=True)})

        elif p == "/api/file/delete":
            # 1. Extract path target from the pre-parsed 'body' dictionary
            path_target = body.get("path", "").strip()

            # 2. Defer the heavy lifting to the core module engine
            from core.deleter import delete_target
            res = delete_target(path_target)

            # 3. Handle response status orchestration using the dictionary code returned
            if "error" in res:
                self._json({"error": res["error"]}, res["code"])
            else:
                self._json({"ok": True, "message": f"Successfully deleted {res['filename']}"}, res["code"])

        elif p == "/api/file/rename":
            path = body.get("path", "")
            new_name = body.get("new_name", "")
            from core.operations import rename_target
            res, code = rename_target(path, new_name)
            self._json(res, code)
            return

        elif p == "/api/file/move":
            # Used for drag-and-drop operations
            src_path = body.get("src_path", "")
            dest_dir = body.get("dest_dir", "")
            from core.operations import move_target
            res, code = move_target(src_path, dest_dir)
            self._json(res, code)
            return

        elif p == "/api/file/paste":
            # Used for copy/cut/paste actions
            src_path = body.get("src_path", "")
            dest_dir = body.get("dest_dir", "")
            action = body.get("action", "copy") # Accept "copy" or "cut"
            from core.operations import paste_target
            res, code = paste_target(src_path, dest_dir, action)
            self._json(res, code)
            return

        elif p == "/api/item/create":
            parent_dir = body.get("parent_dir", "").strip()
            item_name = body.get("name", "").strip()
            item_type = body.get("type", "").strip() # 'file' or 'folder'

            from core.creator import create_item
            res = create_item(parent_dir, item_name, item_type)

            if "error" in res:
                self._json({"error": res["error"]}, res["code"])
            else:
                self._json({"ok": True, "message": res["message"]}, res["code"])
            return

        elif p == "/api/hardware/install":
            import subprocess
            import sys
            try:
                # Executes pip install directly targeting the active environment binary
                subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil"])
                self._json({"ok": True, "message": "psutil installed successfully."}, 200)
            except Exception as e:
                self._json({"ok": False, "error": f"Installation failed: {str(e)}"}, 500)
            return

        elif p == "/api/install-model":
            global install_status
            if install_status["status"] == "downloading":
                self._json({"message": "Download already in progress"}, 400)
                return
                
            # Start download in a non-blocking background thread
            threading.Thread(target=download_worker, daemon=True).start()
            self._json({"message": "Installation started"}, 202)
            return

        elif p == "/api/summarize-file":
            file_path = body.get("filePath")
            
            if not file_path or not os.path.exists(file_path):
                self._json({"error": "File not found"}, 400)
                return

            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(2000) # Read first 2000 characters for token safety
                    
                # Context guard if model isn't downloaded yet
                if not os.path.exists(MODEL_PATH):
                    self._json({"error": "Model not installed. Please use diagnostics panel to install Qwen 0.5B."}, 400)
                    return

                try:
                    from llama_cpp import Llama
                    llm = Llama(model_path=MODEL_PATH, verbose=False)
                    response = llm.create_chat_completion(messages=[{"role": "user", "content": f"Summarize this:\n{content}"}])
                    summary = response["choices"][0]["message"]["content"]
                except ImportError:
                    summary = f"Simulated summary for {os.path.basename(file_path)} using local Qwen 0.5B. (llama-cpp-python not installed)"
                    
                self._json({"summary": summary})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        else:
            self._send(404, "text/plain", "Not found")
