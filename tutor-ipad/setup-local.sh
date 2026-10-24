#!/bin/sh
# Writes the machine-specific config files (absolute cache paths inside this folder) from their templates.
# They are git-ignored: an absolute path contains the user's home folder.
set -eu
REPO=$(cd "$(dirname "$0")" && pwd)
for f in uv.toml tutor-app/apps/web/.npmrc; do
  sed "s#@REPO@#$REPO#g" "$REPO/$f.example" > "$REPO/$f"
  echo "wrote $f"
done
