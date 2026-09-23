"""The merge that applies an Overlay or Extension to its master, and the overlay check.

Spec section Merging Rules, restated in docs/19 § 3: the extension document
wins on single values, appends to arrays, adds values not already present to
multi-value properties, and recurses into objects. A key the target lacks is
added after removing the properties it conflicts with. Unlike the template merge
(`structural_merge.py`), the *second* tree wins.

The merge has to know what a key means, not only its text:
`properties: {description: string}` declares a property *named* `description`,
and an Overlay may add a `description` facet but not a property. So every
mapping is visited with a `Site`, its grammar position (docs/19 § 3.1).

Like the template merge, it mutates neither input and reuses every child it
keeps (invariants I9, I10). A subtree taken from the extension document is
handed to `mark`, which records who wrote it (docs/19 § 5.3). A value restated
unchanged is kept as the target's node, so it is neither marked nor a change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Final

from fastraml.errors import Accumulator
from fastraml.parser.annotations import is_annotation_key
from fastraml.parser.source_ir import METHODS
from fastraml.parser.structural_merge import node_value_equal
from fastraml.yamlnode import TAG_MAP, TAG_SEQ, Node, NodeKind, is_null, node_error, with_content

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastraml.errors import RamlError

__all__ = [
    'ExtensionMergeResult',
    'Site',
    'merge_extension',
]


class Site(Enum):
    """The grammar position of a mapping, which decides what its keys mean."""

    ROOT = auto()
    RESOURCE = auto()
    METHOD = auto()
    RESPONSE = auto()
    #: A `body:` mapping: media-type keys, or else itself a type declaration.
    BODY = auto()
    TYPE = auto()
    SECURITY_SCHEME = auto()
    #: A resource-type, trait or security-scheme application. Always a simple
    #: property, whatever its shape (spec section Merging Rules, Exceptions).
    APPLICATION = auto()
    #: User data: an example, a default, an enum, an annotation value.
    DATA = auto()
    DOCUMENTATION = auto()
    GENERIC = auto()
    # -- name maps: every key is a name, not a facet -------------------------
    TYPES = auto()
    ANNOTATION_TYPES = auto()
    TRAITS = auto()
    RESOURCE_TYPES = auto()
    SECURITY_SCHEMES = auto()
    #: `properties`, `facets`, and every parameter or header map.
    NAMED_TYPES = auto()
    RESPONSES = auto()
    EXAMPLES = auto()


#: Where each name in a name map leads.
_NAME_MAPS: Final = {
    Site.TYPES: Site.TYPE,
    Site.ANNOTATION_TYPES: Site.TYPE,
    Site.NAMED_TYPES: Site.TYPE,
    Site.TRAITS: Site.METHOD,
    Site.RESOURCE_TYPES: Site.RESOURCE,
    Site.SECURITY_SCHEMES: Site.SECURITY_SCHEME,
    Site.RESPONSES: Site.RESPONSE,
    Site.EXAMPLES: Site.DATA,
}

#: The facet keys whose value has its own grammar position, per position. A
#: key listed nowhere recurses as `GENERIC`.
_CHILDREN: Final[dict[Site, dict[str, Site]]] = {
    Site.ROOT: {
        'types': Site.TYPES,
        'schemas': Site.TYPES,
        'annotationTypes': Site.ANNOTATION_TYPES,
        'traits': Site.TRAITS,
        'resourceTypes': Site.RESOURCE_TYPES,
        'securitySchemes': Site.SECURITY_SCHEMES,
        'baseUriParameters': Site.NAMED_TYPES,
        'documentation': Site.DOCUMENTATION,
        'securedBy': Site.APPLICATION,
    },
    Site.RESOURCE: {
        'uriParameters': Site.NAMED_TYPES,
        'type': Site.APPLICATION,
        'is': Site.APPLICATION,
        'securedBy': Site.APPLICATION,
    },
    Site.METHOD: {
        'headers': Site.NAMED_TYPES,
        'queryParameters': Site.NAMED_TYPES,
        'queryString': Site.TYPE,
        'body': Site.BODY,
        'responses': Site.RESPONSES,
        'is': Site.APPLICATION,
        'securedBy': Site.APPLICATION,
    },
    Site.RESPONSE: {
        'headers': Site.NAMED_TYPES,
        'body': Site.BODY,
    },
    Site.TYPE: {
        'properties': Site.NAMED_TYPES,
        'facets': Site.NAMED_TYPES,
        'items': Site.TYPE,
        'type': Site.TYPE,
        'schema': Site.TYPE,
        'examples': Site.EXAMPLES,
        'example': Site.DATA,
        'default': Site.DATA,
        # `enum` is not data: the spec's own example of a multi-value simple
        # property, so an extension's values are added to the target's.
    },
    Site.SECURITY_SCHEME: {
        'describedBy': Site.METHOD,
    },
}

#: Properties that cannot coexist in one object; adding one removes the other
#: (docs/19 § 3.4). Only pairs the spec itself declares exclusive and that
#: are not synonyms.
_CONFLICTS: Final[dict[Site, dict[str, str]]] = {
    Site.METHOD: {'queryString': 'queryParameters', 'queryParameters': 'queryString'},
    Site.TYPE: {'example': 'examples', 'examples': 'example'},
}

#: Deprecated spellings the spec calls synonymous with a current one. They are
#: one property to the merge, so `schemas:` in an extension adds to the
#: master's `types:` instead of displacing it (docs/19 § 3.2).
_SYNONYMS: Final[dict[Site, dict[str, str]]] = {
    Site.ROOT: {'schemas': 'types'},
    Site.TYPE: {'schema': 'type'},
}

#: Keys that accept one scalar or a sequence of them; a lone scalar is merged
#: as a one-item sequence (docs/19 § 3.2). `type` is not one of them: a scalar
#: there is a type expression, a sequence is multiple inheritance.
_SEQUENCE_KEYS: Final = frozenset({'protocols', 'mediaType', 'is', 'securedBy', 'allowedTargets'})

#: The spec's ignored properties, and `extends` itself; at the root only, since
#: a trait's `usage` is an ordinary facet an Overlay may change.
_IGNORED_AT_ROOT: Final = frozenset({'uses', 'usage', 'extends'})

#: Keys an Overlay may add or change anywhere they are facets (docs/19 § 4.2).
_OVERLAY_FACETS: Final = frozenset(
    {'title', 'displayName', 'description', 'usage', 'example', 'examples', 'documentation'}
)

#: The root declaration maps, by the name the visibility check uses (docs/19 § 5.2).
_DECLARATION_KINDS: Final = {
    Site.TYPES: 'types',
    Site.ANNOTATION_TYPES: 'annotationTypes',
    Site.TRAITS: 'traits',
    Site.RESOURCE_TYPES: 'resourceTypes',
    Site.SECURITY_SCHEMES: 'securitySchemes',
}


@dataclass(slots=True, eq=False)
class ExtensionMergeResult:
    """The target tree after one extension document, and what it declared."""

    tree: Node
    #: Root declaration names the document added, per kind (`types`, `traits`,
    #: ...), in document order. `schemas` entries count as `types`.
    declared: dict[str, list[str]] = field(default_factory=dict)
    #: Overlay violations, accumulated; `None` for an Extension or a clean Overlay.
    error: RamlError | None = None


def merge_extension(
    target: Node,
    extension: Node,
    *,
    location: str,
    overlay: bool,
    mark: Callable[[Node], None],
) -> ExtensionMergeResult:
    """Apply one extension document's root mapping to the target tree's.

    `location` is the extension document's URI, for violations. `mark` is
    called on every key and value subtree taken from `extension`.
    """
    merger = _Merger(location, overlay, mark)
    tree = merger.mapping(target, extension, Site.ROOT, free=False)
    return ExtensionMergeResult(tree=tree, declared=merger.declared, error=merger.violations.result())


def _child_site(site: Site, name: str) -> Site:  # noqa: PLR0911 - one return per grammar rule
    """The grammar position of the value under `name` in a mapping at `site`."""
    named = _NAME_MAPS.get(site)
    if named is not None:
        return named
    if site is Site.DATA or site is Site.APPLICATION:
        return site
    if is_annotation_key(name):
        return Site.DATA
    if site is Site.BODY:
        # A media-type key leads to a type declaration; any other key means the
        # body itself is one (docs/08 § 6.3).
        return Site.TYPE if '/' in name else _child_site(Site.TYPE, name)
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


def _canonical(site: Site, name: str) -> str:
    """The current spelling of a deprecated synonym; any other key unchanged."""
    synonyms = _SYNONYMS.get(_facet_site(site, name))
    return name if synonyms is None else synonyms.get(name, name)


def _facet_site(site: Site, name: str) -> Site:
    """`site`, or `TYPE` for a non-media key of a body that is itself a type."""
    if site is Site.BODY and '/' not in name:
        return Site.TYPE
    return site


class _Merger:
    __slots__ = ('declared', 'location', 'mark', 'overlay', 'violations')

    def __init__(self, location: str, overlay: bool, mark: Callable[[Node], None]) -> None:  # noqa: FBT001 - private
        self.location = location
        self.overlay = overlay
        self.mark = mark
        self.violations = Accumulator()
        self.declared: dict[str, list[str]] = {}

    # -- the recursion --------------------------------------------------------

    def mapping(self, target: Node, extension: Node, site: Site, *, free: bool) -> Node:
        """Target keys in target order, then new keys in extension order (I8)."""
        own = target.content
        merged: list[Node | None] = list(own)
        index = {own[i].value: i for i in range(0, len(own), 2)}
        # A synonym finds its partner only when the exact spelling is absent, so
        # a target that wrote both (which its decode rejects) still merges pairwise.
        synonyms = {_canonical(site, own[i].value): i for i in range(len(own) - 2, -1, -2)}
        added: list[Node] = []
        changed = False
        content = extension.content
        for i in range(0, len(content), 2):
            key, value = content[i], content[i + 1]
            name = key.value
            if site is Site.ROOT and name in _IGNORED_AT_ROOT:
                continue
            position = index.get(name)
            if position is None:
                position = synonyms.get(_canonical(site, name))
            if position is None or merged[position] is None:
                self._remove_conflicts(merged, index, site, name, key, free=free)
                self._change(site, name, key, 'added', free=free)
                self._declare(site, name)
                self._declare_all(_child_site(site, name), value)
                self.mark(key)
                self.mark(value)
                added += (key, value)
                changed = True
                continue
            old = merged[position + 1]
            assert old is not None  # noqa: S101 - a live key's value is live
            child = _child_site(site, name)
            child_free = free or self._allowed_key(site, name)
            new, replaced = self._value(old, value, child, name, key, site, free=free, child_free=child_free)
            if new is old:
                continue
            changed = True
            merged[position + 1] = new
            if replaced:
                # The pair is the extension document's now, key and all, so a
                # diagnostic on either names one file (docs/19 § 5.3).
                merged[position] = key
                self.mark(key)
        if not changed:
            return target
        return with_content(target, [node for node in merged if node is not None] + added)

    def _value(  # noqa: PLR0911, PLR0913, PLR0917 - one decision table over the property kinds
        self,
        old: Node,
        new: Node,
        site: Site,
        name: str,
        key: Node,
        parent: Site,
        *,
        free: bool,
        child_free: bool,
    ) -> tuple[Node, bool]:
        """Merge one value. Returns the result and whether it replaced `old` wholesale."""
        if site is Site.DATA or site is Site.DOCUMENTATION:
            return self._replace(old, new, parent, name, key, free=free)
        old, new = _normalize(old, new, site, name)
        if site is Site.APPLICATION:
            # Applications are simple properties: a list of names is
            # multi-value, anything parameterised is replaced whole.
            if old.kind is NodeKind.SEQUENCE and new.kind is NodeKind.SEQUENCE and _scalars(old) and _scalars(new):
                return self._union(old, new, parent, name, key, free=free), False
            return self._replace(old, new, parent, name, key, free=free)
        if old.kind is not new.kind:
            return self._replace(old, new, parent, name, key, free=free)
        if new.kind is NodeKind.MAPPING:
            merged = self.mapping(old, new, site, free=child_free)
            return merged, False
        if new.kind is NodeKind.SEQUENCE:
            return self._union(old, new, parent, name, key, free=free), False
        return self._replace(old, new, parent, name, key, free=free)

    def _replace(  # noqa: PLR0913 - the pair and where it sits
        self, old: Node, new: Node, site: Site, name: str, key: Node, *, free: bool
    ) -> tuple[Node, bool]:
        if node_value_equal(old, new):
            # Restated unchanged: not a difference, and still the master's node.
            return old, False
        self._change(site, name, key, 'changed', free=free)
        self.mark(new)
        return new, True

    def _union(  # noqa: PLR0913 - the pair and where it sits
        self, old: Node, new: Node, site: Site, name: str, key: Node, *, free: bool
    ) -> Node:
        """A multi-value or array property: each item not already present is added.

        The spec says only that an array's objects "are added"; skipping ones
        already there is what makes a restated array no change (docs/19 § 7).
        Scalars compare through a set, so a long `enum` is not quadratic.
        """
        items = list(old.content)
        scalars = {(item.tag, item.value) for item in items if item.kind is NodeKind.SCALAR}
        composites = [item for item in items if item.kind is not NodeKind.SCALAR]
        for item in new.content:
            if item.kind is NodeKind.SCALAR:
                identity = (item.tag, item.value)
                if identity in scalars:
                    continue
                scalars.add(identity)
            else:
                if any(node_value_equal(existing, item) for existing in composites):
                    continue
                composites.append(item)
            self.mark(item)
            items.append(item)
        if len(items) == len(old.content):
            return old
        self._change(site, name, key, 'added', free=free)
        return with_content(old, items)

    # -- conflicts and declarations -------------------------------------------

    def _remove_conflicts(  # noqa: PLR0913 - the mapping being built and the key that displaces
        self,
        merged: list[Node | None],
        index: dict[str, int],
        site: Site,
        name: str,
        key: Node,
        *,
        free: bool,
    ) -> None:
        conflicts = _CONFLICTS.get(_facet_site(site, name))
        other = None if conflicts is None else conflicts.get(name)
        if other is None:
            return
        position = index.get(other)
        if position is None or merged[position] is None:
            return
        self._change(site, other, key, 'removed', free=free)
        merged[position] = merged[position + 1] = None

    def _declare(self, site: Site, name: str) -> None:
        kind = _DECLARATION_KINDS.get(site)
        if kind is not None:
            self.declared.setdefault(kind, []).append(name)

    def _declare_all(self, site: Site, value: Node) -> None:
        """A whole declaration map the target lacked: every name in it is new."""
        if site in _DECLARATION_KINDS and value.kind is NodeKind.MAPPING:
            for i in range(0, len(value.content), 2):
                self._declare(site, value.content[i].value)

    # -- the overlay check (docs/19 § 4) --------------------------------------

    def _change(self, site: Site, name: str, key: Node, change: str, *, free: bool) -> None:
        """Record one difference from the target tree; an Overlay may make only some."""
        if not self.overlay or free or self._allowed_key(site, name):
            return
        if site is Site.TYPES and change == 'added':
            return
        self.violations.add(
            node_error('not allowed in an overlay', self.location, key, info={'field': name, 'change': change})
        )

    @staticmethod
    def _allowed_key(site: Site, name: str) -> bool:
        """Whether everything at and below `name` is free for an Overlay to change."""
        if site in _NAME_MAPS or site is Site.DATA:
            # A key in a name map is a name — a property called `description`
            # is not the `description` facet.
            return False
        if name in _OVERLAY_FACETS or is_annotation_key(name):
            return True
        return site is Site.ROOT and name == 'annotationTypes'


def _scalars(node: Node) -> bool:
    """A scalar, or a sequence holding only scalars: a simple property's value."""
    if node.kind is NodeKind.SCALAR:
        return True
    return node.kind is NodeKind.SEQUENCE and all(item.kind is NodeKind.SCALAR for item in node.content)


def _normalize(old: Node, new: Node, site: Site, name: str) -> tuple[Node, Node]:  # noqa: PLR0911 - one return per spelling pair
    """Make two spellings of the same thing comparable (docs/19 § 3.2).

    Only a pair whose kinds differ is touched, and a wrapped scalar keeps its
    identity inside the new container.
    """
    if old.kind is new.kind:
        return old, new
    if is_null(old) and new.kind is NodeKind.MAPPING:
        return _empty_mapping(old), new
    if is_null(new) and old.kind is NodeKind.MAPPING:
        return old, _empty_mapping(new)
    if site is Site.TYPE:
        if old.kind is NodeKind.SCALAR and new.kind is NodeKind.MAPPING:
            return _type_mapping(old), new
        if new.kind is NodeKind.SCALAR and old.kind is NodeKind.MAPPING:
            return old, _type_mapping(new)
    if name in _SEQUENCE_KEYS:
        if old.kind is NodeKind.SCALAR and new.kind is NodeKind.SEQUENCE:
            return _one_item(old), new
        if new.kind is NodeKind.SCALAR and old.kind is NodeKind.SEQUENCE:
            return old, _one_item(new)
    return old, new


def _empty_mapping(at: Node) -> Node:
    return Node(NodeKind.MAPPING, TAG_MAP, '', [], at.line, at.column, at.end_line, at.end_column)


def _type_mapping(scalar: Node) -> Node:
    """`string` as `{type: string}`, positioned at the scalar."""
    key = Node(NodeKind.SCALAR, '!!str', 'type', [], scalar.line, scalar.column, scalar.line, scalar.column)
    return Node(
        NodeKind.MAPPING, TAG_MAP, '', [key, scalar], scalar.line, scalar.column, scalar.end_line, scalar.end_column
    )


def _one_item(scalar: Node) -> Node:
    return Node(
        NodeKind.SEQUENCE, TAG_SEQ, '', [scalar], scalar.line, scalar.column, scalar.end_line, scalar.end_column
    )
