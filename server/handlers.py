#!/usr/bin/env python3
import json
import uuid
import time
import signal
import os
import io
import zipfile
import mimetypes
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
update_status = {"status": "idle", "message": ""}
UPDATE_LOCK = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = Path(BASE_DIR).parent
MODELS_DIR = os.path.join(BASE_DIR, "vm_manager", "models")
MODEL_PATH = os.path.join(MODELS_DIR, "qwen2.5-0.5b-instruct-q4_k_m.gguf")
MODEL_URL = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

def download_worker():
    global install_status
    try:
        os.makedirs(MODELS_DIR, exist_ok=True)
        install_status = {"status": "downloading", "progress": 0, "error": None}
        
        def report_hook(block_num, block_size, total_size):
            if total_size > 0:
                progress = int((block_num * block_size / total_size) * 100)
                install_status["progress"] = min(progress, 99)

        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH, reporthook=report_hook)
        install_status = {"status": "completed", "progress": 100, "error": None}
    except Exception as e:
        install_status = {"status": "failed", "progress": 0, "error": str(e)}

def restart_application(server):
    server.shutdown()
    server.server_close()
    subprocess.Popen([sys.executable, str(REPO_DIR / "main.py")], cwd=str(REPO_DIR))
    os._exit(0)

def update_worker(server):
    global update_status
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=REPO_DIR, capture_output=True, text=True, check=True,
        ).stdout.strip()
        if dirty:
            raise RuntimeError("Local tracked changes found. Commit or stash them before updating.")

        upstream = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=REPO_DIR, text=True, stderr=subprocess.STDOUT,
        ).strip()
        if not upstream.startswith("origin/"):
            raise RuntimeError("This branch is not tracking an origin branch.")

        update_status = {"status": "checking", "message": "Checking GitHub for updates..."}
        subprocess.run(["git", "fetch", "origin"], cwd=REPO_DIR, check=True, capture_output=True, text=True)
        counts = subprocess.check_output(
            ["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"],
            cwd=REPO_DIR, text=True, stderr=subprocess.STDOUT,
        ).split()
        local_commits, remote_commits = map(int, counts)
        if local_commits:
            raise RuntimeError("This clone has unpublished local commits. Push or remove them before updating.")
        if remote_commits == 0:
            update_status = {"status": "current", "message": "CrocoHelms is already up to date."}
            return

        update_status = {"status": "updating", "message": f"Installing {remote_commits} update(s)..."}
        subprocess.run(
            ["git", "merge", "--ff-only", upstream],
            cwd=REPO_DIR, check=True, capture_output=True, text=True,
        )
        update_status = {"status": "restarting", "message": "Update installed. Restarting CrocoHelms..."}
        timer = threading.Timer(2.5, restart_application, args=(server,))
        timer.daemon = True
        timer.start()
    except Exception as e:
        update_status = {"status": "error", "message": str(e)}
    finally:
        UPDATE_LOCK.release()

def is_local_request(handler):
    return handler.client_address[0] in ("127.0.0.1", "::1")

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
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, "application/json", json.dumps(data))

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        for h, v in [
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type")
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
        elif p == "/api/update-status":
            if not is_local_request(self):
                self._json({"message": "Updates can only be managed from this device."}, 403)
            else:
                self._json(update_status)

        elif p == "/api/config":
            self._json({"downloader": config.DOWNLOADER or "none"})

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
        global update_status
        p = self.path.split("?")[0]

        if p == "/api/update":
            expected_origin = f"http://{self.headers.get('Host', '')}"
            if not is_local_request(self) or self.headers.get("Origin") != expected_origin:
                self._json({"error": "Updates can only be started from this device."}, 403)
                return
            if not UPDATE_LOCK.acquire(blocking=False):
                self._json({"error": "An update is already in progress."}, 409)
                return
            if update_status["status"] in ("checking", "updating", "restarting"):
                UPDATE_LOCK.release()
                self._json({"error": "An update is already in progress."}, 409)
                return
            update_status = {"status": "checking", "message": "Checking GitHub for updates..."}
            threading.Thread(target=update_worker, args=(self.server,), daemon=True).start()
            self._json({"ok": True}, 202)
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
