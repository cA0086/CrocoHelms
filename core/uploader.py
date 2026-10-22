#!/usr/bin/env python3
import os
import time
import uuid
import email
from state import ul_lock, uploads
from core.scanner import fmt_size

def handle_upload(handler):
    content_type   = handler.headers.get("Content-Type", "")
    content_length = int(handler.headers.get("Content-Length", 0))
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[9:].strip().encode()
            break
    if not boundary: 
        return {"error": "No boundary"}, 400

    ul_id = str(uuid.uuid4())[:8]
    with ul_lock: 
        uploads[ul_id] = {"id": ul_id, "filename": "?", "dest_path": "", "status": "uploading", "received": 0, "total": content_length, "added": time.time(), "last_log": "Receiving…"}

    try:
        CHUNK = 65536
        buf = b""
        received = 0
        while received < content_length:
            chunk = handler.rfile.read(min(CHUNK, content_length - received))
            if not chunk: break
            buf += chunk
            received += len(chunk)
            with ul_lock: 
                uploads[ul_id]["received"] = received

        raw = (f"Content-Type: {content_type}\r\nContent-Length: {len(buf)}\r\n\r\n").encode() + buf
        msg = email.message_from_bytes(raw)
        
        dest_path = None
        file_data = None
        orig_filename = "upload"
        rel_path = None

        for part in msg.walk():
            cd = part.get("Content-Disposition", "")
            if not cd: continue
            params = {}
            for seg in cd.split(";"):
                seg = seg.strip()
                if "=" in seg:
                    k, v = seg.split("=", 1)
                    params[k.strip()] = v.strip().strip('"')
            name = params.get("name", "")
            
            if name == "dest_path":
                dest_path = part.get_payload(decode=True).decode("utf-8", errors="replace").strip()
            elif name == "relative_path":
                rel_path = part.get_payload(decode=True).decode("utf-8", errors="replace").strip()
            elif name == "file":
                orig_filename = params.get("filename", "upload")
                file_data = part.get_payload(decode=True)

        if not dest_path or file_data is None:
            with ul_lock: 
                uploads[ul_id].update({"status": "error", "last_log": "Missing file or destination"})
            return {"error": "Missing file or dest_path"}, 400

        # ─── NESTED DIRECTORY RECONSTRUCTION ───
        target_file_path = rel_path if rel_path else orig_filename
        clean_rel_path = target_file_path.lstrip("/")
        
        final_path = os.path.join(dest_path, clean_rel_path)
        
        # Dynamically create any missing nested parent directories
        os.makedirs(os.path.dirname(final_path), exist_ok=True)

        # Handle filename collisions
        if os.path.exists(final_path):
            base, ext = os.path.splitext(final_path)
            i = 1
            while os.path.exists(f"{base}_{i}{ext}"): 
                i += 1
            final_path = f"{base}_{i}{ext}"

        with open(final_path, "wb") as f: 
            f.write(file_data)
            
        size = len(file_data)
        with ul_lock:
            uploads[ul_id].update({
                "status": "complete", 
                "filename": clean_rel_path, 
                "dest_path": dest_path,
                "received": size, 
                "total": size, 
                "last_log": f"Saved {fmt_size(size)} → {clean_rel_path}"
            })
            
        return {"id": ul_id, "ok": True, "filename": clean_rel_path, "path": final_path}, 200
        
    except Exception as e:
        with ul_lock: 
            uploads[ul_id].update({"status": "error", "last_log": str(e)})
        return {"error": str(e)}, 500