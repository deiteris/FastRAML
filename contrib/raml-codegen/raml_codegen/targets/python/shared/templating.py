"""The Jinja environment every Python target renders its templates with."""

from __future__ import annotations

from typing import TYPE_CHECKING

import jinja2

from . import docs

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ['template_environment']


def template_environment(templates: Path) -> jinja2.Environment:
    """An environment over `templates`, with the docstring filters every target uses.

    A target adds the filters only it needs; `attribute` differs by target,
    because a model field and a client argument are documented differently.
    """
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(templates),
        autoescape=False,  # noqa: S701 - the output is Python, not HTML
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    environment.filters['repr'] = repr
    environment.filters['one_line'] = docs.one_line
    environment.filters['summary'] = docs.summary
    environment.filters['details'] = docs.details
    return environment
