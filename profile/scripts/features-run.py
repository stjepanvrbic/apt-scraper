"""Pre-run gate of the listing features cron job. Hermes runs cron scripts only from the profile's scripts directory,
so this file hands over to the listing-features skill, which prepares the extraction tasks and prints the wake gate.
It runs on Hermes's own Python, which has Pillow for the contact sheets."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'listing-features' / 'scripts'))

import features  # noqa: E402

sys.exit(features.main(['run']))
