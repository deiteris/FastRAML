# Phase 5 brief — endpoints, stages 1 and 2 (P4, P6)

A working brief for a fresh session. Read this, then the document it names. It
exists so you do not have to re-derive what earlier sessions already settled.

---

## 1. Where the project stands

Master is at the Phase 8a merge plus two follow-up fixes. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
# 1965 passed, 42 skipped
```

TCK: 740 of 930, ratchet clean.

**Every pass P0–P10 runs.** The type system is complete but for JSON Schema
(Phase 8b) and one tracked union item ([01](01-scope-and-coverage.md) § 3.7).
What is missing is not machinery but *reach*: 106 fixtures still failing need
endpoints and nothing else, and another 84 need endpoints plus templates.

| Category | Endpoints alone | Also needs templates |
|---|---|---|
| `Types/` | 19 | 0 |
| `Methods/` | 17 | 0 |
| `MethodResponses/` | 16 | 0 |
| `EdgeCases/` | 12 | 13 |
| `Annotations/` | 11 | 3 |
| `Resources/` | 9 | 9 |
| `Fragments/` | 8 | 3 |
| `Responses/` | 6 | 0 |
| everything else | 8 | 46 |
| **total** | **106** | **84** |

That `Types/` row is the shape of this phase: nineteen *type* fixtures fail
because their types live under a resource the parser does not decode. P10
already knows how to reject them.

### 1.1 What you inherit

| Module | What you will use from it |
|---|---|
| `pyraml.parser.uritemplates` | `extract_uri_template_params`, `resource_path_name`, `UriTemplateExpression` — written ahead of the critical path, unused so far |
| `pyraml.types.shape` | `make_shape`, `make_body_shape`, `make_property`, `make_property_map` |
| `pyraml.registry` | `Raml.endpoints`, `global_media_types`, `global_protocols`, `ParseCtx`, `target_scope` |
| `pyraml.domains` | `DomainLocation` — eleven members are unreachable until this phase and Phases 6–7 create their sites |
| `pyraml.parser.facets` | the scalar-facet builders |

### 1.2 The seams you pick up

- **`APIFragment._raw_endpoints`** — `list[tuple[Node, Node]]`, every `/path`
  key and its value, in declaration order (`fragments.py`).
- **`entry.py`** — `# P4-P6 — endpoints, security schemes, URI parameter
  propagation. API only. Phases 5 to 7`, between P3 and P7.
- **`Raml.endpoints`** — declared `dict[str, EndPoint]`, never written.
- **`Raml.global_media_types`** — already filled from `mediaType:` at the root.

`_raw_secured_by` and `_raw_security_schemes` are Phase 7's; leave them.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding rules. The two that bite here: **declaration order
   is preserved everywhere the model is exposed**, and **structural merge never
   mutates either input** (Phase 6's, but stage 1 is built to make it possible).
2. **`docs/08-templates-and-endpoints.md` §§ 2, 3, 8** — your specification.
   § 2 explains why the two stages exist at all; do not skip it and rediscover
   the reason by building the one-stage version.
3. **`docs/02-architecture.md` § 1** — **P4 before P7** is one of the two
   orderings the pipeline requires, and this phase is what makes it matter.

Skim only: § 6 (provenance — Phase 6 fills it, but stage 1 must carry the
`scope` and `provenance` slots), doc 13 § 3 for the public surface.

---

## 3. What to build

Doc 15's Phase 5 build list, in dependency order.

### 3.1 `parser/source_ir.py` — stage 1

`SourceEndPoint` and `SourceOperation`, exactly the slot lists in doc 08 § 3.
Decode **only** the four directive keys (`type:`, `is:`, `securedBy:`, and the
method / `/subresource` recursions); everything else is appended verbatim to a
rebuilt `body` mapping node.

**Directive resolution is a no-op stub in this phase.** `type:` and `is:` are
parsed into references and stored, never applied. That is Phase 6's, and the IR
exists precisely so Phase 6 has something to merge into.

Fill `scope` from `raml.current_ctx()` at decode time. `provenance` stays empty
until Phase 6, but the slot is declared now: adding it later means touching
every construction site.

### 3.2 `parser/endpoints.py` — the model

`EndPoint`, `Operation`, `Request`, `Response`, `Body`. `__slots__` on every one
(`@dataclass(slots=True, eq=False)` where a dataclass suits), and declaration
order preserved in every map.

### 3.3 `parser/source_decode.py` — stage 2

Materialize the IR into the model, **decoding each retained tree exactly once**.
The one-decode rule is the whole point of the split (§ 2): a body decoded once
per media type, or once per trait application, is where the naive version's cost
goes.

The two loops in § 3 run over the whole tree before the other starts — resolve
directives for every endpoint, *then* decode every endpoint. Phase 6 needs that
ordering; write it now so it does not have to be retrofitted.

### 3.4 Headers, query parameters, the query string

All properties declarations, so they reuse `make_property_map` — which already
registers each one in `fragment_typedefs`, which is what makes P9 and P10 see
them without either pass changing (this is why Phase 8a could land first).

`queryString` is a single shape, not a property map, and is mutually exclusive
with `queryParameters`.

### 3.5 Media types and bodies (doc 08 § 8.3)

`decode_media_type_node`'s two spellings, the "instantiate once per default
media type" rule, and the mixed-keys error that catches
`body: {application/json: …, type: Foo}`.

With no root `mediaType:`, a body that does not name one is
`explicit media type is required`.

### 3.6 URI templates and P6 (doc 08 §§ 8.1–8.2)

`uritemplates.py` already parses and positions errors; this phase *calls* it.

- synthesise a required `string` for every template variable with no
  `uriParameters` entry;
- a declared parameter absent from the template is `uri parameter is not used`;
- `default`/`enum`/`example`/`examples` values may not contain `/`;
- **P6 propagation**: each endpoint's parameter map is rewritten to
  ancestor-declared parameters first, then its own, in path order.

The same routine serves `baseUri` + `baseUriParameters`, which is what
`Root/baseuri/invalid-wrong-param.raml` is waiting for.

### 3.7 Duplicate URIs (doc 08 § 8.1)

Compared on template *text*, unexpanded, so `/users/{userId}`,
`/users/{username}` and `/users/me` coexist while `/users: {/foo:}` and
`/users/foo:` collide.

### 3.8 The `DomainLocation` sites this phase creates

`RESOURCE`, `METHOD`, `RESPONSE`, `REQUEST_BODY`, `RESPONSE_BODY`. Wrap each
decoder in `Raml.target_scope(...)`. A missing scope is **silent** — the
annotation records the enclosing site instead — so each one needs a test that
names it (`CLAUDE.md`; `tests/unit/test_domain_extensions.py` has the pattern).

This is what lights up the `Annotations/` fixtures whose application site is a
resource or a method.

---

## 4. Decisions already settled — do not re-litigate

1. **Two stages, not one** (doc 08 § 2). Eager decoding costs more *and* is
   wrong: a trait grafted after decode would need its subtree re-decoded, and
   its type names resolved in the wrong namespace.
2. **P4 runs before P7** (doc 02 § 1). Template-contributed subtrees must become
   shapes before the worklist drains, or they never resolve.
3. **Directive resolution is stubbed here.** `type:`/`is:` parsed and stored,
   not applied.
4. **`full_uri` excludes the base URI** (doc 08 § 8.1); the base is exposed
   separately on the API.
5. **Stage 1 keeps YAML, stage 2 makes shapes.** No shape exists for a header,
   query parameter, body or response until stage 2.
6. **Declaration order is preserved** in `endpoints`, `operations`, `responses`,
   headers and query parameters — asserted over the corpus by
   `tests/tck/test_invariants.py::TestDeclarationOrder`, which will start seeing
   endpoints the moment this lands.

---

## 5. Reference source — read ranges, not files

go-raml is at `../go-raml-main`. Go is installed: when the question is what it
*does*, run it (`go test -run <name> .` against a throwaway `zz_*_test.go`,
deleted afterwards). **Read its implementation as well as measuring it** — Phase
8a's worst regression came from probing behaviour that thirty seconds in
`validate.go` would have answered outright.

| Need | Where |
|---|---|
| Stage 1 | `endpoint.go`, grep `makeSourceEndPoint` |
| Stage 2 | grep `decodeSourceEndPoint` |
| Media types and bodies | grep `decodeMediaTypeNode` |
| URI parameters and propagation | grep `resolveURIParameters` |

`KNOWN-ISSUES.md` in that checkout records three places it diverges from the
spec; add to it if this phase turns up more.

---

## 6. Definition of done

- `Resources/`, `Methods/`, `Responses/`, `MethodResponses/` pass except where a
  fixture uses a trait or resource type.
- A nested resource exposes ancestor URI parameters before its own, in path
  order.
- A duplicate absolute URI is rejected; three sibling templates with different
  variable names are not.
- A body with no media type and no root `mediaType:` is an error; with a root
  `mediaType:` it is instantiated once per default.
- Each of the five new `DomainLocation`s has a test that an annotation applied
  there records it.
- **Every shape a stage-2 decode creates is in `fragment_typedefs`**, so P9 and
  P10 reach it. Assert it over the corpus — it is the seam that makes Phase 8a's
  work apply to endpoints, and it fails silently.
- The full gate passes.

**Ratchet expectation: large, and in both directions.** Up to ~106 fixtures are
reachable. But this phase creates shapes that P10 will validate for the first
time, so a valid fixture can regress on a *validation* error that is really a
decode bug. Read every line of the diff; a valid fixture moving to `fail` is
this phase's bug, not Phase 8a's.

Unit tests: `tests/unit/test_source_ir.py`, `tests/unit/test_endpoints.py`.

---

## 7. Scope boundary

Phase 5 builds endpoints. It does **not**:

- apply `type:` or `is:` — Phase 6 (the structural merge, `templates.py` is
  already written);
- decode `securitySchemes` or apply `securedBy` — Phase 7;
- touch doc 10 § 6.2's four decoders, which need Phase 8b's `JsonShape` work as
  well as this phase's decoders.

---

## 8. Working method

Branch: `git checkout -b phase-5-endpoints`. One logical change per commit —
stage 1, the model, stage 2, media types, URI parameters, propagation. **Commit
the ratchet separately from the code that moves it.**

If the code must diverge from a document, **amend the document in the same
commit**. Every phase so far has owed several, and each was a real defect in the
plan rather than a formality: Phase 4b's design changed outright on contact with
`make_scalar_facet`'s call sites, and Phase 8a's `as_fraction` rule was
specified wrongly in doc 10 and made `multipleOf: 1.1` reject `2.2`.
