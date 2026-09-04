#!/usr/bin/env bash
# Package a player-facing Gundam Wing Endless Duel release.
#
# The zip contains the built executable, the launcher's runtime assets, the
# localization mods, and the shared libraries the binary needs. It never
# contains the ROM, generated C, or anything else derived from copyrighted
# data — players bring their own ROM and the binary reads it at launch.
#
# Usage:
#   scripts/package_release.sh [<build-dir>] [<platform-tag>]
#
# Both arguments are optional; BUILD_DIR and PLATFORM still work as env vars
# so an existing local habit keeps working. Defaults: build, and the host's
# own <os>-<arch>.
#
# Writes: dist/gundamwingendlessduel-<VERSION>-<platform-tag>.zip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

BUILD_DIR="${1:-${BUILD_DIR:-build}}"
PLATFORM="${2:-${PLATFORM:-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)}}"
VERSION="$(tr -d '[:space:]' < VERSION)"
if [ -z "$VERSION" ]; then
  echo "package_release.sh: VERSION is empty" >&2
  exit 1
fi

NAME="gundamwingendlessduel-$VERSION-$PLATFORM"
STAGE="dist/$NAME"
EXE="$BUILD_DIR/GundamWingEndlessDuelSNESRecomp"
[ -f "$EXE.exe" ] && EXE="$EXE.exe"

if [ ! -f "$EXE" ]; then
  echo "package_release.sh: $EXE not found — build first:" >&2
  echo "  cmake -S . -B $BUILD_DIR -DCMAKE_BUILD_TYPE=Release && cmake --build $BUILD_DIR -j" >&2
  exit 1
fi
EXE_DIR="$(cd "$(dirname "$EXE")" && pwd)"

rm -rf "$STAGE"
mkdir -p "$STAGE"
cp "$EXE" "$STAGE/"
cp README.md LICENSE VERSION "$STAGE/" 2>/dev/null || true

# Launcher runtime assets (fonts, chrome, boxart) are staged into the BUILD
# dir by recomp_ui.cmake POST_BUILD — that copy is the one beside the exe at
# runtime, so it is the one that ships. Repo-root assets/ is the fallback for
# builds without the launcher.
if [ -d "$EXE_DIR/assets" ]; then
  cp -R "$EXE_DIR/assets" "$STAGE/"
elif [ -d assets ] && [ -n "$(ls -A assets 2>/dev/null)" ]; then
  cp -R assets "$STAGE/"
fi
if [ ! -f "$STAGE/assets/fonts/LatoLatin-Regular.ttf" ]; then
  echo "package_release.sh: launcher fonts missing from $EXE_DIR/assets —" >&2
  echo "  the launcher would start with no text. Rebuild the game target." >&2
  exit 1
fi

# Localization: mods/ is the package a netplay host transfers to a guest, and
# translations/ is the table it reads. CMake stages both beside the exe; ship
# what the build actually produced rather than the repo copies, so a stale
# build cannot masquerade as a current one.
for d in mods translations; do
  if [ -d "$EXE_DIR/$d" ]; then
    cp -R "$EXE_DIR/$d" "$STAGE/"
  elif [ -d "$d" ]; then
    cp -R "$d" "$STAGE/"
  fi
done
if [ ! -f "$STAGE/mods/preloaded/packages/gwed.localization/1.0.0/endless_duel.toml" ]; then
  echo "package_release.sh: localization package incomplete — a guest that" >&2
  echo "  downloads this mod would run untranslated while reporting the same" >&2
  echo "  mod set as the host. Build the StageLocalization target." >&2
  exit 1
fi

# Shared libraries. Without this the zip runs on the machine that built it and
# nowhere else; the framework owns the per-platform rules.
BUNDLE="snesrecomp/tools/ci/bundle_runtime_libs.sh"
if [ -f "$BUNDLE" ]; then
  bash "$BUNDLE" --exe "$STAGE/$(basename "$EXE")" --build-dir "$EXE_DIR"
else
  echo "package_release.sh: $BUNDLE missing (old snesrecomp pin) —" >&2
  echo "  the zip may depend on libraries only this machine has." >&2
fi

cat > "$STAGE/README.txt" <<EOF
Gundam Wing Endless Duel Recompiled $VERSION
Platform pack: $PLATFORM

This build does NOT include the SNES ROM.
On first launch, pick your legally obtained copy of
Shin Kidou Senki Gundam W - Endless Duel (Japan) (.sfc / .smc).

  CRC32   c0aecdca
  SHA-256 dd94308d822636c6ddf73c5e2644c84f2eb8fb4d9201150fc5f37d44d6f423f1

Netplay lobbies match on game title plus this VERSION string: two peers on
different versions are refused a seat rather than allowed to desync later.
Internet play needs ICE (libjuice); this pack is built with
SNESRECOMP_NET_ICE=ON.
EOF

# Guard the licensing rule mechanically rather than by memory: no ROM-derived
# bytes leave this tree in a release zip.
if find "$STAGE" \( -name '*.sfc' -o -name '*.smc' -o -name '*.srm' \) | grep -q .; then
  echo "package_release.sh: refusing to package — ROM data found in $STAGE" >&2
  exit 1
fi
# Generated C is ROM-derived too, and it has no business in a player zip.
if find "$STAGE" -name 'bank*_v2.c' -o -name 'dispatch_v2.c' | grep -q .; then
  echo "package_release.sh: refusing to package — generated C found in $STAGE" >&2
  exit 1
fi

rm -f "dist/$NAME.zip"
( cd dist && zip -qr "$NAME.zip" "$NAME" )
rm -rf "$STAGE"
echo "Packaged: dist/$NAME.zip"
ls -l "dist/$NAME.zip"
