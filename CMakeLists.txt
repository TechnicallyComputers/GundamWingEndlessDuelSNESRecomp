cmake_minimum_required(VERSION 3.20)
project(GundamWingEndlessDuelSNESRecomp C CXX)

set(CMAKE_C_STANDARD 11)

# Release pin for lobby matching. Release builds stamp the VERSION file so two
# peers can prove they are the same build; everything else stamps "dev".
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" _GAME_VER_RAW)
string(STRIP "${_GAME_VER_RAW}" GAME_RELEASE_VERSION)
set(SNESRECOMP_BUILD_VERSION "" CACHE STRING
    "Lobby release pin (empty = Release uses VERSION, else dev)")
if(SNESRECOMP_BUILD_VERSION STREQUAL "")
    get_property(_is_multi GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
    if(_is_multi)
        set(SNESRECOMP_BUILD_VERSION "$<IF:$<CONFIG:Release>,${GAME_RELEASE_VERSION},dev>")
    elseif(CMAKE_BUILD_TYPE STREQUAL "Release")
        set(SNESRECOMP_BUILD_VERSION "${GAME_RELEASE_VERSION}")
    else()
        set(SNESRECOMP_BUILD_VERSION "dev")
    endif()
endif()

# ICE (STUN/TURN via static libjuice) — required for internet play. The
# public lobby hands out HOSTED sessions, and a build without ICE refuses
# them at launch: both peers logged "hosted lobby requires ICE, but ICE is
# not available in this build" and fell back to running offline solo, which
# looks like a desync but is two disconnected games. Must be set BEFORE
# runner.cmake is included so recomp_net picks it up.
set(SNESRECOMP_NET_ICE ON CACHE BOOL
    "ICE/libjuice transport for internet netplay (lobby hosted sessions)" FORCE)

set(SNESRECOMP_ROOT "${CMAKE_SOURCE_DIR}/snesrecomp" CACHE PATH
    "Path to the snesrecomp checkout used by this game")
if(NOT EXISTS "${SNESRECOMP_ROOT}/runner/runner.cmake")
    message(FATAL_ERROR
        "snesrecomp submodule missing at ${SNESRECOMP_ROOT}.\n"
        "Run: git submodule update --init --recursive")
endif()
include(${SNESRECOMP_ROOT}/runner/runner.cmake)

# Generated recompiler output. Not committed: it is derived from a copyrighted
# ROM, so every developer regenerates it locally from their own copy.
file(GLOB GAME_GEN_SOURCES CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/src/gen/*.c)
if(NOT GAME_GEN_SOURCES)
    message(FATAL_ERROR
        "src/gen/ is empty — run `bash tools/regen.sh` with a verified "
        "Shin Kidou Senki Gundam W - Endless Duel (Japan).sfc at the repo root before building.")
endif()

add_executable(GundamWingEndlessDuelSNESRecomp
    ${SNESRECOMP_RUNNER_SOURCES}
    src/main.c
    src/host_contract.c
    src/codegen_setup.c
    src/game_rtl.c
    src/gen_stubs.c
    ${GAME_GEN_SOURCES}
)

target_include_directories(GundamWingEndlessDuelSNESRecomp PRIVATE
    ${SNESRECOMP_RUNNER_INCLUDE_DIRS}
    # common_rtl.c includes "config.h": the desktop key-map / Config contract
    # a host is expected to supply. The framework's shared version is used
    # here; copy it into src/ and drop this line once this game needs its own.
    ${SNESRECOMP_ROOT}/runner/src/desktop
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/recomp
)
target_link_libraries(GundamWingEndlessDuelSNESRecomp PRIVATE ${SNESRECOMP_RUNNER_LIBRARIES})
target_compile_definitions(GundamWingEndlessDuelSNESRecomp PRIVATE
    SNES_GAME_VERSION="${SNESRECOMP_BUILD_VERSION}"
    SNES_GAME_TITLE="Gundam Wing Endless Duel"
)
snesrecomp_target_sdl(GundamWingEndlessDuelSNESRecomp)

# Shared Dear ImGui pre-boot launcher (ROM picker, verification, display /
# audio / input settings). Self-contained: it brings its own ImGui, GL link and
# staged assets, and defines RECOMP_LAUNCHER so src/main.c compiles its GUI path.
set(RECOMP_UI_ROOT "${CMAKE_SOURCE_DIR}/recomp-ui" CACHE PATH
    "Root directory of the recomp-ui launcher repo" FORCE)
if(NOT EXISTS "${RECOMP_UI_ROOT}/recomp_ui.cmake")
    message(FATAL_ERROR
        "recomp-ui missing at ${RECOMP_UI_ROOT}.\n"
        "Run: git submodule update --init --recursive recomp-ui")
endif()
include(${RECOMP_UI_ROOT}/recomp_ui.cmake)
# BOXART stages launcher_assets/img/boxart.tga beside the exe as
# assets/img/boxart.tga, where the launcher looks by default. Guarded by
# EXISTS inside recomp_ui.cmake, so a repo without the art configures fine —
# fetch it with snesrecomp/tools/new_project/fetch_boxart.py and reconfigure.
recomp_target_launcher_ui(GundamWingEndlessDuelSNESRecomp CONSOLE snes
    BOXART "${CMAKE_SOURCE_DIR}/launcher_assets/img/boxart.tga")

# Delay-sync netplay + MotK lobby client (defines SNES_HAS_LOBBY_CLIENT, which
# is what compiles the netplay blocks in src/main.c and lights the launcher's
# netplay button). LAN by default; opt into ICE with -DSNESRECOMP_NET_ICE=ON.
snesrecomp_enable_recomp_net(GundamWingEndlessDuelSNESRecomp)

# Generated C is machine output: it is not held to the runner's warning bar,
# and treating it as such buries real warnings from hand-written host code.
if(NOT MSVC)
    set_source_files_properties(${GAME_GEN_SOURCES} PROPERTIES
        COMPILE_OPTIONS "-w")
endif()

snesrecomp_enable_recomp_net(GundamWingEndlessDuelSNESRecomp)
# Rollback netcode is an optional framework feature. Calling it unguarded
# makes the port fail to CONFIGURE against any framework build that does not
# ship it -- including a framework branch carrying only runtime fixes, which
# is exactly what you want to bisect a rendering bug against. Absent, the port
# builds without rollback rather than not at all.
if(COMMAND snesrecomp_enable_rollback)
    snesrecomp_enable_rollback(GundamWingEndlessDuelSNESRecomp)
endif()
# No multitap: two seats, one controller per port.
