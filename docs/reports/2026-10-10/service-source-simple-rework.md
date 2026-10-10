# Simple rework of the source-retention replacement

Date: 2026-10-10. Working branch: `refactor/service-source-simple`, from
`2b6502e`. The original replacement was parked on
`refactor/service-authoring` at `bc4b49d` after the 2026-10-09 review
concluded it overreached: its acceptance benchmarks failed (large/service
time +39.0% against noise 3.2%, hover +44.4%), and the language-service
design it was meant to serve (docs/archive/language-server.md § 5) needs no
canonical-record machinery.

## 1. What landed

Six commits, each independently useful, none building the record
architecture:

| Commit | Change |
|---|---|
| `3a3553c` | Shared value descent extracted to `fastraml/types/navigation.py` (parked-branch pick; representation-agnostic). |
| `2873223` | Post-parse include reads no longer enter the parse's text store (`fastraml/parser/includes.py`); restores the docs/20 § 2 contract that a file is decoded at most once per parse. |
| `2e724b5` | The deprecated `schemas:`/`schema:` spellings are recorded in `Raml.syntax_aliases` as they are decoded; `deprecated-schemas` reads the records, so no rule in the default set requires retained trees and service snapshots parse text-only by default (docs/21 § 2). |
| `885a16f` | `visible_names`: a fragment resolver's local and directly imported declaration candidates, for completion (docs/04 § 2). |
| `efd9630` | `Workspace.source`: a file's current text composed once per text and held by the workspace, invalidated on change, close or disk change; folding and selection serve from it (docs/21 § 4). New `source-structure` feature workload with its reach test. |
| `661e063` | The general corpora carry the deprecated spellings (docs/18 § 6), so the parse workloads exercise the compatibility recording, pinned by a reach test. |

## 2. First-use mechanics

The service composes a file's YAML tree in two independent caches, each
built lazily on first interaction:

- The hover index (`fastraml/service/hover.py`, `_node`) composes the
  file's snapshot text into a per-snapshot cache. The first hover or inlay
  that needs the tree pays it: any field hover reaches it through the
  field documentation, and a hover in a literal include additionally
  triggers the one-time context discovery that composes every fragment
  file.
- The workspace source cache (`fastraml/service/workspace.py`,
  `Workspace.source`) composes the file's current text into a per-buffer
  cache. The first folding or selection request for that text pays it.

Cost, measured on the hover corpus at 400 families (entry 304 KiB,
13,204 lines): one `compose` is 60.1 ms best of 5 (61.1, 60.7, 62.9,
62.3, 60.1), against a full unwrap+validate parse of the same file at
158.8 ms best of 5. One composition is about 38 percent of one parse, or
0.2 ms per KiB. Nothing is built eagerly; the cache is built by whichever
interaction first needs the tree.

## 3. Numbers

`bench ab 2b6502e`, 3 rounds, best of 3 per round, at `efd9630`; the
`large/service` row is re-measured on the spelling-carrying corpora of the
last commit:

| Workload | A ms | B ms | time | noise | peak | kept |
|---|---:|---:|---:|---:|---:|---:|
| `hover/unwrap` | 274.8 | 349.4 | +27.1% | 3.3% | +16.6% | +4.0% |
| `inlays/unwrap` | 291.1 | 354.9 | +21.9% | 4.8% | +12.9% | +3.3% |
| `effective-types/unwrap` | 209.9 | 212.7 | noise | 2.6% | noise | -27.5% |
| `large/service` | 476.0 | 474.0 | noise | 6.1% | -20.1% | -24.0% |

New workload, no base number, so linearity (`--repeat 3`):
`source-structure/unwrap` time 1.004 (113.4 / 56.5 ms), peak 0.998,
retained 1.000.

General parse workloads, on corpora that carry the deprecated spellings
(pinned by `test_general_corpora_exercise_the_deprecated_spellings`): every
other `large` library declares its types under the `schemas:` alias, every
other inherited type uses the `schema:` facet, and `endpoints` declares its
payload type in a `schemas:` table with every fourth body typed by
`schema:`. With the spellings in the input, `large` across all eight
configurations, and `endpoints` and `small` on parse, unwrap and
unwrap+lint, are within noise on time. Allocation peak and kept move +0.4 to
+0.8 percent on the configurations that hold the model — the
`syntax_aliases` records themselves, about 1.3 thousand of them, roughly
120 KB on `large` — and are noise where the model is dropped. The include
text-store fix still touches only `load_include`, which join calls and no
bench workload does, and the lint rule's record read, against the base's
retained-tree walk, is within noise on the lint configurations.

Attribution: at `2e724b5` (before the last two commits) the same A/B
already shows hover +25.6% and inlays +23.5%, and comparing `2e724b5` to
`efd9630` moves neither. The entire first-use delta is `2e724b5`'s:
snapshots stopped retaining parse trees, so the first hover or inlay per
snapshot composes the file on demand. The deltas match one composition
each (hover +74.6 ms, inlays +63.8 ms, against 60 ms of compose).

## 4. Interpreting service-workload deltas

The parked branch's number misled because the workload lumps three costs
into one number. A service request's cost decomposes as:

1. The parse: the snapshot rebuild, paid per edit.
2. First use: one composition per file per snapshot, paid by whichever
   interaction needs the tree first.
3. Repeated use: cached, about zero.

Against that, the three designs differ on two axes:

| | parse side | first use | repeated use |
|---|---|---|---|
| `2b6502e` (retained trees) | composes every file's tree and keeps them | free | hover/inlay cached; folding and selection re-compose per request |
| parked `bc4b49d` (records) | builds a canonical record for every file, plus the model | projects a tree from the record, more than a compose | cached from the record |
| `efd9630` (this branch) | lighter than the base | one text composition, about 38% of a parse | everything cached |

The parked branch regressed the parse side (paid on every edit, which is
why large/service moved +39.0%) and the first-use side (projection is
dearer than compose). This branch regresses only the first-use side, by
exactly one composition, and improves the parse side's memory. The hover
workload's cold-snapshot convention (docs/12 § 4) is the right place for
that first-use number, because an editor session rebuilds the snapshot on
every edit, so first use recurs once per file per edit. The repeated-use
side is not A/B-able — no existing workload drives folding or selection
repeatedly, and a new workload has no base number by docs/12 § 5 — so the
branch pins it instead: the `source-structure` reach test counts `compose`
calls and requires each measured pass to compose the text once, shared by
fifteen requests.

Residual inefficiency: a clean file is composed twice per snapshot, once
from the snapshot text for the hover index and once from the buffer text
for the workspace cache. Sharing one tree between them when the texts are
identical is the first revisit candidate (docs/15 § 2).

## 5. Decision

Keep `2e724b5`. The trade is one first-use composition per file per
snapshot (about 60 ms on the 304 KiB corpus entry, one time per edit) for
a persistent memory reduction in the long-running server: large/service
peak -20.1%, kept -24.0% (measured on the spelling-carrying corpus), and
effective-types kept -27.5%, with the edit loop's time within noise.
Accepted with the revisit note in docs/15 § 2; the interpretation rule
above is recorded in docs/12 § 5.

## 6. Verification

Gate (`uv run ruff check . && uv run ruff format --check . && uv run mypy
fastraml/ && uv run pytest -q`) green at `efd9630`. Measurement commands:

```bash
uv run python -m bench ab 2b6502e --bench hover --bench inlays --bench effective-types --config unwrap --rounds 3 --repeat 3
uv run python -m bench ab 2b6502e --bench large --config service --rounds 3 --repeat 3
uv run python -m bench ab 2b6502e --bench large --config parse --config unwrap --config validate --config unwrap+validate --config unwrap+graph --config unwrap+lint --config unwrap+occurrences --rounds 3 --repeat 3
uv run python -m bench ab 2b6502e --bench endpoints --config parse --config unwrap --config unwrap+lint --rounds 3 --repeat 3
uv run python -m bench ab 2b6502e --bench small --config parse --config unwrap --config unwrap+lint --rounds 3 --repeat 3
uv run python -m bench ab 2b6502e --bench datatype-fragments --config parse --config unwrap --config unwrap+lint --rounds 3 --repeat 3
uv run python -m bench linearity --bench source-structure --repeat 3
```
