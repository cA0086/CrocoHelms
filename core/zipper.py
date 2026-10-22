#!/usr/bin/env python3
import io
import zipfile
from pathlib import Path

def zip_directory_buffer(dir_path_str: str) -> tuple[bytes | None, str, str | None]:
    """
    Recursively compresses a directory structure into an in-memory binary buffer.
    Returns: (bytes_payload, archive_name, error_message)
    """
    source_dir = Path(dir_path_str).resolve()
    if not source_dir.is_dir():
        return None, "", "Target path is not a valid directory or has been removed."

    archive_name = f"{source_dir.name or 'archive'}.zip"
    
    try:
        memory_buffer = io.BytesIO()
        with zipfile.ZipFile(memory_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Walk the entire directory tree recursively
            for file_path in source_dir.rglob("*"):
                if file_path.is_file():
                    # write(filename, arcname) creates the relative path layout inside the zip
                    relative_archive_path = file_path.relative_to(source_dir)
                    zf.write(str(file_path), str(relative_archive_path))
                    
        return memory_buffer.getvalue(), archive_name, None
    except Exception as e:
        return None, "", f"FileSystem read exception: {str(e)}"