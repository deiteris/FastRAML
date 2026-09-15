"""Whole-document lint rules over the effective graph."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from fastraml.views.graph import is_declaration
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.views.lint.engine import Context

__all__ = ['UnusedTrait', 'UnusedType']


class UnusedType:
    meta: ClassVar = RuleMeta(
        id='unused-type',
        category=Category.SPEC,
        summary='a declared type that nothing references',
        rationale=(
            'An unreferenced declaration contributes no constraint to the effective API. It is either dead '
            'weight or evidence that a payload or parameter was not wired to the type intended for it.'
        ),
        severity=Severity.INFO,
        good=(
            '#%RAML 1.0\ntitle: t\ntypes:\n  Used: string\n/a:\n  get:\n    responses:\n      200:\n'
            '        body:\n          application/json:\n            type: Used\n'
        ),
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  Orphan: string\n',
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        for iri, node in ctx.graph.nodes.items():
            if node.kinds[0] != 'Type' or not is_declaration(iri):
                continue
            if node.attributes.get('isAnnotationType') or any(
                edge.predicate != 'declares' for edge in ctx.graph.into(iri)
            ):
                continue
            base = ctx.graph.shape_at(iri)
            if base is not None:
                yield ctx.at(
                    self.meta,
                    'type is never referenced',
                    location=base.location,
                    position=base.key_pos,
                    iri=iri,
                    type=base.name,
                )


class UnusedTrait:
    meta: ClassVar = RuleMeta(
        id='unused-trait',
        category=Category.SPEC,
        summary='a declared trait that no operation applies',
        rationale=(
            'A trait exists only to contribute its method facets at an application site. A trait with no '
            'application changes no operation and is either obsolete or was omitted from an `is:` list.'
        ),
        severity=Severity.INFO,
        good='#%RAML 1.0\ntitle: t\ntraits:\n  paged:\n    queryParameters:\n      page?: integer\n/a:\n  get:\n    is: [paged]\n',
        bad='#%RAML 1.0\ntitle: t\ntraits:\n  paged:\n    queryParameters:\n      page?: integer\n',
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        for iri, node in ctx.graph.nodes.items():
            if node.kinds[0] != 'Trait' or any(edge.predicate == 'appliesTrait' for edge in ctx.graph.into(iri)):
                continue
            definition = node.entity
            yield ctx.at(
                self.meta,
                'trait is never applied',
                location=definition.location,
                position=definition.key_pos,
                iri=iri,
                trait=definition.name,
            )
