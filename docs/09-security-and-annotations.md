# 09 — Security schemes and annotations

Two features that share a shape: both are declared centrally, applied by
reference, and validated against their declaration.

---

## Part A — Security schemes

### A1. Model

```python
class SecuritySchemeDefinition:  # the declaration
    __slots__ = (
        "id",
        "name",
        "type",
        "display_name",
        "description",
        "described_by",  # SecuritySchemeDescription | None
        "settings",  # SecuritySchemeSettings | None
        "link",  # SecuritySchemeFragment | None
        "annotations",
        "location",
        "key_pos",
        "value_pos",
        "_raml",
    )


class SecuritySchemeDescription:  # `describedBy:`
    __slots__ = ("headers", "query_parameters", "query_string", "responses", "annotations", "location", "position")


class SecurityScheme:  # a reference from `securedBy:`
    __slots__ = ("id", "name", "params", "definition", "compiled_params", "location", "value_pos", "_raml")
```

`describedBy` reuses the *same* decoders as an operation: `headers` and
`queryParameters` are properties declarations, `queryString` is a type, and
`responses` is a response map. There is no second implementation of any of them,
and the shapes it produces go into `fragment_typedefs` like everything else, so
they are resolved, unwrapped and validated by the normal passes.

### A2. The six scheme types

Per spec § Security Scheme Types, `type:` must be one of:

| `type:` | Settings class | Required settings |
|---------|----------------|-------------------|
| `OAuth 1.0` | `OAuth1Settings` | `requestTokenUri`, `authorizationUri`, `tokenCredentialsUri`; optional `signatures` ⊆ {`HMAC-SHA1`,`RSA-SHA1`,`PLAINTEXT`} |
| `OAuth 2.0` | `OAuth2Settings` | `accessTokenUri`, `authorizationGrants`; `authorizationUri` required iff a grant is `authorization_code` or `implicit`; optional `scopes` |
| `Basic Authentication` | `BasicSettings` | none |
| `Digest Authentication` | `DigestSettings` | none |
| `Pass Through` | `PassThroughSettings` | none (values come from `describedBy`) |
| `x-<other>` | `CustomSettings` | none; settings kept as raw data |

`authorizationGrants` values are the four RFC 6749 names or **any absolute URI**
(spec allows extension grants such as
`urn:ietf:params:oauth:grant-type:saml2-bearer`).

An unknown `type:` that does not start with `x-` is an error. Settings keys that
the declared type does not define are an error, so `type: Basic Authentication`
with an `accessTokenUri` is caught rather than silently ignored.

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
pyRAML implements this as an optional capability on the settings object:

```python
class OperationParamsApplier(Protocol):
    def apply_operation_params(self, params: dict[str, Any]) -> Any: ...
```

Only `OAuth2Settings` implements it, narrowing `scopes` (the supplied scopes must
be a subset of the declared ones). The result is stored on
`SecurityScheme.compiled_params`, leaving the shared definition untouched — the
same scheme applied to two operations with different scopes must not have the two
interfere.

A `scopes` override on a non-OAuth-2.0 scheme is an error rather than a silent
no-op.

When the definition came in via `!include`, the settings live on the linked
fragment; resolution follows the link before looking for the applier.

---

## Part B — Annotations (domain extensions)

### B1. Model

```python
class DomainExtension:
    __slots__ = (
        "id",
        "name",
        "value",  # value: DataNode
        "defined_by",  # BaseShape — the annotation type
        "location",
        "key_pos",
        "value_pos",
        "anchor",
        "_raml",
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

P10 validates each extension's value against the resolved annotation type's shape,
after that shape has been unwrapped. Because unwrap replaces shape objects, P9
re-binds `defined_by` to the unwrapped instance; skipping that step silently
validates against the un-flattened declaration and misses inherited constraints.

### B5. `allowedTargets` — implemented here, unlike the reference

go-raml parses `allowedTargets` and then ignores it. pyRAML enforces it, because
"processors MAY ignore annotations" is not licence to accept an annotation the
author explicitly restricted.

Implementation: each application site passes a `DomainLocation` when it creates
the extension.

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
the above. In P10, if the annotation type declares targets and the application's
site is not among them, the diagnostic is
`annotation not allowed at this target` with both the site and the allowed list.

Two subtleties:

- An annotation applied inside a **trait or resource type** ends up on the
  operations that use it. Spec § Annotations says such annotations "are also
  applied to the resource type, resource, or method that inherits" — so the site
  recorded is the *materialised* one (Method/Resource), not Trait/ResourceType,
  for annotations *inside* the template body. An annotation on the template
  declaration itself records Trait/ResourceType.
- `RequestBody` vs `ResponseBody` vs `TypeDeclaration` are distinguished by which
  decoder creates the extension, not by inspecting the shape afterwards.

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
