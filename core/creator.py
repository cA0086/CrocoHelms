#!/usr/bin/env python3
from pathlib import Path

def create_item(parent_dir_str: str, name: str, item_type: str) -> dict:
    """
    Safely creates an empty file or a new directory branch.
    item_type must be either 'file' or 'folder'
    """
    if not parent_dir_str or not name:
        return {"error": "Missing directory destination or item name.", "code": 400}
        
    # Clean the name strings to prevent path traversal trickery
    clean_name = Path(name).name
    target_path = Path(parent_dir_str).resolve() / clean_name

    if target_path.exists():
        return {"error": f"An item named '{clean_name}' already exists here.", "code": 409}

    try:
        if item_type == "folder":
            target_path.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "message": f"Created folder '{clean_name}'", "code": 201}
        elif item_type == "file":
            target_path.touch()
            return {"ok": True, "message": f"Created empty file '{clean_name}'", "code": 201}
        else:
            return {"error": "Unknown creation structural type constraint.", "code": 400}
    except Exception as e:
        return {"error": f"OS filesystem error: {str(e)}", "code": 500}