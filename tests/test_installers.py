import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def steps(installer: str) -> list[str]:
    text = (ROOT / installer).read_text(encoding='utf-8')
    pattern = r"Write-Step '([^']+)'" if installer.endswith('.ps1') else r"^step '([^']+)'"
    return re.findall(pattern, text, re.MULTILINE)


class InstallerTests(unittest.TestCase):
    def test_both_installers_run_the_same_steps_in_the_same_order(self):
        windows = [step for step in steps('install.ps1') if not step.startswith('Log in to Claude')]
        macos = [step for step in steps('install.sh')
                 if not step.startswith(('Log in to Claude', 'Grant Accessibility'))]
        self.assertEqual(windows, macos)

    def test_both_installers_end_by_opening_the_profile_chat_in_the_dashboard(self):
        for installer in ('install.ps1', 'install.sh'):
            self.assertEqual(steps(installer)[-1], 'Hermes web dashboard', installer)
            text = (ROOT / installer).read_text(encoding='utf-8')
            self.assertIn('/chat?profile=', text, installer)
            self.assertIn('HERMES_DASHBOARD_READY port=', text, installer)


if __name__ == '__main__':
    unittest.main()
