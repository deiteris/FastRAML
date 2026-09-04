# 01 — Scope and spec coverage

## 1. Goals

1. A **compliant RAML 1.0 parser** for Python 3.12+, producing a fully typed,
   navigable model of an API definition or data-type fragment.
2. An **easy interface**: `parse_from_path(...)` / `parse_from_string(...)`,
   plus `shape.validate(value)` for instance validation against a declared type.
3. **Optimal time and memory** for definitions of any size. go-raml parses a
   7124-type / 148-library project in ~280 ms / ~48 MB. Python cannot match a
   compiled language, but the *algorithmic* choices that produce that number are
   language-independent and are all carried over (see
   [12-performance.md](12-performance.md)). Target: within one order of magnitude
   of go-raml on the same corpus, and strictly linear in input size.
4. **Position-accurate diagnostics** for every error, so the model can back an
   LSP server, a linter, or a formatter later.

## 2. Non-goals (initially)

- XML Schema (XSD) external types. `!include foo.xsd` is rejected with a clear
  error, matching go-raml. XML *serialization hints* (the `xml:` facet) are parsed
  and retained, but nothing serializes to XML.
- Code generation, HTTP middleware, an LSP server, format converters (JSON Schema,
  OpenAPI, AMF graph). The model is designed so these can be built on top; none
  ships in v1.
- Thread safety. A parser instance is single-threaded, exactly as in go-raml
  (`// WARNING: Not thread-safe`). Parallelism, if ever needed, is across
  independent parser instances.

## 3. Spec coverage matrix

Legend: **v1** = required for the first release · **v1.1** = planned follow-up ·
**out** = explicitly not supported.

### 3.1 Document root (spec § The Root of the Document)

| Node | Status | Notes |
|------|--------|-------|
| `title` | v1 | Required; must be non-empty |
| `description`, `version` | v1 | Annotated-scalar form supported |
| `baseUri`, `baseUriParameters` | v1 | Template parsed (RFC 6570 L1/L2); undeclared vars synthesised as required `string` |
| `protocols` | v1 | Non-empty, case-insensitive `HTTP`/`HTTPS` |
| `mediaType` | v1 | Scalar or sequence; validated as RFC 6838 `type/subtype` |
| `documentation` | v1 | Sequence of `{title, content}`; `!include` per item |
| `types` / `schemas` | v1 | Mutually exclusive; `schemas` is a deprecated alias |
| `traits`, `resourceTypes` | v1 | See [08](08-templates-and-endpoints.md) |
| `annotationTypes` | v1 | See [09](09-security-and-annotations.md) |
| `securitySchemes`, `securedBy` | v1 | See [09](09-security-and-annotations.md) |
| `uses` | v1 | See [04](04-fragments-and-namespaces.md) |
| `(annotationName)` | v1 | Domain extensions at every documented target |
| `/relativeUri` | v1 | See [08](08-templates-and-endpoints.md) |

### 3.2 Data types (spec § RAML Data Types)

| Feature | Status | Notes |
|---------|--------|-------|
| Type declarations, all common facets | v1 | `default`, `schema`/`type`, `example(s)`, `displayName`, `description`, annotations, `facets`, `xml`, `enum` |
| `any` | v1 | |
| `object`: `properties`, `minProperties`, `maxProperties`, `additionalProperties`, `discriminator`, `discriminatorValue` | v1 | |
| Property optionality via `?` suffix and via `required:` | v1 | Including the `preference?` / `preference??` corner cases of spec § Property Declarations |
| Pattern properties `/re/` and `//` | v1 | First matching pattern wins; explicit property beats pattern |
| `array`: `items`, `minItems`, `maxItems`, `uniqueItems` | v1 | |
| Scalars: `string`, `number`, `integer`, `boolean`, `date-only`, `time-only`, `datetime-only`, `datetime`, `file`, `nil` | v1 | |
| Union types (`\|`) | v1 | Union+`enum` interaction: v1.1 (go-raml also defers it) |
| Facets written on a union declaration | **v1.1** | Parsed, **not enforced**. See below |
| JSON Schema external types | v1 | Draft 4/6/7/2019-09/2020-12 as supported by the chosen library |
| XML Schema external types | out | Documented deviation |
| References to inner schema elements (`file.json#/definitions/Foo`) | v1 | JSON Pointer only |
| User-defined facets (`facets:`) | v1 | Name collisions with built-ins rejected |
| Default-type determination | v1 | Spec § Determine Default Types, exactly |
| Type expressions (arrays, unions, groups, `?`) | v1 | See [06](06-type-expressions.md) |
| Multiple inheritance | v1 | Including union expansion combinatorics |
| Inline type declarations | v1 | |
| `example` / `examples`, `strict` | v1 | Validated unless `strict: false` |
| `xml:` facet | v1 (parse only) | Retained on the model; no serializer |
| Recursive types | v1 | Marked with a `RecursiveShape` after unwrap |

### 3.3 Resources, methods, responses

| Feature | Status |
|---------|--------|
| Nested resources, absolute-URI computation, duplicate-URI rejection | v1 |
| `uriParameters` (declared + synthesised), reserved `version`, `ext` | v1 |
| Methods `get/put/post/delete/patch/head/options` (+ `trace`/`connect` as an extension) | v1 |
| `headers`, `queryParameters`, `queryString` (mutually exclusive) | v1 |
| `body` with explicit and default media types | v1 |
| `responses`, status-code keys normalised to strings/ints, duplicate rejection | v1 |
| `protocols`, `is`, `securedBy` at method level | v1 |

### 3.4 Resource types and traits

| Feature | Status |
|---------|--------|
| Declaration inline and via typed fragments | v1 |
| Application with and without parameters | v1 |
| Reserved parameters `resourcePath`, `resourcePathName`, `methodName` | v1 |
| All ten transform functions (`!singularize` … `!upperhyphencase`) | v1 |
| Optional methods (`post?`) | v1 |
| Merge algorithm of spec § Algorithm of Merging Traits and Methods | v1 |
| Collection merge semantics (spec § Effect on Collections) | v1 |
| Resource-type chaining (`type:` inside a resource type) | v1 |

### 3.5 Modularization

| Feature | Status |
|---------|--------|
| `!include` (relative, workspace-absolute, URL) | v1 (URL opt-in) |
| Non-YAML includes as scalars | v1 |
| Typed fragments: `Library`, `DataType`, `NamedExample`, `DocumentationItem`, `ResourceType`, `Trait`, `AnnotationTypeDeclaration`, `SecurityScheme` | v1 |
| Libraries and `uses:` namespacing (no chaining) | v1 |
| `Overlay` / `Extension` fragments + merging rules | **v1.1** |

### 3.6 Annotations

| Feature | Status |
|---------|--------|
| `annotationTypes` declaration | v1 |
| Applying annotations, value validation against the annotation type | v1 |
| Annotating scalar-valued nodes (the `value:` map form) | v1 |
| `allowedTargets` enforcement | **v1** (go-raml leaves this unimplemented; pyRAML implements it — see [09](09-security-and-annotations.md) § Targets) |

### 3.7 Known gap: facets on a union declaration

`T: {type: A | B, maximum: 2}` is parsed and the `maximum` is **not enforced**.

This is a gap, not a deviation: § 4 below lists decisions we would defend, and
this is not one. It is recorded here because the failure mode is silent — a
document that looks constrained is not.

The spec is clear. § Union Type: an instance is valid "if and only if it is a
valid instance of at least one of the super types obtained by expanding all
unions in that type hierarchy", so the facet constrains each expanded branch.
Conformant behaviour is to distribute it to the members.

Why it is not done yet. P7 gives the declaration the *union* kind, and
`UnionShape` recognises no scalar facets, so `maximum` reaches `KindBase` and is
filed under `custom_facets` — where P10 would call it `unknown facet`. Doing it
properly means `UnionShape` retaining the undigested nodes and P9 decoding them
against each member once `anyOf` is settled, cloning the members first: the
empty-union branch of the merge adopts the parent's member objects by reference
([07](07-resolution-and-inheritance.md) § 3.4), so decoding in place would
mutate the parent for every other subtype. That is P7/P9 work.

The reference implementation has the same gap and a worse one: its
`UnionShape.unmarshalYAMLNodes` discards every facet but `discriminator`
outright. Written up in `KNOWN-ISSUES.md` in that checkout, with a reproduction.

Until then P10 skips the custom-facet check on a union base, with a comment
saying so, rather than reporting `unknown facet` for something the spec allows.

**This is the only gap the TCK still measures.** After Phase 8b the two fixtures
it accounts for are the only two that do not do what their name promises:

| Fixture | The facet that goes unenforced |
|---|---|
| `Types/Type Expressions/union-with-facets/invalid-not-supported-facet.raml` | one written directly on a union declaration |
| `Types/types-constraits-conflict/invalid-constraints-conflict.raml` | `minimum`/`maximum` on a subtype of a union, which is the same branch |

## 4. Deliberate deviations

Each deviation is a decision, not an accident. All are surfaced in the public
docs and, where user-visible, in the error message.

### D1 — No XSD

`!include x.xsd` fails with `xml schema external types are not supported`. Users
pre-convert to JSON Schema or RAML types. Rationale: a conformant XSD validator is
a project of comparable size to this parser.

### D2 — Numeric formats are not cross-compatible

The spec says `integer` inherits all facets of `number`, which literally implies
`format: float` is legal on an `integer`. pyRAML follows go-raml and rejects that:

- `number` accepts `float`, `double` only.
- `integer` accepts `int8`, `int16`, `int32`, `int`, `int64`, `long` only.

`int` is an alias of `int32`, `long` of `int64`, matching go-raml's size table.

### D3 — Regular-expression engine

go-raml uses Go's RE2: no backreferences, no lookaround, linear time.
Python's `re` is a backtracking engine: it *accepts* more of ECMA-262 but is
vulnerable to catastrophic backtracking on a hostile `pattern:` facet.

**Decision:** use `re` by default (better spec fidelity), and mitigate:

- Compile every `pattern:` / pattern-property regex **once, at parse time**, and
  report a positioned diagnostic on failure.
- Provide `ParseOptions(regex_engine=...)` with `"re"` (default) and `"re2"`
  (uses `google-re2` if installed, rejecting patterns it cannot compile). Servers
  that parse untrusted RAML should select `"re2"`.
- Document that `re` is used, so users know backreferences work here but not in
  go-raml — a compatibility note in both directions.

### D4 — Typed fragments are self-contained

A `Trait`, `ResourceType`, `DataType`, `SecurityScheme` or `NamedExample`
fragment may reference only types it declares itself or imports through its own
`uses:`. It cannot see the namespace of the document that includes it. Unqualified
dangling references inside a fragment are errors.

This rule is stricter than a plain reading of the spec, and it is what makes
fragments cacheable: each file is parsed once and reused at every inclusion site.
It is the parser's most far-reaching structural rule, inherited from go-raml
unchanged. See [04](04-fragments-and-namespaces.md) § Anchor scoping.

One case runs the other way. **Parameter values** passed to a template — for
example `is: [paged: {responseType: types.PagedResult}]` — resolve in the
*calling* document's namespace, because the author writes that text in the
caller. See [08](08-templates-and-endpoints.md) § Provenance.

### D5 — I/O is sandboxed to a workspace root

All file reads are confined to a workspace root directory (default: the directory
of the entry file, overridable). RAML "absolute" include paths (`/types/foo.raml`)
resolve against that root, per spec § Includes. Symlink escapes are refused.
Remote (`http(s)://`) includes are **disabled unless** an HTTP client is supplied.

### D6 — Include size limit

A single `!include` target is capped (default 64 KiB, configurable, `0` disables).
Prevents a hostile or accidental multi-gigabyte include from exhausting memory.
Note this is go-raml's `DefaultMaxIncludeSize`; the constant name in its README
says 1 MiB but the code says `1 << 16`. pyRAML documents 64 KiB and means it.

### D7 — Ordered maps are plain dicts

go-raml carries a third-party ordered map because Go maps are unordered, and the
spec requires processors to preserve declaration order (spec § The Root of the
Document). Python dicts preserve insertion order by language guarantee, so
pyRAML uses `dict` and gets the requirement for free.

### D8 — No ANTLR

go-raml generates its type-expression parser with ANTLR. The ANTLR Python runtime
is slow and a heavy dependency for a grammar of nine productions. pyRAML uses a
hand-written tokenizer plus recursive-descent parser with a memoised
expression→AST cache. See [06](06-type-expressions.md).

### D9 — A tab after a key's colon depends on the YAML backend

`title:<TAB>My API` parses under libyaml and is rejected by PyYAML's pure-Python
scanner. The tab is legal YAML; the pure scanner is wrong. Both scanners are
PyYAML's and neither is ours to fix, so pyRAML accepts the divergence rather than
pretending it does not exist.

Consequences, and the reason this is a recorded deviation rather than a silent
one: the backend is **not** purely a performance choice, `backend_name()` is
therefore semantic as well as diagnostic, and CI runs the whole suite under both
backends so the set of divergences cannot grow unnoticed
([14](14-testing.md) § 6). A user who hits it can replace the tab with a space,
or install libyaml.

### D10 — Two YAML 1.2 characters and one construct are not accepted

All three are PyYAML scanner limitations, shared with `gopkg.in/yaml.v3`, so
pyRAML is no stricter than the reference implementation:

- **U+2028 and U+2029.** YAML 1.1 reads them as line breaks; YAML 1.2 says they
  are ordinary characters. In an unquoted scalar PyYAML splits the line and then
  fails somewhere else, so `compose` detects the character on the failure path
  and reports `unquoted line separator character` at its exact position with the
  fix — quote the value. Quoted forms work today and are unaffected: the check
  runs only after composition has already failed.
- **A flow scalar beginning with a colon**, as in `[ ::vector ]`. Block sequences
  and plain values accept `::vector`; only the flow form is rejected. Not a
  construct RAML uses.

## 5. Dependency budget

| Dependency | Purpose | Required? |
|-----------|---------|-----------|
| `PyYAML` (with libyaml if available) | composition to a node tree; scalar resolution is replaced with YAML 1.2 ([03](03-yaml-and-io.md) § 2.2) | yes |
| `jsonschema` + `referencing` | JSON Schema external types | yes |
| `inflect` (or a vendored irregular-noun table) | `!singularize` / `!pluralize` | yes |
| `ruamel.yaml` | the YAML 1.2 oracle in `tests/conformance` | dev only |
| `google-re2` | opt-in linear-time regex engine (D3) | optional |
| `httpx` / `requests` | remote includes | optional, user-supplied client |

Everything else is standard library. No runtime dependency on a compiled
extension is *required*; libyaml is a large but optional speed-up.
