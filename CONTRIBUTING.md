# Contributing to AutoYY

AutoYY is Windows-first and keeps compatibility wrappers under `scripts/` while shared rules live in `src/autoyy/`.

## Development setup

```powershell
git clone https://github.com/DaBaoAgent/AutoYY.git
cd AutoYY
python -m pip install -e ".[dev]"
python -m autoyy doctor
```

Python 3.11+ is required. Media workflows require `yt-dlp`, `ffmpeg`, and `ffprobe`. Node or Deno, aria2, and ASR backends are optional capabilities reported by `doctor`.

## Before changing behavior

Add or update a regression test first. Bugs involving exit codes, path containment, resume state, zero-byte files, source grounding, or batch voiceover quality must have a fixture that fails before the fix and passes afterward.

Do not use live YouTube access in CI. External commands must be represented by fake executables or deterministic fixtures.

## Quality gates

Run all of these before opening a pull request:

```powershell
python -m compileall -q scripts src
python -m pytest -q
python -m pytest --cov=autoyy --cov-report=term-missing --cov-fail-under=85 -q
python -m ruff check .
git diff --check
```

Batch voiceover work has a stricter contract. A writer handles one topic at a time, reads the full SRT, writes a candidate, completes Humanizer/fact/reviewer gates, and uses `autoyy voiceover promote` for the final file. Never weaken a quality threshold to make a batch pass.

## Pull request scope

Keep commits focused. Separate behavior fixes, structural refactors, and documentation when practical. Preserve existing script paths for compatibility unless a migration is explicitly documented.

Generated project state belongs under a project `.autoyy/` directory and must not be committed. Never commit browser cookies, tokens, proxy credentials, source media, private sample corpora, or machine-specific paths.

## Third-party code

`vendor/blader-humanizer/` retains its upstream MIT license and version metadata. Do not remove or replace third-party license notices.
