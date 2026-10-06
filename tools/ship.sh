#!/bin/bash
# Gate: battery green locally and the published wiki fresh -> merge -> CONFIRM the CI run of the merge commit.
# Usage: tools/ship.sh <branch-or-pr>
set -e
python3 -m pytest tests/ -q --tb=line || { echo "BATTERY RED — NOT MERGING"; exit 1; }
bash tools/check_wiki_fresh.sh || { echo "WIKI STALE — run the generators, push the wiki, then ship"; exit 1; }
gh pr merge --repo ISAAC-DOE/isaac-ai-ready-record "$1" --squash --delete-branch
sha=$(gh pr view --repo ISAAC-DOE/isaac-ai-ready-record "$1" --json mergeCommit --jq .mergeCommit.oid)
echo "merged as ${sha:0:7} — waiting for the CI run of that commit..."
# Wait for the run of the merge commit itself: the newest completed run on main can be the previous merge's.
for i in $(seq 1 45); do
  result=$(gh run list --repo ISAAC-DOE/isaac-ai-ready-record --workflow "Validation Battery" --commit "$sha" --json status,conclusion --jq '.[0] | select(.status=="completed") | .conclusion' 2>/dev/null)
  if [ -n "$result" ]; then
    echo "MAIN CI (${sha:0:7}): $result"
    [ "$result" = "success" ] || { echo "MAIN CI RED — investigate now, do not walk away"; exit 1; }
    exit 0
  fi
  sleep 20
done
echo "MAIN CI (${sha:0:7}): still running after 15 min — check manually"
