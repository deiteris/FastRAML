---
name: sparql
description: Write your own SPARQL query against a RAML model with fastraml query. Covers the built-in catalogue, the urn:fastraml:ns:raml# namespace, all 14 node kinds and 22 edges, the three traps that produce plausible wrong answers (the range hop, aliasOf, and JSON Schema types), and the four output formats. Use when auditing a whole API document and no catalogue query fits the question.
license: MIT
allowed-tools: Bash(fastraml:*) Read
---

# Write your own `fastraml query`

Read this when no catalogue query fits and you need to write SPARQL against the
model. For a question about one named type or endpoint, use `refs`, `deps` or
`show` instead. They are faster, and they return routes, which SPARQL cannot.

Running a query needs `pyoxigraph`. Reading the catalogue does not.

## Start from a catalogue query

Seventeen queries ship with fastraml, and each one is a worked example of the
vocabulary. Read one before you write anything:

```bash
fastraml query --list                 # The names, and the question each answers
fastraml query --show unused-types    # Print one, to read or to copy and edit
```

```sparql
# Declared types that nothing references. Dead weight, or a missing wiring.
PREFIX raml: <urn:fastraml:ns:raml#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?unit ?name WHERE {
  ?u raml:declares ?t ; raml:name ?unit .
  ?t a raml:Type ; raml:name ?name .
  FILTER NOT EXISTS { ?s ?p ?t . FILTER(?p != raml:declares) }
}
ORDER BY ?unit ?name
```

Then run your edited version:

```bash
fastraml query -w . api.raml -Q my-query.rq
fastraml query -w . api.raml -q 'PREFIX raml: <urn:fastraml:ns:raml#> SELECT ...'
```

fastraml checks an `-n NAME` against the catalogue before it opens the document,
so a typo reports the typo rather than a parse error.

## Namespace

The namespace is `urn:fastraml:ns:raml#`.

It is a URN rather than an HTTP address because the project claims no domain
name, and a namespace that resolves to a 404 is worse than one that never
promised to resolve.

Treat the string as provisional before version 1.0. If you are writing something
long-lived in Python, read `RAML_NS` from the package instead of hard-coding it.

## Node kinds

`Unit`, `Api`, `EndPoint`, `Operation`, `Request`, `Response`, `Payload`,
`Parameter`, `Property`, `PatternProperty`, `Type`, `SecurityScheme`, `Trait`,
`ResourceType`.

A `Type` node carries a second kind that names its shape class, such as
`ObjectShape`, `ArrayShape`, `UnionShape`, `StringShape` or `RecursiveShape`.
The kinds run most specific first: `kinds[0]` is the category and `kinds[1]` is
the RAML type kind.

## Edges

| Predicate | From | To |
| --- | --- | --- |
| `declares` | `Unit` | any declaration |
| `unit` | `Api` | `Unit` |
| `endpoint` | `Api` | `EndPoint` |
| `parent` | `EndPoint` | `EndPoint` |
| `supportedOperation` | `EndPoint` | `Operation` |
| `request` | `Operation` | `Request` |
| `returns` | `Operation` | `Response` |
| `payload` | `Request` or `Response` | `Payload` |
| `parameter` | `EndPoint`, `Request` or `Response` | `Parameter` |
| `queryString` | `Request` | `Type` |
| `range` | `Payload`, `Parameter`, `Property` or `PatternProperty` | `Type` |
| `property` | `Type` | `Property` |
| `patternProperty` | `Type` | `PatternProperty` |
| `items` | `Type` | `Type` |
| `anyOf` | `Type` | `Type` |
| `inherits` | `Type` | `Type` |
| `aliasOf` | `Type` | `Type` |
| `recursionHead` | `Type` | `Type` |
| `appliesTrait` | `EndPoint` or `Operation` | `Trait` |
| `appliesResourceType` | `EndPoint` | `ResourceType` |
| `securedBy` | `EndPoint` or `Operation` | `SecurityScheme` |
| `annotation` | anything annotated | `Type` |

Four of these need a note:

- `declares` points from the file a declaration was **written in**, not from the
  map that named it.
- `endpoint` is flat. It reaches every resource, not just the top-level ones.
  Use `parent` when you want the nesting.
- `request` is missing when the method sends nothing.
- `securedBy` reflects security after inheritance, and `appliesTrait` and
  `appliesResourceType` reflect templates that fastraml has already applied.

## Three traps

### `range` is the hop people forget

A `Parameter`, `Property` and `Payload` are nodes in their own right, not edges
straight to a type. That extra hop matters: `required`, the binding
(`path`, `query` or `header`) and the media type belong to the **use** of a
type, not to the type. The same declared type can be a required path parameter
in one place and an optional header in another.

So the edge you want is always `raml:range`:

```sparql
?prop raml:range ?type .
```

### `aliasOf` is not optional

`User[]` does not put the declaration of `User` under `items`. It puts an
*alias* of `User` there.

A query that skips `aliasOf` therefore stops one hop short of every array member
type, and reports the member's **supertypes** instead of the member. The answer
looks plausible and is wrong.

Allow `raml:aliasOf*` in any path that reaches a type:

```sparql
?payload raml:range/raml:aliasOf* ?type .
```

### A JSON Schema type is not a leaf

Types defined by an external JSON schema project their properties, items and
members like any other type, so a query that walks `raml:property` reaches
inside them. Do not filter them out.

## Output formats

All four SPARQL result shapes work:

- `SELECT` prints TSV, or JSON Lines with `--json`. An unbound `OPTIONAL` gives
  you an empty column, or `null` in JSON.
- `ASK` prints `true` or `false`, or `{"ask": true}` with `--json`.
- `CONSTRUCT` and `DESCRIBE` print N-Triples.

## What the vocabulary is not

The vocabulary holds about thirty terms, and fastraml adds one only when it makes
a real question easier to ask. There are no `rdfs:subClassOf` axioms, no
`owl:Restriction` and no inference. Nothing here needs a reasoner, so write your
alternations out in full.
