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
- Code generation, HTTP middleware, an LSP server, format converters (OpenAPI,
  AMF graph). The model is designed so these can be built on top.

  **JSON Schema is the exception, and it is in.** `views/jsonschema.py` converts
  a shape to draft-07 ([16](16-graph.md) § 12). It went in rather than beside
  because it is the same kind of thing `views/` already holds — a projection of
  the model that decides no RAML rule — and because the reverse direction is
  already here: `types/jsonschema_.py` reads JSON Schema, so a parser that only
  read one and never wrote one was the odd shape. go-raml keeps its equivalent
  in the same repository, at `converter/jsonschema.go`, which this follows.
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
| Facets written on a union declaration | v1 | Distributed to the members at P9. See § 3.7 |
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

### 3.7 Facets on a union declaration

`T: {type: A | B, maximum: 2}` constrains **both** branches. This section
recorded it as a known gap through Phase 8b — parsed and not enforced — because
the failure mode is silent: a document that looks constrained was not. It is
implemented, and the section is kept because the shape of the answer is not
obvious.

The spec. § Union Type: an instance is valid "if and only if it is a valid
instance of at least one of the super types obtained by expanding all unions in
that type hierarchy", so the facet constrains each expanded branch.

**A union recognises no facets of its own, and it cannot.** Whether `minimum` is
a built-in facet, a custom one or a mistake is a question only a *member* can
answer, and the members may answer differently — the TCK's
`union-with-facets/valid-custom-facet.raml` has `integer | number | string` where
`minimum` is built-in for two members and a `facets:` declaration on the third.
So `UnionShape` keeps the facets as YAML (`pending_facets`) rather than digesting
them, and P9 hands them to each member to decode
([07](07-resolution-and-inheritance.md) § 3.4). A facet no member can place
surfaces as `unknown facet` positioned on the member it could not be placed on.

**Each member is replaced by a subtype of itself, never modified in place.** Two
independent reasons: the merge's "both unions" branch adopts the parent's member
objects by reference, so writing to one would narrow the parent type for every
other subtype of it; and only a subtype makes the member's own `facets:`
declarations visible to P10, which walks from `inherits[0]`.

The reference implementation still has the gap, and a worse one: its
`UnionShape.unmarshalYAMLNodes` discards every facet but `discriminator`
outright, so the constraint is not merely unenforced but unrecoverable. Written
up as entry 1 of `KNOWN-ISSUES.md` in that checkout, with a reproduction.

## 4. Deliberate deviations

Each deviation is a decision, not an accident. All are surfaced in the public
docs and, where user-visible, in the error message.

### D1 — No XSD

`!include x.xsd` where a type or fragment is expected fails with
`xml schema external types are not supported`. Users pre-convert to JSON Schema
or RAML types. Rationale: a conformant XSD validator is a project of comparable
size to this parser.

Two corrections from the Phase 9 reconciliation. This section used to say
"matching go-raml"; **go-raml has no XSD handling at all** and the TCK ships no
`.xsd` fixture, so there was nothing to match — the message is pyRAML's own.
And until Phase 9 there was no such message: the file reached the header check
and produced `unknown fragment kind: head: <?xml version="1.0"?>`, which is true,
useless, and points the author at the wrong thing to fix.

Only `.xsd` is rejected. `!include foo.xml` at a value position is an ordinary
non-YAML scalar include and may perfectly well be an example.

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

**What `re2` covers, exactly.** Every regex pyRAML compiles goes through one
function, `parser/facets.py::regex_engine`: `pattern:` facets, `/re/` pattern
properties, and the patterns the § 6.3 JSON Schema projection builds. A pattern
the engine will not take is a positioned `invalid pattern` for a RAML facet, and
is dropped from the *view* for a projected one — the projection is a convenience
over a schema that still validates, so refusing the whole type would lose more
than it protects.

**What it cannot cover**, and this is the boundary a security-conscious consumer
needs: the regexes executed *inside* an external JSON Schema at validation time.
`jsonschema` calls `re.search` directly for `pattern` and `patternProperties`
and offers no hook to replace the engine. So under `regex_engine="re2"` a
backreference in a `pattern:` facet is refused and the same backreference inside
an `!include`d schema still runs on `re`. A test asserts this rather than
leaving it to be discovered.

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

### D11 — A JSON-schema type is a type, and may be used as one

Spec § Using XML and JSON Schemas states that a type defining an external schema
"MUST NOT participate in type inheritance or specialization, or effectively in
any type expression", and separately that schemas are "forbidden in any
declaration of query parameters, query string, URI parameters, and headers".

pyRAML enforces the **inheritance** half and not the rest. A `JsonShape` is a
container for a compiled schema that answers `validate(value)`. Everywhere the
spec forbids — a property, an array item, a union member, a parameter — that is
the only thing asked of it, and delegating is the whole implementation. A union
is a list of types to validate against; the union itself is just an entry point.

Inheritance is genuinely different and stays refused: it asks for a RAML facet
to be *merged into* a compiled schema, and there is no such operation.
`JsonShape.inherit` errors unless the source carries the identical raw schema,
which is also exactly go-raml's behaviour.

Established by measurement rather than by reading:

- The restriction is not load-bearing. With the checks removed, unions, arrays,
  optionals and parameters all validate correctly through the compiled schema,
  with positioned diagnostics. Nothing else needed changing.
- go-raml has no parameter check at all, and accepts schema types in type
  expressions.
- The parameter half costs no TCK fixture. The expression half costs exactly one
  — `spec-examples/APIs/external-type-extend-invalid.raml`, the spec's own
  example — which is recorded in `SKIPPED_FIXTURES` with this deviation as its
  reason. It is skipped rather than ratcheted to `fail`, because a `fail` entry
  means work outstanding ([14](14-testing.md) § 1.2) and this is a decision.
- AMF, the other widely used implementation, does not enforce it either.

The one hazard is real and accepted: an *object* schema as a query parameter
parses and then rejects every possible value, because a parameter arrives as
text. That is not specific to schemas — a plain RAML `type: object` query
parameter is equally unsatisfiable and equally unrefused — so banning one
spelling of it prevents nothing.

### D12 — A discriminated union dispatches

Spec § Using Discriminator leaves the choice open (`raml-10.md:762`):

> A RAML processor **MAY** provide an implementation that automatically selects a
> concrete type from a set of possible types, but a simpler alternative is to
> store a unique value associated with the type inside the object.

pyRAML provides one. Where every member of a union is an object declaring the
same `discriminator` with distinct values, validation looks the tag up in a table
instead of trying each member in turn ([05](05-type-model.md) § 9.1).

**This is a deviation because it narrows.** The spec's general union rule is that
a value is valid if it is a valid instance of at least one member, so
`{kind: Dog, meows: true}` against `Cat | Dog` is a valid `Cat` wherever `Cat`
does not constrain `kind`. Dispatch refuses it. The MAY clause licenses that:
selecting the concrete type *is* the alternative implementation it offers, and an
author who writes a discriminator has said the tag identifies the type.

Two cases are deliberately left alone:

- An **absent** tag falls back to the linear scan. Whether the property is
  required is the members' own rule, and they report it better than a dispatch
  failure would.
- A union that does not discriminate uniformly is scanned, exactly as before.
  A document that never wrote a discriminator sees no change at all.

Costs no TCK fixture; the suite stands at 951. `discriminator` **MUST NOT**
appear on a union type itself (`raml-10.md:833`), and that rule is untouched —
it is still refused at decode time. The table is built from the members' own
declarations, and a use site written `Cat | Dog` is how a discriminated hierarchy
reaches a body.

## 5. Dependency budget

| Dependency | Purpose | Required? |
|-----------|---------|-----------|
| `PyYAML` (with libyaml if available) | composition to a node tree; scalar resolution is replaced with YAML 1.2 ([03](03-yaml-and-io.md) § 2.2) | yes |
| `jsonschema` + `referencing` | JSON Schema external types | yes |
| `inflect` (or a vendored irregular-noun table) | `!singularize` / `!pluralize` | yes |
| `ruamel.yaml` | the YAML 1.2 oracle in `tests/conformance` | dev only |
| `google-re2` | opt-in linear-time regex engine (D3) | optional |
| `httpx` / `requests` | remote includes | optional, `pyraml[http]` or user-supplied; synchronous only ([03](03-yaml-and-io.md) § 5.1) |

Everything else is standard library. No runtime dependency on a compiled
extension is *required*; libyaml is a large but optional speed-up.
