# macOS release implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement these tasks inline. The human has authorized implementation and public GitHub publication; continue without additional approval gates.

**Goal:** Ship native Apple Silicon and Intel offline Mac installers on a new public GitHub repository.

**Architecture:** Keep the portable application core and add native packaging, platform UI/runtime fixes and release smoke validation. GitHub Actions builds on real Macs and publishes installers on version tags.

**Tech Stack:** CPython 3.12, Tk, CTranslate2 4.8.2, SentencePiece 0.2.1, PyInstaller 6, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-07-macos-release-design.md`

## Global Constraints

- CPU INT8 only; default two threads, one worker, one shared model, idle unload after 180 seconds.
- Offline runtime, verified bundled model, no first-launch downloads.
- Native Apple Silicon and Intel builds, macOS 14 or later.
- Current-user writable data; no personal state or unrelated files in the public repository.

## Review Focus

- Repeated model reloads must not repeatedly increase process niceness.
- A frozen app moved into a Unicode path must retain its model and lexicon.
- Both native architectures must actually support INT8 and bundle native libraries.
- Mac font metrics and clipboard must work with the current UI.
- A download must contain weights and clear first-launch instructions rather than depend on a local developer environment.

### Task 1: Native platform behavior

- [x] Write regression tests for repeated macOS priority setup, font fallback and platform-specific installation guidance; run them RED.
- [x] Implement absolute priority, Chinese font selection and visible startup errors; run focused tests GREEN and the full suite.

### Task 2: Distribution and native verification

- [x] Write smoke-runner tests for the offline guard; run RED and validate JSON reports with the real model.
- [x] Add source install/launch scripts, a PyInstaller spec, native DMG builder, model notice and an offline self-test entry point.
- [x] Add native Apple Silicon/Intel CI that tests the frozen bundle after relocation and publishes installers/checksums/reports for version tags or an explicitly published manual run.
- [x] Run local tests and syntax checks, then request a fresh code review and fix material findings.

### Task 3: GitHub release

- [x] Audit the exact publication manifest for personal state, credentials, unrelated tools and oversized Git files.
- [x] Create the requested public repository, commit and upload only the audited manifest.
- [x] Trigger native builds, inspect failures and repair them until both installers and frozen smoke reports pass.
- [x] Verify the GitHub Release assets and provide repository/download links with any remaining practical limitations.

Release: [v0.3.1](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/tag/v0.3.1).
Verification: `docs/macos-verification.md`.
