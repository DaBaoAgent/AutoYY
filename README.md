# AutoYY

AutoYY is a Windows-first Codex/Hermes/Workbuddy workflow for producing traceable Chinese documentary-explainer content packages from performance data and authorized long-form source media.

It combines topic/source planning, resumable media/subtitle preparation, SRT-first voiceover writing, deterministic batch quality gates, publication metadata, cover workflows, stateful resume, and final package validation.

## Why this version is strict

Multi-topic writing must not trade quality for throughput. AutoYY treats every topic as an independent unit: one source context, one candidate script, one Humanizer pass, one fact check, one independent reviewer result, and one deterministic gate. A batch is complete only when every topic passes.

The final `爆款口播稿.txt` should not be written directly. Writers create `爆款口播稿.candidate.txt`; AutoYY promotes it only after the current source/script hashes and all required quality gates pass.

## Requirements

- Windows 10/11 is the primary supported runtime.
- Python 3.11+.
- `yt-dlp`, `ffmpeg`, and `ffprobe` for media workflows.
- Node or Deno is recommended for current YouTube extraction.
- aria2 is optional for download acceleration.
- FunASR or faster-whisper is optional for local subtitle fallback.

Set `AUTOYY_WORK_ROOT` to choose the default project-output root. On Windows the fallback is `D:\自动剪辑`.

## Install for development

```powershell
git clone https://github.com/DaBaoAgent/AutoYY.git
cd AutoYY
python -m pip install -e ".[dev]"
python -m autoyy doctor
```

For Codex Skill installation, clone or junction the repository into the Codex skills directory. Keep production outputs outside the skill repository.

## Stable CLI

```powershell
python -m autoyy doctor
python -m autoyy validate <project-root>
python -m autoyy publication validate <project-root>
python -m autoyy voiceover scaffold <topic-folder>
python -m autoyy voiceover validate <project-root>
python -m autoyy voiceover promote <topic-folder>
python -m autoyy peer patterns --platform douyin
python -m autoyy state show <project-root>
python -m autoyy state approve <project-root> <topic> voiceover --reason "reviewed"
python -m autoyy state force <project-root> <topic> <stage> --reason "rerun requested"
```

The compatibility scripts under `scripts/` remain available for existing automation. Exit-code contract is `0=success`, `1=work completed with failed/incomplete items`, and `2=invalid input, missing required dependency, or unusable state/schema`.

## Batch voiceover quality contract

For two or more topics, the writer stage uses one topic per worker/context by default. Do not concatenate several SRTs into one prompt and generate several final scripts in one call.

Per topic:

Quality records use `attempt=1..3`, `humanizer.mode=embedded`, `reviewer.independent=true`, and fact/reviewer coverage counts. A third failed attempt becomes `blocked_quality` rather than triggering an unlimited rewrite loop.

1. Read the full `字幕.srt`; summaries are navigation aids only.
2. Write `爆款口播稿.candidate.txt`, not the final filename.
3. Run the bundled `vendor/blader-humanizer/SKILL.md` in Embedded mode on that candidate.
4. Run source-grounded fact verification and an independent semantic reviewer.
5. Create/update `.autoyy/voiceover-quality.json` with the current source/script SHA-256 values, `srt_full_read=true`, Humanizer/fact/reviewer pass state, and evidence entries containing both `source_text` and the corresponding `script_excerpt`.
6. Run `python -m autoyy voiceover promote <topic-folder>`.
7. After all topics, run `python -m autoyy voiceover validate <project-root>`.

The gate enforces 4,500–5,500 non-whitespace characters by default, at least five source-backed direct quotations, source-backed numeric claims, evidence-anchor coverage, banned AI/template-pattern checks, duplicate-paragraph checks, and cross-topic copy/template detection. One failed topic makes the batch `INCOMPLETE` and returns exit code 1. Verified supplemental facts outside the SRT must be recorded as `source_kind=verified_source` evidence with a source reference and verification date.

Cross-topic hard failures include a shared contiguous block of 80+ characters, matching blocks of 30+ characters totaling more than 8% of the shorter script, highly similar openings/endings, or highly similar long paragraphs.

## Resume state

Each production project may contain `.autoyy/state.json`. It records stage status and non-secret fingerprints for source, subtitle, voiceover, publication, cover, and package validation. Changing an upstream fingerprint marks dependent ready stages stale. A corrupted state file is reported rather than silently replaced. Ready stages can be explicitly approved; forcing or changing an upstream fingerprint revokes affected approvals and marks dependents stale.

The user evidence library also defaults to the project `.autoyy/peer-hit-library.csv`; the tracked asset is only a seed/template. Normal use therefore does not dirty the skill repository.

## Cover workflows

`gen_jimeng_cover_prompts.py` creates strict 3:4 and 4:3 external-generation prompts and rejects titles that are not exactly 6+8 characters. Existing prompts/covers are not overwritten without `--force`.

`build_topic_covers_from_video.py` is the local deterministic fallback. It requires explicit topic cover text, Pillow, ffmpeg/ffprobe, and caller-provided fonts. It does not infer copy from historical folder names.

## Testing

```powershell
python -m compileall -q scripts src
python -m pytest -q
python -m pytest --cov=autoyy --cov-report=term-missing --cov-fail-under=85 -q
python -m ruff check .
git diff --check
```

CI runs on Windows with Python 3.11 and 3.12 and exercises the PowerShell compatibility entry plus parallel fake-download integration without depending on live YouTube access.

## Troubleshooting

Run `python -m autoyy doctor` first. Missing optional ASR backends or aria2 do not block unrelated stages. A failed project stage should be repaired and resumed instead of forcing a ready status or deleting approved outputs.

## License

AutoYY is released under the MIT License. Bundled third-party code keeps its own license notice; see `vendor/blader-humanizer/LICENSE`.
