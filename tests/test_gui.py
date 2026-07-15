import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_collector.gui import App, application_root


class GuiPathTests(unittest.TestCase):
    def test_app_does_not_override_tkinter_options_helper(self):
        self.assertNotIn("_options", App.__dict__)

    def test_source_mode_uses_project_root(self):
        self.assertEqual(application_root(), Path(__file__).resolve().parent.parent)

    def test_frozen_mode_uses_executable_directory(self):
        executable = Path("C:/Tools/collector.exe")
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(executable)
        ):
            self.assertEqual(application_root(), executable.parent)


if __name__ == "__main__":
    unittest.main()
