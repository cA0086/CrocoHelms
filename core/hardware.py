#!/usr/bin/env python3
import subprocess

def get_system_specs():
    try:
        # Gracefully check if psutil exists before using it
        import psutil
    except ImportError:
        return {
            "ok": False,
            "missing_dep": "psutil",
            "error": "The 'psutil' library is required to read system metrics."
        }

    try:
        # 1. Check RAM (In Gigabytes)
        vm = psutil.virtual_memory()
        total_ram = round(vm.total / (1024**3), 1)
        avail_ram = round(vm.available / (1024**3), 1)
        
        # 2. Check CPU
        cpu_cores = psutil.cpu_count(logical=False) or 1
        cpu_threads = psutil.cpu_count(logical=True) or 1
        
        # 3. Check for GPU
        has_gpu = False
        gpu_name = "None"
        try:
            gpu_out = subprocess.check_output(["nvidia-smi", "-L"], text=True, timeout=2)
            if gpu_out:
                has_gpu = True
                gpu_name = gpu_out.split("(")[0].strip()
        # FIXED: FileNotFoundError is a built-in Python exception, not part of subprocess
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass

        # 4. Determine AI Readiness Tier
        tier = "micro"
        if total_ram >= 7.5:
            tier = "standard"
        elif total_ram >= 3.5:
            tier = "small"

        return {
            "ok": True,
            "ram_total": total_ram,
            "ram_avail": avail_ram,
            "cpu_cores": cpu_cores,
            "cpu_threads": cpu_threads,
            "has_gpu": has_gpu,
            "gpu_name": gpu_name,
            "tier": tier
        }
    except Exception as e:
        return {"ok": False, "error": f"Failed to read hardware: {str(e)}"}