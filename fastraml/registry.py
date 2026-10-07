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
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, cast

from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, RamlError
from fastraml.loaders import SchemeLoader
from fastraml.yamlnode import AUTHORED_NODES, DEFAULT_MAX_DEPTH, mark_subtree

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from fastraml.datanode import DataNode
    from fastraml.loaders import ResourceLoader
    from fastraml.parser.annotations import AnnotationSites, DomainExtension
    from fastraml.parser.fragments import ExtensionFragment, Fragment, ReferenceResolver
    from fastraml.parser.includes import IncludeRef
    from fastraml.parser.structural_merge import ProvenanceOverlay
    from fastraml.parser.substitutions import Substitutions
    from fastraml.positions import Position
    from fastraml.types.base import Property
    from fastraml.types.expressions import ExprCache
    from fastraml.types.schema_compile import SchemaRegistry
    from fastraml.yamlnode import Node

    # Written out so the field list below remains readable without runtime
    # imports from parser or type modules.
    BaseShape = Any
    EndPoint = Any
    SecurityScheme = Any
    SourceInfo = dict[int, tuple[Node | None, Node]]

__all__ = [
    'DEFAULT_MAX_INCLUDE_SIZE',
    'EMPTY_CTX',
    'ParseCtx',
    'Raml',
    'Stage',
]

#: Per-file ceiling for an `!include` target, in bytes. `0` disables the limit.
DEFAULT_MAX_INCLUDE_SIZE: Final = 65536


class Stage(Enum):
    """One step of the pass driver, in the order it runs them (docs/02 § 1).

    Named for what a finished step settles rather than numbered: P6 runs with
    P4, before P5, and the discriminator declaration check runs with P7. Two
    steps are optional, so a consumer asks whether a stage is in
    `Raml.completed`, not whether a later one is.
    """

    DECODED = 'decoded'  # P0-P3: fragments, declarations and `uses:`
    ENDPOINTS = 'endpoints'  # P4 and P6
    SECURITY = 'security'  # P5
    RESOLVED = 'resolved'  # P7, and the discriminator declaration check
    ANNOTATIONS = 'annotations'  # P8
    UNWRAPPED = 'unwrapped'  # P9, when requested
    VALIDATED = 'validated'  # P10, when requested


class Identified(Protocol):
    """Anything `Raml.broken` can mark: every model entity has an id."""

    @property
    def id(self) -> int: ...


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
    #: not always known at the decode site: a nested annotation inside a trait
    #: body records its materialized declaration's site, while a root annotation
    #: keeps `Trait`
    #: (docs/09-security-and-annotations.md § B4).
    target: DomainLocation = DomainLocation.API


#: The scope outside any fragment decode, and a template's caller scope when
#: the caller has none: one instance, not one per use (docs/12 § 2).
EMPTY_CTX: Final = ParseCtx()


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
        raml._parse_ctx_stack.append(raml.scope(raml.current_ctx().anchor, self._target))  # noqa: SLF001

    def __exit__(self, *exc: object) -> None:
        self._raml._parse_ctx_stack.pop()  # noqa: SLF001


class _Marking:
    """`Raml.marking`. A class rather than `@contextmanager`, as for
    `_TargetScope`: a decoder enters one per response, operation and resource.
    """

    __slots__ = ('_entity', '_raml')

    def __init__(self, raml: Raml, entity: Identified) -> None:
        self._raml = raml
        self._entity = entity

    def __enter__(self) -> None:
        pass

    def __exit__(self, kind: object, error: BaseException | None, traceback: object) -> None:
        if isinstance(error, RamlError):
            self._raml.mark(self._entity, error)


class _MarkedScope:
    """`Raml.provenance_scope`: push the scope recorded for a node, if any."""

    __slots__ = ('_node', '_pushed', '_raml')

    def __init__(self, raml: Raml, node: Node) -> None:
        self._raml = raml
        self._node = node
        self._pushed = False

    def __enter__(self) -> None:
        raml = self._raml
        scope = raml._marked_scope(self._node)  # noqa: SLF001
        if scope is not None:
            raml._parse_ctx_stack.append(scope)  # noqa: SLF001
            self._pushed = True

    def __exit__(self, *exc: object) -> None:
        if self._pushed:
            self._raml._parse_ctx_stack.pop()  # noqa: SLF001


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
        'retain_text',
        'workspace_root_uri',
        # --- caches ----------------------------------------------------------
        'caching_includes',
        'expr_cache',
        'fragments',
        'include_data',
        'include_heads',
        'include_nodes',
        'json_schema_registry',
        'mapping_keys',
        # --- indices ---------------------------------------------------------
        'custom_facet_refs',
        'domain_extensions',
        'endpoints',
        'fragment_annotations',
        'fragment_resolvers',
        'fragment_typedefs',
        'fragment_types',
        'include_refs',
        'shapes',
        'substitutions',
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
        'annotation_sites',
        'annotation_type_changes',
        'broken',
        'completed',
        'entry_point',
        'extensions',
        'source_info',
        'source_nodes',
        'source_texts',
        'stopped_at',
    )

    def __init__(  # noqa: PLR0913 - the parse's configuration, keyword-only, one field each
        self,
        *,
        loader: ResourceLoader | None = None,
        workspace_root_uri: str = '',
        max_include_size: int = DEFAULT_MAX_INCLUDE_SIZE,
        max_depth: int = DEFAULT_MAX_DEPTH,
        retain_source: bool = False,
        retain_text: bool = False,
        regex_engine: Literal['re', 're2'] = 're',
    ) -> None:
        # An empty SchemeLoader rather than None: a registry built without one
        # reports "no loader for URI scheme" instead of an AttributeError.
        self.loader: ResourceLoader = loader if loader is not None else SchemeLoader({})
        self.workspace_root_uri = workspace_root_uri
        self.max_include_size = max_include_size
        self.max_depth = max_depth
        self.retain_source = retain_source
        self.retain_text = retain_text or retain_source
        self.regex_engine = regex_engine

        self.fragments: dict[str, Fragment] = {}
        #: Each composed include target. Emptied when the parse ends, unless
        #: `retain_source` keeps it, and then `caching_includes` is false so a
        #: later read does not fill it again (docs/03 § 4.3).
        self.include_nodes: dict[str, Node] = {}
        self.caching_includes = True
        # Equal YAML mapping keys share a string across this parse's files,
        # without depending on Python's process-global intern table.
        self.mapping_keys: dict[str, str] = {}
        # The `#%RAML` first line of an included file that has one, by target:
        # a comment to YAML, so the composed node no longer carries it.
        self.include_heads: dict[str, str] = {}
        # A file `content_include` read to see its first line, held for whichever
        # reader comes next, so deciding content from fragment costs no second read.
        self.include_data: dict[str, bytes] = {}
        # One parse per distinct expression text, not per occurrence. Held here
        # rather than on the expression parser so it dies with the parse.
        self.expr_cache: ExprCache = {}
        # Built on first use by `types/schema_compile.py`: this module imports
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
        #: P7's supplied-value bindings, refreshed by public P9, never by P10.
        self.custom_facet_refs: dict[DataNode, list[Property]] = {}
        self.domain_extensions: list[DomainExtension] = []
        self.include_refs: dict[str, list[IncludeRef]] = {}
        #: Each scalar a template substitution produced, and the caller's
        #: values in it: where a name in it was written (docs/08 § 5.1).
        self.substitutions: Substitutions = {}

        # A worklist, drained from the left in P7 while resolution appends to
        # the right; a deque keeps both ends O(1).
        self.unresolved_shapes: deque[BaseShape] = deque()

        #: The API's effective protocols, upper-cased: its `protocols:`, else
        #: its baseUri's literal HTTP or HTTPS scheme, else none (docs/08 § 6.1).
        #: `APIFragment.protocols` and `Operation.protocols` stay as authored.
        self.global_protocols: list[str] = []
        self.global_media_types: list[str] = []
        self.global_secured_by: list[SecurityScheme] = []

        self._parse_ctx_stack: list[ParseCtx] = []
        # Created by the annotation decoder only for retained application sites;
        # P4 releases it after materialization (docs/09 § B4).
        self.annotation_sites: AnnotationSites | None = None
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
        #: The stages that finished, in order, and the one that raised: how far
        #: a model `parse_lenient` returned got (docs/13 § 1).
        self.completed: list[Stage] = []
        self.stopped_at: Stage | None = None
        #: Entity id -> why it is incomplete. The entity is in the model and
        #: its identity (name, positions) is sound; its content is partial
        #: (docs/13 § 1).
        self.broken: dict[int, RamlError] = {}
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
            return EMPTY_CTX
        return self._parse_ctx_stack[-1]

    def target_scope(self, target: DomainLocation) -> _TargetScope:
        """Decode a construct that annotations attach to a different thing.

        The anchor is carried over unchanged — this narrows where an annotation
        is being applied, never which namespace a name resolves in. A context
        manager rather than a push/pop pair because a decoder that raises
        mid-construct must not leave the site behind on the stack.
        """
        return _TargetScope(self, target)

    @contextmanager
    def stage(self, stage: Stage) -> Iterator[None]:
        """Run one step of the pass driver, recording whether it finished."""
        try:
            yield
        except BaseException:
            self.stopped_at = stage
            raise
        self.completed.append(stage)

    def mark(self, entity: Identified, error: RamlError) -> None:
        """Record that `entity` is in the model but incomplete (docs/13 § 1).

        A second failure of the same entity joins the first: a template that
        failed to apply, then the merged content, are both on the mark. The
        same failure met again, by a second referrer or an inherited copy, is
        kept once, as `Accumulator` keeps it (docs/11 § 2).
        """
        earlier = self.broken.get(entity.id)
        if earlier is None:
            self.broken[entity.id] = error
            return
        both = Accumulator()
        both.add(earlier)
        both.add(error)
        self.broken[entity.id] = cast('RamlError', both.result())

    def marking(self, entity: Identified) -> _Marking:
        """Decode `entity`'s content, marking it if that fails."""
        return _Marking(self, entity)

    def scope(self, anchor: ReferenceResolver | None, target: DomainLocation) -> ParseCtx:
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
        return _MarkedScope(self, node)

    @property
    def has_provenance(self) -> bool:
        """Whether a decoder has node-specific scopes to consult."""
        return bool(self._active_overlay or self._document_provenance)

    def scope_for(self, node: Node) -> ParseCtx | None:
        """The node's recorded namespace at the current annotation target."""
        return self._marked_scope(node)

    def reference_scope(self, node: Node, default: ParseCtx) -> ParseCtx:
        """Select a name's own provenance, falling back only to its enclosing scope.

        The caller supplies the name-bearing node: a directive or annotation
        name may be its key, independently of its arguments.
        """
        return self._marked_scope(node) or default

    def location_of(self, node: Node, default: str) -> str:
        """The location to report for `node`: its recorded scope's anchor, else `default`.

        Consulted by every stage-2 entity constructor, so a diagnostic inside a
        trait-contributed response names the trait's file. The anchor is the
        namespace the node resolves in, which can differ from where it was
        authored (docs/08 § 4.2). Merge-created containers carry no mark, so
        callers ask about the specific child node.
        """
        if self._document_provenance:  # checked here: every stage-2 constructor asks
            anchor = self.document_anchor(node)
            if anchor is not None:
                return anchor.location
        overlay = self._active_overlay
        scope = None if overlay is None else overlay.get(node)
        if scope is not None and scope.anchor is not None:
            return scope.anchor.location
        return default

    def _marked_scope(self, node: Node) -> ParseCtx | None:
        """The document mark first, then the active unit's (docs/19 § 5.3)."""
        if self._document_provenance:  # checked here: stage 2 asks for every value it decodes
            authored = self.document_ctx(node)
            if authored is not None:
                return authored
        overlay = self._active_overlay
        scope = None if overlay is None else overlay.get(node)
        if scope is not None and scope.target is not self.current_ctx().target:
            # Provenance selects a namespace; the materializing decoder selects
            # the annotation site (docs/09 § B4).
            return self.scope(scope.anchor, self.current_ctx().target)
        return scope

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
        anchor = self.document_anchor(node)
        return default if anchor is None else anchor.location

    def document_ctx(self, node: Node) -> ParseCtx | None:
        """The current scope re-anchored at `node`'s authoring document, if any.

        A document mark names a namespace only; the annotation target stays the
        one in effect (docs/19 § 5.3).
        """
        anchor = self.document_anchor(node)
        if anchor is None:
            return None
        return self.scope(anchor, self.current_ctx().target)

    def document_site[S: ParseCtx | None](self, node: Node, location: str, scope: S) -> tuple[str, S | ParseCtx]:
        """The location and scope to decode `node` under.

        Its authoring document's, if an extension document wrote it, else the
        ones given: `is: [paged]` added to a method the root API declares
        names `paged` in the extension document's namespace (docs/19 § 5.3).
        """
        anchor = self.document_anchor(node)
        if anchor is None:
            return location, scope
        return anchor.location, self.scope(anchor, self.current_ctx().target)

    @contextmanager
    def authorship(self) -> Iterator[None]:
        """Run the passes with the document marks in force, then drop them.

        Inside, `node_error` names a marked node's authoring document.
        Diagnostics are built at a hundred sites that know only the location
        they were handed; the lookup happens once a failure exists, so the
        success path pays nothing (docs/11 § 8).

        On the way out, success or failure, the marks are cleared. Only the
        passes read them, and they reference every node an extension document
        wrote: kept, they would hold those YAML trees for as long as the model
        lives, which P4 avoids for the API's own tree (docs/19 § 5.3).
        """
        token = AUTHORED_NODES.set(self._document_provenance)
        try:
            yield
        finally:
            AUTHORED_NODES.reset(token)
            self._document_provenance.clear()

    def load_bounded(self, uri: str) -> tuple[bytes, bool]:
        """`uri`'s bytes under `max_include_size`, and whether it exceeds it.

        The loader is asked for at most one byte past the limit, so an oversized
        file is detected without ever being read whole; its bytes are then a
        truncated prefix, which the caller must not use. A non-positive limit
        reads everything. `OSError` propagates for the caller to report.
        """
        limit = self.max_include_size
        data = self.loader.load(uri, max_bytes=limit if limit > 0 else None)
        return data, 0 < limit < len(data)

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
        """Keep source text when `retain_text` or `retain_source` is on."""
        if self.retain_text:
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
    def unwrapped(self) -> bool:
        """Whether P9 finished: `Stage.UNWRAPPED in completed` (docs/02 § 1)."""
        return Stage.UNWRAPPED in self.completed

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
