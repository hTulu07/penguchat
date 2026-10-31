#!/usr/bin/env bash
# Removes Penguchat. Pass --purge to also delete your login session and settings.
set -euo pipefail
APP_ID="io.github.htulu07.Penguchat"
PREFIX="${PREFIX:-$HOME/.local}"
rm -rf "$PREFIX/lib/penguchat"
rm -f "$PREFIX/bin/penguchat" \
      "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg" \
      "$PREFIX/share/applications/$APP_ID.desktop" \
      "$PREFIX/share/metainfo/$APP_ID.metainfo.xml" \
      "$HOME/.config/autostart/$APP_ID.desktop"
if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$HOME/.local/share/penguchat" "$HOME/.cache/penguchat" "$HOME/.config/penguchat"
fi
kbuildsycoca6 >/dev/null 2>&1 || true
echo "Penguchat removed."
