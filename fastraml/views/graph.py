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
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from fastraml.nodes import (
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
from fastraml.parser.endpoints import EndPoint, Operation
from fastraml.types.base import BaseShape
from fastraml.views.walk import DECLARATIONS, DEFAULT_BASE, Addresses, Bucket, Walk, workspace_of

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from fastraml.parser.directives import DirectiveRef, SecurityScheme
    from fastraml.parser.endpoints import Body, Request, Response
    from fastraml.parser.fragments import APIFragment, Fragment
    from fastraml.parser.resourcetypes import ResourceTypeDefinition
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.parser.traits import TraitDefinition
    from fastraml.registry import Raml
    from fastraml.types.base import Parameter, PatternProperty, Property

__all__ = [
    'DEFAULT_BASE',
    'RAML_NS',
    'TYPE_EDGES',
    'USE_EDGES',
    'Edge',
    'Graph',
    'Route',
    'build_graph',
    'is_declaration',
]

#: The vocabulary namespace. A URN rather than an `http(s)` IRI on purpose: this
#: project claims no domain name, and a prefix that 404s is worse than one that
#: never promised to resolve. Provisional until 1.0 — see docs/16 § 2.
RAML_NS: Final = 'urn:fastraml:ns:raml#'

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

_REQUEST_EDGES: Final = (*TYPE_EDGES, 'parameter', 'queryString', 'payload')

_XSD: Final = 'http://www.w3.org/2001/XMLSchema#'

#: Opens the tail of every declaration IRI (§ 3).


def is_declaration(iri: str) -> bool:
    """Whether `iri` names a declaration rather than a node inside one.

    Exported because more than one view has to agree about it: a rule that
    judges *declared* types and a query that does the same must draw the line
    in one place, which is the mistake docs/16 § 6.2 records — three catalogue
    queries matched every `Type` node instead of every declared one, and
    returned one row per use of a problem instead of one row per problem.

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


class Edge(NamedTuple):
    """One immutable relationship, shared by the list and both indexes."""

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

    __slots__ = ('_incoming', '_outgoing', '_request_shape_iris', 'addresses', 'base', 'edges', 'nodes', 'root')

    def __init__(  # noqa: PLR0913 - optional prebuilt indexes avoid a second edge pass
        self,
        base: str,
        nodes: dict[str, GraphNode[Any]],
        edges: list[Edge],
        *,
        root: str = '',
        addresses: Addresses | None = None,
        incoming: dict[str, list[Edge]] | None = None,
        outgoing: dict[str, list[Edge]] | None = None,
    ) -> None:
        self.base = base
        #: Where every entity the walk reached was addressed. The join between
        #: a node in this graph and the same entity in any other emitter's
        #: output, which is the reason assignment is one walk (`fastraml.views.walk`).
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
        self._request_shape_iris: frozenset[str] | None = None
        if incoming is None or outgoing is None:
            # `build_graph` supplies both, because the sink filled them as it
            # went; this is for a `Graph` assembled from edges by hand, which
            # any consumer building one does. Same `get`
            # branch as `_GraphSink.edge`, to right-size the same singletons.
            self._outgoing: dict[str, list[Edge]] = {}
            self._incoming: dict[str, list[Edge]] = {}
            for edge in edges:
                out = self._outgoing.get(edge.subject)
                if out is None:
                    self._outgoing[edge.subject] = [edge]
                else:
                    out.append(edge)
                into = self._incoming.get(edge.object)
                if into is None:
                    self._incoming[edge.object] = [edge]
                else:
                    into.append(edge)
        else:
            self._incoming = incoming
            self._outgoing = outgoing

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

    def request_shape_iris(self) -> frozenset[str]:
        """Type nodes reachable from caller-supplied request data."""
        if self._request_shape_iris is not None:
            return self._request_shape_iris

        roots = []
        for iri, node in self.nodes.items():
            if isinstance(node, RequestNode):
                roots.append(iri)
                continue
            if not isinstance(node, ParameterNode):
                continue
            if any(
                isinstance(self.nodes.get(edge.subject), (ApiNode, EndPointNode))
                for edge in self.into(iri, ('parameter',))
            ):
                roots.append(iri)

        self._request_shape_iris = frozenset(
            route.target
            for root in roots
            for route in self.walk(root, _REQUEST_EDGES)
            if isinstance(self.nodes.get(route.target), TypeNode)
        )
        return self._request_shape_iris

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
        # `name` is the first key the loop below would try, and it is the answer
        # for all but anonymous shapes — so read it directly and skip building
        # the dictionary entirely. The structural test has to run first: a Type
        # whose name only repeats its own IRI segment is exactly the case the
        # loop exists to fall through (docs/12 § 19e).
        name = node.name
        if name and not (name == segment and node.kinds[0] == 'Type' and not is_declaration(iri)):
            return name
        # Bound once: derived per read, and this asks up to seven times (§ 2.8).
        attributes = node.attributes
        keys: tuple[str, ...] = ('name', 'path', 'method', 'statusCode', 'mediaType', 'type')
        # Only a `Type`: an operation's name defaults to its method, which also
        # spells its segment, and there `get` is exactly what the reader wants.
        structural = node.kinds[0] == 'Type' and not is_declaration(iri)
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
        # `node.name`, not `node.attributes['name']`: the dictionary a `TypeNode`
        # builds projects the shape and walks every facet its kind declares, all
        # of it discarded here (docs/12 § 19e).
        matched = [iri for iri, node in self.nodes.items() if node.name == name]
        if not matched:
            matched = [iri for iri in self.nodes if iri == name or iri.endswith('/' + name)]
        declared = [iri for iri in matched if is_declaration(iri)]
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
            if not (is_declaration(iri) or kind in ('EndPoint', 'Operation')):
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
    sink = _GraphSink(workspace_of(raml))
    walk = Walk(raml, base, sink)
    walk.run()
    return Graph(
        base,
        sink.nodes,
        sink.edges,
        root=sink.root,
        addresses=Addresses(base, walk.iris),
        incoming=sink.incoming,
        outgoing=sink.outgoing,
    )


class _GraphSink:
    """The `Sink` that builds nodes and edges.

    Every method turns one role into the node class that owns it. The mapping is
    static — `endpoint` can only make an `EndPointNode` — which is what keeps a
    node's kind and its entity from disagreeing (`docs/16` § 2.7).

    First reach wins, so a declared type is one node rather than one per use
    site and a type cycle closes (`docs/16` § 3).
    """

    __slots__ = ('edges', 'incoming', 'nodes', 'outgoing', 'root')

    def __init__(self, root: str) -> None:
        self.nodes: dict[str, GraphNode[Any]] = {}
        self.edges: list[Edge] = []
        self.incoming: dict[str, list[Edge]] = {}
        self.outgoing: dict[str, list[Edge]] = {}
        self.root = root

    def _add(self, node: GraphNode[Any]) -> None:
        self.nodes.setdefault(node.iri, node)

    def unit(self, iri: str, fragment: Fragment) -> None:
        if iri not in self.nodes:
            self.nodes[iri] = UnitNode(iri, fragment, self.root)

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
        if iri not in self.nodes:
            self.nodes[iri] = _UNRESOLVED[bucket](iri, ref, self.root)

    def edge(self, subject: str, predicate: str, obj: str) -> None:
        """One relationship, appended to the list and to both adjacency indices.

        Written as a `get`/branch rather than `setdefault(key, []).append(...)`
        to **right-size the singleton lists**, which is what nearly every entry
        in both indices is: on `bench_endpoints`, 100% of `incoming` and 73% of
        `outgoing` hold exactly one edge. A list built as `[]` and then appended
        to over-allocates to capacity 4 and costs 88 bytes; built as `[edge]` it
        is exact and costs 64. At 54 505 singletons that is 1.3 MB, and the
        measured saving is 1.23 MB of peak — about 5% of the projection.

        It is **not** faster, and the discarded empty lists `setdefault` builds
        eagerly are not why. Those come off CPython's list freelist and cost
        effectively nothing; an A/B of `build_graph` alone is 77.1 ms against
        77.2 ms. Only the memory moves.
        """
        edge = Edge(subject, predicate, obj)
        self.edges.append(edge)
        out = self.outgoing.get(subject)
        if out is None:
            self.outgoing[subject] = [edge]
        else:
            out.append(edge)
        into = self.incoming.get(obj)
        if into is None:
            self.incoming[obj] = [edge]
        else:
            into.append(edge)
