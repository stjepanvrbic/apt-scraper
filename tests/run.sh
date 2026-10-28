#!/usr/bin/env bash
# Runs every check on macOS or Linux in a fresh virtual environment: Ruff, the Python suite with no skip the
# platform could run, then the gallery's lint, build, build-output check and Playwright suite.
# Needs Python 3.14, Node 24 and, for the installer tests, bash and perl. CI runs it on fresh machines.
#   tests/run.sh [--work <directory>]     the virtual environment and the test report go in --work, default a new
#                                         directory under TMPDIR
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
WORK=
while [ $# -gt 0 ]; do
    case "$1" in
        --work) WORK=$2; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$WORK" ] || WORK=$(mktemp -d "${TMPDIR:-/tmp}/apt-scraper-tests.XXXXXX")
mkdir -p "$WORK"

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }

step "Virtual environment in $WORK/venv"
"$PYTHON" --version
"$PYTHON" -m venv --clear "$WORK/venv"
# shellcheck disable=SC1091
. "$WORK/venv/bin/activate"
python -m pip install --disable-pip-version-check --quiet -r "$ROOT/tests/requirements.txt"

step 'Ruff'
(cd "$ROOT" && ruff check .)

step 'Python suite'
(cd "$ROOT" && python -m pytest tests -q -rs -p no:cacheprovider --junitxml "$WORK/pytest.xml")
python "$ROOT/tests/skips.py" "$WORK/pytest.xml"

step 'Gallery: packages, lint, build'
cd "$ROOT/gallery"
npm ci --no-audit --no-fund
npm run lint
npm run build
# The committed assets must be the build of the committed source.
if ! git -C "$ROOT" diff --exit-code --stat -- profile/skills/gallery/web; then
    echo 'The gallery build differs from the committed assets in profile/skills/gallery/web. Build and commit them.' >&2
    exit 1
fi

step 'Gallery: Playwright'
if [ "$(uname)" = Linux ]; then
    npx playwright install --with-deps chromium
else
    npx playwright install chromium
fi
npm test

printf '\n\033[32mAll checks passed.\033[0m\n'
