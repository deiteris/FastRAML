# 09 — Security schemes and annotations

Two features that share a shape: both are declared centrally, applied by
reference, and validated against their declaration.

---

## Part A — Security schemes

### A1. Model

```python
@dataclass(slots=True, eq=False)
class SecuritySchemeDefinition:      # the declaration, in security.py
    id: int
    name: str
    location: str
    type: str
    display_name / description: ScalarFacet[str] | None
    described_by: SecuritySchemeDescription | None
    settings: SecuritySchemeSettings | None
    link: SecuritySchemeDefinition | None   # the target of an `!include`
    link_uri: str | None                    # filled by fragments.py, see below
    annotations: dict[str, DomainExtension]
    key_pos / value_pos


@dataclass(slots=True, eq=False)
class SecuritySchemeDescription:     # `describedBy:`
    id, location
    headers / query_parameters: dict[str, Property]
    query_string: BaseShape | None
    responses: dict[str, Response]
    annotations, value_pos


@dataclass(slots=True, eq=False)
class SecurityScheme:                # a reference from `securedBy:`
    id, name, location
    definition: SecuritySchemeDefinition | None   # None until P5 binds it
    params: dict[str, Node]                       # undigested until P5
    compiled_params: list[str] | None             # what the settings made of them
    is_null: bool
    value_pos
```

`describedBy` reuses the *same* decoders as an operation: `headers` and
`queryParameters` are properties declarations, `queryString` is a type, and
`responses` is a response map. There is no second implementation of any of them,
and the shapes it produces go into `fragment_typedefs` like everything else, so
they are resolved, unwrapped and validated by the normal passes.

**`SecurityScheme` lives in `parser/directives.py`, not here.** It is the
directive reference promoted: applying a trait or a resource type produces a
merged tree and leaves nothing behind on the reference, but applying a scheme
produces a *binding*, and a binding needs somewhere to live. Putting it beside
`DirectiveRef` follows the same rule that put the three references there
([02](02-architecture.md) § 3), and it keeps `source_decode.py` — which builds
these during stage 2 — from importing the module that resolves them.

**The `!include` is followed in `fragments.py`**, as it is for a trait or a
resource type: `link_uri` is recorded here and `link` filled in there, because
following it means parsing a fragment.

### A2. The six scheme types

Per spec § Security Scheme Types, `type:` must be one of:

| `type:` | Settings it accepts | What it then requires |
|---------|--------------------|-----------------------|
| `OAuth 1.0` | `requestTokenUri`, `authorizationUri`, `tokenCredentialsUri`, `signatures` | all three URIs; `signatures` ⊆ {`HMAC-SHA1`,`RSA-SHA1`,`PLAINTEXT`} |
| `OAuth 2.0` | `authorizationUri`, `accessTokenUri`, `authorizationGrants`, `scopes` | `accessTokenUri`; `authorizationUri` iff a grant is `authorization_code` or `implicit` |
| `Basic Authentication` | none | — |
| `Digest Authentication` | none | — |
| `Pass Through` | none (values come from `describedBy`) | — |
| `x-<other>` | none | — |

`authorizationGrants` values are the four RFC 6749 names or **any absolute URI**
(spec allows extension grants such as
`urn:ietf:params:oauth:grant-type:saml2-bearer`).

An unknown `type:` that does not start with `x-` is an error. Settings keys that
the declared type does not define are an error, so `type: Basic Authentication`
with an `accessTokenUri` is caught rather than silently ignored — and so is a
`settings:` block on a type that has none at all.

**One `SecuritySchemeSettings` class, not six.** An earlier draft of this section
gave each type a class. But the two things that differ between them are *which
keys the type accepts* and *what it then requires*, and the first is a table
(`SCHEME_TYPES`) while the second is one function (`_validate_settings`). Six
near-empty classes would put each type name in two places and let the two
disagree; the table is the single place a type is named.

### A3. `securedBy:` and the null scheme

```yaml
securedBy: [null, oauth_2_0]
```

`null` in the list means "may also be called with no scheme" (spec § Applying
Security Schemes). It parses to a `SecurityScheme` whose `definition` is a
pre-built null definition — so downstream code never special-cases `None` in the
list, it just sees a scheme of type `null`.

### A4. Inheritance of `securedBy`

Three levels, each overriding the one above (spec § Applying Security Schemes):
API root → resource → method.

The parser needs to distinguish "declared nothing" from "declared `[null]`", so
every level carries an `explicit_secured_by` flag set during stage-1 decode. The
resolution in P5:

```
for each endpoint:
    if endpoint.explicit_secured_by:
        for each operation without explicit_secured_by:
            operation.secured_by = endpoint.secured_by     # replace, not append
    # neither explicit → both already hold the API global, set at decode time
```

Operations with their own `securedBy` are never touched — which is what makes
`securedBy: [null]` on a method correctly *remove* inherited security rather than
adding to it.

Resource-level schemes do **not** propagate to nested resources (spec: "Security
schemes applied to a resource MUST NOT incorporate nested resources").

### A5. Per-application parameters

```yaml
securedBy: [oauth_2_0: {scopes: [ADMINISTRATOR]}]
```

Spec § Applying Security Schemes permits custom parameters at the point of
inclusion; the list of valid ones is "specified by the security scheme type".
Only OAuth 2.0 defines any — `scopes`, which must be a subset of the declared
ones — so this is a check on the settings' type rather than a capability
protocol. An earlier draft of this section proposed an `OperationParamsApplier`
protocol implemented by one class out of six; with the six collapsed into one
(§ A2), the protocol has nothing left to dispatch on.

The result is stored on `SecurityScheme.compiled_params`, leaving the shared
definition untouched — the same scheme applied to two operations with different
scopes must not have the two interfere.

A `scopes` override on a non-OAuth-2.0 scheme is an error rather than a silent
no-op: the author believed it did something.

When the definition came in via `!include`, the settings live on the linked
definition; resolution follows `link` before looking at them.

### A6. Where a scheme name resolves

**Against the API**, and this is the one place security differs from traits and
resource types, which resolve lexically against the document that wrote the
reference ([04](04-fragments-and-namespaces.md) § 4).

Only a `Library` and an `APIFragment` declare `securitySchemes:`. A `securedBy:`
can be written in an API root, a resource, a method, a trait body or a resource
type body — and of those, the last two live in fragments that have no
`securitySchemes:` of their own and therefore no lexical namespace that could
hold one. A lexical lookup from a trait fragment would fail for every scheme the
API declares. go-raml resolves against the API for the same reason; fastRAML
matches it.

The API's own `uses:` still applies, so `securedBy: [lib.oauth]` reaches a
library's scheme by the ordinary qualified-name rule.

---

## Part B — Annotations (domain extensions)

### B1. Model

```python
class DomainExtension:
    __slots__ = (
        "id",
        "name",
        "value",  # value: DataNode
        "defined_by",  # BaseShape — the annotation type, filled by P8
        "location",
        "key_pos",
        "value_pos",
        "anchor",  # ReferenceResolver — the scope `name` resolves in
        "target",  # DomainLocation — where it was applied (§ B5)
    )
```

The internal name "domain extension" comes from AMF (and go-raml) and is kept
because "annotation" collides with Python's own vocabulary in a codebase that is
heavily typed.

### B2. Declaration

`annotationTypes:` is decoded by the *same* function as `types:`, with
`is_annotation=True`. Consequences, all correct per spec § Declaring Annotation
Types:

- an annotation type is a full type declaration and may use any facet;
- it may extend a data type;
- if it declares neither `type:` nor `properties:` its type is `string`
  (which falls out of the default-type inference rules);
- it is registered in `fragment_annotations[location]` rather than
  `fragment_types[location]`, so it cannot be used where a data type is expected —
  spec: "annotation types themselves can neither be extended nor used anywhere
  data types can be used".

Shapes created under `annotationTypes:` are flagged `is_annotation_type=True`, so
the expression builder routes their internal references through
`reference_annotation_type` (which falls back to `types` — see
[04](04-fragments-and-namespaces.md) § 3.1).

### B3. Application

Any mapping key of the form `(name)` is an annotation application. It is
recognised in every decoder that has a `default:` branch — API root, library root,
resource, method, response, type declaration, example, security scheme,
documentation item, and inside the annotated-scalar form. One helper:

```python
def unmarshal_domain_extension(raml, location, key_node, value_node) -> DomainExtension
```

It strips the parentheses (an empty name is an error), converts the value to a
`DataNode` (so nested positions survive), captures the current `ParseCtx` as the
anchor, appends the extension to `Raml.domain_extensions`, and returns it for the
caller to attach.

The flat `Raml.domain_extensions` list is the reason P8 and the annotation part of
P10 are single loops rather than a model traversal.

### B4. Resolution and validation

P8 binds each extension's `name` to its declaration via the captured anchor
(qualified `lib.name` works through the anchor's `uses:`). An unresolvable name is
an error — spec: "All annotations used in an API specification MUST be declared in
its annotationTypes node."

`resolve_domain_extensions` runs **unconditionally**, between P7 and P9: an
undeclared annotation is malformed input whether or not the caller asked to
unwrap or validate. It fills `defined_by` and nothing else; errors accumulate.
An extension whose `anchor` is `None` — built outside a fragment decode — falls
back to `Raml.resolver_at(location)`, exactly as P7 does for a shape.

P10 validates each extension's value against the resolved annotation type's shape,
after that shape has been unwrapped. Because unwrap replaces shape objects, P9
re-binds `defined_by` to the unwrapped instance; skipping that step silently
validates against the un-flattened declaration and misses inherited constraints.

### B5. `allowedTargets` — implemented here, unlike go-raml

go-raml parses `allowedTargets` and then ignores it. fastRAML enforces it, because
"processors MAY ignore annotations" is not licence to accept an annotation the
author explicitly restricted.

Implementation: the site rides on the `ParseCtx` stack, in `fastraml/domains.py`.
`unmarshal_domain_extension` reads `current_ctx().target` the same way it reads
the anchor, and a decoder that establishes a narrower site wraps itself in
`Raml.target_scope(...)` — a context manager, so a decode that raises
mid-construct cannot leave its site behind for the next annotation.

An explicit parameter was the obvious alternative and is wrong twice over. The
annotated-scalar form (`minLength: {value: 10, (a): x}`) is built by
`make_scalar_facet`, which has some four dozen call sites across `types/` and
`parser/` that would each have to thread a value through. And the two subtleties
below need an answer that the *decode* site does not have: an annotation inside a
trait body records where the trait was materialised, which is a later pass
entirely.

The enum is a leaf module of its own because `registry.py` carries it on
`ParseCtx` and `parser/annotations.py` reads it; either owning it would invert a
layering direction ([02](02-architecture.md) § 2).

```python
class DomainLocation(StrEnum):
    API = "API"
    DOCUMENTATION_ITEM = "DocumentationItem"
    RESOURCE = "Resource"
    METHOD = "Method"
    RESPONSE = "Response"
    REQUEST_BODY = "RequestBody"
    RESPONSE_BODY = "ResponseBody"
    TYPE_DECLARATION = "TypeDeclaration"
    EXAMPLE = "Example"
    RESOURCE_TYPE = "ResourceType"
    TRAIT = "Trait"
    SECURITY_SCHEME = "SecurityScheme"
    SECURITY_SCHEME_SETTINGS = "SecuritySchemeSettings"
    ANNOTATION_TYPE = "AnnotationType"
    LIBRARY = "Library"
    OVERLAY = "Overlay"
    EXTENSION = "Extension"
```

`allowedTargets` accepts a single string or a sequence; each value must be one of
the above, and a value that is not is `unknown annotation target`, positioned at
that entry rather than at the `allowedTargets` key — in a sequence of six the key
says nothing about which one is wrong. In P10, if the annotation type declares
targets and the application's site is not among them, the diagnostic is
`annotation not allowed at this target` with both the site and the allowed list.

**Absent and empty are different**, and `BaseShape.allowed_targets` is
`list[DomainLocation] | None` so they stay that way: absent means any target is
allowed, empty means none is. Collapsing them would silently permit every
application of an annotation whose author allowed none.

Three subtleties:

- A facet's annotated-scalar form has no target of its own — the spec's
  vocabulary has no member for a facet — so it records the **enclosing
  declaration's** site, which is what falls out of the ctx stack without a rule.
  An annotation on `minLength:` inside a type is annotating that type.

- An annotation applied inside a **trait or resource type** ends up on the
  operations that use it. Spec § Annotations says such annotations "are also
  applied to the resource type, resource, or method that inherits" — so the site
  recorded is the *materialised* one (Method/Resource), not Trait/ResourceType,
  for annotations *inside* the template body. An annotation on the template
  declaration itself records Trait/ResourceType.
- `RequestBody` vs `ResponseBody` vs `TypeDeclaration` are distinguished by which
  decoder creates the extension, not by inspecting the shape afterwards — and
  `body:` has **two spellings**, which decide between them:

  ```yaml
  body:                     # RequestBody / ResponseBody: the media-type node
    application/json:       #   is what the table above calls "the body node"
      (annotation): here

  body:                     # TypeDeclaration: with no media-type key the body
    type: User[]            #   *is* the type declaration
    (annotation): here
  ```

  The spec's table defines `RequestBody` as "the body node of a method", which
  in the media-type spelling is the node one level down. Both readings are
  pinned by fixtures — `Annotations/target-locations/valid-request-body.raml`
  and `valid-response-body.raml` for the first, `Annotations/complex-01/valid.raml`
  for the second — and getting it wrong regresses them in opposite directions.

### B6. Annotations and inheritance

Spec § Annotations: "Annotations applied to a data type are not inherited when
that data type is inherited." So `BaseShape.inherit` does **not** merge
`annotations` — deliberately, and with a comment saying so, because every other
facet on the base *is* merged and the omission looks like a bug otherwise.
The information remains reachable through `base.inherits`.

For traits: "if the inheriting resource type, resource, or method explicitly
applies an annotation of a given type, then this annotation overrides all
applications of that annotation type which would otherwise have been inherited."
This falls out of the structural merge for free — the annotation key exists in the
target, so pass 1 keeps the target's value and never grafts the source's.
