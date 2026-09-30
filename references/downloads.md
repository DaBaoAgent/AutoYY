# Authorized download workflow

## Contents

- Preconditions
- Dependencies
- Manifest use
- Cookie and proxy handling
- Subtitle selection
- Failure handling
- Copyright boundary

## Preconditions

Download only when the user has authorized the action and has a lawful basis to use the source. Public viewing does not imply permission to download, republish, or monetize.

Never bypass DRM, paywalls, private-video access, regional controls, bot protections, or account security. Do not install credential helpers or browser automation packages without explicit approval.

## Dependencies

The compatibility wrapper supports Windows PowerShell 5.1+ and delegates business logic to Python 3.11+. `yt-dlp`, `ffmpeg`, and `ffprobe` are required for verified media completion; Node or Deno may be required by current YouTube extraction.

Use an up-to-date `yt-dlp`. Record the version in troubleshooting notes. Parallel rows use AutoYY's Python worker pool, so behavior is consistent across Windows PowerShell 5.1 and PowerShell 7.

## Manifest use

Start with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/download_from_manifest.ps1 `
  -Manifest topics.csv `
  -OutputRoot "D:\自动剪辑\AutoYY-20260101-示例项目" `
  -PlanOnly
```

Review the plan, then remove `-PlanOnly` to download. Optional parameters:

```powershell
-YtDlp "D:\Tools\yt-dlp.exe"
-FfmpegLocation "D:\Tools\ffmpeg\bin"
-CookiesFromBrowser "chrome"
-Proxy "socks5://127.0.0.1:1080"
-MaxHeight 1080
-MinHeight 720
```

Do not hardcode a proxy, browser, executable, or disk path into the manifest.

## Cookie and proxy handling

Use browser cookies only after explicit authorization. Ask the user to close Chrome if the cookie database is locked. Never print, copy, or persist cookie contents in logs or output folders.

Use a proxy only when the user supplies or authorizes it. Report proxy failures separately from availability.

## Subtitle selection

Preference: manual English, automatic English, manual Chinese or another language, then automatic subtitles in another available language.

Normalize the selected output to `字幕.srt`. Record the selected language. Do not claim English subtitles when a fallback was used.

## Failure handling

- Resume partial media; do not delete it automatically.
- Skip only an existing source/SRT pair that passes non-empty, SRT, and ffprobe verification; file names alone never establish readiness.
- Distinguish authentication, geo restriction, missing formats, missing subtitles, disk space, and network failures.
- Retry transient failures with bounded retries.
- If metadata succeeds but media fails, retain the verified manifest row and mark the topic blocked.
- Do not lower resolution below the user's threshold silently.

## Copyright boundary

Preserve source URLs, channel names, verification dates, and licensing notes. Prefer owned, licensed, Creative Commons, public-domain, or explicitly permitted material. When permission is unclear, provide research and metadata but flag production use for rights review.
