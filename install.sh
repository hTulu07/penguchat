#!/usr/bin/env bash
# Installs Penguchat for the current user (no root needed).
#   ./install.sh         use PyQt6 from your distro's packages
#   ./install.sh --pip   download PyQt6 into a private virtual environment instead
set -euo pipefail
cd "$(dirname "$0")"

APP_ID="io.github.htulu07.Penguchat"
PREFIX="${PREFIX:-$HOME/.local}"
LIB="$PREFIX/lib/penguchat"
PYTHON="$(command -v python3)"

has_deps() { "$1" -c "import PyQt6.QtWebEngineWidgets, PyQt6.QtDBus" 2>/dev/null; }

distro_hint() {
    local ids=""
    [[ -r /etc/os-release ]] && ids="$(. /etc/os-release; echo "${ID:-} ${ID_LIKE:-}")"
    case " $ids " in
        *" fedora "*|*" rhel "*)   echo "sudo dnf install python3-pyqt6 python3-pyqt6-webengine" ;;
        *" debian "*|*" ubuntu "*) echo "sudo apt install python3-pyqt6 python3-pyqt6.qtwebengine" ;;
        *" arch "*)                echo "sudo pacman -S python-pyqt6 python-pyqt6-webengine" ;;
        *" suse "*|*" opensuse "*) echo "sudo zypper install python3-PyQt6 python3-PyQt6-WebEngine" ;;
        *)                         echo "install PyQt6 and PyQt6-WebEngine with your package manager" ;;
    esac
}

if [[ "${1:-}" == "--pip" ]]; then
    echo "Creating a virtual environment in $LIB/venv (downloads about 150 MB)..."
    python3 -m venv "$LIB/venv"
    "$LIB/venv/bin/pip" install --quiet --upgrade pip
    "$LIB/venv/bin/pip" install --quiet -r requirements.txt
    PYTHON="$LIB/venv/bin/python"
elif ! has_deps python3; then
    echo "Penguchat needs PyQt6 and PyQt6-WebEngine. Install them with:"
    echo "  $(distro_hint)"
    echo "or let this script download them for you:"
    echo "  ./install.sh --pip"
    exit 1
fi

install -Dm644 penguchat.py "$LIB/penguchat.py"
mkdir -p "$PREFIX/bin"
printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$PYTHON" "$LIB/penguchat.py" > "$PREFIX/bin/penguchat"
chmod 755 "$PREFIX/bin/penguchat"
install -Dm644 "data/$APP_ID.svg"          "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
# Absolute path: the desktop session's PATH often lacks ~/.local/bin.
sed "s|^Exec=penguchat|Exec=$PREFIX/bin/penguchat|" "data/$APP_ID.desktop" > "$APP_ID.desktop.tmp"
install -Dm644 "$APP_ID.desktop.tmp"       "$PREFIX/share/applications/$APP_ID.desktop"
rm -f "$APP_ID.desktop.tmp"
install -Dm644 "data/$APP_ID.metainfo.xml" "$PREFIX/share/metainfo/$APP_ID.metainfo.xml"

gtk-update-icon-cache -q "$PREFIX/share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
kbuildsycoca6 >/dev/null 2>&1 || true

echo "Penguchat installed. Open it from your app menu or run: penguchat"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Note: $PREFIX/bin is not in your PATH." ;; esac
