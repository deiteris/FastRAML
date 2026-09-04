# pyRAML

A [RAML 1.0](https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md)
parser for Python 3.12+.

> **Status: complete and unreleased.** Every pass runs, every RAML construct is
> decoded, and the compliance kit stands at **916 of 916**. Overlays and
> Extensions are the one language feature deferred, and XML Schema external
> types are out of scope. **The API is not stable before 1.0.**

```python
from pyraml import ParseOptions, ObjectShape, parse_from_path

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
pyraml validate api.raml           # exit 1 and a positioned trace if invalid
pyraml validate --json *.raml      # one JSON object per file
pyraml info api.raml               # YAML backend, timing, model counts
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
[docs/12-performance.md](docs/12-performance.md) records each technique, whether
it transfers from Go, and the places where Go advice has to be inverted for
CPython.

## Design documents

`docs/` is normative and settled before the code; each document owns one area and
states the decisions that area has already made. Start at
[docs/README.md](docs/README.md).

The design follows [go-raml](https://github.com/acronis/go-raml). Where the two
disagree, [docs/01-scope-and-coverage.md](docs/01-scope-and-coverage.md) § 4
records the deviation and its reason.

## Development

```bash
uv sync                                  # create the environment
uv run pytest -q                         # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy pyraml/
```

All four must pass before any change lands.

### RAML Test Compliance Kit

The TCK fixtures are not vendored. Point the suite at a checkout:

```bash
PYRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck
```

Without that variable the TCK tests skip. `tests/tck/ratchet.json` records the
expected outcome per fixture, and CI fails on drift in either direction — a
regression, or progress that was not recorded. See
[docs/14-testing.md](docs/14-testing.md).

### Benchmarks

```bash
python -m bench run                      # every bench, every configuration
python -m bench linearity                # the one hard requirement
PYRAML_BENCH=1 uv run pytest tests/bench # the same gate, under pytest
```

## Optional dependencies

| Extra | For |
|-------|-----|
| `google-re2` | `ParseOptions(regex_engine="re2")` — linear-time patterns for untrusted input |
| `httpx` or `requests` | remote `!include`; supply the client yourself, or use `pyraml validate -r` |
| libyaml | selected automatically when PyYAML was built with it; roughly an order of magnitude faster, and **not only** a speed choice ([D9](docs/01-scope-and-coverage.md)) |

## Licence

To be decided before the first release.
