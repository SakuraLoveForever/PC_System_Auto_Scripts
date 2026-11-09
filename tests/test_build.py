"""The installed app must start without unpacking itself into TEMP."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build


class BuildTests(unittest.TestCase):
    def test_directory_build_keeps_executable_path_and_user_config(self):
        self.assertIn("--onedir", build.CMD)
        self.assertNotIn("--onefile", build.CMD)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            (dist / "config.json").write_text("user settings")
            package = root / "package" / build.NAME
            (package / "_internal").mkdir(parents=True)
            (package / f"{build.NAME}.exe").write_bytes(b"exe")
            (package / "_internal" / "runtime.dll").write_bytes(b"runtime")
            with mock.patch.object(build, "DIST_DIR", dist), \
                 mock.patch.object(build, "PACKAGE_DIR", package.parent), \
                 mock.patch.object(build.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                 mock.patch.object(build.sys, "argv", ["build.py", "--keep-exe"]):
                build.main()
                run.assert_any_call(["icacls", str(dist / f"{build.NAME}.exe"),
                                     "/setintegritylevel", "M"], check=True, capture_output=True, text=True)
            self.assertEqual((dist / f"{build.NAME}.exe").read_bytes(), b"exe")
            self.assertTrue((dist / "_internal" / "runtime.dll").is_file())
            self.assertEqual((dist / "config.json").read_text(), "user settings")
