#!/usr/bin/env python3
import threading

# Thread locks
dl_lock = threading.Lock()
ul_lock = threading.Lock()
tree_lock = threading.Lock()

# Global Registries
downloads = {}
uploads = {}
cached_tree = None