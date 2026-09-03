# 04 — Fragments and namespaces

This document settles: what a fragment is, how names are looked up, and the rule
that makes both cacheable — **anchor scoping**.

## 1. Fragment kinds

| Kind | Root shape | Declares | Also allows |
|------|-----------|----------|-------------|
| `API` (`#%RAML 1.0`) | mapping | everything | `/resources` |
| `Library` | mapping | types, annotationTypes, resourceTypes, traits, securitySchemes | `usage`, `uses` |
| `DataType` | a single type declaration | one shape | `uses` |
| `AnnotationTypeDeclaration` | a single type declaration | one shape | `uses` — structurally identical to `DataType` |
| `NamedExample` | mapping of name → example | examples | `uses` |
| `DocumentationItem` | `{title, content}` | one doc item | `uses` |
| `ResourceType` | a resource-type body | one RT definition | `uses` |
| `Trait` | a trait body | one trait definition | `uses` |
| `SecurityScheme` | a security-scheme body | one SS definition | `uses` |
| `Overlay` / `Extension` | mapping + `extends` | v1.1 | |

Every non-API fragment may carry a root-level `uses:` (spec § Typed Fragments).
The decoder therefore does the same thing everywhere: **strip `uses:` from the
root mapping first, then hand the remainder to the kind-specific decoder.** That
is one shared helper (`filter_fragment_uses`), not nine copies.

## 2. Fragment classes

```python
class Fragment(Protocol):
    location: str  # file:// URI


class ReferenceResolver(Fragment, Protocol):
    def reference_type(self, name: str) -> BaseShape: ...
    def reference_annotation_type(self, name: str) -> BaseShape: ...
    def resource_type_definition(self, name: str) -> ResourceTypeDefinition: ...
    def trait_definition(self, name: str) -> TraitDefinition: ...


class SecuritySchemeResolver(Protocol):
    def security_scheme_definition(self, name: str) -> SecuritySchemeDefinition: ...
```

`ReferenceResolver` is implemented by *all* typed fragments, because all of them
can carry `uses:` and therefore all of them can resolve a qualified name.
`SecuritySchemeResolver` is only `Library` and `APIFragment`, since only those
declare schemes.

Capability is discovered by protocol/`isinstance` check, not by a fat base class:
a `NamedExample` genuinely has no traits, and modelling that as a method that
always raises is honest and cheap.

## 3. Name resolution

Two functions cover every lookup in the parser.

```python
def resolve_reference(local: dict[str, T] | None,
                      uses:  dict[str, LibraryLink] | None,
                      name:  str,
                      pick:  Callable[[Library, str], T | None]) -> T
```

1. Split on the **last** dot (`CutLast`). RAML type names may contain dots
   (the RDT `IDENTIFIER` production includes `.`), so `a.b.c` means library `a.b`,
   type `c` — splitting on the first dot would be wrong.
2. No dot → look up `local[name]`. Miss is an error.
3. Dot present → try `local[name]` first anyway (a fragment may legitimately
   declare a key containing a dot), then `uses[prefix].link` and `pick(lib, suffix)`.
4. Namespace chaining is **not** permitted: `files.file-type.File` fails. Spec
   § Applying Libraries: "processors MUST NOT allow any composition of namespaces
   using '.' across multiple libraries." Step 1's last-dot split combined with
   step 3's single library hop enforces this naturally — `files.file-type` is not
   a key in `uses`.

A miss raises `UnresolvedReferenceError(reason, name)` rather than returning a
diagnostic. The reason is one of `reference not found`, `library not found`,
`library not resolved`, `invalid reference`; the caller knows the location and
the position and turns those parts into a positioned `RamlError`. Splitting it
this way keeps the two resolvers free of location plumbing, and lets
`reference_annotation_type` catch a miss and retry against `types` without
manufacturing an error object on the way.

```python
def resolve_library_reference(uses, name, pick) -> T
```

The same, minus the local map: used by `DataType`, `NamedExample`,
`DocumentationItem`, `Trait`, `ResourceType` and `SecurityScheme` fragments, which
have no local declaration table of their own. An unqualified name there is an
error by construction — which is deviation **D4** made mechanical.

### 3.1 Annotation types fall back to types

`reference_annotation_type(name)` looks in `annotationTypes` first and falls back
to `types`. Spec § Declaring Annotation Types says an annotation type declaration
"has the same syntax as a data type declaration" and may extend a data type, so
`annotationTypes: {ConfigInstance: Config}` must find `Config` among the types.
go-raml does the same fallback.

## 4. Anchor scoping (`ParseCtx`)

### 4.1 The problem

A name written in a document must resolve in *that document's* namespace, even
though the object it produces may end up attached somewhere else entirely — a
trait's response body ends up on an operation in the API file, but the type name
inside it belongs to the trait fragment.

Storing `location` on the shape and looking the fragment up later is not enough,
because after the structural merge a single operation's tree contains nodes from
three different files.

### 4.2 The mechanism

```python
@dataclass(slots=True, frozen=True)
class ParseCtx:
    anchor: ReferenceResolver | None
```

`Raml` keeps a **stack** of these. Every fragment decoder pushes its own
`ParseCtx` on entry and pops on exit:

```python
def decode_library(self, text, uri):
    lib = Library(uri, self)
    self.push_ctx(ParseCtx(anchor=lib))  # self-referential: a library
    try:  # resolves names against itself
        lib.decode(compose(text, uri=uri))
    finally:
        self.pop_ctx()
```

Every construct that can contain a name **captures the top of the stack at
creation time**:

| Entity | Field | Governs |
|--------|-------|---------|
| `BaseShape` | `anchor` | unqualified and qualified type references inside it |
| `Trait` (the `is:` entry) | `anchor` | the trait *name* lookup |
| `ResourceType` (the `type:` entry) | `anchor` | the resource-type name lookup |
| `TraitDefinition` / `ResourceTypeDefinition` | `anchor` | names inside the template body |
| `DomainExtension` | `anchor` | the annotation-type name |

Resolution then uses the captured anchor, falling back to a location lookup only
for shapes built without a parse context (programmatic construction, tests).

### 4.3 Consequences of anchor scoping

**Typed fragments do not inherit the caller's scope.** When `traits: {paged:
!include traits/paged.raml}` is decoded, the trait fragment's decoder pushes the
*fragment's own* `ParseCtx`, not the API's. So:

```yaml
# traits/paged.raml
#%RAML 1.0 Trait
responses:
  200:
    body:
      application/json:
        type: PagedResult        # ✗ error — not declared in this fragment
```

```yaml
# traits/paged.raml
#%RAML 1.0 Trait
uses: { models: ../models.raml }
responses:
  200:
    body:
      application/json:
        type: models.PagedResult # ✓
```

Why this matters beyond purity: if a fragment could see its includer's namespace,
the *same file* would mean different things at different inclusion sites, and
`Raml.fragments` — one parse per file — would be wrong. The rule is what makes the
cache sound.

**Inline declarations follow the opposite rule.** A trait declared directly under
the API's `traits:` key is decoded with the API's `ParseCtx` on top, so it resolves
against the API's `types:`, both qualified and unqualified. Forward references
work because shape resolution is deferred to pass P7.

**Trait and resource-type *names* are lexically scoped too.** `is: [paged]`
written inside a `ResourceType` fragment resolves against that fragment's
`traits:`/`uses:` only — there is no fallback to the including API. To use an
API-level trait from inside a fragment, import it and qualify it:

```yaml
#%RAML 1.0 ResourceType
uses: { t: traits.raml }
get:
  is: [t.paged]
```

**Resource-type names on endpoints** resolve against the API's `resourceTypes:`
plus the API's `uses:`, since that `type:` text is written in the API.

## 5. What each decoder does

### 5.1 API and Library

Both decode a root mapping key by key. The API has a **pre-pass**
(`preprocess`) that extracts three global settings before anything else, because
later decoding depends on them:

| Key | Stored as | Needed by |
|-----|-----------|-----------|
| `mediaType` | `Raml.global_media_types` | body decoding, to know the default media type |
| `protocols` | `Raml.global_protocols` | operations |
| `securedBy` | `Raml.global_secured_by` | every operation without its own |

The remaining keys are decoded in document order. `types`/`schemas` and their
library equivalents are mutually exclusive and produce a positioned error when
both appear.

Type declarations go through `unmarshal_types(node, location, is_annotation)`
which, per name:

- rejects redefinition of a built-in type name (`string`, `object`, …);
- rejects a duplicate name in the same map;
- builds the shape;
- registers it in `fragment_types[location]` (or `fragment_annotations`), **and**
  appends it to `fragment_typedefs[location]`.

The `fragment_typedefs` list is the "everything declared in this file" index —
it also receives body shapes, headers, query parameters, query strings, URI
parameters and base-URI parameters, wherever they are created. Unwrap and
validation iterate *that* list, which is why they need no traversal of the model
graph at all. This is a real performance property, not bookkeeping: it turns
"walk every reachable shape" into "iterate a flat list".

Endpoints are **not** decoded here. `/foo` keys produce stage-1
`SourceEndPoint` IR appended to `api.source_endpoints`
(see [08](08-templates-and-endpoints.md)).

### 5.2 DataType

The whole remaining mapping (after `uses:` is stripped) *is* the type
declaration. A synthetic key node carrying the file's base name is fabricated so
the shape gets a sensible `name`, then the ordinary shape builder runs.

A `.json` file is instead wrapped into a synthetic `{type: "<raw json>"}` mapping
and takes the JSON-Schema path.

### 5.3 NamedExample

Each remaining key is an example name; each value goes through the shared example
builder. Used by `examples: !include examples/paging.raml`.

### 5.4 Trait / ResourceType / SecurityScheme fragments

Each strips `uses:` and delegates to the same definition builder used for the
inline form (`make_trait_definition(key_node=None, value_node=filtered, …)`).
One code path for `traits: {x: {...}}` and for `traits: {x: !include x.raml}`.

Note the deliberate asymmetry in when shapes get resolved: a SecurityScheme
fragment does **not** resolve its own shapes eagerly, because its `describedBy`
bodies get embedded into operations and must be resolved in the same global batch
(P7). Resolving them at fragment-decode time races the parent's `uses:`
resolution, which has not run yet. go-raml has a long comment about exactly this
bug; the ordering is preserved here.

## 6. Fragment cache lifecycle

```python
def parse_fragment(raml, uri: str, kind: FragmentKind) -> Fragment:
    if (cached := raml.get_fragment(uri)) is not None:
        return cached  # I3: decoded at most once
    text = load_fragment_text(raml, uri)
    check_fragment_kind(text, uri, kind)
    frag = make_fragment(raml, kind, uri)
    raml.put_fragment(uri, frag)  # BEFORE decoding — cycles resolve here
    raml.push_ctx(ParseCtx(anchor=frag))
    try:
        frag.decode(compose(text, uri=uri))  # the header line is kept, so line numbers hold
    finally:
        raml.pop_ctx()
    resolve_uses(raml, frag.uses, uri)  # recursive, after the body
    return frag
```

Two ordering details in this sequence control correctness:

- The fragment is registered **before** its body is decoded. `a.raml` →
  `b.raml` → `a.raml` therefore terminates, producing a cyclic object graph
  instead of infinite recursion.
- `uses:` is resolved **after** the body, in a separate stage. While the body is
  decoding, each `LibraryLink` has `link is None`. Nothing dereferences the link,
  because all name resolution is deferred to P7. Resolving `uses:` first would be
  simpler, but it breaks mutual imports.

`resolve_uses` accumulates: one unreadable library does not hide the state of the
others. A `uses:` value resolves by the same three rules as an `!include`
argument (`resolve_ref_uri`, [03](03-yaml-and-io.md) § 4.1).

The six fragments that declare nothing of their own — DataType, NamedExample,
DocumentationItem, Trait, ResourceType, SecurityScheme — resolve all four
reference kinds identically, through `uses:` alone, so that implementation is
written once and shared. This is not a capability base class: what a fragment
*can* do is still discovered by protocol check, and `Library` and `APIFragment`
override all four with the local-table form.
