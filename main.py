#!/usr/bin/env python3
import threading
import time
import webbrowser
from http.server import HTTPServer

import config
from server.handlers import Handler, get_tree


def print_update_token():
    print()
    print("╔══════════════════════════════════════════╗")
    print("║  Update token for browser dashboard     ║")
    print(f"║  {config.UPDATE_TOKEN:<34}║")
    print("║  Enter this token in the dashboard      ║")
    print("╚══════════════════════════════════════════╝")
    print()

def open_browser(url):
    time.sleep(1.3)
    try: webbrowser.open(url)
    except: pass

def main():
    print()
    print("╔══════════════════════════════════════════╗")
    print("║         VM Download Manager              ║")
    print("╠══════════════════════════════════════════╣")
    if config.DOWNLOADER: 
        print(f"║  Downloader  : {config.DOWNLOADER:<26}║")
    else:
        print("║  ⚠  No downloader found!                ║")
        print("║     apt install aria2 wget curl          ║")
    url = f"http://127.0.0.1:{config.PORT}"
    print(f"║  Address     : {url:<26}║")
    print("╚══════════════════════════════════════════╝\n")
    print_update_token()
    
    threading.Thread(target=get_tree, daemon=True).start()
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    
    server = HTTPServer((config.HOST, config.PORT), Handler)
    try:
        print("  Running — Ctrl+C to stop\n")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.shutdown()

if __name__ == "__main__":
    main()