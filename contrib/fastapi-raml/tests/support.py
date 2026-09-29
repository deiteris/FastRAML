"""Build an app and read the parsed RAML back, for the rule-by-rule tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastraml import ParseOptions, parse_from_string

from fastapi_raml import build, render


def parsed(app: Any) -> Any:
    """`app` rendered, built and parsed. Raises `BuildError` if the RAML does not parse."""
    served = build(app)
    return parse_from_string(
        served.text,
        file_name='api.raml',
        base_dir=Path(__file__).resolve().parent,
        options=ParseOptions(unwrap=True, validate=True),
    )


def operation(app: Any, path: str, verb: str) -> Any:
    """The parsed operation at `path` -- the full URI, as FastAPI spells it."""
    return parsed(app).endpoints[path].operations[verb]


def dropped(app: Any) -> list[str]:
    return render(app).dropped
