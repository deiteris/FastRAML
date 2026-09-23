---
name: sparql
description: Write your own SPARQL query against a RAML model with fastraml query. Covers the built-in catalogue, the urn:fastraml:ns:raml# namespace, all 14 node kinds and 22 edges, the three traps that produce plausible wrong answers (the range hop, aliasOf, and JSON Schema types), and the four SPARQL result shapes. Use when auditing a whole API document and no catalogue query fits the question. Not for one named type or endpoint (fastraml refs, deps, show) or for defects with a severity (fastraml lint).
license: MIT
allowed-tools: Bash(fastraml:*) Read
---

# Write your own `fastraml query`

Read this when no catalogue query fits and you need to write SPARQL against the
model. For a question about one named type or endpoint, use `refs`, `deps` or
`show` instead. They are faster, and they return routes, which SPARQL cannot.

Running a query needs `pyoxigraph`. Reading the catalogue does not.

## Start from a catalogue query

Nine queries ship with fastraml, and each one is a worked example of the
vocabulary. Read one before you write anything:

```bash
fastraml query --list                 # The names, and the question each answers
fastraml query --show type-fan-in     # Print one, to read or to copy and edit
```

```sparql
# Declared types ranked by how many operations can carry them.
PREFIX raml: <urn:fastraml:ns:raml#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?unit ?name (COUNT(DISTINCT ?op) AS ?operations) WHERE {
  ?u raml:declares ?t ; raml:name ?unit .
  ?t a raml:Type ; raml:name ?name .
  ?op a raml:Operation .
  ?op (raml:request|raml:returns)/(raml:payload|raml:parameter)/raml:range/(raml:range|raml:items|raml:anyOf|raml:inherits|raml:property|raml:patternProperty|raml:aliasOf|raml:recursionHead)* ?t .
}
GROUP BY ?t ?unit ?name
ORDER BY DESC(?operations) ?unit ?name
```

**The catalogue holds reports, not judgements.** Anything with a severity and a
right answer — unused types, unsecured operations, unbounded strings — is a lint
rule rather than a query, and `fastraml lint` reports it with an exit code. Run
`fastraml skills get lint` before writing a query that grades something.

Then run your edited version:

```bash
fastraml query -w . api.raml -Q my-query.rq
fastraml query -w . api.raml -q 'PREFIX raml: <urn:fastraml:ns:raml#> SELECT ...'
```

On Windows PowerShell (5.1), an inline `-q` argument containing double quotes
— string literals, so almost any useful query — arrives with the quotes
mangled, and the SPARQL parser fails with `expected ENCODE_FOR_URI`. Put the
query in a file and use `-Q`; it is not mangled. Note that `Set-Content
-Encoding utf8` on that same PowerShell writes a BOM, which the parser also
rejects (`expected CONSTRUCT` at 1:10) — save the file as UTF-8 without BOM.

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

A `Type` node carries a second `rdf:type` that names its shape class, such as
`ObjectShape`, `ArrayShape`, `UnionShape`, `StringShape`, `JsonShape` or
`RecursiveShape`, so `?t a raml:ObjectShape` selects object types. In
`fastraml graph --format json` the same pair is the node's `kinds`: `kinds[0]`
is `Type` and `kinds[1]` the shape class.

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

Five of these need a note:

- `declares` points from the file a declaration was **written in**, not from the
  map that named it.
- `endpoint` is flat. It reaches every resource, not just the top-level ones.
  Use `parent` when you want the nesting.
- `request` is missing when the method sends nothing.
- `securedBy` reflects security after inheritance, and `appliesTrait` and
  `appliesResourceType` reflect templates that fastraml has already applied.
- `queryString` is the operation's `queryString:` facet — one type describing
  the whole query string, mutually exclusive with `queryParameters`. Named
  query parameters never flow through it: they are `Parameter` nodes with
  `binding "query"`, reached via `parameter`.

## Data properties

Edges connect nodes; the literals on a node are how you *filter* them. They are
predicates in the same `raml:` namespace, so the one `PREFIX` covers both. The
ones a query reaches for:

| Predicate | On | Carries |
| --- | --- | --- |
| `name` | most declarations | the name it is declared or referenced by |
| `type` | `Type`, `SecurityScheme` | the declared type (`string`, `object`, …) or the scheme type |
| `binding` | `Parameter` | `"path"` (RAML's `uriParameters`), `"query"` or `"header"` |
| `required` | `Parameter` and `Property` always, `Type` when present | `true`/`false` (an `xsd:boolean`) |
| `path` | `EndPoint` | the resource URI, e.g. `/tenants` |
| `method` | `Operation` | `get`, `post`, … |
| `statusCode` | `Response` | e.g. `200` |
| `mediaType` | `Payload` | e.g. `application/json` |
| `isAnnotationType` | `Type` | `true` on annotation types |
| `scopes` | `Operation` | the scopes in force after `securedBy`, when any; one triple per scope |
| `unsecured` | `Operation` | `true` when a `null` entry in `securedBy` lets the operation be called with no scheme |
| `version`, `baseUri` | `Api` | the API's version and base URI |
| `description` | `EndPoint`, `Operation`, `Response`, `Type`, `Api` | text, when present |
| `displayName` | `Type` | text, when present — a separate attribute from `name` |
| `definedIn` | most | the unit it was written in, relative to the root |
| `line`, `column` | most | the source position, only when known |

`definedIn`, `line` and `column` are absent on `Unit` (whose name already is
its path), `Api`, and `Property`/`PatternProperty` (projected without a
position). To see every attribute a node kind carries, run
`fastraml graph --format nt` and read one node of that kind.

A `Type`'s shape facets are literals on the `Type` node itself, not under a
sub-node. `enum` (one triple per member) can appear on any kind; the rest
depend on the shape:

- strings: `minLength`, `maxLength`, `pattern`; files: `minLength`, `maxLength`, `fileTypes`
- numbers and integers: `minimum`, `maximum`, `multipleOf`, `format`; date-time: `format`
- arrays: `minItems`, `maxItems`, `uniqueItems`
- objects: `minProperties`, `maxProperties`, `additionalProperties`,
  `discriminator`; `discriminatorValue` is not projected

Structure is not a facet: `properties`, `items`, `anyOf`, pattern properties
and a recursive type's head are edges (`property`, `items`, `anyOf`,
`patternProperty`, `recursionHead`).

Two properties need a warning.

**`name` is not universal.** It is present on `Unit`, `EndPoint`, `Operation`,
`Parameter`, `Property`, `Trait`, `ResourceType` and `SecurityScheme`, and on a
`Type` only when the shape is named — an inline or anonymous shape has none. A
`Payload` never has a `name`; its identity is the `mediaType`. A query that
filters `?n raml:name "x"` therefore skips anonymous types and all payloads.
Filter by kind first, then by name.

**Read the URI from `path`, not `name`.** An `EndPoint`'s `name` is its
`displayName` when it has one and the URI only as a fallback; `path` is always
the URI. An `Operation`'s `name` is likewise its `displayName` (else the verb);
`method` is always the verb.

So "which endpoints carry a `uuids` query parameter" is an edge walk plus two
property filters — the pattern for most parameter questions:

```sparql
PREFIX raml: <urn:fastraml:ns:raml#>
SELECT DISTINCT ?endpoint WHERE {
  ?ep a raml:EndPoint ; raml:path ?endpoint .
  {
    ?ep raml:parameter ?p .   # declared on the resource: applies to every method
  }
  UNION
  {
    ?ep raml:supportedOperation ?op .
    ?op raml:request ?req .
    ?req raml:parameter ?p .
  }
  ?p a raml:Parameter ; raml:name "uuids" ; raml:binding "query" .
}
ORDER BY ?endpoint
```

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

Read the default output directly. Use `--json` only when the task requires a
program to parse `SELECT` or `ASK` results.

- `SELECT` prints TSV. An unbound `OPTIONAL` gives an empty column.
- `ASK` prints `true` or `false`.
- `CONSTRUCT` and `DESCRIBE` print N-Triples.

## What the vocabulary is not

The vocabulary is the node kinds, edges and literals above; fastraml adds a
term only when it makes a real question easier to ask. There are no `rdfs:subClassOf` axioms, no
`owl:Restriction` and no inference. Nothing here needs a reasoner, so write your
alternations out in full.
