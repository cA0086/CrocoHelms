#!/usr/bin/env python3
import os
import subprocess

PORT = 8765
HOST = "0.0.0.0"
APP_DIR = os.path.abspath(os.path.dirname(__file__))
GITHUB_OWNER = "cA0086"
GITHUB_REPO = "CrocoHelms"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
SERVICE_NAME = os.environ.get("CROCOHEMS_SERVICE_NAME", "crocohelms.service")
UPDATE_ENABLED = os.environ.get("CROCOHEMS_UPDATE_ENABLED", "true").lower() not in {"0", "false", "no"}
UPDATE_TOKEN = os.environ.get("CROCOHEMS_UPDATE_TOKEN", "change-me")
UPDATE_CACHE_SECONDS = 600

# Set this to a strong secret on the EC2 instance, then keep the same value in the local UI.
# Example: export CROCOHEMS_UPDATE_TOKEN="my-long-random-token"

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