#!/usr/bin/env bash
# Install Dwarf Fortress (Classic) + DFHack at pinned versions into ./df/
#
# Pinned versions (see README.md "Versions" section before changing):
#   - Dwarf Fortress Classic 53.14 (Linux 64-bit, free version from Bay 12 Games)
#   - DFHack 53.14-r2 (Linux 64-bit)
#
# Usage: bash setup/install.sh [target_dir]   (default target: ./df)
set -euo pipefail

DF_VERSION="53.14"
DF_TARBALL="df_53_14_linux.tar.bz2"
DF_URL="https://www.bay12games.com/dwarves/${DF_TARBALL}"

DFHACK_VERSION="53.14-r2"
DFHACK_TARBALL="dfhack-${DFHACK_VERSION}-Linux-64bit.tar.bz2"
DFHACK_URL="https://github.com/DFHack/dfhack/releases/download/${DFHACK_VERSION}/${DFHACK_TARBALL}"
DFHACK_SHA256="53745714caccd9f3df442fb866b2df57d51454ca7c680fdab9ead6cba5c39d43"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-${REPO_ROOT}/df}"
CACHE="${REPO_ROOT}/setup/cache"

mkdir -p "${CACHE}" "${TARGET}"

fetch() { # fetch <url> <dest> -- skip if already present
    local url="$1" dest="$2"
    if [[ -f "${dest}" ]]; then
        echo "already downloaded: ${dest}"
    else
        echo "downloading ${url}"
        curl -fL --retry 4 --retry-delay 2 -o "${dest}.part" "${url}"
        mv "${dest}.part" "${dest}"
    fi
}

fetch "${DF_URL}" "${CACHE}/${DF_TARBALL}"
fetch "${DFHACK_URL}" "${CACHE}/${DFHACK_TARBALL}"

echo "${DFHACK_SHA256}  ${CACHE}/${DFHACK_TARBALL}" | sha256sum -c -

echo "extracting Dwarf Fortress ${DF_VERSION} -> ${TARGET}"
# Note: unlike pre-v50 releases, the 53.x linux tarball has NO top-level
# wrapper directory (dwarfort, data/, g_src/ etc. are at the root).
tar -xjf "${CACHE}/${DF_TARBALL}" -C "${TARGET}"

echo "extracting DFHack ${DFHACK_VERSION} on top"
tar -xjf "${CACHE}/${DFHACK_TARBALL}" -C "${TARGET}"

# --- init settings for headless operation -----------------------------------
# In DF v50+, user overrides live in prefs/*.txt (defaults are in
# data/init/*_default.txt). PRINT_MODE:TEXT is required for DFHACK_HEADLESS
# (see DFHack Core docs).
mkdir -p "${TARGET}/prefs"
cat > "${TARGET}/prefs/init.txt" <<'EOF'
[PRINT_MODE:TEXT]
[SOUND:NO]
[INTRO:NO]
[WINDOWED:YES]
[FPS_CAP:0]
[G_FPS_CAP:5]
EOF

# Disable DF's own seasonal autosave; the harness takes explicit snapshots.
cat > "${TARGET}/prefs/d_init.txt" <<'EOF'
[AUTOSAVE:NONE]
[AUTOBACKUP:NO]
[PAUSE_ON_LOAD:YES]
[INITIAL_SAVE:NO]
EOF

# Portable mode: keep saves under df/save/ instead of ~/.local/share/Bay 12 Games.
cat > "${TARGET}/prefs/portable.txt" <<'EOF'
This file's presence indicates that this is a portable copy of Dwarf Fortress. You can delete the file or change the setting in-game to change this.
EOF

# Make the harness Lua scripts visible to DFHack.
SCRIPT_PATHS="${TARGET}/dfhack-config/script-paths.txt"
mkdir -p "$(dirname "${SCRIPT_PATHS}")"
LINE="+${REPO_ROOT}/dfhack-scripts"
grep -qxF "${LINE}" "${SCRIPT_PATHS}" 2>/dev/null || echo "${LINE}" >> "${SCRIPT_PATHS}"

# System dependencies: DF links SDL2 even in headless TEXT mode.
if ldd "${TARGET}/dwarfort" 2>/dev/null | grep -q 'not found'; then
    echo
    echo "MISSING SHARED LIBRARIES:" >&2
    ldd "${TARGET}/dwarfort" | grep 'not found' >&2
    echo "On Ubuntu: sudo apt-get install -y libsdl2-2.0-0 libsdl2-image-2.0-0" >&2
    exit 1
fi

echo
echo "Installed DF ${DF_VERSION} + DFHack ${DFHACK_VERSION} in ${TARGET}"
echo "Next: python -m setup.make_world  (generates the pinned world + embark save)"
