#!/usr/bin/env python3
import io
import stat
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


def extract_zip_archive(archive_path_str: str, destination_path_str: str) -> tuple[dict, int]:
    archive_path = Path(archive_path_str).resolve()
    destination = Path(destination_path_str).resolve()

    if not archive_path.is_file() or archive_path.suffix.lower() != ".zip":
        return {"error": "Select a valid ZIP archive."}, 400
    if not destination.is_dir():
        return {"error": "Destination path is not a valid directory."}, 400

    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = []
            for info in archive.infolist():
                member_path = Path(info.filename)
                if not info.filename or "\\" in info.filename or member_path.is_absolute() or ".." in member_path.parts:
                    return {"error": "Archive contains an unsafe file path."}, 400

                target = (destination / member_path).resolve()
                if not target.is_relative_to(destination):
                    return {"error": "Archive contains an unsafe file path."}, 400
                if stat.S_ISLNK(info.external_attr >> 16):
                    return {"error": "Archive contains a symbolic link, which cannot be extracted."}, 400
                if target.exists():
                    return {"error": f"An item named '{member_path}' already exists in the destination."}, 409
                members.append((info, target))

            for info, target in members:
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    output.write(source.read())

        return {"ok": True, "message": f"Extracted {archive_path.name} successfully."}, 200
    except zipfile.BadZipFile:
        return {"error": "The selected file is not a valid ZIP archive."}, 400
    except Exception as exc:
        return {"error": f"Extraction failed: {exc}"}, 500