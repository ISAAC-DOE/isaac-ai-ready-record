#!/bin/bash
# Gate: battery green locally -> merge -> CONFIRM post-merge CI on main.
# Usage: tools/ship.sh <branch-or-pr>
set -e
python3 -m pytest tests/test_validation_battery.py -q --tb=line || { echo "BATTERY RED — NOT MERGING"; exit 1; }
gh pr merge --repo ISAAC-DOE/isaac-ai-ready-record "$1" --squash --delete-branch
echo "merged — waiting for main CI conclusion..."
for i in $(seq 1 30); do
  result=$(gh run list --repo ISAAC-DOE/isaac-ai-ready-record --workflow "Validation Battery" --branch main --limit 1 --json status,conclusion --jq '.[0] | select(.status=="completed") | .conclusion' 2>/dev/null)
  if [ -n "$result" ]; then
    echo "MAIN CI: $result"
    [ "$result" = "success" ] || { echo "MAIN CI RED — investigate now, do not walk away"; exit 1; }
    exit 0
  fi
  sleep 20
done
echo "MAIN CI: still running after 10 min — check manually"
