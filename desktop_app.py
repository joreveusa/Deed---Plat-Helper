"""
Deed & Plat Helper — Desktop App Launcher (Host Mode)

Starts the Flask server in a background thread and opens the app
in a native-looking window (Chrome/Edge --app mode or pywebview).
A system-tray icon (via pystray) keeps the app accessible when
the window is closed.

Usage:
    python desktop_app.py              # normal launch
    python desktop_app.py --port 5000  # custom port
    python desktop_app.py --browser    # use default browser
"""

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# Ensure the project root is on sys.path so `import app` works
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def get_local_ip() -> str:
    """Best-effort detection of the machine's LAN IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def wait_for_server(host: str, port: int, timeout: float = 20.0) -> bool:
    """Poll until the Flask server is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def start_flask(port: int):
    """Import and run the Flask app (blocking — call in a thread)."""
    from app import app
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.WARNING)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True,
            use_reloader=False)


def find_chrome_or_edge() -> str | None:
    """Find Chrome or Edge executable for --app mode."""
    candidates = []

    # Edge (preferred on Windows — always installed)
    edge_paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    candidates.extend(edge_paths)

    # Chrome
    chrome_paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    candidates.extend(chrome_paths)

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def open_app_window(url: str):
    """Open the app in a native-feeling window (no URL bar, no browser chrome).

    Strategy:
    1. Try pywebview (true native window) — needs compatible backend
    2. Fall back to Chrome/Edge --app mode (looks native, widely available)
    3. Fall back to default browser
    """
    # Strategy 1: pywebview
    try:
        import webview
        window = webview.create_window(
            "Deed & Plat Helper",
            url,
            width=1400,
            height=900,
            min_size=(900, 600),
        )
        webview.start()
        return True
    except ImportError:
        pass
    except Exception as e:
        print(f"[info] pywebview failed ({e}) — trying Chrome/Edge app mode")

    # Strategy 2: Chrome/Edge --app mode
    browser = find_chrome_or_edge()
    if browser:
        print(f"[OK] Opening in app mode: {os.path.basename(browser)}")
        # --app flag removes URL bar and browser chrome
        subprocess.Popen([
            browser,
            f"--app={url}",
            "--new-window",
            f"--window-size=1400,900",
            "--disable-features=TranslateUI",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    # Strategy 3: default browser
    print("[info] No Chrome/Edge found — opening in default browser")
    import webbrowser
    webbrowser.open(url)
    return True


def create_tray_icon(port: int, on_open_cb):
    """Create a pystray system-tray icon with Open / Quit options."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        print("[tray] pystray or Pillow not installed — skipping tray icon")
        return None

    icon_path = PROJECT_ROOT / "favicon.png"
    try:
        img = Image.open(icon_path).resize((64, 64))
    except Exception:
        img = Image.new("RGB", (64, 64), (50, 120, 200))

    lan_ip = get_local_ip()

    def on_open(icon, item):
        on_open_cb()

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem(f"Open  (http://localhost:{port})", on_open, default=True),
        pystray.MenuItem(f"Network: http://{lan_ip}:{port}", lambda *a: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Deed & Plat Helper", on_quit),
    )

    icon = pystray.Icon("DeedHelper", img, "Deed & Plat Helper", menu)
    return icon


def main():
    parser = argparse.ArgumentParser(description="Deed & Plat Helper Desktop App")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default 5000)")
    parser.add_argument("--no-tray", action="store_true", help="Skip system tray icon")
    parser.add_argument("--browser", action="store_true",
                        help="Open in default browser instead of app window")
    args = parser.parse_args()

    port = args.port
    url = f"http://localhost:{port}"
    lan_ip = get_local_ip()

    # ── Start Flask server in a daemon thread ──────────────────────────────
    print("=" * 60)
    print(f"  Deed & Plat Helper  —  Desktop Mode")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Network: http://{lan_ip}:{port}")
    print("=" * 60)

    flask_thread = threading.Thread(target=start_flask, args=(port,), daemon=True)
    flask_thread.start()

    if not wait_for_server("127.0.0.1", port):
        print("[ERROR] Flask server did not start in time.")
        sys.exit(1)

    print(f"[OK] Server ready on port {port}")

    # ── System tray ────────────────────────────────────────────────────────
    tray_icon = None
    if not args.no_tray:
        tray_icon = create_tray_icon(port, lambda: open_app_window(url))

    if tray_icon:
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()

    # ── Open the main window ──────────────────────────────────────────────
    if args.browser:
        import webbrowser
        webbrowser.open(url)
    else:
        open_app_window(url)

    # Keep the process alive for tray + server
    print("[info] Server running. Right-click tray icon or Ctrl+C to quit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[info] Shutting down...")


if __name__ == "__main__":
    main()
