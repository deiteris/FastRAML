# pyRAML — project rules

A RAML 1.0 parser for Python 3.12+, modelled on go-raml. **`docs/` is normative.**
Start at `docs/README.md`; each document owns one area and states the decisions
that area has already settled.

## Before changing anything

1. Read the document that owns the area. `docs/02-architecture.md` maps areas to
   documents and to modules.
2. If the code must differ from a document, **amend the document in the same
   commit**. A doc that lies is worse than no doc.
3. Follow `docs/15-implementation-plan.md` phase order. It is dictated by
   dependencies, not by importance: the type system precedes endpoints, the merge
   precedes templates, validation comes last.

**Current state: Phases 0, 1 and 2 complete.** Phase 0: positions, errors, uris,
loaders, yamlnode. Phase 1: registry, fragments, includes, namespaces, datanode,
facets, references, annotations, the entry points and the pass driver (P0–P3).
Phase 2: the whole `types/` package — `BaseShape`, the seventeen kinds,
inference, examples, xml, and `make_shape`, wired into `types:`,
`annotationTypes:`, `baseUriParameters:` and the typed fragments. Three leaf
modules were built ahead of the critical path: the RDT expression parser
(Phase 3's), template variables and transforms (Phase 6's), and URI template
parsing (Phase 5's).

**Phase 3, resolution (P7), is next.** It drains `Raml.unresolved_shapes`:
parse each type expression, bind each name, and swap the real kind in for every
`UnknownShape`. Everything still deferred is retained as the original `Node` on
a `_raw_*` attribute; `grep -rn '_raw_' pyraml/` lists every seam, and a comment
beside each names the phase that decodes it. A brief per phase lives in
`docs/briefs/`.

## The gate

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
```

All four must pass before any phase is reported done. `mypy` is strict for
`pyraml.*` and lenient for tests, by design.

## Invariants — breaking one is a bug, not a diagnostic

- Every `location` is a `file://` or `http(s)://` URI. OS paths exist only inside
  `loaders.py`.
- A file is composed at most once, and decoded at most once, per parse.
- Structural merge never mutates either input, and preserves node identity.
- After resolution no reachable shape is an `UnknownShape`; after unwrap every
  reachable shape has `unwrapped is True` and `link is None`.
- Declaration order is preserved everywhere the model is exposed.

Full list with the pass that establishes each: `docs/02-architecture.md` § 4.

## Rules that look like style but are load-bearing

- **`Node` defines no `__eq__`/`__hash__`.** The provenance overlay is
  `dict[Node, ParseCtx]` keyed by object identity. Do not add equality, and do
  not key overlays by `id()` — that neither keeps the node alive nor stays unique.
- **`__slots__` on every model class**; `@dataclass(slots=True, eq=False)` when a
  dataclass suits. A generated `__eq__` on a recursive model is a correctness
  hazard as well as a cost.
- **Never `copy.deepcopy`.** Use the three documented clone operations
  (`docs/07-resolution-and-inheritance.md` § 5).
- **Accumulate errors; do not fail fast** — except for an unreadable entry file,
  a missing or unrecognised RAML header, a non-mapping root, and a fragment whose
  kind does not match its context.
- **Numeric facets never pass through `float`.** `Fraction` is built from the raw
  scalar text.
- **No per-character Python loops** where a compiled regex or a C-level string
  method will do. go-raml's byte loops are correct in Go and slow here
  (`docs/12-performance.md` § 12).
- Single quotes; `docs/` is excluded from ruff so illustrative code keeps its
  density.

## Working with the reference implementation

go-raml lives at `../go-raml-main`. The design documents already capture its
decisions, so **read targeted line ranges when you need a detail, not whole
files**. Reading it wholesale is what consumes a session's context.

## TCK

Fixtures are not vendored. They are found via `PYRAML_TCK_DIR`, falling back to
`../go-raml-main/raml-tck`; without either, TCK tests skip.

```bash
PYRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck -q
```

`tests/tck/ratchet.json` records the expected outcome per fixture. CI fails on
drift in either direction — a regression, or unrecorded progress. Regenerate with
`--update-ratchet` and read the diff before committing it.

## Testing

Pin decisions, not incidental behaviour. Assert on a diagnostic's message key and
`info` dict, never on assembled message text. Every documented corner case in
`docs/14-testing.md` § 2–3 gets a test that names the rule it protects.

Note that symlink-escape tests skip on Windows without Developer Mode. They are
the security-critical ones; trust CI's Linux job, not a local green run.

## Commits

One logical change per commit, present-tense subject with a `type:` prefix
(`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`). Work on a branch per
phase (`phase-1-fragments`); `master` holds completed phases.
