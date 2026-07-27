import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SetupScriptTests(unittest.TestCase):
    def test_setup_and_access_support_arbitrary_project_path(self):
        with tempfile.TemporaryDirectory(prefix="mil portal ") as raw_directory:
            root = Path(raw_directory) / "custom install"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(PROJECT_ROOT / ".env.example", root / ".env.example")
            shutil.copy2(PROJECT_ROOT / "scripts/setup.sh", scripts / "setup.sh")
            shutil.copy2(PROJECT_ROOT / "scripts/access.sh", scripts / "access.sh")

            environment = {
                **os.environ,
                "HOME": str(root / "fake home"),
                "USER": "test-user",
                "PORTAL_PORT_BASE": "30000",
                "PORTAL_SKIP_CLI_LINK": "1",
                "PORTAL_SKIP_PORT_CHECK": "1",
            }
            first = subprocess.run(
                ["bash", str(scripts / "setup.sh")],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            env_file = root / ".env"
            self.assertTrue(env_file.is_file())
            port_line = next(
                line
                for line in env_file.read_text(encoding="utf-8").splitlines()
                if line.startswith("PORTAL_PORT=")
            )
            remote_port = port_line.split("=", 1)[1]
            self.assertTrue(remote_port.isdigit())
            self.assertIn(str(root), first.stdout)

            second = subprocess.run(
                ["bash", str(scripts / "setup.sh")],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertIn("Existing configuration preserved", second.stdout)
            self.assertIn(f"Remote portal port: {remote_port}", second.stdout)

            access = subprocess.run(
                ["bash", str(scripts / "access.sh"), "lab-server", "19000"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertIn("http://127.0.0.1:19000", access.stdout)
            self.assertIn(
                f"127.0.0.1:19000:127.0.0.1:{remote_port}",
                access.stdout,
            )
            self.assertIn("lab-server", access.stdout)
            self.assertIn("Windows PowerShell/CMD", access.stdout)

            default_access = subprocess.run(
                ["bash", str(scripts / "access.sh")],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertIn("test-user@210.125.181.82", default_access.stdout)

            alternate_port = subprocess.run(
                ["bash", str(scripts / "access.sh"), "19001"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertIn("http://127.0.0.1:19001", alternate_port.stdout)
            self.assertIn("test-user@210.125.181.82", alternate_port.stdout)


if __name__ == "__main__":
    unittest.main()
