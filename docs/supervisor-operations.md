# Supervisor Operations

`autoyy supervisor` is the persistent control plane for long-running AutoYY batches. It does not replace editorial, visual, or quality-review agents. It may automatically advance only deterministic machine stages already allowed by `runtime`: source acquisition, subtitle/ASR recovery, and final package validation.

## Safety boundaries

- Exactly one supervisor process may hold a project root at a time.
- `voiceover`, `publication`, and `cover` remain lease-bound external work.
- Queue priority and SLA change scheduling order only; they never weaken dependencies or quality gates.
- Cookies, proxies, and other credentials are not persisted in `supervisor.json`.
- A stop request uses an independent marker so an in-flight heartbeat cannot overwrite it.

## Core commands

```powershell
autoyy supervisor start <root> --manifest <manifest.csv>
autoyy supervisor run <root> --manifest <manifest.csv>
autoyy supervisor run <root> --manifest <manifest.csv> --stop-when-idle
autoyy supervisor status <root>
autoyy supervisor stop <root>
autoyy history <root>
autoyy queue show <root>
autoyy queue set <root> <topic> --priority 5
autoyy queue set <root> <topic> --sla-minutes 120
autoyy queue clear <root> <topic>
```

## Restart and crash recovery

The supervisor persists `.autoyy/supervisor.json` after every cycle. A new invocation creates a new session generation and records the previous session ID when the prior run did not end cleanly. Startup performs state recovery before new work is selected:

1. stale supervisor process locks are reclaimed only when the recorded PID is no longer alive;
2. stages left in `running` are converted to failed/interrupted state by the state recovery path;
3. expired work leases are removed;
4. disk artifacts are reconciled by the normal runtime path;
5. the persisted retry ledger is retained, so transient failures do not lose their backoff after restart.

`supervisor start` launches a detached child and writes stdout/stderr to `.autoyy/supervisor.log`. It deliberately refuses proxy/cookie command-line parameters; use foreground `supervisor run` or a secure OS service wrapper when those are required. `--max-cycles N` is useful for bounded service wrappers and tests. `0` means no cycle limit. `--stop-when-idle` exits after the configured number of consecutive idle/waiting cycles instead of polling forever.

## Fairness, priority, and SLA

Each runnable candidate begins with the normal stage/status strategy score. Queue policy then adds three independent terms:

- explicit priority (`-10..10`), bounded so it cannot permanently dominate the queue;
- SLA urgency for work due within six hours, one hour, or already overdue;
- wait-time aging, which grows continuously and eventually outranks a stream of newer work.

This policy is intentionally scheduling-only. It cannot make a blocked dependency runnable, approve a quality record, bypass a lease, or convert a failed gate to ready.

## History-driven tuning

`autoyy history` reads local structured events and reports per-stage samples, success/failure rates, p50/p95 elapsed time, failure-code counts, and observed throughput. The supervisor periodically derives conservative recommendations from those signals.

Current automatic tuning is deliberately narrow:

- repeated source network/rate-limit failures reduce download concurrency;
- a sufficiently stable source history may permit one extra download worker within the existing hard cap;
- elevated subtitle/ASR failure rate collapses ASR concurrency to one;
- GPU ASR concurrency may increase only when hardware capacity and a stable history both support it;
- package workers remain hardware-bounded because content-quality failures are not evidence of compute overload.

Explicit operator worker values override learned recommendations.

## Endurance and fault injection

The control-plane soak supports 1–500 synthetic topics, bounded operation counts, random stage failures, worker crashes, and an optional duration limit. A valid run requires no duplicate claims, no dangling leases, no stage-capacity violations, and no failed stages left after a completed workload.

```powershell
autoyy runtime soak --topics 100 --operations 1800 --workers 8 --fault-rate 0.08 --crash-rate 0.05
autoyy runtime soak --topics 500 --operations 6000 --workers 8 --fault-rate 0.05 --crash-rate 0.02
autoyy runtime soak --topics 500 --operations 100000 --duration-seconds 28800 --workers 8 --fault-rate 0.03 --crash-rate 0.01
```

The duration form is intended for overnight/endurance runs. CI should keep a smaller deterministic soak for fast regression feedback.
