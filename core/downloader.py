#!/usr/bin/env python3
import os
import re
import subprocess
from urllib.parse import urlparse
from config import DOWNLOADER
from state import dl_lock, downloads

def extract_filename(url):
    try:
        name = urlparse(url).path.rstrip("/").split("/")[-1]
        return name if name else "download"
    except: 
        return "download"

# Helper to turn raw bytes into clean UI strings (e.g., 1.20 GB)
def format_bytes(bytes_val):
    if bytes_val < 1024: return f"{bytes_val:.1f} B"
    elif bytes_val < 1048576: return f"{bytes_val/1024:.1f} KB"
    elif bytes_val < 1073741824: return f"{bytes_val/1048576:.1f} MB"
    else: return f"{bytes_val/1073741824:.2f} GB"

def make_cmd(url, dest_dir, filename, tool):
    os.makedirs(dest_dir, exist_ok=True)
    fp = os.path.join(dest_dir, filename)
    if tool == "aria2c":
        return ["aria2c", "--console-log-level=warn", "--summary-interval=1",
                "-x16", "-s16", "-k1M", "--dir", dest_dir, "--out", filename, url]
    if tool == "wget": return ["wget", "--progress=dot:giga", "-O", fp, url]
    if tool == "curl": return ["curl", "-L", "--progress-bar", "-o", fp, url]
    raise RuntimeError("No downloader available.")

def run_download(dl_id, url, dest_dir, filename):
    tool = DOWNLOADER
    if not tool:
        with dl_lock: 
            downloads[dl_id].update({"status": "error", "last_log": "No downloader found."})
        return
        
    cmd = make_cmd(url, dest_dir, filename, tool)
    
    with dl_lock:
        downloads[dl_id]["status"] = "downloading"
        downloads[dl_id]["tool"]   = tool

    pct_regex = re.compile(r"(\d+(?:\.\d+)?)%")
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        with dl_lock: 
            downloads[dl_id]["pid"] = proc.pid
            
        for line in proc.stdout:
            line = line.strip()
            if not line: continue
            
            # Extract the percentage float for the blue progress bar
            match = pct_regex.search(line)
            
            # 1. Clean the raw terminal output
            clean_log = line.replace(".", "").strip()
            clean_log = re.sub(r'\s+', ' ', clean_log) 
            display_text = clean_log[-40:] # Fallback default

            # 2. SMART FORMATTER (Translates "1310720K 89%" -> "1.25 GB / 1.40 GB")
            wget_match = re.search(r'^(\d+)([KMGkmg]?)\s+(\d+(?:\.\d+)?)%', clean_log)
            if wget_match:
                try:
                    amt = float(wget_match.group(1))
                    unit = wget_match.group(2).upper()
                    pct = float(wget_match.group(3))
                    
                    # Convert K/M/G into raw bytes
                    multiplier = {"K": 1024, "M": 1048576, "G": 1073741824}.get(unit, 1)
                    current_bytes = amt * multiplier
                    
                    # Calculate total size based on current percentage
                    if pct > 0:
                        total_bytes = current_bytes / (pct / 100.0)
                        display_text = f"{format_bytes(current_bytes)} / {format_bytes(total_bytes)}"
                    else:
                        display_text = f"{format_bytes(current_bytes)} / ???"
                except ValueError:
                    pass

            with dl_lock:
                if dl_id not in downloads: break
                
                # Send the beautiful "MB / GB" string to the React UI
                downloads[dl_id]["last_log"] = display_text
                
                # Update the blue fill bar width
                if match:
                    try:
                        pct_val = float(match.group(1))
                        downloads[dl_id]["progress"] = pct_val / 100.0
                    except ValueError:
                        pass
                        
        proc.wait()
        
        with dl_lock:
            if dl_id in downloads:
                downloads[dl_id]["status"] = "complete" if proc.returncode == 0 else "error"
                if proc.returncode == 0:
                    downloads[dl_id]["progress"] = 1.0
                    downloads[dl_id]["last_log"] = "Done"
                else:
                    downloads[dl_id]["last_log"] = f"Exit code {proc.returncode}"
                    
    except Exception as e:
        with dl_lock:
            if dl_id in downloads: 
                downloads[dl_id].update({"status": "error", "last_log": str(e)})