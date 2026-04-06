"""
Create desktop shortcuts for Deed & Plat Helper.

Run once after installation:
    python create_shortcut.py

Creates:
  - "Deed & Plat Helper.lnk"          (host mode — starts server + window)
  - "Deed & Plat Helper (Client).lnk" (client mode — connects to host)
"""

import os
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DESKTOP = Path(os.path.expanduser("~")) / "Desktop"


def find_python() -> str:
    """Find the project's Python executable."""
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv_py.exists():
        return str(venv_py)
    venv_py2 = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py2.exists():
        return str(venv_py2)
    return sys.executable


def create_ico():
    """Convert favicon.png to .ico for the shortcut icon."""
    png_path = PROJECT_ROOT / "favicon.png"
    ico_path = PROJECT_ROOT / "app_icon.ico"
    if ico_path.exists():
        return str(ico_path)
    if not png_path.exists():
        return ""
    try:
        from PIL import Image
        img = Image.open(png_path)
        # Create multiple sizes for best quality
        img.save(str(ico_path), format='ICO',
                 sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print(f"[OK] Created icon: {ico_path}")
        return str(ico_path)
    except Exception as e:
        print(f"[WARN] Could not create .ico: {e}")
        return ""


def create_shortcut(name: str, target_script: str, arguments: str = "",
                    icon_path: str = "", description: str = ""):
    """Create a Windows .lnk shortcut on the Desktop using PowerShell."""
    lnk_path = DESKTOP / f"{name}.lnk"
    python_exe = find_python()

    # Use PowerShell to create the shortcut (most reliable on Windows)
    ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("{lnk_path}")
$sc.TargetPath = "{python_exe}"
$sc.Arguments = '"{target_script}" {arguments}'
$sc.WorkingDirectory = "{PROJECT_ROOT}"
$sc.Description = "{description}"
$sc.WindowStyle = 7
'''
    if icon_path and os.path.exists(icon_path):
        ps_script += f'$sc.IconLocation = "{icon_path},0"\n'

    ps_script += "$sc.Save()\n"

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print(f"[OK] Shortcut created: {lnk_path}")
            return True
        else:
            print(f"[ERROR] PowerShell error: {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"[ERROR] Failed to create shortcut: {e}")
        return False


def main():
    print("=" * 55)
    print("  Deed & Plat Helper — Shortcut Creator")
    print("=" * 55)
    print()

    # Create .ico from favicon
    ico = create_ico()

    # Host shortcut (starts server + opens window)
    host_script = PROJECT_ROOT / "desktop_app.py"
    ok1 = create_shortcut(
        name="Deed & Plat Helper",
        target_script=str(host_script),
        icon_path=ico,
        description="Launch Deed & Plat Helper (Server + App)"
    )

    # Client shortcut (connects to host)
    client_script = PROJECT_ROOT / "client_launcher.py"
    ok2 = create_shortcut(
        name="Deed & Plat Helper (Client)",
        target_script=str(client_script),
        icon_path=ico,
        description="Connect to Deed & Plat Helper on the network"
    )

    print()
    if ok1 or ok2:
        print("  ✅ Shortcuts created on your Desktop!")
        print()
        print("  • 'Deed & Plat Helper'          — Run on the main server machine")
        print("  • 'Deed & Plat Helper (Client)'  — Run on other office computers")
    else:
        print("  ❌ Shortcut creation failed. You can manually create shortcuts to:")
        print(f"     {host_script}")
        print(f"     {client_script}")

    print()
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
