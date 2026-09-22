<p align="center">
  <a href="https://github.com/deiteris/FastRAML"><img src="assets/logo.png" alt="fastRAML" width="300"></a>
</p>
<p align="center">
    <em>Parse, validate, inspect, and convert RAML 1.0 in Python</em>
</p>
<p align="center">
<a href="https://github.com/deiteris/FastRAML/actions/workflows/ci.yml?query=branch%3Amaster">
    <img src="https://github.com/deiteris/FastRAML/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI">
</a>
<a href="https://github.com/deiteris/FastRAML/blob/master/docs/14-testing.md">
    <img src="https://img.shields.io/badge/RAML%20TCK-915%2F915-brightgreen" alt="RAML TCK">
</a>
<a href="https://github.com/deiteris/FastRAML/blob/master/LICENSE">
    <img src="https://img.shields.io/badge/licence-MIT-blue" alt="Licence">
</a>
<a href="#status">
    <img src="https://img.shields.io/badge/status-beta-orange" alt="Status: beta">
</a>
</p>

---

**Documentation**: [docs/README.md](https://github.com/deiteris/FastRAML/blob/master/docs/README.md)

**Source Code**: [deiteris/FastRAML](https://github.com/deiteris/FastRAML)

---

fastRAML reads [RAML 1.0](https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md)
— the API description language — and gives you the **effective** API, everything
resolved and merged, rather than the text of one file. Types inherit, traits add
parameters, resource types add methods, and `!include` pulls in other documents;
fastRAML resolves all of it and hands you a typed model plus tools for
validation, navigation, linting, and conversion.

The key features are:

* **Effective model**: resolves `!include`, `uses`, type expressions and inheritance, then applies traits, resource types and security schemes. Declaration order and source locations remain available on the typed Python model.
* **Type and value validation**: implements RAML's built-in shapes and facets, custom facets, examples, defaults, annotations, recursive types, and JSON Schema external types. A shape can also validate an application value directly.
* **Tested coverage with explicit boundaries**: all **915 evaluated fixtures in the RAML Test Compliance Kit (TCK)** produce their expected outcome. Overlay and Extension merging is deferred, and XML Schema external types are not supported; the [coverage matrix](https://github.com/deiteris/FastRAML/blob/master/docs/01-scope-and-coverage.md) records the details.
* **Structured diagnostics**: errors carry source locations and trace chains, including failures reached through includes and merged templates. Independent failures accumulate rather than stop the parse, wherever the parser can continue safely.
* **Model navigation**: `list`, `show`, `refs` and `deps` inspect named entities and the routes between them. `graph` emits RDF, Graphviz or JSON, while `tree` emits an addressed containment view.
* **Analysis and linting**: run custom SPARQL or one of 9 named graph queries. `lint` checks the effective model against 68 built-in rules, with opt-in security (OWASP and OAuth), HTTP semantics (RFC 9110), problem details (RFC 9457) and style rulesets, per-rule explanations and plugins ([Linting](#linting)).
* **Version comparison**: `compat` walks two effective API models in parallel and classifies compatibility impact by whether a value is sent in a request or received in a response ([docs/16](https://github.com/deiteris/FastRAML/blob/master/docs/16-graph.md#103-the-policy-is-separable-and-named)). It exits non-zero when the policy identifies a breaking change.
* **OpenAPI and JSON Schema output**: convert an effective API to a typed OpenAPI 3.0.3 document, or a RAML shape to JSON Schema draft-07. Both conversion APIs report information the target format could not represent.
* **Typed and measured**: ships `py.typed` and checks the package with strict mypy. The benchmark gate checks linear scaling; on the recorded machine, 7000 types across 150 libraries parse, unwrap and validate in **429 ms** using **98 MB**. The method, the per-configuration numbers, and the comparison against [go-raml](https://github.com/acronis/go-raml) — measured rather than quoted — are in [docs/12](https://github.com/deiteris/FastRAML/blob/master/docs/12-performance.md).
* **Version-matched agent guides**: the CLI ships its own usage guides for coding agents, so the guide always matches the installed version ([Using it from an agent](#using-it-from-an-agent)).

## Status

**fastRAML is in beta.** The parser matches the expected outcome of every
TCK fixture in its evaluated scope, and the design is settled in
[`docs/`](https://github.com/deiteris/FastRAML/blob/master/docs/README.md).
What is *not* settled is the surface you code against:

* **The public API may change before 1.0**, including names, signatures and
  model attributes. There is no release yet, so pin a commit:
  `git+https://github.com/deiteris/FastRAML@<commit>`.
* **Emitted identifiers are provisional.** The `fastraml://id` address base and
  the `urn:fastraml:ns:raml#` RDF namespace are not frozen; read
  `fastraml.RAML_NS` rather than hard-coding it.
* **Features may be added or withdrawn.** The
  [coverage matrix](https://github.com/deiteris/FastRAML/blob/master/docs/01-scope-and-coverage.md)
  records what is deferred and what is out of scope.

Report anything that looks wrong at
[Issues](https://github.com/deiteris/FastRAML/issues). A failure within the
documented supported surface is a bug.

## Requirements

Python 3.12 or newer. Tested on Linux and Windows against 3.12 and 3.13.

## Installation

fastraml is not yet on PyPI. Until it is:

```bash
uv tool install git+https://github.com/deiteris/FastRAML
```

## Python API

```python
from fastraml import ParseOptions, ObjectShape, parse_from_path

raml = parse_from_path('api.raml', ParseOptions(unwrap=True, validate=True))
api = raml.entry_point

user = api.types['User']
user.validate({'name': 'Bob', 'age': 35})  # None, or a RamlError

shape = user.shape
if isinstance(shape, ObjectShape):
    for name, prop in shape.properties.items():
        print(name, prop.base.type, prop.required)
```

Pass `unwrap=True, validate=True` together unless you specifically want to
inspect un-flattened declarations — `validate=True` alone unwraps a private copy
of every type it checks, and the benchmarks measure it slower for that.

`parse_lenient(path)` returns `(model, error)` rather than raising, for an editor
that needs a partial model on every keystroke. It still raises when there is no
model to return: the entry file cannot be read, its RAML header is missing or
unrecognised, it is an Overlay or Extension, or its root is not a mapping.

OpenAPI export stays typed until the serialization boundary:

```python
from fastraml import to_openapi

openapi, dropped = to_openapi(raml)
print(openapi.info.title)
get_users = openapi.paths['/users'].get
if get_users is not None:
    print(get_users.responses['200'].description)
payload = openapi.to_dict()  # JSON/YAML-ready only when you need it
```

### Files outside the entry file's directory

The parser reads files only inside a *workspace root*, which defaults to the
directory of the file you parse. An `!include`, `uses:` or JSON Schema `$ref`
that leaves that directory fails with `path is outside the workspace root`, and
the error suggests a root that would contain it. Set the root to a directory
that contains every file the document reaches:

```python
raml = parse_from_path('api/api.raml', ParseOptions(workspace_root='.'))
```

On the command line, pass `-w DIR` (`--workspace-root`). `--no-workspace-guard`
turns the check off; use it only for documents you trust.

## Command line

```bash
fastraml validate api.raml           # exit 1 and a positioned trace if invalid
fastraml validate --json *.raml      # one JSON object per file
fastraml info api.raml               # YAML backend, timing, model counts
fastraml openapi api.raml            # OpenAPI 3.0.3 YAML; --format json for JSON
fastraml lint api.raml               # spec, security and style checks (see Linting)
```

### Inspecting the model

These commands work on the resolved model, through its containment and graph
views
([docs/16](https://github.com/deiteris/FastRAML/blob/master/docs/16-graph.md)):

```bash
fastraml list api.raml               # every name you can ask about
fastraml refs api.raml User          # everything that uses User, with the route to it
fastraml deps api.raml User          # everything User is built from, with the route
fastraml show api.raml /users        # the effective view: everything merged in, with origins
fastraml graph api.raml              # the whole projection as Turtle (or nt, dot, json)
fastraml tree api.raml               # addressed JSON retaining containment and leaf data
fastraml query --list                # 9 named analysis queries
fastraml query api.raml -n type-fan-in   # or -q '<sparql>' for your own
```

`refs` and `deps` print a **route** for each result, not only the entity it
reached, for example:

```
Operation  api.raml:446  Add a book -request-> request -payload-> application/json -range-> ... -inherits-> Book
```

`show` accepts a type, a resource by its path or `displayName`, or any name that
`list` prints. `query` needs the `graph` extra (see
[Optional dependencies](#optional-dependencies)).

### Linting

`fastraml lint` checks whether a valid document is a *good* one. It runs on the
effective model, after traits, resource types and inheritance are applied, so it
sees what a client of the API sees. The 68 built-in rules fall into five
rulesets, and you choose which ones run:

* **`spec`** (8 rules, the default `recommended` ruleset): problems the RAML and
  JSON Schema specifications themselves imply, such as a `$ref` whose sibling
  keywords are ignored, deprecated `schemas:`, or a body whose media type cannot
  carry its declared type.
* **`security`** (27 rules, opt-in): derived from the
  [OWASP API Security Top 10 (2023)](https://api-security.owasp.org/editions/2023/en/0x11-t10)
  and the OAuth RFCs (6749, 6750 and 9700), for the categories a document can
  express. They cover authentication (unsecured operations, HTTP Basic, OAuth
  1.0, the OAuth 2.0 password and implicit grants, credentials in the query
  string, HTTPS-only transport and OAuth endpoints), unrestricted resource
  consumption (unbounded strings, numbers, arrays, files and objects, rate-limit
  headers and `429` responses), input validation (unanchored and
  backtracking-prone patterns, file types, wildcard request media types),
  guessable numeric resource IDs, objects that accept undeclared properties, and
  typed `400`/`422`, `401` and `500` error responses. Authorization logic,
  server-side request forgery and business-flow abuse depend on runtime
  behaviour, which no rule over a document can check.
* **`http`** (14 rules, opt-in): what
  [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) requires and RAML does
  not check: no body on 1xx, 204, 304 or HEAD responses; the
  `WWW-Authenticate`, `Allow`, `Proxy-Authenticate`, `Location` and
  `Content-Range` headers their status codes call for; header names that are
  HTTP tokens, declared once regardless of case, and not connection-specific;
  date headers typed as HTTP dates; a `Content-Type` header that agrees
  with the body media types; retired status codes; a 304 that repeats its
  200's validators; and responses the declared request can never produce.
* **`problem-details`** (3 rules, opt-in): for APIs that adopt
  [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457), which replaces RFC 7807:
  error bodies use `application/problem+json` or `+xml`, the standard members
  have their standard types, and `status` agrees with the response code.
* **`style`** (16 rules, opt-in): authoring conventions such as descriptions,
  examples, display names, concise type spellings and closed objects.

`all` enables every built-in rule plus every enabled plugin. `fastraml lint
--explain RULE` lists the OWASP category, RFC clause or CWE a rule follows from.

```bash
fastraml lint api.raml                        # the recommended rules
fastraml lint --list-rules                    # every rule, with its category, severity and source
fastraml lint --explain unused-type           # what a rule checks, with good and bad examples
fastraml lint api.raml --rule https-only=error    # enable or regrade one rule for this run
fastraml lint api.raml --format text          # one line per finding; also json and summary
```

`lint` exits 1 when any finding has `error` severity, or when a document fails
to parse. Built-in rules report at `warning` or `info`, so a run fails only on
rules you have raised to `error`, or on warnings too with `--fail-on warning`.
Every file is linted before the command exits. Output is capped at 1,000
findings, and 100 per rule in each file, keeping errors and warnings ahead of
info; the exit status still counts every finding, and `--max-findings 0`
removes the cap.

Keep lasting policy in the `lint:` section of the [configuration](#configuration)
file:

```yaml
lint:
  extends: [recommended, security]  # also: style, all
  categories:
    security: { severity: error }
  rules:
    - id: unused-type
      match: '.*internal.*'         # drop only the findings whose message matches
      disabled: true
```

To silence one finding, put a comment on the line directly above the line it
points to:

```yaml
# fastraml: ignore missing-description,missing-example
User: string
```

For your organisation's own policy, write a plugin: a package that exports a
sequence of rules under the `fastraml.lint_rules` entry point group.

```toml
[project.entry-points."fastraml.lint_rules"]
house-style = "myorg.ramlrules:RULES"
```

Installing a plugin changes nothing on its own: its rules run only once
`plugins: [house-style]` names it in the configuration. `--list-rules` shows
which package provides each rule. No plugins are published yet. The rule model,
every built-in policy and the output formats are in
[docs/18](https://github.com/deiteris/FastRAML/blob/master/docs/18-linting.md).

### Comparing versions

```bash
fastraml compat v1.raml v2.raml      # exit 1 if any change is breaking
```

The Python API renders the same comparison as Markdown, for a pull request or a
build summary. `examples/backward_report.py` is complete and runnable, and its
two RAML files exercise every model-native compatibility rule:

```python
from fastraml import ParseOptions, backward_markdown, parse_from_path

options = ParseOptions(unwrap=True)
old = parse_from_path('v1.raml', options)
new = parse_from_path('v2.raml', options)
print(backward_markdown(old, new))
```

### Configuration

Every command that parses a document takes `--config FILE`: one YAML file with
`parser:`, `lint:` and `compatibility:` sections. For example, a deployment
behind an HTTP redirect can regrade only the transition from HTTP+HTTPS to
HTTPS:

```yaml
compatibility:
  rules:
    - id: protocol-removed
      impact: compatible
      match:
        before: [HTTP, HTTPS]
        after: [HTTPS]
```

## Using it from an agent

`fastraml skills` serves a usage guide from inside the installed package, so the
instructions an agent reads always match the version that answers them:

```bash
fastraml skills install              # into ./.agents/skills/, read by most agents
fastraml skills get core             # or just print the guide
```

## Related packages

Separate distributions, versioned independently. fastRAML depends on none of
them, so the parser takes no web framework. `raml-codegen` reads `fastraml tree`
output and depends on no parser, and `fastraml-viewer` depends on nothing.

| Package | Direction | What it is |
|---------|-----------|------------|
| [`raml-document`](https://github.com/deiteris/FastRAML/tree/master/contrib/raml-document) | — | A typed authoring model, and a reader that builds one from pydantic models |
| [`fastapi-raml`](https://github.com/deiteris/FastRAML/tree/master/contrib/fastapi-raml) | code → RAML | Renders a FastAPI app's routes as RAML, and serves them |
| [`aiohttp-raml`](https://github.com/deiteris/FastRAML/tree/master/contrib/aiohttp-raml) | code → RAML | Code-first RAML for aiohttp: pydantic-validated views that describe themselves |
| [`fastmcp-raml`](https://github.com/deiteris/FastRAML/tree/master/contrib/fastmcp-raml) | RAML → MCP | Serves a RAML-described API as an MCP server |
| [`raml-mock`](https://github.com/deiteris/FastRAML/tree/master/contrib/raml-mock) | RAML → HTTP | Runs an in-process mock that validates requests and returns examples |
| [`raml-codegen`](https://github.com/deiteris/FastRAML/tree/master/contrib/raml-codegen) | tree → code | Generates a typed `httpx` client, or a FastAPI server interface to implement, from `fastraml tree` output |
| [`fastraml-viewer`](https://github.com/deiteris/FastRAML/tree/master/contrib/fastraml-viewer) | — | The tree viewer as static assets any server can mount |

## Design documents

`docs/` is normative and settled before the code; each document owns one area and
states the decisions that area has already made. Start at
[docs/README.md](https://github.com/deiteris/FastRAML/blob/master/docs/README.md).

Where fastRAML reads the spec differently from
[go-raml](https://github.com/acronis/go-raml),
[docs/01-scope-and-coverage.md](https://github.com/deiteris/FastRAML/blob/master/docs/01-scope-and-coverage.md)
§ 4 records the difference and its reason.

## Optional dependencies

| Extra | For |
|-------|-----|
| `fastraml[graph]` (`pyoxigraph`) | `fastraml query` — SPARQL over the graph projection; the graph itself needs nothing |
| `fastraml[serve]` (`fastraml-viewer`) | `fastraml serve` — the document in a browser; the built viewer bundle, a static package with no dependencies of its own ([why it is its own distribution](https://github.com/deiteris/FastRAML/blob/master/docs/17-consumers.md#72-why-fastraml-viewer-is-its-own-distribution)) |
| `fastraml[http]` (`httpx`) or `requests` | remote `!include`; supply the client yourself, or use `fastraml validate -r`. Synchronous clients only — from async code run the parse in `asyncio.to_thread` ([why](https://github.com/deiteris/FastRAML/blob/master/docs/03-yaml-and-io.md#51-the-http-client-is-synchronous-and-refused-if-it-is-not)) |
| `fastraml[re2]` (`google-re2`) | `ParseOptions(regex_engine="re2")` — linear-time patterns for untrusted input |
| libyaml | selected automatically when PyYAML was built with it; roughly an order of magnitude faster, and **not only** a speed choice ([D9](https://github.com/deiteris/FastRAML/blob/master/docs/01-scope-and-coverage.md#d9--a-tab-after-a-keys-colon-depends-on-the-yaml-backend)) |

## Development

```bash
uv sync                                  # create the environment
uv run pytest -q                         # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy fastraml/
```

All four must pass before any change lands.

`uv sync` leaves out the viewer bundle, so `fastraml serve` does not work in a
fresh checkout. Run `npm run build` in `viewer/`, then `uv sync --group viewer`.

### RAML Test Compliance Kit

The fixtures are a submodule, from
[deiteris/raml-tck](https://github.com/deiteris/raml-tck):

```bash
git submodule update --init    # if you cloned without --recurse-submodules
uv run pytest tests/tck
```

`FASTRAML_TCK_DIR=<path>` runs against a different checkout instead. With
neither, the TCK tests skip. `tests/tck/ratchet.json` records the
expected outcome per fixture, and CI fails on drift in either direction — a
regression, or progress that was not recorded. See
[docs/14-testing.md](https://github.com/deiteris/FastRAML/blob/master/docs/14-testing.md).

### Benchmarks

```bash
python -m bench run                      # every bench, every configuration
python -m bench linearity                # the one hard requirement
python -m bench compare                  # fail on a >25 % regression against the baseline
FASTRAML_BENCH=1 uv run pytest tests/bench # the same gate, under pytest
```

Run `compare` before and after any change to a hot path, and put the delta in
the commit message.

## Licence

MIT. See [LICENSE](https://github.com/deiteris/FastRAML/blob/master/LICENSE).
