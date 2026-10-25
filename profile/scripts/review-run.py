"""Pre-run gate of the ranking review cron job. Hermes runs cron scripts only from the profile's scripts directory, so
this file hands over to the ranking skill, which writes the task of a waiting review and prints the wake gate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'ranking' / 'scripts'))

import ranking  # noqa: E402

sys.exit(ranking.main(['run']))
