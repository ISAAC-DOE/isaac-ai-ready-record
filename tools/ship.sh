#!/bin/bash
# Gate: battery must be green before any merge. Usage: tools/ship.sh <branch>
set -e
python3 -m pytest tests/test_validation_battery.py -q --tb=line || { echo "BATTERY RED — NOT MERGING"; exit 1; }
gh pr merge --repo ISAAC-DOE/isaac-ai-ready-record "$1" --squash --delete-branch
