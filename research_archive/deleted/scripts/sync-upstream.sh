#!/usr/bin/env bash
# Merge upstream FirstLight_CR into this fork and push.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

echo "Fetching upstream..."
git fetch upstream --prune

git checkout main
echo "Merging upstream/main..."
git merge --no-edit upstream/main
echo "Pushing origin..."
git push origin main
echo "Done."
