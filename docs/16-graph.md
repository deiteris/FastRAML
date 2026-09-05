# 16. The graph projection

**Status: built.** `pyraml/graph.py`, and the `graph` / `refs` / `deps` / `query`
verbs of the CLI ([13](13-public-api.md) § 8).

This document owns one area: turning the parsed model into something you can
*ask questions of*. It settles the vocabulary, the IRI scheme, what is projected
and what deliberately is not, and why the AMF metamodel was assessed and not
adopted.

## 1. The problem, and what is not the problem

RAML's syntax is a tree; its semantics are a graph. Types inherit from types,
properties reference types, resources instantiate resource types, methods apply
traits, libraries introduce namespaced declarations. Reading a document set means
resolving those edges by hand, and the effort scales with how much resolution the
reader has to perform mentally — which for traits, resource types and
multiple inheritance is a lot.

**The parser already does that resolution.** After P9 the model *is* the
effective graph: what remains is projecting it into a form where following an
edge is a lookup rather than a visitor.

So this module is a projection and nothing else. It contains no RAML rule, no
merge, no name resolution, no inference. If a question about the graph turns into
a question about what RAML means, the answer belongs in another document and
another pass. The dependency runs one way — `graph.py` imports the model and
nothing in the model imports `graph.py`.

### 1.1 Effective, not declared

Build it from a parse made with `ParseOptions(unwrap=True)`. That is what makes a
resource type's `<<itemType>>[]` appear as an array of the type the caller
actually bound, rather than as an unresolved template variable.

There is **one graph, of the effective model**, not two graphs with
`declared*`/`effective*` edge pairs. Two reasons, and the second is decisive:

- The declared form has not vanished. `inherits` survives P9 (`unwrap.py`
  `_unwrap_parents` rewrites the list to the flattened parents rather than
  clearing it), so `raml:inherits` still links a use site to the declaration it
  names. Asking *where does `id` come from* is a reverse walk over that edge.
- P9 mutates in place. `_unwrap` sets `_unwrapped` on the shape it was given and
  `unwrap_shapes` replaces entries in `Raml.shapes`; the pre-unwrap edges are
  gone by the time anything could project them. Carrying both would mean two
  parses, and `BaseShape.id` is unique *within one parse* — so there would be no
  join between the two graphs, only two disconnected ones.

If the declared view is ever wanted, it is a second `build_graph` call on a
second parse with `unwrap=False`, kept in a separate graph and correlated by IRI.
The IRI scheme in § 3 is structural precisely so that this stays possible.

## 2. The vocabulary

Small and RAML-specific: about thirty terms. This is a **vocabulary, not an
ontology** — there are no `rdfs:subClassOf` axioms, no `owl:Restriction`, no
inference regime. Nothing here needs a reasoner, and a term is added when it
makes a real question easier to ask, not because the model has a field.

Namespace: `urn:pyraml:ns:raml#`, exported as `RAML_NS`.

A URN rather than an `http(s)` IRI, deliberately. This project claims no domain
name, and a namespace that resolves to a 404 is worse than one that never
promised to resolve. It is **provisional until 1.0** — an early consumer should
read `RAML_NS` rather than hard-code the string.

### 2.1 Node kinds

`Unit`, `Api`, `EndPoint`, `Operation`, `Request`, `Response`, `Payload`,
`Parameter`, `Property`, `PatternProperty`, `Type`, `SecurityScheme`, `Trait`,
`ResourceType`.

A `Type` node carries a second kind naming the shape class — `ObjectShape`,
`ArrayShape`, `UnionShape`, `StringShape`, `RecursiveShape` and the rest — so
`kinds[0]` is the category and `kinds[1]` is the RAML type kind. Most-specific
first, as AMF's `@type` arrays are.

### 2.2 Edges

| Predicate | From | To | Notes |
|---|---|---|---|
| `declares` | `Unit` | any declaration | one per `types:`/`traits:`/… entry |
| `unit` | `Api` | `Unit` | the document the API root was written in |
| `endpoint` | `Api` | `EndPoint` | flat: every resource, not only top-level |
| `parent` | `EndPoint` | `EndPoint` | nesting, for the tree view |
| `supportedOperation` | `EndPoint` | `Operation` | |
| `request` | `Operation` | `Request` | omitted when the method sends nothing |
| `returns` | `Operation` | `Response` | |
| `payload` | `Request`/`Response` | `Payload` | one per media type |
| `parameter` | `EndPoint`/`Request`/`Response` | `Parameter` | binding on the node |
| `queryString` | `Request` | `Type` | mutually exclusive with query parameters |
| `range` | `Payload`/`Parameter`/`Property`/`PatternProperty` | `Type` | **the uniform "has this type" edge** |
| `property` | `Type` | `Property` | |
| `patternProperty` | `Type` | `PatternProperty` | |
| `items` | `Type` | `Type` | array member type |
| `anyOf` | `Type` | `Type` | union member |
| `inherits` | `Type` | `Type` | survives unwrap; the link back to a declaration |
| `aliasOf` | `Type` | `Type` | one type under a second name (07 § 3.6) |
| `recursionHead` | `Type` | `Type` | the back-edge a `RecursiveShape` marks |
| `appliesTrait` | `EndPoint`/`Operation` | `Trait` | as written; already applied |
| `appliesResourceType` | `EndPoint` | `ResourceType` | as written; already applied |
| `securedBy` | `EndPoint`/`Operation` | `SecurityScheme` | after inheritance |
| `annotation` | anything annotated | `Type` | the annotation *type* it bound to |

### 2.3 Why `range` and not a direct edge

A `Parameter`, a `Property` and a `Payload` are nodes of their own rather than
edges straight to a type, and the extra hop is load-bearing: `required`, the
binding (`path`/`query`/`header`) and the media type belong to the **use**, not
to the type. The same declared type is a required path parameter here and an
optional header there. Collapsing the node would have to put those on the type,
which is wrong, or drop them, which loses the question.

Every such node reaches its type through the same predicate, `range`, so a
traversal spells the alternation once.

### 2.4 `aliasOf` is not optional, and that is not obvious

`User[]` does **not** put the declaration of `User` under `items`. It puts an
*alias* of `User` there — a distinct shape that shares the referent's containers
(docs/07 § 3.6). So a traversal closure that omits `aliasOf` stops one hop short
of every array member type and reports the member's **supertypes** instead of the
member, which looks like a plausible answer and is the wrong one.

`TYPE_EDGES` therefore includes `aliasOf` and `recursionHead`. This was found by
a test asserting that an operation returning `UserList` reaches `User`; it did
not, and reached `Entity` instead.

### 2.5 Literals

Names, positions and every `ScalarFacet` the shape kind holds. The facets are
read off the instance by walking `__slots__`, not from a per-kind table — the
same discipline `tests/golden/project.py` uses and for the same reason: a facet
added to a kind must appear here without this module being edited, or the
projection silently omits new information.

Numeric facets are `Fraction`s and are rendered by exact decimal expansion, never
through `float`. The project rule (docs/10 § 5.2) is about comparison, but a
graph literal reading `1.100000000000000088` would be a defect of this module
regardless of whether anything compares it.

## 3. The IRI scheme

Node IRIs are **structural**: a path describing where the entity sits, not an
opaque identifier.

```
pyraml://id#/declarations/types/User
pyraml://id/lib.raml#/declarations/types/Address
pyraml://id#/web-api/endpoint/%2Fusers/supportedOperation/get/returns/200
pyraml://id#/web-api/endpoint/%2Fusers/supportedOperation/get/returns/200/payload/application%2Fjson/schema
```

Three properties, each of which cost something to get:

**Stable across runs.** `BaseShape.id` is a per-parse counter, so a graph keyed
on it would be meaningless in a diff, a cache or a committed query result. A
structural path depends only on the source.

**Machine-independent.** The root is `pyraml://id`, mirroring AMF's `amf://id`
for the same reason. A declaration authored outside the entry document gets its
unit's path *relative to the entry document's directory* — `pyraml://id/lib.raml#…`
— so no absolute filesystem path enters the graph. A file outside that directory
keeps its whole URI, which is rare and visibly different when it happens.

**Assigned once.** A shape reached twice keeps its first IRI. That is what makes
a declared type one node rather than one node per use site, and what closes a
type cycle. It also makes **walk order part of the contract**: declarations are
registered before endpoints are walked, so `User` lands at
`#/declarations/types/User` and not at whichever response body happened to reach
it first. go-raml's converter pre-registers for exactly this reason
(`converter/jsonld.go`, `preRegisterTypes`).

Every segment is percent-escaped with an empty safe set, so a media type, a
`/{userId}` template and a `/^x-/` pattern-property name each survive as one
segment.

### 3.1 Names are not unique, and a collision is silent

A structural IRI is derived from *names*, and RAML does not promise the names
are distinct. `type1: [string, string]` gives two parents the same one. Without
disambiguation the second merges into the first: no error is raised, the node
count is plausible, and **two types have become one**.

`_Builder.claim` therefore records which shape holds each IRI and appends `/!2`,
`/!3` … to a name already taken. The suffix begins with `!`, which segment
escaping always percent-encodes, so a disambiguated IRI can never collide with
one a real name produced.

This is not a hypothetical. go-raml's converter carries a test for exactly the
same hazard — `TestJSONLD_NoDuplicateIDs`, "a regression net for intermediate
`*BaseShape` objects that bypass `shapeIDs` registration and accidentally claim
a contextID already in use". Taking that seriously found the same hole here, in
one corpus fixture (`EdgeCases/inherit-multiple-scalars`). The corpus law is
docs/14 § 4, law 12.

### 3.2 A dot is not always a namespace separator

`type: lib.Collection` is a qualified reference; `securitySchemes: {oauth2.0: …}`
is a declaration whose *name* contains a dot. A reference is therefore matched
against the whole name first and only then against the dotted tail — splitting
first makes the tail `0`, which matches nothing.

An unmatched reference still gets an edge, to a node created at that moment, so
an application is never invisible and **no edge ever dangles**. That last part is
also a corpus law: an edge to a node that does not exist is a traversal that
silently ends early.

### 3.1 Ordering

RDF is a set of triples and declaration order is an invariant everywhere the
model is exposed (docs/02 § 4). The two are reconciled by **not** reconciling
them: the in-memory `Graph` preserves order — `nodes` is insertion-ordered and
`out()` returns edges as they were added — and the RDF serialisations do not,
because encoding order as `rdf:List` would put `rdf:rest*/rdf:first` into every
query for a property nothing here needs.

Order is therefore available to the Python API and to `--format json`, and absent
from Turtle and N-Triples. Where order matters, read the graph directly.

## 4. What is deliberately not projected

- **Examples and default values.** Data, not structure; `Examples.entries()` is
  the way to read them and the golden layer already pins them.
- **Trait and resource-type *bodies*.** They have been applied; the applied
  result is projected on the operation. `appliesTrait` records that a trait was
  used, which is the question anyone asks about it.
- **Documentation items, `uses:` prefixes, protocols, `baseUriParameters`.**
  Nothing has needed them yet. § 2's rule applies: add a term when a question
  wants it.
- **Diagnostics.** A graph of a document that failed to parse is not built at
  all; the CLI reports the error instead.

## 5. Queries, and the limits of property paths

`Graph` answers questions directly:

```python
graph = build_graph(parse_from_path('api.raml', ParseOptions(unwrap=True)))
user, = graph.find('User', kinds=['Type'])
for path in graph.walk(user, USE_EDGES, reverse=True):
    print(graph.kind_of(path.target), [graph.label(n) for n in path.nodes])
```

`walk` is breadth-first and returns a `Route` per node reached: the nodes, and the
predicate taken at each hop.

**That return type is the reason not to make SPARQL the only interface.** SPARQL
1.1 property paths are excellent at reachability and cannot bind intermediate
nodes — `?a (raml:property/raml:range|raml:items)* ?b` answers *whether* `b` is
reachable and says nothing about what lay between, because a path expression
introduces no variables. For a navigation tool the intermediates *are* the
answer: "why does this endpoint expose `User`" is a request for the route. A
recursive walk in code returns it; a property path cannot.

Two edge closures are exported so a caller and a query cannot drift:

- `TYPE_EDGES` — what a type is *made of*.
- `USE_EDGES` — `TYPE_EDGES` plus containment **and application**, which walked
  in reverse from a type arrives at the operations and resources that can carry
  it, and from a trait, a security scheme or an annotation type at every site
  that uses it.

### 5.1 SPARQL

For aggregation, joins and filtering — "types referenced by more than ten
endpoints" — SPARQL is genuinely better than hand-written code, so the graph
serialises to N-Triples and Turtle and the CLI has a `query` verb.

`pyoxigraph` is an **optional** dependency, the way `google-re2` and the CLI's
HTTP client are: pyRAML does not import it, `pyraml query` reports its absence
with the install command, and everything else works without it. `rdflib` will
read the same output; it is a pure-Python SPARQL engine and will be markedly
slower on a large document set.

Both serialisations are checked against a real RDF parser in
`tests/unit/test_graph.py`, not merely eyeballed — a hand-written emitter that
produces almost-valid N-Triples is the obvious failure mode.

## 6. AMF was assessed and not adopted

The reference implementation ships a full AMF-compatible JSON-LD converter
(`go-raml:converter/jsonld.go`, 1787 lines over a model whose class names match
this one's almost exactly). It is the concrete cost estimate for the alternative,
and it was read before this design was settled.

Three different proposals hide behind "use AMF":

**Run AMF to produce the graph.** Rejected. It adds a JVM/Node dependency and a
second RAML implementation whose resolution pipelines do their own trait and
resource-type expansion. The graph would then answer *AMF's* questions, and the
916-of-916 conformance this project maintains would describe a model nobody
queries.

**Emit AMF's vocabulary from this model.** Rejected. It is strictly more work,
not less: AMF's WebAPI model is a union metamodel over RAML, OAS and AsyncAPI
with RAML's type system re-encoded as SHACL shapes, so the mapping inherits that
impedance permanently and on someone else's release cadence. It is also lossy
exactly where this parser is strongest — the provenance overlay, `location` vs
`anchor`, and the outcome-versus-source distinction the four trait priority
classes produce.

**Emit AMF JSON-LD as an export.** Not rejected — deferred, with a stated
trigger. The one real payoff is interoperating with tooling written against AMF,
notably the API-governance rulesets. Nothing needs that yet, and if it does, it
is an *additional serialiser* over this graph rather than a change to it. That is
the whole reason § 3's IRIs are structural and § 2's vocabulary is separable from
them.

**What was taken from AMF: the IRI discipline, and nothing else.** Structural
paths, escaped segments, one flat identifier space, declarations registered
before use sites. That part is right, is the expensive part to get right, and
costs nothing to adopt. The paths themselves are ours — AMF's doubled
`parameter/parameter` segment and its `PropertyShape` indirection exist to serve
SHACL, which we are not producing.

**SHACL is not built either.** Constraints like "a `Payload`'s range is a `Type`"
restate guarantees the parser already enforces, so validating them proves
nothing. SHACL would earn its place for *policy above RAML conformance* — every
public operation is documented, every 4xx uses the standard error type — and that
is a consumer's rule set, not a parser's.

## 7. Cost

On `bench_large` (7000 types, 150 libraries):

| | |
|---|---|
| parse + unwrap | ~945 ms |
| projection | ~237 ms |
| nodes / edges | 41 359 / 61 307 |
| N-Triples | 268 951 statements, ~144 ms to serialise |

The projection is roughly a quarter of the parse it follows, which is the right
order for a single walk over a model already in memory. It is **not** benchmarked
in `bench/` and not a CI gate: it is not on the parse hot path, and docs/12 Part 4
gates what the parser costs, not what a consumer of it costs.

A quarter of a million triples is also the honest argument for `pyoxigraph` over
`rdflib` when the document set is large, and for reading the `Graph` directly
when the question is a walk rather than a join.
