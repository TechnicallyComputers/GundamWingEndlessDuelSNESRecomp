#!/usr/bin/env bash
# Package a Gundam Wing Endless Duel release: a SETUP PACK.
#
# The zip does not contain the game. It contains a setup program (the host
# built with -DSNESRECOMP_SETUP_HOST=ON, i.e. without recompiled code), the
# recompiler and this source tree, and the localization mods. On first run the
# player picks their own ROM and the launcher recompiles and rebuilds locally.
# No ROM, no src/gen, no recomp/funcs.h ever leaves this tree in a zip -- the
# framework's packager enforces that mechanically.
#
# Usage:
#   scripts/package_release.sh [<build-dir>] [<platform-tag>] [--embed-toolchain]
#
# Both positionals are optional; BUILD_DIR and PLATFORM still work as env vars.
# Defaults: build, and the host's own <os>-<arch>. --embed-toolchain copies
# the cmake-clang-v1 pack named by RETCOMM_TOOLCHAIN_DIR into the zip so the
# player downloads nothing.
#
# Writes: dist/gundamwingendlessduel-<VERSION>-<platform-tag>.zip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

EXTRA=()
POS=()
for a in "$@"; do
  case "$a" in
    --embed-toolchain|--no-embed-toolchain) EXTRA+=("$a") ;;
    *) POS+=("$a") ;;
  esac
done
BUILD_DIR="${POS[0]:-${BUILD_DIR:-build}}"
PLATFORM="${POS[1]:-${PLATFORM:-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)}}"

IMPL="snesrecomp/tools/ci/stage_setup_host.sh"
if [ ! -f "$IMPL" ]; then
  echo "package_release.sh: $IMPL missing — the snesrecomp pin predates setup packs." >&2
  echo "  Update the submodule: git submodule update --init --recursive" >&2
  exit 1
fi

# mods/ is the localization package a netplay host transfers to a guest and
# translations/ is the table it reads; CMake stages both beside the exe and
# the pack refuses to build without them.
exec bash "$IMPL" \
  --build-dir "$BUILD_DIR" \
  --artifact "$PLATFORM" \
  --exe-name GundamWingEndlessDuelSNESRecomp \
  --zip-prefix gundamwingendlessduel \
  --display-name "Gundam Wing Endless Duel Recompiled" \
  --runtime-dir mods \
  --runtime-dir translations \
  --rom-hint "Shin Kidou Senki Gundam W - Endless Duel (Japan) (.sfc / .smc)" \
  --rom-crc32 c0aecdca \
  --rom-sha256 dd94308d822636c6ddf73c5e2644c84f2eb8fb4d9201150fc5f37d44d6f423f1 \
  "${EXTRA[@]+"${EXTRA[@]}"}"
