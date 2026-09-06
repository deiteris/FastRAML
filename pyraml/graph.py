"""The effective model as a labelled directed graph — docs/16-graph.md.

RAML's syntax is a tree and its semantics are a graph: types inherit from types,
properties reference types, resources instantiate resource types, methods apply
traits. Following those edges by hand across a document set is the thing that is
tedious about RAML, and it is tedious in proportion to how much resolution the
reader has to perform mentally.

This module does none of that resolution. It runs **after** the parser has,
projecting the already-unwrapped model — one node per entity, one edge per
semantic relationship — so that a question like *which operations can return a
`User`* is a traversal rather than a visitor.

**The graph is of the effective model, not the declared one.** Call it on a parse
made with `ParseOptions(unwrap=True)`; that is what makes a resource type's
`<<itemType>>[]` show up as an array of the type the caller actually bound. The
declared form is still visible where the model kept it — `raml:inherits` survives
unwrap and is what links a use site back to the declaration it names.

Nothing here belongs to the parser: no rule is decided in this module, and it
imports the model rather than being imported by it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal
from urllib.parse import quote

from pyraml.nodes import (
    ApiNode,
    EndPointNode,
    Entity,
    GraphNode,
    OperationNode,
    ParameterNode,
    PatternPropertyNode,
    PayloadNode,
    PropertyNode,
    RequestNode,
    ResourceTypeNode,
    ResponseNode,
    SecuritySchemeNode,
    TraitNode,
    TypeNode,
    UnitNode,
    UnresolvedNode,
    UnresolvedResourceTypeNode,
    UnresolvedSchemeNode,
    UnresolvedTraitNode,
)
from pyraml.parser.directives import SecurityScheme
from pyraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
from pyraml.parser.fragments import APIFragment, DataTypeFragment, Fragment, Library
from pyraml.types.base import BaseShape, Parameter
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from pyraml.types.jsonschema_ import projected

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.directives import DirectiveRef
    from pyraml.registry import Raml
    from pyraml.types.base import Shape

__all__ = [
    'DEFAULT_BASE',
    'RAML_NS',
    'TYPE_EDGES',
    'USE_EDGES',
    'Edge',
    'Graph',
    'Route',
    'build_graph',
]

#: The vocabulary namespace. A URN rather than an `http(s)` IRI on purpose: this
#: project claims no domain name, and a prefix that 404s is worse than one that
#: never promised to resolve. Provisional until 1.0 — see docs/16 § 2.
RAML_NS: Final = 'urn:pyraml:ns:raml#'

#: The default root of every node IRI. `pyraml://id` mirrors AMF's `amf://id`,
#: for the same reason: node identity is structural and document-local, and
#: rooting it at a real file URI would make every IRI machine-specific
#: (docs/16 § 3).
DEFAULT_BASE: Final = 'pyraml://id'

#: The edges to follow when asking what a type is *made of*. This is the closure
#: `deps` uses, and the one a SPARQL property path would spell as an alternation
#: — kept here so the two cannot drift.
TYPE_EDGES: Final = (
    'range',
    'items',
    'anyOf',
    'inherits',
    'property',
    'patternProperty',
    # Both look optional and are not. `User[]` does not put the *declaration* of
    # `User` under `items`: it puts an alias of it there (docs/07 § 3.6), so a
    # closure without `aliasOf` stops one hop short of every array member type
    # and reports the member's supertypes instead of the member. `recursionHead`
    # is the same argument for a cyclic type — the walk's own `seen` set is what
    # stops it, not the absence of the edge.
    'aliasOf',
    'recursionHead',
)

#: `TYPE_EDGES` plus containment and application, which is what a *use* question
#: needs: walked in reverse from a type it arrives at the operations and
#: resources that can carry it, which is the query the whole projection exists
#: for. The application edges are here for the same reason — reversed from a
#: trait, a security scheme or an annotation type, they name every site that
#: uses it, and a `refs` that could not answer that would be half a tool.
USE_EDGES: Final = (
    *TYPE_EDGES,
    'queryString',
    'parameter',
    'payload',
    'request',
    'returns',
    'supportedOperation',
    'endpoint',
    'securedBy',
    'appliesTrait',
    'appliesResourceType',
    'annotation',
)

_XSD: Final = 'http://www.w3.org/2001/XMLSchema#'

#: Opens the tail of every declaration IRI (§ 3).
_DECLARATIONS: Final = '#/declarations/'


def _is_declaration(iri: str) -> bool:
    """Whether `iri` names a declaration rather than a node inside one.

    Told apart by depth. A declaration's tail is `<bucket>/<name>` and nothing
    more — `types/User` — while a node beneath it keeps going:
    `types/Admin/inherits/User`. A name cannot contribute a `/` of its own,
    because every segment is percent-escaped.
    """
    _, marker, tail = iri.partition(_DECLARATIONS)
    return bool(marker) and tail.count('/') == 1


def _outermost(matched: Sequence[str]) -> list[str]:
    """Drop any IRI that sits inside another one in the same set.

    A node passes its name down to the nodes it contains, so a query parameter
    `login` matches both `…/parameter/query/login` and the schema beneath it,
    `…/parameter/query/login/schema`. Those are one entity at two depths, and
    reporting them as an ambiguity asks the caller to choose between a thing and
    part of itself.

    Distinct from the declaration rule above and not a replacement for it: a
    synthetic parent at `…/types/Admin/inherits/Entity` is not *inside*
    `…/types/Entity`, so containment cannot settle that pair and only the
    declaration rule can. This one settles what that rule leaves.

    Two declarations of one name are unaffected — neither contains the other,
    and that ambiguity is real.

    Walks each IRI's own ancestors against a set rather than comparing every
    pair. The pairwise form is quadratic *and* builds a string per comparison,
    which a common property name reaches the cliff of immediately: `id` matches
    4800 nodes on the benchmark corpus, and the walk answers in 3 ms where the
    scan took two seconds.
    """
    have = set(matched)
    outermost = []
    for iri in matched:
        head = iri
        while True:
            head, separator, _ = head.rpartition('/')
            if not separator:
                outermost.append(iri)
                break
            if head in have:
                break
    return outermost


#: The `#/declarations/<bucket>/` segments holding something a `type:`, `is:` or
#: `securedBy:` entry can name, and the node that stands in where one of those
#: names resolved to nothing. A `Literal` rather than `str` so the lookup is
#: total and mypy says so.
_Bucket = Literal['traits', 'resourceTypes', 'securitySchemes']
_UNRESOLVED: Final[dict[_Bucket, type[UnresolvedNode[Any]]]] = {
    'traits': UnresolvedTraitNode,
    'resourceTypes': UnresolvedResourceTypeNode,
    'securitySchemes': UnresolvedSchemeNode,
}

#: Percent-escaped in an IRI segment. Everything outside is escaped, so a media
#: type, a URI template and a `/regex/` property name all survive as one segment.
_SAFE: Final = ''


# -- the graph ----------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Edge:
    """One relationship. Frozen so a set of edges deduplicates by value."""

    subject: str
    predicate: str
    object: str


@dataclass(slots=True, eq=False)
class Route:
    """One route through the graph: the nodes reached and the edges taken.

    The reason this type exists is the one thing SPARQL 1.1 property paths
    cannot do. `?a (p|q)* ?b` answers *whether* `b` is reachable; it cannot bind
    what lay between, because a path expression introduces no variables. For a
    navigation tool "why does this endpoint expose `User`" is the question, and
    the answer is the intermediate nodes (docs/16 § 5).
    """

    #: Every node from the origin to the target, inclusive. Length >= 1.
    nodes: tuple[str, ...]
    #: One predicate per hop, so `len(predicates) == len(nodes) - 1`.
    predicates: tuple[str, ...]

    @property
    def target(self) -> str:
        return self.nodes[-1]

    def __len__(self) -> int:
        return len(self.predicates)


class Graph:
    """Nodes, edges, and both adjacency directions.

    Built once and read many times: the reverse index is what makes "who points
    at this" as cheap as "what does this point at", and inverse traversal is
    most of what a navigation question turns out to be.
    """

    __slots__ = ('_incoming', '_outgoing', 'base', 'edges', 'nodes', 'root')

    def __init__(
        self,
        base: str,
        nodes: dict[str, GraphNode[Any]],
        edges: list[Edge],
        *,
        root: str = '',
    ) -> None:
        self.base = base
        #: The entry document's directory URI. What the IRIs above are
        #: relative to, and what a consumer needs to turn a shape's absolute
        #: `location` back into the path a person typed.
        self.root = root
        #: Every node carries the model object it was projected from, so the
        #: way back to the model is the node itself. Two side maps used to hold
        #: that for types and for endpoints — 60% of the nodes — and nothing
        #: recorded it for the rest (docs/16 § 2.7).
        self.nodes = nodes
        self.edges = edges
        self._outgoing: dict[str, list[Edge]] = {}
        self._incoming: dict[str, list[Edge]] = {}
        for edge in edges:
            self._outgoing.setdefault(edge.subject, []).append(edge)
            self._incoming.setdefault(edge.object, []).append(edge)

    def __repr__(self) -> str:
        return f'Graph(nodes={len(self.nodes)}, edges={len(self.edges)})'

    # -- adjacency ------------------------------------------------------------

    def out(self, iri: str, predicates: Sequence[str] | None = None) -> list[Edge]:
        """Edges leaving `iri`, in insertion order — which is declaration order."""
        edges = self._outgoing.get(iri, [])
        return edges if predicates is None else [e for e in edges if e.predicate in predicates]

    def into(self, iri: str, predicates: Sequence[str] | None = None) -> list[Edge]:
        """Edges arriving at `iri`. The half a tree walk cannot give you."""
        edges = self._incoming.get(iri, [])
        return edges if predicates is None else [e for e in edges if e.predicate in predicates]

    def entity_at(self, iri: str) -> Entity | None:
        """The model object behind a node, or `None` if the IRI is unknown.

        Every node has one, so this returns `None` only for an IRI that names
        no node. The three narrowing helpers below are `isinstance` over this.
        """
        node = self.nodes.get(iri)
        return node.entity if node is not None else None

    def endpoint_at(self, iri: str) -> EndPoint | None:
        """The resource behind a node, or `None`. See `shape_at`."""
        found = self.entity_at(iri)
        return found if isinstance(found, EndPoint) else None

    def operation_at(self, iri: str) -> Operation | None:
        """The method behind a node, or `None`. See `shape_at`."""
        found = self.entity_at(iri)
        return found if isinstance(found, Operation) else None

    def shape_at(self, iri: str) -> BaseShape | None:
        """The declaration behind a node, or `None` for a node that is not a type.

        The graph keeps every entity's *structure*; the model keeps its detail.
        A caller that has located something here and now wants its facets asks
        for the shape rather than reading the projection, which deliberately
        carries only what a traversal needs (§ 2.5).
        """
        found = self.entity_at(iri)
        return found if isinstance(found, BaseShape) else None

    def kind_of(self, iri: str) -> str:
        node = self.nodes.get(iri)
        return node.kinds[0] if node is not None else 'Unknown'

    def label(self, iri: str) -> str:
        """A short human name, falling back to the kind and then to the IRI.

        An anonymous shape is *named* by the facet that holds it — the model
        calls an array's member `items` — so a route ended `-items-> items`,
        telling the reader the hop they had just followed and nothing about the
        node. A name that only repeats the IRI segment is structural rather than
        authored, and the node's `type` is the useful answer. A declaration is
        exempt: `types/User` repeats its segment too, and there `User` is the
        name a person wrote.
        """
        node = self.nodes.get(iri)
        if node is None:
            return iri.rsplit('/', 1)[-1] or iri
        segment = iri.rsplit('/', 1)[-1]
        # Bound once: derived per read, and this asks up to seven times (§ 2.8).
        attributes = node.attributes
        keys: tuple[str, ...] = ('name', 'path', 'method', 'statusCode', 'mediaType', 'type')
        # Only a `Type`: an operation's name defaults to its method, which also
        # spells its segment, and there `get` is exactly what the reader wants.
        structural = node.kinds[0] == 'Type' and not _is_declaration(iri)
        if structural and attributes.get('name') == segment:
            keys = keys[1:]
        for key in keys:
            value = attributes.get(key)
            if isinstance(value, str) and value:
                return value
        return segment or iri

    def find(self, name: str, kinds: Sequence[str] | None = None) -> list[str]:
        """Every node matching `name`, for turning a CLI word into an IRI.

        **A declaration wins over a node beneath one.** A synthetic parent
        carries the name of the type it resolves to, so `Admin: [User, Entity]`
        makes `Entity` match both its own declaration and
        `…/types/Admin/inherits/Entity`. Reporting that pair as an ambiguity is
        useless: they are the same type, and only one of them is somewhere an
        author can go. Without this rule `refs Entity` fails on a two-type
        document, which is how the rule was found.

        Two *declarations* of one name — the same type declared in two libraries
        — stay ambiguous. That one the caller has to resolve, and the whole IRI
        is accepted here so that it can.
        """
        matched = [iri for iri, node in self.nodes.items() if node.attributes.get('name') == name]
        if not matched:
            matched = [iri for iri in self.nodes if iri == name or iri.endswith('/' + name)]
        declared = [iri for iri in matched if _is_declaration(iri)]
        if declared:
            matched = declared
        matched = _outermost(matched)
        if kinds is None:
            return matched
        return [iri for iri in matched if self.nodes[iri].kinds[0] in kinds]

    def entries(self, kinds: Sequence[str] | None = None) -> list[tuple[str, str, str]]:
        """The navigable inventory: `(kind, name, iri)`, sorted by kind then name.

        What `find` can resolve to exactly one node, which is the set a reader
        may usefully pass back to `refs`, `deps` or `show`. Every *declaration*
        — a type, trait, resource type, security scheme or annotation type —
        plus endpoints and operations, which have no declaration bucket but are
        the entities a reader most often starts from.

        Deliberately not every node. The nodes inside a declaration outnumber
        the declarations by twenty to one on a real document and are reached by
        walking, not by naming: listing them would bury the answer in the
        question. `kinds` narrows further.

        A name that two declarations share appears twice, because it is two
        entities and `find` will rightly call it ambiguous. Sorting is by kind
        and then by name so the output is stable across parses; declaration
        order within a kind is not preserved here, and is not what a reader
        scanning for a name wants.
        """
        wanted = None if kinds is None else {kind.casefold() for kind in kinds}
        found = []
        for iri, node in self.nodes.items():
            kind = node.kinds[0]
            if not (_is_declaration(iri) or kind in ('EndPoint', 'Operation')):
                continue
            if wanted is not None and kind.casefold() not in wanted:
                continue
            found.append((kind, self.label(iri), iri))
        return sorted(found, key=lambda entry: (entry[0], entry[1]))

    def suggest(self, name: str, limit: int = 5) -> list[str]:
        """Names close to `name`, for a miss that would otherwise be a dead end.

        Suggests; never substitutes. Running the nearest name answers a question
        the caller did not ask, which is the same reason `find` reports an
        ambiguity rather than picking from it.
        """
        import difflib  # noqa: PLC0415 - only a failed lookup pays for this

        names = list(dict.fromkeys(name for _, name, _ in self.entries()))
        close = difflib.get_close_matches(name, names, n=limit, cutoff=0.6)
        if close:
            return close
        # `get_close_matches` is ratio-based, so a short query inside a long
        # name — `user` against `userLoginInfo` — scores below any cutoff worth
        # using. A reader typing a fragment of the name they half-remember is
        # the commonest miss there is.
        folded = name.casefold()
        return [candidate for candidate in names if folded in candidate.casefold()][:limit]

    # -- traversal ------------------------------------------------------------

    def walk(
        self,
        origin: str,
        predicates: Sequence[str] = TYPE_EDGES,
        *,
        reverse: bool = False,
        max_depth: int | None = None,
    ) -> list[Route]:
        """Every node reachable from `origin`, each with the route that found it.

        Breadth-first, so the route reported is a shortest one; a cyclic type
        stops the walk at its first re-entry rather than unrolling, which is the
        same reason `RecursiveShape` exists in the model at all.
        """
        seen = {origin}
        frontier = [Route(nodes=(origin,), predicates=())]
        found: list[Route] = []
        while frontier:
            nxt: list[Route] = []
            for path in frontier:
                if max_depth is not None and len(path) >= max_depth:
                    continue
                edges = self.into(path.target, predicates) if reverse else self.out(path.target, predicates)
                for edge in edges:
                    other = edge.subject if reverse else edge.object
                    if other in seen:
                        continue
                    seen.add(other)
                    step = Route(nodes=(*path.nodes, other), predicates=(*path.predicates, edge.predicate))
                    found.append(step)
                    nxt.append(step)
            frontier = nxt
        return found

    def route(self, origin: str, target: str, predicates: Sequence[str] = TYPE_EDGES) -> Route | None:
        """One shortest route from `origin` to `target`, or `None`."""
        for path in self.walk(origin, predicates):
            if path.target == target:
                return path
        return None

    # -- serialisation --------------------------------------------------------

    def to_ntriples(self) -> Iterator[str]:
        """N-Triples: one statement per line, absolute IRIs, no prefixes.

        The lowest-common-denominator RDF syntax, which is the point — anything
        that reads RDF reads this, and it needs no serialiser state.
        """
        for iri, node in self.nodes.items():
            subject = _iri(iri)
            for kind in node.kinds:
                yield f'{subject} {_iri(_RDF_TYPE)} {_iri(RAML_NS + kind)} .'
            for key, value in node.attributes.items():
                # A multi-valued facet is one triple per member, which is how
                # RDF spells a set. Joining them would be lossy here too.
                for one in value if isinstance(value, tuple) else (value,):
                    yield f'{subject} {_iri(RAML_NS + key)} {_literal(one)} .'
        for edge in self.edges:
            yield f'{_iri(edge.subject)} {_iri(RAML_NS + edge.predicate)} {_iri(edge.object)} .'

    def to_turtle(self) -> Iterator[str]:
        """Turtle with the vocabulary prefixed. Same statements, readable."""
        yield f'@prefix raml: <{RAML_NS}> .'
        yield f'@prefix xsd: <{_XSD}> .'
        yield ''
        for iri, node in self.nodes.items():
            subject = _iri(iri)
            yield f'{subject} a {", ".join("raml:" + k for k in node.kinds)} ;'
            statements = [
                f'    raml:{key} {_literal(one, prefixed=True)}'
                for key, value in node.attributes.items()
                for one in (value if isinstance(value, tuple) else (value,))
            ]
            statements += [f'    raml:{e.predicate} {_iri(e.object)}' for e in self.out(iri)]
            if statements:
                yield ' ;\n'.join(statements) + ' .'
            else:
                yield '    .'
            yield ''

    def to_dot(self) -> Iterator[str]:
        """Graphviz. For looking at a document set rather than querying it."""
        yield 'digraph raml {'
        yield '  rankdir=LR; node [shape=box, fontname="monospace", fontsize=10];'
        ids = {iri: f'n{index}' for index, iri in enumerate(self.nodes)}
        for iri, node in self.nodes.items():
            yield f'  {ids[iri]} [label="{_dot(self.label(iri))}\\n{node.kinds[0]}"];'
        for edge in self.edges:
            if edge.subject in ids and edge.object in ids:
                yield f'  {ids[edge.subject]} -> {ids[edge.object]} [label="{_dot(edge.predicate)}"];'
        yield '}'

    def to_json(self) -> dict[str, Any]:
        """The graph as plain data, for a consumer that wants neither RDF nor DOT."""
        return {
            'base': self.base,
            'nodes': [
                {
                    'iri': iri,
                    'kinds': list(node.kinds),
                    'attributes': {
                        key: list(value) if isinstance(value, tuple) else value
                        for key, value in node.attributes.items()
                    },
                }
                for iri, node in self.nodes.items()
            ],
            'edges': [{'s': e.subject, 'p': e.predicate, 'o': e.object} for e in self.edges],
        }


_RDF_TYPE: Final = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'


# -- serialisation helpers ----------------------------------------------------


def _iri(value: str) -> str:
    return '<' + value.replace('\\', '%5C').replace('>', '%3E').replace(' ', '%20') + '>'


def _literal(value: str | int | bool, *, prefixed: bool = False) -> str:  # noqa: FBT001 - a literal *is* a bool or not
    if value is True or value is False:
        suffix = '^^xsd:boolean' if prefixed else f'^^<{_XSD}boolean>'
        return f'"{"true" if value else "false"}"{suffix}'
    if isinstance(value, int):
        suffix = '^^xsd:integer' if prefixed else f'^^<{_XSD}integer>'
        return f'"{value}"{suffix}'
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return '"' + escaped.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t') + '"'


def _dot(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


def _segment(value: str) -> str:
    """One IRI path segment. Everything unusual is percent-escaped.

    A media type, a `/{userId}` template and a `/^x-/` pattern-property name all
    have to survive as a single segment, so the safe set is empty rather than
    the `/` `urllib` leaves alone by default.
    """
    return quote(value, safe=_SAFE)


def build_graph(raml: Raml, *, base: str = DEFAULT_BASE) -> Graph:
    """Project a parsed model. Use `ParseOptions(unwrap=True)` — see the module docstring."""
    builder = _Builder(raml, base)
    builder.run()
    return Graph(base, builder.nodes, builder.edges, root=builder.root)


class _Builder:
    """One projection. Node IRIs are assigned on first encounter, so the walk
    order is part of the contract: declarations before endpoints, which is what
    makes `User` land at `#/declarations/types/User` and not at whichever
    response body happened to reach it first. go-raml's converter pre-registers
    for exactly this reason (`converter/jsonld.go`, `preRegisterTypes`).
    """

    __slots__ = (
        '_segments',
        'base',
        'claimed',
        'edges',
        'emitted',
        'iris',
        'nodes',
        'raml',
        'root',
    )

    def __init__(self, raml: Raml, base: str) -> None:
        self.raml = raml
        self.base = base
        self.nodes: dict[str, GraphNode[Any]] = {}
        self.edges: list[Edge] = []
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
        self.root = raml.location.rsplit('/', 1)[0] + '/' if raml.location else ''

    # -- infrastructure -------------------------------------------------------

    def unit(self, location: str) -> str:
        """The IRI prefix for declarations authored in `location`.

        Relative to the entry document's directory, so the graph does not carry
        the absolute path of the machine that produced it. A file outside that
        directory keeps its whole URI, which is rare and visibly different.
        """
        if location == self.raml.location or not location:
            return self.base
        relative = location.removeprefix(self.root)
        return f'{self.base}/{self.segment(relative)}'

    def segment(self, value: str) -> str:
        escaped = self._segments.get(value)
        if escaped is None:
            escaped = _segment(value)
            self._segments[value] = escaped
        return escaped

    def add(self, node: GraphNode[Any]) -> str:
        """Record `node`, or keep the one already at its IRI.

        First reach wins, which is what makes a declared type one node rather
        than one per use site and what closes a type cycle (docs/16 § 3).
        """
        self.nodes.setdefault(node.iri, node)
        return node.iri

    def declare(self, unit: str, bucket: str, name: str) -> str:
        """The IRI of one declaration in `unit`'s `types:`, `traits:` and so on."""
        return f'{unit}{_DECLARATIONS}{bucket}/{self.segment(name)}'

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

    def edge(self, subject: str, predicate: str, obj: str) -> None:
        if obj:
            self.edges.append(Edge(subject=subject, predicate=predicate, object=obj))

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
        unit = self.add(UnitNode(self.unit(shape.location), holder, self.root))
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
        self.add(UnitNode(unit, fragment, self.root))
        for name, shape in fragment.types.items():
            self.declared(shape, self.shape(shape, self.declare(unit, 'types', name)))
        for name, shape in fragment.annotation_types.items():
            self.declared(shape, self.shape(shape, self.declare(unit, 'annotations', name)))
        for name, scheme in fragment.security_schemes.items():
            iri = self.add(SecuritySchemeNode(self.declare(unit, 'securitySchemes', name), scheme, self.root))
            # Under both the declaration and whatever an `!include` resolved to,
            # so a `securedBy:` bound to either finds this one node.
            self.iris[scheme.id] = iri
            self.iris[scheme.resolved().id] = iri
            self.edge(unit, 'declares', iri)
        for name, trait in fragment.traits.items():
            iri = self.add(TraitNode(self.declare(unit, 'traits', name), trait, self.root))
            self.iris[trait.id] = iri
            self.iris[trait.resolved().id] = iri
            self.edge(unit, 'declares', iri)
        for name, resource_type in fragment.resource_types.items():
            iri = self.add(ResourceTypeNode(self.declare(unit, 'resourceTypes', name), resource_type, self.root))
            self.iris[resource_type.id] = iri
            self.iris[resource_type.resolved().id] = iri
            self.edge(unit, 'declares', iri)

    def api(self) -> None:
        entry = self.raml.entry_point
        if not isinstance(entry, APIFragment):
            return
        api = self.add(ApiNode(f'{self.base}#/web-api', entry, self.root))
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
        iri = self.add(
            EndPointNode(f'{self.base}#/web-api/endpoint/{self.segment(endpoint.full_uri)}', endpoint, self.root)
        )
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
        self, subject: str, predicate: str, bucket: _Bucket, ref: DirectiveRef | SecurityScheme, location: str
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
        self.edge(subject, predicate, self.add(_UNRESOLVED[bucket](local, ref, self.root)))

    def operation(self, endpoint: str, operation: Operation) -> None:
        iri = self.add(
            OperationNode(f'{endpoint}/supportedOperation/{self.segment(operation.method)}', operation, self.root)
        )
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
        iri = self.add(RequestNode(f'{operation}/request', request, self.root))
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
        iri = self.add(ResponseNode(f'{operation}/returns/{self.segment(response.code)}', response, self.root))
        self.edge(operation, 'returns', iri)
        self.annotated(iri, response.annotations)
        for name, param in response.headers.items():
            child = self.parameter(f'{iri}/parameter/header/{self.segment(name)}', param)
            self.edge(iri, 'parameter', child)
        for media, body in response.bodies.items():
            self.edge(iri, 'payload', self.payload(iri, media, body))

    def payload(self, parent: str, media: str, body: Body) -> str:
        iri = self.add(PayloadNode(f'{parent}/payload/{self.segment(media or "default")}', body, self.root))
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
        self.add(ParameterNode(iri, param, self.root))
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
        self.add(TypeNode(iri, base, self.root, kind))
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
                self.add(PropertyNode(child, prop, self.root))
                self.edge(child, 'range', self.shape(prop.base, f'{child}/schema'))
                self.edge(iri, 'property', child)
            for name, pattern in (shape.pattern_properties or {}).items():
                # Keyed by the `/regex/` as written, not by position: a pattern
                # property has no name of its own and an index would move under
                # any edit above it.
                child = f'{iri}/patternProperty/{self.segment(name)}'
                self.add(PatternPropertyNode(child, pattern, self.root))
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
