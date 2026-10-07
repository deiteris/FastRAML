"""The structural grammar shared by extension merge and source queries.

These are mapping positions, not annotation targets or a second resolver.
`child_site` is the extension merge's existing grammar (docs/19 § 3.1).
`keys` projects a composed source tree into those positions for an editor
(docs/21 § 4.2); data and application arguments stay opaque.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Final

from fastraml import facet_names as fn
from fastraml.parser.annotations import is_annotation_key
from fastraml.parser.facets import annotated_scalar_value
from fastraml.yamlnode import NodeKind, pairs

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.yamlnode import Node

__all__ = [
    'METHODS',
    'NAME_MAPS',
    'Key',
    'Site',
    'child_site',
    'facet_site',
    'fragment_site',
    'is_media_type_map',
    'keys',
]

#: Resource methods; optional `?` spellings belong to resource-type templates.
METHODS: Final = frozenset({'get', 'patch', 'put', 'post', 'delete', 'options', 'head'})


class Site(Enum):
    """The grammar position of a mapping, which decides what its keys mean."""

    ROOT = auto()
    RESOURCE = auto()
    METHOD = auto()
    RESPONSE = auto()
    BODY = auto()
    TYPE = auto()
    SECURITY_SCHEME = auto()
    APPLICATION = auto()
    DATA = auto()
    DOCUMENTATION = auto()
    GENERIC = auto()
    TYPES = auto()
    ANNOTATION_TYPES = auto()
    TRAITS = auto()
    RESOURCE_TYPES = auto()
    SECURITY_SCHEMES = auto()
    NAMED_TYPES = auto()
    RESPONSES = auto()
    EXAMPLES = auto()


NAME_MAPS: Final = {
    Site.TYPES: Site.TYPE,
    Site.ANNOTATION_TYPES: Site.TYPE,
    Site.NAMED_TYPES: Site.TYPE,
    Site.TRAITS: Site.METHOD,
    Site.RESOURCE_TYPES: Site.RESOURCE,
    Site.SECURITY_SCHEMES: Site.SECURITY_SCHEME,
    Site.RESPONSES: Site.RESPONSE,
    Site.EXAMPLES: Site.DATA,
}

_CHILDREN: Final[dict[Site, dict[str, Site]]] = {
    Site.ROOT: {
        fn.FACET_TYPES: Site.TYPES,
        fn.FACET_SCHEMAS: Site.TYPES,
        fn.FACET_ANNOTATION_TYPES: Site.ANNOTATION_TYPES,
        fn.FACET_TRAITS: Site.TRAITS,
        fn.FACET_RESOURCE_TYPES: Site.RESOURCE_TYPES,
        fn.FACET_SECURITY_SCHEMES: Site.SECURITY_SCHEMES,
        fn.FACET_BASE_URI_PARAMETERS: Site.NAMED_TYPES,
        fn.FACET_DOCUMENTATION: Site.DOCUMENTATION,
        fn.FACET_SECURED_BY: Site.APPLICATION,
    },
    Site.RESOURCE: {
        fn.FACET_URI_PARAMETERS: Site.NAMED_TYPES,
        fn.FACET_TYPE: Site.APPLICATION,
        fn.FACET_IS: Site.APPLICATION,
        fn.FACET_SECURED_BY: Site.APPLICATION,
    },
    Site.METHOD: {
        fn.FACET_HEADERS: Site.NAMED_TYPES,
        fn.FACET_QUERY_PARAMETERS: Site.NAMED_TYPES,
        fn.FACET_QUERY_STRING: Site.TYPE,
        fn.FACET_BODY: Site.BODY,
        fn.FACET_RESPONSES: Site.RESPONSES,
        fn.FACET_IS: Site.APPLICATION,
        fn.FACET_SECURED_BY: Site.APPLICATION,
    },
    Site.RESPONSE: {fn.FACET_HEADERS: Site.NAMED_TYPES, fn.FACET_BODY: Site.BODY},
    Site.TYPE: {
        fn.FACET_PROPERTIES: Site.NAMED_TYPES,
        fn.FACET_FACETS: Site.NAMED_TYPES,
        fn.FACET_ITEMS: Site.TYPE,
        fn.FACET_TYPE: Site.TYPE,
        fn.FACET_SCHEMA: Site.TYPE,
        fn.FACET_EXAMPLES: Site.EXAMPLES,
        fn.FACET_EXAMPLE: Site.DATA,
        fn.FACET_DEFAULT: Site.DATA,
    },
    Site.SECURITY_SCHEME: {fn.FACET_DESCRIBED_BY: Site.METHOD},
}


def child_site(site: Site, name: str) -> Site:  # noqa: PLR0911 - one return per grammar rule
    """The grammar position of the value under `name` at `site`."""
    named = NAME_MAPS.get(site)
    if named is not None:
        return named
    if site is Site.DATA or site is Site.APPLICATION:
        return site
    if is_annotation_key(name):
        return Site.DATA
    if site is Site.BODY:
        return Site.TYPE if '/' in name else child_site(Site.TYPE, name)
    if site is Site.ROOT or site is Site.RESOURCE:
        if name.startswith('/'):
            return Site.RESOURCE
        if site is Site.RESOURCE and name.removesuffix('?') in METHODS:
            return Site.METHOD
    children = _CHILDREN.get(site)
    if children is not None:
        found = children.get(name)
        if found is not None:
            return found
    return Site.GENERIC


def facet_site(site: Site, name: str) -> Site:
    """The body's type facets use the type-declaration grammar."""
    return Site.TYPE if site is Site.BODY and '/' not in name else site


def is_media_type_map(node: Node) -> bool:
    """The body's media-type spelling, shared with its decoder (docs/08 § 6.3)."""
    return (
        node.kind is NodeKind.MAPPING
        and bool(node.content)
        and all('/' in node.content[index].value for index in range(0, len(node.content), 2))
    )


def fragment_site(kind: str | None) -> Site:
    """The root syntax of a typed fragment; unknown files are opaque."""
    return {
        'API': Site.ROOT,
        'Overlay': Site.ROOT,
        'Extension': Site.ROOT,
        'Library': Site.ROOT,
        'DataType': Site.TYPE,
        'AnnotationTypeDeclaration': Site.TYPE,
        'Trait': Site.METHOD,
        'ResourceType': Site.RESOURCE,
        'SecurityScheme': Site.SECURITY_SCHEME,
        'DocumentationItem': Site.DOCUMENTATION,
        'NamedExample': Site.EXAMPLES,
    }.get(kind or '', Site.DATA)


@dataclass(frozen=True, slots=True, eq=False)
class Key:
    """One source mapping key and its grammar position."""

    node: Node
    value: Node
    owner: Node
    site: Site
    #: The containing name table, e.g. `headers` or `facets`.
    table: str

    def type_value(self) -> Node | None:
        """The authored type-expression value, when this key declares one."""
        if (self.value.line, self.value.column) < (self.node.line, self.node.column):
            return None
        if self.site in (Site.TYPES, Site.ANNOTATION_TYPES, Site.NAMED_TYPES) or (
            self.site is Site.TYPE and self.node.value in ('type', 'schema')
        ):
            return annotated_scalar_value(self.value)
        return None

    @property
    def annotation(self) -> bool:
        """An annotation key in a facet position, rather than a declared name."""
        return self.site not in NAME_MAPS and is_annotation_key(self.node.value)


def keys(root: Node, site: Site, *, table: str = '') -> Iterator[Key]:
    """Source keys, without decoding data or expanding templates.

    A body's two spellings are distinguished by its decoder's predicate.
    Unknown facets can contain user data, so their children are opaque too.
    """
    stack = [(root, site, table)]
    while stack:
        node, context, table = stack.pop()
        if context in (Site.DATA, Site.APPLICATION, Site.GENERIC):
            continue
        if node.kind is NodeKind.SEQUENCE:
            stack.extend(
                (item, context, table)
                for item in reversed(node.content)
                if (item.line, item.column) >= (node.line, node.column)
            )
            continue
        if node.kind is not NodeKind.MAPPING:
            continue
        if context is Site.BODY and not is_media_type_map(node):
            if any('/' in key.value for key, _ in pairs(node)):
                continue  # a mixed body has no sound grammar context
            context = Site.TYPE
        children = []
        for key, value in pairs(node):
            yield Key(key, value, node, context, table)
            if (value.line, value.column) < (key.line, key.column):
                continue  # an expanded alias's children are written at its anchor
            child = child_site(context, key.value)
            child_table = key.value if child in NAME_MAPS else table
            children.append((value, child, child_table))
        stack.extend(reversed(children))
