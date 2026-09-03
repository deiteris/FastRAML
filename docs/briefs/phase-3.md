# Phase 3 brief — type expressions and resolution (P7)

A working brief for a fresh session. Read this, then the two documents it names.
It exists so you do not have to re-derive what earlier sessions already settled.

---

## 1. Where the project stands

Thirty-four commits on `master`. Working tree clean. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
# 1624 passed, 42 skipped
```

**Phases 0, 1 and 2 are complete.** Phase 0 gives you positions, diagnostics,
URIs, loaders and the YAML node model; Phase 1 the fragment layer; Phase 2 the
whole `types/` package. What you will touch constantly:

| Module | Public surface |
|---|---|
| `pyraml.errors` | `RamlError.new/.wrap`, `err.append`, `Accumulator`, `ErrorKind`, `Trace` |
| `pyraml.yamlnode` | `Node`, `NodeKind`, `pairs`, `is_null`, `node_error(msg, location, node, info=)`, `TAG_*` |
| `pyraml.registry` | `Raml`: `next_id()`, `current_ctx()`, `put_shape`, `unresolved_shapes`, `expr_cache`, `types_in(uri)`, `annotation_types_in(uri)`, `fragments` |
| `pyraml.parser.references` | `resolve_reference`, `resolve_library_reference`, `UnresolvedReferenceError`, `cut_last` |
| `pyraml.parser.fragments` | `ReferenceResolver` protocol: `reference_type`, `reference_annotation_type`; `LibraryLink`; `Library`, `APIFragment` |
| `pyraml.types.base` | `BaseShape` (all 27 slots), `Property`, `PatternProperty`, `Shape`, `KindBase`, `TYPE_*`, `BUILTIN_TYPES` |
| `pyraml.types.shape` | `make_shape`, `_attach_kind`, `KIND_TO_CLASS`. **This and `types/resolve.py` are your modules.** |
| `pyraml.types.complex_` | `UnknownShape`, `UnionShape`, `ArrayShape`, `ObjectShape`, `JsonShape` |
| `pyraml.types.expressions` | `parse_expression`, `tokenize`, `Primitive`/`Reference`/`Array`/`Optional_`/`Union` |

### 1.1 The seam you pick up

`Raml.unresolved_shapes` — a `deque[BaseShape]`, every one of them carrying an
`UnknownShape`. Phase 2 filled it and touched nothing else about resolution. On a
parsed TCK corpus it holds shapes from `type:` expressions, bare named
references, `type:` sequences (multiple inheritance, `base.type == 'composite'`)
and `type: !include` links (`base.link is not None`).

`BaseShape._visiting` exists and is never set. You are its first reader.

`BaseShape.type_expr_refs` exists and is always empty; `TypeExprRef` is an `Any`
alias in `base.py` under `TYPE_CHECKING`. You replace it with the real class.

`grep -rn '_raw_' pyraml/` lists the *other* phases' seams. None of them is
yours — Phase 3 touches no fragment attribute.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding project rules.
2. **`docs/06-type-expressions.md`** — all four sections. § 3 is the algorithm
   you implement; § 3.1 is the rule most likely to be got wrong.
3. **`docs/07-resolution-and-inheritance.md`** §§ 1–2 only. § 3 onward is
   Phase 4's; do not implement `inherit`.
4. **`docs/04-fragments-and-namespaces.md`** §§ 3–4 — the lookup rules and anchor
   scoping, which decide *where* a name resolves.
5. **`docs/02-architecture.md`** § 2 (the layering rules you must not widen) and
   § 4 (invariant **I5**, which this phase establishes).

Skim only: doc 05 § 4 (the walk you are reusing, not rewriting), doc 15 Phase 3.

---

## 3. What to build

Build in this order; each step depends only on the ones above it.

### 3.1 Record the declaration's form — **done**

Landed already, because § 3.4 cannot be written without it. Recorded here so you
know why the code and go-raml differ.

Doc 06 § 3.1: a `Reference` produces an *alias* when the declaration was a bare
scalar (`Foo: Bar`) and *inheritance* when it was a mapping (`Foo: {type: Bar}`),
including a mapping that carries nothing else. Phase 2 recorded neither: it left
`UnknownShape.facets == []` in both cases, which would have made every alias a
subtype.

go-raml distinguishes them by nil-versus-empty on that same slice
(`BaseShape.decode`, `shape.go` 1121), which is free in Go. Here it is
`UnknownShape.from_mapping`, set by `_attach_kind`. Doc 06 § 3.1 now says why,
and `TestAliasVersusInheritance` in `tests/unit/test_shape_decode.py` pins all
four forms.

### 3.2 `TypeExprRef` and the two lookups it needs — **done**

`TypeExprRef` is the real record in `types/base.py`, per doc 06 § 3.2. Both
lookups now exist, and both are shaped so that `types/` need not import
`parser/fragments.py` at runtime — that module imports `make_shape`, so it is
the one genuine cycle in the layout (doc 02 § 2):

- `ReferenceResolver.library_link(prefix)`, on the protocol so P7 reaches it
  through the anchor it already holds. It emits the library half of a
  `lib.Type` reference.
- `Raml.resolver_at(location)`, doc 04 § 4.2's fallback for a shape with no
  anchor. Filled by the fragment decoder in the line that already performs the
  capability check. On a full TCK parse it is never reached — all 578 unresolved
  shapes carry an anchor — so it exists for programmatic construction and tests.

### 3.3 The expression cache lives on the registry — **done**

`parse_expression(text, cache)`, with `Raml.expr_cache` typed `ExprCache =
dict[str, RdtNode | RamlError]`. It used to be a module-level dict in
`expressions/parser.py`, which outlived every parse and grew for the life of the
interpreter. The parser takes a `dict`, not a `Raml`, so `expressions/` stays a
leaf that knows nothing about the registry.

### 3.4 `pyraml/types/resolve.py` — the driver and the visitor, one module

Doc 07 § 1 writes `resolve_shapes` as a method on `RAML`. It cannot be one here:
doc 02 § 3 has `registry.py` importing nothing from `types/` at runtime, and that
is what keeps the import graph acyclic without indirection. These are free
functions taking `raml` as their first argument. Amend doc 07 § 1's pseudo-code
in the same commit.

Doc 02 § 2 lists `expressions/build.py` for the AST → shape visitor, as a module
separate from the driver. **Merge them instead.** The two are mutually recursive
— `resolve_shape` runs the visitor, and the visitor's `Reference` case calls
`resolve_shape` on the referent (doc 06 § 3, row 2) — and both ways out of that
are already ruled out by § 2 itself: a `resolve` callback parameter is the
`decode_facets(facets, make_shape)` shape that section rejects, and a deferred
import is the indirection it exists to prevent. They are one algorithm; go-raml
splits them only because Go's ANTLR runtime wants a visitor struct. Amend doc 02
§ 2's layout and doc 15's Phase 3 step 1.

The driver is doc 07 § 1 verbatim: `while q:` over the deque, never a snapshot,
because the visitor appends to it. Errors accumulate.

`resolve_shape` is doc 07 § 1.1's five-line table, in that order — the
already-resolved short-circuit *before* the `_visiting` check, so a shape reached
twice by two referrers is free and only a genuine cycle errors.

The visitor is doc 06 § 3's six-row table. Two things carry from `target.base` to
every anonymous shape it allocates: `anchor`, so inner references resolve in the
right namespace, and `type_expr`, so columns rebase correctly.

**Reuse `_attach_kind`.** It is already go-raml's `MakeConcreteShapeYAML` —
same three arguments, same in-place `base.shape` swap, same
`_split_declarations` and `_check_custom_facet_names` afterwards. P7 does not
need a second constructor; it needs that one, made public.

### 3.5 Wire P7 into the driver

One line in `parser/entry.py`, where the comment `# P7 — resolve shapes (drain
the unknown worklist).` already marks the slot. It runs after `decode_fragment`
and before P8.

---

## 4. Decisions already settled — do not re-litigate

1. **No ANTLR** (deviation **D8**, doc 01). The lexer and parser are hand-written
   and already exist. You consume them; you do not touch them beyond § 3.3.
2. **Resolution is in place.** `base.shape` is swapped on the existing
   `BaseShape`; the `BaseShape` object is never replaced. Every reference already
   taken to it — from a property, an items slot, a `uses:` table — stays valid.
   That is the entire reason for the `BaseShape` + kind-object split (doc 05 § 1).
3. **The worklist is not re-traversed.** There is no second walk of the model.
   The decoder registered exactly the shapes that need work; resolution touches
   those and nothing else (doc 07 § 1). If you find yourself walking the type
   graph looking for `UnknownShape`s, stop.
4. **`_visiting` is a plain boolean on the shape, set on entry and cleared in a
   `finally`.** No visited-set allocation per call. It detects type cycles that
   go *through resolution* (`A: B` / `B: A`), which are errors. Cycles through a
   property are legal and belong to Phase 4's recursion marking.
5. **Anonymous inner shapes are fresh `BaseShape`s.** `string[]` is two
   declarations. Sharing one base would leak `minItems: 1`, written beside the
   expression, onto the item type (doc 06 § 3).
6. **Alias versus inheritance is decided by the declaration's form, and nothing
   else** — not by whether any facet was written beside the `type:`. See § 3.1.
7. **The expression cache is keyed on text alone and caches failures too.** The
   AST carries only intra-expression columns, never a file position, which is
   what makes one entry safe for every occurrence. A malformed expression
   repeated 500 times costs one parse and 500 correctly positioned diagnostics,
   because the *caller* supplies the location and rebases the column by
   `base.type_expr.value_pos.column` (doc 06 § 2.2).
8. **A name resolves against `base.anchor`, not `base.location`.** The anchor was
   captured when the shape was created and is the fragment whose `uses:` governs
   both bare names and the `lib.` prefix. A trait's body resolves in the trait
   file's namespace even after it is grafted onto an operation elsewhere
   (doc 04 § 4).
9. **An annotation type falls back to `types:`.** `is_annotation_type` on the
   base selects `reference_annotation_type`, which already tries
   `annotationTypes` then `types` (doc 04 § 3.1). Both resolvers exist.
10. **Self-reference is an error, and it is not the same check as the cycle
    flag.** `ref is target.base` catches `Foo: Foo`; `Foo: Foo[]` is caught by
    `_visiting` on the referent, because the items base is a different object.
11. **`types/` still imports from exactly two `parser/` modules**, and there is
    still exactly one deferred import, in `shape.py`. `resolve.py` needs
    `parser.references` and `parser.fragments` for annotations only — keep them
    under `TYPE_CHECKING`. Reference lookup goes through the `anchor`, which is
    already a `ReferenceResolver` object, so no runtime import is required.

---

## 5. Reference source — read ranges, not files

go-raml is at `../go-raml-main`. Open it for a mechanical detail, not for
orientation.

| Need | File and lines |
|---|---|
| The worklist drain and `resolveShape` | `resolve.go` 24–38, 127–199 |
| Multiple inheritance and link resolution | `resolve.go` 74–101 |
| The whole AST → shape visitor | `rdt_visitor.go` (all 268 lines) |
| `VisitReference` — alias vs inherits, the two refs | `rdt_visitor.go` 192–268 |
| `MakeConcreteShapeYAML` — what `_attach_kind` already is | `shape.go` 617–669 |
| `decode` — where nil-vs-empty facets is set | `shape.go` 1121–1157 |
| `UnknownShape` | `complex.go` 1571–1620 |
| The RDT corpus | `rdt/examples.txt` (13 lines) |

---

## 6. Definition of done

From `docs/15-implementation-plan.md` Phase 3, made concrete:

- Every line of `rdt/examples.txt` builds the expected shape — thirteen
  expressions covering primitives, references, dotted references, arrays,
  optionals, unions and grouping. They are already a parser fixture in
  `tests/unit/test_expressions.py`; extend them to assert the built shape.
- A test pins `Foo: Bar` → alias and `Foo: {type: Bar}` → inherits (doc 06 § 3.1).
  The decode half of this is done; the resolution half is not.
- A test pins a cyclic `A: B` / `B: A` as a diagnostic, not a hang.
- A test pins that one malformed expression written in two files produces two
  diagnostics with two different locations off one cached parse.
- **Invariant I5** asserted over the parsed corpus in
  `tests/tck/test_invariants.py`, beside the existing I1 and I4 checks: after a
  parse, no reachable shape is an `UnknownShape`.
- The TCK ratchet moves. Record the new baseline and **read the diff before
  committing it**:
  `PYRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck --update-ratchet`
  Today the whole corpus sits at 558 pass / 372 fail, with `Types/` at 174 / 128
  and `Libraries/` at 37 / 12 — those two are where expressions and cross-file
  references live, so they are where movement is expected.
- The full gate passes.

Unit tests belong in `tests/unit/test_expressions.py` (the parser and the cache)
and a new `tests/unit/test_resolve.py` (the visitor and the driver).

---

## 7. Scope boundary

Phase 3 answers **what kind is this, and which declaration does this name refer
to**. It does not flatten anything.

Do not implement: `inherit` or `alias_to` bodies, `unwrap`, recursion marking or
the clone operations — all Phase 4, doc 07 §§ 3–5. `link` is *resolved* here
(the linked fragment's shape is resolved and its kind built locally) but is
**not** rewritten to `inherits`; that rewrite is the first step of unwrap
(doc 07 § 2).

Likewise: no endpoints (Phase 5), no traits or resource types (Phase 6), no
security schemes (Phase 7), no validation (Phase 8). `JsonShape.validator` stays
`None`.

---

## 8. Working method

Branch first: `git checkout -b phase-3-resolution`. Commit in logical units with
`type:` prefixes. If the code must diverge from a document, **amend the document
in the same commit** — this phase already owes three amendments before a line of
it is written (doc 02 § 2's layout, doc 07 § 1's pseudo-code, doc 15's Phase 3
step 1), and finding them is what re-reading the docs was for.
