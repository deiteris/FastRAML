# Plan: partial models, then the language service

**Status: accepted; M1 in progress.** This document orders the work proposed in
`research/partial-models.md` (PM) and `research/language-server.md` (LS). It
is not normative. Each milestone amends its owning numbered document in the
same commit, as `AGENTS.md` requires. When the service lands, a normative
`docs/21-language-service.md` takes over from LS, and `docs/15` records
what remains.

Rules for every milestone:

- Work on a branch, one logical change per commit, and pass the gate for each
  commit.
- A behaviour change amends its owning document in the same commit.
- A performance claim needs a workload (`docs/12` § 5). The signals added in
  M1 and M2 must show no allocation delta on a valid corpus.
- Tests pin decisions and assert on message keys and `info`.

## Decisions, and the milestone each must precede

All seven were taken as recommended on 2026-09-26. D3 stays conditional: M3.3
tries (b) first and falls back to (a) if the TCK ratchet moves.

| # | Decision | Taken | Needed before |
|---|---|---|---|
| D1 | How duplicate chains collapse (PM § 6.1) | (b): keep one chain and record the other sites on it | M1.2 |
| D2 | Where the partial-model contract lives | `docs/13` § 1, with `docs/11` § 2 split into *reported* and *retained* | M2 |
| D3 | Whole-value template substitution (LS O5) | give the substitution the caller's node position if the TCK allows it, else keep a map from the substitution to the caller's node | M3.3 |
| D4 | Where the server lives (LS O1) | `fastraml/service/`, with the extras `fastraml[lsp]` and `fastraml[mcp]` | M4 |
| D5 | How roots are chosen (LS O2) | discovered from headers, with a `raml.roots` override | M4 |
| D6 | Where the syntax tables live (LS O3) | parser-owned tables that the decoders read | M5 |
| D7 | Libraries (LS O6, O7) | pygls; the official `mcp` SDK | M4, M7 |

## M1: Safety and noise in today's parser

No new API. This fixes defects that affect current users of `parse_lenient`,
`fastraml validate` and `contrib/sphinxcontrib-fastraml`.

**M1.1 Recursion marked after a P9 failure** (PM A6, A4).

- Code: `types/unwrap.py`. `unwrap_shapes` runs `finish_unwrap` on the shapes
  it flattened before it raises. A shape whose merge failed keeps its declared
  form and is not flagged unwrapped. `Raml.unwrapped` stays `False`.
- Documents: `docs/07` § 6 and `docs/11` § 2.
- Tests:
  - a recursive type next to an unrelated failing merge ends in
    `RecursiveShape`;
  - the failed shape, and each shape enclosing it, is neither flagged
    unwrapped nor merged.
- Done. `test_consumer_traversal.py` gained no case: the tree links named
  types by `$ref`, so its walk terminates with or without marking. The defect
  is in the object model, which `test_unwrap.py` walks.

**M1.2 One mistake, one chain** (PM A5, D1).

- Code: `errors.py` (`Accumulator` and `RamlError.append` collapse on the
  innermost frame and keep the other sites), and `parser/security.py` (bind
  once per written site).
- Documents: `docs/11` § 1 and § 2.
- Tests:
  - a trait applied N times gives one chain with N sites;
  - a root `securedBy` inherited over three levels gives one chain;
  - distinct mistakes at one position with different `info` stay distinct;
  - pickling round-trips the new slot.
- Check: the TCK ratchet is unchanged; `fastraml validate --json` output is
  reviewed for any consumer-visible change.

**M1.3 Siblings retained** (PM A1, A2).

- Code: the ten builder sites listed under PM A2. Each attaches its container
  before `raise_if_any()`.
- Documents: `docs/11` § 2 gains a *retained* column.
- Tests: for each row of that table, the siblings of a failed entity are
  present in the model. For example, one bad type keeps the good ones in
  `entry_point.types`, and one bad response key keeps the endpoint and its
  other operations.
- Check: the contrib suites (the Sphinx extension renders more of a broken
  document) and the viewer's committed JSON (which should not change, because
  it is built from valid input).

Exit criteria: every row of the PM § 2 table is re-measured, and A1, A2, A5
and A6 no longer reproduce.

## M2: Signals and the contract

**M2.1 Stage (S1).**

- Code: `registry.py` (`completed`, `stopped_at`, and an `IntEnum` named
  `Pass`), and `parser/entry.py` (`_run_passes` sets both).
- Documents: `docs/02` § 1 and § 3, and `docs/13` § 1.
- Tests: a failure at each pass sets the matching stage, and a clean parse
  sets `completed = P10`, or the last pass the options requested.

**M2.2 Broken entities (S2),** one commit per entity kind:

1. declarations with a bad facet;
2. responses, methods and resources;
3. `DirectiveRef` and `SecurityScheme`;
4. P7 shapes;
5. P8 extensions;
6. P9 shapes.

- Code: `Raml.broken: dict[int, RamlError]` and `is_broken()`. Each decoder
  that can keep a partly built entity keeps it and marks it.
- Documents: `docs/13` § 1 (the contract text from PM § 5) and `docs/02` § 4
  (invariants hold for entities that are not broken).
- Views: effective views check `completed` rather than `unwrapped`.

**M2.3 Mutation corpus** (PM § 8).

- Code: `tests/partial/` (or a marked module), which generates mutations of
  the valid TCK documents and the fixtures.
- Assertions: the contract, the traversal law, and view gating.
- The TCK part skips without the submodule, as the TCK tests do.
- Bench: `bench run` on `large` and `endpoints` shows no allocation delta.

Exit criteria: the mutation corpus passes, and the contract is in `docs/13`.

## M3: The occurrence index

**M3.1 View.**

- Code: `views/occurrences.py`, which builds the index after P10 from the
  flat registries (LS § 5.2), keyed by ID.
- The index drops, and counts, any occurrence that fails the law.
- `tests/unit/test_views.py` covers it.

**M3.2 CLI and law.**

- CLI: `fastraml refs --sites FILE NAME` prints `file:line:col`.
- Test: the law and the round trip (LS § 9), over the fixtures, and over the
  TCK when present.
- Bench: a workload that builds the index (`docs/12` § 5).

**M3.3 Driven by the law.** Each fix is committed with the law failures it
removes:

- G3: columns in quoted scalars (`docs/11` § 3);
- G2 by D3: the authored URI for each occurrence, and template substitution
  (`docs/06` § 3, `docs/08` § 4 and § 5);
- G9: source info beyond shapes.

Exit criteria: the law's drop count over the TCK is zero, or each remaining
drop is listed as a known limit (such as a partial template substitution).

## M4: Service and a read-only LSP

- Code:
  - `fastraml/service/`: a workspace, an overlay loader, root discovery (D5),
    dependents, debounce and flush, snapshots, position encoding, and queries;
  - an LSP adapter behind the `fastraml lsp` verb (D4, D7).
- Features:
  - diagnostics from the parser and lint, with a suppress quick fix;
  - definition, references and highlight;
  - hover, using `render`;
  - document and workspace symbols;
  - links, folding and selection ranges;
  - type hierarchy.
- Boundary: `docs/02` § 2 and `tests/unit/test_views.py` admit `service/` as a
  composition root.
- Documents: new `docs/21-language-service.md`. LS moves to `archive/`.
- Tests:
  - pygls' test client over the fixtures;
  - features gated by stage over the mutation corpus, never raising;
  - latency measured on `large`, to decide whether G8 is needed.

## M5: Completion

- Code (one commit each):
  - G4 grammar tables, with the decoders refactored to read them. Each
    construct is its own commit, and the TCK is unchanged;
  - G5 name enumeration in resolvers;
  - G6 sandboxed listing in `loaders.py`;
  - G10 value descent shared with P10.
- Service: cursor context (LS § 5.3), completion, signature help, and template
  parameters.
- Documents: `docs/04`, `docs/05`, `docs/08`, `docs/09`, `docs/03` § 5,
  `docs/10`, and `docs/21`.
- Tests:
  - each table key decodes, and one unlisted key gives `unknown field`;
  - completion offers exactly the table;
  - annotation names are filtered by `allowedTargets`.

## M6: Editing features

Rename, checked by reparsing (LS § 5.5); semantic tokens; inlay hints; the
effective view as a virtual document; code lens.

## M7: MCP

The `fastraml mcp` verb, with tools addressed by name over the same service
(LS § 6), in `docs/21`.

## Deferred, each only if measured

- G8, a compose cache shared across parses: only if M4's latency on `large`
  asks for it.
- Continuation gated by containment (PM § 7): only if the mutation corpus
  shows it adds no duplicated chain.
- G7, references inside unapplied templates.
