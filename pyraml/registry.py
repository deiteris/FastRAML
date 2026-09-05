"""`Raml` — the registry that owns one parse.

Caches, ID generation and the cross-file indices live here rather than being
threaded through every call. Two of those caches are load-bearing rather than
merely fast: `fragments` makes a file decode at most once (invariant I3) and
`include_nodes` makes it compose at most once (I2), which is what turns a
mutually-importing pair of libraries into a cyclic object graph instead of an
infinite recursion.

This module deliberately imports nothing from `pyraml.parser` or `pyraml.types`
at runtime — the annotations are `TYPE_CHECKING`-only — so the import graph
stays acyclic without runtime indirection. See docs/02-architecture.md section 3.
"""

from __future__ import annotations

import itertools
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Literal

from pyraml.domains import DomainLocation
from pyraml.loaders import SchemeLoader
from pyraml.yamlnode import DEFAULT_MAX_DEPTH, NodeKind

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from pyraml.loaders import ResourceLoader
    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.fragments import Fragment, ReferenceResolver
    from pyraml.parser.includes import IncludeRef
    from pyraml.parser.structural_merge import ProvenanceOverlay
    from pyraml.types.expressions import ExprCache
    from pyraml.types.jsonschema_ import SchemaRegistry
    from pyraml.yamlnode import Node

    # Phase 2 onwards replace these aliases with the real classes. They are
    # written out so that the field list below reads as its finished form and
    # `grep` finds every seam.
    BaseShape = Any
    EndPoint = Any
    SecurityScheme = Any
    SourceInfo = Any

#: The two facets whose *value* names a type. `provenance_scope_for` consults
#: them before the mapping that holds them, because a caller-substituted `type:`
#: must beat the scope of the grafted body it now sits inside.
_TYPE_FACETS: Final = frozenset({'type', 'schema'})

__all__ = [
    'DEFAULT_MAX_INCLUDE_SIZE',
    'ParseCtx',
    'Raml',
]

#: Per-file ceiling for an `!include` target, in bytes. `0` disables the limit.
DEFAULT_MAX_INCLUDE_SIZE: Final = 65536


@dataclass(frozen=True, slots=True)
class ParseCtx:
    """The lexical scope in effect while a fragment is being decoded.

    Every construct that can contain a name captures the top of `Raml`'s stack
    at creation time, so a trait body grafted onto an operation in another file
    still resolves its type names in the trait's own namespace.
    See docs/04-fragments-and-namespaces.md section 4.
    """

    anchor: ReferenceResolver | None = None
    #: Where an `(annotation)` written here is being applied. On the stack
    #: rather than passed to `unmarshal_domain_extension`, because the answer is
    #: not always known at the decode site: an annotation inside a trait body
    #: records the site it is *materialised* at, not `Trait`
    #: (docs/09-security-and-annotations.md section B5).
    target: DomainLocation = DomainLocation.API


_EMPTY_CTX: Final = ParseCtx()


class Raml:
    """Everything produced by one parse.

    A `Raml` instance is single-threaded and its contents are mutable; the model
    it exposes may be cyclic. See docs/13-public-api.md section 7.
    """

    # The grouping, and the order within it, mirror the field list in
    # docs/02-architecture.md section 3 so the two can be read side by side.
    __slots__ = (  # noqa: RUF023 - grouped by role, not sorted
        # --- configuration ---------------------------------------------------
        'loader',
        'max_depth',
        'max_include_size',
        'regex_engine',
        'retain_source',
        'workspace_root_uri',
        # --- caches ----------------------------------------------------------
        'expr_cache',
        'fragments',
        'include_nodes',
        'json_schema_registry',
        # --- indices ---------------------------------------------------------
        'domain_extensions',
        'endpoints',
        'fragment_annotations',
        'fragment_resolvers',
        'fragment_typedefs',
        'fragment_types',
        'include_refs',
        'shapes',
        # --- work queues -----------------------------------------------------
        '_discriminator_shapes',
        'unresolved_shapes',
        # --- global metadata harvested from the API root ---------------------
        'global_media_types',
        'global_protocols',
        'global_secured_by',
        # --- transient parse state -------------------------------------------
        '_active_overlay',
        '_id_counter',
        '_parse_ctx_stack',
        'entry_point',
        'source_info',
        'source_nodes',
        'unwrapped',
    )

    def __init__(  # noqa: PLR0913 - the parse's configuration, keyword-only, one field each
        self,
        *,
        loader: ResourceLoader | None = None,
        workspace_root_uri: str = '',
        max_include_size: int = DEFAULT_MAX_INCLUDE_SIZE,
        max_depth: int = DEFAULT_MAX_DEPTH,
        retain_source: bool = False,
        regex_engine: Literal['re', 're2'] = 're',
    ) -> None:
        # An empty SchemeLoader rather than None: a registry built without one
        # reports "no loader for URI scheme" instead of an AttributeError.
        self.loader: ResourceLoader = loader if loader is not None else SchemeLoader({})
        self.workspace_root_uri = workspace_root_uri
        self.max_include_size = max_include_size
        self.max_depth = max_depth
        self.retain_source = retain_source
        self.regex_engine = regex_engine

        self.fragments: dict[str, Fragment] = {}
        self.include_nodes: dict[str, Node] = {}
        # One parse per distinct expression text, not per occurrence. Held here
        # rather than on the expression parser so it dies with the parse.
        self.expr_cache: ExprCache = {}
        # Built on first use by `types/jsonschema_.py`: this module imports
        # nothing from `types/` at runtime (docs/02 section 3).
        self.json_schema_registry: SchemaRegistry | None = None

        self.fragment_types: dict[str, dict[str, BaseShape]] = {}
        self.fragment_annotations: dict[str, dict[str, BaseShape]] = {}
        self.fragment_resolvers: dict[str, ReferenceResolver] = {}
        self.fragment_typedefs: dict[str, list[BaseShape]] = {}
        # The pre-P9 inline-declaration rule visits only shapes that actually
        # wrote either discriminator facet, not the whole reachable type graph.
        self._discriminator_shapes: list[BaseShape] = []
        self.endpoints: dict[str, EndPoint] = {}
        self.shapes: list[BaseShape] = []
        self.domain_extensions: list[DomainExtension] = []
        self.include_refs: dict[str, list[IncludeRef]] = {}

        # A worklist, drained from the left in P7 while resolution appends to
        # the right; a deque keeps both ends O(1).
        self.unresolved_shapes: deque[BaseShape] = deque()

        self.global_protocols: list[str] = []
        self.global_media_types: list[str] = []
        self.global_secured_by: list[SecurityScheme] = []

        self._parse_ctx_stack: list[ParseCtx] = []
        # The overlay of the IR unit currently being materialized, or None
        # outside stage 2 — which is where all but endpoint decoding happens.
        self._active_overlay: ProvenanceOverlay | None = None
        self._id_counter = itertools.count(1)
        self.entry_point: Fragment | None = None
        self.unwrapped = False
        self.source_nodes: dict[str, Node] = {}
        self.source_info: SourceInfo | None = None

    def __repr__(self) -> str:
        return f'Raml({self.location!r}, fragments={len(self.fragments)})'

    # -- identity -------------------------------------------------------------

    def next_id(self) -> int:
        """The next entity id. One counter per parse; see docs/02 section 3.1."""
        return next(self._id_counter)

    # -- the parse-context stack ----------------------------------------------

    def push_ctx(self, ctx: ParseCtx) -> None:
        self._parse_ctx_stack.append(ctx)

    def pop_ctx(self) -> None:
        if self._parse_ctx_stack:
            self._parse_ctx_stack.pop()

    def current_ctx(self) -> ParseCtx:
        """The innermost scope, or an empty one outside any fragment decode."""
        if not self._parse_ctx_stack:
            return _EMPTY_CTX
        return self._parse_ctx_stack[-1]

    @contextmanager
    def target_scope(self, target: DomainLocation) -> Iterator[None]:
        """Decode a construct that annotations attach to a different thing.

        The anchor is carried over unchanged — this narrows where an annotation
        is being applied, never which namespace a name resolves in. A context
        manager rather than a push/pop pair because a decoder that raises
        mid-construct must not leave the site behind on the stack.
        """
        self.push_ctx(replace(self.current_ctx(), target=target))
        try:
            yield
        finally:
            self.pop_ctx()

    # -- the provenance overlay (docs/08 section 6.3) --------------------------

    @contextmanager
    def active_overlay(self, overlay: ProvenanceOverlay) -> Iterator[None]:
        """Decode one IR unit's body with its provenance marks readable.

        Saved and restored rather than set, because an endpoint's own body
        decode encloses each of its operations'.
        """
        previous = self._active_overlay
        self._active_overlay = overlay
        try:
            yield
        finally:
            self._active_overlay = previous

    @contextmanager
    def provenance_scope(self, node: Node) -> Iterator[None]:
        """Push the scope the active overlay records for `node`, if it records one.

        A node with no mark is one the enclosing unit wrote itself, and the
        scope already in effect is the right one for it.
        """
        overlay = self._active_overlay
        scope = None if overlay is None else overlay.get(node)
        if scope is None:
            yield
            return
        self.push_ctx(scope)
        try:
            yield
        finally:
            self.pop_ctx()

    def scope_for(self, node: Node) -> ParseCtx | None:
        """The scope a type-bearing node should be decoded under, most specific first.

        The `type:`/`schema:` facet value of a mapping beats the mapping itself:
        a value the caller substituted is more specific than the grafted body it
        was substituted into.
        """
        overlay = self._active_overlay
        if overlay is None:
            return None
        if node.kind is NodeKind.MAPPING:
            content = node.content
            for index in range(0, len(content) - 1, 2):
                if content[index].value in _TYPE_FACETS:
                    scope = overlay.get(content[index + 1])
                    if scope is not None:
                        return scope
        return overlay.get(node)

    def location_of(self, node: Node, default: str) -> str:
        """Which file `node` was authored in.

        The single answer to that question, consulted by every entity
        constructor: a diagnostic inside a trait-contributed response names the
        trait's path, not the API's, even when the node sits below a
        merge-synthesised container that carries no mark of its own.
        """
        overlay = self._active_overlay
        if overlay is None:
            return default
        scope = overlay.get(node)
        if scope is not None and scope.anchor is not None:
            return scope.anchor.location
        return default

    # -- stores ---------------------------------------------------------------

    def get_fragment(self, uri: str) -> Fragment | None:
        return self.fragments.get(uri)

    def put_fragment(self, uri: str, fragment: Fragment) -> None:
        self.fragments[uri] = fragment

    def put_type(self, name: str, location: str, shape: BaseShape) -> None:
        self.fragment_types.setdefault(location, {})[name] = shape

    def put_annotation_type(self, name: str, location: str, shape: BaseShape) -> None:
        self.fragment_annotations.setdefault(location, {})[name] = shape

    def put_resolver(self, location: str, resolver: ReferenceResolver) -> None:
        """Index a fragment by the names it can resolve (docs/04 section 4.2)."""
        self.fragment_resolvers[location] = resolver

    def resolver_at(self, location: str) -> ReferenceResolver | None:
        """The scope a name written in `location` resolves in.

        P7's fallback for a shape whose `anchor` is `None` — one built outside
        any fragment decode, which means programmatic construction or a test.
        Every shape a parse produces carries an anchor instead.
        """
        return self.fragment_resolvers.get(location)

    def put_typedef(self, location: str, shape: BaseShape) -> None:
        """Record a shape in the flat per-file index unwrap and validation iterate."""
        self.fragment_typedefs.setdefault(location, []).append(shape)

    def put_shape(self, shape: BaseShape) -> None:
        self.shapes.append(shape)

    def store_source_node(self, uri: str, node: Node) -> None:
        """Keep a fragment's root node when `retain_source` is on; else a no-op."""
        if self.retain_source:
            self.source_nodes[uri] = node

    # -- read surface (docs/13-public-api.md section 3) -----------------------

    @property
    def location(self) -> str:
        return '' if self.entry_point is None else self.entry_point.location

    @property
    def is_unwrapped(self) -> bool:
        return self.unwrapped

    def types_in(self, uri: str) -> Mapping[str, BaseShape]:
        return self.fragment_types.get(uri, {})

    def annotation_types_in(self, uri: str) -> Mapping[str, BaseShape]:
        return self.fragment_annotations.get(uri, {})

    def typedefs_in(self, uri: str) -> Sequence[BaseShape]:
        return self.fragment_typedefs.get(uri, ())

    def include_refs_in(self, uri: str) -> Sequence[IncludeRef]:
        return self.include_refs.get(uri, ())

    def source_node(self, uri: str) -> Node | None:
        return self.source_nodes.get(uri)
