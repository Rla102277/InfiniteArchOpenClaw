import plistlib
import unittest
from pathlib import Path

from aihq import service


class ServiceTests(unittest.TestCase):
    def test_os_plist_is_a_valid_keepalive_agent(self):
        data = service.os_plist(Path("/tmp/os"), 8731)
        plistlib.dumps(data)
        self.assertEqual(data["Label"], service.OS_LABEL)
        self.assertEqual(data["ProgramArguments"][1:], ["-m", "aihq.cli", "start", "--no-browser", "--port", "8731"])
        self.assertEqual(data["WorkingDirectory"], "/tmp/os")
        self.assertTrue(data["RunAtLoad"])
        self.assertEqual(data["KeepAlive"], {"SuccessfulExit": False})
        self.assertIn("/opt/homebrew/bin", data["EnvironmentVariables"]["PATH"])

    def test_darkroom_plist_opens_the_app_with_control_port(self):
        data = service.darkroom_plist(7980)
        plistlib.dumps(data)
        self.assertEqual(data["ProgramArguments"][-2:], ["--control", "7980"])


if __name__ == "__main__":
    unittest.main()
