#!/usr/bin/env bash
set -euo pipefail

# Deploy the local integration from this repo into a Home Assistant config volume.
# Best practice: NEVER edit the target folder directly; always edit in this repo
# and re-deploy.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$REPO_ROOT/custom_components/bloomin8_eink_canvas"
DEST_DIR_DEFAULT="/Volumes/config/custom_components/bloomin8_eink_canvas"

DEST_DIR="${1:-$DEST_DIR_DEFAULT}"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Source integration folder not found: $SRC_DIR" >&2
  exit 2
fi

# Create target parent directory if needed.
mkdir -p "$(dirname "$DEST_DIR")"

# Refuse to deploy into the repo itself (safety net).
if [[ "$DEST_DIR" == "$SRC_DIR" ]]; then
  echo "Refusing to deploy: destination equals source." >&2
  exit 3
fi

echo "Deploying '$SRC_DIR' -> '$DEST_DIR'"

# Mirror source into destination.
# -a: archive, preserves timestamps/permissions where possible
# --delete: remove stale files in destination
# --inplace: avoid dot-prefixed temp files on some mounted volumes/filesystems
# Excludes: caches and local dev artifacts
rsync -a --delete --inplace \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$SRC_DIR/" "$DEST_DIR/"

echo "Done."
