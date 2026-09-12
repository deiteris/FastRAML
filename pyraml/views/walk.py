"""One traversal of the effective document, addressing what it reaches.

RAML's syntax is a tree and its semantics are a graph, and the walk that turns
one into the other is not specific to any output. It is here, alone, because
more than one consumer needs it and because they must agree: a reference is
followable only when the emitter that wrote it and the emitter that reads it
address the entity identically.

**An address is not an identity** (`docs/16` § 3.1). `Addresses.of` is keyed on
the model's `id` and is many-to-one on purpose — a linked declaration and its
link target share one address, so a `securedBy:` bound to either finds the one
declaration. It is not a pure function of structure either: RAML does not
promise names are distinct, so `claim` disambiguates, and which of two
identically-named siblings keeps the bare address depends on visit order. That
is why assignment is one walk rather than a formula each emitter applies.

**Everything referenceable gets an address, and nothing else does.** A thing
needs one exactly when something can point at it; examples, defaults and
documentation prose are pointed at by nothing and are placed by containment
instead (`docs/16` § 4).

Nothing here decides a RAML rule. It runs after P10 and imports the model
rather than being imported by it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, Protocol
from urllib.parse import quote

from pyraml.parser.directives import SecurityScheme
from pyraml.parser.fragments import APIFragment, DataTypeFragment, Library
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from pyraml.types.jsonschema_ import projected
from pyraml.uris import relative_to

if TYPE_CHECKING:
    from pyraml.registry import Raml


def workspace_of(raml: Raml) -> str:
    """The directory every path a view prints is relative to, with a trailing `/`.

    **The workspace root, not the entry document's directory.** Those are the
    same until a project keeps its shared libraries beside its APIs rather than
    beneath one of them — and then the entry's directory is the wrong anchor
    twice over: a sibling library shares no prefix with it, so a path relative
    to it either ascends or, as it did, stays the whole
    `file:///C:/…/common/types.raml`. That reached the tree as a declaration's
    key and a consumer's URL.

    The root is also the boundary `SafeFileLoader` enforces, so every file a
    parse can read is at or beneath it: relative to the root, no path a view
    prints ever ascends. It falls back to the entry's directory, which is what
    the root defaults to when nobody passes `--workspace-root`.
    """
    root = raml.workspace_root_uri
    if root:
        return root if root.endswith('/') else root + '/'
    return raml.location.rsplit('/', 1)[0] + '/' if raml.location else ''


if TYPE_CHECKING:
    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.directives import DirectiveRef
    from pyraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
    from pyraml.parser.fragments import Fragment
    from pyraml.parser.resourcetypes import ResourceTypeDefinition
    from pyraml.parser.security import SecuritySchemeDefinition
    from pyraml.parser.traits import TraitDefinition
    from pyraml.registry import Raml
    from pyraml.types.base import BaseShape, Parameter, PatternProperty, Property, Shape

__all__ = [
    'DEFAULT_BASE',
    'Addresses',
    'Bucket',
    'Sink',
    'Walk',
    'address',
]

#: The default root of every address. `pyraml://id` mirrors AMF's `amf://id`,
#: for the same reason: an address is structural and document-local, and rooting
#: it at a real file URI would make every one of them machine-specific
#: (docs/16 § 3).
DEFAULT_BASE: Final = 'pyraml://id'

#: The infix every declaration's address carries, so a reader can tell a
#: declaration from a use site without consulting the model.
DECLARATIONS: Final = '#/declarations/'

#: The declaration maps a `type:`, `is:` or `securedBy:` can name.
Bucket = Literal['traits', 'resourceTypes', 'securitySchemes']

#: Nothing is safe in a path segment. A media type, a `/{userId}` template and a
#: `/^x-/` pattern-property name all have to survive as one segment, so the safe
#: set is empty rather than the `/` `urllib` leaves alone by default.
_SAFE: Final = ''


def _segment(value: str) -> str:
    """One address path segment. Everything unusual is percent-escaped."""
    return quote(value, safe=_SAFE)


class Sink(Protocol):
    """What a `Walk` reports, one method per role it can reach.

    One method per role rather than one taking a kind, so an emitter maps a role
    to its own vocabulary statically. A single `visit(entity, kind)` would put
    that mapping back at runtime, which is the divergence `nodes.py` exists to
    make impossible (`docs/16` § 2.7).

    Every method is given an address already assigned. A sink that wants only
    the addresses implements them all as `...` — see `_NullSink`.
    """

    def unit(self, iri: str, fragment: Fragment) -> None: ...
    def api(self, iri: str, fragment: APIFragment) -> None: ...
    def type_(self, iri: str, base: BaseShape, shape_kind: str) -> None: ...
    def property_(self, iri: str, prop: Property) -> None: ...
    def pattern_property(self, iri: str, prop: PatternProperty) -> None: ...
    def parameter(self, iri: str, param: Parameter) -> None: ...
    def payload(self, iri: str, body: Body) -> None: ...
    def request(self, iri: str, request: Request) -> None: ...
    def response(self, iri: str, response: Response) -> None: ...
    def operation(self, iri: str, operation: Operation) -> None: ...
    def endpoint(self, iri: str, endpoint: EndPoint) -> None: ...
    def trait(self, iri: str, definition: TraitDefinition) -> None: ...
    def resource_type(self, iri: str, definition: ResourceTypeDefinition) -> None: ...
    def security_scheme(self, iri: str, definition: SecuritySchemeDefinition) -> None: ...
    def unresolved(self, iri: str, bucket: Bucket, ref: DirectiveRef | SecurityScheme) -> None: ...
    def edge(self, subject: str, predicate: str, obj: str) -> None: ...


class Addresses:
    """Every entity a walk reached, at the address it was given.

    `of` is keyed on the model's own `id` and never on `id()`, which is neither
    stable nor unique once an object is freed (`docs/16` § 3.1). It is
    many-to-one: a linked declaration and its link target share one address, so
    a `securedBy:` bound to either finds the one declaration.
    """

    __slots__ = ('base', 'of')

    def __init__(self, base: str, of: dict[int, str]) -> None:
        self.base = base
        self.of = of

    def __repr__(self) -> str:
        return f'<Addresses base={self.base!r} entities={len(self.of)}>'


class _NullSink:
    """The sink that records nothing, for a walk wanted only for its addresses."""

    __slots__ = ()

    def unit(self, iri: str, fragment: Fragment) -> None: ...
    def api(self, iri: str, fragment: APIFragment) -> None: ...
    def type_(self, iri: str, base: BaseShape, shape_kind: str) -> None: ...
    def property_(self, iri: str, prop: Property) -> None: ...
    def pattern_property(self, iri: str, prop: PatternProperty) -> None: ...
    def parameter(self, iri: str, param: Parameter) -> None: ...
    def payload(self, iri: str, body: Body) -> None: ...
    def request(self, iri: str, request: Request) -> None: ...
    def response(self, iri: str, response: Response) -> None: ...
    def operation(self, iri: str, operation: Operation) -> None: ...
    def endpoint(self, iri: str, endpoint: EndPoint) -> None: ...
    def trait(self, iri: str, definition: TraitDefinition) -> None: ...
    def resource_type(self, iri: str, definition: ResourceTypeDefinition) -> None: ...
    def security_scheme(self, iri: str, definition: SecuritySchemeDefinition) -> None: ...
    def unresolved(self, iri: str, bucket: Bucket, ref: DirectiveRef | SecurityScheme) -> None: ...
    def edge(self, subject: str, predicate: str, obj: str) -> None: ...


def address(raml: Raml, *, base: str = DEFAULT_BASE) -> Addresses:
    """Assign an address to every referenceable entity in a parsed model.

    Use `ParseOptions(unwrap=True)`: addresses are of the effective document, so
    the map for a declared model and the map for an unwrapped one differ.
    """
    walk = Walk(raml, base, _NullSink())
    walk.run()
    return Addresses(base, walk.iris)


class Walk:
    """One traversal. Addresses are assigned on first encounter, so the visit
    order is part of the contract: declarations before endpoints, which is what
    makes `User` land at `#/declarations/types/User` and not at whichever
    response body happened to reach it first. go-raml's converter pre-registers
    for exactly this reason (`converter/jsonld.go`, `preRegisterTypes`).

    Because order decides assignment, two emitters that walked separately would
    address the same entity differently and their outputs could not be joined.
    They share this class instead, and differ only in the sink.
    """

    __slots__ = (
        '_segments',
        'base',
        'claimed',
        'emitted',
        'iris',
        'raml',
        'root',
        'sink',
    )

    def __init__(self, raml: Raml, base: str, sink: Sink) -> None:
        self.raml = raml
        self.base = base
        self.sink = sink
        #: Model entity id -> the IRI of the node projecting it. One map for
        #: every kind, because ids come from one counter per parse (docs/02
        #: § 3.1) and so are unique across kinds. Keyed on the model's own id,
        #: never `id()`, which is neither stable nor unique once freed.
        self.iris: dict[int, str] = {}
        #: IRI -> the entity id holding it. Two shapes given the same structural
        #: name would otherwise merge into one node in silence; see `claim`.
        self.claimed: dict[str, int] = {}
        #: Shapes already walked, so a type reached twice is projected once and
        #: a cycle terminates.
        self.emitted: set[int] = set()
        self._segments: dict[str, str] = {}
        self.root = workspace_of(raml)

    # -- infrastructure -------------------------------------------------------

    def unit(self, location: str) -> str:
        """The IRI prefix for declarations authored in `location`.

        Relative to the workspace root, so the graph does not carry the absolute
        path of the machine that produced it.
        """
        if location == self.raml.location or not location:
            return self.base
        return f'{self.base}/{self.segment(relative_to(location, self.root))}'

    def segment(self, value: str) -> str:
        escaped = self._segments.get(value)
        if escaped is None:
            escaped = _segment(value)
            self._segments[value] = escaped
        return escaped

    def declare(self, unit: str, bucket: str, name: str) -> str:
        """The IRI of one declaration in `unit`'s `types:`, `traits:` and so on."""
        return f'{unit}{DECLARATIONS}{bucket}/{self.segment(name)}'

    def claim(self, fallback: str, shape_id: int) -> str:
        """`fallback`, or the first free variation of it, claimed for `shape_id`.

        A structural IRI is derived from *names*, and RAML does not promise the
        names are distinct: `type1: [string, string]` gives two parents the same
        one. Without this the second silently merges into the first — the node
        count is plausible, no error is raised, and two types have become one.

        go-raml's converter has a test for exactly this hazard
        (`TestJSONLD_NoDuplicateIDs`, "a regression net for intermediate
        BaseShape objects that bypass shapeIDs registration and accidentally
        claim a contextID already in use"). It cost one corpus fixture to
        confirm the same hole was here.

        The suffix begins with `!`, which `_segment` always percent-escapes, so
        a disambiguated IRI can never collide with a name that produced one.
        """
        candidate, index = fallback, 1
        while self.claimed.setdefault(candidate, shape_id) != shape_id:
            index += 1
            candidate = f'{fallback}/!{index}'
        return candidate

    def assign(self, entity_id: int, iri: str) -> str:
        """Record `iri` as `entity_id`'s address and hand it back.

        First assignment wins, matching `shape`: an endpoint reached through the
        nested map and again through the flat one is one entity at one address.
        """
        return self.iris.setdefault(entity_id, iri)

    def edge(self, subject: str, predicate: str, obj: str) -> None:
        """Report a relationship, unless there is nothing to point at.

        An empty object means the target does not exist — a body with no schema,
        say — and an edge to it would dangle.
        """
        if obj:
            self.sink.edge(subject, predicate, obj)

    # -- driver ---------------------------------------------------------------

    def run(self) -> None:
        for location, declared in self.raml.fragment_types.items():
            for name in declared:
                self.reserve(declared[name], self.declare(self.unit(location), 'types', name))
        for location, declared in self.raml.fragment_annotations.items():
            for name in declared:
                self.reserve(declared[name], self.declare(self.unit(location), 'annotations', name))

        for location, fragment in self.raml.fragments.items():
            self.fragment(location, fragment)
        self.api()

    def reserve(self, shape: BaseShape | None, iri: str) -> None:
        if shape is not None and shape.id not in self.iris:
            self.iris[shape.id] = self.claim(iri, shape.id)

    def declared(self, shape: BaseShape, iri: str) -> None:
        """Record that `shape` is declared, by the file it was written in.

        Not by the map that named it. `types: {X: !include x.raml}` names `X`
        here and writes it there, and `definedIn` on the node already says
        `x.raml` — a `declares` edge from this document would contradict it.
        The file's node is made on demand, so a document holds one only if it
        holds a declaration.
        """
        holder = self.raml.fragments.get(shape.location)
        if holder is None:
            return
        unit = self.unit(shape.location)
        self.sink.unit(unit, holder)
        self.edge(unit, 'declares', iri)

    def fragment(self, location: str, fragment: Fragment) -> None:
        """Every declaration a fragment holds, in declaration order.

        A `Fragment` is a protocol with no `types`, so the `isinstance` calls
        are what narrow it to the kinds that declare something: the two with
        declaration maps, and the typed fragment that is itself one declaration.
        """
        unit = self.unit(location)
        if isinstance(fragment, DataTypeFragment):
            # The whole document is one declaration, so the file declares it.
            # It is normally reached first as a parent of the `types:` entry
            # that included it and already holds an IRI; the fallback names it
            # after the file, which is what a type with no name of its own is
            # called everywhere else.
            if fragment.shape is not None:
                named = fragment.shape.name or location.rsplit('/', 1)[-1]
                self.declared(fragment.shape, self.shape(fragment.shape, self.declare(unit, 'types', named)))
            return
        if not isinstance(fragment, (APIFragment, Library)):
            return
        self.sink.unit(unit, fragment)
        for name, shape in fragment.types.items():
            self.declared(shape, self.shape(shape, self.declare(unit, 'types', name)))
        for name, shape in fragment.annotation_types.items():
            self.declared(shape, self.shape(shape, self.declare(unit, 'annotations', name)))
        for name, scheme in fragment.security_schemes.items():
            iri = self.declare(unit, 'securitySchemes', name)
            self.sink.security_scheme(iri, scheme)
            # Under both the declaration and whatever an `!include` resolved to,
            # so a `securedBy:` bound to either finds this one node.
            self.iris[scheme.id] = iri
            self.iris[scheme.resolved().id] = iri
            self.edge(unit, 'declares', iri)
        for name, trait in fragment.traits.items():
            iri = self.declare(unit, 'traits', name)
            self.sink.trait(iri, trait)
            self.iris[trait.id] = iri
            self.iris[trait.resolved().id] = iri
            self.edge(unit, 'declares', iri)
        for name, resource_type in fragment.resource_types.items():
            iri = self.declare(unit, 'resourceTypes', name)
            self.sink.resource_type(iri, resource_type)
            self.iris[resource_type.id] = iri
            self.iris[resource_type.resolved().id] = iri
            self.edge(unit, 'declares', iri)

    def api(self) -> None:
        entry = self.raml.entry_point
        if not isinstance(entry, APIFragment):
            return
        api = f'{self.base}#/web-api'
        self.sink.api(api, entry)
        self.edge(api, 'unit', self.unit(entry.location))
        seen: set[int] = set()
        for endpoint in self.raml.endpoints.values():
            self.endpoint(api, endpoint, seen)

    # -- endpoints ------------------------------------------------------------

    def endpoint(self, api: str, endpoint: EndPoint, seen: set[int]) -> None:
        """One resource. Walked through the nested map as well as the flat one,
        so the projection does not depend on which of the two `Raml.endpoints`
        turns out to be.
        """
        if endpoint.id in seen:
            return
        seen.add(endpoint.id)
        iri = self.assign(endpoint.id, f'{self.base}#/web-api/endpoint/{self.segment(endpoint.full_uri)}')
        self.sink.endpoint(iri, endpoint)
        self.edge(api, 'endpoint', iri)

        parent = endpoint.full_uri[: -len(endpoint.uri)] if endpoint.uri and endpoint.full_uri != endpoint.uri else ''
        if parent:
            self.edge(iri, 'parent', f'{self.base}#/web-api/endpoint/{self.segment(parent)}')
        if endpoint.resource_type is not None:
            self.applies(iri, 'appliesResourceType', 'resourceTypes', endpoint.resource_type, endpoint.location)
        for trait in endpoint.traits:
            self.applies(iri, 'appliesTrait', 'traits', trait, endpoint.location)
        for name, param in endpoint.uri_parameters.items():
            self.edge(iri, 'parameter', self.parameter(f'{iri}/parameter/path/{self.segment(name)}', param))
        self.secured(iri, endpoint.secured_by)
        self.annotated(iri, endpoint.annotations)

        for operation in endpoint.operations.values():
            self.operation(iri, operation)
        for child in endpoint.endpoints.values():
            self.endpoint(api, child, seen)

    def applies(
        self, subject: str, predicate: str, bucket: Bucket, ref: DirectiveRef | SecurityScheme, location: str
    ) -> None:
        """A `type:`, `is:` or `securedBy:` reference, pointed at what it named.

        The declaration comes off the reference, which the pass that resolved it
        recorded. Matching the name again would re-run P4's work and get a
        different answer: `a.paged` and `b.paged` are one name in two libraries,
        and a lookup cannot tell them apart — it returns whichever was declared
        first, so `refs a.paged` reports a use that is not there.

        A reference that resolved to nothing still gets an edge, to a node
        holding the name, so an application is never invisible and no edge
        dangles.
        """
        if not ref.name:
            return
        definition = ref.definition if isinstance(ref, SecurityScheme) else ref.resolved
        target = self.iris.get(definition.id) if definition is not None else None
        if target is not None:
            self.edge(subject, predicate, target)
            return
        local = f'{self.unit(location)}#/declarations/{bucket}/{self.segment(ref.name)}'
        self.sink.unresolved(local, bucket, ref)
        self.edge(subject, predicate, local)

    def operation(self, endpoint: str, operation: Operation) -> None:
        iri = self.assign(operation.id, f'{endpoint}/supportedOperation/{self.segment(operation.method)}')
        self.sink.operation(iri, operation)
        self.edge(endpoint, 'supportedOperation', iri)
        for trait in operation.traits:
            self.applies(iri, 'appliesTrait', 'traits', trait, operation.location)
        self.secured(iri, operation.secured_by)
        self.annotated(iri, operation.annotations)
        if operation.request is not None:
            self.request(iri, operation.request)
        for response in operation.responses.values():
            self.response(iri, response)

    def request(self, operation: str, request: Request) -> None:
        if not (request.headers or request.query_parameters or request.bodies or request.query_string):
            # A method always has a `Request` object; most methods send nothing.
            # An empty node here would be one per GET in the graph, all identical
            # and all noise.
            return
        iri = self.assign(request.id, f'{operation}/request')
        self.sink.request(iri, request)
        self.edge(operation, 'request', iri)
        for name, param in request.headers.items():
            self.edge(iri, 'parameter', self.parameter(f'{iri}/parameter/header/{self.segment(name)}', param))
        for name, param in request.query_parameters.items():
            child = self.parameter(f'{iri}/parameter/query/{self.segment(name)}', param)
            self.edge(iri, 'parameter', child)
        if request.query_string is not None:
            self.edge(iri, 'queryString', self.shape(request.query_string, f'{iri}/queryString'))
        for media, body in request.bodies.items():
            self.edge(iri, 'payload', self.payload(iri, media, body))

    def response(self, operation: str, response: Response) -> None:
        iri = self.assign(response.id, f'{operation}/returns/{self.segment(response.code)}')
        self.sink.response(iri, response)
        self.edge(operation, 'returns', iri)
        self.annotated(iri, response.annotations)
        for name, param in response.headers.items():
            child = self.parameter(f'{iri}/parameter/header/{self.segment(name)}', param)
            self.edge(iri, 'parameter', child)
        for media, body in response.bodies.items():
            self.edge(iri, 'payload', self.payload(iri, media, body))

    def payload(self, parent: str, media: str, body: Body) -> str:
        iri = self.assign(body.id, f'{parent}/payload/{self.segment(media or "default")}')
        self.sink.payload(iri, body)
        if body.shape is not None:
            self.edge(iri, 'range', self.shape(body.shape, f'{iri}/schema'))
        return iri

    def parameter(self, iri: str, param: Parameter) -> str:
        """A URI, query or header parameter.

        A node of its own rather than an edge straight to the type, because
        `required` and the binding belong to the *use* and not to the type: the
        same declared type is a required path parameter here and an optional
        header there. The model says so too — `Parameter` holds the property
        and adds the binding, so nothing here has to be told which it is.
        """
        self.assign(param.id, iri)
        self.sink.parameter(iri, param)
        self.edge(iri, 'range', self.shape(param.base, f'{iri}/schema'))
        return iri

    def secured(self, subject: str, schemes: list[SecurityScheme]) -> None:
        """One `securedBy` edge per scheme in force.

        `securedBy: [null]` removes inherited security rather than naming a
        scheme, so it produces no edge. The subject records it — as `unsecured`,
        alongside the scopes the schemes narrowed to — and both are read off
        `Operation.secured_by` when the attributes are asked for.
        """
        for scheme in schemes:
            if scheme.is_null:
                continue
            # P5 already bound this reference to its declaration, so the IRI is
            # computed from the definition rather than matched by name. Matching
            # re-derived work the model had done, and did it worse: two libraries
            # declaring one scheme name are indistinguishable to a name lookup
            # and are two different objects here.
            target = self.iris.get(scheme.definition.id) if scheme.definition is not None else None
            if target is not None:
                self.edge(subject, 'securedBy', target)
            else:
                self.applies(subject, 'securedBy', 'securitySchemes', scheme, self.raml.location)

    def annotated(self, subject: str, annotations: dict[str, DomainExtension]) -> None:
        """One edge per annotation, to the annotation *type* P8 bound it to."""
        for name, extension in annotations.items():
            defined_by = extension.defined_by
            target = self.iris.get(defined_by.id) if defined_by is not None else None
            if target is None:
                # Nothing was bound, so there is no declaration to point at and
                # nothing this layer could resolve that P8 could not.
                target = f'{self.base}#/declarations/annotations/{self.segment(name.rsplit(".", 1)[-1])}'
            self.edge(subject, 'annotation', target)

    # -- shapes ---------------------------------------------------------------

    def shape(self, base: BaseShape | None, fallback: str) -> str:
        """One type, at its reserved IRI if it has one and at `fallback` if not.

        Reached twice, a shape keeps its first IRI: the second visit returns it
        and emits nothing, which is what closes a type cycle and what makes a
        declared type one node rather than one per use site.
        """
        if base is None:
            return ''
        iri = self.iris.get(base.id)
        if iri is None:
            iri = self.iris[base.id] = self.claim(fallback, base.id)
        if base.id in self.emitted:
            return iri
        self.emitted.add(base.id)

        kind = type(base.shape).__name__ if base.shape is not None else 'UnknownShape'
        self.sink.type_(iri, base, kind)
        # Structure and facets come from the *projected* shape, so a type defined
        # by a JSON schema has members here rather than being a leaf. Without it
        # `deps errorScheme` reported that it is made of nothing, every SPARQL
        # query walking `raml:property` skipped those types, and `diff` — which
        # compares nodes, attributes and reference edges — saw no change when a
        # whole schema was replaced (docs/16 § 2.6).
        view = projected(base)

        for parent in base.inherits:
            self.edge(iri, 'inherits', self.shape(parent, f'{iri}/inherits/{self.segment(parent.name or "anonymous")}'))
        if base.alias is not None:
            self.edge(iri, 'aliasOf', self.shape(base.alias, f'{iri}/aliasOf'))
        for name, extension in base.annotations.items():
            self.annotated(iri, {name: extension})
        self.children(iri, view.shape)
        return iri

    def children(self, iri: str, shape: Shape | None) -> None:
        """The declarations a kind contains. One branch per container facet."""
        if isinstance(shape, ObjectShape):
            for name, prop in (shape.properties or {}).items():
                child = f'{iri}/property/{self.segment(name)}'
                self.sink.property_(child, prop)
                self.edge(child, 'range', self.shape(prop.base, f'{child}/schema'))
                self.edge(iri, 'property', child)
            for name, pattern in (shape.pattern_properties or {}).items():
                # Keyed by the `/regex/` as written, not by position: a pattern
                # property has no name of its own and an index would move under
                # any edit above it.
                child = f'{iri}/patternProperty/{self.segment(name)}'
                self.sink.pattern_property(child, pattern)
                self.edge(child, 'range', self.shape(pattern.base, f'{child}/schema'))
                self.edge(iri, 'patternProperty', child)
        elif isinstance(shape, ArrayShape):
            if shape.items is not None:
                self.edge(iri, 'items', self.shape(shape.items, f'{iri}/items'))
        elif isinstance(shape, UnionShape):
            for index, member in enumerate(shape.any_of or ()):
                self.edge(iri, 'anyOf', self.shape(member, f'{iri}/anyOf/{index}'))
        elif isinstance(shape, RecursiveShape):
            # The head is always an ancestor of this marker, so it already holds
            # an IRI: `shape` assigns before it recurses. Following the back-edge
            # would unroll the cycle the marker exists to close.
            head = self.iris.get(shape.head.id)
            if head is not None:
                self.edge(iri, 'recursionHead', head)
