import shutil
from pathlib import Path

def rename_target(path_str, new_name):
    """Renames a file or directory in place."""
    src = Path(path_str.strip())
    if not src.exists():
        return {"error": "Source file or folder does not exist."}, 404
        
    new_name = new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name:
        return {"error": "Invalid target name structure."}, 400
        
    try:
        dest = src.with_name(new_name)
        if dest.exists():
            return {"error": "A target item with that name already exists."}, 409
            
        src.rename(dest)
        return {"ok": True, "message": f"Renamed to {new_name}"}, 200
    except Exception as e:
        return {"error": f"Rename execution failure: {str(e)}"}, 500


def move_target(src_path_str, dest_dir_str):
    """Moves a file or directory into a target directory (Supports Drag & Drop)."""
    src = Path(src_path_str.strip())
    dest_dir = Path(dest_dir_str.strip())
    
    if not src.exists():
        return {"error": "Source target does not exist."}, 404
    if not dest_dir.is_dir():
        return {"error": "Destination path is not a valid directory."}, 400
        
    try:
        dest_path = dest_dir / src.name
        if dest_path.exists():
            return {"error": f"An item named '{src.name}' already exists in the target directory."}, 409
            
        shutil.move(str(src), str(dest_path))
        return {"ok": True, "message": f"Successfully moved {src.name}"}, 200
    except Exception as e:
        return {"error": f"Move execution failure: {str(e)}"}, 500


def paste_target(src_path_str, dest_dir_str, operation_type="copy"):
    """Handles copying or cutting files/folders into a new workspace directory."""
    src = Path(src_path_str.strip())
    dest_dir = Path(dest_dir_str.strip())
    
    if not src.exists():
        return {"error": "Source clipboard item no longer exists."}, 404
    if not dest_dir.is_dir():
        return {"error": "Target drop directory is invalid."}, 400
        
    dest_path = dest_dir / src.name
    if dest_path.exists():
        return {"error": f"An item named '{src.name}' already exists in this directory."}, 409
        
    try:
        if operation_type == "cut":
            shutil.move(str(src), str(dest_path))
            msg = f"Successfully moved {src.name}"
        else: # Default behavior: copy
            if src.is_dir():
                shutil.copytree(str(src), str(dest_path))
            else:
                shutil.copy2(str(src), str(dest_path))
            msg = f"Successfully copied {src.name}"
            
        return {"ok": True, "message": msg}, 200
    except Exception as e:
        return {"error": f"Clipboard execution failure: {str(e)}"}, 500