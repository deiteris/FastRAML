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
  resolved. (`go-raml:parse.go` calls `buildEndPoints` then `resolveShapes`.)
- **P7 before P9.** Unwrapping merges a shape with its parents; a shape whose
  concrete kind is still `UnknownShape` cannot be merged.

## 2. Package layout

```
pyraml/
  __init__.py             public API re-exports (see doc 13)
  py.typed

  errors.py               Diagnostic, StackTrace, Accumulator, ErrorKind
  positions.py            Position (1-based, with end), position helpers
  uris.py                 path↔file:// URI, RFC 3986 reference resolution
  loaders.py              ResourceLoader protocol; File/SafeFile/HTTP/Scheme loaders

  yamlnode.py             Node model + composer adapter (doc 03)
  datanode.py             DataNode / ValueNode: structured user data (doc 03 §6)

  registry.py             Raml — the central store, caches, id counter, ParseCtx stack

  parser/
    entry.py              parse_from_path/parse_from_string; the P0–P10 driver
    includes.py           !include resolution, node cache, size limit
    fragments.py          all fragment classes and their decoders (doc 04)
    references.py         resolve_reference / resolve_library_reference (doc 04)
    facets.py             scalar-facet *builders*, annotated-scalar form
    documentation.py      DocumentationItem
    annotations.py        DomainExtension (doc 09)
    security.py           SecuritySchemeDefinition + settings variants (doc 09)
    templates.py          variable index, substitution, transform functions (doc 08)
    traits.py             TraitDefinition / Trait reference (doc 08)
    resourcetypes.py      ResourceTypeDefinition / ResourceType reference (doc 08)
    source_ir.py          stage-1 SourceEndPoint / SourceOperation (doc 08)
    structural_merge.py   spec merging algorithm + provenance overlay (doc 08)
    source_decode.py      stage-2 materialization (doc 08)
    endpoints.py          EndPoint, Operation, Request, Response, Body
    uritemplates.py       RFC 6570 L1/L2 parsing and parameter synthesis

  types/
    base.py               BaseShape, Property, PatternProperty, ScalarFacet
    inference.py          identify_shape_type, default-type rules (doc 05)
    scalars.py            String/Number/Integer/Boolean/Date*/File/Nil shapes
    complex_.py           Object/Array/Union/Any/Json/Unknown/Recursive shapes
    inherit.py            per-kind inheritance rules (doc 07)
    unwrap.py             unwrap driver + recursion marking (doc 07)
    jsonschema_.py        external JSON Schema types (doc 10 §5)
    validate.py           check() and validate() (doc 10)
    expressions/
      lexer.py            RDT tokenizer (doc 06)
      parser.py           RDT recursive-descent parser + AST cache
      build.py            AST → shapes
```

Rules on the layout:

- `types/` imports from `parser/` in exactly one place: `parser/facets.py`, for
  `make_scalar_facet`. Everything else a shape needs from YAML arrives as a
  `Node` (from `yamlnode.py`, which both layers may import).

  That one edge is deliberate. `ScalarFacet` is a type-model class and lives in
  `types/base.py`, but *building* one needs the parser twice over: an `!include`
  at a facet position has to be read through the include cache, and the
  annotated-scalar form has to turn `(annotation)` keys into `DomainExtension`s.
  Every one of the fourteen shapes decodes scalar facets, so the alternative —
  threading a builder callback through every `decode_facets` — would cost more
  than the rule protects. The edge cannot cycle: `parser/facets.py` imports
  `types/base.py` and nothing else from `types/`.

  References in the other direction are free, because they are annotations only:
  `BaseShape` names `DomainExtension`, `DataNode`, `DataTypeFragment` and
  `ReferenceResolver` under `TYPE_CHECKING`, which `from __future__ import
  annotations` keeps as strings and never imports at runtime.
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
        "retain_source",  # bool, keep raw node trees + entity index
        "regex_engine",  # "re" | "re2"
        # --- caches (the reason this class exists) -------------------------
        "fragments",  # uri -> Fragment      (one parse per file)
        "include_nodes",  # uri -> Node          (one compose per file)
        "expr_cache",  # str -> RdtAst        (one parse per expression)
        "json_schema_registry",  # shared JSON Schema compiler/registry
        # --- indices -------------------------------------------------------
        "fragment_types",  # uri -> {name: BaseShape}
        "fragment_annotations",  # uri -> {name: BaseShape}
        "fragment_typedefs",  # uri -> [BaseShape]   (everything declared there)
        "endpoints",  # full_uri -> EndPoint (duplicate detection)
        "shapes",  # [BaseShape] in creation order
        "domain_extensions",  # [DomainExtension]
        "include_refs",  # uri -> [IncludeRef]  (tooling)
        # --- work queues ---------------------------------------------------
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
        "source_info",  # entity id -> (key Node, value Node)
    )
```

### 3.1 Identity and IDs

Every model entity gets a monotonically increasing `int` id from a single
`itertools.count(1)`. IDs are used as:

- clone-memo keys, so a structure-sharing deep copy can preserve graph shape;
- the key of the `source_info` index, so tooling can go entity → source node
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
| I6 | After P9 every reachable shape has `unwrapped is True` and `link is None` | P9 |
| I7 | Every entity carries a `location` plus `key_pos`/`value_pos`, both 1-based | all |
| I8 | Order of declaration is preserved in every mapping the model exposes | dict semantics |
| I9 | Structural merge never mutates either input tree | P4a |
| I10 | Node identity is stable across the merge, so provenance lookups work | P4a |

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
