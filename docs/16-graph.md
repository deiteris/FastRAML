# 16. The graph projection

**Status: built.** `fastraml/views/`, behind the `graph`, `tree`, `list`, `refs`, `deps`, `show`, `query`,
`diff` and `openapi` verbs of the CLI ([13](13-public-api.md) § 8).

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

So this layer is a projection and nothing else. It contains no RAML rule, no
merge, no name resolution, no inference. If a question about the graph turns into
a question about what RAML means, the answer belongs in another document and
another pass. The dependency runs one way — `views/graph.py` and `nodes.py` import the
model and nothing in the model imports either.

"A projection" states an intention, and an intention permits anything that can
be argued for. Three clauses say what it forbids, and each has a test:

**It holds references, not copies.** A node's data is its IRI, the model object
it projects, and the root that object's location is reported relative to. Nothing
else is stored. A field restating something the entity holds is a second copy of
the model that drifts from the first and doubles the memory: 82,287 values in
6.0 MB, on a document whose model already held all of them.
*Test:* every node class's slots are a subset of `iri`, `entity`, `root` and
`TypeNode.shape_kind`.

**It owns the vocabulary, not the values.** `additionalProperties`, `statusCode`,
`definedIn` are this vocabulary's names for things the model spells
`additional_properties`, `code`, `location`. Translating between them is the
work. Deciding what the value *is* is not — that happened in a pass.
*Test:* `attributes` is a property computed from the entity, and a fresh
dictionary each read.

**It resolves nothing by name.** Every reference the model resolved carries what
it resolved to. Matching a name again gets a different answer where two libraries
declare one: § 3.2a is a worked case where the graph reported an application
against a trait that was never applied.
*Test:* two libraries declaring `paged`, and the edge lands on the one whose
parameter the merge produced.

A fourth follows from the first: **a fact reachable by following an edge is not
an attribute.** An operation does not restate its endpoint's path (§ 2.8).

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

Namespace: `urn:fastraml:ns:raml#`, exported as `RAML_NS`.

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
| `declares` | `Unit` | any declaration | the file it is **written in**, not the map that named it |
| `unit` | `Api` | `Unit` | the document the API root was written in |
| `endpoint` | `Api` | `EndPoint` | flat: every resource, not only top-level |
| `parent` | `EndPoint` | `EndPoint` | nesting, for the tree view |
| `supportedOperation` | `EndPoint` | `Operation` | |
| `request` | `Operation` | `Request` | omitted when the method sends nothing |
| `returns` | `Operation` | `Response` | |
| `payload` | `Request`/`Response` | `Payload` | one per media type |
| `parameter` | `Api`/`EndPoint`/`Request`/`Response` | `Parameter` | binding on the node; `Api` owns `baseUriParameters` |
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

### 2.6 A schema type is not a leaf

Structure and facets are read through `projected(base)` ([10](10-validation.md)
§ 6.3), so a type defined by a JSON schema has properties, items and members in
the graph like any other.

Without it a `JsonShape` held no `ScalarFacet` slots and no children, so it
projected as a leaf — and three verbs then answered wrongly rather than saying
they could not: `deps errorScheme` reported it is made of nothing, every
catalogue query walking `raml:property` skipped those types, and `diff`, which
compares nodes, attributes and reference edges, saw **no change at all** when a
whole response schema was replaced. On a schema-heavy document that is every
type in it.

The cost is proportional to the structure gained: on the benchmark's schema
corpus the graph goes from 401 nodes to 6001, and `unwrap+graph` from about
93 ms to 140 ms.

It was not proportional at first, and the benchmark did not say so. On a real
149-endpoint document the build went from 65 ms to **1260 ms** — because
`applies` resolved each `is:`/`securedBy:` name by scanning every node in the
graph, and nine times the nodes made that quadratic bite. The scan is now an
index built as nodes are created, and the same document builds in 144 ms: nine
times the graph for a little over twice the time.

**Why the gate missed it.** `bench_endpoints` makes 6000 trait applications, so
the code was covered — but its names are unqualified, and declarations are
projected first, so every scan matched within a few nodes and returned. A
*qualified* name (`roles_lib.public`) never matches on the first candidate, so
it scans the whole graph before falling back. No corpus has qualified names and
a large graph together, which is the shape of a real library-using API.

### 2.7 A node's kind is its class

`fastraml/nodes.py` holds one class per kind. `TypeNode` carries a `BaseShape`,
`ResponseNode` a `Response`, `ParameterNode` a `Parameter`; the entity's type is
a parameter of the class, so `kinds[0]` is read off the class and a node whose
kind and entity disagree does not typecheck. There is no kind string to get
wrong and nothing to check at runtime.

Two shapes of many-to-one are in the vocabulary and the classes say which:

- **One entity, two kinds.** The entry document is both a file and an API, so
  `UnitNode` and `ApiNode` hold the same `APIFragment` and say different things
  about it. A kind derived from the entity's class could not tell them apart.
- **One kind, two entities.** A trait, a resource type or a security scheme is
  normally its definition. Where a name matches no declaration an edge is still
  emitted, so the application is not invisible, and `UnresolvedTraitNode` and
  its two siblings carry the *reference* — a name and nothing else, because
  nothing else is known.

Every node holds a model object. On a real 149-endpoint document that is 32,090
of 32,090, across `BaseShape`, `Property`, `Body`, `Response`, `Parameter`,
`Request`, `Operation`, `EndPoint`, `TraitDefinition`,
`SecuritySchemeDefinition` and the two fragment kinds. `entity_at` reads the
node, and `shape_at`, `endpoint_at` and `operation_at` narrow it.

This is what makes § 1's rule enforceable rather than aspirational. A node with
no entity is something this layer invented, and inventing is the failure mode
the rule exists to prevent.

### 2.9 A file that declares something is a node

Every fragment holding a declaration gets a `Unit`, whatever its kind. A typed
fragment is *one* declaration and has no `types:` map, so walking the maps never
reaches it — it is reached as a parent of the `types:` entry that included it —
and it needs a node all the same. On a real document that is 161 files rather
than 3: the API, two libraries, and the 158 `!include`d data types.

Without them `definedIn` names something the graph does not contain. No edge
dangles, because it is a literal rather than an edge, but it is the same defect
one level down: 158 of the 161 files a type could be written in were unreachable,
so "what does this file declare" had no answer for 96% of them.

`declares` runs from the file a declaration is **written in**, not from the map
that named it. `types: {X: !include x.raml}` names `X` in one document and writes
it in another; `definedIn` already says `x.raml`, and an edge from the naming
document would contradict it. One declarer per declaration, and it agrees with
the literal.

### 2.8 The literals are derived, not stored

`attributes` is a method on each node class. It reads the entity when asked;
nothing is written at build time.

Holding them costs 6.0 MB on a real 149-endpoint document — 82,287 values in
32,090 dictionaries, restating what the model already holds, allocated whether
or not anything reads them. Deriving all of them costs 33 ms, and the verbs
that navigate never ask for more than a handful: `entries` labels 525 rows, not
32,090. A caller reading one node's attributes more than once binds them to a
local, because each read builds a fresh dictionary; `diff` and `label` do.

**What the view is for.** The keys are `additionalProperties`, `statusCode`,
`isAnnotationType` — this vocabulary's names, which the model spells
`additional_properties`, `code`, `is_annotation_type`. Mapping between them is
the work, and it belongs here. The rule: **this layer owns the vocabulary, not
the values.** A stored attribute is a value it does not own.

Three things follow from it:

- **An Operation has no `path`.** The endpoint holds it and the
  `supportedOperation` edge reaches it. A fact reachable by following an edge
  is not an attribute — storing it would also report a moved resource once per
  method beneath it, where § 10.4 diffs the edge and reports it once.
- **`scopes` is a fold** over `Operation.secured_by`, so an operation secured by
  two OAuth schemes reports the scopes of both.
- **`unsecured` and the binding** come off the entity: the first from
  `securedBy: [null]` being visible in `secured_by`, the second from
  `Parameter.binding` (§ 5 of [05](05-type-model.md)).

The equivalence is checked by dumping every attribute of every node across five
corpora and 216,961 nodes: the only cells that differ are the `path` deletion,
which is exactly the operation count.

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

**The scheme belongs to `fastraml/views/walk.py`, not to this module.** More than one
emitter addresses the same document, and a reference is followable only when the
emitter that wrote it and the emitter that reads it agree. Assignment is one
traversal — `Walk`, reporting to a `Sink` — and every emitter is a sink over it.
`address(raml)` runs that traversal for the map alone; `Graph.addresses` is the
map the graph was built with, and is the join between a node here and the same
entity in any other emitter's output.

```
fastraml://id#/declarations/types/User
fastraml://id/lib.raml#/declarations/types/Address
fastraml://id#/web-api/endpoint/%2Fusers/supportedOperation/get/returns/200
fastraml://id#/web-api/endpoint/%2Fusers/supportedOperation/get/returns/200/payload/application%2Fjson/schema
```

Three properties, each of which cost something to get:

**Stable across runs.** `BaseShape.id` is a per-parse counter, so a graph keyed
on it would be meaningless in a diff, a cache or a committed query result. A
structural path depends only on the source.

**Machine-independent.** The root is `fastraml://id`, mirroring AMF's `amf://id`
for the same reason. A declaration authored outside the entry document gets its
unit's path *relative to the workspace root* — `fastraml://id/lib.raml#…` — so no
absolute filesystem path enters the graph. The tree keys its declaration maps by
the same path (§ 11), and a consumer builds URLs out of that key.

**The anchor is the workspace root**, which `--workspace-root` sets and which
defaults to the entry document's directory. It is the boundary `SafeFileLoader`
enforces, so every file a parse can read is at or beneath it: a path relative to
it never ascends, and a project whose shared libraries sit beside its APIs
rather than beneath one of them gets `shared/money.raml`. Relative to the entry
*document's* directory the same file is `../shared/money.raml` — machine-
independent, and not where the file is in terms a reader of the project uses.

`fastraml.uris.relative_to` computes it, in one place. A `removeprefix` is not the
same function: a path that shares no prefix with the root falls back to the
whole URI, which is the one outcome this property exists to exclude.

**Assigned once.** A shape reached twice keeps its first IRI. That is what makes
a declared type one node rather than one node per use site, and what closes a
type cycle. It also makes **walk order part of the contract**: declarations are
registered before endpoints are walked, so `User` lands at
`#/declarations/types/User` and not at whichever response body happened to reach
it first. go-raml's converter pre-registers for exactly this reason.

Every segment is percent-escaped with an empty safe set, so a media type, a
`/{userId}` template and a `/^x-/` pattern-property name each survive as one
segment.

### 3.1 Names are not unique, and a collision is silent

A structural IRI is derived from *names*, and RAML does not promise the names
are distinct. `type1: [string, string]` gives two parents the same one. Without
disambiguation the second merges into the first: no error is raised, the node
count is plausible, and **two types have become one**.

`Walk.claim` therefore records which shape holds each IRI and appends `/!2`,
`/!3` … to a name already taken. The suffix begins with `!`, which segment
escaping always percent-encodes, so a disambiguated IRI can never collide with
one a real name produced.

This is not a hypothetical. go-raml's converter carries a test for exactly the
same hazard — `TestJSONLD_NoDuplicateIDs`, "a regression net for intermediate
`*BaseShape` objects that bypass `shapeIDs` registration and accidentally claim
a contextID already in use". Taking that seriously found the same hole here, in
one corpus fixture (`EdgeCases/inherit-multiple-scalars`). The corpus law is
docs/14 § 4, law 12.

**An address is therefore not an identity, and must not be made into one.** It
fails injectivity in both directions. Two entities share one address wherever
the model links: `traits: {paged: !include p.raml}` registers the entry and the
fragment it resolves to against one node, so a reference bound to either finds
it — `Addresses.at` returns a list for that reason. And an entity's address is
not a function of the document alone, because `claim` breaks a tie by visit
order. Identity stays `BaseShape.id`, the per-parse counter; `Addresses.of` maps
identity to address and is deliberately many-to-one.

### 3.2 A dot is not always a namespace separator

`type: lib.Collection` is a qualified reference; `securitySchemes: {oauth2.0: …}`
is a declaration whose *name* contains a dot. A reference is therefore matched
against the whole name first and only then against the dotted tail — splitting
first makes the tail `0`, which matches nothing.

An unmatched reference still gets an edge, to a node created at that moment, so
an application is never invisible and **no edge ever dangles**. That last part is
also a corpus law: an edge to a node that does not exist is a traversal that
silently ends early.

### 3.2a An application points at what was applied

`appliesTrait`, `appliesResourceType`, `securedBy` and `annotation` read the
declaration off the reference — `DirectiveRef.resolved`, `SecurityScheme.definition`,
`DomainExtension.defined_by` — which the pass that resolved the name recorded.

Matching the name again is not equivalent, and the difference is visible:
`a.paged` and `b.paged` are one name in two libraries. A lookup returns whichever
was declared first, so `is: [b.paged]` produces an edge to `a`'s trait — an
application recorded against a declaration that was never applied, and a
`refs a.paged` that reports a use which does not exist. There is no name
resolution in this layer; § 1 says so and this is what it costs to mean it.

A reference that resolved to nothing still gets an edge, to a node carrying the
name, so an application is never invisible and no edge dangles. That node is an
`Unresolved*Node` (§ 2.7), and its presence is the signal that a name matched
nothing — not that the projection failed to look hard enough.

### 3.2b A subschema is addressed by its own document

A shape the § 6.3 projection built carries its **canonical URI** in `location` —
the schema document plus the JSON Pointer within it. That is one field and not a
new one: `location` is already a URI, and already carries a fragment for a RAML
type declared from `schema.json#/definitions/User`. Its address is that URI
rebased onto `unit()`: `fastraml://id/shared%2Fmoney.schema.json#/definitions/Amount`.

Containment addressing is wrong for these. `money.schema.json#/definitions/Amount`
is **one** subschema however many RAML types `$ref` it, and addressing it by the
type that reached it first makes every other type's copy answer to a stranger's
URI — measured at 355 IRIs owned by more than one declaration on a mid-sized API.
A JSON Pointer is already the standard identity for a subschema, and using it
makes the address reference-independent in the way `unit()` does for a library
type: `Parcel` is `shared%2Fmeasures.raml#/declarations/types/Parcel` whoever
imports it.

That identity is also what lets one projection be **shared**. `SchemaRegistry`
keys projections on it, so a `$ref` target is projected once per parse rather
than once per referencing schema: 18,972 view shapes down to 1,803 on the same
API, and `tree` from 508 ms to 262 ms. Sharing without the identity is a
corruption of exactly the kind § 3.1 describes; with it, two types pointing at
one node is the truth.

**One table, keyed by that URI alone.** A document is reached two ways — as the
RAML type that `!include`d it, and as the target of another schema's `$ref` —
and it was cached under one key per way. Nothing joined them, so 20 documents on
that API were projected into two independent shape trees. Both wrote the same
canonical `location`, so both asked `unit()` for the same address and `claim`
split them rather than reporting a collision: **287 addresses ended in `/!2`**,
every one a subschema, and `graph`, `tree` and `diff` each saw one document's
properties twice. Merging the tables took that to 6, and those 6 are real —
`"type": ["integer", "null"]` gives a union and its members one JSON Pointer
between them, which is § 3.1's case and not this one.

A shape's `name` is part of the same identity and comes off the same URI
(`_subschema_name`): a whole document is called after its file, a `definitions`
or `$defs` entry after its key, and a position like `#/properties/items/items`
is called nothing. Naming it from the *reference* that arrived first made the
same type named or nameless by walk order, since only the `$ref` path carried a
name — and it took the last segment of any pointer, so `#/properties/foo` was
`foo` one way and anonymous the other.

The identity holds only where **the document is the schema**, which
`_is_one_schema` decides. A `$ref` target is read by `SchemaRegistry` and is not
a fragment at all; an `!include`d schema is wrapped into a one-type
`DataTypeFragment`. Either way the file is the schema.

An API or a library is not: every schema written **inline** in it compiles under
that one URI. Sharing on it made the second inline schema in a file answer with
the first one's projection, and addressing on it had two of them claim
`fastraml://id#/properties/x`. These get no fragment, and its absence is what
tells a consumer to fall back to containment.

Being *inside* a projection is the walk's own state, not the shape's: `Walk.shape`
takes `in_schema`, set where `projected()` substitutes for a declaration and
passed down unchanged. It cannot be read off the shape, because a RAML type
declared from `schema.json#/definitions/User` has the same kind of `location` and
must still answer at its declaration IRI.

Comparing the document against the declaring shape's own `location` is *not* the
same test. P9 moves a schema onto a synthesised parent whose location already is
the schema file, so a real schema file reads as inline under that comparison and
the declaration and its parent then address one subschema two ways.

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

Without this rule `fastraml refs Entity` exited 1 on a three-type document. It was
found by asking what navigation still needed, not by a test, which is why one
now names it.

**And an outer node wins over one inside it.** A node passes its name down to
what it contains, so a query parameter `login` matches both
`…/parameter/query/login` and `…/parameter/query/login/schema`. Those are one
entity at two depths, and reporting the pair asks the caller to choose between a
thing and part of itself — `fastraml show login` exited 1 on exactly that.

The two rules are independent and both are needed. Containment cannot settle the
synthetic-parent case, because `…/types/Admin/inherits/Entity` is not *inside*
`…/types/Entity`; the declaration rule cannot settle the parameter case, because
neither node is a declaration. Two declarations of one name survive both, which
is right: neither contains the other and the question is real.

### 3.4 Ordering

RDF is a set of triples and declaration order is an invariant everywhere the
model is exposed (docs/02 § 4). The two are reconciled by **not** reconciling
them: the in-memory `Graph` preserves order — `nodes` is insertion-ordered and
`out()` returns edges as they were added — and the RDF serialisations do not,
because encoding order as `rdf:List` would put `rdf:rest*/rdf:first` into every
query for a property nothing here needs.

Order is therefore available to the Python API and to `--format json`, and absent
from Turtle and N-Triples. Where order matters, read the graph directly.

### 3.5 The inventory, and a miss that is not a dead end

`find` answers "which node is this name?". `Graph.entries()` answers the question
that comes before it — **what names are there** — returning `(kind, name, iri)`
for every declaration plus every endpoint and operation. That is exactly the set
`find` resolves to one node, so a listed name is one the caller can use, and a
test asserts it for every row.

Deliberately not every node. On a real document the nodes inside declarations
outnumber the declarations about twenty to one, and they are reached by walking
rather than by naming; listing them buries the answer in the question. Sorted by
kind then name, so two runs diff cleanly rather than churning.

`Graph.suggest(name)` is for the miss. `difflib.get_close_matches` first, then a
substring pass — the second is not belt-and-braces: `get_close_matches` is
ratio-based, so a half-remembered fragment (`List` against `UserList`, `user`
against `userLoginInfo`) scores below any cutoff worth using, and typing a
fragment is the commonest miss there is.

It **suggests and never substitutes**. Running the nearest name answers a
question the caller did not ask, which is the same principle as § 3.3's refusal
to pick from a genuine ambiguity.

## 4. What is deliberately not projected

The boundary is not "data versus structure". **A thing is projected when
something can point at it.** Every predicate in § 2.2 is emitted where the model
holds a referent — `annotation` points at the annotation *type*, never at the
value — and everything below is absent because nothing in the model refers to
it, so no traversal can arrive at it and no query can ask.

That is also the reach of the addressing traversal (`fastraml/views/walk.py`): an entity
needs an address exactly when a reference to it has to be followable. An emitter
that wants the list below places it by containment instead, which needs no
address. So the list is a boundary a fuller emitter crosses without this module
changing — not a shortfall it has to work around.

- **Examples and default values.** Nothing points at an example; the containing
  shape holds it. `Examples.entries()` is the way to read them and the golden
  layer already pins them.
- **Trait and resource-type *bodies*.** They have been applied; the applied
  result is projected on the operation. `appliesTrait` records that a trait was
  used, which is the question anyone asks about it.
- **`!include` references.** `raml.include_refs` records 1,083 of them on a
  real 149-endpoint document, and nothing asks: `deps` and `refs` work on types
  and endpoints, not files. § 2's rule applies — add a term when a question
  wants it. A `Unit` node per file exists (§ 2.9), so the question would have
  somewhere to land if one arrives.
- **Documentation items, `uses:` prefixes and protocols.**
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

`refs` and `deps` report each result's **position** as well as its route: the
graph has carried `definedIn` and `line` on every node from the start, and a
result you cannot navigate to is half an answer. They also take `--kind`,
`--depth` and `--limit`, because one type on a 2000-endpoint document reaches
10 503 routes and unbounded output is indistinguishable from none.

Two edge closures are exported so a caller and a query cannot drift:

- `TYPE_EDGES` — what a type is *made of*.
- `USE_EDGES` — `TYPE_EDGES` plus containment **and application**, which walked
  in reverse from a type arrives at the operations and resources that can carry
  it, and from a trait, a security scheme or an annotation type at every site
  that uses it.

`Graph.request_shape_iris()` materialises one common directional closure: every
type node reachable from a request body, request parameter, query string,
resource URI parameter or base URI parameter through the graph's direct edges.
"Input" is not intrinsic to a type
because one declaration can be used on both sides of the wire; it is a property
of a route from a request site. The immutable result is cached in a dedicated
graph slot. Consumers therefore share the derivation without adding redundant
transitive `input` edges or maintaining private caches.

### 5.1 SPARQL

For aggregation, joins and filtering — "types referenced by more than ten
endpoints" — SPARQL is genuinely better than hand-written code, so the graph
serialises to N-Triples and Turtle and the CLI has a `query` verb.

`pyoxigraph` is an **optional** dependency, the way `google-re2` and the CLI's
HTTP client are: fastRAML does not import it, `fastraml query` reports its absence
with the install command, and everything else works without it. `rdflib` will
read the same output; it is a pure-Python SPARQL engine and will be markedly
slower on a large document set.

Both serialisations are checked against a real RDF parser in
`tests/unit/test_graph.py`, not merely eyeballed — a hand-written emitter that
produces almost-valid N-Triples is the obvious failure mode.

## 6. The catalogue, and whether SPARQL earned its keep

`fastraml/views/queries.py`. Nine named reports, run with `fastraml query -n NAME`,
listed with `--list` and printed with `--show`. Both of the latter are text
operations: they need no store and no document, so a reader without
`pyoxigraph` can still see what the tool would ask.

| Query | Answers |
|---|---|
| `annotation-usage` | every annotation type and how many sites apply it |
| `type-fan-in` | types ranked by how many operations can carry them |
| `recursive-types` | types that close a cycle |
| `scheme-usage` | each scheme, what it guards, and any narrowed scopes |
| `error-response-types` | every 4xx/5xx and the type it returns |
| `media-types` | which media types are used and how often |
| `required-query-parameters` | required query parameters |
| `enums` | every closed value set |
| `endpoint-tree` | every resource and its methods |

All nine are **whole-document** reports that take no arguments. That is
the division: parameterised navigation is `refs` and `deps`, which return a
route (§ 5), while questions that attach a judgement and severity are lint rules
([18](18-linting.md) § 4.1).

### 6.1 The verdict

The test this projection was to be judged by was whether real analysis queries
become materially simpler than the equivalent code. Having written them:

**Yes, for whole-document analysis. No, for navigation.** The split is clean and
falls exactly where § 5 predicted.

Where SPARQL clearly won during that assessment:

- **Negation.** The former `unused-types` query is `FILTER NOT EXISTS { ?s ?p ?t . FILTER(?p !=
  raml:declares) }` — *any* incoming edge other than the declaration counts as a
  use, so the query needs no list of the ways a type can be referenced and
  cannot fall out of date when an edge is added. The graph's `into()` primitive
  gives code the same generalisation; [18](18-linting.md) § 4 records why the
  lint rule uses it instead of retaining a duplicate query.
- **Outer joins.** The former `trait-usage` query reports an unused trait as `0` rather than
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

These measurements predate [18](18-linting.md)'s extraction of judgement-shaped
queries and cover the original seventeen-entry catalogue. They remain the
evidence for not making SPARQL the lint engine; they are not a list of commands
the current catalogue still exposes.

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
the tool should be used. `fastraml query` builds and loads the graph on every
invocation, so a session that asks several questions should hold one store
rather than run the command repeatedly. The queries themselves are not the cost.

That conclusion is a ratio of about six to one, which survives the measurement
noise the figures above carry. No individual millisecond count here should be
quoted on its own.

## 7. AMF was assessed and not adopted

go-raml ships a full AMF-compatible JSON-LD converter: 1787 lines over a model
whose class names match this one's almost exactly. It is the concrete cost
estimate for the alternative, and it was read before this design was settled.

Three different proposals hide behind "use AMF":

**Run AMF to produce the graph.** Rejected. It adds a JVM/Node dependency and a
second RAML implementation whose resolution pipelines do their own trait and
resource-type expansion. The graph would then answer *AMF's* questions, and the
915-of-915 conformance this project maintains would describe a model nobody
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
notably the API-governance rulesets. Nothing needs that yet.

It would be **a sink over `fastraml/views/walk.py`, not a serialiser over this graph.**
An AMF consumer renders as well as queries, so it wants what § 4 excludes —
api-console 6.6.69 reads 357 vocabulary terms across twelve namespaces, of which
the `data:` DataNode tree for examples and defaults is a large part. Reading
those out of a graph that deliberately omits them is not possible. Sharing the
walk and the addresses is what makes the two outputs joinable; sharing the graph
would not, because a renderer wants containment and a query wants adjacency. § 3's addresses being
structural, and § 2's vocabulary being separable from them, are what keep the
option open.

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

`fastraml show FILE NAME`, and `fastraml/views/render.py` behind it.

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

**The shape is written by hand; every value is emitted by PyYAML.** The split is
the point. An emitter cannot produce comments, and the aligned `# origin` column
is the whole reason this view beats the source it came from — so the document
structure is assembled here. Deciding when a *scalar* needs quoting is a
different job, it is not a short rule, and this module has no business owning
one.

It did own one, briefly: a denylist of `': '`, `' #'` and a few leading
characters. That was right about punctuation and silently wrong about every
string that merely *reads* as another type — `yes`, `null` and `1.0` all emitted
bare and loaded back as a bool, a null and a float. The same hole existed on the
key side, where a property named `yes` became the key `True`. Both are gone:
`_dumped()` hands the value to `yaml.safe_dump` and takes the scalar back.

Only strings take that path. A `Fraction` is still expanded by hand, because
numbers never pass through `float` here (docs/10 § 5.2); and routing an `int`
through the string path once turned `maxLength: 36` into the string `"36"`,
which PyYAML then quoted to preserve — correctly, and uselessly.

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

### 9.6 What is rendered

Everything the model carries for the entity, which for a while was less than it
sounds. `displayName`, `description` and `protocols` were on `EndPoint`,
`Operation` and `Response` and none of them reached the page — so a status code
appeared as a bare `404:` with no gloss, and a resource's own statement of what
it is for was missing entirely. On a trait-heavy document the description is
frequently the *only* part of a response that differs between two operations
sharing one body, which makes it the opposite of decoration.

First line only, as with a type's description: these may be paragraphs and the
view is meant to fit a screen.

**Custom facets and annotations are shown**, and none of the three used to be.
A `facets:` block states what a subtype must supply, and the value that
satisfies it is written somewhere else entirely — which is exactly the pair a
reader cannot hold in their head. Annotations are RAML's main extension point;
one real document applies 187 of them. An application round-trips in the form it
was written, `(name): value`.

**A union names its members** — `string | nil`, not `union`. Naming is not
expansion, so it is not gated on `--depth`: a union reaching the limit as the
bare word `union` says nothing, and one more level was showing exactly the
member names the reader wanted. JSON Schema makes this the common case rather
than a corner, since every nullable field projects to a union of the type and
`nil`. The join stops one level down, so a union of unions reads
`union | string` rather than unrolling a tree onto one line.

**An array names its member** for the same reason — `notification[]`, not
`array`. The member is the fact a reader wants: whether a list holds a shared
schema or an inline copy of one. Without it that reference is a `--depth`
away, which is the wrong price for the commonest shape in a JSON Schema
document. Unlike the union join this is not gated on `nested`, because an array
chain is linear rather than a tree and `string[][]` is as deep as it goes; a
union member is parenthesised, `(a | b)[]`, or it reads as a union with an array
on one side.

`items:` then gives way to the name, as `inherits:` does — `items: notification`
under `type: notification[]` is the same fact twice. It returns as soon as
`--depth` opens the member, which is more than the name.

### 9.7 A JSON-schema type opens like any other

Through `JsonShape.as_shape()` — the § 6.3 projection of
[10](10-validation.md), built "for consumers that want a uniform model", and
this is one. `_projected()` substitutes it for structure *and* for facets, so a
schema type shows its properties, its `required` flags and its bounds instead of
a bare name.

Without it `_has_structure` tested for object, array and union and fell through
to `False` for every `JsonShape`, so `--depth` could never open one. On a
schema-heavy document — where that is *every* type — the flag did nothing at all
and `show` printed `errorScheme` however deep it was asked to go.

Rendering only. The projection is a view: not in `Raml.shapes`, carrying no
positions, and never fed back into a pass, which `as_shape`'s own docstring names
as the failure mode to avoid. So a property inside a schema gets no `file:line`
note — there is no RAML declaration to point at.

One defect was underneath, found only by making the path reachable:
**`_narrow_json` dropped the compiled schema.** It carried a hand-written field
list into the subtype — `raw` and `validator`, but not `_compiled`, which is what
`as_shape()` reads — so the projection returned `None` on every *declared* schema
type once P9 had run, which is exactly the shape a consumer holds. It now copies
every slot the kind declares, via the `copyable_slots` helper that exists so a
hand list cannot go stale.

The substitution itself lives in `types/jsonschema_.py` as `projected(base)`, not
here: it is a statement about what a schema type *is* to any consumer walking
structure, and this module is one consumer of several.

**A named `definitions` entry keeps its name.** `#/definitions/uuid` projects to
a `StringShape` and `#/definitions/contact` to an `ObjectShape`, so rendering the
structural word printed `string` and `object` — the alias problem of § 9.2 in
JSON Schema clothing: true, and useless. Neither answers the question a reader
opens this view to ask, which is whether a field reuses a shared schema or
inlines a copy of it.

The name was never missing. `_view_base` sets it from the subschema's canonical
URI (§ 3.2b), so every form a `$ref` can address carries one however the walk
arrived. What is missing is a *marker*: `BaseShape.name` holds
a property key as well — `make_shape` sets it from the key node — and the two are
indistinguishable on the shape. So reading `base.name` in `_type_name` renders
`currencyCode: currencyCode`, and retires the member naming of § 9.2 on every
declared union, whose name is already the rendered key.

`subschema_document(base)` is that marker: a fragment in `location` means the
shape is a subschema, so its `name` is a `definitions` key rather than a property
key. Reading it off the shape rather than off `as_shape_defs` also survives
sharing — a walk served a subtree from the cache never re-enters it, so the names
inside are missing from *that* document's table. The
cheaper guess — print the name only where it differs from the key — fails exactly
where the feature earns its place: a property named `contact` whose type is
`#/definitions/contact` is the commonest shape of all, and suppressing it there
loses the one case a reader was chasing.

`_Level` carries the ids, accumulated rather than replaced, because a schema
reached through another schema's property contributes its definitions without
retiring the outer ones. They join the level *below* the `type:` line of the
schema that owns them, which the enclosing context names.

`_type_name` places the test below `alias` and the sole named parent — those are
the RAML document's own words for the type and outrank a name the schema chose —
and above the union member join, so a definition that *is* a union reads as its
name with its members one `--depth` away.

**A `type:` line naming a schema also names its file.** A bare `type: contact`
reads as a RAML type called `contact`, and a reader goes looking for a `types:`
declaration that is not there. The `.json` says which it is and the path says
where.

The path is the document the type's **body** was read from, not the one that
names it. A definition is often a one-line hop:

```json
"definitions": { "uuid": { "$ref": "../_common/types/uuid.json" } }
```

`uuid` is named in this file and *is* `../_common/types/uuid.json`. That target
is the shared schema a reader is asking about, so it is what the note gives, and
it is free: a view shape's `location` is already its own document (§ 3.2b).

A **whole-schema type** reads as its structure, `type: object`. Its only parent
is named for the file the schema was included from, which the note already
carries with its directory, so `_type_name` skips that parent and `_body` drops
the `inherits:` line with it. That parent's file comes from
`JsonShape.document_uri`, the one lookup this needs.

Notes go on `type:` lines, where a RAML type carries its own `file:line`. On a
189-line render of a nine-definition type that is 48 notes naming 10 distinct
files, and +36% of the output — most of it a line repeating the document its
enclosing `type:` line already named. Suppressing those is outstanding work: the
note earns its place where a type comes from *elsewhere*, and a schema's own
properties are not elsewhere.

### 9.8 What each security scheme adds, and why it is not merged

A scheme's `describedBy` declares the headers, query parameters and responses a
caller using it must send and expect. None of it was rendered, so an operation's
`Authorization` header — the one thing every caller needs — appeared nowhere.

It is shown as **one block per scheme**, nested under `securedBy:`:

```yaml
  securedBy:
    oauth2:                       # security/oauth2.raml:8
      headers:
        Authorization:
          type: string
          pattern: Bearer [0-9a-zA-Z\-\._~+/]*=*
      responses:
        401:
          description: The request was denied due to an invalid access token.
    session:                      # security/session.raml:8
      headers:
        Cookie: string
```

**Never merged into the operation.** Spec § Applying Security Schemes: a method
"can be authenticated by *any* of the specified security schemes". Three schemes
are three ways to call it, not one call carrying three `Authorization` headers,
and hoisting them into the operation's `headers:` would state something false.

Merging would also need a precedence rule for a response code the operation and
the scheme both declare — a `401` from a trait *and* from the scheme is the
ordinary case, not a corner — and **the spec defines none**, because it never
merges them. Separate blocks mean the question never arises.

A scheme that describes nothing is still named, because it is still an
alternative the reader may use; and when *no* scheme in the list contributes
anything the flat `securedBy: [a, b]` form is kept, since a block per name with
nothing in it is worse than a list. `null` keeps round-tripping as `null` — it
removes inherited security (docs/09 § A3) — with the explanation in the line's
note, never inside the flow sequence where `#` is a syntax error.

## 10. What changed, and what it breaks

`fastraml diff OLD NEW`, and `fastraml/views/diff.py` behind it.

The whole feature exists because § 3's IRIs are **structural**. The same entity
has the same name in both versions, so matching two documents is a dict lookup
rather than a similarity search. That property was designed for the
declared-versus-effective case; it pays for this one at no cost.

### 10.1 The change list is the contract

`diff(old, new) -> list[Change]` holds no opinion: added, removed and altered
nodes, in the old document's declaration order so the result can be committed
and compared.

Two things are dropped before anyone sees them, and both were reported as API
changes by the first version:

- **Positions.** A moved line is not a change.
- **`name`.** It is either part of the node's own IRI — so a rename arrives as
  a removal and an addition — or it is derived from an owner. Left in, it
  reported the two *file names* differing as a change to every document, and
  restated `note? -> note` alongside the `required` change that actually says
  it.

A change strictly inside something that was itself added or removed is
**subsumed**. Removing a property removes the node holding its type; reporting
both says one thing twice and grades a consequence as an independent break.

### 10.2 Direction is what makes the policy possible

Backward compatibility is not a property of an edit. It depends on who consumes
the data:

| Edit | In a request (server consumes) | In a response (client consumes) |
|---|---|---|
| property added, required | **breaking** | safe |
| property removed | risky | **breaking** |
| property becomes optional | safe | **breaking** |
| enum value removed | **breaking** | safe |
| enum value added | safe | **risky** |
| bound tightened (`maxLength` down) | **breaking** | safe |
| bound loosened (`maxLength` up) | safe | **breaking** |

The same edit is breaking on one side and harmless on the other. Nothing in the
model says which side a node is on — the **graph** does, from its containment
path, and that is this projection earning its keep on a question the model
alone cannot answer.

`Change.directions` is a **set**, and that is not fussiness. One declared type
is routinely a POST body and a GET response in the same document; the first
version stopped at the first side it reached, so the canonical CRUD shape was
graded by whichever edge came off the stack first — unstable as well as wrong.
Every side is graded and **the worst is reported**, or a reassurance buries a
break.

Direction-independent: a resource, method or status code removed.

### 10.3a Where the reporting is shared with `lint`

`diff` and `lint` both grade, and the temptation is to merge them. They are not
the same problem: a lint rule is a predicate over **one** document and a diff
rule a function of **two**, so neither can be written as the other. Nor are the
scales two spellings of one axis — `safe` is not `info`, and this view reports
non-problems on purpose because its output is a complete description of what
changed, where a lint report is a list of defects ([18](18-linting.md) § 1).

What is shared is the arithmetic, in `views/severity.py`: worst-first, and "this
grade and everything worse". Both had their own copy, a tuple with `.index()`
here and a dict there. `Ranking` takes the vocabulary as data and knows no
grade's name — a test asserts that, because the moment it names one the two
scales have started to look like one.

Two consequences followed, and neither was cosmetic. `--severity` is a
**threshold** on both verbs now; it was a repeatable exact set here and a
threshold there, so one flag name meant opposite things on two verbs of one tool
([13](13-public-api.md) § 8). And `record()` — the `--json` shape — moved into
this module from `cli.py`, because a shape a consumer regrades from is the
view's contract to promise, not the presentation layer's to invent.

### 10.3 The policy is separable, and named

`classify(change) -> Rule` returns the rule, not a bare severity, so a report
can say *why* and a team can suppress one by name without forking anything.
`RULES` is the whole table.

`other` is `risky` rather than `safe` on purpose. An unrecognised change is the
one case where silence misleads.

This is a policy, and [§ 7](#7-amf-was-assessed-and-not-adopted) says policy
above RAML conformance belongs to a consumer. That line stands: backward
compatibility follows from the spec's own semantics — `required`, and which
side of the wire consumes a value — unlike "every operation must be
documented", which is genuinely org-specific. The structure keeps both true:
the change list is the contract, `--json` carries it whole, and the grading is
one consumer of it.

[18](18-linting.md) amends the line by making its provenance test explicit:
policy derived from RAML semantics may ship in a view, policy derived from a
published security standard may ship off by default, and organisation-specific
taste remains a plugin concern. The operation-description example remains in
that last group.

### 10.4 Edges are diffed, not only nodes

A **reference** can change while every node stays exactly where it was.
Swapping an operation's `securedBy` from OAuth 2.0 to an API key alters no node
and no attribute; so does replacing a body's type with a structurally identical
one. The first version compared nodes and their attributes only, reported *no
changes at all* for both edits, and exited 0 — for a tool whose contract is
"exit 1 if breaking", the worst answer available.

Only reference edges are compared: `securedBy`, `inherits`, `aliasOf`,
`appliesTrait`, `appliesResourceType`, `annotation`, `recursionHead`. A
containment edge — `property`, `payload`, `returns` — cannot change without the
node at its end being added or removed, so diffing it would repeat what the node
already said.

A swap arrives as an `unlinked` and a `linked`, not as an opaque "changed":
which target went and which arrived is exactly what decides whether the swap
breaks anyone. `securedBy` is graded outright — requiring a credential where
none was required refuses every existing caller. The rest are `risky`: an
inheritance or annotation now naming something else is a real change whose
effect this cannot compute.

### 10.5 What it does not do

- **A rename reads as a removal and an addition.** No similarity matching:
  correct, noisy, and better than a confident wrong guess about which old name
  became which new one.
- **Structural equality is not semantic equality.** Two types with identical
  properties and facets are indistinguishable here even if they mean different
  things to a reader — replacing `Ref` with `Other`, both `type: string`, is
  reported as a retargeted reference and graded `risky` rather than judged.
- **Examples and descriptions** are `cosmetic`. Nothing on the wire changed.

## 11. The effective document as a tree

`graph` answers *what points at what*. `tree` answers *what is here*.

The module is `fastraml/views/tree.py`, named for the shape of what it emits.
Every module in that package is a view of the effective model, so "effective"
is the word they share and cannot be the one that tells them apart: `graph`
emits a node set, this emits containment.

```bash
fastraml tree api.raml            # the whole document, addressed
fastraml tree --positions api.raml
```

```python
from fastraml import ParseOptions, build_tree, parse_from_path

document = build_tree(parse_from_path('api.raml', ParseOptions(unwrap=True)))
```

### 11.1 Why it is not the graph, and not a filter of it

The two are lossy on **orthogonal** axes, so neither can be derived from the
other:

| | leaf data | identity |
|---|---|---|
| model | complete | complete (`id` per entity) |
| graph | drops what nothing points at (§ 4) | addresses, and references as edges |
| tree | complete | collapsed into nesting |

The graph's edges cannot be recovered from a tree whose references are names,
because names are not unique. The tree's examples and defaults cannot be
recovered from the graph, because they are not in it.

**Not for performance.** `attributes` is a `@property`, computed on access, and
`walk`/`out`/`into` read the edge indexes only — traversal never touches it, so
carrying more data would cost a query nothing. The constraint is `Literal_`:
node attributes are scalars, and an example is a structured `DataNode`. The
reason is shape, not speed. A renderer wants containment; a query wants
adjacency; api-console demonstrates the cost of asking one to be the other —
its first act on receiving AMF is to re-nest the flat graph into a tree, at 2.4x
to 5.9x the memory.

### 11.2 References are addresses

A tree meets a cross-reference at four places, and each was a **name** until the
addressing pass existed:

| | was | is |
|---|---|---|
| recursion | `{"recursive_ref": "Chain"}` | `{"$ref": "fastraml://id#/declarations/types/Chain"}` |
| `inherits` | `["Named"]`, or the parent inlined when anonymous | `[{"$ref": "…/types/Named"}]`, or inlined — § 11.3 |
| an alias | `"alias_of": "A"` | `"alias_of": {"$ref": "…/types/A"}` |
| an annotation | `["tier"]` | `[{"name": "tier", "type": "…/declarations/annotations/tier"}]` |

A name cannot say which of two libraries declaring `paged` was meant — the same
ambiguity § 3.2a removes from the graph — and an anonymous shape has no name at
all.

### 11.3 A supertype is referenced or inlined, and which one is not a style choice

A **declaration** is listed under `types` in this same document, so `$ref` loses
nothing, and repeating it would make one type read differently depending on
which subtype you arrived through.

An **anonymous** supertype is in no other part of the tree — `type: integer |
number` with a facet beside it gains one per member when P9 distributes the
facet (§ 8c) — so referencing it would delete it from the only view that carries
it. The graph node at its address holds § 2.5's literals, not the shape, so
"follow the address into the graph" is not a recovery. It is inlined, and
carries its own `id`.

The test is membership of the declaration maps, not the shape of the address:
a nested anonymous shape inside a declaration carries `#/declarations/` too.

An **alias** parent inlines for the same reason, which also makes the alias hop
visible rather than reporting the referent's name in its place — the wrong
answer docs/07 § 3.6 warns about. It costs nothing measurable: on a 2000-type
document, referencing alias parents instead produced an output of identical
size.

Both outputs come from one `Walk`, so **an address printed by `tree` names
the node printed by `graph`**. That join is the whole point; law 15 asserts it.

### 11.3a A typed fragment is a declaration

A `#%RAML 1.0 DataType` document is one declaration, and `fragment_types` lists
it only when some document's `types:` included it. As the **entry point**
nothing lists it, and reading only that map projected a document whose entire
content is a type as having none — silently, because an empty map is what a
document with no types looks like. `fastraml tree user.raml` printed
`"types": {}`.

Only the entry point is folded in. An included fragment is already listed under
the name that included it, and the graph addresses its shape *under* that
declaration — `…/types/User/inherits/user.raml` — rather than top-level, because
`types: {User: !include user.raml}` is a **link**: two shapes, two ids, and the
one the walk reaches first keeps the declaration address. Adding a top-level
entry for the second would invent a declaration the graph does not have.

### 11.4 What it carries that the graph does not

Examples, defaults, `xml`, `allowedTargets`, custom facet *values*, type
expressions, and every container inline. `kind_facets` is driven off
`copyable_slots`, so this is wider than `facets_of` by construction: a tree
places its members rather than pointing at them, so it needs the containers as
well as the constraints.

Plus what a **reader** needs and a traversal does not. Each of these reached no
view at all, and each omission was invisible, because an absent key looks
exactly like a document that did not say it:

| | where | why a reader needs it |
|---|---|---|
| `baseUriParameters` | `entry_point` | `{tenant}` is a value every caller supplies; without it no request can be built |
| `documentation` items | `entry_point` | the prose pages of an API are not reachable from any type |
| `displayName`, `description` | an endpoint | an operation had both and its resource had neither, which is what a navigation pane is built from |
| `settings` | a scheme | the OAuth 2.0 URLs, grants and scopes |
| `describedBy` | a scheme | the headers and query parameters a secured request must carry |
| `annotations` | an endpoint, operation, response | see below |

**Security schemes are a section of their own**, keyed by file then name like
`types`. The settings belong to the declaration, and a `securedBy:` entry
already points at it by address — the rule § 11.3 applies to a supertype.
Repeating a scheme at every use site would be the duplication that rule exists
to prevent.

**A scheme is projected through the link it holds, under the name that holds
it.** `oauth2: !include scheme.raml` decodes to a definition carrying a link
and nothing else — no `type`, no `settings`, no `describedBy` — and the
SecurityScheme fragment it points at is one scheme rather than a
`securitySchemes:` map, so the section built by walking the fragments never
reaches it. The content therefore comes through `resolved()`, which is the
security-scheme form of the rule § 2.4 states for a type alias: a traversal
that stops at the holder reports the wrong answer rather than an error, and
here the wrong answer is an empty scheme beside a use site that says it is
bound, because P5 applies what `resolved()` gives. The **name and the address
stay the holder's**: `oauth2` is what `securedBy:` writes and what a use site's
`declaration` points at, while the link target is named for its file.

**An annotation is recorded where it was applied, with its value.** The
document-wide `annotations` list gives `target: "Resource"` — a *kind*, not an
address — so a reader could see that something was deprecated and not what.
`shape` had always pointed at the annotation type; an endpoint, an operation and
a response now do the same, and each site carries the value as well as the type.
Without the value a site says a thing is deprecated and not what to use instead,
and the document-wide list cannot supply it: keyed by kind, three `Method`
entries with three different messages are three rows nothing tells apart.

Two fields answer different questions and both are kept: `bound` says a
`securedBy` entry resolved, `declaration` says where to. A `securedBy: [null]`
entry binds to a synthesised definition that no document declares, so it is
`bound` with no address — reporting only the address would make it read as
unbound.

### 11.4b Three more that were shapes of a value rather than its absence

The omissions above were keys the projection did not write. These three were
written, in a form that carried less than the model held — the harder kind to
find, because the key is there and a consumer reading it gets an answer.

**`entry_point.secured_by` — what secures the API by default.** `securedBy:` at
the root is harvested before the main decode and lands on `Raml`, not on the
fragment, so the section built from the fragment never saw it. Nothing was
*lost*: P5 gives the same list to every endpoint that declared none, so a reader
can find it on any operation page. What was lost is that it is a **default** —
the difference between one endpoint to check and all of them, which is the
question a root page exists to answer. 6 documents across the corpus declare
one.

**An example is a record, not a bare value.** RAML's form B writes the value
under `value:` beside a `displayName`, a `description`, a `strict` flag and
annotations of its own; all four reached `Example` and none reached a consumer.
15 across the corpus, and silently: an example with no metadata and one whose
metadata was discarded project identically. `strict: false` is the sharpest of
them, because it is *why* such an example is in the document — it marks one that
deliberately does not validate, and dropped it reads as one that does.

Always a record, on `example:` as well as on every entry of `examples:`. A
consumer that has to test which form arrived is what § 11.4a exists to prevent,
and the same argument settles it here: the shape follows what the facet *means*,
not whether this instance happens to carry anything extra.

**`declared_facets` is a map, spelled as `properties` is.** A `facets:` entry is
a `Property` in every respect — it has a type and it is required or it is not —
and this emitted a sorted list of names. So a consumer could say that `Nameable`
demands `onlyIn` and neither that it demands a string nor that `since?` is
optional: **19 of the corpus's 82 declarations name a non-string type and 7 are
optional**. Sorting them broke the declaration-order invariant (docs/02 § 4) at
the last step, the same way `sort_keys` did.

Renamed from `declares_facets` rather than redefined under it. An old consumer
now gets `undefined` — loud — where keeping the name would have handed it a map
where it indexed a list.

### 11.4a A bound is an exact decimal string; a count is a number

`minimum`, `maximum` and `multipleOf` are decimal text on **every** kind that
declares one — `"0.01"`, `"9223372036854775807"`, `"1.7976931348623157E+308"`.
`minLength`, `maxItems` and the rest are JSON numbers. The split follows what a
facet *means*, not how large its value is, so a consumer never has to ask which
form arrived.

**A string, because JSON's number is a double.** It is arbitrary precision on
paper and a double in every consumer that matters, so a bound written as one is
rounded on the way in: `9223372036854775807` reads back as `…808`. The parser
holds a bound exactly and never passes one through `float` (CLAUDE.md), and a
JSON number undoes that at the last step. A count is bounded by what fits in
memory and has no such ceiling.

**A decimal, not the exact ratio.** `Fraction` is what the model holds and
`str(Fraction)` is exact, but a float-valued bound near the top of its range is
an integer, so its ratio is that integer: `1.7976931348623157e308` spells out to
309 digits, 292 of them zeros. A decimal is exact as well and is what the author
wrote. Every numeric scalar RAML can carry is decimal, so every bound built from
one has a terminating expansion and the conversion loses nothing; a ratio with
no terminating expansion keeps the `n/d` form.

Scientific notation only where the plain form passes 21 characters *and* the
exponent is materially shorter, so `100` is `100` rather than `1E+2` and
`123456789012345678901234567890.5` stays as written.

**The conversion belongs to the producer.** `Fraction` has already done the
arithmetic here. Left to consumers, each one needs exact long division to
display a bound, and the one that skips it writes `Number(n) / Number(d)` and
puts the value back through the float the encoding exists to avoid.

**Example and default data is not a bound.** It is the author's payload, an
integer in it has to stay an integer, and a string in its place is a different
value — so it is a JSON number and precision there belongs to the consumer.
`viewer/src/numbers.ts` is what that looks like: it keeps the literal's source
text for anything a double cannot hold.

### 11.5 Positions are separate

`positions_of` projects them apart. A one-line edit shifts every position after
it, and a view that churned on every edit would be regenerated unread, which is
the same as not having one (docs/14 § 2).

### 11.6 It is also the golden layer

`tests/golden/project.py` is a two-line wrapper. The property that made it worth
promoting is the one the goldens already relied on: driven off `__slots__`, so a
facet added to a kind cannot go missing from it.

### 11.7 The law a consumer may rely on

> **A consumer descends containment, follows a link, and stops at a recursion
> marker. It maintains no ancestor set.**

That is the whole contract, and `unwrap=True` is what buys it. Nine passes of
RAML logic happen before a consumer sees anything:

| | who does it |
|---|---|
| `!include` resolution, `uses:` namespacing | P1 |
| type expressions — `(A\|B)[]`, nested arrays | P6/P7 |
| inheritance merge, facet narrowing, multiple inheritance | P9 |
| trait and resource-type application, optional-method filtering, the four priority classes | P4a |
| overlays and extensions | P3 |
| security scheme binding, OAuth scope narrowing | P5 |
| annotation type binding | P8 |
| default `mediaType`, baseUri parameter propagation | P4/P6 |

What is left is two operations. `tests/unit/test_consumer_traversal.py` is the
executable statement of that: a walker with no `seen` set, no depth budget, and
no knowledge of RAML.

**A recursion marker is not a link, and merging them would break the law.**
A cycle can close through a property, an array's items or a union member, and
P9 marks each — verified for all three routes plus mutual and three-deep
cycles. Without the marker a naive walk cannot tell a repeat from a fresh
subtree, and every consumer would need its own ancestor set. Three of the four
renderers in the wider ecosystem reach the same design independently: Redoc's
`x-circular-ref`, Stoplight's `MirroredRegularNode`, and swagger-js leaving the
`$ref` in place. Scalar is the one that merges them, and pays with an
ancestor-set guard in its renderer.

Putting the marker in `type` rather than in a separate key is deliberate: a
consumer that switches on `type` and has not handled `recursive` gets an
unrecognised value — a loud failure — where a separate key would be silently
ignored and hang.

### 11.8 What still leaks, and the rule that decides it

> A marker belongs in the projection when it describes a property of the
> **data**. It is a leak when it describes a property of the **parser**.

A cycle is a property of the data. An alias is not.

`Prices: Price[]` puts an *alias* of `Price` under `items` (docs/07 § 3.6), and
the projection currently emits the alias node:

```json
"items": { "name": null, "type": "object",
           "inherits": [{"$ref": ".../Money"}], "alias_of": {"$ref": ".../Price"} }
```

A consumer reading that honestly concludes *an array of anonymous objects
extending `Money`* — `Price`'s supertype. That is a wrong answer, not a missing
one. Across the TCK corpus: **427 alias nodes, 200 reading as anonymous, 50
carrying a misleading `inherits`.**

Nothing is lost by making it transparent. An alias shares its referent's
containers, so it holds no facets of its own; `items` should be
`{"$ref": <Price>}`. The law's test carries this as a strict `xfail` until it
is.

**`annotationTypes:` is a section of its own**, keyed by file like `types` and
`security_schemes`. Without it every `(name):` application pointed at an address
the tree did not contain — **318 of 318 across the corpus**. Law 15 did not see
it: that law resolves addresses against the *graph*, which does carry annotation
types. A tree consumer has only the tree, so `test_consumer_traversal.py`
resolves them against the tree instead.

**An alias never reaches the output.** `Prices: Price[]` emits
`items: {"$ref": <Price>}`. All 267 alias shapes in the corpus target a
declaration, so the reference always resolves, and an alias holds no facets of
its own — the inlined copy was pure duplication on top of a wrong answer. Two
goldens shrank by 133 lines.

**A cycle is always a marker, never a bare link.** The projector closes a cycle
P9 did not mark, and used to close it with `{"$ref": …}` — 31 times across the
corpus. A consumer expanding links cannot tell that from an ordinary reference,
so it would re-enter and loop. It now emits the same marker P9 does: one
meaning, one spelling.

`RecursiveShape.head` is the exception, and stays a bare `{"$ref": …}`. It is a
**back-pointer**, not containment: the node holding it already carries the
marker, so expanding there would mark one cycle twice.

By the same rule, and now applied:

| | verdict | evidence |
|---|---|---|
| `kind` | removed — redundant with `type` | 1:1 across all 16 kinds, 879 documents, **zero** ambiguous pairs |
| `link` | removed — which fragment a declaration arrived through is parser state | |
| `is_annotation_type` | removed — `false` on every shape; `annotation_types` is the section that says it | |
| `type_expr` | **kept** | what the author wrote is data, not parser state |

### 11.10 The metamodel

What a consumer sees is a closed set, and that closure is the point: AMF's
contribution is a *declared vocabulary* a consumer can read a spec of, rather
than an implementation's field list. Narrowed to RAML it is ten node types,
against AMF's fifty-odd across five vocabularies.

```
Document          title, version, base_uri, base_uri_parameters, protocols,
                  media_types, documentation, types, annotation_types,
                  security_schemes, endpoints, annotations
Resource          display_name, description, uri_parameters, operations,
                  secured_by, annotations
Operation         display_name, description, protocols, query_parameters,
                  headers, query_string, bodies, responses, secured_by,
                  annotations
Response          description, headers, bodies, annotations
Body              the shape it carries, keyed by media type
Parameter         binding, required, type
SecurityScheme    type, settings, described_by, annotations
Annotation        name, type
Documentation     title, content
Shape             type, plus that type's own facets; properties, items and
                  any_of are containment. A `json` shape adds json_schema
                  and projection
```

A `json` shape is the one kind that carries the same type twice, and it has to.
The spec forbids a JSON-schema type from participating in inheritance or
specialization, so the parser decodes no RAML facet from it: the shape has no
properties, no items and no facets, and a consumer reading only that reports a
type made of nothing. `json_schema` is the schema in its own vocabulary, and
`projection` is the § 6.3 projection of it onto the nearest RAML shape, where
`#/definitions/line` is an ordinary nested type. Neither is derivable from the
other by a consumer: the first needs a JSON Schema implementation to read, and
the second has already thrown away whatever § 6.3 could not express.

**`json_schema` is the resolved document, and a JSON value rather than a
string.** A `$ref` naming another file names nothing a consumer of the tree
has — the tree is one document and the directory the schema was written in is
not in it — so `as_schema` pulls every such reference in under `definitions`
and rewrites it to a local pointer. What arrives validates the same instances
and needs nothing else to read. A pointer *within* the document stays a
pointer: it is followable where it stands, inlining it discards the sharing the
author expressed, and `#/definitions/node` inside `node` has no finite
expansion.

A pointer inside a pulled-in file does **not** stay a pointer, because it is a
pointer into that file and the result is not that file. Left alone it names
whatever the bundle happens to have at the same path, and a schema that
validates something other than what its author wrote is worse than one a reader
cannot follow.

Nested rather than merged onto the shape. A schema carries its own
`description` and `example`, and so does the RAML declaration wrapping it;
merging would pick a winner between two things the author wrote separately.

**A consumer reads the projection as the type.** It is where a schema type's
structure and constraints are, so a view that names types, lists their
attributes or asks what one contains follows `projection` first and gets an
`object` with properties rather than a `json` with nothing — `json` is the
mechanism the type arrived by, not what it is. `json_schema` is then one more
thing a reader can open, like any other. `viewer/src/model.ts` does this in one
accessor, `contentOf`, which is what keeps a schema type from being a branch in
every block that renders a shape.

Every node carries `id`, its address. Three constructs and nothing else:

| | means |
|---|---|
| `{"$ref": <address>}` | a link — the target is a declaration, look it up |
| `{"type": "recursive", "head": {"$ref": …}}` | the structure repeats from here; do not expand |
| anything else | containment — descend |

The vocabulary is the model's own field names (§ 11.9), so this list is not a
translation of the model but a projection of it with the parser's states left
out.

### 11.9 The vocabulary is the model's field names

`min_length`, not `minLength`. The projection is the model serialised, so there
is no translation layer and no table to drift.

The consequence is deliberate: **the wire format versions with the model.**
Renaming a field is a published change. What makes that tractable rather than
reckless is that the golden layer holds the same names, so no rename can happen
without a golden diff showing exactly what a consumer will see.

The graph keeps RAML's spelling — `raml:minLength` — because those are RDF
predicate IRIs in a published vocabulary, a different naming system for a
different purpose (§ 2). The two disagreeing is by design, not drift.

### 11.11 The contract is generated

`fastraml/views/bindings.py` writes `viewer/src/tree.d.ts` — the same key list as
TypeScript declarations, for a consumer outside Python. Hand-written it would go
stale the first time a kind grew a facet, and stale *quietly*: a key the
declarations omit still arrives, and a consumer that does not read it looks like
a document that did not say it. That is law 14's argument, applied across a
boundary no type checker spans.

Two things are derived, and both by reading source rather than importing it:

| | from | how |
|---|---|---|
| which keys arrive, and which are optional | `tree.py`'s own AST | every `_Projector` method's opening display, its `out[...] =` stores, and its `for field in (...)` tuples; a key written under an `if` is `?` |
| a kind's facets and their types | the `self.x: T` annotations in each kind's `__init__` | Python keeps no runtime record of these, so nothing but the AST has them |

The value type of a *structural* key is not derivable — `out['operations']` is
an expression — so those are declared in `_STRUCTURAL`. Only their types: the
key sets come from the AST, so **a key added to the projection and not declared
there fails generation, by name**. The hand-written half cannot fall behind,
because it is not the half that says which keys exist.

**The data beside the contract is held to the same standard.**
`viewer/public/api.json` is what `npm run sample` writes and what the viewer's
two gates — `smoke` and `shots` — then read. It was not checked, so a change to
the projection left both of them running against the *previous* shape of the
tree: they pass, because a page rendered from old data is still a page, and what
they stop measuring is the emitter. `TestTheViewerSampleIsNotStale` regenerates
it and compares, which is the same idiom as the goldens and as the check above
this one — compared as parsed JSON, since the file is written through a shell
redirect and its line endings are the platform's.

Found the way it would be. Wrapping an example in a record left `items.example`
a bare string in the committed sample, the type page dropped it, and the only
thing that noticed was the reachability check, by a route that had nothing to do
with examples.

Three things this found on its first two runs, none of which any test could see:

- **`JsonShape` leaked its compiled validator.** `kind_facets` walks
  `copyable_slots` and skipped only names beginning `__`, so `_compiled`,
  `_cached_shape`, `_cached_defs` and `validator` were projected — the first as
  a Python `repr` carrying an **absolute filesystem path**. Now one rule: a
  leading underscore is a working buffer. `raw` goes too, being the schema text
  `type_expr` already carries.
- **A facet's spelling comes from what the emitter does with it**, not from the
  annotation the model declares. `minimum` is a `Fraction` on a number and an
  `int` on an integer, and both are emitted as a decimal string (§ 11.4a), so
  the annotation alone is right for neither kind. `_EXACT` is read out of
  `tree.py` for the same reason `_BACK_POINTERS` is: two copies of one list are
  two things to forget.
- **Three keys were missing from the first generated file.** `shape()` has two
  `for field in (...)` loops and both call the variable `field`; a last-wins
  mapping kept only the second, dropping `display_name`, `description` and
  `required`. Caught by law 19, which is why that law asks the corpus rather
  than the generator.

## 12. A shape as JSON Schema

`views/jsonschema.py`. `to_json_schema(shape)` returns a draft-07 document and a
list of what it could not carry. Its shape dispatch follows go-raml's, which is
where the decisions below were checked against a second implementation.

It is a view in the same sense `render` is: a projection of one entity, deciding
no RAML rule, importing nothing from `views/walk.py` because it addresses
nothing — a schema refers to types by name, which is what JSON Schema has.

### 12.1 What the shape of the output is

```json
{ "$schema": "http://json-schema.org/draft-07/schema",
  "$ref": "#/definitions/Node",
  "definitions": { "Node": { … } } }
```

**Only the entry point and recursion heads become definitions.** Everything else
is written where it stands. This is the one thing easy to get wrong: a
*property's* shape carries the property's name, so a rule of "a named shape is a
definition" hoists `name`, `tags` and every `items` into `definitions` and then
refers to each from the single place it is used.

**A name is occupied before its body is walked.** The entry is set to `{}` first,
so a type that reaches itself finds it already there. Without that the walk does
not terminate.

### 12.2 Decisions that are not obvious

| | |
|---|---|
| **The entry point must be unwrapped** | Invariant I12. An un-flattened shape carries only what its own declaration wrote, so the schema would silently omit every inherited facet. go-raml refuses the same case. |
| **A `RecursiveShape` emits an *empty* schema plus a `$ref`** | Not one built from its base. Every RAML type may carry custom facets and those can be recursive too, so building the base first is how the walk fails to terminate. A `$ref` ignores its siblings per the spec, so nothing is lost. |
| **A pattern property's key is already bare** | RAML writes `/^x-/` and `patternProperties` keys are bare regexes — but P2 strips the delimiters at decode, so there is nothing to strip here. go-raml slices them off because its own model keeps them. |
| **Numbers do not pass through `float`** | A facet holds a `Fraction` built from the raw text ([10](10-validation.md) § 5.3). An integral one is written as an integer; the rest as their shortest decimal. `multipleOf: 1.1` stays `1.1`. |
| **A `JsonShape` hands back what the author wrote** | It is already a JSON Schema, held as its source text. Projecting it through the RAML model and back would be a round trip that can only lose. |

### 12.3 What JSON Schema cannot carry

Reported in the second return value, never dropped in silence. RAML's `fileTypes`
is a list and `contentMediaType` is one value, so the rest are named. A
`datetime` with `format: rfc2616` has no JSON Schema format at all, so the
grammar is written out as a `pattern` — go-raml's spelling, character for
character. `datetime-only` is the same case.

### 12.4 How it is gated

`tests/unit/test_jsonschema_view.py`, and the half that matters is differential:
for each value, the RAML shape and the emitted schema must reach the same
verdict, checked with the `jsonschema` library rather than with this project's
own reader. A structural comparison would pass while the schema said something
subtly different; agreeing on instances is the claim worth making.

## 13. An API as OpenAPI 3.0.3

`views/openapi.py`. `to_openapi(raml)` returns a typed `OAS3Document` and a list
of information the target format could not carry. Every OpenAPI object is a
slotted, non-equality dataclass, so nested values stay discoverable and
statically typed instead of collapsing into `dict[str, Any]`. `to_dict()` is the
separate wire projection, and the CLI writes that value as YAML or JSON with
`fastraml openapi`.

The mapping: the input must be unwrapped; effective resources become flat
`paths`; URI, query and header parameters keep their binding; bodies become
media-type `content`; and recursive types occupy a component name before their
body is walked.

**A component per named type, everything else inline.** Named means a `types:`
block named it — the API's own *and every library's* — or the projection did,
which it does for the subschemas a `$ref` can address (§ 3.2b). Leaving the
libraries out is not a small loss: one `lib.errorScheme` on a corpus API was
written out under 539 responses, and naming them took that document from 907 KB
to 616 KB with nothing dropped. Names are claimed API-first, so its own
vocabulary survives intact and a colliding library type becomes `roles_lib.paged`
rather than replacing what is there. Unused declarations are still not exported.

A schema document is reached both as the RAML type that included it and as
another schema's `$ref`; `by_uri` joins those onto one component. A RAML type
that says something the schema does not is a subtype rather than a second name
for it and keeps its own, because sharing would put one type's description under
every `$ref` to the document.

**A `$ref` with siblings is written as an `allOf`.** OpenAPI 3.0's Reference
Object *replaces* what sits beside it rather than refining it, so
`{$ref, description, nullable}` silently says only what the target says — the
`oneOf: [null, $ref]` a schema writes for a nullable date loses both. The wrap
lives in `OAS3Schema.to_dict` and not at each site that builds one, which leaves
the model free to set a facet beside a `$ref` and to take it off again: a body's
example belongs on the Media Type Object and a parameter's description on the
Parameter Object, and both are moved there after the schema is finished. What a
use site adds is measured against its referent (`_subtract`), because after
unwrap it carries everything it inherited and would otherwise repeat all of it.

`baseUriParameters` feed `servers.variables`, whose defaults must be strings:
the parameters are typed, so a scalar default is carried as that type
(`default: 443` on an integer parameter is an int) and emitted stringified.
A default with no string form falls back to the variable name and is reported.

The schema half has its own typed visitor. Reusing § 12's dictionary visitor
would collapse every nested `OAS3Schema` back to `dict[str, Any]`, defeating the
model this view exists to expose. It follows the same shape dispatch decisions,
then applies the OpenAPI 3.0 differences (`nullable`, singular `example`, integer
formats, `format: binary`, XML and discriminator). It imports no OpenAPI library.

RAML annotations become `x-<name>` fields. OAuth 1.0, Pass Through and custom
security schemes have no OpenAPI 3.0 equivalent; they use an HTTP bearer shell,
retain their RAML identity under `x-raml-*`, and add an entry to `dropped`.
Custom facets and OAuth grant URIs that name no standard flow are also reported,
never silently discarded. The CLI prints these notices to stderr so stdout stays
a valid OpenAPI document.

`tests/unit/test_openapi_view.py` pins metadata, servers, documentation, paths,
parameters, bodies, lazy components, recursion, nullable unions, query strings,
security, annotations, loss reporting, and the unwrapped/API preconditions.
