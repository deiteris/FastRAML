# 16. The graph projection

**Status: built.** `pyraml/graph.py` and `pyraml/render.py`, and the `graph`,
`refs`, `deps`, `show` and `query` verbs of the CLI ([13](13-public-api.md) § 8).

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

### 3.3 Looking a name up: a declaration wins

`Graph.find` turns a name into an IRI, and more than one node can carry the same
name. `Admin: [User, Entity]` builds a synthetic parent per branch, and each one
carries the name of the type it resolves to — so `Entity` matches both its own
declaration and `…/types/Admin/inherits/Entity`.

**A declaration wins.** The two are the same type, and only the declaration is
somewhere an author can go, so reporting the pair as an ambiguity helps nobody.
A declaration is told from a node inside one by depth: its tail after
`#/declarations/` is `<bucket>/<name>` and nothing more, and a name contributes
no `/` of its own because every segment is escaped.

Two *declarations* of one name — the same type declared in two libraries — stay
ambiguous, and `find` returns both. That question only the caller can answer,
which is why a whole IRI is also accepted as a name.

Without this rule `pyraml refs Entity` exited 1 on a three-type document. It was
found by asking what navigation still needed, not by a test, which is why one
now names it.

### 3.4 Ordering

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

## 6. The catalogue, and whether SPARQL earned its keep

`pyraml/queries.py`. Seventeen named questions, run with `pyraml query -n NAME`,
listed with `--list` and printed with `--show`. Both of the latter are text
operations: they need no store and no document, so a reader without
`pyoxigraph` can still see what the tool would ask.

| Query | Answers |
|---|---|
| `unused-types` | declared types nothing references |
| `trait-usage` | every trait and how many operations apply it; **0 means dead** |
| `annotation-usage` | every annotation type and how many sites apply it |
| `type-fan-in` | types ranked by how many operations can carry them |
| `multiple-inheritance` | types with more than one direct supertype |
| `recursive-types` | types that close a cycle |
| `undocumented-operations` | operations with no description |
| `unsecured-operations` | operations with no scheme, after inheritance and `[null]` |
| `scheme-usage` | each scheme, what it guards, and any narrowed scopes |
| `error-response-types` | every 4xx/5xx and the type it returns |
| `untyped-payloads` | bodies whose type is `any` |
| `media-types` | which media types are used and how often |
| `get-with-request-body` | GETs that declare a payload |
| `required-query-parameters` | required query parameters |
| `unbounded-strings` | string properties with no `maxLength`, `pattern` or `enum` |
| `enums` | every closed value set |
| `endpoint-tree` | every resource and its methods |

All seventeen are **whole-document** questions that take no arguments. That is
the division: parameterised navigation is `refs` and `deps`, which return a
route (§ 5).

### 6.1 The verdict

The test this projection was to be judged by was whether real analysis queries
become materially simpler than the equivalent code. Having written them:

**Yes, for whole-document analysis. No, for navigation.** The split is clean and
falls exactly where § 5 predicted.

Where SPARQL clearly wins:

- **Negation.** `unused-types` is `FILTER NOT EXISTS { ?s ?p ?t . FILTER(?p !=
  raml:declares) }` — *any* incoming edge other than the declaration counts as a
  use, so the query needs no list of the ways a type can be referenced and
  cannot fall out of date when an edge is added. That property is not available
  to hand-written code without rebuilding the same generalisation.
- **Outer joins.** `trait-usage` reports an unused trait as `0` rather than
  omitting it, and the unused trait is the whole reason to run the query. One
  `OPTIONAL` does it; an inner join silently answers the opposite question.
- **Transitive closure combined with aggregation.** `type-fan-in` is a `*` path
  under a `COUNT(DISTINCT)` and a `GROUP BY`. This is the case with no tidy
  imperative equivalent, and it is the most useful query in the list.

SPARQL wins nothing on two kinds of question: anything `refs` and `deps` already
answer, and flat filters. `required-query-parameters` is no shorter than a loop,
and more people can read the loop.

### 6.2 Three queries were wrong, and still returned rows

`multiple-inheritance`, `unbounded-strings` and `type-fan-in` each matched every
`Type` node instead of every *declared* type.

A response body that resolves to `Admin` carries `Admin`'s parents. The
unrestricted queries therefore returned one row per **use** of a problem instead
of one row per problem, and labelled most of those rows `application/json`,
which names nothing an author can go and fix. `GROUP BY ?name` then merged each
declaration with its uses. All three now match on `?u raml:declares ?t` and
group by the node.

Each wrong version ran, returned plausible output, and would have been copied.
That is the argument for a tested catalogue over a section of example queries.

### 6.3 Cost at scale

Read these figures as orders of magnitude, not as measurements of a real API.
Neither benchmark corpus exercises the whole catalogue: `bench_large` declares
7000 types and no endpoints, and `bench_endpoints` declares 2000 endpoints over
a handful of types. Most queries therefore match nothing on a given corpus, and
a query that matches nothing returns in microseconds. The table below counts
only the queries that match, because averaging in the others would report the
corpus's shape as if it were the query's speed.

**These are the only figures in this document that the harness did not
produce.** Running a query needs an RDF store, and `pyoxigraph` is an optional
extra that `bench/` does not depend on, so there is no `bench` configuration for
them. They come from a script instead: one corpus per process, on a clean
checkout, each timing the fastest of five runs after a warm-up. Treat them as
weaker evidence than § 8's, and re-measure before relying on any single one.

| | `bench_endpoints` | `bench_large` |
|---|---|---|
| triples | 239 547 | 268 951 |
| queries that match anything | 8 of 17 | 3 of 17 |
| slowest | `type-fan-in`, 29 ms | `unbounded-strings`, 73 ms |
| median of those | 7 ms | 44 ms |
| serialising to N-Triples | 152 ms | 162 ms |
| **loading the store** | **421 ms** | **443 ms** |

**Loading the store costs about six times the slowest query.** That decides how
the tool should be used. `pyraml query` builds and loads the graph on every
invocation, so a session that asks several questions should hold one store
rather than run the command repeatedly. The queries themselves are not the cost.

That conclusion is a ratio of about six to one, which survives the measurement
noise the figures above carry. No individual millisecond count here should be
quoted on its own.

## 7. AMF was assessed and not adopted

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

## 8. Cost

`bench_large`, 7000 types over 150 libraries. Both columns come from one
`bench run` invocation, because a difference taken across two sessions measures
the sessions as much as the code:

| | parse + unwrap | + projection | the projection's share |
|---|---|---|---|
| time | 365 ms | 590 ms | **+225 ms, ~62 %** |
| allocated | 30.3 MB | 60.5 MB | **+30 MB, ~100 %** |
| peak RSS | 100 MB | 172 MB | **+72 MB, ~72 %** |

The graph holds 41 359 nodes and 61 307 edges. Serialising it produces 268 951
N-Triples statements in 161 ms.

**The projection costs about two thirds of the parse and doubles the
allocation.** That is more than a single walk over an in-memory model should
need. The graph is a second materialised representation of the document, about
the size of the model it was built from, and this section says so rather than
calling it cheap. Code that needs one question answered should call `Graph`
methods on a document it has already parsed, not add the projection to a hot
path.

A quarter of a million triples is also the argument for `pyoxigraph` over
`rdflib` on a large document set, and for reading the `Graph` directly when the
question is a walk rather than a join.

### 8.1 How these figures are measured, and why that matters

The projection is the `unwrap+graph` benchmark configuration
([12](12-performance.md) Part 4). It is not a CI gate, because the projection is
a consumer rather than a pass. Its figures still come from the harness, which
runs each measurement in a fresh subprocess and takes the fastest of several
runs.

That is not a formality. The first version of this section reported the parse at
945 ms and the projection at "roughly a quarter" of it. Both figures came from a
script that ran two benchmarks in one interpreter while a 240 000-triple RDF
store stayed in memory. The parse figure was inflated by 2.5x, and the ratio was
wrong by a factor of nearly three: the projection costs two thirds of the parse,
not a quarter. `bench/harness.py` uses a fresh subprocess to prevent exactly
this, and its docstring says so.

## 9. The effective view

`pyraml show FILE NAME`, and `pyraml/render.py` behind it.

This answers the question a reader asks most often, and the one neither `refs`
nor a query answers: **what is this type, actually?** Every inherited property
in one place, every constraint beside the property it constrains, and for each
one the file and line it was really written on.

```
Admin:                # api.raml:46
  type: object
  inherits: [User, Entity]
  properties:
    level: integer    # api.raml:49
    name: string      # User, api.raml:44
    address: Address  # User, api.raml:45
    id:               # Entity, api.raml:31
      type: string
      maxLength: 36
```

### 9.1 Why it does not go through the graph

The graph resolves the *name* — `Graph.find` picks one declaration, and
`Graph.shape_at` hands back the shape it was projected from. Everything after
that reads the **model**.

That split is deliberate. The projection carries what a traversal needs and
drops facet detail on purpose (§ 2.5), so rendering from it would render a lossy
copy. This is the "find here, ask the model there" division the design has
assumed since § 5: the graph is an index, not a replacement for the model.

### 9.2 The output is RAML

Not a table, and not a bespoke format. It is the notation the reader already
knows, it pastes back into a document, and two versions of it diff. Origins ride
in trailing comments so the whole thing stays loadable YAML — which is a corpus
law rather than an intention (docs/14 § 4, law 13), because a renderer that
emits a key it forgot to quote produces something that looks right and will not
load. Three fixtures do exactly that: `//:`, the spec's way to constrain every
additional property, has an empty pattern and so an empty key.

### 9.3 Where a property came from

Each property is attributed to the **furthest** ancestor that declares it at the
same position — `Admin.id` reports `Entity`, not `User`, even though after
unwrap `User` carries it too.

Position is what identifies "the same" property, and that is what makes the
attribution useful rather than merely decorative: a subtype that *narrows* an
inherited property re-declares it at its own line, so the position stops
matching and the subtype is correctly named as the origin. That is the case a
reader is usually trying to settle — *is this limit 36 or 8, and who set it?*

This is per-**property** provenance, obtained for free from `location` and
`key_pos`. Per-**facet** provenance — which link in the chain set `maxLength`
when three of them mention it — is still not available; the parser knows at
merge time and does not retain it (After-v1 item 4 in
[15](15-implementation-plan.md)).

### 9.4 Endpoints, not only types

An endpoint is the entity that needs this most. It accumulates a resource type,
any number of traits, security inherited from the API root, and URI parameters
propagated down from every ancestor — and none of that is visible at the place
it is written.

```
/items:                    # api.raml:27
  type: collection
  get:                     # api.raml:29
    is: [first, second]
    headers:
      X-Trait: string      # first, api.raml:12
    queryParameters:
      shared?:             # api.raml:32
        type: string
        description: from the method itself
      fromType?: string    # collection, api.raml:26
      onlyFirst?: string   # first, api.raml:10
      onlySecond?: string  # second, api.raml:18
```

That is the four trait priority classes of [08](08-templates-and-endpoints.md),
resolved and attributed, in one screen.

**`shared?` carries no attribution, and that is the interesting one.** The
resource type and the method both declare it, and the method wins; naming
`collection` there would answer the reader's actual question — which description
applies — with the wrong one.

Getting that right needs an exact span. `Sources` maps a merged-in item's line
back to a declaration using `key_pos.line` to `value_pos.end_line`, both of
which the parser records. An earlier version guessed the end as "until the next
declaration in the same file"; the last declaration in a file has no next one,
so its span ran to the bottom of the document and swallowed every endpoint below
it. Attribution is *also* gated on the site having applied the declaration, so
two independent checks have to agree before a name is printed.

Per-facet provenance is still out of reach (§ 9.3), and the same caution applies
here: this says which declaration a key was written in, not which one supplied
the value that won a merge.

### 9.5 Depth

`--depth` counts levels of *expansion*. The default of 1 shows the type's own
effective properties and names their types rather than opening them, because a
named type is worth naming — the reader can ask for it by name, and a real API
expands into far more than fits on a screen.

Depth alone does not open a scalar: `level:` followed by `type: integer` is two
lines saying what one line said. Only something with structure is opened, and a
type cycle stops at its first re-entry however deep the walk was asked to go.
