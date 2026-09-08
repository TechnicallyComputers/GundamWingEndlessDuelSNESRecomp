#!/usr/bin/env bash
# Build Gundam Wing: Endless Duel in one of the project's named configurations.
#
# The point is that the configurations have NAMES. Which cmake flags make a
# "dev build" versus "what a player ends up running" is a fact about this
# project, and it belongs in the repo rather than in whoever last typed the
# command line.
#
#   dev      playable, TCP debug server ON. What you develop against.
#   release  playable, no debug server. The shape a PLAYER ends up running.
#   trace    release-shaped but with the debug server, for probing a build
#            that behaves like the shipped one.
#   verify   dev plus execution policy `verify` — run optimized paths and
#            publish the FAITHFUL result, counting mismatches. The promotion
#            gate; meaningless until something is registered to promote.
#   setup    what CI ACTUALLY ships (-DSNESRECOMP_SETUP_HOST=ON): a host with
#            no recompiled code, whose only path is Generate & rebuild. It
#            cannot boot a guest by construction. NOTE: it refuses to
#            configure in a tree that HAS generated code (runner.cmake
#            guards this, so a setup zip can never smuggle ROM-derived C).
#            Use a clean checkout, or move src/gen aside, to build it.
#
# Note what `setup` implies. CI does not ship a playable recompiled game; it
# ships the wizard, and the player's machine produces the playable binary. The
# setup host's rebuild passes only -DCMAKE_BUILD_TYPE=Release
# (snesrecomp/host/snesrecomp_codegen_host.c), so every other option falls back
# to its CHECKED-IN cmake default. A flag added to release.yml never reaches
# the binary a player runs. To change what players get, change the default in
# runner.cmake / CMakeLists.txt — not a CI command line.
#
# Execution policy (recomp-ai-rules/OPTIMIZATION.md §2): off|on|force|verify|
# auto. The build sets only the DEFAULT. At runtime SNESRECOMP_EXECUTION_MODE
# overrides it and SNESRECOMP_FORCE_FLOOR=1 beats both — the faithful floor
# stays forceable in every build, including the shipped one, which is why this
# is not a compile-time split.
#
# Flags:
#   --config <name>   dev (default) | release | trace | verify | setup
#   --exec <policy>   override the configuration's execution default
#   --build-dir <d>   override the build directory
#   --jobs <n>        parallelism (default: nproc)
#   --clean           delete the build directory first
#   --no-build        configure only
#   -h|--help         this message
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

TARGET=GundamWingEndlessDuelSNESRecomp
CONFIG=dev
EXEC_OVERRIDE=""
BUILD_DIR=""
JOBS="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"
CLEAN=0
DO_BUILD=1

while [ $# -gt 0 ]; do
  case "$1" in
    --config)    CONFIG=$2; shift 2 ;;
    --exec)      EXEC_OVERRIDE=$2; shift 2 ;;
    --build-dir) BUILD_DIR=$2; shift 2 ;;
    --jobs|-j)   JOBS=$2; shift 2 ;;
    --clean)     CLEAN=1; shift ;;
    --no-build)  DO_BUILD=0; shift ;;
    -h|--help)   sed -n '2,/^set -euo/p' "$0" | sed -n '/^# \?/p' | sed 's/^# \?//'; exit 0 ;;
    *) echo "build.sh: unknown flag: $1 (try --help)" >&2; exit 2 ;;
  esac
done

SETUP_HOST=OFF
case "$CONFIG" in
  dev)     TRACE=ON;  EXEC=off;    DEFAULT_DIR=build-dev ;;
  release) TRACE=OFF; EXEC=off;    DEFAULT_DIR=build-release ;;
  trace)   TRACE=ON;  EXEC=off;    DEFAULT_DIR=build-trace ;;
  verify)  TRACE=ON;  EXEC=verify; DEFAULT_DIR=build-verify ;;
  setup)   TRACE=OFF; EXEC=off;    DEFAULT_DIR=build-setup; SETUP_HOST=ON ;;
  *) echo "build.sh: --config must be dev|release|trace|verify|setup (got '$CONFIG')" >&2; exit 2 ;;
esac

# `release` keeps EXEC=off deliberately. A release build flips to `on` only
# once a replacement has cleared its promotion gates, and this port has
# registered none — shipping `on` today would print a policy the build cannot
# act on. Change the line above when the first optimization is promoted; do
# not paper over it with --exec at the call site.

[ -n "$EXEC_OVERRIDE" ] && EXEC="$EXEC_OVERRIDE"
[ -n "$BUILD_DIR" ] || BUILD_DIR="$DEFAULT_DIR"

if [ "$CLEAN" = "1" ] && [ -d "$BUILD_DIR" ]; then
  echo "[clean] rm -rf $BUILD_DIR"
  rm -rf "$BUILD_DIR"
fi

if [ "$SETUP_HOST" = "OFF" ]; then
  if [ ! -d src/gen ] || [ -z "$(ls -A src/gen 2>/dev/null)" ]; then
    echo "ERROR: src/gen/ is empty — run 'bash tools/regen.sh' with a verified ROM first." >&2
    echo "       (or build the wizard instead: --config setup)" >&2
    exit 1
  fi
fi

GENERATOR=(-G "Unix Makefiles")
command -v ninja >/dev/null 2>&1 && GENERATOR=(-G Ninja)

echo "[1/2] configure: config=$CONFIG trace=$TRACE exec-default=$EXEC setup-host=$SETUP_HOST dir=$BUILD_DIR"
cmake -S . -B "$BUILD_DIR" "${GENERATOR[@]}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DSNESRECOMP_ENABLE_TRACE="$TRACE" \
  -DSNESRECOMP_SETUP_HOST="$SETUP_HOST" \
  -DSNESRECOMP_NET_ICE=ON \
  -DSNESRECOMP_EXECUTION_DEFAULT="$EXEC"

if [ "$DO_BUILD" = "0" ]; then
  echo "      (--no-build) configured only."
  exit 0
fi

echo "[2/2] build: $TARGET -j$JOBS"
cmake --build "$BUILD_DIR" --target "$TARGET" -j"$JOBS"

BIN="$BUILD_DIR/$TARGET"
[ -f "$BIN" ] || BIN="$(find "$BUILD_DIR" -maxdepth 3 -type f -name "$TARGET" | head -1)"
if [ -n "$BIN" ] && [ -f "$BIN" ]; then
  echo "      ELF: $BIN ($(du -h "$BIN" | cut -f1))"
else
  echo "      WARNING: built, but no '$TARGET' binary found under $BUILD_DIR" >&2
fi

cat <<EOM

  Runtime policy overrides (no rebuild needed):
    SNESRECOMP_EXECUTION_MODE=off|on|force|verify|auto
    SNESRECOMP_FORCE_FLOOR=1        force the faithful floor, beats everything
EOM
