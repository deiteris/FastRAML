# Plan: partial models, then the language service

**Status: accepted; M1 to M3 done, M4 in progress.** This document orders the work proposed in
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
| D1 | How duplicate chains collapse (PM § 6.1) | (b) was taken, then found moot: the duplicates are identical in every frame, so there are no other sites to record. Identical chains collapse; see M1.2 | M1.2 |
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

- Finding: the duplicate chains are identical in every frame, including the
  outer ones. No frame names the application or the inheriting level, so
  there are no sites to record, and a new `RamlError` slot would stay empty.
  Where the LSP wants them as related information, it reads the trait's or
  resource type's applications from the model (M3), not from the error.
- Code: `errors.py`. `Accumulator.result` drops a chain equal frame for frame
  to one already kept. It is the only place independent failures meet;
  `RamlError.append` has one caller, which appends a distinct detail.
  `parser/security.py` still binds an inherited scheme once per level. Only
  the report was wrong, so the binding is unchanged.
- Documents: `docs/11` § 2.
- Tests: a trait applied three times, a resource type applied twice, and a
  root `securedBy` inherited over three levels each give one chain, in strict
  and in lenient parsing. Chains that differ in position or `info` stay
  distinct, and the collapsed error pickles.
- Found on the way: an unresolved `securedBy` name puts the name into the
  message text (`reference not found: nope`), not into `info`, because
  `UnresolvedReferenceError` is wrapped as text (`docs/11` § 6).

**M1.3 Siblings retained** (PM A1).

- Finding: the builder sites hold two different defects.
  - A1, *lost siblings*: the five declaration maps were built, and then the
    decoder raised before the fragment assigned the map. The good siblings
    are sound and already registered, so retaining them needs no mark.
  - A2, *lost ancestors*: a bad key in a response drops its operation and
    every enclosing endpoint, because each level raises before its parent
    attaches it. Sibling top-level resources already survive. Keeping the
    ancestors means keeping *incomplete* entities, which without a mark
    repeats A4. A2 therefore moves to M2.2 item 2, where each kept ancestor
    is marked in the same commit.
- Code: `unmarshal_types` and `_definitions` fill the fragment's own map,
  passed in, and then raise.
- Documents: `docs/11` § 2 separates *reported* from *retained*.
- Tests: for each map, in an API and in a library, a bad entry keeps the good
  sibling in the fragment; the fragment and the registry agree; the error is
  the strict one.
- `make_parameter_map` and `documentation:` fail fast on their first error, so
  they have no siblings to lose. Whether they should accumulate is a question
  of recovery granularity, not of retention.

Exit criteria: A1, A5 and A6 no longer reproduce. A2 is carried to M2.2.

## M2: Signals and the contract

**M2.1 Stage (S1).**

- Finding: an `IntEnum` of pass numbers, holding the last pass that
  completed, cannot answer "did P6 run". The driver runs P6 with P4, before
  P5, and P9 is optional while P10 is not, so a later stage can finish
  without an earlier one.
- Code, as done: `Stage` in `registry.py` names the driver's seven steps in
  execution order. `Raml.completed: list[Stage]` lists the steps that
  finished and `Raml.stopped_at: Stage | None` names the one that raised.
  `Raml.stage(...)` is a context manager that records both, and
  `_run_passes` runs each step inside it. `Stage` is exported.
- Documents: `docs/02` § 1 (the stage table) and `docs/13` § 1.
- Tests: a failure at each stage names it and lists every earlier stage; a
  clean parse lists exactly the stages its options ran.
- `Raml.unwrapped` is now a read-only property, `Stage.UNWRAPPED in completed`,
  rather than a second record of the same fact. `Raml.is_unwrapped`, which
  only returned it, is removed.

**M2.2 Broken entities (S2),** one commit per entity kind:

1. type and annotation type declarations. Done: `make_shape(attach=...)`
   registers the declaration before decoding it, and one `except` marks it
   and gives it an `UnknownShape` if no kind was settled, so a kept shape is
   never kindless. The mark is (a): every entity a failure passes through;
1a. trait, resource type and security scheme definitions. Done: each builder
   takes `attach`; `_one_definition` also marks a definition whose `!include`
   fails; a definition fragment attaches through a method that names it
   first; `describedBy:` attaches its description. Found while doing item 2:
   without this, a failed `describedBy` response was marked inside a scheme
   the model dropped;
2. responses, methods and resources: an operation or endpoint enclosing a
   failed child is kept and marked, which resolves A2. Done:
   `decode_source_endpoint`, `decode_source_operation` and `_decode_response`
   take an `attach` callable, and `decode_responses` fills the holder's map.
   P6 now also runs over a kept resource, so a strict parse can report an
   unused URI parameter beside the key that failed;
3. `DirectiveRef` and `SecurityScheme`. `SecurityScheme` done: marked in
   the `except` around `_bind`. A failure to apply a trait or resource type
   happens in the source IR, before any `Operation` exists. Decided on
   2026-09-26: mark what lacks the contribution rather than the reference,
   which has no `id`. `note_failure` records the failure on the
   `SourceOperation` or `SourceEndPoint` where it is caught, and stage 2
   copies it into `Raml.broken` for the entity that IR becomes. No ids move;
4. P7 shapes. Done: `resolve_shape` marks in its `except`, so a referrer
   that resolved a failing shape out of queue order is marked too;
5. P8 extensions. Done;
6. P9 shapes. Done: the M1.1 `except` also marks.

- Code: `Raml.broken: dict[int, RamlError]`, read by membership; no
  `is_broken()` accessor, which would only repeat it. Each builder takes an
  `attach` callback, attaches the entity as soon as it has an identity, and
  decodes its content inside `with raml.marking(entity)`. A pass that already
  has an `except` there (P7, P9, `make_shape`) calls `Raml.mark` in it, and
  `mark` is the one place a mark is written (decisions of 2026-09-26: attach
  first; mark every entity the failure passes through). A second failure
  joins the first, so a template's failure and one in the merged content are
  both on the mark; the same failure met again is kept once.
- Documents: `docs/13` § 1 (the contract text from PM § 5) and `docs/02` § 4
  (invariants hold for entities that are not broken).

**M2.3 Mutation corpus** (PM § 8).

- Code, as done: `tests/partial/test_mutations.py`. Each mutation deletes a
  line, indents one, adds an unknown key, corrupts a scalar or misspells a
  name. The TCK half is exhaustive over every valid document (about 21,500
  parses, 16 s) and skips without the submodule; the fixtures half parses
  every seventh mutation of every fixture file (4 s). A sample of one site
  per kind passed where the exhaustive run found three defects.
- Assertions: nothing but a fatal entry failure escapes; `completed` and
  `stopped_at` agree with the error; fragments agree with the registry;
  every mark is on an entity reachable through the model's public fields;
  no registered shape is kindless, and no marked one flagged unwrapped;
  containment terminates without a visited set; on an unwrapped model the
  tree, graph, lint and OpenAPI views run and the tree obeys the traversal
  law.
- Found, each fixed in its own commit before the corpus:
  - an `is:` entry skipped by the first-occurrence rule, or on a resource
    with no methods, was never bound; lint crashed on the clean parse
    (`docs/08` § 3.2);
  - a repeated mapping key was kept, last wins, so what the first held was
    reported and marked inside nothing; YAML 1.2 forbids it, and composition
    now rejects it (`docs/03` § 1);
  - one failing property cost a type its kind, and in P7 its siblings were
    marked inside nothing; the kind is now attached before its
    declarations are built (`docs/05` § 3).
- Bench: no allocation delta on `large` or `templates` from any of the
  three fixes.

Exit criteria: the mutation corpus passes, and the contract is in `docs/13`.
Met.

Later, the corpus left the gate: random mistakes find defects by volume, which
is exploration, not a test. It runs with `pytest --mutations`; each defect
above has a unit test that names its rule.

## M3: The occurrence index

**M3.1 View.** Done.

- Code: `views/occurrences.py`, which builds the index after P10 from the
  flat registries (LS § 5.2), keyed by ID.
- The index drops, and counts, any occurrence that fails the law.
- `tests/unit/test_views.py` covers it.
- As built (`docs/16` § 9): the definitions, references and links LS § 5.2
  lists, except template variables, custom facet uses and keys inside values,
  and `extends`, which has no position. A candidate the law rejects is kept
  in `dropped`, not only counted, so M3.3 can see each one. `at` returns a
  list, because a template applied twice defines one property span twice.
- Survey over the valid TCK documents and the fixtures: about 2,400 kept and
  53 dropped. The drops are template substitution (G2), `securedBy:` entries
  with parameters (a `SecurityScheme` keeps no name position), and properties
  that unwrap replaced with a recursion marker.
- Found: a coverage gap the law cannot see. A built-in written alone
  (`type: string`) is settled at decode, and P7 records no `TypeExprRef` for
  it, so hover on it has no occurrence. For M3.3.

**M3.2 CLI and law.** Done.

- CLI: `fastraml refs --sites FILE NAME` prints `file:line:col`.
- Test: the law and the round trip (LS § 9), over the fixtures, and over the
  TCK when present.
- Bench: a workload that builds the index (`docs/12` § 5).
- As built: `refs --sites` prints each site with its role, and `--json` gives
  one object per site. `tests/unit/test_occurrence_law.py` holds the drops
  as a ratchet, `DROPPED`, so each M3.3 fix removes its entries. The mutation
  corpus also builds the index on every partial model and checks the round
  trip. The bench configuration is `unwrap+occurrences`, which runs on every
  workload.
- Cost, after review: the first index took 91 ms on `large`, more than
  `build_graph` (82 ms), and needed `retain_source`, which keeps every YAML
  tree. `ParseOptions(retain_text=True)` now keeps the texts alone, and the
  index builds each candidate once, with no generators and no `Position`
  until one is asked for: 46 ms against the graph's 89 ms, for 24,854
  occurrences. `unwrap+occurrences` on `large` is 394 ms and 37.3 MB
  allocated, against 460 ms and 48.4 MB for `unwrap+graph`.
- Found: P7 places the name in a dotted type name, `Dot.Type`, past a dot
  that names no library (`Types/dot-notation-types`). Fixed in M3.3.

**M3.3 Driven by the law.** Each fix is committed with the law failures it
removes:

- G3: columns in quoted scalars (`docs/11` § 3);
- G2 by D3: the authored URI for each occurrence, and template substitution
  (`docs/06` § 3, `docs/08` § 4 and § 5);
- G9: source info beyond shapes.

Exit criteria: the law's drop count over the TCK is zero, or each remaining
drop is listed as a known limit (such as a partial template substitution).

As done so far, one commit each:

- the law's own findings: a dot that names no library is part of a type
  name; a `securedBy:` entry keeps where its name is written; a recursion
  marker is placed where the edge it replaces was written;
- a built-in written alone is found by the index, from the `type:` node the
  shape keeps. Recording it in the decoder cost every parse 5.9 % in
  allocation on `large`, so it is not recorded there;
- G3: `Position.within(text)` places a column past the quote of a quoted
  scalar on one line, for P7, the URI-template checks and the index;
- G2 by D3: (b) was tried first and fails. A `Node` does not know its file,
  so a substitution placed at the caller's node disagrees with the shape's
  `location` whenever the template is in another file, and a key would start
  after its own value. (a) is done instead: P4 records, for each substituted
  scalar, where each caller's value lies in it and where it was written
  (`docs/08` § 5.1). P7 places names and diagnostics there, and
  `TypeExprRef.location` names the file. The record is dropped when P7 ends.
  A partial substitution, `<<item>>[]`, is placed too; only a transformed
  value is not, because it is written nowhere;
- a substituted annotation name, `(<<tag>>)`, is placed where the caller
  wrote it: the decoder reads the record into `DomainExtension.name_site`,
  and P8 reports there (`docs/09` § B1);
- G9 is closed without parser work. Every trait, resource type and security
  scheme definition, endpoint, operation, response, body and documentation
  item already carries `key_pos` and a full-span `value_pos`, which is what
  symbol and folding ranges need (`language-server.md` § 8, G9).
  `tests/unit/test_entity_spans.py` pins them.

Remaining drops over the TCK, each a known limit: eight transformed values,
which are written nowhere. M3.3 is done.

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
  - every query on a parse stopped at each stage, never raising (not the
    mutation corpus, which is an exploration outside the gate);
  - latency measured on `large`, to decide whether G8 is needed.

As done so far:

- The service core, protocol-free (`docs/21`): the workspace, root discovery
  by header or by `roots` globs, the overlay loader inside the folder's
  sandbox, lazy snapshots dropped when a file they read changes, position
  conversion for UTF-8, UTF-16 and UTF-32, and the queries. The folder is
  listed through `SafeFileLoader.files`, an early part of G6, so OS paths stay
  in `loaders.py`.
- Found on the way: `type: !include` was recorded twice in `include_refs`.
  Fixed in its own commit.
- A dropped snapshot is freed before the next parse, since the server defers
  full collections for its whole run (`docs/21` § 2).
- The pygls adapter behind `fastraml lsp` (`docs/21` § 5): every M4 feature,
  debounced diagnostics with lint as a second tier, and the suppress quick fix.
- Latency on `large`: 477 ms per edit, against 360 ms for a plain parse.
  Composing the unchanged libraries is about a quarter of the parse, which
  bounds what G8 can save. Whether that asks for G8 is still open.
- Left: moving LS to `archive/`.

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
