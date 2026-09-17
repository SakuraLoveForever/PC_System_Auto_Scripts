"""
PyInstaller build script for PC_System_Auto_Scripts.

Usage:
    pip install -r requirements.txt
    python build.py            # build dist/PC_System_Auto_Scripts.exe
    python build.py --clean    # also remove build/ and the generated spec file

The build only ever deletes artifacts it owns (build/, the PyInstaller spec and
the packaged executable). Runtime data next to the executable — config.json,
startup_backups/, the log file — is preserved, so rebuilding never destroys a
user's configuration or a pending "restore" backup.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
NAME = "PC_System_Auto_Scripts"
SPEC_FILE = BASE_DIR / f"{NAME}.spec"

# Never deleted by a build: the app's own runtime data.
PRESERVED = ("config.json", "startup_backups", "pc_auto_scripts.log")

CMD = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", NAME,
    "--noconfirm",
    "--hidden-import", "pystray",
    "--hidden-import", "PIL",
    "--hidden-import", "PIL._tkinter_finder",
    str(BASE_DIR / "main.py"),
]


def _remove_build_artifacts(clean_all: bool) -> None:
    """Remove only what this script produces; keep the executable by default."""
    exe = DIST_DIR / f"{NAME}.exe"
    if exe.exists():
        try:
            exe.unlink()
            print(f"  removed {exe.relative_to(BASE_DIR)}")
        except OSError as exc:
            print(f"  could not remove {exe.name}: {exc} (is it running?)")
            sys.exit(1)

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, onexc=lambda func, path, exc: print(f"  skip locked: {path}"))

    if clean_all and SPEC_FILE.exists():
        try:
            SPEC_FILE.unlink()
            print(f"  removed {SPEC_FILE.name}")
        except OSError:
            pass


def _report_preserved_data() -> None:
    for name in PRESERVED:
        path = DIST_DIR / name
        if path.exists():
            print(f"  preserved dist/{name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Windows executable.")
    parser.add_argument("--clean", action="store_true",
                        help="also delete the generated .spec file")
    parser.add_argument("--keep-exe", action="store_true",
                        help="do not delete the existing dist executable first")
    args = parser.parse_args()

    DIST_DIR.mkdir(exist_ok=True)
    print("Cleaning build artifacts (runtime data is preserved)...")
    if not args.keep_exe:
        _remove_build_artifacts(args.clean)
    _report_preserved_data()

    print("Building exe with PyInstaller...")
    result = subprocess.run(CMD, cwd=str(BASE_DIR))
    if result.returncode != 0:
        print("\nBuild failed. Check errors above.", file=sys.stderr)
        sys.exit(1)

    exe = DIST_DIR / f"{NAME}.exe"
    print(f"\nBuild successful: {exe}")
    print("Runtime data stays beside the exe: config.json, startup_backups/, "
          "pc_auto_scripts.log")


if __name__ == "__main__":
    main()
