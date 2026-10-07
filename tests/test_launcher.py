import unittest
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_vbs_uses_pythonw_without_console_python_command(self) -> None:
        text = (ROOT / "launch_app.vbs").read_text(encoding="utf-8")
        self.assertIn("pythonw.exe", text)
        self.assertIn("shell.Run command, 0, False", text)

    def _fixture(self, directory):
        app = Path(directory) / "移动后的程序 with spaces"
        app.mkdir()
        (app / "launch_app.vbs").write_bytes((ROOT / "launch_app.vbs").read_bytes())
        (app / "run.py").write_text("", encoding="utf-8")
        return app

    def _runtime(self, folder):
        folder.mkdir(parents=True)
        for name in ("python.exe", "pythonw.exe"):
            (folder / name).write_bytes(b"test fixture: do not execute")

    def _probe(self, app, *arguments):
        result = subprocess.run(["cscript", "//nologo", "//U", str(app / "launch_app.vbs"),
                                 "--print-runtime", *arguments], capture_output=True, timeout=5)
        output = result.stdout.decode("utf-16-le") if b"\0" in result.stdout else result.stdout.decode("utf-8", errors="replace")
        return result.returncode, output.strip()

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_bundled_runtime_wins_over_stale_machine_absolute_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._fixture(tmp)
            self._runtime(app / ".runtime" / "python")
            (app / "config").mkdir()
            (app / "config" / "python_runtime.txt").write_text("Z:/old-computer/python.exe", encoding="utf-16")
            code, output = self._probe(app)
            self.assertEqual(code, 0, output)
            self.assertEqual(Path(output), app / ".runtime" / "python" / "pythonw.exe")

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_stale_record_falls_back_to_local_venv_console_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._fixture(tmp)
            self._runtime(app / ".venv" / "Scripts")
            (app / "config").mkdir()
            (app / "config" / "python_runtime.txt").write_text("Z:/old-computer/python.exe", encoding="utf-16")
            code, output = self._probe(app, "--console")
            self.assertEqual(code, 0, output)
            self.assertEqual(Path(output), app / ".venv" / "Scripts" / "python.exe")

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_relative_recorded_runtime_is_based_on_program_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._fixture(tmp)
            self._runtime(app / "custom runtime")
            (app / "config").mkdir()
            (app / "config" / "python_runtime.txt").write_text("custom runtime/python.exe", encoding="utf-16")
            code, output = self._probe(app)
            self.assertEqual(code, 0, output)
            self.assertEqual(Path(output), app / "custom runtime" / "pythonw.exe")

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_missing_runtime_reports_setup_instruction_without_launching_gui(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._fixture(tmp)
            code, output = self._probe(app)
            self.assertEqual(code, 1)
            self.assertIn("install_offline.ps1", output)


if __name__ == "__main__":
    unittest.main()
