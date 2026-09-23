# 04 - Fragments and namespaces

This document defines fragment kinds, lexical name resolution, and the cache
rule that makes typed fragments reusable: a fragment is decoded in its own
namespace, never its includer's namespace.

## 1. Fragment kinds

| Kind | Root form | Local declarations |
|---|---|---|
| `API` | mapping | API declarations and resources |
| `Library` | mapping | types, annotation types, traits, resource types, security schemes |
| `DataType` | type declaration | one type |
| `AnnotationTypeDeclaration` | type declaration | one annotation type |
| `NamedExample` | mapping | named examples |
| `DocumentationItem` | `{title, content}` | one documentation item |
| `ResourceType` | resource-type body | one resource type |
| `Trait` | trait body | one trait |
| `SecurityScheme` | security-scheme body | one security scheme |
| `Overlay`, `Extension` | mapping | none; merged into the root API ([19](19-overlays-and-extensions.md)) |

Every fragment has a URI `location`; it can be `file://` or, with an enabled
HTTP loader, `http(s)://`. Every supported fragment may have root-level `uses:`.
Library and API decoders read it with their root declarations. Other typed
fragment decoders remove it before decoding their kind-specific body.

## 2. Resolver capabilities

All supported fragments implement `ReferenceResolver`:

```python
class ReferenceResolver(Fragment, Protocol):
    def reference_type(self, name: str) -> BaseShape: ...
    def reference_annotation_type(self, name: str) -> BaseShape: ...
    def resource_type_definition(self, name: str) -> ResourceTypeDefinition: ...
    def trait_definition(self, name: str) -> TraitDefinition: ...
    def library_link(self, prefix: str) -> LibraryLink | None: ...
```

Only API and Library fragments implement `SecuritySchemeResolver`, because only
they declare security schemes. Resolver capabilities are checked structurally;
a fragment is not required to expose unsupported declaration kinds.

## 3. Name resolution

`resolve_reference()` resolves declarations in a fragment with a local table.
`resolve_library_reference()` resolves declarations in a typed fragment that has
no local table. Both apply these rules:

1. Split a qualified reference on its last dot.
2. An unqualified name resolves only in the local table. A typed fragment with
   no local table rejects it.
3. For a dotted name, first try the complete name in the local table. This
   permits declaration names containing dots.
4. Otherwise resolve the prefix in the fragment's `uses:` map, then resolve the
   suffix in that library.
5. A second library hop is not allowed. `files.file-type.File` is not a
   namespace chain; its prefix must be one direct `uses:` key.

Failed lookups distinguish `reference not found`, `library not found`, `library
not resolved`, and `invalid reference`; callers attach source location and
position to those errors.

Annotation-type resolution searches `annotationTypes` first and falls back to
ordinary `types`. This permits an annotation type declaration to extend a data
type.

## 4. Anchor scoping

`Raml` keeps a stack of `ParseCtx` values. A fragment decode pushes its own
context, whose `anchor` is that fragment's resolver. Every type-bearing or
reference-bearing entity captures the active context when it is created.

The captured anchor governs type references, template directive names, and
annotation type names. It is retained even when a source node is merged into an
endpoint authored by another file. A shape constructed outside fragment decoding
may fall back to `Raml.resolver_at(location)`; parsed shapes carry an anchor.

Consequences:

- A typed fragment can reference only declarations it owns or libraries it
  imports through its own `uses:` map.
- A trait or resource type declared inline in an API uses the API namespace.
- A trait or resource-type reference written inside a typed fragment resolves
  through that fragment's local declarations and imports.
- A resource-type reference written on an API endpoint resolves through the API
  declarations and imports.
- Template parameter values use the caller's context because their source text
  is written by the caller. Provenance overlay rules for merged endpoint nodes
  are defined in [08](08-templates-and-endpoints.md).

## 5. Fragment decoding

API decoding first collects global `mediaType`, `protocols`, and `securedBy`,
then decodes remaining declarations in source order. It retains resource source
nodes for endpoint construction.

Libraries decode their declarations and root `uses:` map. A DataType or
AnnotationTypeDeclaration fragment creates one shape named from its file base
name. A `.json` DataType target creates a JSON Schema shape. NamedExample and
DocumentationItem fragments decode their respective values. Trait, ResourceType,
and SecurityScheme fragments use the same definition builders as inline
declarations.

Every created shape is indexed in `Raml.fragment_typedefs` by authored location.
Unwrap and validation use this index rather than discovering declarations by
walking the model graph.

## 6. Fragment cache and `uses:` lifecycle

Fragments are cached by resolved URI. A fragment is registered before its body
is decoded, allowing mutually importing libraries to terminate as a cyclic model
graph. Its `uses:` entries are resolved after body decoding. Reference binding
waits until P7, so unresolved links during body decoding do not prevent mutual
imports.

`uses:` resolution accumulates failures across entries. It uses the same URI
rules as `!include`; see [03](03-yaml-and-io.md).
