"""Runs the dashboard front-end tests (tests/ui/*.js) with Node.js when it is installed; skipped otherwise.

  helpers.test.js  pure helpers: dates, links (XSS-safe), merging messages, sizes
  smoke.test.js    the whole app in a tiny fake DOM with a fake backend: chat list + tabs, search, open chat, every message type,
                   reply + send, menus, all settings sections (profile, notifications, privacy, chat, contacts, AI bot, AI log)
"""
import os
import shutil
import subprocess
import unittest

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestDashboardFrontend(unittest.TestCase):
    def run_js(self, name):
        r = subprocess.run(["node", os.path.join(UI, name)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)

    def test_helpers(self): self.run_js("helpers.test.js")
    def test_app_smoke(self): self.run_js("smoke.test.js")
    def test_syntax(self):
        r = subprocess.run(["node", "--check", os.path.join(os.path.dirname(UI), "..", "web", "app.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
