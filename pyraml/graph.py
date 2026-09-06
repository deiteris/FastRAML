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
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final, Literal
from urllib.parse import quote

from pyraml.parser.directives import DirectiveRef, SecurityScheme
from pyraml.parser.endpoints import Body, EndPoint, Operation, Response
from pyraml.parser.fragments import APIFragment, Fragment, Library
from pyraml.parser.resourcetypes import ResourceTypeDefinition
from pyraml.parser.security import SecuritySchemeDefinition
from pyraml.parser.traits import TraitDefinition
from pyraml.types.base import BaseShape, Parameter, PatternProperty, Property, ScalarFacet
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from pyraml.types.jsonschema_ import projected
from pyraml.uris import relative_to

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.endpoints import Request
    from pyraml.positions import Position
    from pyraml.registry import Raml
    from pyraml.types.base import Shape

__all__ = [
    'DEFAULT_BASE',
    'RAML_NS',
    'TYPE_EDGES',
    'USE_EDGES',
    'Edge',
    'Entity',
    'Graph',
    'GraphNode',
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

#: The model's binding names to this vocabulary's. They differ in one place —
#: RAML declares `uriParameters`, and the graph has always spelled that `path`,
#: in the attribute and in the IRI segment. Renaming it would move every URI
#: parameter's IRI, which § 3 promises is stable, so the mapping stays here:
#: owning the vocabulary is this layer's job, and the model's name is its own.
_BINDING: Final[dict[str, str]] = {'uri': 'path', 'query': 'query', 'header': 'header'}


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


#: The `#/declarations/<bucket>/` segments that hold something a `type:`, `is:`
#: or `securedBy:` entry can name, and the node kind each one declares. A
#: `Literal` rather than `str` so the lookup below is total and mypy says so.
_Bucket = Literal['traits', 'resourceTypes', 'securitySchemes']
_DECLARED_KINDS: Final[dict[_Bucket, str]] = {
    'traits': 'Trait',
    'resourceTypes': 'ResourceType',
    'securitySchemes': 'SecurityScheme',
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


#: What a node attribute may be. A tuple is a genuinely multi-valued facet.
type Literal_ = str | int | bool | tuple[str, ...]

#: Everything a node can stand for. Every kind in the vocabulary projects one of
#: these, and the field below is not optional, so this list being total is
#: checked by `mypy` at each of the fifteen places a node is created rather than
#: asserted afterwards (docs/16 § 2.7).
type Entity = (
    BaseShape
    | Property
    | PatternProperty
    | Parameter
    | Body
    | Request
    | Response
    | Operation
    | EndPoint
    | Fragment
    | TraitDefinition
    | ResourceTypeDefinition
    | SecuritySchemeDefinition
    | DirectiveRef
    | SecurityScheme
)


@dataclass(slots=True, eq=False)
class GraphNode:
    """One entity: what it is, what it is called, and its literal facets.

    `kinds` is most-specific first, as AMF's `@type` arrays are, so a consumer
    that wants one label reads `kinds[0]` and one that wants to filter reads the
    rest.
    """

    iri: str
    kinds: tuple[str, ...]
    #: The model object this node projects. Every node has one: a node without
    #: an entity would be something this layer invented, and inventing is what
    #: it is not for (docs/16 § 1).
    entity: Entity
    #: The entry document's directory URI, which `definedIn` is relative to.
    #: The one piece of context the entity does not carry; every node in a
    #: graph holds the same string object.
    root: str = ''

    @property
    def attributes(self) -> dict[str, Literal_]:
        """The node's literals, read from the entity on demand.

        Derived rather than stored. Storing them copied 81,688 values into
        32,090 dicts on a real 149-endpoint document — 6.0 MB restating what
        the model already held, built whether or not anything read it
        (docs/16 § 2.8).

        A tuple value is a genuinely multi-valued facet — `enum`, OAuth scopes
        — and is not joined into a string: an enum value may itself contain a
        space, so `["new york", "london"]` and three separate values would be
        indistinguishable, and a diff could not say which member was removed.
        """
        return _attributes(self)

    def __repr__(self) -> str:
        return f'GraphNode({self.iri!r}, {self.kinds[0]!r})'


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
        nodes: dict[str, GraphNode],
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


def _number_text(value: Fraction) -> str:
    """A `Fraction` as text, without going through `float`.

    The project rule is that numbers never pass through `float` on either side
    of a comparison (docs/10 § 5.2), and a graph literal is no exception even
    though nothing compares it: `1.1` reaching a reader as `1.100000000000000088`
    would be a defect of this module, not of the parser.
    """
    if value.denominator == 1:
        return str(value.numerator)
    residue = value.denominator
    for factor in (2, 5):
        while residue % factor == 0:
            residue //= factor
    if residue != 1:
        # Not representable as a terminating decimal, so it is reported exactly
        # as the ratio it is. `multipleOf: 1/3` cannot arise from RAML source,
        # which is decimal, but a merged bound could in principle.
        return f'{value.numerator}/{value.denominator}'
    digits = 0
    scaled = value
    while scaled.denominator != 1:
        scaled *= 10
        digits += 1
    text = str(abs(scaled.numerator)).rjust(digits + 1, '0')
    sign = '-' if scaled.numerator < 0 else ''
    return f'{sign}{text[:-digits]}.{text[-digits:]}'


# -- the projection -----------------------------------------------------------


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
        'declared',
        'edges',
        'emitted',
        'entities',
        'nodes',
        'raml',
        'root',
        'scheme_iris',
        'shape_iris',
        'shapes',
    )

    def __init__(self, raml: Raml, base: str) -> None:
        self.raml = raml
        self.base = base
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[Edge] = []
        self.shape_iris: dict[int, str] = {}
        #: The inverse of `shape_iris`, by IRI, for `Graph.shape_at`.
        #: Declaration fragment (`#/declarations/traits/paged`) -> the first IRI
        #: carrying it, filled by `declare` at the five places a declaration is
        #: projected. `applies` used to find one by scanning every node for a
        #: matching suffix, which is quadratic — 18 million `str.endswith` calls
        #: on a real document once schema contents joined the graph.
        self.declared: dict[str, str] = {}
        #: `SecuritySchemeDefinition.id` -> its node IRI. Keyed on the parse's own
        #: counter rather than a name, because P5 has already bound each
        #: `securedBy:` entry to its definition and a name cannot tell two
        #: libraries' schemes apart. Not `id()`: the project forbids it, and this
        #: counter is unique per parse anyway (docs/02 § 3.1).
        self.scheme_iris: dict[int, str] = {}
        #: IRI → the `BaseShape.id` holding it. Two shapes given the same
        #: structural name would otherwise merge into one node in silence; see
        #: `claim`.
        self.claimed: dict[str, int] = {}
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

    def node(self, iri: str, entity: Entity, *kinds: str) -> str:
        """Create the node for `entity`, or return the one that already holds it.

        The entity is required and not optional, so that `mypy` rather than a
        test is what establishes that every node has one. A projection whose
        nodes are not all backed by something in the model has copied rather
        than projected (docs/16 section 1).
        """
        if iri not in self.nodes:
            self.nodes[iri] = GraphNode(iri=iri, kinds=kinds, entity=entity, root=self.root)
        return iri

    def declare(self, unit: str, bucket: str, name: str) -> str:
        """The IRI of one declaration, recorded so a name can be resolved to it.

        Registered where the declaration is *created* rather than recovered from
        the IRI afterwards. Sniffing for `#/declarations/` in `node` would have
        meant a substring search on all 58,000 node creations to re-learn
        something the caller already knew, and `startswith` cannot do it — the
        marker sits after the unit's own URI, not at the front.
        """
        iri = f'{unit}{_DECLARATIONS}{bucket}/{self.segment(name)}'
        self.declared.setdefault(iri[iri.index(_DECLARATIONS) :], iri)
        return iri

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

    def relative(self, location: str) -> str:
        return relative_to(location, self.root)

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
        if shape is not None and shape.id not in self.shape_iris:
            self.shape_iris[shape.id] = self.claim(iri, shape.id)

    def fragment(self, location: str, fragment: Fragment) -> None:
        """Every declaration a fragment holds, in declaration order.

        Only the two that declare anything. The `isinstance` is what narrows
        `Fragment` — a protocol with no `types` — to the pair that has them.
        """
        if not isinstance(fragment, (APIFragment, Library)):
            return
        unit = self.unit(location)
        self.node(unit, fragment, 'Unit')
        for name, shape in fragment.types.items():
            self.edge(unit, 'declares', self.shape(shape, self.declare(unit, 'types', name)))
        for name, shape in fragment.annotation_types.items():
            self.edge(unit, 'declares', self.shape(shape, self.declare(unit, 'annotations', name)))
        # Each of the three is positioned, like every other node. Without it the
        # location column is empty for exactly the declarations a reader most
        # often wants to open — a trait is applied far from where it is written.
        for name, scheme in fragment.security_schemes.items():
            iri = self.node(self.declare(unit, 'securitySchemes', name), scheme, 'SecurityScheme')
            # Under both the declaration and whatever an `!include` resolved to,
            # so a `securedBy:` bound to either finds this one node.
            self.scheme_iris[scheme.id] = iri
            self.scheme_iris[scheme.resolved().id] = iri
            self.edge(unit, 'declares', iri)
        for name, trait in fragment.traits.items():
            iri = self.node(self.declare(unit, 'traits', name), trait, 'Trait')
            self.edge(unit, 'declares', iri)
        for name, resource_type in fragment.resource_types.items():
            iri = self.node(self.declare(unit, 'resourceTypes', name), resource_type, 'ResourceType')
            self.edge(unit, 'declares', iri)

    def api(self) -> None:
        entry = self.raml.entry_point
        if not isinstance(entry, APIFragment):
            return
        api = self.node(f'{self.base}#/web-api', entry, 'Api')
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
        iri = self.node(f'{self.base}#/web-api/endpoint/{self.segment(endpoint.full_uri)}', endpoint, 'EndPoint')
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
        """A `type:`/`is:` reference, pointed at the declaration it names.

        The name may be qualified (`lib.collection`), and the declaration then
        lives in that library's unit rather than this one. Resolving it here
        would be re-implementing P4; instead the name is matched against what
        the projection has already declared.

        **The whole name is tried before the dotted tail.** A dot is not only a
        namespace separator: `securitySchemes: {oauth2.0: …}` is a declaration
        whose name contains one, and splitting first makes the tail `0`, which
        matches nothing. That fixture is in the corpus, and it produced an edge
        to a node that did not exist.

        An unmatched name still gets an edge, to a node created here, so an
        application is never invisible and no edge ever dangles.
        """
        name = ref.name
        if not name:
            return
        for candidate in (name, name.rsplit('.', 1)[-1]):
            found = self.declared.get(f'{_DECLARATIONS}{bucket}/{self.segment(candidate)}')
            if found is not None:
                self.edge(subject, predicate, found)
                return
        local = f'{self.unit(location)}#/declarations/{bucket}/{self.segment(name)}'
        self.edge(subject, predicate, self.node(local, ref, _DECLARED_KINDS[bucket]))

    def operation(self, endpoint: str, operation: Operation) -> None:
        iri = self.node(f'{endpoint}/supportedOperation/{self.segment(operation.method)}', operation, 'Operation')
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
        iri = self.node(f'{operation}/request', request, 'Request')
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
        iri = self.node(f'{operation}/returns/{self.segment(response.code)}', response, 'Response')
        self.edge(operation, 'returns', iri)
        self.annotated(iri, response.annotations)
        for name, param in response.headers.items():
            child = self.parameter(f'{iri}/parameter/header/{self.segment(name)}', param)
            self.edge(iri, 'parameter', child)
        for media, body in response.bodies.items():
            self.edge(iri, 'payload', self.payload(iri, media, body))

    def payload(self, parent: str, media: str, body: Body) -> str:
        iri = self.node(f'{parent}/payload/{self.segment(media or "default")}', body, 'Payload')
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
        self.node(iri, param, 'Parameter')
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
            target = self.scheme_iris.get(scheme.definition.id) if scheme.definition is not None else None
            if target is not None:
                self.edge(subject, 'securedBy', target)
            else:
                self.applies(subject, 'securedBy', 'securitySchemes', scheme, self.raml.location)

    def annotated(self, subject: str, annotations: dict[str, DomainExtension]) -> None:
        for name, extension in annotations.items():
            defined_by = extension.defined_by
            tail = name.rsplit('.', 1)[-1]
            wanted = f'#/declarations/annotations/{self.segment(tail)}'
            target = self.shape_iris.get(defined_by.id) if defined_by is not None else None
            if target is None:
                target = next((iri for iri in self.nodes if iri.endswith(wanted)), f'{self.base}{wanted}')
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
        iri = self.shape_iris.get(base.id)
        if iri is None:
            iri = self.shape_iris[base.id] = self.claim(fallback, base.id)
        if base.id in self.emitted:
            return iri
        self.emitted.add(base.id)

        kind = type(base.shape).__name__ if base.shape is not None else 'UnknownShape'
        self.node(iri, base, 'Type', kind)
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
                self.node(child, prop, 'Property')
                self.edge(child, 'range', self.shape(prop.base, f'{child}/schema'))
                self.edge(iri, 'property', child)
            for name, pattern in (shape.pattern_properties or {}).items():
                # Keyed by the `/regex/` as written, not by position: a pattern
                # property has no name of its own and an index would move under
                # any edit above it.
                child = f'{iri}/patternProperty/{self.segment(name)}'
                self.node(child, pattern, 'PatternProperty')
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
            head = self.shape_iris.get(shape.head.id)
            if head is not None:
                self.edge(iri, 'recursionHead', head)


# -- the literals, derived ----------------------------------------------------


def _attributes(node: GraphNode) -> dict[str, Literal_]:  # noqa: PLR0911, PLR0912 - one arm per node kind
    """One node's literals, read from the entity it projects.

    Dispatched on the node's *kind* rather than on the entity's class, because
    the two are not one-to-one: the entry document is the entity behind both
    the `Api` node and its `Unit` node, and they say different things about it.
    The kind is this layer's own vocabulary, which is what should be choosing.

    Every key here is a name docs/16 § 2 owns — `additionalProperties`,
    `statusCode`, `isAnnotationType` — and the model spells each of them
    differently. That mapping is the work this function exists to do, and it is
    why the projection keeps an attribute view at all: this layer owns the
    vocabulary, not the values (§ 2.8).
    """
    entity, kind = node.entity, node.kinds[0]
    if kind == 'Type':
        return _type_attributes(entity, node.root) if isinstance(entity, BaseShape) else {}
    if kind == 'Property':
        return {'name': entity.name, 'required': entity.required} if isinstance(entity, Property) else {}
    if kind == 'PatternProperty':
        return {'name': _pattern(entity), 'pattern': _pattern(entity)} if isinstance(entity, PatternProperty) else {}
    if kind == 'Parameter':
        if not isinstance(entity, Parameter):
            return {}
        bound: dict[str, Literal_] = {
            'name': entity.name,
            'binding': _BINDING[entity.binding],
            'required': entity.required,
        }
        return bound | _where(entity.base.location, entity.key_pos, node.root)
    if kind == 'Payload':
        if not isinstance(entity, Body):
            return {}
        media: dict[str, Literal_] = {'mediaType': entity.media_type} if entity.media_type else {}
        return media | _where(entity.location, entity.key_pos, node.root)
    if kind == 'Response':
        if not isinstance(entity, Response):
            return {}
        coded = _named({'statusCode': entity.code}, _text(entity.display_name) or entity.code, entity.description)
        return coded | _where(entity.location, entity.key_pos, node.root)
    if kind == 'Operation':
        return _operation_attributes(entity, node.root) if isinstance(entity, Operation) else {}
    if kind == 'EndPoint':
        if not isinstance(entity, EndPoint):
            return {}
        path = _named({'path': entity.full_uri}, _text(entity.display_name) or entity.full_uri, entity.description)
        return path | _where(entity.location, entity.key_pos, node.root)
    if kind == 'Api':
        if not isinstance(entity, APIFragment):
            return {}
        return _drop(
            {
                'name': _text(entity.title),
                'version': _text(entity.version),
                'description': _text(entity.description),
                'baseUri': _text(entity.base_uri),
            }
        )
    if kind == 'Unit':
        # No `definedIn`: a file is not defined somewhere else, and its name
        # already is its path relative to the root.
        return {'name': relative_to(entity.location, node.root)} if isinstance(entity, Fragment) else {}
    return _declared_attributes(entity, node.root)


def _declared_attributes(entity: Entity, root: str) -> dict[str, Literal_]:
    """A trait, a resource type or a security scheme.

    Two kinds of entity reach here per node kind. One is the definition, which
    knows where it was written. The other is a *reference* whose name matched no
    declaration — `applies` still emits an edge, so that an application is never
    invisible, and the placeholder it points at has a name and nothing else.
    """
    if isinstance(entity, SecuritySchemeDefinition):
        typed: dict[str, Literal_] = {'name': entity.name}
        if entity.type:
            typed['type'] = entity.type
        return typed | _where(entity.location, entity.key_pos, root)
    if isinstance(entity, (TraitDefinition, ResourceTypeDefinition)):
        return {'name': entity.name} | _where(entity.location, entity.key_pos, root)
    if isinstance(entity, (DirectiveRef, SecurityScheme)):
        return {'name': entity.name} if entity.name else {}
    return {}


def _type_attributes(base: BaseShape, root: str) -> dict[str, Literal_]:
    """A declaration: its common facets, then whatever its kind holds.

    The kind's facets are read off the instance rather than a per-kind table, so
    a facet added to a kind appears here without this module being touched —
    the same reason the golden projector walks `__slots__` (docs/14 § 2). They
    come from the *projected* shape, so a type defined by a JSON schema has them
    rather than reading as a leaf (§ 2.6); `as_shape` caches, so asking twice
    costs one dictionary build and no second projection.
    """
    found = _drop(
        {
            'name': base.name,
            'type': base.type,
            'displayName': _text(base.display_name),
            'description': _text(base.description),
            'required': _facet_value(base.required.value) if base.required is not None else None,
            'isAnnotationType': base.is_annotation_type or None,
        }
    )
    found.update(_where(base.location, base.key_pos, root))
    view = projected(base)
    shape = view.shape
    if shape is not None:
        # One of the two places `getattr` is genuinely required: the attribute
        # name comes from `_slots`, so there is no static expression for
        # "whatever this kind declares". The `isinstance` recovers the type.
        for name in _slots(type(shape)):
            value = getattr(shape, name, None)
            if isinstance(value, ScalarFacet):
                literal = _facet_value(value.value)
                if literal is not None:
                    found[_camel(name)] = literal
    if view.enum is not None:
        # `DataNode.raw` is the plain Python value already; unwrapping the
        # `ValueNode` by hand would only reproduce it.
        found['enum'] = tuple(str(member.raw) for member in view.enum)
    return found


def _operation_attributes(operation: Operation, root: str) -> dict[str, Literal_]:
    """A method, and what its security amounts to.

    `scopes` is a fold over every scheme in force, not the last one seen. The
    eager writer set it once per scheme inside the loop, so a method secured by
    two OAuth schemes kept only the second one's scopes.

    There is deliberately no `path`. The endpoint holds it and the
    `supportedOperation` edge reaches it, and a fact reachable by following an
    edge is not an attribute (§ 2.8). Storing it also made a moved resource
    report a change once per method beneath it rather than once at the edge.
    """
    found = _named(
        {'method': operation.method}, _text(operation.display_name) or operation.method, operation.description
    )
    found.update(_where(operation.location, operation.key_pos, root))
    scopes = tuple(
        scope for scheme in operation.secured_by if not scheme.is_null for scope in scheme.compiled_params or ()
    )
    if scopes:
        found['scopes'] = scopes
    if any(scheme.is_null for scheme in operation.secured_by):
        # `securedBy: [null]` is how an author *removes* inherited security
        # (docs/09 § A3). It is a fact about the method, so it is recorded on
        # the method rather than dropped for having no scheme to point at.
        found['unsecured'] = True
    return found


def _named(found: dict[str, Literal_], name: str, description: ScalarFacet[str] | None) -> dict[str, Literal_]:
    """`found`, plus the two things almost every kind says about itself."""
    if name:
        found['name'] = name
    text = _text(description)
    if text is not None:
        found['description'] = text
    return found


def _where(location: str, position: Position | None, root: str) -> dict[str, Literal_]:
    """Where an entity was written, spelled the way the vocabulary spells it."""
    found: dict[str, Literal_] = {'definedIn': relative_to(location, root)}
    if position is not None and position.is_known:
        found['line'] = position.line
        found['column'] = position.column
    return found


def _drop(found: dict[str, Literal_ | None]) -> dict[str, Literal_]:
    """A facet that is absent says nothing rather than saying `None`."""
    return {key: value for key, value in found.items() if value is not None}


# -- small readers ------------------------------------------------------------


def _slots(cls: type) -> Iterator[str]:
    """Every `__slots__` entry down the MRO.

    One of the two places `getattr` is genuinely required: `__slots__` is not
    declared by any base class in the hierarchy, `object` does not have it, and
    the names are what the walk is *for*.
    """
    for klass in cls.__mro__:
        yield from getattr(klass, '__slots__', ())


def _camel(name: str) -> str:
    head, _, rest = name.partition('_')
    return head + ''.join(part.title() for part in rest.split('_') if part)


def _text(facet: ScalarFacet[str] | None) -> str | None:
    return facet.value if facet is not None and facet.value else None


def _pattern(pattern: PatternProperty) -> str:
    return pattern.pattern.pattern


def _facet_value(value: object) -> str | int | bool | None:
    """One `ScalarFacet`'s value as a literal.

    `object` rather than a union: the facets are `ScalarFacet[T]` for seven
    different `T`, they are reached through the untyped `__slots__` walk above,
    and the point of this function is to be the one place that decides what an
    unrecognised `T` becomes.
    """
    if value is True or value is False:
        return value
    if isinstance(value, Fraction):
        return _number_text(value)
    if isinstance(value, (int, str)):
        return value
    return None
