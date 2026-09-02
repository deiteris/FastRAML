# pyRAML

A [RAML 1.0](https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md)
parser for Python 3.12+.

> **Status: in development.** Phase 0 of
> [the implementation plan](docs/15-implementation-plan.md) — foundations only.
> There is no working parser yet.

## Design documents

The architecture is settled before the code. Start at
[docs/README.md](docs/README.md); the reading order is listed there.

The design follows [go-raml](https://github.com/acronis/go-raml), which achieves
compliance and performance through a small number of structural decisions —
chiefly a two-stage endpoint build that merges traits and resource types on YAML
trees before any type resolution. [docs/12-performance.md](docs/12-performance.md)
records each technique and its Python translation.

## Development

```bash
uv sync                                  # create the environment
uv run pytest                            # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy pyraml/
```

### RAML Test Compliance Kit

The TCK fixtures are not vendored yet. Point the test suite at a checkout:

```bash
PYRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck
```

Without that variable the TCK tests are skipped. See
[docs/14-testing.md](docs/14-testing.md).

## Licence

To be decided before the first release.
