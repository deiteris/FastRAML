"""RAML descriptions, which are Markdown, as the nodes a Sphinx page is made of.

Rendered by MyST, so a description is searched, themed and translated like any
paragraph the author wrote, and works in every builder, PDF included -- which
raw HTML would not.

GitHub-flavoured Markdown only (`gfm_only`), and deliberately not MyST's own
syntax. A description is read by every consumer of the RAML file -- the viewer,
`fastraml convert openapi`, code generators -- and a Sphinx role written in one would be
literal text everywhere but here. So links go from the prose to the API, never
from the API to the prose.

The one link a description makes into the API is fastraml's own: `` [`Book`] ``
or `[list them][GET /books]`, a reference link whose label names something the
document declares (fastraml's docs/16 § 11). fastraml resolves the name;
`links` says what each label becomes. The labels are put in markdown-it's
reference table before parsing, so markdown-it decides what is a link, and each
link it makes is then swapped for the cross-reference. Any other renderer of
the same prose shows the label as bracketed text.

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
    from collections.abc import Callable, Mapping

    from docutils.nodes import Element, Node

    #: What one link's text becomes: the cross-reference holding it.
    type Linker = Callable[[list[Node]], Node]

_CONFIG = MdParserConfig(gfm_only=True)

#: The href a resolved link is rendered with until it is swapped for its
#: cross-reference. Not a scheme anything resolves.
_PENDING = 'raml-doc-link:'


def markdown(text: str | None, document: nodes.document, links: Mapping[str, Linker] | None = None) -> list[Node]:
    """`text` as block nodes, ready to append to an entry's content."""
    holder = nodes.container()
    _render(text, document, holder, sections=False, links=links)
    rendered = list(holder.children)
    holder.children = []
    return rendered


def markdown_sections(
    text: str | None, document: nodes.document, section: nodes.section, links: Mapping[str, Linker] | None = None
) -> None:
    """`text` appended to `section`, its headings opening subsections of it."""
    _render(text, document, section, sections=True, links=links)


def _render(
    text: str | None, document: nodes.document, holder: Element, *, sections: bool, links: Mapping[str, Linker] | None
) -> None:
    if not text or not text.strip():
        return
    references = {label: {'href': f'{_PENDING}{label}', 'title': ''} for label in links or {}}
    renderer = cast('SphinxRenderer', create_md_parser(_CONFIG, SphinxRenderer).renderer)
    renderer.setup_render(
        {'document': document, 'current_node': holder, 'myst_config': _CONFIG}, {'references': references}
    )
    renderer.nested_render_text(text, 0, temp_root_node=holder if sections else None)
    if not links:
        return
    for reference in list(holder.findall(nodes.reference)):
        uri = reference.get('refuri', '')
        if uri.startswith(_PENDING):
            reference.replace_self(links[uri.removeprefix(_PENDING)](list(reference.children)))
