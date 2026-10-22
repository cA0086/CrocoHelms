#!/usr/bin/env python3
import shutil
from pathlib import Path

def delete_target(target_path_str: str) -> dict:
    """
    Safely deletes a file or recursively purges a directory structure.
    Returns a dictionary indicating operation status.
    """
    if not target_path_str:
        return {"error": "No target path provided.", "code": 400}
        
    fp = Path(target_path_str).resolve()
    
    # Safety Check: Prevent accidental root or system file purges
    if fp in (Path("/"), Path("/home/ubuntu"), Path.cwd()):
        return {"error": "Permission denied: Cannot delete critical workspace anchors.", "code": 403}

    if not fp.exists():
        return {"error": "Target file or directory does not exist on disk.", "code": 404}

    try:
        if fp.is_file() or fp.is_symlink():
            fp.unlink() # Instantly erase the file segment
        elif fp.is_dir():
            shutil.rmtree(fp) # Recursively clear out the directory sub-tree
            
        return {"ok": True, "filename": fp.name, "code": 200}
    except Exception as e:
        return {"error": f"OS filesystem error: {str(e)}", "code": 500}