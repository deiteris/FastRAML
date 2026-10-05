"""Lint rules over the Markdown a document carries."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.views.doclinks import DocLinks, Outcome, prose_of
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.positions import Position
    from fastraml.views.lint.engine import Context

__all__ = ['BrokenDocLink']

#: The message key for each way a link fails, so a `match:` filter can keep
#: one and drop another (docs/18 § 3).
_MESSAGES: Final = {
    Outcome.UNRESOLVED: 'link names nothing',
    Outcome.AMBIGUOUS: 'link names more than one target',
    Outcome.OUT_OF_SCOPE: 'library prose links into an API',
}


class BrokenDocLink:
    meta: ClassVar = RuleMeta(
        id='broken-doc-link',
        category=Category.STYLE,
        summary='a link in a description names nothing, or more than one thing',
        rationale=(
            'A description links a declaration, resource, method or documentation item by name, as '
            '[`Book`] or [the books][GET /books] (docs/16 § 11). A link that names nothing renders as its '
            'bracketed text; one that names two things links to neither; and one that a library writes to '
            'an API resource names nothing in the next API that uses the library. A bare [label] is not '
            'reported, because prose uses brackets for other things too.'
        ),
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ndescription: Returns a [`Book`].\ntypes:\n  Book: object\n',
        bad='#%RAML 1.0\ntitle: t\ndescription: Returns a [`Book`].\n',
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        """Each explicit link that does not resolve, once per place it was written.

        Once per text, position and label rather than per entity: an inherited
        or contributed description reaches many entities from one place.
        """
        links = DocLinks(ctx.raml, ctx.graph.addresses)
        seen: set[tuple[str, Position, str]] = set()
        for iri, node in ctx.graph.nodes.items():
            for facet, owner in prose_of(node.entity):
                for link in links.links(facet, owner):
                    if not link.explicit or link.outcome is Outcome.RESOLVED:
                        continue
                    key = (facet.location, facet.key_pos, link.label)
                    if key in seen:
                        continue
                    seen.add(key)
                    info: dict[str, object] = {'link': link.written}
                    if link.outcome is Outcome.AMBIGUOUS:
                        info['targets'] = [target.address for target in link.targets]
                    yield ctx.at(
                        self.meta,
                        _MESSAGES[link.outcome],
                        location=facet.location,
                        position=facet.key_pos,
                        iri=iri,
                        **info,
                    )
