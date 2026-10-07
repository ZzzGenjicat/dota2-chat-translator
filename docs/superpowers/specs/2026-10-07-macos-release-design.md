# macOS offline translator release

The user wants a working macOS version of the existing offline Dota 2 translator, published to a new public GitHub repository named `dota2-chat-translator`.

Reuse the current Tk GUI, GSI receiver, shared M2M100 418M CTranslate2 INT8 engine, lexicon and bounded caches. Runtime translation must remain offline. CPU inference stays at two threads by default, one worker, one shared model and a 180-second idle timeout. macOS priority must be lowered to an absolute target once rather than incremented on every model load.

Provide separate native Apple Silicon and Intel `.app` bundles in `.dmg` installers. Bundle the verified model and Python dependencies; a downloaded installer must not need Python, Homebrew or a first-launch model download. Build on native GitHub macOS runners with PyInstaller onedir mode so model weights are not unpacked on every launch. Support macOS 14 or later. Include model provenance and first-use instructions. Developer ID signing/notarization requires the owner's Apple credentials and is outside the available environment; explain the standard macOS first-open process without disabling system protections.

Use current-user Application Support for writable files. Steam libraries, external drives, Unicode paths and manual directory selection keep using the existing portable discovery. Use an available Chinese UI font and provide a visible startup error on macOS. Keep Windows behavior and launchers working.

Add source install/launch scripts, reproducible build tooling and a GitHub workflow for both architectures. Each native build must run unit tests, real INT8 translations in both directions, a Tk/clipboard/GSI smoke check, then test the frozen app from a moved path with spaces and Chinese characters while remote Python network access is blocked. Publish installers, SHA256 checksums and test reports to a versioned GitHub Release.

Publish only translator source, tests, build tooling, lexicon and relevant documentation. Exclude personal settings, credentials, chat caches, screenshots, local environments, unrelated ban-card tools and model weights from Git source. Weights belong in the installers, with the existing pinned revision and SHA256 verification.
