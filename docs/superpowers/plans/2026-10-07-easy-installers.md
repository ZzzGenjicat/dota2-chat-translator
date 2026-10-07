# Simple offline installers implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. The user has authorized simple installers and public publication. No additional approval gates are needed for this scope.

**Goal:** Publish Windows setup EXE and both native Mac DMGs with bundled offline model and clear novice instructions.

**Architecture:** Extend existing frozen-app packaging with a Windows spec, focused Windows builder and Inno Setup definition. Share license collection without changing the application core. Verify the actual installation lifecycle before releasing all three platforms together.

**Tech Stack:** CPython 3.12, PyInstaller 6.22.0, CTranslate2 4.8.2, Inno Setup 6.7.3, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-07-easy-installers-design.md`

## Constraints and review focus

- Windows 10/11 x64; macOS 14+ with separate arm64/x86_64 packages.
- CPU INT8, 2 threads/1 worker/shared model/180-second idle unload remain the defaults.
- All end-user installers include Python, native inference libraries, tokenizer, glossary and verified model; no installation-time network access.
- Use explicit Git staging to protect unrelated files in this checkout. Work on `codex/easy-installers`; reuse existing local build resources rather than duplicating model weights in a worktree.
- Installer testing must use an isolated AppId and shortcut directory, install below a temporary Unicode path, and verify reinstall/uninstall and preserved user data without touching a normal installation.
- Avoid including optional GPU libraries or developer modules where CPU inference does not require them; inspect native dependencies and validate the shipped executable.

## Task 1: Native Windows installer

Files: `packaging/windows.spec`, `packaging/windows.iss`, `packaging/WINDOWS_FIRST_USE.txt`, `tools/build_windows.py`, `tools/package_licenses.py`, `tools/build_macos.py`, `tests/test_windows_packaging.py`, `src/dota2_map_assistant/platform_runtime.py`.

- [x] Add a failing regression for frozen Windows missing-model guidance; verify RED, implement reinstall guidance, verify GREEN.
- [x] Add reusable third-party license collection, native x64 Windows spec and Chinese per-user setup definition with shortcuts/uninstall and isolated test mode.
- [x] Build the native Windows app; inspect DLL dependencies, run relocated frozen self-test without source PYTHONPATH.
- [x] Compile setup; install/upgrade/self-test/uninstall in the isolated test location, report results, and write SHA256.
- [x] Run full regressions and record actual frozen/installer verification evidence.

## Task 2: Publication and novice flow

Files: `.github/workflows/macos-release.yml`, `README.md`, `pyproject.toml`, `packaging/release_notes.md`, `packaging/publication-files.txt`, `docs/installer-verification.md`.

- [x] Bump version to 0.3.2 and make the Windows CI job build/test setup; require it and both native Mac jobs before publishing all release artifacts.
- [x] Add Chinese first-use instructions and a README download/installation table that directs ordinary users to setup/DMG files.
- [x] Validate workflow syntax, request one fresh whole-change review, and repair material findings.
- [x] Push the audited changes and dispatch native builds with publication enabled; inspect and fix failures until all platforms pass.
- [x] Verify public asset availability, native reports, installer lifecycle report and checksums; update this plan and verification record, then provide one shareable download page.

## Execution record

Ruling: Work in the existing checkout on a dedicated branch — native build dependencies and the verified model are already present, and explicit staging preserves unrelated untracked files. Prior baseline is 209 passing Windows cases and successful native Mac builds.

Task 1 completed: final native Windows build and actual installation lifecycle passed; 210 regressions and actionlint passed. App-local Python/MSVC libraries included; optional CUDA/cuDNN absent. Local EXE 531769926 bytes, SHA256 `c6b8ab92d8d552297bfb49f1005233f44eaf463ebf273d685940c95812aa9607`.

Whole-change read-only reviewer dispatched once, but could not run due account usage limit. Fallback audit covered the complete diff, packaging dependencies, installer isolation, CI gating, artifact filters and novice instructions; no material defect found. No independent reviewer verdict is claimed.

First native run `37669529016` caught a real small-screen clipping issue and blocked publication. Reproduced RED with max1008x712, fixed feed/composer/copy/status layout and tightened sidebar spacing; small-screen and125% controls now pass. Full 211 Windows cases and final local actual installer lifecycle passed. Corrected local EXE 531769860 bytes, SHA256 `b4f907f5fb80c60b158173779cc03a3600a12c6d46eff3c72fdc71e97ac80505`.

Published source commit `e1d0f65554f6696b0e4892b60af1e0f85ab10207`; native release run `37670598841`: all three builds and publish passed. Release v0.3.2 has10 assets; all three anonymous installer HEADs HTTP200, checksums match GitHub digests, published offline/lifecycle reports all pass. Exact release sizes/checksums recorded in `docs/installer-verification.md`; build-machine dependency differences mean the CI installer differs from the local installer. Novice download page is the shared v0.3.2 Release URL.
