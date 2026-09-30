# AutoYY environment and portability facts

## Supported environment

- AutoYY is Windows-first.
- Python 3.11+ is required.
- Required media commands: `yt-dlp`, `ffmpeg`, `ffprobe`.
- Current YouTube extraction should have Node or Deno available when yt-dlp requires a JavaScript runtime.
- aria2 is optional download acceleration.
- FunASR and faster-whisper are optional local ASR backends.

## Working roots

Production outputs must stay outside the skill repository. Configure the default output root with `AUTOYY_WORK_ROOT`. The Windows fallback is `D:\自动剪辑`.

Repository code must not assume a particular username, developer checkout path, private sample corpus, or one-off production directory.

## Runtime state

Per-project state and learned peer data live below `<project>\.autoyy\`. This directory contains workflow metadata/fingerprints only and is ignored by Git.

Do not place cookies, tokens, proxies, browser-session contents, source media, or private credentials in state files or fixtures.

## Development checks

Use `python -m autoyy doctor` to inspect the current machine rather than documenting machine-specific executable locations here. The doctor reports required and optional capabilities without installing or changing system software.
