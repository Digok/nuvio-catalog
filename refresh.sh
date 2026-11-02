#!/bin/bash
# Weekly: rebuild catalog and publish to GitHub Pages (docs/ on main).
set -euo pipefail
cd "$(dirname "$0")"
env -u PYTHONPATH /usr/bin/python3.12 build.py > data/last-build.log 2>&1
git add -A docs build.py
git diff --cached --quiet || { git commit -qm "chore: weekly catalog refresh"; git push -q origin main; }
