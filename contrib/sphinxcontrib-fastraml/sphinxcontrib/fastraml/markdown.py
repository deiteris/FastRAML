"""RAML descriptions, which are Markdown, as the nodes a Sphinx page is made of.

Rendered by MyST, so a description is searched, themed and translated like any
paragraph the author wrote, and works in every builder, PDF included -- which
raw HTML would not.

GitHub-flavoured Markdown only (`gfm_only`), and deliberately not MyST's own
syntax. A description is read by every consumer of the RAML file -- the viewer,
`fastraml openapi`, code generators -- and a Sphinx role written in one would be
literal text everywhere but here. So links go from the prose to the API, never
from the API to the prose, and every link in a description is external.

A heading inside a description becomes a rubric, since a description sits
inside an entry and an entry cannot hold a section. Documentation items are
the exception: they are sections, and their headings nest as subsections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from docutils import nodes
from myst_parser.config.main import MdParserConfig
from myst_parser.mdit_to_docutils.sphinx_ import SphinxRenderer
from myst_parser.parsers.mdit import create_md_parser

if TYPE_CHECKING:
    from docutils.nodes import Element, Node

_CONFIG = MdParserConfig(gfm_only=True)


def markdown(text: str | None, document: nodes.document) -> list[Node]:
    """`text` as block nodes, ready to append to an entry's content."""
    holder = nodes.container()
    _render(text, document, holder, sections=False)
    rendered = list(holder.children)
    holder.children = []
    return rendered


def markdown_sections(text: str | None, document: nodes.document, section: nodes.section) -> None:
    """`text` appended to `section`, its headings opening subsections of it."""
    _render(text, document, section, sections=True)


def _render(text: str | None, document: nodes.document, holder: Element, *, sections: bool) -> None:
    if not text or not text.strip():
        return
    renderer = cast('SphinxRenderer', create_md_parser(_CONFIG, SphinxRenderer).renderer)
    renderer.setup_render({'document': document, 'current_node': holder, 'myst_config': _CONFIG}, {})
    renderer.nested_render_text(text, 0, temp_root_node=holder if sections else None)
