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
python -m autoyy resources
python -m autoyy runtime reconcile <project-root> --dry-run
python -m autoyy runtime plan <project-root> --manifest <manifest.csv>
python -m autoyy runtime run <project-root> --manifest <manifest.csv>
python -m autoyy runtime soak --topics 80 --operations 1000 --workers 8 --fault-rate 0.10
python -m autoyy profile <project-root>
python -m autoyy diagnose <project-root>
python -m autoyy schedule <project-root> --capabilities source,subtitle,voiceover --strategy finish-first
python -m autoyy validate <project-root> --workers 4
python -m autoyy batch inventory <project-root>
python -m autoyy batch status <project-root>
python -m autoyy batch plan <project-root> voiceover
python -m autoyy work claim <project-root> --worker-id <run-id> --stage auto --capabilities voiceover,publication --strategy finish-first
python -m autoyy publication validate <project-root>
python -m autoyy voiceover scaffold <topic-folder> --lease-token <token>
python -m autoyy voiceover validate <project-root> --workers 4
python -m autoyy voiceover promote <topic-folder>
python -m autoyy peer patterns --platform douyin
python -m autoyy state show <project-root>
python -m autoyy state approve <project-root> <topic> voiceover --reason "reviewed"
python -m autoyy state force <project-root> <topic> <stage> --reason "rerun requested"
```

`autoyy profile` reports real inventory size, discovery/state timing, ignored legacy topic candidates, and historical per-topic latency from local run events. `autoyy diagnose` summarizes recent structured error codes, active leases, failed/blocked stages, and actionable hints. `autoyy schedule` is read-only; `work claim --stage auto` applies the same scheduler and creates the atomic lease.

Scheduler strategies are `finish-first` (default: finish partially completed topics), `repair-first` (prioritize stale/failed work), and `source-first` (feed upstream stages). Stage concurrency budgets prevent an agent swarm from overloading download/ASR/cover resources.

`autoyy runtime reconcile` validates existing video/SRT artifacts against disk before resuming older projects; `runtime run` performs this reconciliation once at startup. `autoyy runtime` is the unattended control plane. Runtime completion is conservative: active leases report `waiting_active`, quality/other blocked stages report `blocked_work`, missing operator input reports `waiting_input`, and lease-bound editorial/visual work reports `waiting_external`; none of these states are reported as complete. The default pass budget scales with topic count; an ASR topic that fails is isolated for the remainder of that run so other topics can continue, then becomes retryable again on the next run. It may automatically execute only deterministic machine stages (`source`, `subtitle`, `package`). It stops at `voiceover`, `publication`, or `cover` with `waiting_external`; those stages remain lease-bound Agent/human work and keep their existing provenance/quality gates. Runtime planning fails closed on ignored legacy topic directories or a missing manifest unless the operator explicitly resolves/accepts the condition.

`--json` may be placed before or after the subcommand for agent-friendly compact output. The compatibility scripts under `scripts/` remain available for existing automation. Exit-code contract is `0=success`, `1=work completed with failed/incomplete items`, and `2=invalid input, missing required dependency, or unusable state/schema`.

## Batch voiceover quality contract

For two or more topics, one topic per worker/context is now a machine-enforced lease workflow, not just a prompt convention. Run `autoyy batch plan`, then `autoyy work claim`; a worker with an active lease receives the same topic again instead of consuming another directory. Do not concatenate several SRTs into one prompt.

Per topic:

Quality records use `attempt=1..3`, `humanizer.mode=embedded`, `reviewer.independent=true`, and fact/reviewer coverage counts. A third failed attempt becomes `blocked_quality` rather than triggering an unlimited rewrite loop.

1. Claim exactly one topic with `python -m autoyy work claim <root> --worker-id <run-id> --stage voiceover`; keep the returned lease token alive with `work heartbeat` during long runs.
2. Read that topic's full `字幕.srt`; summaries are navigation aids only.
3. Write `爆款口播稿.candidate.txt`, not the final filename, then create the quality scaffold with the matching `--lease-token`.
4. Run the bundled `vendor/blader-humanizer/SKILL.md` in Embedded mode on that candidate.
5. Run source-grounded fact verification and an independent semantic reviewer.
6. Create/update `.autoyy/voiceover-quality.json` with the current source/script SHA-256 values, `srt_full_read=true`, Humanizer/fact/reviewer pass state, and evidence entries containing both `source_text` and the corresponding `script_excerpt`.
7. Run `python -m autoyy voiceover promote <topic-folder>`.
8. Release the lease only after the topic has been submitted/reviewed, then claim the next topic. After all topics, run `python -m autoyy voiceover validate <project-root> --workers 4`.

The gate enforces 4,500–5,500 non-whitespace characters by default, at least five source-backed direct quotations, source-backed numeric claims, evidence-anchor coverage, banned AI/template-pattern checks, duplicate-paragraph checks, and cross-topic copy/template detection. One failed topic makes the batch `INCOMPLETE` and returns exit code 1. Verified supplemental facts outside the SRT must be recorded as `source_kind=verified_source` evidence with a source reference and verification date.

Cross-topic hard failures include a shared contiguous block of 80+ characters, matching blocks of 30+ characters totaling more than 8% of the shorter script, highly similar openings/endings, or highly similar long paragraphs.

## Resume state

Each production project may contain `.autoyy/state.json`. It records stage status and non-secret fingerprints for source, subtitle, voiceover, publication, cover, and package validation. Changing an upstream fingerprint marks dependent ready stages stale. A corrupted state file is reported rather than silently replaced. Ready stages can be explicitly approved; forcing or changing an upstream fingerprint revokes affected approvals and marks dependents stale.

The user evidence library also defaults to the project `.autoyy/peer-hit-library.csv`; the tracked asset is only a seed/template. Normal use therefore does not dirty the skill repository.

## Performance and observability

Long-running download/ASR work appends local structured events to `<project>/.autoyy/events.jsonl`. The log is local-only, rotates at 10 MiB, includes run IDs, topic/stage status, elapsed time, and stable error codes, and is consumed by `profile`/`diagnose`. It is not remote telemetry.

Large media files use a sampled state fingerprint (size plus beginning/middle/end SHA-256 samples) after ffprobe verification instead of rereading multi-gigabyte media end to end. Text and small files still use full SHA-256. Download overlaps subtitle discovery with video transfer by default. Worker concurrency is adaptive by default: a wave with substantial retryable network/rate-limit failures reduces the next wave, then stable waves recover toward the requested worker count. Use `--fixed-workers` only for troubleshooting; `--rate-limit` adds an explicit yt-dlp bandwidth ceiling. Command-level retries use bounded exponential backoff and stable failure classes.

Faster-whisper reads media directly and does not require an intermediate WAV. FunASR still uses ffmpeg audio extraction. `transcribe --device auto` chooses CUDA only when a usable NVIDIA GPU is visible; otherwise it uses CPU. A CUDA/CUDNN/OOM failure falls back to CPU for that topic instead of losing the batch. `transcribe --workers 1..4` remains bounded to avoid accidental RAM/VRAM exhaustion. Runtime also auto-sizes download/ASR/package workers from detected CPU, RAM, and GPU capacity. Final package validation defaults to four workers based on the recorded workload benchmark.

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
