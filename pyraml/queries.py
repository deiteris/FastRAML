"""A catalogue of analysis queries over the graph — docs/16-graph.md § 6.

These exist to answer a question about the projection itself: **do real
analysis queries become materially simpler than the equivalent code against the
model?** That was the test the graph was built to be judged by, and a catalogue
that runs is the only honest way to run it.

Every entry is a whole-document question. Parameterised navigation — *what uses
`User`*, *what is `User` made of* — is `refs` and `deps`, which return a route
rather than a hit and are not expressible as a property path at all
(docs/16 § 5). The division is deliberate: a query here takes no arguments, so
`pyraml query -n <name>` needs nothing but a file.

Importing this module does **not** require `pyoxigraph`. The queries are text;
only running them needs a store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pyraml.graph import RAML_NS

__all__ = ['QUERIES', 'Query', 'render']

PREFIX: Final = f'PREFIX raml: <{RAML_NS}>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n'

#: The alternation that closes over "what a type is made of", as a SPARQL path.
#: Generated from `TYPE_EDGES` would be tidier and is not done: a query is text a
#: user copies out and edits, and one built by string arithmetic cannot be read
#: in the output of `--list`. `tests/unit/test_queries.py` asserts the two agree.
REACHES: Final = (
    'raml:range|raml:items|raml:anyOf|raml:inherits|raml:property|raml:patternProperty|raml:aliasOf|raml:recursionHead'
)

#: From an operation to the shapes it carries, in either direction.
CARRIES: Final = '(raml:request|raml:returns)/(raml:payload|raml:parameter)/raml:range'


@dataclass(frozen=True, slots=True)
class Query:
    """One named question, its SPARQL, and what a row means."""

    name: str
    question: str
    sparql: str


def render(query: Query) -> str:
    """The query with its prefixes, ready for a store."""
    return PREFIX + query.sparql


def _q(name: str, question: str, sparql: str) -> Query:
    return Query(name=name, question=question, sparql=sparql.strip() + '\n')


_CATALOGUE: Final = [
    # -- dead weight ----------------------------------------------------------
    _q(
        'unused-types',
        'Declared types that nothing references. Dead weight, or a missing wiring.',
        """
SELECT ?unit ?name WHERE {
  ?u raml:declares ?t ; raml:name ?unit .
  ?t a raml:Type ; raml:name ?name .
  # Any incoming edge other than the declaration itself counts as a use, so
  # this needs no list of the ways a type can be referenced.
  FILTER NOT EXISTS { ?s ?p ?t . FILTER(?p != raml:declares) }
}
ORDER BY ?unit ?name
""",
    ),
    _q(
        'trait-usage',
        'Every trait and how many operations apply it. Zero means it is dead.',
        """
SELECT ?name (COUNT(?op) AS ?uses) WHERE {
  ?trait a raml:Trait ; raml:name ?name .
  OPTIONAL { ?op raml:appliesTrait ?trait }
}
GROUP BY ?name
ORDER BY ?uses ?name
""",
    ),
    _q(
        'annotation-usage',
        'Every annotation type and how many sites apply it.',
        """
SELECT ?name (COUNT(?site) AS ?uses) WHERE {
  ?a a raml:Type ; raml:isAnnotationType true ; raml:name ?name .
  OPTIONAL { ?site raml:annotation ?a }
}
GROUP BY ?name
ORDER BY DESC(?uses) ?name
""",
    ),
    # -- blast radius ---------------------------------------------------------
    _q(
        'type-fan-in',
        'Declared types ranked by how many operations can carry them. The blast radius of a change.',
        f"""
SELECT ?unit ?name (COUNT(DISTINCT ?op) AS ?operations) WHERE {{
  ?u raml:declares ?t ; raml:name ?unit .
  ?t a raml:Type ; raml:name ?name .
  ?op a raml:Operation .
  ?op {CARRIES}/({REACHES})* ?t .
}}
GROUP BY ?t ?unit ?name
ORDER BY DESC(?operations) ?unit ?name
""",
    ),
    _q(
        'multiple-inheritance',
        'Types with more than one direct supertype, where the merge rules are hardest to predict.',
        """
SELECT ?unit ?name (COUNT(?parent) AS ?parents) WHERE {
  # Declared types only. Every response body that resolves to a multi-parent
  # type has the same parents, so the un-restricted form reports one row per
  # *use* of the problem instead of one row per problem — and labels most of
  # them `application/json`, which names nothing an author can go and fix.
  ?u raml:declares ?t ; raml:name ?unit .
  ?t a raml:Type ; raml:name ?name ; raml:inherits ?parent .
}
GROUP BY ?t ?unit ?name
HAVING (COUNT(?parent) > 1)
ORDER BY DESC(?parents) ?unit ?name
""",
    ),
    _q(
        'recursive-types',
        'Types that close a cycle. Every consumer generating code from these needs to know.',
        """
SELECT DISTINCT ?name WHERE {
  ?marker raml:recursionHead ?head .
  ?head raml:name ?name .
}
ORDER BY ?name
""",
    ),
    # -- documentation and security ------------------------------------------
    _q(
        'undocumented-operations',
        'Operations with no description. The commonest governance rule there is.',
        """
SELECT ?path ?method WHERE {
  ?ep a raml:EndPoint ; raml:path ?path ; raml:supportedOperation ?op .
  ?op raml:method ?method .
  FILTER NOT EXISTS { ?op raml:description ?any }
}
ORDER BY ?path ?method
""",
    ),
    _q(
        'unsecured-operations',
        'Operations reachable with no security scheme, after inheritance and after securedBy: [null].',
        """
SELECT ?path ?method WHERE {
  ?ep a raml:EndPoint ; raml:path ?path ; raml:supportedOperation ?op .
  ?op raml:method ?method .
  FILTER NOT EXISTS { ?op raml:securedBy ?scheme }
}
ORDER BY ?path ?method
""",
    ),
    _q(
        'scheme-usage',
        'Every security scheme and the operations it guards, with any narrowed scopes.',
        """
SELECT ?scheme ?path ?method ?scopes WHERE {
  ?s a raml:SecurityScheme ; raml:name ?scheme .
  OPTIONAL {
    ?ep raml:supportedOperation ?op .
    ?ep raml:path ?path .
    ?op raml:securedBy ?s ; raml:method ?method .
    OPTIONAL { ?op raml:scopes ?scopes }
  }
}
ORDER BY ?scheme ?path ?method
""",
    ),
    # -- payloads -------------------------------------------------------------
    _q(
        'error-response-types',
        'Every 4xx and 5xx response and the type it returns. Does each use the standard error type?',
        """
SELECT ?path ?method ?code ?type WHERE {
  ?ep a raml:EndPoint ; raml:path ?path ; raml:supportedOperation ?op .
  ?op raml:method ?method ; raml:returns ?r .
  ?r raml:statusCode ?code .
  FILTER(STRSTARTS(?code, "4") || STRSTARTS(?code, "5"))
  OPTIONAL { ?r raml:payload/raml:range/raml:inherits/raml:name ?type }
}
ORDER BY ?path ?method ?code
""",
    ),
    _q(
        'untyped-payloads',
        'Payloads whose type is `any` — a body that constrains nothing.',
        """
SELECT ?path ?method ?media WHERE {
  ?ep a raml:EndPoint ; raml:path ?path ; raml:supportedOperation ?op .
  ?op raml:method ?method .
  ?op (raml:request|raml:returns)/raml:payload ?payload .
  ?payload raml:range ?shape .
  ?shape a raml:AnyShape .
  OPTIONAL { ?payload raml:mediaType ?media }
}
ORDER BY ?path ?method
""",
    ),
    _q(
        'media-types',
        'Which media types the document actually uses, and how often. Inconsistency shows up as a long tail.',
        """
SELECT ?media (COUNT(?payload) AS ?payloads) WHERE {
  ?payload a raml:Payload ; raml:mediaType ?media .
}
GROUP BY ?media
ORDER BY DESC(?payloads) ?media
""",
    ),
    _q(
        'get-with-request-body',
        'GET operations that declare a request payload. Spec-legal, and almost always a mistake.',
        """
SELECT ?path ?media WHERE {
  ?ep a raml:EndPoint ; raml:path ?path ; raml:supportedOperation ?op .
  ?op raml:method "get" ; raml:request/raml:payload ?payload .
  OPTIONAL { ?payload raml:mediaType ?media }
}
ORDER BY ?path
""",
    ),
    # -- constraints ----------------------------------------------------------
    _q(
        'required-query-parameters',
        'Required query parameters. Each one is a request that fails without it.',
        """
SELECT ?path ?method ?name WHERE {
  ?ep a raml:EndPoint ; raml:path ?path ; raml:supportedOperation ?op .
  ?op raml:method ?method ; raml:request/raml:parameter ?p .
  ?p raml:binding "query" ; raml:required true ; raml:name ?name .
}
ORDER BY ?path ?method ?name
""",
    ),
    _q(
        'unbounded-strings',
        'String properties with no maxLength, pattern or enum. The usual input-validation gap.',
        """
SELECT ?unit ?owner ?property WHERE {
  # Declared types only, as `multiple-inheritance` explains: fixing the
  # declaration fixes every body that inherits it, so the declaration is the
  # actionable row.
  ?u raml:declares ?t ; raml:name ?unit .
  ?t a raml:Type ; raml:name ?owner ; raml:property ?p .
  ?p raml:name ?property ; raml:range ?s .
  ?s a raml:StringShape .
  FILTER NOT EXISTS { ?s raml:maxLength ?max }
  FILTER NOT EXISTS { ?s raml:pattern ?pattern }
  FILTER NOT EXISTS { ?s raml:enum ?enum }
}
ORDER BY ?unit ?owner ?property
""",
    ),
    _q(
        'enums',
        'Every closed value set in the document: one row per permitted value.',
        """
# One row per value, not one per set. `raml:enum` is multi-valued, because an
# enum member may contain a space and joining them would lose the boundaries.
SELECT ?name ?value WHERE {
  ?t raml:enum ?value .
  OPTIONAL { ?t raml:name ?name }
}
ORDER BY ?name ?value
""",
    ),
    _q(
        'endpoint-tree',
        'Every resource and its methods, in path order. The table of contents.',
        """
SELECT ?path ?method WHERE {
  ?ep a raml:EndPoint ; raml:path ?path .
  OPTIONAL { ?ep raml:supportedOperation/raml:method ?method }
}
ORDER BY ?path ?method
""",
    ),
]

#: Keyed by name, in catalogue order.
QUERIES: Final[dict[str, Query]] = {query.name: query for query in _CATALOGUE}
