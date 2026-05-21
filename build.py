"""
PyInstaller build script for PC_System_Auto_Scripts.
Usage:
    pip install pyinstaller customtkinter pystray Pillow
    python build.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
NAME = "PC_System_Auto_Scripts"

CMD = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", NAME,
    "--clean",
    "--noconfirm",
    str(BASE_DIR / "main.py"),
]


def main():
    print("Cleaning old build artifacts...")
    for d in [BASE_DIR / "build", DIST_DIR]:
        if d.exists():
            shutil.rmtree(d, onexc=lambda func, path, exc: print(f"  skip locked: {path}"))
    for f in BASE_DIR.glob("*.spec"):
        try:
            f.unlink()
        except OSError:
            pass

    print("Building exe with PyInstaller...")
    result = subprocess.run(CMD, cwd=str(BASE_DIR))
    if result.returncode == 0:
        exe = DIST_DIR / f"{NAME}.exe"
        print(f"\nBuild successful: {exe}")
    else:
        print("\nBuild failed. Check errors above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
