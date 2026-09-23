"""`Raml`: the registry that owns one parse.

Holds configuration, caches, entity IDs, cross-file indices, the P7 worklist,
and the `ParseCtx` stack with the active provenance overlay. Two caches are
correctness requirements, not optimisations: `include_nodes` composes a file at
most once (invariant I2) and `fragments` decodes it at most once (I3). A
fragment is registered before its body decodes, so mutually importing libraries
become a cyclic model graph rather than infinite recursion.

Imports nothing from `fastraml.parser` or `fastraml.types` at runtime; the
annotations are `TYPE_CHECKING`-only. See docs/02-architecture.md § 3.
"""

from __future__ import annotations

import itertools
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from fastraml.domains import DomainLocation
from fastraml.loaders import SchemeLoader
from fastraml.yamlnode import AUTHORED_NODES, DEFAULT_MAX_DEPTH, NodeKind, mark_subtree

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from fastraml.loaders import ResourceLoader
    from fastraml.parser.annotations import DomainExtension
    from fastraml.parser.fragments import ExtensionFragment, Fragment, ReferenceResolver
    from fastraml.parser.includes import IncludeRef
    from fastraml.parser.structural_merge import ProvenanceOverlay
    from fastraml.positions import Position
    from fastraml.types.expressions import ExprCache
    from fastraml.types.jsonschema_ import SchemaRegistry
    from fastraml.yamlnode import Node

    # Written out so the field list below remains readable without runtime
    # imports from parser or type modules.
    BaseShape = Any
    EndPoint = Any
    SecurityScheme = Any
    SourceInfo = dict[int, tuple[Node | None, Node]]

#: The two facets whose *value* names a type. `scope_for` consults them before
#: the mapping that holds them, because a caller-substituted `type:` must beat
#: the scope of the grafted body it now sits inside.
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
    See docs/04-fragments-and-namespaces.md § 4.
    """

    anchor: ReferenceResolver | None = None
    #: Where an `(annotation)` written here is being applied. On the stack
    #: rather than passed to `unmarshal_domain_extension`, because the answer is
    #: not always known at the decode site: an annotation inside a trait body
    #: records the site it is *materialised* at, not `Trait`
    #: (docs/09-security-and-annotations.md § B4).
    target: DomainLocation = DomainLocation.API


_EMPTY_CTX: Final = ParseCtx()


class _TargetScope:
    """`Raml.target_scope`: push the current anchor at another target.

    A class rather than `@contextmanager`: a decoder enters one per construct,
    and a generator per use cost more than the push it guards (docs/12 § 2).
    The scope is read on entry, as the generator did.
    """

    __slots__ = ('_raml', '_target')

    def __init__(self, raml: Raml, target: DomainLocation) -> None:
        self._raml = raml
        self._target = target

    def __enter__(self) -> None:
        raml = self._raml
        raml._parse_ctx_stack.append(raml._scope(raml.current_ctx().anchor, self._target))  # noqa: SLF001

    def __exit__(self, *exc: object) -> None:
        self._raml._parse_ctx_stack.pop()  # noqa: SLF001


class _MarkedScope:
    """Push the scope `reader` finds for `node`, if it finds one."""

    __slots__ = ('_node', '_pushed', '_reader', '_stack')

    def __init__(self, stack: list[ParseCtx], reader: Callable[[Node], ParseCtx | None], node: Node) -> None:
        self._stack = stack
        self._reader = reader
        self._node = node
        self._pushed = False

    def __enter__(self) -> None:
        scope = self._reader(self._node)
        if scope is not None:
            self._stack.append(scope)
            self._pushed = True

    def __exit__(self, *exc: object) -> None:
        if self._pushed:
            self._stack.pop()


class Raml:
    """Everything produced by one parse.

    A `Raml` instance is single-threaded and its contents are mutable; the model
    it exposes may be cyclic. See docs/13-public-api.md § 3.
    """

    # Grouped by role, as in docs/02-architecture.md § 3.
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
        '_document_provenance',
        '_scopes',
        '_id_counter',
        '_parse_ctx_stack',
        'annotation_type_changes',
        'entry_point',
        'extensions',
        'source_info',
        'source_nodes',
        'source_texts',
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
        # nothing from `types/` at runtime (docs/02 § 2).
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
        # The overlay of the source unit being materialized in P4 stage 2;
        # `None` at every other time.
        self._active_overlay: ProvenanceOverlay | None = None
        # Which extension document authored a node of the target tree. Empty
        # unless the entry is an Overlay or Extension (docs/19 § 5.3).
        self._document_provenance: dict[Node, ReferenceResolver] = {}
        # One `ParseCtx` per (anchor, target) pair: a scope is a value, and the
        # decoders push the same few thousands of times.
        self._scopes: dict[tuple[ReferenceResolver | None, DomainLocation], ParseCtx] = {}
        self._id_counter = itertools.count(1)
        self.entry_point: Fragment | None = None
        #: The Overlays and Extensions applied to the root API, in application
        #: order; empty unless the entry is one (docs/19 § 6).
        self.extensions: list[ExtensionFragment] = []
        #: Root annotation types an extension document changed: name ->
        #: (document URI, position of the change) (docs/19 § 4.4).
        self.annotation_type_changes: dict[str, tuple[str, Position]] = {}
        self.unwrapped = False
        self.source_nodes: dict[str, Node] = {}
        self.source_texts: dict[str, str] = {}
        self.source_info: SourceInfo | None = {} if retain_source else None

    def __repr__(self) -> str:
        return f'Raml({self.location!r}, fragments={len(self.fragments)})'

    # -- identity -------------------------------------------------------------

    def next_id(self) -> int:
        """The next entity id. One counter per parse; see docs/02 § 3."""
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

    def target_scope(self, target: DomainLocation) -> _TargetScope:
        """Decode a construct that annotations attach to a different thing.

        The anchor is carried over unchanged — this narrows where an annotation
        is being applied, never which namespace a name resolves in. A context
        manager rather than a push/pop pair because a decoder that raises
        mid-construct must not leave the site behind on the stack.
        """
        return _TargetScope(self, target)

    def _scope(self, anchor: ReferenceResolver | None, target: DomainLocation) -> ParseCtx:
        """The one `ParseCtx` for this anchor and target."""
        key = (anchor, target)
        scope = self._scopes.get(key)
        if scope is None:
            scope = self._scopes[key] = ParseCtx(anchor=anchor, target=target)
        return scope

    # -- the provenance overlay (docs/08 § 4) ---------------------------------

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

    def provenance_scope(self, node: Node) -> _MarkedScope:
        """Push the scope recorded for `node`, if one is recorded.

        A node with no mark is one the enclosing unit wrote itself, and the
        scope already in effect is the right one for it.
        """
        return _MarkedScope(self._parse_ctx_stack, self._marked_scope, node)

    def scope_for(self, node: Node) -> ParseCtx | None:
        """The scope a type-bearing node should be decoded under, most specific first.

        The `type:`/`schema:` facet value of a mapping beats the mapping itself:
        a value the caller substituted is more specific than the grafted body it
        was substituted into.
        """
        if self._active_overlay is None and not self._document_provenance:
            return None
        if node.kind is NodeKind.MAPPING:
            content = node.content
            for index in range(0, len(content) - 1, 2):
                if content[index].value in _TYPE_FACETS:
                    scope = self._marked_scope(content[index + 1])
                    if scope is not None:
                        return scope
        return self._marked_scope(node)

    def location_of(self, node: Node, default: str) -> str:
        """The location to report for `node`: its recorded scope's anchor, else `default`.

        Consulted by every stage-2 entity constructor, so a diagnostic inside a
        trait-contributed response names the trait's file. The anchor is the
        namespace the node resolves in, which can differ from where it was
        authored (docs/08 § 4.2). Merge-created containers carry no mark, so
        callers ask about the specific child node.
        """
        documents = self._document_provenance
        if documents:
            anchor = documents.get(node)
            if anchor is not None:
                return anchor.location
        overlay = self._active_overlay
        if overlay is None:
            return default
        scope = overlay.get(node)
        if scope is not None and scope.anchor is not None:
            return scope.anchor.location
        return default

    def _marked_scope(self, node: Node) -> ParseCtx | None:
        """The document mark first, then the active unit's (docs/19 § 5.3)."""
        if self._document_provenance:
            scope = self.document_ctx(node)
            if scope is not None:
                return scope
        overlay = self._active_overlay
        return None if overlay is None else overlay.get(node)

    # -- document provenance (docs/19 § 5.3) ----------------------------------

    def mark_authored(self, node: Node, author: ReferenceResolver) -> None:
        """Record `node` and its whole subtree as written by `author`.

        Authorship is a fact about the node, so a mark is never replaced.
        """
        mark_subtree(self._document_provenance, node, author)

    def document_anchor(self, node: Node) -> ReferenceResolver | None:
        """The extension document that wrote `node`, if one did."""
        documents = self._document_provenance
        return documents.get(node) if documents else None

    def document_location(self, node: Node, default: str) -> str:
        """The authoring document's URI for `node`, else `default`.

        Unlike `location_of`, ignores template scopes: a substituted scalar
        keeps the template's positions, so only authorship names its file.
        """
        documents = self._document_provenance
        if documents:
            anchor = documents.get(node)
            if anchor is not None:
                return anchor.location
        return default

    def document_ctx(self, node: Node) -> ParseCtx | None:
        """The current scope re-anchored at `node`'s authoring document, if any.

        A document mark names a namespace only; the annotation target stays the
        one in effect (docs/19 § 5.3).
        """
        anchor = self.document_anchor(node)
        if anchor is None:
            return None
        return self._scope(anchor, self.current_ctx().target)

    def document_scope(self, node: Node) -> _MarkedScope:
        """Decode `node` in its authoring document's namespace, if it has one."""
        return _MarkedScope(self._parse_ctx_stack, self.document_ctx, node)

    @contextmanager
    def reporting_authorship(self) -> Iterator[None]:
        """Let `node_error` name a marked node's authoring document.

        Diagnostics are built at a hundred sites that know only the location
        they were handed; the lookup happens once a failure exists, so the
        success path pays nothing (docs/11 § 8).
        """
        token = AUTHORED_NODES.set(self._document_provenance)
        try:
            yield
        finally:
            AUTHORED_NODES.reset(token)

    def release_document_provenance(self) -> None:
        """Drop the marks once the passes have run (docs/19 § 5.3).

        Only the passes read them, and they reference every node an extension
        document wrote. Kept, they would hold those YAML trees for as long as
        the model lives, which P4 avoids for the API's own tree.
        """
        self._document_provenance.clear()

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
        """Index a fragment by the names it can resolve (docs/04 § 2)."""
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

    def store_source_text(self, uri: str, text: str) -> None:
        """Keep source text for comment-aware tooling when retention is on."""
        if self.retain_source:
            self.source_texts[uri] = text

    def put_source_info(self, entity_id: int, key: Node | None, value: Node) -> None:
        """Index an entity's authored nodes when source retention is on."""
        if self.source_info is not None:
            self.source_info[entity_id] = (key, value)

    # -- read surface (docs/13-public-api.md § 3) -----------------------------

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
