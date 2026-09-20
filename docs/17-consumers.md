# 17 — Consumers

Everything that sits *on top of* the parser: the `viewer/` SPA, the
distributions under `contrib/`, and the document under `fixtures/` that several
of them read. None of it decides a RAML rule, and **nothing under `fastraml/` may
import any of it.**

This document settles the boundary and the gate. What each consumer *is* belongs
in its own README, which is where the detail lives.

## 1. Why they exist

A format with no consumer is a format whose gaps nothing measures. A unit suite
asks the questions its author already thought to ask; a consumer asks whatever
the job in front of it requires, which is how gaps in the model surface at all.

Each of these therefore uses the model rather than testing it. A construct the
viewer finds awkward to display is evidence about the projection. A construct
`fastmcp-raml` cannot reach — `queryString:`, which had no route through
`views/jsonschema.py` — is evidence about the view.

Because they are consumers, they may hold opinions a parser may not.
`fastmcp-raml` moves a JSON media type to the front of a body's list because the
caller it serves reads only the first. That is a decision about MCP rather than
about RAML, so it belongs on this side of the line.

## 2. The boundary

**Nothing under `fastraml/` may import a consumer**, and no consumer may hold a
rule that belongs to the language. The direction is one-way and total:

```
fastraml/  ←  viewer/            (through `fastraml tree` output, not through Python)
         ←  raml-codegen       (the same: output, not Python)
         ←  contrib/*          (through the public API in docs/13)
```

**One exception, and it is the exception that proves the rule: `fastraml-viewer`.**
It holds no rule — a directory of static assets, a function that locates them,
and a server that runs them — and it depends on nothing, so nothing can drift
under either end of an edge to it. `fastraml serve` imports it *optionally*,
inside the verb, the way `query` imports `pyoxigraph` ([13](13-public-api.md)
§ 8): a missing package is a message naming it rather than a crash, and
`import fastraml` never loads it. What the import asks for is where the files
are and how to run them — a `Path` and a socket — not a reading of the
language. The SPA in `viewer/` is still consumed through `fastraml tree`
output, not through Python: what `serve` imports is the *packaging* of the
bundle, not the bundle's reading of a document.

A consumer that needs a rule the parser does not have has found a gap in the
parser. The fix is a pass, or a view under `fastraml/views/` (docs/16), never a
reimplementation on this side. `views/jsonschema.py` is here because of exactly
that: `fastmcp-raml` needed JSON Schema, JSON Schema is a projection of the
model, and a projection of the model is `views/`.

The gap is sometimes only in the *export list*. `raml-mock` generates
`uniqueItems: true` arrays, which needs the equality `uniqueItems` is defined in
terms of; it was reaching into `fastraml.types.values` for it, past the surface
docs/13 names. Writing its own would have duplicated a rule of the language, so
`same_value` is exported instead (docs/13 § 8). `Conversion` followed, for a
different reason: `fastmcp-raml` converts a whole API into one JSON Schema
document, and the exported `to_json_schema` builds a fresh conversion per call,
so it cannot share a definitions table.

**A deep import is worth catching on its own account**, whichever of those it
turns out to be. A consumer ships pinned to `fastraml>=0.1,<0.2`, so a module
that moves inside a patch release breaks a published wheel — and the two cases
above import a *public* name by its internal path, which nothing would have
flagged. The check is one line:

```bash
grep -rn '^from fastraml\.\|^import fastraml\.' contrib/*/[a-z]*/ --include='*.py'
```

Anything it prints is either a name that belongs in `__all__` or an import that
should go through it.

The dependency does not run the other way either, even through the test suite.
`tests/` may read `fixtures/`, and may **read** a consumer's committed output —
`viewer/src/tree.d.ts`, `viewer/public/api.json`,
`contrib/raml-codegen/raml_codegen/tree.py` — but may not **import** `contrib`: a
consumer that the parser's own gate depends on is no longer downstream of it.

Reading is not importing, and the distinction is the whole of it. A generated
file is the parser's output that happens to be stored in a consumer, so the gate
checks it for the same reason it checks a golden: nothing else would notice it
going stale. `test_bindings.py` imports the *generated text* by writing a copy to
a temporary directory, never from `contrib/`.

### 2.1 The other direction: a rule the language does not have

The rule above has a mirror image that is easier to miss, because nothing fails
loudly. A consumer can grow support for a construct RAML does not define, and
then only its own fixtures will exercise it.

`raml-mock` did. It carried `4xx` response classes in five places — status
selection, the configured-status check, the state-error path, `RouteBehavior`
validation — and a test fixture declaring `4xx:` to drive them. RAML has no such
key: P4 rejects anything but a 3-digit code (docs/08 § 3), so no parsed
`Operation` could ever hold one and every one of those branches was unreachable
from a real document. The construct is OpenAPI's, arrived at by analogy.

The tell is that the *fixture* had to be invalid for the feature to be reachable
at all. A consumer's fixtures are not a second opinion about the language — they
are documents the parser must accept, and one the parser rejects is evidence
about the fixture first. The same question that § 2 asks of a missing rule
applies to a surplus one: is this in the spec? If not, it does not belong on
either side of the line.

## 3. `fixtures/`

One worked RAML document, exercising every construct the model carries —
libraries, a JSON Schema type, traits and resource types, six security schemes,
a discriminated hierarchy, a recursive type, a templated `baseUri`, a
`queryString`, four `documentation:` entries.

```
fixtures/sample/     api.raml, common.raml, invoice.json
fixtures/shared/     machine.raml, measures.raml, money.schema.json
```

`shared/` sits outside `sample/` so that the includes ascend. That is what puts
the workspace root under test: parse `sample/api.raml` without `-w fixtures` and
the loader refuses the ascent, which is the behaviour being checked.

**Five consumers read it, so a change here moves five things:**

| Reader | How it uses the document |
|--------|--------------------------|
| `tests/unit/test_bindings.py` | Regenerates `viewer/public/api.json` and fails if the committed copy differs. |
| `viewer/` | Renders that committed JSON. `npm run sample` rewrites it. |
| `contrib/fastmcp-raml` | Builds MCP tools from it; its suite asserts what each construct becomes. |
| `contrib/raml-mock` | Serves its routes in process, and its `examples/server.py` keeps the book resource in memory; its suite checks that the effective API can answer every one of them. |
| `contrib/raml-codegen` | Reads it as *projected JSON* (`tests/api.json`, committed) and generates a Python client and a FastAPI server, each kept as a golden record. |

It lives at the repo root because it belongs to no one of them. Putting it
inside any consumer makes the other four reach into that consumer's directory to
run their own tests.

## 4. `viewer/`

A React SPA over `fastraml tree` output. Outside the Python gate, outside
`fastraml/`, and detailed in `viewer/README.md`.

Its `src/tree.d.ts` is generated by `python -m fastraml.views.bindings
typescript -o viewer/src/tree.d.ts`; a stale copy fails
`tests/unit/test_bindings.py`, as does a stale `public/api.json`.
`contrib/raml-codegen/raml_codegen/tree.py` is the same arrangement in the other
language, and fails the same test.

## 5. `contrib/`

Six separate `uv` projects, each with its own lock, its own gate, and
`fastraml` as an editable path dependency. They are not packaged from this
project's `pyproject.toml` and nothing in the root gate sees them.

| Distribution | Direction | What it is |
|--------------|-----------|------------|
| `raml-document` | — | A typed authoring model for a RAML document — `TypeDecl`, `Body`, `Response`, `Method`, `Resource`, `SecurityScheme`, `Document` — and a reader that builds one from pydantic models. Depends on no web framework. |
| `fastapi-raml` | code → RAML | Renders a FastAPI app's routes as RAML, and serves it. |
| `fastmcp-raml` | RAML → MCP | Serves a RAML-described API as an MCP server through FastMCP. |
| `raml-mock` | RAML → HTTP | Runs an in-process aiohttp mock, validates common HTTP representations, and returns examples or generated values. |
| `fastraml-viewer` | — | The built `viewer/` bundle as static assets, a function that says where they are, and `serve(document)`, which runs them over stdlib HTTP for `fastraml serve` (§ 2). Depends on nothing, including `fastraml`. |
| `raml-codegen` | tree → code | Generates source from a `fastraml tree` document. Two targets: `python`, a typed `httpx` client, and `fastapi`, a server interface to implement (§ 5.3). **Depends on no parser** (§ 5.2). |

`fastapi-raml` and `fastmcp-raml` both need an authoring model, and one
duplicated across two integrations is one that disagrees with itself — so
`raml-document` holds the single copy and depends on neither of them.

It is deliberately *not* `fastraml`'s model. The parse model describes a document
that has been read, and after `unwrap` it refers to types by address. An author
writing a document refers to types by name and has run no pass.

### 5.1 What may not go here

A rule the RAML language states. If two integrations both need it, that is
evidence it belongs in a pass or a view, not in `raml-document`.

### 5.2 `raml-codegen` depends on no parser

Every other `contrib` project imports `fastraml`. `raml-codegen` does not, and
its `pyproject.toml` does not name it: the input is a `fastraml tree` document,
read through the generated binding in docs/16 § 11.11a. That is `viewer/`'s
arrangement in Python, and the edge in § 2 is the same one.

```bash
fastraml tree api.raml > api.json
raml-codegen python api.json -o out/
```

The consequence needs saying, because it costs what § 6 is for: a project that
does not import `fastraml` cannot notice the model moving under it. So the check
sits on the side that owns the projection. Its suite reads committed trees —
`tests/api.json` and `tests/inline.json` — and `tests/unit/test_bindings.py`
regenerates them and fails while either is stale, exactly as it does for
`viewer/public/api.json`.

It is worth having because it **generates** rather than renders. The viewer was
the only consumer of the tree until now, and a renderer never has to decide what
a union, a recursion marker or a `json` shape becomes. A generator has no such
option, so it reaches parts of the projection the viewer cannot.

### 5.3 Both directions, over one fixture

`fastapi-raml` goes code → RAML. `raml-codegen fastapi` goes the other way, so
`contrib/` now covers the two workflows over the same document: **code-first**,
where the app is the source and the RAML falls out of it, and **design-first**,
where the document is the source and the server interface falls out of that.

The `fastapi` target is also where the § 2.1 line is drawn most finely, because
it is the first consumer that *enforces* a facet rather than reporting one. That
is not a rule the package holds: RAML says what `minLength:` constrains and
pydantic says the same thing in its own words, so transcribing one into the
other is a spelling. What would be a rule is deciding something the document
does not — and the four places it does are named in its README as conventions,
not as readings.

One facet does not transcribe, and it is the kind of finding § 1 says consumers
exist to produce. pydantic compares `multiple_of` in binary floating point, so
with `multipleOf: 1.1` it accepts `3.3000000000000003`, which the exact decimal
arithmetic of docs/10 rejects. Spelling the field `Decimal` to close the gap
would let a facet decide the *type*. It stays documented.

## 6. The gate

Each consumer carries its own, and CI runs all of them.

Run this in each of `contrib/raml-document`, `contrib/fastapi-raml`,
`contrib/fastmcp-raml`, `contrib/fastraml-viewer`, `contrib/raml-mock` and
`contrib/raml-codegen`:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy <package>/ && uv run pytest -q
```

Run `npm run check` in `viewer/`. It is `tsc`, `layers`, `smoke` and `shots`.

CI runs `npm run ci` there instead, which replaces the screenshots with the
production build. The screenshots are for looking at, `shots/` is gitignored,
and capturing them on a runner nobody watches costs a browser download and
proves nothing `smoke` has not already proved. Everything that can fail
meaningfully is in both.

The `contrib` job is a matrix over the six, and it is the job that notices
when a change to the model breaks a *consumer* of it rather than a test of it —
which is the whole reason these are in the repository. `fastraml-viewer` gets a
Node step first: its build hook vendors `viewer/dist`, so without a bundle
`uv sync` fails at the install rather than later.

The root's `serve` tests need the bundle the same way. The root carries
`fastraml-viewer` as the `viewer` dependency group rather than in `dev`: a
fresh checkout has no `viewer/dist` until `npm run build` has run, and a plain
`uv sync` must not fail for want of Node. CI installs the group after its Node
step; locally it is `uv sync --group viewer`, and the gate for `serve` is
`uv run --group viewer pytest -q`.

## 7. Publishing

Six distributions, versioned independently, each published by a tag whose form
names it — `v0.1.0` for the parser, `fastapi-raml-v0.1.0` for one consumer.
`.github/workflows/publish.yml` re-runs that project's gate, checks the tag
against the version it is about to publish, and uploads over Trusted Publishing,
so no token is stored. A tag can point anywhere, which is why a green CI run on
the commit is not accepted in place of the gate.

**The root README's logo is a repo-relative path, and PyPI cannot resolve it.**
GitHub renders a relative image against the repository, which is the only form
that works while the repository is private —
`raw.githubusercontent.com` serves nothing from a private repo, to anyone. PyPI
has no base to resolve against, so the project page will show a broken image
until this is dealt with at the first release: either the repository is public
by then and the path goes back to an absolute one, or the build substitutes it.
A data URI is not an option; both sanitisers drop `src` when it is one. Every
other link in that file is already absolute, because only the image needs a
repository to resolve against.

### 7.1 The dependency on `fastraml` is bounded

`[tool.uv.sources]` makes it an editable path for development and **does not
reach the wheel** — a built `fastapi-raml` declares `Requires-Dist: fastraml`
and resolves it from PyPI. So the bound in `[project]` is the only thing
standing between a published consumer and a parser that has moved under it, and
before 1.0 a *minor* bump may break: the pin is `>=0.1,<0.2`, not `<1`.

### 7.2 Why `fastraml-viewer` is its own distribution

`fastapi-raml` used to tell a user with no viewer to "build `viewer/`", which is
a directory a pip install does not have. The bundle is 517 KiB, so shipping it
is cheap; the question was only where.

Not inside `fastapi-raml`, because then anything else wanting the viewer —
`raml-mock`, a plain file server — has to take FastAPI to get it. So it is a
distribution that depends on nothing at all, and `fastapi-raml[viewer]` mounts
it when present and says so when absent. `fastraml serve` takes the same route:
its extra is `fastraml[serve]`, and in a checkout the editable source in the
root `pyproject.toml` keeps the wheel and the working tree from disagreeing.

`viewer/dist` is generated and gitignored, so `hatch_build.py` copies it in at
build time and refuses to build without it. Building from an *sdist* finds no
`viewer/` and skips the copy, because the assets were vendored when the sdist
itself was built.

### 7.3 The sdist is an allow-list

`[tool.hatch.build.targets.sdist]` names what ships. The default — everything
the root `.gitignore` does not exclude — is wrong here, and quietly: hatchling
does not read *nested* `.gitignore` files, so `viewer/node_modules` was
invisible to git and visible to the sdist. That is 4913 files and 41 MB, against
717 KiB with the list.
