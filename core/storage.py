#!/usr/bin/env python3
import shutil
import os
from pathlib import Path

def analyze_storage(target_dir="."):
    """
    Returns real-time disk block allocation stats for the drive 
    hosting the target directory.
    """
    try:
        # Resolve the absolute path to ensure we check the correct mounted drive
        target_path = Path(target_dir).resolve()
        
        # shutil asks the OS for the raw block data
        total, used, free = shutil.disk_usage(target_path)
        
        return {
            "ok": True,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "percent_used": round((used / total) * 100, 1)
        }
    except Exception as e:
        return {"error": f"Storage analysis failed: {str(e)}"}