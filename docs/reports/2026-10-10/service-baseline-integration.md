# Service baseline integration record

Date: 2026-10-10. Execution of the
[accepted recovery plan](../../research/2026-10-10/service-recovery-plan.md).

## 1. Measurement-only control

Base: `master` / `origin/master` at `2b6502e`. Branch:
`test/service-workload-baseline`. The measurement work is preserved on the parked
branch in `7c954ba`; this port includes its two mixed scenarios, reach tests,
profiling guidance, plan and dated evidence, without parked production changes.

The portable driver uses each revision's service interface. On original master,
folding and selection call the text-based query functions, as its LSP adapter
does. On newer revisions they use the workspace's source cache. Repeated outlines
are exercised whether or not a revision caches them; cache identity is checked
only where the implementation provides it. The suite has 32 workloads here,
not the parked implementation's 40.

Windows gate: Ruff, formatting, strict mypy and pytest pass, with **5,810 passed,
67 skipped, 1 xfailed**. The TCK is initialized at the original pinned submodule
revision. Local Docker's Linux engine is unavailable; GitHub CI must supply the
Linux/security verification before integration.

Linearity, three repeats, actual `unwrap` mixed scenarios:

| Workload | Full / half ms | Normalized time | Peak | Retained |
|---|---:|---:|---:|---:|
| `service-session` | 2,952.0 / 1,486.6 | 0.993 | 0.991 | 0.997 |
| `service-source-first` | 3,147.7 / 1,596.9 | 0.986 | 0.991 | 0.997 |

This verifies the historical uncached behavior is reached and scales. It does
not establish acceptance of the simplified production candidate.

## 2. Simplified candidate

Pending comparison against the measurement-only master control and inspection
of first-use/source ownership costs. Starting candidate: `fb0fb73`.

## 3. Selective recovery

Pending acceptance of the production baseline. The first planned experiment is
model-backed declaration inlays and `type_written`, without record capture.
