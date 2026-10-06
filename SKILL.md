---
name: autoyy
description: Plan, produce, refresh, and validate documentary-explainer content packages from performance screenshots, topic research, and long-form source videos. Use when Codex needs to analyze multi-platform creator data; identify repeatable documentary topics; search and verify 30+ minute YouTube sources; prepare a download manifest; download authorized HD video and subtitles; write and humanize Chinese voiceover scripts; create or recursively bulk-refresh Douyin-ready publication metadata; generate photorealistic topic or collection covers; resume an interrupted batch; or audit whether every topic folder is complete.
---

# Produce Documentary Content

Build a traceable pipeline from creator data to one ready-to-edit folder per documentary topic. Keep facts tied to subtitles or reliable sources, preserve source metadata, and validate every deliverable before reporting completion.

Use `D:\自动剪辑` as the default working root. If the user does not provide a project directory, create a dedicated project subdirectory such as `D:\自动剪辑\AutoYY-YYYYMMDD-项目名`. Inspect but never reorganize, rename, or overwrite unrelated files already present in the working root.

> **⚠️ 目录分工（2026-08-14 体检补充）**：
> - **工作产出根 = `D:\自动剪辑`**：所有选题项目目录（`AutoYY-YYYYMMDD-选题名/`）、下载的视频/字幕/文案/封面都在这。
> - **技能代码本体 = `current AutoYY Git checkout`**（git 仓库 `DaBaoAgent/AutoYY`，SSH 443 同步）：本技能目录是指向它的 junction，SKILL.md/scripts/assets/vendor 的修改直接进 git。**不要**把工作产出放这里。

## Start

1. Determine which stages the user requested: performance audit, topic planning, source research, authorized downloads, text production, cover generation, full pipeline, resume, or audit.
2. Record configurable inputs instead of hardcoding prior values: input data, topic count, project directory under the default working root, target platforms, duration, resolution, subtitle languages, script length and tone, and cover mode.
3. For multi-topic or full-pipeline work, begin with `python -m autoyy runtime plan <root> [--manifest <manifest.csv>]`. Use `supervisor run` for long/unattended machine-stage operation, `runtime run` for a bounded foreground pass, and `schedule`/`work claim` for external Agent stages. Resume from verified files rather than restarting.
4. Ask only for decisions that materially change the result or permission scope. Never assume authorization to use browser cookies, download copyrighted media, bypass access controls, overwrite approved assets, or publish externally.

## Route to supporting skills and tools

- Read `references/tool-map.md` when planning a full pipeline or when the user asks which tools and skills are used at each stage.
- Read every supplied screenshot. Use visual inspection and OCR; use the spreadsheet skill when consolidating or analyzing tabular records.
- Browse the web for current YouTube links, view counts, availability, duration, quality, and recommendations. Treat all volatile metrics as dated observations.
- Use Chrome control only when the user explicitly needs signed-in browser state. Use `--cookies-from-browser` only after explicit authorization.
- Before writing or revising any voiceover, read `vendor/blader-humanizer/SKILL.md` completely and apply its Embedded mode. This humanization pass is mandatory, including single-topic drafts, batch generation, and rewrites.
- Use the image-generation skill for all raster cover generation and edits. Load the relevant master cover from `assets/` as a style reference.
- Use `python -m autoyy diagnose <project-root>` when a batch stalls or repeatedly retries; use the structured error code and recent event context instead of rerunning the whole project blindly.
- For an existing/partially completed project, run `python -m autoyy runtime reconcile <root> --dry-run` first when state may lag behind disk artifacts. Only ffprobe/SRT-verified artifacts are promoted to ready; missing or invalid formerly-ready artifacts are downgraded.
- Use `python -m autoyy runtime plan|run` as the long-batch control plane. Runtime may execute `source`, `subtitle`, and `package`; it must stop with `waiting_external` at `voiceover`, `publication`, or `cover`. Those stages remain lease-bound and may not be self-completed by the orchestrator.
- For unattended/resumable operation, prefer `python -m autoyy supervisor start <root> [--manifest ...]` when the work must survive terminal closure, or `supervisor run` when foreground logs are required. The supervisor persists heartbeat/session generation, recovers interrupted running stages after process/machine restart, applies retry backoff, and periodically retunes bounded worker counts from local history. Detached `start` refuses proxy/cookie command-line parameters rather than exposing sensitive authorization data. It still may not self-complete lease-bound editorial/visual stages.
- Use `autoyy queue set <root> <topic> --priority N` or `--sla-minutes N` only for explicit operator priority/SLA needs. Never encode quality exceptions as priority. Aging remains active so older runnable work cannot be starved forever.
- Use `python -m autoyy schedule <project-root> --capabilities <csv> --strategy finish-first` for read-only agent planning, and `work claim --stage auto` to atomically claim the selected task.
- Use `scripts/download_from_manifest.ps1` as the backward-compatible Windows entry. It delegates to the tested Python download core, validates resume artifacts before skipping them, supports optional aria2c acceleration, and supports `-Parallel N` without PowerShell runspace-specific logic.
- If a topic folder has a video but no usable SRT, run `scripts/transcribe.py` as the local ASR fallback: it extracts audio with ffmpeg and writes `字幕.srt` (FunASR preferred for Chinese, faster-whisper already installed; resumes automatically by skipping folders that already have subtitles).
- Use `scripts/validate_subtitles.py` to check SRT syntax, time-axis order, and subtitle-video alignment drift before writing the voiceover.
- Use `scripts/peer_hit_library.py` to learn from peer hit documentary titles and tags: run `--patterns` before writing `发布信息.txt` to see which hook pattern performs best, and `--import`/`--add` to feed observed hits and own post-publish results back into the library so the ranking keeps evolving.
- Use `scripts/validate_publication_info.py` to recursively audit publication files, including topic folders nested under archive directories such as `@已发`.
- Use `scripts/validate_deliverables.py` for the final folder audit.

## Workflow

### 1. Audit published performance

Read all visible records, not just top performers. Preserve platform, date, canonical topic, displayed title, views/reads, impressions, likes, comments, duration, and screenshot source when available.

Normalize title variants into a canonical topic without altering raw values. Deduplicate overlapping screenshots and cross-platform reposts before aggregation. Calculate platform medians, outliers, and cross-platform topic totals. Identify the repeatable audience promise behind each winner, such as extreme risk plus giant engineering, war relic plus salvage, counterintuitive animal behavior, or closed institutions. Do not infer missing metrics.

### 2. Generate and score candidate topics

Allocate a batch by default as 70% proven themes, 20% adjacent extensions, and 10% experiments. Score each candidate from 0–5 on:

1. danger or conflict;
2. visual density;
3. counterintuitive insight;
4. numeric scale;
5. source quality and usable rights.

Prioritize candidates scoring at least 20/25. Lower the score when the source only partially matches the planned angle.

### 3. Research and verify long sources

For every candidate, verify a direct watch URL and capture:

- Chinese topic and translated Chinese video title;
- original title, channel, URL, upload date;
- duration, current view count, highest available resolution;
- original language and subtitle availability;
- match grade: exact, high, adjacent, or reject;
- copyright/license note and verification date.

Default filters are duration at least 30 minutes and resolution at least 720p; prefer 1080p or 4K. Never fabricate view counts or quality. Reject inaccessible, misleading, synthetic, compilation-only, or weakly matched sources.

Write approved rows to a UTF-8 CSV manifest using `assets/topic-manifest-template.csv`.

### 4. Create the topic report

Lead with evidence from the user's performance data. For each proposed topic include its validated audience promise, source link and dated metadata, translated source title, likely hook, intended platforms, match grade, and licensing caveat. Separate verified facts from editorial inference.

### 5. Download authorized media and subtitles

Read `references/downloads.md` before downloading. Create one Chinese-named subdirectory per topic. Use the manifest and run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/download_from_manifest.ps1 `
  -Manifest <manifest.csv> `
  -OutputRoot <output-root>
```

Optional P1 hardening flags (all backward compatible):

- `-UseAria2 -Aria2Connections 8` — aria2c multi-connection per file (auto-detected; install with `winget install aria2.aria2`). Falls back to yt-dlp built-in concurrent fragments when aria2c is missing.
- `-Parallel 4` / `autoyy download --workers 4` sets the target topic concurrency. Python download waves adapt downward when retryable network/rate-limit failures spike and recover toward the target after stable waves. Use `--fixed-workers` only to disable adaptation for diagnosis.
- `autoyy download --rate-limit 20M` adds an explicit per-process yt-dlp bandwidth ceiling. Transient command failures use bounded exponential backoff; authentication, unsupported URLs, and deterministic validation failures are not blindly retried.
- `-Trace` — print every yt-dlp command for debugging.
- Resume trusts validation, not status labels: any existing non-empty video/SRT pair is skipped only after SRT and ffprobe checks pass. `下载状态.csv` is a report, not a source of truth.

Prefer manual English subtitles, then automatic English, then another available language. Keep the original URL and metadata in the manifest. Do not defeat DRM, paywalls, regional access controls, or platform security.

### 5.5 Local ASR fallback transcription

If a topic folder has a playable video but no usable SRT (YouTube has no caption track, or caption download failed), generate the subtitle locally instead of blocking the topic:

```powershell
python scripts/transcribe.py <output-root> [--manifest <manifest.csv>] `
  --ffmpeg-location "<ffmpeg-bin-dir>"
```

- Backend `auto` is language-aware: Chinese sources prefer FunASR, while English/other or unknown-language sources prefer multilingual faster-whisper. Manifest `source_language` overrides the CLI fallback; `--language auto` is the default. `--device auto` selects CUDA only when a usable NVIDIA GPU is detected, otherwise CPU; CUDA/CUDNN/OOM failures fall back to CPU for that topic. Install the corresponding optional extra when needed.
- AutoYY respects the existing Hugging Face environment. Use `--hf-endpoint` explicitly when a mirror is required; the script does not mutate the global endpoint at import time.
- Folders are skipped only when the existing SRT actually validates. A malformed non-empty SRT is quarantined as `.invalid*` and regenerated; `--overwrite` still forces re-transcription.
- Run `scripts/validate_subtitles.py` afterward to confirm the generated SRT parses and aligns with the video.

### 6. Write the Chinese voiceover

Read the full subtitle or transcript before drafting. Use subtitles as the narrative backbone and reliable sources only for necessary context. Read `references/content-style.md` and `vendor/blader-humanizer/SKILL.md` completely before writing. For recurring technical terms (military, marine, nuclear, aviation, medical, ecology), keep translations consistent with `assets/term-glossary.csv` — extend the glossary when a new domain appears.

Default requirements:

- pure spoken Chinese with no production labels;
- approximately 4,500–5,500 non-whitespace Chinese characters unless the user sets another target;
- light, conversational popular-science tone;
- a strong opening hook, steady reveals, and a meaningful ending;
- short, speakable sentences with technical terms explained simply;
- no invented facts, unrelated filler, duplicated paragraphs, template transitions, or calls to follow/share.

Voice profile is explicit, never inferred across a batch:
- `default`: follow `references/content-style.md`, including third-person narrator rules.
- `laorou`: first read `laorou/SKILL.md` and its style guide, then run `python -m autoyy voiceover scaffold <topic-folder> --voice-profile laorou`. This profile overrides narrator perspective only; source grounding, Humanizer, evidence, reviewer, length, anti-copy, and promotion gates remain mandatory.

**Non-Chinese subtitle source (English SRT) — gate traps:** the machine gate compares every quoted string against the normalized SRT and only accepts Arabic digits as numbers.

- Keep every quote verbatim in the source language (English) and at most 120 characters, so pick short lines; `DIRECT_QUOTE_RE` cannot match a longer quote, and a translated quote fails as `direct quotes not found in source`.
- Never wrap Chinese prose in quotes. A quoted Chinese term (even 4 characters, e.g. 「指令装载」) is extracted as a quote and fails the same check. Use plain text or an unquoted description instead.
- Write ordinary quantities as Chinese numerals. `NUMBER_RE` only matches `\d+`, so Chinese numerals never need evidence; every Arabic digit left in the script must either appear in the SRT or be covered by an evidence item with `source_kind="verified_source"` plus `source_ref` and `verified_at`.
- External facts stay citable as Chinese numerals with `verified_source` evidence, which keeps a short source video able to support a 4,500-5,500 character script.
- Quotes that span two SRT cues still match after whitespace normalization; verify the whole script with `validate_voiceover(topic, script_path=topic/"爆款口播稿.candidate.txt")` before attesting, since `promote` refuses a missing HMAC attestation (`AUTOYY_QUALITY_ATTESTATION_KEY`, one key per operator machine, `.env` is gitignored).

After drafting:

1. Keep the first draft under a versioned filename; do not promote it to `爆款口播稿.txt` yet.
2. Run the bundled Humanizer in Embedded mode on the full draft. Complete its draft rewrite, AI-pattern audit, fabrication audit, and final rewrite internally.
3. Preserve every supported fact, name, date, quantity, quotation, uncertainty, chronology, causal statement, target length, and the user's approved voice. Prefer local sentence or paragraph repairs when they solve the problem.
4. Recheck the result against the subtitle/transcript, then read it aloud for spoken rhythm, opening hook, ending, and character count. If new source-grounded material is added, run the Humanizer pass again.
5. Promote only the cleaned narrator-ready text to `爆款口播稿.txt`. Never include Humanizer notes, headings, audit bullets, or process labels in the script.
6. After the promoted file is non-empty and passes final validation, delete superseded voiceover copies in the same topic directory, including `爆款钩子文案.txt` and versioned `爆款口播稿-*` drafts. Leave only `爆款口播稿.txt` as the script deliverable. If validation fails, retain the drafts for repair. Never delete subtitles, source notes, publication information, or unrelated text files.

Remove padding-based binary contrasts, ceremonial sequencing, abstract essence claims, assistant route markers, repeated template colons, over-even paragraph shapes, and generic engagement questions. Keep a contrast, sequence, or question when it carries necessary facts, causality, quoted speech, or a user-requested specific comment hook.

Do not pad to length. If the source cannot support the requested duration, state the evidence gap and propose supplemental sources. If the Humanizer instructions cannot be loaded or the pass cannot be completed, mark the voiceover blocked rather than claiming completion.


#### Mandatory batch voiceover gate

When a request contains two or more topic directories, quality outranks throughput:

1. Build the queue with `python -m autoyy schedule <project-root> --capabilities voiceover --strategy finish-first` (or the stage-specific `batch plan`), then **claim exactly one topic** using `python -m autoyy work claim <project-root> --worker-id <unique-run-id> --stage voiceover`. The lease is mandatory for multi-topic `voiceover scaffold`; the same worker cannot claim a second directory until its active lease is released. Never manually enumerate several SRTs into one writing prompt.
2. Read the current topic's complete `字幕.srt`; summaries may navigate the source but cannot replace the full read.
3. Write `爆款口播稿.candidate.txt`. Then run `python -m autoyy voiceover scaffold <topic-folder> --lease-token <token>` so writer provenance is tied to the claimed topic. Do not write or overwrite `爆款口播稿.txt` directly.
4. Run `vendor/blader-humanizer/SKILL.md` in Embedded mode on that candidate, then perform a source-grounded fact check and an independent semantic review in a fresh context.
5. Create/update `<topic>/.autoyy/voiceover-quality.json`. It must bind current source/script SHA-256, use `attempt=1..3`, `humanizer.mode=embedded`, `reviewer.independent=true`, fact/reviewer coverage counts, and evidence with matching script excerpts. Supplemental non-SRT facts require `source_kind=verified_source`, `source_ref`, and `verified_at`.
6. Evidence must be distributed through the script; a long ungrounded span is a hard failure. Numbers, dates, direct quotations, names, and causal claims may not be invented to reach the length target.
7. Promote only through `python -m autoyy voiceover promote <topic-folder>`. The command refuses stale/missing gates and checks the candidate against sibling final scripts for cross-topic copying before creating the final file.
8. Keep long leases alive with `autoyy work heartbeat`; release a lease only after that topic's work is handed to the next gate, then claim the next topic. After all topics, run `python -m autoyy voiceover validate <project-root> --workers 4`. Any failed topic makes the batch `INCOMPLETE` and exit code 1. Never report overall completion because files merely exist.

Hard default limits: 4,500-5,500 non-whitespace characters, at least five source-backed direct quotations when the source contains enough real dialogue, no unsupported numeric claims, no banned AI/template shells, and no duplicated long paragraphs. Cross-topic hard failures include a shared contiguous block of 80+ characters, 30+ character matching blocks totaling over 8% of the shorter script, highly similar openings/endings, or highly similar long paragraphs. Fixed required profile signatures are excluded from the copy comparison.

Automatic writer -> Humanizer -> machine gate -> reviewer repair loops may run at most three times. A third failure becomes `blocked_quality`; do not weaken thresholds, fabricate filler, or mark the topic complete.

### 7. Create publication information

Learn from peer hits before writing, then keep the library evolving:

1. Run `python scripts/peer_hit_library.py --patterns --platform douyin` and inspect count, median, P75, peak, and confidence. Do not compare unlike platforms as one population by default.
2. Use `--list --platform <platform> --category <category>` to inspect similar-topic evidence. Treat low-sample patterns as weak evidence, not a winner.
3. The mutable library defaults to the current project's `.autoyy/peer-hit-library.csv`; `assets/peer-hit-library.csv` is a read-only seed/template.
4. Write the title from the topic's checked factual hook and the evidence patterns. Imitate structure only; never copy a peer title.
5. After publishing or observing a peer hit, feed dated evidence back with `--add` or `--import`. Use `--dry-run` before bulk imports when appropriate.

Write `发布信息.txt` as:

```text
<one accurate curiosity-driven title of at most 25 characters>
#标签1 #标签2 #标签3 #标签4 #标签5
```

Use exactly two non-empty lines with no field labels, blank lines, emoji, publishing advice, platform notes, or extra copy. Count every Chinese character, digit, Latin letter, punctuation mark, and space toward the 25-character title limit. Use Douyin-style information gaps built from a factual number, contrast, consequence, or question, but do not exaggerate beyond the source. Use exactly five topic-specific hashtags on the second line.

For a bulk refresh:

1. Recursively find every existing `发布信息.txt`; include nested archive folders and do not create a file in a container-only directory.
2. Read each topic folder name and its existing publication text. Preserve the topic and any accurate, specific tags; rewrite only what is needed.
3. Produce a unique title for each topic. Do not reuse one template across the batch.
4. Overwrite only `发布信息.txt` after confirming the discovered file count equals the planned update count. Do not touch video, subtitles, voiceover, or covers.
5. Run `python scripts/validate_publication_info.py <root>` and scan for legacy fields such as `爆款标题：`, `匹配标签：`, `发布建议：`, and `版权提醒：`.

### 8. Generate covers

Read the cover section in `references/content-style.md` and use the relevant style-reference asset.

- Topic-cover mode: create 3:4 and 4:3 photorealistic covers. Put a six-character main title and eight-character subtitle in the upper half, each on one line; make the subtitle about two-thirds the width of the main title.
- Collection-cover mode: create 1:1 and 4:3 photorealistic covers. Put the exact four-character collection name on one centered line with no subtitle.

For topic covers, match the approved reference typography precisely: an extra-large vivid golden-yellow rough brush-calligraphy main title with a thin black outline and strong soft black shadow, followed by a medium-large white rough brush-calligraphy subtitle with the same black outline and shadow. Center both lines in the upper half, keep the subtitle about two-thirds the main-title width, and leave safe margins around every character. Use high contrast and no extra text, logo, or watermark. Inspect every generated image for exact Chinese text and actual pixel ratio before saving.

#### 即梦 (Jimeng) cover-prompt mode

When covers are generated externally via 即梦 instead of an in-pipeline image tool, write one `封面提示词-即梦.txt` per topic directory containing exactly two plain prompt paragraphs separated by a blank line — no instructions, headers, or usage notes inside the file:

- Paragraph 1: `3:4竖版构图` prompt (portrait scene + text block placed at the upper-third).
- Paragraph 2: `4:3横版构图` prompt (landscape wide scene + text block placed upper-center).

Shared typography per topic: a six-character main title in extra-large golden-yellow rough brush calligraphy (thin black outline, soft black shadow), plus an eight-character white rough brush-calligraphy subtitle at about two-thirds the main-title width. Portrait scenes favor depth/close-up compositions; landscape scenes favor wide establishing shots. Both paragraphs must render the same title/subtitle pair.

Batch generation: fill the CSV template (see Resources) with one row per topic (`目录名,主标题,副标题,竖版画面,横版画面`) and run:

```powershell
python scripts/gen_jimeng_cover_prompts.py <output-root> --csv <filled-csv>
```

The script treats 6+8 title lengths as hard validation. Invalid rows fail without writing. Existing standard cover files or a non-empty prompt are preserved unless `--force` is explicit; arbitrary unrelated images do not count as completed covers.

### 9. Validate and hand off

Expected topic directory:

```text
<NN-中文选题>/
├── 高清源视频.<mp4|mkv|webm>
├── 字幕.srt
├── 发布信息.txt
├── 爆款口播稿.txt
├── 封面-3比4.<png|jpg>
└── 封面-4比3.<png|jpg>
```

Run:

```powershell
python scripts/validate_deliverables.py <output-root>
python scripts/validate_subtitles.py <output-root> --ffprobe-location "<ffmpeg-bin-dir>"
```

Do not report success until deterministic validation and manual checks agree. Report completed, incomplete, and blocked topics separately.

## Resume and overwrite rules

- Treat existing user files as authoritative unless they are known outputs of the current run.
- Skip complete video and subtitle files.
- Never replace an approved script or cover without explicit instruction.
- Keep logs and manifest state so a stopped batch can continue.
- Use versioned filenames while drafting; after a verified promotion, remove superseded voiceover copies and retain only the final standard filename.

## Resources

- Read `references/workflow-spec.md` for manifest fields, stage gates, and acceptance checks.
- Read `references/tool-map.md` for the end-to-end stage, tool, supporting-skill, and output map.
- Read `references/content-style.md` before writing scripts, publication information, or covers.
- Use `vendor/blader-humanizer/SKILL.md` in Embedded mode for every generated or revised voiceover. `vendor/blader-humanizer/LICENSE` records the bundled upstream license.
- Read `references/downloads.md` before media retrieval or cookie/proxy troubleshooting.
- Use `assets/topic-manifest-template.csv` as the batch manifest schema.
- Use `assets/term-glossary.csv` to keep documentary technical terms translated consistently across scripts and subtitles.
- Use `assets/peer-hit-library.csv` only as a tracked seed/template.
- Store evolving peer/post-publish evidence in `<project>/.autoyy/peer-hit-library.csv` or an explicit `--library` path so normal use never dirties the repository.
- Use `assets/topic-cover-3x4-approved.png` and `assets/topic-cover-4x3-approved.png` for the approved topic-cover typography, scale, color, outline, shadow, and layout.
- Use `assets/collection-cover-1x1.png` and `assets/collection-cover-4x3.png` for collection-cover style.
- Use `assets/jimeng-cover-template.csv` and `scripts/gen_jimeng_cover_prompts.py` for batch-generating 即梦 cover prompts (two plain paragraphs: 3:4 + 4:3).
- Use `scripts/download_from_manifest.ps1` for downloading (validated resume, aria2c acceleration, optional parallel rows).
- Use `scripts/transcribe.py` for the local ASR fallback when a video has no subtitle (FunASR preferred, faster-whisper built-in; skips folders that already have SRT).
- Use `scripts/validate_subtitles.py` for SRT syntax and subtitle-video alignment checks.
- Use `scripts/peer_hit_library.py` to learn from and evolve peer hit publication titles/tags (`--patterns` / `--list` / `--add` / `--import` / `--template`).
- Use `scripts/validate_publication_info.py` for recursive two-line publication metadata validation.
- Use `scripts/validate_deliverables.py` for deterministic acceptance checks.
