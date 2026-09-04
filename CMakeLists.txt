cmake_minimum_required(VERSION 3.20)
project(GundamWingEndlessDuelSNESRecomp C CXX)

set(CMAKE_C_STANDARD 11)

# Release pin for lobby matching. Release builds stamp the VERSION file so two
# peers can prove they are the same build.
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" _GAME_VER_RAW)
string(STRIP "${_GAME_VER_RAW}" GAME_RELEASE_VERSION)

# Everything else stamps "dev+<short commit>", and "-dirty" when the tree has
# uncommitted changes.
#
# A bare "dev" made every development build claim the same identity, so the
# server's version_mismatch check -- the thing that is supposed to stop two
# different builds meeting -- passed for any pair of them. Two peers running
# different code seated happily and the divergence surfaced later as a desync,
# which is the expensive way to learn it.
#
# The dirty marker is a warning, not an identity: two dirty trees are NOT
# distinguished from each other, because hashing the working tree on every
# configure costs more than it is worth. It says "this build is not what its
# commit says", which is the part a reader needs.
set(SNESRECOMP_DEV_VERSION "dev")
find_package(Git QUIET)
if(GIT_FOUND)
    execute_process(
        COMMAND "${GIT_EXECUTABLE}" rev-parse --short=8 HEAD
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        OUTPUT_VARIABLE _git_sha OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET RESULT_VARIABLE _git_rc)
    if(_git_rc EQUAL 0 AND _git_sha)
        set(SNESRECOMP_DEV_VERSION "dev+${_git_sha}")
        # Submodules count: the framework is most of what a build IS, so a
        # bumped snesrecomp with an untouched game tree is a different build.
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" status --porcelain --untracked-files=no
            WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
            OUTPUT_VARIABLE _git_dirty OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
        if(NOT _git_dirty STREQUAL "")
            # "-dirty" alone says a build is not its commit; it does not say
            # WHICH uncommitted state it is, so two peers built from different
            # working trees reported the same identity and a stale binary was
            # indistinguishable from a current one. Hashing the diff gives the
            # uncommitted state a name: same tree -> same id, changed tree ->
            # different id, and copying one artifact to both machines still
            # matches (unlike a timestamp, which would never match).
            # The parent's diff renders a dirty submodule as one gitlink line
            # ("Subproject commit <sha>-dirty") that is identical whatever
            # changed inside it -- and the framework and launcher ARE
            # submodules, so most edits were invisible to this hash. Digest
            # each working tree that contributes code to the build.
            execute_process(
                COMMAND "${GIT_EXECUTABLE}" diff HEAD
                WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
                OUTPUT_VARIABLE _git_diff ERROR_QUIET)
            foreach(_sub snesrecomp recomp-ui)
                if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${_sub}/.git")
                    execute_process(
                        COMMAND "${GIT_EXECUTABLE}" diff HEAD
                        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/${_sub}"
                        OUTPUT_VARIABLE _sub_diff ERROR_QUIET)
                    string(APPEND _git_diff "${_sub_diff}")
                endif()
            endforeach()
            string(SHA1 _git_diff_sha "${_git_diff}")
            string(SUBSTRING "${_git_diff_sha}" 0 8 _git_diff_short)
            set(SNESRECOMP_DEV_VERSION
                "${SNESRECOMP_DEV_VERSION}-dirty.${_git_diff_short}")
            set(_git_dirty_flag TRUE)
            set(_git_dirty_tag "-dirty.${_git_diff_short}")
        endif()
    endif()
endif()
message(STATUS "Lobby version pin (non-release): ${SNESRECOMP_DEV_VERSION}")

# A Release-TYPE build is not automatically a released BUILD. Configuring with
# -DCMAKE_BUILD_TYPE=Release in a working tree with local changes stamped the
# bare VERSION string, so a binary full of uncommitted work presented itself as
# 0.1.0 and matched every other 0.1.0 on the wire -- the same lie a bare "dev"
# told, wearing a release number. A clean tree still stamps the plain version,
# so CI artifacts are unaffected; anything else carries what it really is.
set(GAME_RELEASE_PIN "${GAME_RELEASE_VERSION}")
if(_git_dirty_flag)
    set(GAME_RELEASE_PIN "${GAME_RELEASE_VERSION}+${_git_sha}${_git_dirty_tag}")
    message(WARNING
        "Release build from a modified tree: pinning lobby version as "
        "'${GAME_RELEASE_PIN}'. It will not match a clean ${GAME_RELEASE_VERSION} build.")
endif()

set(SNESRECOMP_BUILD_VERSION "" CACHE STRING
    "Lobby release pin (empty = Release uses VERSION, else dev+<sha>)")
if(SNESRECOMP_BUILD_VERSION STREQUAL "")
    get_property(_is_multi GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
    if(_is_multi)
        set(SNESRECOMP_BUILD_VERSION "$<IF:$<CONFIG:Release>,${GAME_RELEASE_PIN},${SNESRECOMP_DEV_VERSION}>")
    elseif(CMAKE_BUILD_TYPE STREQUAL "Release")
        set(SNESRECOMP_BUILD_VERSION "${GAME_RELEASE_PIN}")
    else()
        set(SNESRECOMP_BUILD_VERSION "${SNESRECOMP_DEV_VERSION}")
    endif()
endif()
message(STATUS "Lobby version pin: ${SNESRECOMP_BUILD_VERSION}")

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
set(SNESRECOMP_ENABLE_MODS ON CACHE BOOL
    "Enable Endless Duel's built-in localization mod catalog" FORCE)
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
    src/gwed_display.c
    src/gwed_ws_patch.c
    src/translation_mod.c
    src/widescreen_mod.c
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
# Build identity. Two peers that are not running the same build will diverge,
# and until now neither the log nor the wire carried anything that could say
# so: a real match desynced mid-fight and establishing "same build?" took
# forensic comparison of two logs that recorded no version at all.
#
# The game repo's SHA is the right identifier because it pins the snesrecomp
# submodule, so one value covers both. Deliberately NOT a hash of the binary:
# the same source produces different bytes on Linux and Windows, which would
# report a mismatch on every cross-platform match -- the case this exists to
# serve.
execute_process(
  COMMAND git -C "${CMAKE_CURRENT_SOURCE_DIR}" describe --always --dirty --abbrev=12
  OUTPUT_VARIABLE GWED_BUILD_ID
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET)
if(NOT GWED_BUILD_ID)
  set(GWED_BUILD_ID "unknown")
endif()
# git describe --dirty says "dirty" and stops there; name the uncommitted state
# so two peers can tell a stale binary from a current one.
if(_git_dirty_tag)
  string(REPLACE "-dirty" "" GWED_BUILD_ID "${GWED_BUILD_ID}")
  set(GWED_BUILD_ID "${GWED_BUILD_ID}${_git_dirty_tag}")
endif()
message(STATUS "Build identity: ${GWED_BUILD_ID}")

target_compile_definitions(GundamWingEndlessDuelSNESRecomp PRIVATE
    GWED_BUILD_ID="${GWED_BUILD_ID}"
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

add_custom_target(GundamWingEndlessDuelSNESRecompStageLocalization ALL
    COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/mods"
        "$<TARGET_FILE_DIR:GundamWingEndlessDuelSNESRecomp>/mods"
    COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/translations"
        "$<TARGET_FILE_DIR:GundamWingEndlessDuelSNESRecomp>/translations"
    # The translation table also goes INSIDE the localization package, because
    # the package is the unit a host transfers to a guest. Staged rather than
    # committed twice: the repo keeps one copy under translations/, and the
    # built product carries a self-contained package.
    #
    # Without this the package is a manifest describing a translation whose
    # data ships separately, so a guest that downloads the mod gets the
    # description and none of the data -- it then runs untranslated while
    # reporting the same mod set as the host.
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_SOURCE_DIR}/translations/endless_duel.toml"
        "$<TARGET_FILE_DIR:GundamWingEndlessDuelSNESRecomp>/mods/preloaded/packages/gwed.localization/1.0.0/endless_duel.toml")
add_dependencies(GundamWingEndlessDuelSNESRecompStageLocalization
    GundamWingEndlessDuelSNESRecomp)

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
