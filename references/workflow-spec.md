# Workflow specification

## Contents

- Stage gates
- Manifest schema
- Performance audit fields
- Topic scoring
- Source acceptance
- Folder acceptance
- Batch handoff

## Stage gates

1. **Audit complete:** every readable screenshot is processed, overlap is removed, missing values stay blank.
2. **Topic approved:** the topic has a clear audience promise and an evidence-backed score.
3. **Source approved:** URL, metadata, duration, resolution, content match, subtitle status, and rights note are verified.
4. **Media ready:** a playable HD source and an SRT exist, or the topic is explicitly marked blocked. When a video has no usable SRT, run the local ASR fallback (`scripts/transcribe.py`) before blocking the topic.
4.5. **Subtitle quality:** the SRT parses cleanly, timestamps are ordered, and the last subtitle's end time drifts less than the configured threshold from the video duration (`scripts/validate_subtitles.py`).
5. **Text ready:** the script is grounded, speakable, within target length, has completed the bundled Humanizer Embedded-mode pass, and passes a post-humanization fact check. The verified `爆款口播稿.txt` is the only retained voiceover copy; superseded script drafts are removed after successful promotion.
6. **Publication ready:** publication metadata passes the canonical two-line validator; peer evidence is read from the project-local `.autoyy/peer-hit-library.csv` with platform/category/sample-confidence context.
7. **Cover ready:** both ratios, exact Chinese text, and reference style pass visual inspection.
8. **Package complete:** deterministic validation and manual relevance checks pass.

## Manifest schema

Use UTF-8 CSV with these columns:

| Field | Meaning |
|---|---|
| `id` | Stable two-digit sequence |
| `folder_name` | `NN-中文选题` |
| `topic_cn` | Canonical Chinese topic |
| `video_title_cn` | Accurate Chinese translation |
| `original_title` | YouTube title verbatim |
| `url` | Direct watch URL |
| `channel` | Channel name |
| `duration_seconds` | Integer |
| `max_height` | Highest verified video height |
| `view_count` | Dated public view count |
| `verified_at` | ISO date |
| `source_language` | Primary language |
| `subtitle_language` | Preferred or available language |
| `match_grade` | `exact`, `high`, `adjacent`, or `reject` |
| `rights_note` | License/permission status |
| `notes` | Editorial or download note |

## Performance audit fields

Keep raw and normalized values separate: platform, screenshot, capture time, displayed date, raw title, canonical topic, views/reads, impressions, likes, comments, duration, and visible notes.

For each platform calculate sample count, total visible consumption, median, and top outliers. Explain that cross-platform totals combine different metrics and are not unique-user counts.

## Topic scoring

- **Danger/conflict:** clear stakes or failure cost.
- **Visual density:** new usable evidence or imagery every 10–20 seconds.
- **Counterintuitive insight:** one sentence overturns a common assumption.
- **Numeric scale:** money, time, people, distance, or size clarifies stakes.
- **Source/rights:** credible, high-quality, accessible source with usable authorization.

Use 20–25 as production-ready, 16–19 as conditional, and below 16 as reject or research-only.

## Source acceptance

Accept only when the URL opens as a single video, duration and resolution meet thresholds, the main narrative supports the proposed angle, source credibility is cross-checkable, subtitle availability is known, and the rights note is recorded.

An adjacent source may supply footage but cannot be the only factual basis for an exact-topic script.

## Folder acceptance

Confirm:

- exactly one primary source video under a standard name;
- at least one readable SRT;
- one publication title of at most 25 characters and exactly five hashtags on a separate second line;
- no headings, production notes, calls to action, repeated long paragraphs, assistant route markers, or unjustified AI-template sentence shells in the voiceover;
- exactly one retained voiceover deliverable named `爆款口播稿.txt`, with no superseded `爆款钩子文案.txt` or versioned `爆款口播稿-*` copies;
- the voiceover contains only final spoken text and shows no skipped Humanizer gate, exposed audit notes, fabricated details, or obvious template language;
- both requested covers have correct ratios and exact in-image text;
- no zero-byte files;
- no silently overwritten approved assets.

## Batch handoff

Report complete and incomplete counts, missing items, blocked reasons, subtitle fallback languages, script character range, cover ratio results, manifest path, and output root.

## Deterministic batch voiceover gate

For batches of two or more topics, the writer stage is serialized per topic by default. Each topic gets an isolated source context and must complete its own source read, candidate, Humanizer pass, fact check, reviewer, and machine gate.

The writer creates `爆款口播稿.candidate.txt`. The final `爆款口播稿.txt` is created only by `python -m autoyy voiceover promote <topic-folder>` after `<topic>/.autoyy/voiceover-quality.json` matches the current SRT/script hashes and every required gate passes.

The structured record must bind current source/script SHA-256 values and include `srt_full_read=true`, `attempt=1..3`, `humanizer.mode=embedded`, an independent reviewer, fact/reviewer coverage counts, and distributed evidence anchors. External supplemental facts require `source_kind=verified_source`, `source_ref`, and `verified_at`.

After all topics, run `python -m autoyy voiceover validate <project-root>`. Every topic must report complete. A partial batch returns exit code 1 and may only be described as incomplete/blocked.

## Project state

Production projects use `.autoyy/state.json` with schema version 1. Stages are `source`, `subtitle`, `voiceover`, `publication`, `cover`, and `package`, with statuses `pending`, `running`, `ready`, `blocked`, `failed`, or `stale`. Ready stages may be explicitly approved; a fingerprint change revokes affected approvals.

A changed upstream fingerprint marks dependent ready stages stale. State writes are atomic. A corrupted state file is an explicit error; it is never silently replaced. State contains no secrets. `autoyy state approve` records explicit approval; `autoyy state force` resets a stage to pending, revokes approval, and stales dependents without deleting outputs.
