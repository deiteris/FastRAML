<p align="center">
  <a href="https://github.com/deiteris/FastRAML"><img src="https://raw.githubusercontent.com/deiteris/FastRAML/master/assets/logo.png" alt="fastRAML" width="300"></a>
</p>
<p align="center">
    <em>A complete RAML 1.0 parser for Python — every construct decoded, linear in input size</em>
</p>
<p align="center">
<a href="https://github.com/deiteris/FastRAML/actions/workflows/ci.yml?query=branch%3Amaster">
    <img src="https://github.com/deiteris/FastRAML/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI">
</a>
<a href="https://pypi.org/project/fastraml">
    <img src="https://img.shields.io/pypi/v/fastraml?color=%230A84FF&label=pypi%20package" alt="Package version">
</a>
<a href="https://pypi.org/project/fastraml">
    <img src="https://img.shields.io/pypi/pyversions/fastraml.svg?color=%230A84FF" alt="Supported Python versions">
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

**Documentation**: [https://github.com/deiteris/FastRAML/blob/master/docs/README.md](https://github.com/deiteris/FastRAML/blob/master/docs/README.md)

**Source Code**: [https://github.com/deiteris/FastRAML](https://github.com/deiteris/FastRAML)

---

fastRAML reads [RAML 1.0](https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md)
— the API description language — and gives you the **effective** API rather than
the text of one file. Types inherit, traits add parameters, resource types add
methods, and `!include` pulls in other documents; fastRAML resolves all of it and
hands you a typed model, a command line, and a graph you can query.

The key features are:

* **Complete**: every pass runs and every RAML construct is decoded. The RAML compliance kit stands at **915 of 915** — every fixture outside the skip list does what its name promises.
* **Fast**: 7000 types across 150 libraries parse, unwrap and validate in **429 ms** using 98 MB, against the reference implementation's published ~280 ms in Go for the same work.
* **Linear**: time grows with input size, and that property is asserted in CI. Absolute speed is a property of your machine; linearity is a property of the design.
* **Effective, not literal**: `show` prints a type or endpoint with inheritance, traits, resource types and security already merged in — each line tagged with the file and line it was really written on.
* **Positioned diagnostics**: every error carries a file, line and column, and a trace chain through the includes that reached it. Errors accumulate rather than stopping at the first.
* **Typed**: ships `py.typed` and is checked under strict mypy, so your editor and type checker see the real model instead of `Any`.
* **Queryable**: the model projects to a graph with a small RAML vocabulary, with 17 named analysis queries built in — unused types, unsecured operations, undocumented endpoints.
* **Compatibility-aware**: `diff` grades what changed between two versions by whether it breaks a caller, and exits non-zero on a breaking change, so it gates CI without parsing output.
* **Agent-ready**: `fastraml skills install` drops an [Agent Skill](https://github.com/agentskills/agentskills) into `.agents/skills/`, so Claude Code, GitHub Copilot and other agents drive the CLI correctly.

## Status

**fastRAML is in beta.** The parser is feature-complete against the compliance
kit, and the design is settled in [`docs/`](https://github.com/deiteris/FastRAML/blob/master/docs/README.md).
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
[Issues](https://github.com/deiteris/FastRAML/issues) — a fixture that fails is
a bug, not a limitation.

## Requirements

Python 3.12 or newer. Tested on Linux and Windows against 3.12 and 3.13.

## Installation

```bash
pip install fastraml
```

Or, while it is unreleased:

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

## Command line

```bash
fastraml validate api.raml           # exit 1 and a positioned trace if invalid
fastraml validate --json *.raml      # one JSON object per file
fastraml info api.raml               # YAML backend, timing, model counts
```

And, because the tedious part of RAML is following resolved links by hand, a
view of the **effective** model as a graph
([docs/16](https://github.com/deiteris/FastRAML/blob/master/docs/16-graph.md)):

```bash
fastraml list api.raml               # what is in here: every name you can ask about
fastraml refs api.raml User          # every operation that can carry a User, with the route
fastraml deps api.raml User          # everything User is built from
fastraml graph api.raml              # the whole projection as Turtle (or nt, dot, json)
fastraml show api.raml /users        # the effective view: everything merged in, with origins
fastraml diff v1.raml v2.raml        # what changed, and what it breaks (exit 1 if breaking)
fastraml query --list                # 17 named analysis queries
fastraml query api.raml -n type-fan-in   # or -q '<sparql>' for your own
```

```
Operation  get -returns-> 200 -payload-> application/json -range-> ... -items-> User
```

Each result is a **route**, not just a hit — which is the one thing a SPARQL
property path cannot give you, and the reason these are not a canned query.

## Using it from an agent

`fastraml skills` serves a usage guide from inside the installed package, so the
instructions an agent reads always match the version that answers them:

```bash
fastraml skills install              # into ./.agents/skills/, read by most agents
fastraml skills get core             # or just print the guide
```

## Why it is fast

7000 types across 150 libraries parse, unwrap and validate in **429 ms** using
**98 MB**, against the reference implementation's published ~280 ms in Go for the
same work — and strictly linear in input size, which is the property gated in CI
rather than the wall clock.

That comes from structural decisions, not from Python: a two-stage endpoint build
that merges traits and resource types on YAML trees before any type resolution,
one compose and one decode per file, node identity as a dict key instead of deep
copies, and a worklist instead of a traversal to find work.
[docs/12-performance.md](https://github.com/deiteris/FastRAML/blob/master/docs/12-performance.md)
records each technique, whether it transfers from Go, and the places where Go
advice has to be inverted for CPython.

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

The design follows [go-raml](https://github.com/acronis/go-raml). Where the two
disagree, [docs/01-scope-and-coverage.md](https://github.com/deiteris/FastRAML/blob/master/docs/01-scope-and-coverage.md)
§ 4 records the deviation and its reason.

## Optional dependencies

| Extra | For |
|-------|-----|
| `fastraml[graph]` (`pyoxigraph`) | `fastraml query` — SPARQL over the graph projection; the graph itself needs nothing |
| `fastraml[http]` (`httpx`) or `requests` | remote `!include`; supply the client yourself, or use `fastraml validate -r`. Synchronous clients only — from async code run the parse in `asyncio.to_thread` ([why](https://github.com/deiteris/FastRAML/blob/master/docs/03-yaml-and-io.md#51-the-http-client-is-synchronous-and-refused-if-it-is-not)) |
| `fastraml[re2]` (`google-re2`) | `ParseOptions(regex_engine="re2")` — linear-time patterns for untrusted input |
| libyaml | selected automatically when PyYAML was built with it; roughly an order of magnitude faster, and **not only** a speed choice ([D9](https://github.com/deiteris/FastRAML/blob/master/docs/01-scope-and-coverage.md)) |

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
