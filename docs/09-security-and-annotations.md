# 09 - Security schemes and annotations

This document owns security-scheme decoding and P5 application, plus domain
extension decoding, P8 binding, and P10 validation.

## Part A - Security schemes

### A1. Model and decoding

`SecuritySchemeDefinition` is a centrally declared scheme. It holds its name,
type, display and description facets, optional `describedBy`, optional settings,
include link information, annotations, and positions.

`SecuritySchemeDescription` reuses endpoint decoders:

```python
headers: dict[str, Parameter]
query_parameters: dict[str, Parameter]
query_string: BaseShape | None
responses: dict[str, Response]
```

Its shapes are registered in `fragment_typedefs`, so ordinary resolution,
unwrapping, and validation reach them.

`SecurityScheme` is the model-side form of one `securedBy` entry. It holds the
name, original parameter nodes, an optional bound definition, optional compiled
parameters, the null marker, and its position. It lives in `parser/directives.py`
with `DirectiveRef`; stage 2 can construct it without importing P5 resolution.

Includes are followed by `fragments.py`. The declaration records `link_uri`; the
fragment decoder fills `link`.

### A2. Scheme types and settings

The accepted named scheme types are:

| Type | Accepted settings | Required settings |
|---|---|---|
| `OAuth 1.0` | `requestTokenUri`, `authorizationUri`, `tokenCredentialsUri`, `signatures` | all three URIs |
| `OAuth 2.0` | `authorizationUri`, `accessTokenUri`, `authorizationGrants`, `scopes` | `accessTokenUri`; `authorizationUri` for `authorization_code` or `implicit` |
| `Basic Authentication` | none | none |
| `Digest Authentication` | none | none |
| `Pass Through` | none | none |
| `x-<other>` | none | none |

OAuth 1.0 signatures are `HMAC-SHA1`, `RSA-SHA1`, or `PLAINTEXT`. OAuth 2.0
grants are the four RFC 6749 names or an absolute URI. Unknown non-`x-` types,
settings on a setting-less type, and settings not accepted by the declared type
are errors.

One `SecuritySchemeSettings` model holds all scalar and sequence settings. The
decoder selects the allowed and required fields from the declared scheme type.

Code: `parser/security.py`. Tests: `tests/unit/test_security.py`.

### A3. `securedBy` and null

`securedBy` accepts scheme references, with `null` meaning the endpoint may be
called without a scheme. Stage 1 represents this as a null `DirectiveRef`; stage
2 creates `SecurityScheme(is_null=True, definition=None)`; P5 binds it to a new
`SecuritySchemeDefinition(type='null')`. Consumers therefore receive a real
scheme definition after P5 rather than a `None` sentinel.

### A4. Inheritance

Security defaults select one list at each level: API root, then resource, then
method. An explicit resource list replaces the API default for its own methods;
an explicit method list replaces either. A resource list does not propagate to
nested resources.

Only source resources and source operations carry `explicit_secured_by`. The
API root separately decodes `global_secured_by`; stage 2 shares that list when a
resource or method is not explicit. Explicit empty and `[null]` lists are
therefore distinguishable from omission.

### A5. Per-application parameters

P5 interprets `scopes` on OAuth 2.0 applications as a narrowing subset of the
definition's declared scopes and stores the result on the `SecurityScheme`
reference. The definition remains unchanged. `scopes` on another scheme is an
error.

Current limitation: P5 does not diagnose or compile application parameter keys
other than `scopes`; it retains and ignores them for OAuth 2.0 and other schemes.
There is no focused unit test for those ignored keys.

For an included declaration, P5 follows `link` before reading settings.

### A6. Scheme-name resolution

P5 resolves every scheme name against the API resolver, not the lexical scope
where `securedBy` was authored. This lets references inside applied traits and
resource types resolve API-declared schemes. API `uses` entries remain available,
so qualified library scheme names work normally.

In a target tree, a scheme reference an Overlay or Extension wrote resolves
against that document's resolver instead. The document's view of the target
tree includes the schemes it declared ([19](19-overlays-and-extensions.md)
§ 5.2).

Code: `parser/directives.py`, `parser/security.py`, and
`parser/source_decode.py`. Tests: `tests/unit/test_security.py` and
`tests/unit/test_source_ir.py`.

## Part B - Annotations (domain extensions)

### B1. Model and application

A `DomainExtension` records an annotation application:

```python
id: int
name: str
value: DataNode
defined_by: BaseShape | None
location: str
key_pos: Position
value_pos: Position
anchor: ReferenceResolver | None
target: DomainLocation
```

Any nonempty mapping key of the form `(name)` is an application. The decoder
converts its value to `DataNode`, captures the current anchor and target,
registers it in `Raml.domain_extensions`, and returns it for the owning model
node. The flat list lets P8 and P10 process every application without a model
traversal.

### B2. Annotation declarations and names

`annotationTypes` uses the ordinary shape decoder and stores declarations in
`fragment_annotations`, separately from ordinary `types`. An annotation type is
a full shape declaration and defaults to `string` when no type-bearing facet
settles its kind.

Ordinary data-type expressions resolve only ordinary `types`, so an annotation
type cannot be used as a data type. Within an annotation-type declaration,
references resolve annotation types first and then fall back to ordinary data
types. The latter behavior is tested; a direct annotation-type-to-annotation-type
reference test is still absent.

### B3. Binding and value validation

P8 runs unconditionally after P7. It resolves every extension name through its
captured anchor, or through `resolver_at(location)` when no anchor was captured,
and stores the resulting declaration in `defined_by`. Unresolved applications
accumulate as diagnostics.

P10 validates an extension value against its declared shape. If P9 ran,
`unwrap_shapes` rebinds `defined_by` to any replacement shape. If validation is
requested without P9, P10 unwraps a private cached copy before validation.

Code: `parser/annotations.py`, `types/unwrap.py`, and `types/validate.py`.
Tests: `tests/unit/test_domain_extensions.py`.

### B4. `allowedTargets`

`allowedTargets` accepts one `DomainLocation` string or a sequence. An unknown
entry is reported at that entry. `None` means the facet was absent and permits
every target; an empty list permits none. P10 reports `annotation not allowed at
this target` when an application site is excluded.

The target is carried by `ParseCtx`. A decoder that establishes a narrower
annotation site uses `Raml.target_scope`, which preserves the anchor and restores
the previous target afterward. An annotated scalar does not establish an
independent target and therefore inherits its enclosing declaration site.

The parser currently establishes these sites: API, Library, Overlay, Extension,
documentation item, type declaration, annotation type, example, resource,
method, response, request body, response body, security scheme, and security
scheme settings. An application at the root of an Overlay or Extension targets
that document kind, not API ([19](19-overlays-and-extensions.md) § 5.4).

Template body annotations are decoded only when the template is materialized,
so they receive `Method` or `Resource`. The parser does not establish `Trait` or
`ResourceType` while decoding template definitions. Declaration-time scalar
facets such as `usage` therefore inherit the enclosing fragment target. There is
no focused unit test for either template-body targets or template-definition
targets.

For a media-type body spelling, annotations inside each media-type declaration
target `RequestBody` or `ResponseBody`. For a body without media-type keys, its
contents are a type declaration and annotations target `TypeDeclaration`.

Code: `domains.py`, `registry.py`, `parser/source_decode.py`,
`parser/security.py`, and `types/shape.py`. Tests:
`tests/unit/test_domain_extensions.py`, `tests/unit/test_endpoints.py`, and
`tests/unit/test_security.py`.

### B5. Inheritance

Data-type annotations are not merged by inheritance. They remain available on
the inherited declaration through `inherits`. Template-derived annotation keys
use structural merge: an annotation explicitly present in the target overrides
the source application of that annotation type.

Code: `types/inherit.py` and `parser/structural_merge.py`.
