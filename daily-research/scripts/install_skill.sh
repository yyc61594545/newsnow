#!/usr/bin/env bash
# Best-effort install of the last30days skill into a project skills dir.
# Runs on the GitHub Actions runner (which has internet). If anything fails
# the workflow continues and the deterministic fallback aggregator is used.
set -uo pipefail

DEST="${1:-.claude/skills}"
REPO="https://github.com/mvanhorn/last30days-skill"

mkdir -p "$DEST"
TMP="$(mktemp -d)"

echo "[install_skill] cloning $REPO ..."
if git clone --depth 1 "$REPO" "$TMP/last30days-skill" 2>&1; then
  SRC="$TMP/last30days-skill/skills/last30days"
  if [ -d "$SRC" ]; then
    rm -rf "$DEST/last30days"
    cp -r "$SRC" "$DEST/last30days"
    echo "[install_skill] installed skill -> $DEST/last30days"
    if [ -f "$DEST/last30days/requirements.txt" ]; then
      echo "[install_skill] installing skill python deps (best-effort)"
      pip install -r "$DEST/last30days/requirements.txt" || true
    fi
  else
    echo "[install_skill] expected skill path not found ($SRC); will rely on fallback"
  fi
else
  echo "[install_skill] clone failed; will rely on fallback aggregator"
fi

rm -rf "$TMP"
exit 0
