"""Whole-document lint rules over the effective graph."""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, ClassVar

from fastraml.nodes import TypeNode
from fastraml.parser.fragments import APIFragment, NamedExample
from fastraml.types.examples import examples_of
from fastraml.views.graph import USE_EDGES, is_declaration
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.positions import Position
    from fastraml.views.lint.engine import Context

__all__ = ['NonStrictExample', 'RemoteFragment', 'UnusedTrait', 'UnusedType']


class NonStrictExample:
    meta: ClassVar = RuleMeta(
        id='non-strict-example',
        category=Category.STYLE,
        summary='examples should keep validation enabled',
        rationale=(
            'Setting strict: false disables ordinary example validation, so an example can drift from its '
            'declared type without being rejected. Omit strict or set it to true to keep examples checked.'
        ),
        severity=Severity.WARNING,
        references=('RAML 1.0 § Single Example', 'RAML 1.0 § Multiple Examples'),
        good='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    type: string\n    example: {value: x, strict: true}\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    type: string\n    example: {value: x, strict: false}\n',
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        seen: set[tuple[str, Position]] = set()
        types = ((iri, examples_of(node.entity)) for iri, node in ctx.graph.nodes.items() if isinstance(node, TypeNode))
        # NamedExample fragments have no graph node, including at the entry point.
        fragments = (
            ('', fragment.examples.values())
            for fragment in ctx.raml.fragments.values()
            if isinstance(fragment, NamedExample)
        )
        for iri, examples in chain(types, fragments):
            for example in examples:
                strict = example.strict
                if strict is None or strict.value:
                    continue
                site = (strict.location, strict.key_pos)
                if site in seen:
                    continue
                seen.add(site)
                yield ctx.on(
                    self.meta,
                    'example disables validation',
                    strict,
                    iri=iri,
                    example=example.name or 'example',
                )


class RemoteFragment:
    meta: ClassVar = RuleMeta(
        id='remote-fragment',
        category=Category.STYLE,
        summary='avoid depending on an externally hosted RAML fragment',
        rationale=(
            'A document that imports a hosted fragment depends on its availability and on the content '
            'that server returns later. Keep a local copy for reproducible parses.'
        ),
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  Local: string\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  Remote: !include https://example.test/type.raml\n',
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        for refs in ctx.raml.include_refs.values():
            for ref in refs:
                if ref.abs_uri.startswith(('http://', 'https://')):
                    yield ctx.at(
                        self.meta,
                        'fragment depends on a remote URL',
                        location=ref.source_uri,
                        position=ref.position,
                        path=ref.abs_uri,
                    )
        for fragment in ctx.raml.fragments.values():
            for link in fragment.uses.values():
                target = link.link.location if link.link is not None else ''
                if target.startswith(('http://', 'https://')):
                    yield ctx.at(
                        self.meta,
                        'fragment depends on a remote URL',
                        location=link.location,
                        position=link.value_pos,
                        path=target,
                    )


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
        if not isinstance(ctx.raml.entry_point, APIFragment):
            return
        api = next((iri for iri, node in ctx.graph.nodes.items() if node.kinds[0] == 'Api'), None)
        used = set() if api is None else {route.target for route in ctx.graph.walk(api, USE_EDGES)}
        for iri, node in ctx.graph.nodes.items():
            if (
                not isinstance(node, TypeNode)
                or not is_declaration(iri)
                or node.entity.is_annotation_type
                or iri in used
            ):
                continue
            base = node.entity
            yield ctx.on(self.meta, 'type is never referenced', base, iri=iri, type=base.name)


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
        if not isinstance(ctx.raml.entry_point, APIFragment):
            return
        for iri, node in ctx.graph.nodes.items():
            if node.kinds[0] != 'Trait' or any(edge.predicate == 'appliesTrait' for edge in ctx.graph.into(iri)):
                continue
            definition = node.entity
            yield ctx.on(self.meta, 'trait is never applied', definition, iri=iri, trait=definition.name)
