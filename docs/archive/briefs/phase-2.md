# Phase 2 brief — the type system

A working brief for a fresh session. Read this, then the two documents it names.
It exists so you do not have to re-derive what earlier sessions already settled.

---

## 1. Where the project stands

Twenty commits on `master`. Working tree clean. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy fastraml/ && uv run pytest -q
# 1288 passed, 42 skipped
```

**Phases 0 and 1 are complete.** Phase 0 gives you positions, diagnostics, URIs,
loaders and the YAML node model. Phase 1 gives you the fragment layer. What you
will touch constantly:

| Module | Public surface |
|---|---|
| `fastraml.positions` | `Position(line, column, end_line, end_column)` (1-based, end exclusive), `Position.shifted(n)`, `UNKNOWN` |
| `fastraml.errors` | `RamlError.new/.wrap`, `err.append`, `Accumulator`, `ErrorKind`, `Trace` |
| `fastraml.yamlnode` | `Node`, `NodeKind`, `compose`, `pairs`, `is_null`, `last_leaf`, `node_error(msg, location, node, info=)`, `TAG_*` |
| `fastraml.datanode` | `DataNode`, `ValueNode`, `make_data_node(raml, key, value, location)`, `value_node_of`, `scalar_value(node)` |
| `fastraml.registry` | `Raml`: `next_id()`, `push_ctx`/`pop_ctx`/`current_ctx`, `put_type`, `put_annotation_type`, `put_typedef`, `put_shape`, `unresolved_shapes`, `expr_cache`, `types_in(uri)` |
| `fastraml.parser.facets` | `make_scalar_facet(raml, key, value, location, convert)`, `make_string_facet`, `make_seq_facet`, `scalar_str`, `resolve_annotated_scalar` |
| `fastraml.parser.includes` | `resolve_include(raml, node, location) -> (uri, Node)`, `note_include_ref` |
| `fastraml.parser.annotations` | `DomainExtension`, `is_annotation_key`, `unmarshal_domain_extension` |
| `fastraml.parser.references` | `resolve_reference`, `resolve_library_reference`, `UnresolvedReferenceError`, `cut_last` |
| `fastraml.types.base` | `ScalarFacet[T]` — the only thing in it so far. **This is your module.** |

Three **leaf modules** built ahead of the critical path. Phase 2 does not touch
them; the phase in the last column consumes them:

| Module | Surface | Consumed by |
|---|---|---|
| `fastraml.types.expressions.parser` | `parse_expression(text) -> RdtNode` | Phase 3 |
| `fastraml.parser.templates` | `parse_template_variables`, `apply_template_action`, … | Phase 6 |
| `fastraml.parser.uritemplates` | `extract_uri_template_params`, `resource_path_name` | Phase 5 |

### 1.1 The seams you pick up

Phase 1 decoded what it owned and kept the rest as the original `Node`.
`grep -rn '_raw_' fastraml/` lists every one; a comment beside each names its
phase. Yours:

| Attribute | On | Holds |
|---|---|---|
| `_raw_types` | `Library`, `APIFragment` | the `types:` (or `schemas:`) mapping node |
| `_raw_annotation_types` | `Library`, `APIFragment` | the `annotationTypes:` mapping node |
| `_raw_base_uri_parameters` | `APIFragment` | the `baseUriParameters:` mapping node |
| `_raw_declaration` | `DataTypeFragment` | the whole declaration, `uses:` stripped; name it `fragment.declared_name` |
| `_raw_examples` | `NamedExample` | name → example value node |

`DataTypeFragment.decode_json_schema` has already wrapped an external `.json`
schema as `{type: "<raw json>"}`, so the JSON path needs no branch of its own.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding project rules. "Rules that look like style but are
   load-bearing" is not style advice; two of them (`__slots__` everywhere, never
   `copy.deepcopy`) are about this phase's classes specifically.
2. **`docs/05-type-model.md`** — your primary specification, all nine sections.
3. **`docs/03-yaml-and-io.md`** §§ 6–7 — `DataNode` and `ScalarFacet`, which you
   consume rather than build.
4. **`docs/02-architecture.md`** §§ 1–4 — the pass order, the registry, and
   invariant **I4**, which this phase establishes.

Skim only: doc 06 § 1 (so you know what shape of seam Phase 3 needs), doc 07 § 1
(what unwrap will expect of your objects), doc 10 § 4 (what validation will
expect of custom facets), doc 15 Phase 2.

---

## 3. What to build

The module map is settled — doc 02 § 2 now lists all six. Build them in this
order; each step depends only on the ones above it, so the tree stays green if
you stop between any two.

### 3.1 `fastraml/types/base.py`

`BaseShape` with the `__slots__` list from doc 05 § 1 — the full set, even where
a later phase fills the field. `Property` and `PatternProperty` (doc 05 § 3), and
the `Shape` protocol. `ScalarFacet` is already there.

`BaseShape` holds one kind-specific `Shape` object rather than being subclassed.
That is go-raml parity and it is load-bearing: the kind of a declaration is
unknown at creation time, and it can change again during resolution, so
references already taken to the `BaseShape` must survive a kind swap.

### 3.2 `fastraml/types/inference.py`

`identify_shape_type(facets, default_type, location)` and `FACET_TYPE_HINT` from
doc 05 § 4.2. Four rules, in order, and the `string`/`file` reconciliation is the
one that gets mis-implemented — `pattern` is string-only and poisons it.

### 3.3 `fastraml/types/xml.py` and `fastraml/types/examples.py`

Two leaf records with decoders: `XmlSerialization` (doc 05 § 8; unknown keys
inside `xml:` are an error, so a typo is caught) and `Example`/`Examples`
(doc 05 § 6). Both are common facets, so step 3.5's walk needs them.

They are separate modules on purpose. `xml:` and `example:` have nothing in
common beyond both being facets, and `Example` is consumed by the `NamedExample`
fragment and by Phase 8's validation, while `XmlSerialization` is consumed by
nothing in v1.

### 3.4 `fastraml/types/scalars.py` and `fastraml/types/complex_.py`

The fourteen concrete kinds plus `UnknownShape`, `JsonShape` and `RecursiveShape`
(doc 05 § 1, last paragraph). Each implements the `Shape` protocol; in Phase 2
only `decode_facets` has a real body — `inherit`, `alias_to`, `check`, `validate`
and `clone` belong to Phases 4 and 8, and should raise `NotImplementedError` with
a comment naming the phase.

### 3.5 `fastraml/types/shape.py` — the single decode entry point

`make_shape(raml, key_node, value_node, location, default_type)` — doc 05 § 4 —
plus the `make_body_shape` wrapper that defaults to `any` instead of `string`,
`make_property`, `make_pattern_property`, and the kind dispatch. Every property,
header, query parameter, body, URI parameter and type declaration goes through
it. Getting this one module right is most of the phase.

One `make_property` serves `properties:`, `headers:`, `queryParameters:`,
`uriParameters:`, `baseUriParameters:` and `facets:` — all six are "properties
declarations" per the spec, so all six get `?` handling and inline type
declarations for free (doc 05 §§ 5, 7).

**`shape.py` imports the kind modules; they must not import it back.** A kind
that holds declarations does not build them. It declares which of its facets hold
declarations in a class-level `DECLARATION_FACETS` table, and `make_shape` — which
knows the kind before it constructs anything — builds those children and passes
them to the constructor. `decode_facets` then takes one argument and sees no
declarations. Doc 02 § 2 records the four alternatives and why each lost, and why
`base.py` is the one place `make_shape` cannot live.

### 3.6 Wiring the seams

`unmarshal_types(raml, node, location, *, is_annotation)` per doc 04 § 5.1:
reject a built-in name, reject a duplicate in the same map, build the shape,
register it in `fragment_types` (or `fragment_annotations`) **and** append it to
`fragment_typedefs`. Then call it from the `_raw_*` attributes above, and from
`NamedExample._raw_examples` through the example builder.

---

## 4. Decisions already settled — do not re-litigate

1. **`BaseShape` + a kind object, not a class per kind.** Doc 05 § 1 gives the
   three reasons. A subclass hierarchy cannot represent "type not yet known".
2. **Nothing resolves in this phase.** A type expression, a named reference,
   multiple inheritance — all become `UnknownShape` on the worklist. Resolution
   is P7 and it is Phase 3's. If you find yourself parsing a type expression,
   stop.
3. **Numeric facets never pass through `float`.** Integer bounds are `int`;
   `minimum`/`maximum`/`multipleOf` on numbers are `Fraction` built from the raw
   scalar text. `Fraction(1.1)` embeds the binary-float error and makes
   `multipleOf: 1.1` reject `2.2`.
4. **The four property-optionality cases** (doc 05 § 5). Cases 3 and 4 are the
   ones implementations get wrong: with an explicit `required:`, the `?` is part
   of the name. Chomp exactly one `?`.
5. **Example form A vs form B** is disambiguated by the presence of a `value`
   key, and by nothing else. It is the only workable rule and the spec's own
   example relies on it.
6. **The leftover facet list stays flat** — `[k0, v0, k1, v1, …]`, one pass, no
   intermediate dict. The concrete shape sees only the keys it might handle, and
   anything it does not recognise becomes a custom facet value.
7. **Default type is `string` everywhere except a `body` node**, where it is
   `any`. Two thin wrappers, one shared implementation.
8. **`discriminator` is checked in P10, not at decode time** — the property it
   names may be inherited. The one exception is a discriminator on a union, which
   can never become valid and is rejected immediately.
9. **`types/` imports from exactly two `parser/` modules**: `parser/facets.py`
   for the scalar-facet builders, and `parser/annotations.py` for the two
   functions that build a `DomainExtension` — annotations may be written on a
   declaration and inside `example:`, so the type layer meets them. Doc 02 § 2
   states the reasoning. Do not widen it further: everything else a shape needs
   from YAML arrives as a `Node` or a `DataNode`.
10. **Every shape is appended to `raml.shapes`, and to `unresolved_shapes` iff
    its kind is `UnknownShape`.** That is invariant I4, and P7 depends on it.
11. **`types/` points one way: `shape.py` → `scalars.py`/`complex_.py` →
    `base.py`.** Nothing reaches back — not an import, not a parameter, not a
    field on `Raml`. A kind that holds declarations publishes a
    `DECLARATION_FACETS` table and receives its children through `__init__`. The
    price is that `properties:` is read in `shape.py` while `minProperties:` is
    read in `ObjectShape`; doc 02 § 2 says why that beats the four alternatives.
12. **`Example`/`Examples`, `XmlSerialization` and `make_shape` each have a
    module** — `types/examples.py`, `types/xml.py`, `types/shape.py`. Doc 02 § 2
    was silent on all three until this phase was planned; it is not silent now.

## 5. Definition of done

From `docs/15-implementation-plan.md` Phase 2, made concrete:

- A library whose `types:` uses object, array, string, number, integer, boolean,
  file, nil and the date kinds decodes into shapes with their facets, positions
  and annotations intact.
- Every rule in doc 05 § 4.2 has a test that names it, including the conflicting-
  hints error and the `string`/`file` reconciliation.
- **Both** property-optionality corner cases (doc 05 § 5, rules 3 and 4) have a
  test that names the rule.
- A test asserts invariant I4 over a parsed corpus: every shape in `raml.shapes`
  is either a known kind or present in `unresolved_shapes`.
- `Types/` TCK fixtures parse except where they need expressions or inheritance.
  Record the new baseline and **read the diff before committing it**:
  `uv run pytest tests/tck --update-ratchet`
  Today `Types/` sits at 165 pass / 137 fail, `Examples/` at 5 / 5, `Annotations/`
  at 48 / 45.
- The full gate passes.

Unit tests belong in `tests/unit/test_shapes.py`, `test_inference.py`,
`test_properties.py`, `test_examples.py`.

---

## 6. Scope boundary

Phase 2 **resolves nothing**. No type expression is parsed, no reference is
bound, no inheritance is applied, no example is validated. If you reach for
`parse_expression`, `inherit` or `unwrap`, you have crossed into Phase 3 or 4 —
leave an `UnknownShape` on the worklist instead.

Likewise: no endpoints (Phase 5), no traits or resource types (Phase 6), no
security schemes (Phase 7), no validation (Phase 8).

---

## 7. Working method

Branch first: `git checkout -b phase-2-types`. Commit in logical units with
`type:` prefixes. If the code must diverge from a document, **amend the document
in the same commit** — that rule has now caught three real defects, the most
recent being a class sitting in the wrong package and about to invert the
layering.
