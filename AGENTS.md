# fastRAML — project rules

A RAML 1.0 parser for Python 3.12+. `docs/` is normative: start at `docs/README.md`;
`docs/02-architecture.md` maps each area to its document and modules.

Status: the parser pipeline is complete and the TCK passes 984 of 984. Current
deferred work is listed in `docs/15-implementation-plan.md`.

## Before changing anything

1. Read the document that owns the area.
2. If the code must differ from a document, amend the document in the same commit.
3. Use `docs/15` for current status only; completed phases are archived history.

## The gate

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy fastraml/ && uv run pytest -q
```

All four must pass before work is reported done. `mypy` is strict for `fastraml.*`,
lenient for tests.

Benchmarks are a gate too (`docs/12` § 5). A performance claim needs a workload that
runs the changed code; if none does, add a feature workload and its reach test
first. Measure with `python -m bench ab BASE --bench NAME`, and put the time delta
(only if it exceeds the reported noise) and the allocation delta in the commit
message. For a new feature with no base number, use `bench linearity --bench NAME`.

## Layers and import boundaries

- `fastraml/parser/`, `fastraml/types/`: the passes P0–P10. A rule the RAML language
  states belongs in a pass, nowhere else.
- `fastraml/views/`: runs after P10 and holds no pass (`docs/16-graph.md`; linting
  in `views/lint/`, `docs/18-linting.md`). The model — `parser/`, `types/`,
  `nodes.py`, `registry.py`, `datanode.py` — never imports `fastraml.views`; outside
  `views/`, only `cli.py` does. One view imports another only through the substrates
  `walk`, `graph` and `severity`. `tests/unit/test_views.py` enforces all three.
  `pyoxigraph` and `fastraml-viewer` are optional extras imported inside their CLI
  verb.
- `fastraml/views/bindings/`: TypeScript, Python and Go backends for the tree's wire
  contract (`docs/16-graph.md` § 7). `bindings/schema.py` decides key sets and each key's
  structural kind; a backend only spells a kind. Code that does not vary with the
  schema lives in `bindings/static/`; edit it there and never embed target-language
  code in a Python string. `bindings/conformance/` drivers hold no expectations;
  `tests/unit/test_conformance.py` compares them.
- Consumers (`docs/17-consumers.md`): `viewer/` (React SPA, `npm run check`) and
  `contrib/` (eight separate `uv` projects, each with its own lock and gate, run by CI
  as a matrix). Nothing under `fastraml/` imports a consumer. A consumer may not hold
  a rule the RAML language states, nor invent one it does not; a missing rule is a
  gap in a pass or a view.
- Generated files, never edited by hand: `viewer/src/tree.d.ts`, `viewer/src/walk.ts`,
  `contrib/raml-codegen/raml_codegen/tree.py` and `walk.py`, the same two under
  `contrib/sphinxcontrib-fastraml/sphinxcontrib/fastraml/` (regenerate with
  `python -m fastraml.views.bindings <language> -o ... --runtime ...`), and the
  committed trees `viewer/public/api.json` (`npm run sample` in `viewer/`) and
  `contrib/raml-codegen/tests/api.json`. `tests/unit/test_bindings.py` fails on a
  stale copy and names the command that regenerates it.
  `contrib/raml-codegen/tests/inline.json` is the projection of `DOCUMENT` in
  `test_inline_types.py`; edit `DOCUMENT`, then regenerate it. Nothing checks it
  for staleness.
- `fixtures/` is read by six consumers. A change there moves `test_bindings.py`, the
  viewer's committed JSON, the `fastmcp-raml`, `raml-mock` and
  `sphinxcontrib-fastraml` suites, and `raml-codegen`'s `tests/api.json`.
- `raml-codegen` targets: named `<language>-<library>`, module path
  `targets/<language>/<library>/`; shared tree reading is in
  `targets/python/shared/`. A change to one target must not alter another target's
  golden output.

## Invariants — breaking one is a bug, not a diagnostic

- Every `location` is a `file://` or `http(s)://` URI. OS paths exist only inside
  `loaders.py`.
- A file is composed at most once, and decoded at most once, per parse.
- Structural merge never mutates either input, and preserves node identity.
- After resolution no reachable shape is an `UnknownShape`; after unwrap every
  reachable shape has `unwrapped is True` and `link is None`.
- Declaration order is preserved everywhere the model is exposed.

Full list, with the pass that establishes each: `docs/02-architecture.md` § 4.

## Rules that look like style but are load-bearing

- `Node` defines no `__eq__`/`__hash__`: the provenance overlay is
  `dict[Node, ParseCtx]` keyed by identity. Do not add equality; do not key overlays
  by `id()`.
- A `Node` is never edited after it is built: every childless node shares one
  empty `content` list, and `position` is cached. Build a new node with
  `with_content`/`with_value` instead (`docs/03` § 1); `tests/conftest.py`
  fails the session if the shared list is ever filled.
- `__slots__` on every model class; `@dataclass(slots=True, eq=False)` when a
  dataclass suits.
- Never import `copy`. Use `clone(memo)` or `clone_detached()` (`docs/07` § 6); a
  test enforces this.
- A `facets:` block declares what subtypes must supply. The P10 walk starts at the
  parents, all of them, so the declaring type need not satisfy its own required
  facets, and supplying a value for one is `unknown facet` (`docs/10` § 4).
- Where an annotation was applied rides `ParseCtx`, not a parameter. A decoder that
  establishes a new application site wraps itself in `Raml.target_scope(...)` and
  gets a test that names the site; a missing scope silently records the enclosing
  site (`docs/09` § B4).
- A shape's `location` (file authored in) and its `anchor`'s location (namespace its
  names resolve in) may differ legitimately, e.g. a library resource type reading
  `type: <<item>>` (`docs/08` § 4). Do not assert they agree.
- A template's variable index is keyed by node identity, never by position:
  optional-method filtering removes subtrees between scan and use (`docs/08` § 5).
- An alias shares its referent's containers on purpose (`docs/07` § 3); an
  inheritance merge sharing containers is a corruption (§ 4). Any traversal that
  reaches a type must follow `aliasOf`, or `User[]` reports `User`'s supertypes.
- Accumulate errors; do not fail fast. `parse_lenient` re-raises an unreadable
  entry file, or an unknown/unsupported header, fragment-kind mismatch, or
  non-mapping root only when the outermost frame is located at the entry URI;
  the same included-fragment failure returns a partial model.
- Numbers never pass through `float` on either side of a comparison. Build a facet's
  `Fraction` from the raw scalar text, and a float value from `repr(v)` (through
  `as_fraction`), never `float.as_integer_ratio()`. `multipleOf: 1.1` must accept
  `2.2`.
- `pattern:` and `/regex/` property names use `search`, not `fullmatch`; the author
  writes anchors (`docs/10` § 5).
- The discriminator inline-declaration rule runs before P9, because after unwrap
  every subtype carries an inherited discriminator. The check on discriminator
  values runs in P10, outside the `strict` gate (`docs/05` § 6).
- Read `Examples.entries()`, never `Examples.values`, which is empty when
  `examples: !include ...` is used.
- No per-character Python loops where a compiled regex or C-level string method will
  do (`docs/12` § 2).
- Compile every RAML regex through `compile_pattern` / `regex_engine` in
  `parser/facets.py`, so `regex_engine='re2'` covers it. `re2` is an optional extra.
- Single quotes. `docs/` is excluded from ruff.
- Cite a document section as `docs/NN § S` or `docs/NN-name.md § S`, where `S` is
  the heading's number (`4`, `3.1`, `B4`). `tests/unit/test_doc_refs.py` checks that
  every such reference resolves, and rejects `section N` spellings.

## TCK

Fixtures are the submodule `tests/tck/raml-tck`, under
`tests/tck/raml-tck/tests/raml-1.0/`. `FASTRAML_TCK_DIR` overrides the location;
without either, TCK tests skip.

```bash
git submodule update --init
uv run pytest tests/tck -q
```

`tests/tck/ratchet.json` records the expected outcome per fixture; CI fails on drift
in either direction. Regenerate with `--update-ratchet` and read the diff before
committing. A `fail` entry means outstanding work only (`docs/14` § 2): if a
fixture is wrong, fix it in the suite (`deiteris/raml-tck`, the submodule's origin),
recording go-raml's disagreement in its `KNOWN-ISSUES.md`. Never park it in the
ratchet.

## Consulting go-raml

go-raml (`https://github.com/acronis/go-raml`) is another RAML 1.0 implementation;
the TCK encodes its reading where the spec is silent. Open it only for that.

Reuse an existing clone if you have one; otherwise clone it outside this repository:

```bash
git clone --depth 1 https://github.com/acronis/go-raml
```

Read targeted line ranges, not whole files. To learn what it does, run it (needs
Go): `go test -run <name> .` in the clone, against a throwaway `zz_*_test.go`
deleted afterwards. Its comments have been wrong about its own behaviour.

## Testing

Pin decisions, not incidental behaviour. Assert on a diagnostic's message key and
`info` dict, never on message text. Every corner case a document records gets a test
that names the rule it protects.

Symlink-escape tests and `test_refuses_a_non_regular_file` skip on Windows. They are
security-critical: trust CI's Linux job, not a local green run. When CI is not
available, run Linux in Docker:

```bash
docker run --rm -v "$PWD:/src:ro" -w /w python:3.12-slim bash -lc \
  'cp -r /src/. /w/ && rm -rf /w/.venv && pip -q install uv && uv run pytest -q'
```

Run that before reading a slow Ubuntu CI job as slowness; a hang there has been a
real bug.

## Commits

One logical change per commit; present-tense subject with a `type:` prefix (`feat:`,
`fix:`, `docs:`, `chore:`, `test:`, `refactor:`). Work on a branch; `master` holds
completed work.

Before every commit, re-review the whole staged diff (`git diff --staged`) as a
reviewer would, not as its author. Look for:

- repeated logic that should be shared, and code or options nothing uses;
- a comment, docstring, README or `docs/` claim that the code no longer backs;
- a case the change handles for one kind of input but not its siblings;
- a test that would still pass if the behavior it names broke.

Fix what the review finds, rerun the gate, then commit.
