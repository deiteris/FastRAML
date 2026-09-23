# 08 - Templates and endpoints (the two-stage build)

This document owns resource types, traits, structural merging, endpoint
materialization, provenance, template variables, and URI parameters. The
pipeline is P4: build source IR, apply resource types and traits, materialize
the final endpoint tree; P6 then propagates URI parameters. See [02](02-architecture.md)
sections 1 and 6.

## 1. Merge contract

Traits and resource types merge YAML declaration trees, not typed endpoint
objects. The higher-priority target is the authored method or resource; the
lower-priority source is a trait or resource type.

- A missing side yields the other side. Grafting a source records its provenance.
- Different node kinds and scalars keep the target unchanged.
- Mappings recurse. Their order is target keys in target order, followed by
  source-only keys in source order.
- Sequences are a target-first union. Equality compares scalar tag and value,
  or composite children in order; positions are ignored.
- `example`, `examples`, and `default` are opaque user-data facets. A target
  value replaces a source value wholesale rather than recursively merging data.

`merge_structural` never mutates either input. It allocates new containers but
reuses child nodes, preserving node identity for provenance (architecture
invariants I9 and I10). Code: `parser/structural_merge.py`. Tests:
`tests/unit/test_structural_merge.py`.

## 2. Two-stage endpoint build

### 2.1 Stage 1: source IR

Stage 1 builds `SourceEndPoint` and `SourceOperation`, retaining endpoint and
method bodies as YAML mappings. It consumes the three directive keys:

| Key | On | Result |
|---|---|---|
| `type` | resource | one resource-type `DirectiveRef` |
| `is` | resource or method | trait `DirectiveRef`s |
| `securedBy` | resource or method | security `DirectiveRef`s and explicitness |

HTTP-method and subresource keys recurse into source IR. Every other pair,
including all type-bearing declarations and annotations, remains in `body`.
The retained mapping is fresh, carries the original position, and is `None`
when no pairs remain. `scope` may be absent for programmatic IR construction;
normal P4 construction establishes the API scope.

### 2.2 Directive resolution and stage 2

P4 first constructs source IR for all root resources. It then recursively
applies resource types and traits to every source endpoint. Only after this
whole directive-resolution walk does it decode every final source endpoint into
`EndPoint`, `Operation`, `Request`, `Response`, and `Body` objects.

This order is required: a template can contribute a type-bearing subtree, and
stage 2 must register every resulting shape before P7 drains unresolved shapes.
Every stage-2 shape is registered in `fragment_typedefs`, which is how P9 and
P10 reach endpoint declarations.

Code: `parser/source_ir.py`, `parser/endpoint_build.py`, and
`parser/source_decode.py`. Tests: `tests/unit/test_source_ir.py` and
`tests/unit/test_endpoints.py`.

## 3. Applying directives

### 3.1 Resource types

Applying a resource type follows this order:

1. Resolve the reference in the namespace where its `type` was authored.
2. For an included definition, compile the linked fragment under the linked
   fragment's location and anchor.
3. Add `resourcePath` and `resourcePathName` to the supplied parameters.
4. Drop optional methods such as `post?` unless the target resource already
   declares `post`.
5. Check supplied parameters against all declared variables, then require only
   variables reachable from the filtered tree.
6. Substitute parameters and record caller provenance.
7. Decode the compiled tree to source IR under the resource type's anchor.
8. Apply a chained resource type to that compiled source before merging it into
   the original endpoint. A visited-name set stops cycles.

The merge preserves the target's endpoint and method declarations. Resource
type traits move to `rt_traits`, preserving their lower priority. A resource
type's `securedBy` refs are appended to the target IR list, but its
`explicit_secured_by` flag is not propagated. Consequently, when the target
resource has no explicit `securedBy`, stage 2 selects API-global security and
does not materialize those appended refs. This is current behavior and has no
focused unit test.

Code: `parser/resourcetypes.py`; the declaration decode, the lexical lookup and
the parameter check it shares with traits are in `parser/templates.py`
(`TemplateDefinition`). Tests: `tests/unit/test_resourcetypes.py`.

### 3.2 Traits

For every operation, traits are considered in this exact order:

1. Method `is`.
2. Resource `is`.
3. Resource type method `is`.
4. Resource type resource `is`.

The first occurrence of a trait name wins; each surviving trait is applied once.
Each application resolves its definition lexically, injects `resourcePath`,
`resourcePathName`, and `methodName`, checks parameters in both directions,
substitutes, and merges the compiled body beneath the operation body.

`resourcePath` and `resourcePathName` are built once per resource. `methodName`
is built once per operation. These scalar nodes are read-only and are never
inserted by pointer into a compiled tree.

Code: `parser/traits.py`. Tests: `tests/unit/test_traits.py` and
`tests/unit/test_resourcetypes.py`.

### 3.3 `resourcePathName`

`resourcePathName` is the rightmost path segment that is neither empty nor a
URI-parameter-only segment. A trailing `{ext}` is ignored:

```
/users/{userId}/addresses  -> addresses
/users/{userId}            -> users
/bom/{itemId}{ext}         -> bom
```

Code: `parser/uritemplates.py`.

## 4. Provenance after merge

Merged YAML can contain static template content, caller-supplied parameter
values, and authored endpoint content. These may resolve names in different
namespaces. P4 records that information in a sparse identity map:

```python
ProvenanceOverlay = dict[Node, ParseCtx]
```

The key is the `Node` object itself. `Node` must retain identity equality and
hashing; do not key the map by `id(node)`.

### 4.1 Writers

`compile_source_provenance` leaves static template nodes unmarked. It marks a
caller-supplied complex value at its replacement root, and marks a copied scalar
that received substitution, with the caller scope.

`mark_graft` marks every node in a grafted source subtree with the source scope.
Both writers are set-if-absent: an existing mark is a more-specific scope
boundary, so graft marking neither overwrites it nor descends below it.

### 4.2 Readers

Stage 2 activates one source unit's overlay, selects a base body scope, and
pushes a marked facet-value scope when present. `Raml.scope_for` gives a shape
the most-specific scope: a marked `type` or `schema` value wins over its
containing mapping. `Raml.location_of` uses the marked scope's anchor location
for diagnostics. That namespace location is not necessarily the node's authored
location.

Therefore a shape may legitimately have different `location` and anchor
locations: static template structure is located at its declaration, while a
substituted type name resolves in the caller namespace.

One `BaseShape` has one anchor. The remaining granularity limit is therefore
inside one merged type declaration whose name-bearing content requires
incompatible scopes. Separate media-type body entries are separate `Body` and
`BaseShape` objects and may retain different provenance. No direct regression
test currently isolates this one-shape boundary.

Code: `parser/templates.py`, `parser/structural_merge.py`, `registry.py`, and
`types/shape.py`. Tests: `tests/unit/test_templates.py`,
`tests/unit/test_structural_merge.py`, and `tests/unit/test_traits.py`.

## 5. Template variables

Template bodies are indexed once at declaration time:

```python
declared_variables: set[str]
node_variable_index: dict[Node, list[VariableInfo]]
```

The index is keyed by node identity. Optional-method filtering removes
subtrees after indexing, so positional indexes are invalid. Required variables
are collected by walking the filtered tree and looking up its surviving nodes.
The traversal is iterative.

Each `VariableInfo` stores the variable name, its exact `<<...>>` substring,
and ordered actions. Parsing accepts `<<name | !action | !action>>`; the first
nonempty part is the name, later nonempty parts must be known `!` actions, and
an unclosed placeholder is an error.

The supported actions are `!uppercase`, `!lowercase`, `!uppercamelcase`,
`!lowercamelcase`, `!upperunderscorecase`, `!lowerunderscorecase`,
`!upperhyphencase`, `!lowerhyphencase`, `!singularize`, and `!pluralize`.
Pluralization uses a lazy `pluralizer` instance with the `medium`,
`memorandum`, `vortex`, and `sms` irregular registrations. The parity table in
`tests/unit/data/pluralize_parity.tsv` guards those two actions.

Parameters are not substituted into `uses`, `extends`, or `!include` locations:
`uses` is removed before a template body is captured and includes resolve before
substitution.

Code: `parser/templates.py`. Tests: `tests/unit/test_templates.py`.

## 6. Endpoint construction

### 6.1 URIs and responses

`full_uri` concatenates relative resource URIs and never prepends `baseUri`.
The endpoint index rejects duplicate unexpanded template text, while distinct
template variable names coexist. Response keys must be concrete strings from
`100` through `599`; YAML numeric and quoted spellings normalize to the same
string key. `protocols` accepts only HTTP or HTTPS, case-insensitively, and
stores uppercase values.

The API root parses `baseUri` with the same RFC 6570 level-1/level-2 template
parser, then rejects invalid URI characters, malformed percent escapes, and
malformed schemes in the surrounding text. Relative references are accepted,
and a non-ASCII IRI character must be percent-encoded.

### 6.2 URI parameters

P6 parses RFC 6570 level-1 and level-2 expressions in every relative URI.
Undeclared variables receive synthesized required `string` parameters. A local
`uriParameters` declaration whose name is absent from that resource's own URI
is an error. `default`, `enum`, `example`, and `examples` values for URI
parameters may not contain `/`.

Each endpoint finally exposes ancestor parameters first and its own parameters
afterward, in path order. An inherited parameter is the same `Parameter` object
as the ancestor declaration; a synthesized parameter belongs only to the
endpoint whose URI introduced it. `baseUriParameters` use URI binding and every
declared name must occur in `baseUri`; no declaration is synthesized for an
undeclared base URI variable.

### 6.3 Bodies

A `body` mapping whose keys all contain `/` is a media-type map. Otherwise it
is one declaration instantiated separately for every API default media type;
without a default media type this spelling is an error. A mapping that mixes
media-type keys and type facets is an error. API default media types must use
valid RFC 6838 `type/subtype` syntax.

### 6.4 Query strings

`queryString` and `queryParameters` are mutually exclusive on one request.
P10 checks the flattened `queryString` type for every operation and security
scheme description: it may admit scalar or object values, but no union member
may be an array. Array-typed properties inside an object remain valid.

Code: `parser/fragments.py`, `parser/endpoint_build.py`,
`parser/source_decode.py`, and `parser/uritemplates.py`. Tests:
`tests/unit/test_fragments.py`, `tests/unit/test_endpoints.py`, and
`tests/unit/test_uritemplates.py`.
