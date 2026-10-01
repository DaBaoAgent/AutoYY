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

## Existing-artifact reconciliation

A read-only reconciliation pass over the representative real batch verified 13 already-present standard-topic source videos with ffprobe plus sampled media fingerprints in about 1.1 seconds. Those topics can resume as `source=ready` instead of being downloaded again. Reconciliation does not trust file existence alone, and `--dry-run` performs no state mutation.

## Control-plane soak and fault injection

The endurance harness now supports up to 500 synthetic topics, random stage faults, simulated killed workers, and duration-bounded overnight runs. It exercises the real scheduler, lease files, state transitions, capacity limits, and crash recovery while excluding media/network/model time.

A deterministic 100-topic run with eight workers, 8% stage faults, and 5% worker crashes injected 42 ordinary failures plus 40 crashes. It recovered all 100 topics with zero duplicate claims, dangling leases, capacity violations, or failed stages. Removing repeated inventory scans from the atomic claim path reduced the same seeded workload from 36.483 s (18.69 ops/s) to 27.869 s (24.47 ops/s), about 23.6% lower elapsed time.

A deterministic 500-topic run with eight workers, 5% stage faults, and 2% worker crashes injected 137 ordinary failures plus 82 crashes and completed all 500 topics. Before batched same-wave state checkpoints it took 344.697 s at 9.34 ops/s. Batching each worker wave into one atomic state checkpoint reduced the same seeded workload to 203.921 s at 15.79 ops/s: about 40.8% lower elapsed time and roughly 69% higher control-plane throughput.

For both completed runs:

- duplicate topic claims: 0
- dangling leases: 0
- stage-capacity violations: 0
- failed stages remaining: 0
- workload complete: true
- invariants OK: true

CI runs a smaller deterministic soak on every push. A separate endurance workflow can run the 500-topic case or a duration-bounded long test without slowing normal pull requests.

## Regression rule

Do not accept a performance optimization that weakens deterministic quality gates, source/subtitle verification, one-topic writer isolation, state revision safety, or package validation. Prefer structural reductions in redundant I/O and waiting over looser validation thresholds.
