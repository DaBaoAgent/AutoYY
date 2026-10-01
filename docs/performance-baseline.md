# AutoYY performance baseline

Measured on 2026-10-01 on the project development workstation. These numbers are regression baselines, not universal promises; hardware, source media, network conditions, ASR model size, and storage all change absolute throughput.

## Real workload inventory

Read-only profiling against a representative local production batch found:

- 30 direct child directories;
- 14 standard `NN-...` topic directories recognized by AutoYY;
- 13 additional topic-like legacy directories using `NN ...` naming that older batch discovery silently ignored;
- 312 files totaling about 31.2 GB;
- recognized standard topics contained about 12.3 GB.

`autoyy profile` now surfaces legacy candidates explicitly instead of allowing silent batch omission.

## Package validation

The real 14-topic package set was validated read-only with `require_quality=False` to isolate filesystem/ffprobe throughput:

| workers | elapsed |
|---:|---:|
| 1 | 1.763 s |
| 4 | 0.250 s |
| 8 | 0.203 s |

Four workers are therefore the default final-package validation setting; eight had small additional benefit on this workload.

## Large-media state fingerprint

A representative 1.39 GiB MP4 took about 2.145 s to compute a full SHA-256 fingerprint. The new large-media fingerprint hashes file size plus 1 MiB samples from the beginning, middle, and end after media readability has already been checked by ffprobe.

On the same file the sampled fingerprint took about 0.0037 s, roughly 580x faster, while small files still use full SHA-256.

## Download pipeline overlap

A controlled 20-topic fake-network workload modeled video download, subtitle metadata lookup, and subtitle download latencies. It measured:

| asset scheduling | elapsed |
|---|---:|
| serial within each topic | 3.663 s |
| overlapped metadata/subtitle work with video | 2.485 s |

This is about a 32% reduction in the controlled workload. Live-network results vary; no live copyrighted download is required for this benchmark.

## ASR worker pool

A controlled four-topic ASR workload with fixed 200 ms transcription work per topic measured:

| workers | elapsed |
|---:|---:|
| 1 | 0.857 s |
| 4 | 0.248 s |

The default remains one ASR worker to protect memory. Higher worker counts are explicit and each worker gets an independent backend model cache. Faster-whisper now decodes the source media directly instead of first materializing a full 16 kHz WAV; FunASR keeps the ffmpeg WAV path.

## Scheduler scaling

Synthetic metadata-only project roots were used to isolate state/discovery overhead. At 1,000 topics, representative runs were approximately 40-75 ms for status/profile/scheduler planning on the development machine.

Scheduler overhead is therefore negligible relative to writing, download, ASR, cover generation, or review work. Claim operations remain serialized briefly to enforce one-topic leases and stage concurrency budgets safely.

Default scheduler stage capacities are:

- source: 4
- subtitle: 1
- voiceover: 4
- publication: 6
- cover: 2
- package: 6

Available strategies are `finish-first` (default), `repair-first`, and `source-first`. These alter queue priority only; dependency gates and lease exclusivity do not change.

## Regression rule

Do not accept a performance optimization that weakens deterministic quality gates, source/subtitle verification, one-topic writer isolation, state revision safety, or package validation. Prefer structural reductions in redundant I/O and waiting over looser validation thresholds.
