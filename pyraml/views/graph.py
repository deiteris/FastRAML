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
from typing import TYPE_CHECKING, Any, Final

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
from pyraml.parser.endpoints import EndPoint, Operation
from pyraml.types.base import BaseShape
from pyraml.views.walk import DECLARATIONS, DEFAULT_BASE, Addresses, Bucket, Walk

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pyraml.parser.directives import DirectiveRef, SecurityScheme
    from pyraml.parser.endpoints import Body, Request, Response
    from pyraml.parser.fragments import APIFragment, Fragment
    from pyraml.parser.resourcetypes import ResourceTypeDefinition
    from pyraml.parser.security import SecuritySchemeDefinition
    from pyraml.parser.traits import TraitDefinition
    from pyraml.registry import Raml
    from pyraml.types.base import Parameter, PatternProperty, Property

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


def _is_declaration(iri: str) -> bool:
    """Whether `iri` names a declaration rather than a node inside one.

    Told apart by depth. A declaration's tail is `<bucket>/<name>` and nothing
    more — `types/User` — while a node beneath it keeps going:
    `types/Admin/inherits/User`. A name cannot contribute a `/` of its own,
    because every segment is percent-escaped.
    """
    _, marker, tail = iri.partition(DECLARATIONS)
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
#: names resolved to nothing. `Bucket` is a `Literal` rather than `str` so the
#: lookup is total and mypy says so.
_UNRESOLVED: Final[dict[Bucket, type[UnresolvedNode[Any]]]] = {
    'traits': UnresolvedTraitNode,
    'resourceTypes': UnresolvedResourceTypeNode,
    'securitySchemes': UnresolvedSchemeNode,
}

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

    __slots__ = ('_incoming', '_outgoing', 'addresses', 'base', 'edges', 'nodes', 'root')

    def __init__(
        self,
        base: str,
        nodes: dict[str, GraphNode[Any]],
        edges: list[Edge],
        *,
        root: str = '',
        addresses: Addresses | None = None,
    ) -> None:
        self.base = base
        #: Where every entity the walk reached was addressed. The join between
        #: a node in this graph and the same entity in any other emitter's
        #: output, which is the reason assignment is one walk (`pyraml.views.walk`).
        self.addresses = Addresses(base, {}) if addresses is None else addresses
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


def build_graph(raml: Raml, *, base: str = DEFAULT_BASE) -> Graph:
    """Project a parsed model. Use `ParseOptions(unwrap=True)` — see the module docstring."""
    sink = _GraphSink(raml.location.rsplit('/', 1)[0] + '/' if raml.location else '')
    walk = Walk(raml, base, sink)
    walk.run()
    return Graph(base, sink.nodes, sink.edges, root=sink.root, addresses=Addresses(base, walk.iris))


class _GraphSink:
    """The `Sink` that builds nodes and edges.

    Every method turns one role into the node class that owns it. The mapping is
    static — `endpoint` can only make an `EndPointNode` — which is what keeps a
    node's kind and its entity from disagreeing (`docs/16` § 2.7).

    First reach wins, so a declared type is one node rather than one per use
    site and a type cycle closes (`docs/16` § 3).
    """

    __slots__ = ('edges', 'nodes', 'root')

    def __init__(self, root: str) -> None:
        self.nodes: dict[str, GraphNode[Any]] = {}
        self.edges: list[Edge] = []
        self.root = root

    def _add(self, node: GraphNode[Any]) -> None:
        self.nodes.setdefault(node.iri, node)

    def unit(self, iri: str, fragment: Fragment) -> None:
        self._add(UnitNode(iri, fragment, self.root))

    def api(self, iri: str, fragment: APIFragment) -> None:
        self._add(ApiNode(iri, fragment, self.root))

    def type_(self, iri: str, base: BaseShape, shape_kind: str) -> None:
        self._add(TypeNode(iri, base, self.root, shape_kind))

    def property_(self, iri: str, prop: Property) -> None:
        self._add(PropertyNode(iri, prop, self.root))

    def pattern_property(self, iri: str, prop: PatternProperty) -> None:
        self._add(PatternPropertyNode(iri, prop, self.root))

    def parameter(self, iri: str, param: Parameter) -> None:
        self._add(ParameterNode(iri, param, self.root))

    def payload(self, iri: str, body: Body) -> None:
        self._add(PayloadNode(iri, body, self.root))

    def request(self, iri: str, request: Request) -> None:
        self._add(RequestNode(iri, request, self.root))

    def response(self, iri: str, response: Response) -> None:
        self._add(ResponseNode(iri, response, self.root))

    def operation(self, iri: str, operation: Operation) -> None:
        self._add(OperationNode(iri, operation, self.root))

    def endpoint(self, iri: str, endpoint: EndPoint) -> None:
        self._add(EndPointNode(iri, endpoint, self.root))

    def trait(self, iri: str, definition: TraitDefinition) -> None:
        self._add(TraitNode(iri, definition, self.root))

    def resource_type(self, iri: str, definition: ResourceTypeDefinition) -> None:
        self._add(ResourceTypeNode(iri, definition, self.root))

    def security_scheme(self, iri: str, definition: SecuritySchemeDefinition) -> None:
        self._add(SecuritySchemeNode(iri, definition, self.root))

    def unresolved(self, iri: str, bucket: Bucket, ref: DirectiveRef | SecurityScheme) -> None:
        self._add(_UNRESOLVED[bucket](iri, ref, self.root))

    def edge(self, subject: str, predicate: str, obj: str) -> None:
        self.edges.append(Edge(subject=subject, predicate=predicate, object=obj))
