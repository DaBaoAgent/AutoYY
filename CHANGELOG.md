# Changelog

All notable changes to AutoYY are documented here.

## [Unreleased]

### Added
- `src/autoyy` shared core with safe paths, manifest/media/subtitle/publication validation, state, peer statistics, download orchestration, doctor, and unified CLI.
- Windows CI for Python 3.11/3.12 and compatibility checks.
- Deterministic batch voiceover quality gates with source/script hashes, Humanizer/fact/reviewer status, evidence anchors, and cross-topic copy detection.
- Project-local `.autoyy/state.json` resume state with stale propagation.
- Project-local peer-hit library and confidence-aware statistics.
- Test suite and coverage reporting.

### Changed
- Download PowerShell entry is now a compatibility wrapper around the tested Python core.
- Subtitle, publication, and package validators share one authoritative implementation.
- Jimeng prompt generation rejects invalid 6+8 titles instead of warning and continuing.
- Local cover generation requires explicit topic cover text and no longer contains project-specific topic mappings.
- ASR output is atomic and no longer changes `HF_ENDPOINT` at import time.

### Fixed
- Failed download rows no longer return an overall success code.
- Zero-byte files cannot satisfy resume/ready checks.
- Empty validation roots no longer pass by default.
- SRT numbering gaps are detected.
- Manifest folder traversal outside the output root is rejected.
- Partial batch voiceover failures cannot be reported as complete.
