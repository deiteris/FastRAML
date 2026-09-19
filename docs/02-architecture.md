# 02 — Architecture

## 1. The pipeline

Parsing is a fixed sequence of passes over a shared registry. Each pass has a
single responsibility and a documented precondition; nothing runs out of order.

```
                    ┌──────────────────────────────────────────────┐
  entry file ─────► │ P0  identify fragment kind (first line)      │
                    └──────────────────────────────────────────────┘
                                      │
                    ┌──────────────────────────────────────────────┐
                    │ P1  compose YAML → Node tree (per file)      │
                    │     · !include resolved + cached on demand   │
                    └──────────────────────────────────────────────┘
                                      │
                    ┌──────────────────────────────────────────────┐
                    │ P2  decode fragment                          │
                    │     · uses:/types/traits/RTs/securitySchemes │
                    │     · shapes built eagerly where the type is │
                    │       known, as UnknownShape where it is not │
                    │     · endpoints NOT decoded: stage-1 IR only │
                    └──────────────────────────────────────────────┘
                                      │
                    ┌──────────────────────────────────────────────┐
                    │ P3  resolve uses: (recursive P0–P2 per lib)  │
                    └──────────────────────────────────────────────┘
                                      │
             API only ─►┌──────────────────────────────────────────┐
                        │ P4  build endpoints (two-stage)          │
                        │     4a resolve directives on the IR:     │
                        │        resource types, then traits,      │
                        │        by structural YAML merge          │
                        │     4b materialize IR → EndPoint tree    │
                        │        under a provenance overlay        │
                        └──────────────────────────────────────────┘
                                      │
             API only ─►┌──────────────────────────────────────────┐
                        │ P5  apply security schemes               │
                        │ P6  propagate URI parameters downward    │
                        └──────────────────────────────────────────┘
                                      │
                    ┌──────────────────────────────────────────────┐
                    │ P7  resolve shapes (drain unknown worklist)  │
                    │     · type expressions parsed here           │
                    │     · references bound to their definitions  │
                    └──────────────────────────────────────────────┘
                                      │
                    ┌──────────────────────────────────────────────┐
                    │ P8  resolve domain extensions (annotations)  │
                    └──────────────────────────────────────────────┘
                                      │
       opt-in  ────► ┌──────────────────────────────────────────────┐
                     │ P9  unwrap: flatten inheritance in place     │
                     │     + mark recursion                         │
                     └──────────────────────────────────────────────┘
                                      │
       opt-in  ────► ┌──────────────────────────────────────────────┐
                     │ P10 validate: check() + instance validation  │
                     └──────────────────────────────────────────────┘
```

Two orderings are required for correctness:

- **P4 before P7.** Templates contribute new type-bearing subtrees; those must be
  turned into shapes *before* the shape worklist is drained, or they never get
  resolved. go-raml orders the two passes the same way.
- **P7 before P9.** Unwrapping merges a shape with its parents; a shape whose
  concrete kind is still `UnknownShape` cannot be merged.

## 2. Package layout

```
fastraml/
  __init__.py             lazy public API exports (see doc 13)
  __init__.pyi            eager declarations of that surface for type checkers
  cli.py                  the `fastraml` console script (doc 13 section 8)
  config.py               common parser/lint/compatibility configuration decoder
  config.raml             the closed shape of that common YAML configuration
  py.typed

  views/                  everything that reads the finished model (doc 16).
                          Not passes: nothing under parser/ or types/ imports it
    walk.py               one traversal of the effective model, addressing what
                          it reaches; every view is a sink over it (doc 16 section 3)
    graph.py              the model as a node set — identity and reference (doc 16)
    tree.py               the model as containment — what is here (doc 16 section 11)
    render.py             one type or endpoint as text, for reading (doc 16 section 9)
    queries.py            the named SPARQL analysis catalogue (doc 16 section 6)
    backward/             model-native backward compatibility (doc 16 section 10)
      model.py              coordinates, results and grades — what it can say
      rules.py              every named judgement and what it does to a caller
      compare.py            the walk over two effective models
      markdown.py           the reading view of a finished comparison
      records.py            the JSON record, and the policy that matches it
    lint/                 generic rule engine, built-in policy and plugins (doc 18)

  errors.py               Diagnostic, StackTrace, Accumulator, ErrorKind
  positions.py            Position (1-based, with end), position helpers
  domains.py              DomainLocation — where an annotation was applied (doc 09 §B5)
  uris.py                 path↔file:// URI, RFC 3986 reference resolution
  loaders.py              ResourceLoader protocol; File/SafeFile/HTTP/Scheme loaders

  yamlnode.py             Node model + composer adapter (doc 03)
  datanode.py             DataNode / ValueNode: structured user data (doc 03 §6)

  registry.py             Raml — the central store, caches, id counter, ParseCtx stack

  parser/
    entry.py              parse_from_path/parse_from_string/parse_lenient; the P0–P10 driver
    includes.py           !include resolution, node cache, size limit
    fragments.py          all fragment classes and their decoders (doc 04)
    references.py         resolve_reference / resolve_library_reference (doc 04)
    facets.py             scalar-facet *builders*, annotated-scalar form
    documentation.py      DocumentationItem
    annotations.py        DomainExtension (doc 09)
    security.py           SecuritySchemeDefinition, its settings, and P5 (doc 09)
    templates.py          variable index, substitution, transform functions (doc 08)
    traits.py             TraitDefinition + applying traits to an operation (doc 08)
    resourcetypes.py      ResourceTypeDefinition + applying one to a resource (doc 08)
    directives.py         DirectiveRef and SecurityScheme: the reference forms
    source_ir.py          stage-1 SourceEndPoint / SourceOperation (doc 08)
    structural_merge.py   spec merging algorithm + provenance overlay (doc 08)
    source_decode.py      stage-2 materialization (doc 08)
    endpoints.py          EndPoint, Operation, Request, Response, Body
    endpoint_build.py     P4 and P6: both stages, then URI parameter propagation
    uritemplates.py       RFC 6570 L1/L2 parsing and parameter synthesis

  types/
    base.py               BaseShape, Property, PatternProperty, ScalarFacet, the Shape protocol
    shape.py              make_shape / make_body_shape / make_property; kind dispatch (doc 05 §4)
    resolve.py            P7: the worklist drain *and* the AST → shape visitor (docs 06 §3, 07 §§1–2)
    inference.py          identify_shape_type, default-type rules (doc 05)
    scalars.py            String/Number/Integer/Boolean/Date*/File/Nil shapes
    complex_.py           Object/Array/Union/Unknown/Recursive shapes
    examples.py           Example, Examples (doc 05 §6)
    xml.py                XmlSerialization (doc 05 §8)
    inherit.py            per-kind inheritance rules (doc 07)
    unwrap.py             unwrap driver + recursion marking (doc 07)
    jsonschema_.py        JsonShape + the shared schema registry (doc 10 §6)
    values.py             numeric comparison, uniqueItems, date grammars (doc 10 §§5.2–5.3)
    validate.py           the P10 driver: examples, defaults, facets, annotations (doc 10)
    expressions/
      lexer.py            RDT tokenizer (doc 06)
      parser.py           RDT recursive-descent parser; memoised on a caller-supplied cache
```

**Nothing in `views/` is a pass.** Each module runs after the pipeline has
finished, over a model that is already unwrapped, and decides no RAML rule. The
dependency runs one way — they import the model and nothing in the model imports
them — which is what keeps them consumers rather than stages
([16](16-graph.md) § 1).

That is a package rather than six modules beside the parser so the direction is
checkable: `tests/unit/test_views.py` asserts that nothing under `parser/` or
`types/` imports `fastraml.views`, and that `cli.py` is the only module outside it
that does. A rule that belongs to the language belongs in a pass, and an import
the other way is how one quietly stops being one. `queries.py` is inside because
the package boundary *is* the layer boundary; it consumes a `Graph` rather than
the model, but a package holding only part of the layer would not be worth
enforcing.

`walk.py` sits under the rest rather than beside them. It owns the one traversal
that assigns an address to every referenceable entity, and each view is a `Sink`
over it. Assignment depends on visit order, so two views that walked separately
would address the same entity differently and their outputs could not be joined
([16](16-graph.md) § 3). `graph.py` and `tree.py` are named for the *shape* of
what they emit — a node set against containment — because every module here is a
view of the same effective model, so the input is not what tells them apart.

`viewer/` is outside the package and outside the gate: a React SPA that reads
`fastraml tree` output and renders it as API documentation. It is a **consumer**,
kept in this repository to be read alongside the format it consumes. It has no
Python dependency and nothing depends on it; the TypeScript backend under
`fastraml/views/bindings/` writes its `tree.d.ts` and is the only link between
them, in that direction only.

Keeping it here has already paid: building it against the tree is what found the
`JsonShape` validator leak, the missing annotation values at their sites, and
the three keys the contract had dropped ([16](16-graph.md) § 11.11). A format
with no consumer is a format whose gaps nothing measures.

`types/resolve.py` holds the AST → shape visitor as well as the driver, rather
than the separate `expressions/build.py` an earlier draft of this list named.
The two are mutually recursive — `resolve_shape` runs the visitor, and the
visitor's `Reference` case calls `resolve_shape` on the referent, which may
still be unknown — and both ways of splitting them are ruled out by the rules
below: passing `resolve` in as a callback is the shape rejected under
`DECLARATION_FACETS`, and a deferred import is the indirection this section
exists to prevent. go-raml separates them only because Go's ANTLR runtime wants
a visitor struct.

Rules on the layout:

- **The three directive references share `parser/directives.py`.** An earlier
  draft of the list above put the `Trait` reference in `traits.py`, the
  `ResourceType` reference in `resourcetypes.py` and the scheme reference in
  `security.py`, each beside the *definition* it points at. But stage 1 decodes
  all three and runs two phases before either of those modules exists, and the
  three have one shape — a name, optional parameters, a position — differing
  only in how the name resolves and what applying it does. One `DirectiveRef`
  rather than three near-identical classes in three near-empty modules.

  The same module holds `SecurityScheme`, the one reference that survives into
  the model. Applying a trait or a resource type produces a merged tree and
  leaves nothing on the reference; applying a scheme produces a *binding*, which
  needs somewhere to live. Keeping it here rather than in `security.py` is also
  what lets `source_decode.py` build these in stage 2 without importing the
  module that resolves them.

- **A template definition's `!include` is followed in `fragments.py`**, not in
  the module that owns the definition. Following it means parsing a fragment,
  which `fragments.py` owns; `traits.py` and `resourcetypes.py` record the target
  URI and let it fill in `link`. The reverse import is what the rule below
  forbids, and a deferred one is the indirection this section exists to prevent.

- **`registry.py` imports nothing from `parser/` or `types/` at runtime**, which
  is what keeps the graph acyclic without indirection — every other module
  imports it. `domains.py` exists because of this rule: `ParseCtx` carries a
  `DomainLocation` and `parser/annotations.py` reads one, so the enum sits in a
  leaf both may import rather than in either of them
  ([09](09-security-and-annotations.md) § B5). It does import `yamlnode`, which
  is below it: the provenance overlay lives on `Raml` and its four readers
  (§ 6.3 of [08](08-templates-and-endpoints.md)) need to tell a mapping from a
  scalar.

- `types/` imports from `parser/` in exactly three places: `parser/facets.py`, for
  the scalar-facet builders; `parser/annotations.py`, for the two functions
  that turn an `(annotation)` key into a `DomainExtension`; and
  `parser/includes.py`, for `note_include_ref`, since a `type:` or `examples:`
  scalar may be an `!include` and the reference has to be recorded where it is
  found. Everything else a shape needs from YAML arrives as a `Node` (from
  `yamlnode.py`, which both layers may import) or as a `DataNode` (from
  `datanode.py`, likewise).

  All three edges are deliberate. `ScalarFacet` is a type-model class and lives in
  `types/base.py`, but *building* one needs the parser twice over: an `!include`
  at a facet position has to be read through the include cache, and the
  annotated-scalar form has to turn `(annotation)` keys into `DomainExtension`s.
  Every one of the fourteen shapes decodes scalar facets, so the alternative —
  threading a builder callback through every `decode_facets` — would cost more
  than the rule protects. Annotations are the same story one level up: they may
  be written on a declaration, and inside `example:`, so the type layer has to
  build them where it finds them.

  None of the three can cycle: `parser/facets.py` imports `types/base.py` and
  nothing else from `types/`, and `parser/annotations.py` and
  `parser/includes.py` import nothing from `types/` at runtime at all.

  **`parser/fragments.py` is not on that list and must not join it.** It imports
  `make_shape` at module level, so a runtime edge back to it from `types/` is
  the one genuine cycle in the layout — which is why the deferred import below
  exists. Where P7 needs something a fragment knows, the fragment supplies it:
  a name is resolved through `BaseShape.anchor`, which is already a
  `ReferenceResolver` object, and the `uses:` entry behind a `lib.Type` prefix
  comes from `ReferenceResolver.library_link` for the same reason. The one
  lookup with no object to hang off — finding the scope for a shape whose
  `anchor` is `None` — goes through `Raml.resolver_at`, an index the fragment
  decoder fills where it already performs the capability check.

- **One cycle-breaking deferred import exists, in `types/shape.py`, and no other
  may be added.** Imports deferred only to keep an optional or unusually heavy
  dependency off the ordinary startup path are allowed when measured and
  recorded in [12](12-performance.md); they must not introduce a hidden edge
  between fastRAML modules.

  `type: !include lib.raml` and `examples: !include e.raml` have to parse a
  fragment, so `make_shape` needs `parser.fragments.parse_fragment`; and a
  fragment declares types, so `parser/fragments.py` needs `make_shape`. That
  recursion is in the language — a type may be a file, and a file declares types
  — not in the module layout, so no ordering of the two modules removes it. The
  import therefore sits inside the two functions that link a fragment,
  `_parse_data_type` and `_parse_named_example`, each with a comment saying why.

  The alternative is to note the include at decode time and link it in P7. It
  works for `type:`, where a worklist already exists, and reads badly for
  `examples:`, where none does. If a third such case ever appears, take it.

  References in the other direction are free, because they are annotations only:
  `BaseShape` names `DomainExtension`, `DataNode`, `DataTypeFragment` and
  `ReferenceResolver` under `TYPE_CHECKING`, which `from __future__ import
  annotations` keeps as strings and never imports at runtime.

- **The per-kind `check`/`validate` are methods; the P10 driver is
  `types/validate.py`; what they share is `types/values.py`, a leaf.** The split
  is forced, not stylistic. `validate.py` reaches `unwrap.py` through
  `_ensure_unwrapped`, and `unwrap.py` imports `complex_.py` — so a kind
  importing a helper *from the driver* closes a cycle. `values.py` imports
  `base.py` and nothing else in the package, and both sides import it.

  This is the opposite call from `inherit`/`alias_to`, which Phase 4 moved *off*
  the kinds into free functions. The difference is that merging constructs a
  `UnionShape` and recurses through a base-level driver, neither of which
  validation does: `check` and `validate` dispatch on kind and recurse into
  their own children, which is what a method is.

- **Inside `types/`, dependencies point one way: `shape.py` → `jsonschema_.py` →
  `scalars.py` / `complex_.py` → `base.py`.** `shape.py` imports the concrete
  kinds to dispatch on kind, so the kinds must not import `shape.py` back. But
  three kinds hold declarations — object `properties`, array `items`, union
  `anyOf` — and only `make_shape` can build a declaration.

  `JsonShape` sits one layer above the other kinds rather than beside them
  because it needs things they do not: `Raml.loader` and `referencing` to compile
  a schema, and — for the § 6.3 projection — the object, array and union classes
  to build a view of one. Putting it in `complex_.py` would point that module at
  the loader; leaving the compiler out of `types/` would put schema knowledge in
  `registry.py`, which imports nothing from `types/` at runtime. The projection
  builds its shapes directly rather than through `make_shape`, so no edge runs
  back up.

  **The kinds declare what they hold; `shape.py` decides how to build it.** Each
  declaration-holding kind carries a class-level table, and nothing else:

  ```python
  class ObjectShape:
      DECLARATION_FACETS = {"properties": PROPERTIES}   # fills two keywords
  class ArrayShape:
      DECLARATION_FACETS = {"items": ONE_SHAPE}
  class UnionShape:
      DECLARATION_FACETS = {"anyOf": SHAPE_LIST}
  ```

  A `DeclarationFacet` names how to read the value node and which constructor
  keywords the result arrives under. `properties:` fills two — `properties` and
  `pattern_properties` — because a `/regex/` key inside it is routed to the
  second (§ 5.1 of doc 05); `patternProperties` is not a key anyone writes.

  `make_shape` knows the kind before it constructs anything, so it reads the
  table off the class, builds those children itself, and passes them in:

  ```python
  cls = KIND_TO_CLASS[kind]
  shape = cls(base, **built)     # a typed __init__; no setattr, no later mutation
  shape.decode_facets(rest)      # one argument; `rest` holds no declarations
  ```

  Nothing flows back from `shape.py` into the kinds — not a function, not a
  protocol, not a field on `Raml`. Fourteen kinds never learn that a builder
  exists.

  This also puts `properties:` on the same footing as the five other places a
  declaration appears. One `make_property` serves `properties`, `headers`,
  `queryParameters`, `uriParameters`, `baseUriParameters` and `facets`
  ([05](05-type-model.md) § 5), and the other five are built by their caller.
  Only `ObjectShape` would have been asked to build its own children.

  The cost: an object's facets are decoded in two places. `properties:` is read
  in `shape.py`, `minProperties:` in `ObjectShape`. One table, in the class, is
  where a reader finds out.

  Four alternatives were rejected:

  - `decode_facets(facets, make_shape)` — fourteen of seventeen implementations
    carry an argument they never read, in a protocol doc 05 § 1 publishes.
  - A deferred import inside the three methods. The usual fix, but it is exactly
    the runtime indirection this section exists to avoid, and it is invisible to
    a reader scanning the imports.
  - A module-level builder slot that `shape.py` fills at import: `import
    fastraml.types.complex_` alone becomes a half-initialised module, and a static
    error becomes a runtime one.
  - The builder injected on `Raml` and reached through `base._raml`. It adds no
    import edge and it fits an existing seam, but it is a service locator: the
    dependency vanishes from every signature that uses it.

  Moving `make_shape` into `base.py` does not solve this at all. It travels with
  its need to construct the kinds, so it would close a loop across the layer
  boundary — `parser/facets.py` → `types/base.py` → `types/complex_.py` →
  `parser/facets.py` — and `import fastraml.types.base` would fail outright, since
  `complex_.py` imports `Property` from a `base` that has not defined it yet.
- `registry.py` imports nothing from `parser/` or `types/` at module level; it
  holds the stores and uses `TYPE_CHECKING` imports for annotations. This keeps
  the import graph acyclic without runtime indirection.
- No module beyond `loaders.py` performs I/O.

## 3. The registry (`Raml`)

One object owns everything produced by one parse. It is the direct analogue of
go-raml's `RAML` struct and it exists so that caches, ID generation and cross-file
indices have a single home rather than being threaded through every call.

```python
class Raml:
    __slots__ = (
        # --- configuration -------------------------------------------------
        "loader",  # ResourceLoader
        "workspace_root_uri",  # str, file:// URI of the sandbox root
        "max_include_size",  # int, 0 = unlimited
        "max_depth",  # int, the one recursion ceiling (docs/12 section 14)
        "retain_source",  # bool, keep raw node trees + entity index
        "regex_engine",  # "re" | "re2"
        # --- caches (the reason this class exists) -------------------------
        "fragments",  # uri -> Fragment      (one parse per file)
        "include_nodes",  # uri -> Node          (one compose per file)
        "expr_cache",  # str -> RdtNode | RamlError (one parse per expression)
        "json_schema_registry",  # SchemaRegistry | None, built on first use
        # --- indices -------------------------------------------------------
        "fragment_types",  # uri -> {name: BaseShape}
        "fragment_annotations",  # uri -> {name: BaseShape}
        "fragment_resolvers",  # uri -> ReferenceResolver (P7's anchor fallback)
        "fragment_typedefs",  # uri -> [BaseShape]   (everything declared there)
        "endpoints",  # full_uri -> EndPoint (duplicate detection)
        "shapes",  # [BaseShape] in creation order
        "domain_extensions",  # [DomainExtension]
        "include_refs",  # uri -> [IncludeRef]  (tooling)
        # --- work queues ---------------------------------------------------
        "_discriminator_shapes",  # [BaseShape] that wrote a discriminator facet
        "unresolved_shapes",  # deque[BaseShape] with UnknownShape
        # --- global metadata harvested from the API root -------------------
        "global_protocols",
        "global_media_types",
        "global_secured_by",
        # --- transient parse state ----------------------------------------
        "_parse_ctx_stack",  # [ParseCtx]        lexical scope while decoding
        "_active_overlay",  # ProvenanceOverlay | None (stage-2 only)
        "_id_counter",  # itertools.count
        "entry_point",  # Fragment
        "unwrapped",  # bool
        "source_nodes",  # uri -> Node        (retain_source only)
        "source_texts",  # uri -> str         (retain_source only; comments)
        "source_info",  # shape id -> (key Node | None, value Node)
    )
```

### 3.1 Identity and IDs

Every model entity gets a monotonically increasing `int` id from a single
`itertools.count(1)`. IDs are used as:

- clone-memo keys, so a structure-sharing deep copy can preserve graph shape;
- the key of the `source_info` index, so tooling can go shape → source node
  without re-walking the AST;
- stable identifiers in diagnostics and serialized output.

They are **not** hashes of content and carry no ordering meaning beyond creation
order.

### 3.2 Locations are `file://` URIs, always

The moment a path enters the parser it becomes a canonical `file://` URI
(`uris.path_to_file_uri`). Every cache key, every `Fragment.location`, every
`BaseShape.location` is a URI. Two consequences:

- diamond includes hit the cache regardless of how the path was spelled
  (`./a/../b.raml` vs `b.raml`);
- remote and local fragments live in one namespace, so `uses:` may point at a URL
  without any special-casing downstream.

Conversion back to an OS path happens only inside `loaders.py`.

## 4. Cross-cutting invariants

These hold at every point after the pass that establishes them. Tests assert
them; violations are bugs, not diagnostics.

| # | Invariant | Established by |
|---|-----------|----------------|
| I1 | Every `Fragment.location` and `BaseShape.location` is a `file://`/`http(s)://` URI | P1 |
| I2 | A file is composed at most once per parse | P1 (`include_nodes`, `fragments`) |
| I3 | A fragment is decoded at most once per parse | P2 (`fragments` checked before load) |
| I4 | Every `BaseShape` created during decode is appended to `shapes`, and to `unresolved_shapes` iff its concrete kind is `UnknownShape` | P2/P7 |
| I5 | After P7 no reachable shape is an `UnknownShape` | P7 |
| I6 | After P9 every reachable shape has `unwrapped is True` and `link is None`, and every reachable `UnionShape` has had `build_dispatch()` called | P9 |
| I7 | Every entity carries a `location` plus `key_pos`/`value_pos`, both 1-based | all |
| I8 | Order of declaration is preserved in every mapping the model exposes | dict semantics |
| I9 | Structural merge never mutates either input tree | P4a |
| I10 | Node identity is stable across the merge, so provenance lookups work | P4a |
| I11 | An entity has at most one address; an address may be shared by entities the model links, so `Addresses.of` is many-to-one and `id` remains the identity | `views/walk.py` (after P10) |
| I12 | `validate()`/`validate_or_raise()` are only ever reached on a shape satisfying I6 — **asserted**, because an un-flattened shape silently answers a different question | P9, enforced at the P10 entry |

## 5. Error strategy

Where a failure is local, parsing **accumulates errors instead of failing fast**.
One broken response body does not discard its sibling responses, and one broken
endpoint does not discard the API. Passes collect diagnostics into an accumulator
and return the partial model together with the accumulated trace.

Three failures stop the affected unit immediately, because continuing would
produce a model that misrepresents the source: an unreadable entry file, a
fragment whose first line is not a RAML header, and a root node that is not a
mapping. See [11-diagnostics.md](11-diagnostics.md).

## 6. Why two stages for endpoints

This is the design decision with the widest effect on the rest of the parser. It
is summarised here and detailed in [08](08-templates-and-endpoints.md).

The spec's trait and resource-type algorithm (spec § Algorithm of Merging Traits
and Methods) is defined over *declaration trees*: "the method node receives all
properties of the trait node which are undefined in the method node… object
properties are merged recursively". A parser that turns YAML into typed model
objects first has to re-implement that merge over the model — once per model
class, with an inheritance-aware merge for every facet — and then re-resolve type
references afterwards.

Instead:

- **Stage 1** decodes only the *directives* that drive the merge (`type:`, `is:`,
  `securedBy:`) and keeps everything type-bearing as the original YAML subtree.
- The merge then operates on YAML trees, which is exactly what the spec describes:
  one algorithm, ~150 lines, no per-facet special cases.
- **Stage 2** decodes each merged tree **once** into the real model.

The cost is that the file being decoded no longer implies the lexical scope of a
subtree. A trait's body grafted into an operation still resolves type names in
the trait's namespace, while a parameter value spliced into that body resolves in
the caller's namespace. The **provenance overlay** records this: a sparse map
from node identity to resolution scope, consulted while stage 2 decodes.
