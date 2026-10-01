# Changelog

All notable changes to AutoYY are documented here.

## [Unreleased]

### Added
- Workload profiling, legacy topic inventory diagnostics, local JSONL run observability, and `autoyy diagnose`.
- Deterministic automatic scheduler with stage capacities plus finish-first, repair-first, and source-first strategies.
- Explicit ASR worker pool and direct faster-whisper media decoding.
- `src/autoyy` shared core with safe paths, manifest/media/subtitle/publication validation, state, peer statistics, download orchestration, doctor, and unified CLI.
- Windows CI for Python 3.11/3.12 and compatibility checks.
- Deterministic batch voiceover quality gates with source/script hashes, Humanizer/fact/reviewer status, evidence anchors, and cross-topic copy detection.
- Project-local `.autoyy/state.json` resume state with stale propagation.
- Project-local peer-hit library and confidence-aware statistics.
- Test suite and coverage reporting.
- Dependency-aware `batch status/plan` commands and atomic per-topic `work` leases for agent orchestration.
- Optional parallel package/voiceover validation with bounded `--workers`.

### Changed
- Final package validation now defaults to four workers based on the recorded real-workload benchmark.
- Large verified media uses sampled state fingerprints instead of full-file rereads; small/text artifacts retain full SHA-256.
- Download overlaps subtitle discovery with video transfer and preserves source=ready when only subtitle acquisition fails, enabling immediate ASR fallback.
- Download PowerShell entry is now a compatibility wrapper around the tested Python core.
- Subtitle, publication, and package validators share one authoritative implementation.
- Jimeng prompt generation rejects invalid 6+8 titles instead of warning and continuing.
- Local cover generation requires explicit topic cover text and no longer contains project-specific topic mappings.
- ASR output is atomic and no longer changes `HF_ENDPOINT` at import time.
- ASR auto-routing is source-language aware; malformed existing SRT files are quarantined and regenerated only after runtime prerequisites are available.
- Cross-topic copy validation now uses safe shingle/q-gram upper-bound prefilters before exact comparisons, preserving the original thresholds while reducing batch cost.
- Download and ASR batches checkpoint state incrementally, with revision-conflict retries for concurrent writers.

### Fixed
- Failed download rows no longer return an overall success code.
- Zero-byte files cannot satisfy resume/ready checks.
- Empty validation roots no longer pass by default.
- SRT numbering gaps are detected.
- Manifest folder traversal outside the output root is rejected.
- Partial batch voiceover failures cannot be reported as complete.
- Multi-topic voiceover scaffolding requires a matching active lease, preventing one agent run from silently consuming multiple topic directories.
