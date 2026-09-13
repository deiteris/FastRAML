# contrib

Distributions that sit on pyRAML. Each is a separate `uv` project with its own
lock and its own gate; none is packaged from the root `pyproject.toml`, and the
root gate does not see them. `docs/17-consumers.md` settles the boundary.

| | Direction | |
|---|---|---|
| [`raml-document`](raml-document/) | — | A typed authoring model for a RAML document, and a reader that builds one from pydantic models. No web framework. |
| [`fastapi-raml`](fastapi-raml/) | code → RAML | Renders a FastAPI app's routes as RAML, and serves it. |
| [`fastmcp-raml`](fastmcp-raml/) | RAML → MCP | Serves a RAML-described API as an MCP server through FastMCP. |

`raml-document` is what the other two have in common. It is deliberately not
pyRAML's own model: the parse model is the shape of a document that has been
read and refers to its types by address once unwrapped, where an author writing
one refers to them by name and has run no pass.

## The rule

These consume `pyraml`; nothing in `pyraml/` may import them. A rule the RAML
language states does not belong here — if two integrations both need it, that is
evidence it belongs in a pass or in `pyraml/views/`. `views/jsonschema.py`
arrived that way.

## Working on one

```bash
cd contrib/<name>
uv sync --all-extras --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy <package>/ && uv run pytest -q
```

`pyraml` resolves to `../..` as an editable install, so a change to the parser is
visible here without a reinstall — which is the point. CI runs all three as a
matrix.
