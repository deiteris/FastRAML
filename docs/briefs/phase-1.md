# Phase 1 brief — registry, fragments, includes, namespaces

A working brief for a fresh session. Read this, then the two documents it names.
It exists so you do not have to re-derive what earlier sessions already settled.

---

## 1. Where the project stands

Eleven commits on `master`. Working tree clean. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
# 239 passed, 972 skipped
```

**Phase 0 is complete** and gives you these, all tested:

| Module | Public surface you will use constantly |
|---|---|
| `pyraml.positions` | `Position(line, column, end_line, end_column)` (1-based, end exclusive), `Position.shifted(n)`, `UNKNOWN` |
| `pyraml.errors` | `RamlError.new(msg, location, position, kind=, info=)`, `RamlError.wrap(msg, cause, location, ...)`, `err.append(other)`, `Accumulator`, `ErrorKind`, `Trace` |
| `pyraml.uris` | `path_to_file_uri`, `file_uri_to_path`, `resolve_uri_ref`, `uri_base`, `uri_scheme`, `is_file_uri` |
| `pyraml.loaders` | `ResourceLoader` protocol (`load(uri, *, max_bytes=None) -> bytes`), `SafeFileLoader`, `HTTPLoader`, `SchemeLoader`, `build_loader(root, *, file_loader=, http_client=)`, `LoaderError`, `WorkspaceEscapeError`, `UnsupportedSchemeError` |
| `pyraml.yamlnode` | `Node`, `NodeKind`, `compose(source, *, uri)`, `pairs(node)`, `is_null`, `duplicate_keys`, `read_head`, `last_leaf`, `end_line`, `end_column`, `backend_name`, `TAG_STR`/`TAG_INT`/`TAG_NULL`/`TAG_INCLUDE`/`TAG_MAP`/… |

Three **leaf modules** were built ahead of the critical path and are already
merged. Phase 1 does not touch them; later phases consume them:

| Module | Surface | Consumed by |
|---|---|---|
| `pyraml.types.expressions.parser` | `parse_expression(text) -> RdtNode`, AST: `Primitive`, `Reference`, `Array`, `Optional_`, `Union` | Phase 3 |
| `pyraml.parser.templates` | `VariableInfo`, `parse_template_variables`, `collect_variables_index`, `collect_required_variables`, `iter_indexed`, `apply_template_action` | Phase 6 |
| `pyraml.parser.uritemplates` | `UriTemplateExpression`, `extract_uri_template_params`, `resource_path_name` | Phase 5 |

Empty packages `pyraml/types/` and `pyraml/parser/` exist with docstrings.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding project rules. The "Rules that look like style but
   are load-bearing" section is not style advice.
2. **`docs/04-fragments-and-namespaces.md`** — your primary specification.
3. **`docs/03-yaml-and-io.md`** sections 3–7 — fragment identification,
   `!include`, `DataNode`, the annotated-scalar form.
4. **`docs/02-architecture.md`** sections 1, 3, 4 — the pass order, the `Raml`
   field list, the invariants.

Skim only: doc 05 (so you know what shape of seam Phase 2 needs), doc 15 Phase 1.

---

## 3. What to build

### 3.1 `pyraml/registry.py` — the `Raml` object

Create the **full** field set from `docs/02-architecture.md` § 3, even though
Phase 1 populates only some of it. The rest are declared and left empty; later
phases fill them without editing this file again.

Phase 1 must make these work:

- `fragments: dict[str, Fragment]` — one decode per file
- `include_nodes: dict[str, Node]` — one compose per file
- `include_refs: dict[str, list[IncludeRef]]`
- `fragment_types`, `fragment_annotations`, `fragment_typedefs` — created empty;
  Phase 2 writes to them
- `domain_extensions: list[DomainExtension]`
- `global_protocols`, `global_media_types`, `global_secured_by`
- `_id_counter` — one `itertools.count(1)`; every entity takes `next()`
- `_parse_ctx_stack` with `push_ctx` / `pop_ctx` / `current_ctx`
- `loader`, `workspace_root_uri`, `max_include_size`, `retain_source`
- `entry_point`

`ParseCtx` is `@dataclass(frozen=True, slots=True)` with one field,
`anchor: ReferenceResolver | None`.

### 3.2 `pyraml/parser/fragments.py`

`FragmentKind` enum, the `HEADS` table from doc 03 § 3, `identify_fragment(head)`.

The `Fragment`, `ReferenceResolver` and `SecuritySchemeResolver` protocols from
doc 04 § 2.

Fragment classes. **Decode only what Phase 1 owns**, and retain the rest as raw
`Node` for later phases — that is the seam:

| Fragment | Phase 1 decodes | Retained raw for later |
|---|---|---|
| `APIFragment` | `title` (required, non-empty), `description`, `version`, `baseUri`, `protocols`, `mediaType`, `documentation`, `uses`, `(annotations)` | `types`/`schemas`, `annotationTypes`, `baseUriParameters` → Phase 2; `traits`, `resourceTypes` → Phase 6; `securitySchemes`, `securedBy` → Phase 7; `/relativeUri` → Phase 5 |
| `Library` | `usage`, `uses`, `(annotations)` | `types`, `annotationTypes` → Phase 2; the rest as above |
| `DataTypeFragment` | `uses` | the whole remaining mapping → Phase 2 |
| `NamedExample` | `uses` | each named example → Phase 2 |
| `DocumentationItemFragment` | `title`, `content`, `uses` | — |
| `TraitFragment`, `ResourceTypeFragment`, `SecuritySchemeFragment` | `uses` only | body → Phases 6/7 |

Store retained nodes on a clearly named attribute (`_raw_types`,
`_raw_endpoints`, …) so the later phase has an obvious hook and `grep` finds
every seam.

The `mediaType`/`protocols`/`securedBy` **pre-pass** on `APIFragment` runs before
the main key loop and writes `raml.global_*`. Later decoding depends on those
globals, so ordering is not optional (doc 04 § 5.1).

### 3.3 `pyraml/parser/includes.py`

`IncludeInfo`, `IncludeRef`, `resolve_include_uri`, `note_include_ref`,
`resolve_include` — doc 03 § 4.

### 3.4 `pyraml/datanode.py`

`ValueNode`, `MappingValue`, `MappingEntry`, `SequenceValue`, `SequenceItem`,
`DataNode` — doc 03 § 6. `ValueNode.raw` is computed once at construction.

Inline JSON: a scalar whose text begins with `{` or `[` is parsed as JSON.

### 3.5 `pyraml/parser/facets.py`

`ScalarFacet[T]`, `make_scalar_facet(raml, key_node, value_node, location)`, and
`resolve_annotated_scalar` — doc 03 § 7. One builder serves every scalar facet in
the language, which is why the annotated-scalar form works everywhere without
per-facet code.

### 3.6 `pyraml/parser/references.py`

`resolve_reference` and `resolve_library_reference` — doc 04 § 3.

### 3.7 `pyraml/parser/annotations.py`

`DomainExtension` and `unmarshal_domain_extension` — doc 09 § B1, B3. **Build and
register them; do not resolve them.** Resolution needs annotation types (Phase 2).

### 3.8 `pyraml/parser/entry.py`

`parse_from_path`, `parse_from_string`, `ParseOptions` (doc 13 § 2), and the
pass driver. Phase 1 runs P0–P3 only; leave the later passes as clearly marked
no-ops.

---

## 4. Decisions already settled — do not re-litigate

Each of these is load-bearing and each is easy to "simplify" into a bug.

1. **Register the fragment in `raml.fragments` *before* decoding its body.**
   `a.raml` → `b.raml` → `a.raml` then terminates with a cyclic object graph
   instead of recursing forever.
2. **Resolve `uses:` *after* the body, as a separate stage.** During body
   decoding every `LibraryLink.link` is `None`, and nothing dereferences it
   because all name resolution is deferred to P7. Resolving first is simpler and
   breaks mutual imports.
3. **A typed fragment's decoder pushes its *own* `ParseCtx`, never the caller's.**
   This is deviation D4 and it is what makes the fragment cache sound: without
   it the same file would mean different things at different inclusion sites.
4. **The SecurityScheme fragment must not resolve shapes eagerly.** Its
   `describedBy` bodies are embedded into operations and belong to the same
   global resolution batch. Eager resolution races the parent's `uses:`
   resolution. go-raml has a long comment about exactly this.
5. **`note_include_ref` vs `resolve_include`.** Use `note_include_ref` when a
   *fragment* parser will load the target through its own cache; use
   `resolve_include` only when the content is spliced into the current tree.
   Getting this wrong doubles I/O for every typed-fragment include.
6. **Split reference names on the *last* dot.** RAML type names may contain
   dots. This also enforces "no namespace chaining" for free.
7. **`reference_annotation_type` falls back to `types`.** An annotation type may
   extend a data type.
8. **A RAML-absolute include (`/types/x.raml`) resolves against the workspace
   root**, not the filesystem root.
9. **An `!include` of a non-YAML extension becomes a `!!str` scalar node** —
   that is how `content: !include legal.md` works.
10. **Enforce `max_include_size` by reading `limit + 1` bytes**, so an oversized
    file is detected without being read.

---

## 5. Reference source — read ranges, not files

go-raml is at `../go-raml-main`. The documents already capture its decisions;
open it only for a mechanical detail. Reading it wholesale is what exhausts a
session's context.

| Need | File and lines |
|---|---|
| The registry's fields and caches | `raml.go` 1–200 |
| `resolveUses` | `parse.go` 24–48 |
| Fragment head table | `parse.go` 65–93 |
| Kind check, cache lookup | `parse.go` 188–292 |
| Per-kind decode/parse pairs | `parse.go` 446–528 |
| Entry points and the pass driver | `parse.go` 577–752 |
| Include resolution and caching | `node.go` 332–465 |
| `DataNode` / `ValueNode` | `node.go` 467–603 |
| Annotated scalar, `MakeNode` | `node.go` 228–330 |
| `uses:`, `types:` unmarshalling | `fragment.go` 96–182 |
| `Library` | `fragment.go` 184–260 |
| `APIFragment` and its pre-pass | `fragment.go` 812–1100 |
| Reference resolution | `fragment_utils.go` (all 93 lines) |
| Domain extensions | `extension.go` (all 52 lines) |
| Documentation items | `documentationitem.go` 1–130 |

---

## 6. Definition of done

From `docs/15-implementation-plan.md` Phase 1, made concrete:

- A library with `uses:` and `!include`s parses into a fragment graph with every
  link resolved.
- Two mutually importing libraries terminate, and the resulting graph is cyclic
  rather than duplicated.
- A **counting loader** (wrap `SafeFileLoader`, tally `load` calls) proves each
  file is read exactly once when referenced from five places.
- `parse_from_string` and `parse_from_path` both work; the latter defaults the
  workspace root to the entry file's directory.
- An `!include` cycle through *scalar* values reports `circular include detected`
  with a position.
- An oversized include is rejected without the whole file being read.
- A fragment whose header does not match its context fails fast with the two
  kinds named.
- `Fragments/` and `Libraries/` TCK categories are attempted. Many will still
  fail on types — that is expected. Record the new baseline:
  `PYRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck --update-ratchet`
  and read the diff before committing it.
- The full gate passes.

Unit tests belong in `tests/unit/test_registry.py`, `test_fragments.py`,
`test_includes.py`, `test_references.py`, `test_datanode.py`.

---

## 7. Scope boundary

Phase 1 builds **no shapes**. If you find yourself needing `BaseShape`,
`make_shape`, or a type expression, you have crossed into Phase 2 — stop and
leave a retained raw `Node` instead.

Likewise: no endpoints (Phase 5), no trait or resource-type compilation
(Phase 6), no security-scheme settings (Phase 7), no validation (Phase 8).

---

## 8. Working method

Branch first: `git checkout -b phase-1-fragments`. Commit in logical units with
`type:` prefixes. If the code must diverge from a document, **amend the document
in the same commit** — that rule has already caught two real defects, both found
by implementing a document and discovering it was wrong.
