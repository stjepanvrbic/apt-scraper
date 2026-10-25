"""Pre-run gate of the listing ranking cron job. Hermes runs cron scripts only from the profile's scripts directory,
so this file hands over to the ranking skill, which ranks in code and prints the wake gate.

Hermes wakes the model whenever a cron script fails (cron/scheduler_prompt.py, "Script Error"), and this job runs every
two minutes, so the run never fails: a problem is recorded for ranking.py status instead."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'ranking' / 'scripts'))

import ranking  # noqa: E402

sys.exit(ranking.main(['run']))
