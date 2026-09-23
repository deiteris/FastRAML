# 19 - Overlays and Extensions

This document owns Overlay and Extension documents: loading an `extends`
chain, the extension merge, overlay restrictions, namespaces across the chain,
and document provenance. Spec sections: Overlays and Extensions, Merging Rules.

Status: implemented for an entry document. The TCK's Overlay and Extension
fixtures are in the ratchet ([14](14-testing.md) § 2).

## 1. Terms

- An **Overlay** or **Extension** document starts with `#%RAML 1.0 Overlay` or
  `#%RAML 1.0 Extension`. Together they are *extension documents*.
- A document's **master** is the document its `extends` names: an API, or
  another extension document.
- The **chain** of an extension document is its sequence of masters, ending at
  one API document, the **root API**. Chain position 0 is the root API. Each
  extension document takes the next position in application order.
- The **target tree** is the node tree that results from applying every
  extension document in the chain to the root API.

An Overlay may add or change only the nodes listed in § 4. An Extension may add
or change anything the merge rules allow.

## 2. Loading the chain

An extension document's root is a mapping. It has the keys an API root allows,
plus `extends` and `usage`. `title` is optional, because the target tree inherits
the master's title. Every other key is decoded with the target tree and is
reported by the API decoder, located in the document that wrote it.

`extends` is required and holds a string. The value resolves like an `!include`
argument, relative to the document that contains it ([03](03-yaml-and-io.md)
§ 4.1). Its target must have an API, Overlay, or Extension header. Any other
header reports `unexpected fragment kind`.

Loading reads `extends` from the entry document's root, then follows each
master in turn to the root API. Each file is composed once (invariant I2). A
document that appears twice in its own chain reports `extends cycle`, with the
chain in `info`. The root API must have a `title` of its own, because the target
tree takes its title from there; see § 7.

A failure while loading a master is wrapped in a `resolve extends` frame at
each referring document's `extends` value, so the outermost frame is always in
the entry document. `parse_lenient` re-raises it: with no root API there is no
model to return.

`extends` is read through the same loader as `!include`, so the workspace
sandbox applies ([03](03-yaml-and-io.md) § 5). The default workspace root is the
entry document's directory. An entry document that extends `../api.raml`
therefore needs `ParseOptions(workspace_root=...)` naming a directory that
contains the whole chain.

The chain is applied from the root API outward. `extends`, `usage`, and `uses`
are removed from each extension document before the merge. They are the
spec's ignored properties, plus `extends` itself. `uses` is decoded per
document; see § 5.

Only a chain reached through the entry document is supported. Applying several
extension documents that each extend the same master (spec steps 1 to 3) is not
supported.

Code: `parser/extensions.py`. Tests: `tests/unit/test_extensions.py`.

## 3. The extension merge

`merge_extension(target, extension, ...)` applies one extension document's tree
to the current target tree. It differs from the template merge in
[08](08-templates-and-endpoints.md) § 1. There the authored target wins and
sequences are unioned. Here the extension wins on single values, appends array
items, and removes conflicting properties.

Like `merge_structural`, it never mutates either input. It allocates containers
only on changed paths and reuses every other node, so invariants I9 and I10
hold. Mapping order is the target's keys in target order, then new keys in
extension order (I8).

### 3.1 Grammar positions

The merge and the overlay check both need to know what a mapping key means.
`properties: {description: string}` declares a property *named* `description`,
so it is not a `description` facet. Each mapping is therefore visited with a
grammar position:

| Position | Name maps (key is a name, value has the position shown) | Other keys |
|---|---|---|
| API root | `types`, `schemas`, `annotationTypes` → type declaration; `traits` → method; `resourceTypes` → resource; `securitySchemes` → security scheme; `baseUriParameters` → type declaration; `/…` → resource | facets |
| Resource | HTTP methods (optionally `?`-suffixed) → method; `uriParameters` → type declaration; `/…` → resource | `type`, `is`, `securedBy` applications; facets |
| Method | `headers`, `queryParameters` → type declaration; `responses` → response; `body` → body | `queryString` → type declaration; `is`, `securedBy` applications; facets |
| Response | `headers` → type declaration; `body` → body | facets |
| Body | media-type keys (containing `/`, [08](08-templates-and-endpoints.md) § 6.3) → type declaration | otherwise the body *is* a type declaration |
| Type declaration | `properties`, `facets` → type declaration; `examples` → data | `type`, `items` → type declaration or expression; `example`, `default` → data; facets, including `enum`, the spec's own example of a multi-value simple property |
| Security scheme | none | `describedBy` → method; `settings` → generic |
| Data | none; the value is user data | none |
| Generic | none | keys recurse as generic |

An annotation key `(name)` in any non-data position holds data. Template
parameter text such as `<<name>>` is not interpreted. A key the table does not
list recurses as generic.

Code: `parser/extension_merge.py`. Tests: `tests/unit/test_extension_merge.py`.

### 3.2 Normalization

Before property kinds are compared, a pair whose kinds differ is normalized.
The literal merge would otherwise make the result depend on spellings the
spec treats as equivalent:

- `!!null` and an empty value become an empty mapping. `get:` with no value
  then merges like `get: {}`.
- A scalar in a type-declaration position becomes `{type: <scalar>}`.
  `name: string` then takes an overlay's `name: {description: …}` without
  losing its type.
- A scalar in a position that accepts a sequence of scalars (`protocols`,
  `mediaType`, `is`, `securedBy`, `allowedTargets`) becomes a one-item
  sequence. `protocols: HTTPS` then merges like `protocols: [HTTPS]`.

`type` is not a sequence position. A scalar there is a type expression, and a
sequence is multiple inheritance.

The spec calls the deprecated `schemas` and `schema` "synonymous" with `types`
and `type`. Each pair is one key to the merge, whatever the kinds:

- `schemas:` in an extension document adds to the master's `types:` instead
  of displacing it.
- A replaced pair takes the extension document's key (§ 5.3), so its spelling
  is the one that survives.

Normalization is a deliberate deviation; see § 7.

### 3.3 Property kinds and rules

After normalization, each extension property has one kind:

| Kind | Value | Merge when the target has the same key |
|---|---|---|
| Object | mapping | recurse |
| Array | sequence containing a mapping | append each object not already present (`node_value_equal`); see § 7 |
| Multi-value simple | sequence of scalars | append each value not already present (`node_value_equal`) |
| Single-value simple | scalar | replace |
| Data | data position (§ 3.1) | replace |

Other rules:

- **Different kinds** after normalization: the extension's value replaces the
  target's.
- **Always simple:** the spec makes these simple properties whatever their
  shape: resource-type applications (`type` in a resource position), trait
  applications (`is`), and security-scheme applications (`securedBy`). A
  mapping or a sequence containing mappings there is replaced whole. A
  sequence of names stays multi-value.
- **Named examples:** `examples` is an object keyed by example name, and each
  named example is data. A new name is added, and an existing name is replaced
  whole. `default` is data, like `example`.
- **`documentation`** is replaced whole rather than appended; see § 7.
- **Key absent in the target:** first remove the conflicting properties (§ 3.4)
  from the target mapping, then add the extension's key and its whole value.

### 3.4 Conflicting properties

Only pairs the spec declares mutually exclusive, and that are not synonyms
(§ 3.2), count as conflicts:

| Position | Pair |
|---|---|
| Method | `queryString`, `queryParameters` |
| Type declaration, including a body with no media-type keys | `example`, `examples` |

A removal is silent in the model. The lint rule `extension-removes-property`
reports it ([18](18-linting.md)).

In an Overlay, a removal is a change. § 4 rejects it unless the removed key is
itself allowed. Switching `example` to `examples` is therefore allowed.

## 4. Overlay restrictions

### 4.1 When the check runs

The check runs inside `merge_extension` when the document is an Overlay, at
each point where the target tree changes:

- a key is **added**;
- a value is **replaced** by one that is not `node_value_equal` to it;
- a multi-value or array property **gains** items; or
- a conflicting property is **removed**.

A value restated unchanged is not a difference, because the spec compares
trees. Each violation reports `not allowed in an overlay`, located at the
Overlay's key. `info` has `field` (the key) and `change` (`added`, `changed`,
or `removed`). For a removal, `field` is the removed key and the location is
the key that displaced it. Violations accumulate across the document.

Each Overlay in a chain is checked against the target tree it is applied to,
not against the root API.

### 4.2 Allowed differences

| Key | Where | Allowed |
|---|---|---|
| `title`, `displayName`, `description`, `usage`, `example`, `examples`, `documentation` | any facet position | anything at or below the key |
| `(name)` annotation application | any non-data position | anything at or below the key |
| `annotationTypes` | API root | anything at or below the key; see § 4.4 |
| `types`, `schemas` | API root | new entries. An existing entry is checked like any other type declaration. |

Every other change is rejected. This includes new names in any other name map
(resources, methods, responses, bodies, properties, parameters, headers,
traits, resource types, security schemes) and changes to any other facet.

### 4.3 Checking before template application

The spec compares trees *after* resource types and traits are applied. The
check here runs once per Overlay, *before* application. Both find the same
changes:

- Every allowed key has no effect on the API wherever it appears. A change that
  reaches an operation only through a template is still a change to an
  allowed key in the declaration.
- Any disallowed change to a declaration is already a difference in the
  declaration itself. This covers a new trait, trait `headers`, and a changed
  `is:` or `type:` application.

A second template application is therefore unnecessary.

### 4.4 Annotation types

The spec lets an Overlay change an existing annotation type, provided every
annotation still validates against the merged declaration. That condition is
ordinary annotation validation in P10 ([09](09-security-and-annotations.md)
§ B3), so it applies only when validation is requested, as for any document.
When a value fails against an annotation type that an extension document
changed, the `invalid annotation value` error gets an outer frame,
`annotation type changed by an extension document`. The frame is located at the
key of the change and has the annotation name in `info`. The merge reports the
existing root annotation types each document changed. A newly added annotation
type is not a change, and the latest document to change a type is the one named.

## 5. Namespaces and provenance

### 5.1 `ExtensionFragment`

Each extension document is registered at its own URI as an `ExtensionFragment`
with `kind` (`Overlay` or `Extension`), `location`, `usage`, `uses`, `extends`
(its master's URI), and its chain position. It implements `ReferenceResolver`
and `SecuritySchemeResolver`. Nodes authored in the document resolve their names
through this fragment ([04](04-fragments-and-namespaces.md) § 4).

### 5.2 Visibility along the chain

Every declaration lives in the merged tables of the target tree's API
fragment. A document **sees** a declaration name only if the name is declared
by the document itself or by a document earlier in its chain. It sees a `uses:`
prefix under the same rule.

- The merge records the chain position of every declaration name an extension
  document adds, per declaration kind. A name with no record belongs to the
  root API. A lookup is one lookup in the merged table plus a comparison of
  chain positions.
- Each document's visible `uses:` map is its own map combined with its
  master's, built once. A prefix that names a different library URI from the
  same prefix earlier in the chain reports `library namespace conflict`, with
  `library` and both URIs in `info`. The spec requires this: the trees "MUST
  NOT have uses instructions with the same namespace referring to different
  files".

A root API node therefore cannot resolve a name that only an extension
declares, which is the spec's "Master Tree … validated". An extension document
can use its masters' library prefixes without repeating `uses:`.

### 5.3 Document provenance

The merge records every node it takes from an extension document through
`Raml.mark_authored`. The marks live in
`Raml._document_provenance: dict[Node, ExtensionFragment]`, keyed by node
identity.

- A grafted or replacing subtree is recorded whole, because `Node` has no
  parent pointer.
- A pair the merge adds or replaces uses the extension document's key node as
  well as its value, and both are recorded. A diagnostic on the key then names
  the same file as one on the value.
- Root API nodes are not recorded.
- A mark is never replaced.

Authorship does not depend on where a node is later grafted, so this map is
parse-wide. The [08](08-templates-and-endpoints.md) § 4 overlay is per unit.
Readers consult the document mark first, then the active unit overlay, then
their default:

- `location_of` returns the authoring document's URI.
- `scope_for` and `provenance_scope` push the enclosing `ParseCtx` with the
  authoring document as its anchor. A document mark never changes the
  annotation target.

Checking the document mark first is sound for these reasons:

- Template substitution creates new nodes, so a substituted value never
  carries a document mark and keeps its caller's scope.
- Static template content keeps its identity, so content an extension document
  changed inside a root API trait keeps its document mark after the trait is
  grafted.
- A complex parameter tree cannot mix authors, because `is:` and `type:`
  applications are replaced whole (§ 3.3).

The sites below consult document provenance directly, in addition to the
readers in [08](08-templates-and-endpoints.md) § 4.2. Each decides by the
**value** node it decodes. A value the merge recursed into is a new container,
carries no mark, and stays with the enclosing document. These sites call
`document_location` and `document_anchor`, which ignore template scopes: a
substituted scalar keeps its template's positions, so only authorship may
rename its file.

- `make_scalar_facet`, for location;
- `resolve_include` and `note_include_ref`, for the base of a relative path;
- `make_data_node`, for location;
- `unmarshal_domain_extension`, for location and anchor, keeping the enclosing
  target;
- `decode_documentation_item`, for location;
- `make_template_definition`, for location and anchor;
- `make_security_scheme_definition`, for location. Nested shapes find their
  anchor through `scope_for`.
- stage 1 (`make_source_endpoint`, `make_source_operation`), for an endpoint's
  or operation's location and scope, and for each `type`, `is`, and `securedBy`
  directive separately. A directive an extension document added to a root API
  method resolves in the extension document's namespace.

During a parse, `Raml.reporting_authorship` makes the marks visible to
`node_error` through a context variable. A diagnostic built for a marked node is
then located in the document that wrote it ([11](11-diagnostics.md) § 4).
Diagnostics built from a bare position rather than a node keep the location
their caller passed.

Only the passes read the marks. `Raml.release_document_provenance` clears them
when the passes end, including after a failure. Kept, they would hold every
extension document's YAML tree for as long as the model lives, which P4 avoids
for the API's own tree.

A `DataNode` records one location. In a multi-value sequence the merge extended,
such as `enum`, an item an extension document appended keeps its own
positions but is reported against the `DataNode`'s location.

### 5.4 Root annotations

An annotation application authored at the root of an extension document
targets `Overlay` or `Extension`, not `API`. An annotation type with
`allowedTargets: API` therefore rejects it.

## 6. Result model

`parse_from_path` on an extension document returns a `Raml` with these
properties:

- `entry_point` is the target tree's `APIFragment`, located at the root API's
  URI. P4 onwards and every view read it unchanged.
- `Raml.extensions` lists the `ExtensionFragment`s in application order.
- `Raml.fragments` holds the API fragment and each extension document.
- Entities carry the URI of the document that authored them.

The root API is never decoded on its own, and no extension document body is
decoded on its own, so invariant I3 holds. `parse_lenient` re-raises a failure
to load the chain when the failure's outermost frame is located at the entry
URI ([11](11-diagnostics.md) § 2).

## 7. Deliberate deviations

| Rule | Spec text | Reading here | Reason |
|---|---|---|---|
| `documentation` is replaced | Merging Rules: an Array Property's objects "are added" | replaced whole | The Overlays table says the node "can be overridden", and the localization example needs replacement. The spec contradicts itself here. |
| Shorthand is normalized (§ 3.2) | Merging Rules: different Property Kind → replaced | kinds compared after normalization | The literal result depends on spellings the spec treats as equivalent. An overlay adding `description` to `name: string` would count as a behaviour change. |
| Replaced root API values are not checked on their own | Merging Rules: "Master Tree … validated" | names are checked (§ 5.2); a value a later document replaces is never decoded | Checking those values would mean decoding the root API a second time |
| `default` is data | Merging Rules lists examples and annotations as always simple | `default` is also replaced whole | It holds user data, as in the template merge ([08](08-templates-and-endpoints.md) § 1) |
| An array adds only new objects | Merging Rules: an Array Property's objects "are added" | an object already present is skipped | Otherwise restating an array changes it, and an Overlay that restates one fails § 4.1 |
| Applying sibling documents | Overlays and Extensions, steps 1 to 3 | only the chain from the entry document | Needs an API for several entry documents; not yet designed |

Readings where the spec was followed although another implementation differs:

- conflicting properties are removed (§ 3.4);
- annotation types may change in an Overlay (§ 4.4);
- new traits, resource types, and security schemes, and changes to template
  bodies, are rejected in an Overlay (§ 4.2);
- multi-value merges drop duplicates (§ 3.3).

## 8. Cost

A parse without extension documents pays one truthiness check on
`_document_provenance` in each reader in § 5.3. With `k` extension documents:

- **Compose:** each file once.
- **Merge:** visits every extension node, plus root API nodes only along keys
  the extension also has. Containers are copied only on changed paths, which
  costs O(k · W) for changed mapping widths W.
- **Provenance marks:** one dict insert per extension node.
- **Overlay check:** runs inside the merge. The only non-constant step is
  `node_value_equal` on a disallowed replacement, bounded by the replaced
  subtree.
- **Decode:** the target tree is decoded once, and templates are applied once
  in P4.

Total work is linear in the input. The benchmark suite has an extension shape
([12](12-performance.md) § 4).
