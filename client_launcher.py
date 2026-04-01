"""
Deed & Plat Helper — Client Launcher

For office computers that connect to the host machine running the server.
Opens a pywebview native window (or falls back to the default browser)
pointed at the server address.

Usage:
    python client_launcher.py                          # auto-discover or prompt
    python client_launcher.py --server 192.168.1.50    # direct connect
    python client_launcher.py --server 192.168.1.50 --port 5000
"""

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_ROOT / "client_config.json"


def load_client_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_client_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def discover_server(port: int = 5000, timeout: float = 3.0) -> str | None:
    """
    Try to find the Deed & Plat Helper server on common LAN addresses.
    Scans the local subnet for a responding server.
    """
    try:
        # Get our own IP to determine the subnet
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        my_ip = s.getsockname()[0]
        s.close()
    except Exception:
        return None

    # Scan common IPs in the same /24 subnet
    subnet = ".".join(my_ip.split(".")[:3])
    print(f"[discover] Scanning {subnet}.* for Deed & Plat Helper server on port {port}...")

    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        if ip == my_ip:
            continue
        try:
            sock = socket.create_connection((ip, port), timeout=0.15)
            sock.close()
            print(f"[discover] Found server at {ip}:{port}")
            return ip
        except (OSError, socket.timeout):
            continue
    return None


def check_server(host: str, port: int) -> bool:
    """Quick check if the server is responding."""
    try:
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Deed & Plat Helper Client")
    parser.add_argument("--server", type=str, help="Server IP address")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default 5000)")
    parser.add_argument("--browser", action="store_true",
                        help="Open in default browser instead of native window")
    args = parser.parse_args()

    port = args.port
    server = args.server

    # Load saved config
    cfg = load_client_config()

    if not server:
        server = cfg.get("server_address", "")

    # If no server address, try auto-discovery
    if not server:
        print("[client] No server address configured. Attempting auto-discovery...")
        server = discover_server(port)

    if not server:
        # Prompt the user
        print("\n" + "=" * 50)
        print("  Could not auto-discover the server.")
        print("  Enter the IP address of the host computer")
        print("  (the machine running Deed & Plat Helper server)")
        print("=" * 50)
        server = input("\n  Server IP: ").strip()
        if not server:
            print("[ERROR] No server address provided. Exiting.")
            sys.exit(1)

    # Check connectivity
    print(f"[client] Connecting to {server}:{port}...")
    if not check_server(server, port):
        print(f"[ERROR] Cannot reach server at {server}:{port}")
        print("        Make sure the host machine is running the Deed & Plat Helper server.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    # Save for next time
    cfg["server_address"] = server
    cfg["port"] = port
    save_client_config(cfg)

    url = f"http://{server}:{port}"
    print(f"[OK] Connected to server at {url}")

    # Open window
    if not args.browser:
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
            return
        except ImportError:
            print("[info] pywebview not installed — falling back to browser")

    import webbrowser
    webbrowser.open(url)
    print(f"\n  App opened in browser at {url}")
    print("  Press Ctrl+C to exit this launcher.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
