#!/usr/bin/env python3
from pathlib import Path
from config import SCAN_ROOTS, SKIP_DIRS, MAX_DEPTH, MAX_ENTRIES

def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.2f} TB"

def scan_tree(root, depth=0, counter=None):
    if counter is None: counter = [0]
    if counter[0] > MAX_ENTRIES or depth > MAX_DEPTH: return None
    try:
        root = Path(root)
        if not root.is_dir() or root.is_symlink(): return None
        entries = []
        try:
            items = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return None
        for item in items:
            if item.name.startswith(".") or item.name in SKIP_DIRS: continue
            counter[0] += 1
            if counter[0] > MAX_ENTRIES: break
            if item.is_symlink(): continue
            if item.is_dir():
                child = scan_tree(item, depth+1, counter)
                if child is not None: entries.append(child)
        return {"name": root.name or str(root), "path": str(root), "children": entries}
    except Exception:
        return None

def build_fs_tree():
    trees = []
    for root in SCAN_ROOTS:
        p = Path(root)
        if p.exists() and p.is_dir():
            t = scan_tree(p)
            if t: trees.append(t)
    return trees

def list_folder(path):
    p = Path(path)
    if not p.is_dir(): return {"error": "Not a directory"}
    items = []
    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        return {"error": "Permission denied"}
    for entry in entries:
        if entry.name.startswith("."): continue
        try:
            stat = entry.stat()
            items.append({
                "name": entry.name,
                "path": str(entry),
                "is_dir": entry.is_dir(),
                "size": stat.st_size if entry.is_file() else None,
                "size_fmt": fmt_size(stat.st_size) if entry.is_file() else "—",
                "mtime": stat.st_mtime,
                "ext": entry.suffix.lower().lstrip(".") if entry.is_file() else "",
            })
        except Exception:
            continue
    total_files = sum(1 for i in items if not i["is_dir"])
    total_size  = sum(i["size"] for i in items if i["size"] is not None)
    return {
        "path": str(p),
        "name": p.name or str(p),
        "items": items,
        "total_files": total_files,
        "total_size": total_size,
        "total_size_fmt": fmt_size(total_size),
    }