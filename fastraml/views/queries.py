"""The named SPARQL queries behind `fastraml query -n` (docs/16-graph.md § 3.1).

Every entry is a whole-document question. Parameterised navigation — *what uses
`User`*, *what is `User` made of* — is `refs` and `deps`, which return a route
rather than a hit and are not expressible as a property path at all
(docs/16 § 3). The division is deliberate: a query here takes no arguments, so
`fastraml query -n <name>` needs nothing but a file.

Importing this module does **not** require `pyoxigraph`. The queries are text;
only running them needs a store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fastraml.views.graph import RAML_NS

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
