"""Isolated setup/package checks; never starts THS or changes a real profile."""

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from desktop_setup import component_executable, configure_portable
from package_release import package


class PortableSetupSmoke(unittest.TestCase):
    def test_installer_targets_reader_and_only_writes_selected_user_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            reader = root / "THSTeacher.exe"
            reader.write_bytes(b"offline test only, never executable")
            installer = root / "安装.exe"
            installer.write_bytes(b"offline test only")
            ths = root / "hexin.exe"
            ths.write_bytes(b"offline test only")
            locations = dict(state_dir=root / "state", desktop_dir=root / "desktop", programs_dir=root / "programs")
            preview = configure_portable(installer, str(ths), dry_run=True, **locations)
            self.assertEqual(component_executable(installer), reader)
            self.assertEqual(Path(preview["exe_path"]), reader)
            self.assertFalse((root / "state").exists())
            installed = configure_portable(installer, str(ths), **locations)
            self.assertTrue(installed["verified"])
            config = json.loads((root / "state" / "board_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["board_name"], "老师消息")
            self.assertEqual(Path(config["ths_exe"]), ths)
            self.assertEqual(len(list((root / "desktop").glob("*.lnk"))), 1)

    def test_release_excludes_parent_profile_and_contains_no_code_install_steps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            app = root / "app"
            (app / "_internal").mkdir(parents=True)
            (app / "THSTeacher.exe").write_bytes(b"fixture")
            (app / "_internal" / "runtime.dll").write_bytes(b"fixture runtime")
            (root / "webview-profile").mkdir()
            (root / "webview-profile" / "private-session").write_bytes(b"never package")
            archive = package(app, root / "out", "v0.2.0-test")
            with zipfile.ZipFile(archive) as zipped:
                self.assertIn("THSTeacher/安装.exe", zipped.namelist())
                self.assertIn("THSTeacher/使用说明.txt", zipped.namelist())
                self.assertFalse(any("profile" in name for name in zipped.namelist()))
                self.assertEqual(zipped.read("THSTeacher/安装.exe"), zipped.read("THSTeacher/THSTeacher.exe"))
            self.assertTrue(archive.with_suffix(".sha256").is_file())
            with self.assertRaises(FileExistsError):
                package(app, root / "out", "v0.2.0-test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
