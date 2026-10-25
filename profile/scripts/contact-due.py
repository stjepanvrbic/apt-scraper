"""Pre-run gate of the listing correspondence cron job. Hermes runs cron scripts only from the profile's
scripts directory, so this file hands over to the contact skill, which records new replies and prints
the wake gate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'contact' / 'scripts'))

import contact  # noqa: E402

sys.exit(contact.main(['due']))
