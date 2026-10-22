#!/usr/bin/env python3
import subprocess

PORT = 8765
HOST = "0.0.0.0"

SCAN_ROOTS = ["/workspace", "/root", "/home", "/opt", "/data", "/mnt", "/tmp"]

SKIP_DIRS = {
    "proc", "sys", "dev", "run", "snap", "boot", "lost+found",
    "__pycache__", ".git", "node_modules", ".cache", ".local",
    "site-packages", "dist-packages", "lib64", "lib32",
}

MAX_DEPTH = 8
MAX_ENTRIES = 2000

def pick_downloader():
    for tool in ("aria2c", "wget", "curl"):
        if subprocess.run(["which", tool], capture_output=True).returncode == 0:
            return tool
    return None

DOWNLOADER = pick_downloader()