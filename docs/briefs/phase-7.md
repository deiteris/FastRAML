# Phase 7 brief — security schemes

A working brief for a fresh session. Read this, then the document it names. It
exists so you do not have to re-derive what earlier sessions already settled.

Unlike Phase 6, this phase fails loudly. Every fixture waiting on it is an
`invalid-` one, which means the work is almost entirely *rejection*: decoding a
declaration is easy, and knowing which declarations are malformed is the phase.

---

## 0. What this brief got wrong

Kept rather than rewritten; the corrections have been the most useful part of
every brief since Phase 5.

1. **"`SecurityScheme` is built *from* a `DirectiveRef`" (§ 3.2) does not say
   where it lives, and the obvious answer is wrong.** Putting it in
   `security.py` makes `source_decode.py` — which builds these during stage 2 —
   import the module that resolves them, and `security.py` already imports
   `source_decode` for the `describedBy:` decoders. It goes in `directives.py`,
   beside the reference form it promotes.

2. **"the six settings variants" (§ 3.1) is a count of spec rows, not of
   classes.** What differs between the six is which keys each accepts and what
   each then requires — a table and a function. Six classes would name each type
   twice. `docs/09` §§ A2 and A5 amended, and the `OperationParamsApplier`
   protocol went with them: with one class there is nothing to dispatch on.

3. **§ 3.2's rule about API-rooted resolution was right and the doc did not
   contain it.** It is now `docs/09` § A6 rather than only a brief.

4. **Four unit tests had to be corrected, not the code.** Three used a second
   `securitySchemes:` key in the same document — a YAML duplicate, so the first
   block silently vanished — and one expected a bespoke "not found" diagnostic
   where the shared reference resolver already raises a better one. Worth
   naming because all four failed in the direction that looks like a parser bug.

---

## 1. Where the project stands

Master is at the Phase 6 merge. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
# 2110 passed, 54 skipped
```

TCK: **869 of 916**, ratchet clean. All 47 remaining failures are `invalid-`
fixtures. Twenty are this phase's:

| Category | Waiting on security |
|---|---|
| `SecuritySchemes/` | 11 |
| `EdgeCases/` (oauth settings, scheme types, `describedBy`, dotted names) | 7 |
| `Fragments/securityscheme/` | 2 |

The rest are Phase 8b (JSON Schema, 10), the documented `allowedTargets` and
union-facet gaps, and a scattering of type-system corners.

### 1.1 What you inherit

| Module | What you will use from it |
|---|---|
| `pyraml.parser.directives` | `DirectiveRef`, already decoded from every `securedBy:` — with `is_null_scheme` set for a `[null]` entry and `scope` for the site it was written at |
| `pyraml.parser.source_decode` | `_decode_responses`, and `make_property_map` / `make_shape` through it — `describedBy:` reuses all three, and must not reimplement any |
| `pyraml.parser.fragments` | `_one_definition` and `_definitions`, written for traits and generic over the definition kind |
| `pyraml.registry` | `Raml.global_secured_by`, declared and never written |
| `pyraml.domains` | `SECURITY_SCHEME` and `SECURITY_SCHEME_SETTINGS` |

### 1.2 The seams you pick up

- **`_raw_security_schemes`** on both `Library` and `APIFragment`.
- **`_raw_secured_by`** on `APIFragment`, harvested in `_preprocess` because
  everything decoded afterwards may need it.
- **`SecuritySchemeFragment._raw_definition`** — the last `_DefinitionFragment`
  that still keeps its body rather than decoding it. `TraitFragment` and
  `ResourceTypeFragment` show the shape to follow.
- **`Raml.global_secured_by`**, typed `list[SecurityScheme]` in the field list
  and never assigned.

After this phase `grep -rn '_raw_' pyraml/` should return only Phase 8b's.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding rules.
2. **`docs/09-security-and-annotations.md` Part A** — your whole specification.
   § A2's table is the six scheme types and what each requires; § A4 is the
   inheritance rule and the reason `explicit_secured_by` exists.
3. **`docs/02-architecture.md` § 3** — the layering rules. Two bite here: the
   `!include` of a definition is followed in `fragments.py`, and `security.py`
   may import `source_decode.py` but nothing may import `security.py` except
   `fragments.py` and the pass driver.

---

## 3. What to build

Doc 15's order.

### 3.1 `parser/security.py` — the declaration

`SecuritySchemeDefinition` (§ A1) and the six settings variants (§ A2). Decode
is the easy half; the half the fixtures test is rejection:

- an unknown `type:` that does not start with `x-`;
- a settings key the declared type does not define — `type: Basic
  Authentication` with an `accessTokenUri` is an error, not a silent no-op;
- settings on a type that has none at all (Basic, Digest, Pass Through, `x-`);
- OAuth 1.0 missing any of its three URIs, or naming a signature outside
  {`HMAC-SHA1`, `RSA-SHA1`, `PLAINTEXT`};
- OAuth 2.0 missing `accessTokenUri`, or an `authorizationGrants` entry that is
  neither one of the four RFC 6749 names nor an **absolute URI** — the spec
  allows extension grants such as
  `urn:ietf:params:oauth:grant-type:saml2-bearer`;
- an unknown key in `describedBy:`.

`describedBy:` reuses the operation decoders unchanged, and the shapes it
produces go into `fragment_typedefs` like everything else, so P7, P9 and P10
reach them without knowing security schemes exist.

### 3.2 The reference, and where it resolves

`SecurityScheme` (§ A1) is built *from* a `DirectiveRef` — the reference form
stays in `directives.py`, per `docs/02` § 3. `EndPoint.secured_by` and
`Operation.secured_by` change from `list[DirectiveRef]` to
`list[SecurityScheme]`.

A `[null]` entry gets a pre-built null definition rather than `None`, so nothing
downstream special-cases it (§ A3).

**Scheme names resolve against the API, not lexically** — the one place this
phase differs from traits and resource types. Only a `Library` and an
`APIFragment` declare `securitySchemes:`, and a `securedBy:` written inside a
trait fragment therefore has no lexical namespace that could contain one. Match
go-raml here and say so in the doc.

### 3.3 P5 — inheritance and application

§ A4, and it is three lines of logic guarded by one flag:

```
if operation.explicit_secured_by:  leave it alone
elif endpoint.explicit_secured_by: operation.secured_by = endpoint.secured_by   # replace
else:                              both already hold the API global
```

Replace, never append. That is what makes `securedBy: [null]` on a method
*remove* inherited security instead of adding to it. Resource-level schemes do
not reach nested resources.

Then § A5: per-application parameters. Only OAuth 2.0 has any — `scopes` must be
a subset of the declared ones — and a `scopes` override on any other scheme type
is an error rather than a no-op. The result goes on
`SecurityScheme.compiled_params`, never on the shared definition: the same
scheme applied to two operations with different scopes must not interfere.

### 3.4 The two annotation sites

`Raml.target_scope(DomainLocation.SECURITY_SCHEME)` around a definition's decode,
and `SECURITY_SCHEME_SETTINGS` around its `settings:`. The mechanism is Phase
4b's and the enum members already exist; a missing scope is **silent** — it
records the enclosing site — so each needs a test that names it
(`docs/09` § B5).

---

## 4. Decisions already settled — do not re-litigate

1. **The reference form stays in `directives.py`** (`docs/02` § 3). Stage 1
   already decodes every `securedBy:`, two phases before this module existed.
2. **A null entry is a scheme, not a `None`** (§ A3).
3. **`describedBy:` reuses the operation decoders.** There is no second
   implementation of headers, query parameters or responses.
4. **`explicit_secured_by` is already on the IR and already threaded through
   stage 2.** Phase 5 built it for this.
5. **The `!include` of a definition is followed in `fragments.py`.** Phase 6
   settled the layering; `_one_definition` is already generic.

---

## 5. Reference source

go-raml is at `../go-raml-main`; `securityscheme.go` is the whole declaration
side and `unwrap.go`'s `applySecuritySchemes` / `applySecurityScheme` the
application side. **Read it, and run it when the question is what it does** — the
last two phases each turned up a place where its comments describe behaviour it
does not have.

`KNOWN-ISSUES.md` in that checkout records six divergences. Add to it if this
phase turns up more; where a *fixture* is wrong, fix it there (`docs/14` § 1.2).

---

## 6. Definition of done

- `SecuritySchemes/` passes, along with the security `EdgeCases/` and
  `Fragments/securityscheme/`.
- A test per rejection rule in § 3.1, asserting the message key and `info`, not
  assembled text.
- The three inheritance cases of § A4, including `securedBy: [null]` on a method
  removing an inherited scheme — the case the flag exists for.
- Scope narrowing: a subset accepted, a non-subset rejected, and `scopes` on a
  Basic scheme rejected.
- A test that a `describedBy:` shape reaches `fragment_typedefs`, which is what
  makes P9 and P10 see it.
- The full gate passes.

**Ratchet expectation: one-directional.** Every fixture this phase touches is an
`invalid-` one, so the risk is not silent wrongness — it is rejecting something
valid. Watch the *valid* side of the ratchet, not the invalid side.

Unit tests: `tests/unit/test_security.py`.

---

## 7. Scope boundary

Phase 7 decodes and applies security schemes. It does **not** enforce
`allowedTargets` — that is Phase 8's, with the rest of validation, and the
`Annotations/target-locations/invalid-*` fixtures stay failing until then.

---

## 8. Working method

Branch: `git checkout -b phase-7-security`. One logical change per commit;
**commit the ratchet separately from the code that moves it.**

If the code must diverge from a document, **amend the document in the same
commit**, and record what this brief got wrong in a section at its top.
