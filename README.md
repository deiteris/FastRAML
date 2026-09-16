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
* **Analysis and linting**: run custom SPARQL or one of 9 named graph queries. `lint` provides configurable built-in rules, optional security and style rulesets, explanations, and plugin support.
* **Version comparison**: `diff` reports structural changes and classifies their compatibility impact — by whether a value is sent in a request or received in a response — under a backward-compatibility policy ([docs/16](https://github.com/deiteris/FastRAML/blob/master/docs/16-graph.md#103-the-policy-is-separable-and-named)). It exits non-zero when the policy identifies a breaking change.
* **OpenAPI and JSON Schema output**: convert an effective API to a typed OpenAPI 3.0.3 document, or a RAML shape to JSON Schema draft-07. Both conversion APIs report information the target format could not represent.
* **Typed and measured**: ships `py.typed` and checks the package with strict mypy. The benchmark gate checks linear scaling; on the recorded machine, 7000 types across 150 libraries parse, unwrap and validate in **429 ms** using **98 MB**. The method, the per-configuration numbers, and the comparison against [go-raml](https://github.com/acronis/go-raml) — measured rather than quoted — are in [docs/12](https://github.com/deiteris/FastRAML/blob/master/docs/12-performance.md).
* **Version-matched agent guides**: `fastraml skills get` serves CLI guidance from the installed package, and `fastraml skills install` installs a discovery stub under `.agents/skills/` or another selected directory: a small skill whose only job is to point an agent at `fastraml skills get`, so the guide it reads matches the installed version.

## Status

**fastRAML is in beta.** The parser matches the expected outcome of every
TCK fixture in its evaluated scope, and the design is settled in
[`docs/`](https://github.com/deiteris/FastRAML/blob/master/docs/README.md).
What is *not* settled is the surface you code against:

* **The public API may change before 1.0**, including names, signatures and
  model attributes. Pin a version — `fastraml>=0.1,<0.2`.
* **Emitted identifiers are provisional.** The `fastraml://id` address base and
  the `urn:fastraml:ns:raml#` RDF namespace are not frozen; read `RAML_NS` from
  the package rather than hard-coding it.
* **Features may be added or withdrawn.** Overlays and Extensions are the one
  language feature deferred, and XML Schema external types are out of scope.
  JSON Schema external types are supported.

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

## Example

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
that needs a partial model on every keystroke.

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

## Command line

```bash
fastraml validate api.raml           # exit 1 and a positioned trace if invalid
fastraml validate --json *.raml      # one JSON object per file
fastraml info api.raml               # YAML backend, timing, model counts
fastraml openapi api.raml            # OpenAPI 3.0.3 YAML; --format json for JSON
fastraml lint api.raml               # configurable semantic, security and style checks
```

For inspecting the resolved model, the CLI provides both containment and graph
views
([docs/16](https://github.com/deiteris/FastRAML/blob/master/docs/16-graph.md)):

```bash
fastraml list api.raml               # what is in here: every name you can ask about
fastraml refs api.raml User          # every operation that can carry a User, with the route
fastraml deps api.raml User          # everything User is built from
fastraml graph api.raml              # the whole projection as Turtle (or nt, dot, json)
fastraml tree api.raml               # addressed JSON retaining containment and leaf data
fastraml show api.raml /users        # the effective view: everything merged in, with origins
fastraml diff v1.raml v2.raml        # structural changes and compatibility classification
fastraml query --list                # 9 named analysis queries
fastraml query api.raml -n type-fan-in   # or -q '<sparql>' for your own
```

```
Operation  get -returns-> 200 -payload-> application/json -range-> ... -items-> User
```

Each result is a **route**, not just a hit — which is the one thing a SPARQL
property path cannot give you, and the reason these are not canned queries.

## Using it from an agent

`fastraml skills` serves a usage guide from inside the installed package, so the
instructions an agent reads always match the version that answers them:

```bash
fastraml skills install              # into ./.agents/skills/, read by most agents
fastraml skills get core             # or just print the guide
```

## The rest of the family

Separate distributions, versioned independently, each depending on fastRAML
rather than the other way round — the parser takes no web framework.

| Package | Direction | What it is |
|---------|-----------|------------|
| [`raml-document`](https://github.com/deiteris/FastRAML/tree/master/contrib/raml-document) | — | A typed authoring model, and a reader that builds one from pydantic models |
| [`fastapi-raml`](https://github.com/deiteris/FastRAML/tree/master/contrib/fastapi-raml) | code → RAML | Renders a FastAPI app's routes as RAML, and serves them |
| [`fastmcp-raml`](https://github.com/deiteris/FastRAML/tree/master/contrib/fastmcp-raml) | RAML → MCP | Serves a RAML-described API as an MCP server |
| [`raml-mock`](https://github.com/deiteris/FastRAML/tree/master/contrib/raml-mock) | RAML → HTTP | Runs an in-process mock that validates requests and returns examples |
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
FASTRAML_BENCH=1 uv run pytest tests/bench # the same gate, under pytest
```

## Licence

MIT. See [LICENSE](https://github.com/deiteris/FastRAML/blob/master/LICENSE).
