# 08 — Templates and endpoints (the two-stage build)

This document covers spec § Resource Types and Traits, § Algorithm of Merging
Traits and Methods, and § Resource Types and Traits Effect on Collections. It is
the part of the parser where design choices have the largest effect on
performance and on the amount of code required.

## 1. What the spec requires

Restating spec § Algorithm of Merging Traits and Methods in the terms of a parser:

1. Applying a trait to a method means putting the trait's *branch of the document*
   underneath the method's branch.
2. Properties only in the method: unchanged. Properties only in the trait: added.
   Properties in both: scalars keep the method's value, **collections merge by
   value**, objects recurse.
3. A method may have several traits, and traits arrive from four distances:
   the method's own `is:`, the resource's `is:`, the resource type's method-level
   `is:`, the resource type's resource-level `is:` — in that priority order,
   deduplicated by name with the closest occurrence winning.
4. Resource types merge into resources by the same rules, and a resource type may
   itself have a `type:`, forming a chain.

Note the vocabulary: *branch of the document*. The algorithm is defined on the
YAML tree, not on a typed model. Section § Effect on Collections then adds:
"the actual enum is `[mac, unix, win]`" — a union of the two sequences, target
first.

## 2. Why eager decoding costs more

A parser that builds `Operation`, `Request`, `Response` and `BaseShape` objects
first has to implement step 2 over the model instead of over the tree. That means
a separate inheritance-aware merge for `headers`, for `queryParameters`, for
`responses`, for `body`, and for every shape facet. It must then re-run type
resolution, because a type name contributed by a trait was never resolved in the
operation's context. Every facet added later is another merge case.

go-raml took this approach first and replaced it. fastRAML starts from the
replacement.

## 3. The two stages

### Stage 1 — structural decode

Decoding a resource produces an **IR**, not a model:

```python
class SourceOperation:
    __slots__ = ("id", "method",
                 "traits", "rt_traits",              # resolved directives
                 "secured_by", "explicit_secured_by",
                 "body",                             # Node | None — the remainder
                 "scope",                            # ParseCtx — default namespace
                 "provenance",                       # dict[Node, ParseCtx]
                 "location", "key_pos", "value_pos")

class SourceEndPoint:
    __slots__ = ("id", "uri", "full_uri",
                 "resource_type", "traits", "rt_traits",
                 "secured_by", "explicit_secured_by",
                 "operations",                       # dict[str, SourceOperation]
                 "endpoints",                        # dict[str, SourceEndPoint]
                 "body", "scope", "provenance",
                 "location", "key_pos", "value_pos")
```

Stage 1 consumes exactly four kinds of key and leaves everything else alone:

| Key | Action |
|-----|--------|
| `type:` (on a resource) | build a `DirectiveRef` (name + params) |
| `is:` | build `DirectiveRef`s (names + params) |
| `securedBy:` | build `DirectiveRef`s; set `explicit_secured_by` |
| an HTTP method | recurse into `make_source_operation` |
| a `/subresource` | recurse into `make_source_endpoint` |
| **anything else** | appended verbatim to the retained `body` mapping |

So `headers`, `queryParameters`, `queryString`, `body`, `responses`,
`uriParameters`, `displayName`, `description`, `protocols` and annotations all
stay as YAML. No shape exists yet for any of them.

`body` is rebuilt as a fresh mapping node carrying the original's position, or
left `None` when nothing was retained.

### Stage 2 — materialization

After all directives are merged (§4, §5), each `SourceOperation` /
`SourceEndPoint` is decoded **once** into the real `Operation` / `EndPoint`,
using the ordinary facet decoders — with the provenance overlay active (§6).

```python
for sep in api.source_endpoints:
    self.resolve_source_endpoint(api, sep)  # 4a
for sep in api.source_endpoints:
    api.endpoints[...] = self.decode_source_endpoint(sep)  # 4b
```

Both loops run over the whole tree before the other starts. That ordering matters:
directive resolution can graft type-bearing subtrees from templates, and all of
them must exist before the single decode pass, so that every shape they produce
lands in the P7 worklist.

Two facet rules the stage-2 decoders enforce and are easy to leave out, because
in each case the API root already enforces the same thing on its own copy of the
facet: a `responses:` key must be a **3-digit** status code (`2xx` is not RAML),
and `protocols:` may name only `HTTP` or `HTTPS`, compared case-insensitively.
`VALID_PROTOCOLS` lives on `parser/endpoints.py` so the root and the method share
one definition rather than two that can drift.
`Response.code` and the `Operation.responses` keys are strings after that
validation because spec § Responses settles the apparent YAML-type ambiguity
directly: keys "SHOULD BE numeric", but processors "MUST treat these numeric
keys as string keys in all situations". Its example says `200` and `'200'` are
duplicates. The decoder therefore normalises both spellings to `'200'` rather
than exposing YAML's inferred integer. Consumers that need status classes
convert the already-validated value to an integer locally; this does not admit
OpenAPI's `2XX` response classes.

## 4. The structural merge

```python
def merge_structural(target: Node | None, source: Node | None,
                     source_scope: ParseCtx,
                     overlay: ProvenanceOverlay | None) -> Node | None
```

`target` is the higher-priority branch (the method, the resource); `source` is the
lower-priority one (the trait, the resource type). Rules:

- either side `None` → the other side (grafting the source records provenance);
- differing kinds → **target unchanged** (the explicit node wins);
- mapping + mapping → §4.1;
- sequence + sequence → §4.2;
- scalars → target unchanged.

Neither input is ever mutated (invariant I9). New mapping/sequence nodes are
allocated, but **child pointers are reused** — which preserves node identity
(invariant I10) so the provenance overlay stays valid.

### 4.1 Mappings

Two passes:

1. Walk **target** keys in order. A key present in both recurses. A key present
   only in the target is kept as-is.
2. Walk **source** keys in order, appending those the target does not have, and
   marking each grafted value subtree in the overlay.

Result ordering is therefore "target's keys in target order, then source-only keys
in source order" — deterministic and stable, which matters for golden tests.

**Opaque data facets.** `example`, `examples` and `default` are *not* recursed
into. The spec's "object → recurse" rule is about RAML declaration structure, not
about arbitrary user data: a method's own `example` replaces a trait's example
wholesale rather than being merged key by key. Without this rule, example data
containing a key literally named `example` would be misinterpreted, and partial
example merging would produce values that validate against nothing. Spec § Merging
Rules confirms the intent: "Examples are always Simple Properties despite the
capability to have complex YAML samples as values."

### 4.2 Sequences

Union: all target items, then source items that are not structurally equal to
anything already present. Structural equality compares tag+value for scalars and
element-wise for composites, ignoring positions and comments.

This union rule produces the `[mac, unix, win]` result in the spec's example.
The same rule keeps `is: [{secured: {tokenName: token}}]` and
`is: [{secured: {tokenName: access_token}}]` as two distinct items: they are not
structurally equal, so both survive the sequence merge. Traits are then
deduplicated **by name** in a separate step (§ 5.2), where the closest occurrence
wins.

## 5. Applying directives

### 5.1 Resource types

```python
def apply_resource_type(api, sep, rt, visited):
    if rt.name in visited: return          # cycle guard
    visited.add(rt.name)
    rt_def = resolve_resource_type_definition(api, rt)   # lexical, doc 04 §4
    params = {**rt.params,
              "resourcePath":     scalar(sep.full_uri),
              "resourcePathName": scalar(resource_path_name(sep.full_uri))}
    existing = set(sep.operations)
    compiled = compile_resource_type(rt_def, params, existing,
                                     caller_scope=sep.scope, ...)
    if compiled.resource_type:              # the RT's own `type:` — chain
        apply_resource_type(api, compiled, compiled.resource_type, visited)
    merge_resource_type_into(sep, compiled)
```

`compile_resource_type` does, in order:

1. If the definition is a `!include` link, recurse into the linked fragment's
   definition **with the fragment's own location and scope** — self-containment
   (deviation D4).
2. Validate supplied parameters: every non-reserved supplied name must be
   declared by the template.
3. **Filter optional methods** (spec § Declaring HTTP Methods as Optional): a
   `post?` in the template is dropped unless the target resource already declares
   `post`. This happens *before* substitution.
4. Re-collect required variables **from the filtered tree** and check each is
   supplied. This ordering is required by the spec's own example: `corpResource`
   declares `<<TextAboutPost>>` inside `post?`, and `/queues` — which has no
   `post` — must not be required to supply it.
5. Substitute parameters, recording provenance (§6).
6. Run stage-1 decode on the substituted tree, under the *resource type's* scope,
   producing a `SourceEndPoint`.

`merge_resource_type_into(target, source)` then:

- merges the endpoint-level bodies (`uriParameters`, `description`, …);
- routes the RT's endpoint-level `is:` entries into `target.rt_traits`, **not**
  `target.traits`, preserving priority class;
- appends the RT's `securedBy` to the target's;
- adopts the RT's `type:` if the target has none (chaining);
- for each RT method: if the target has the method, route the RT method's `is:`
  into that operation's `rt_traits` and merge the bodies; otherwise graft the
  whole operation, moving its `traits` to `rt_traits` (it has no "own"
  declarations at this site).

The `traits` / `rt_traits` split exists solely to keep the four priority classes
distinguishable after the merge has flattened everything else.

### 5.2 Traits

Per operation, build the priority-ordered list and deduplicate by name, first
occurrence winning:

```python
all_traits = [
    *op.traits,  # 1. method's own
    *sep.traits,  # 2. resource's own
    *op.rt_traits,  # 3. resource type's method-level
    *sep.rt_traits,
]  # 4. resource type's resource-level
```

Spec § Effect on Collections: "priority is given to the trait in closest proximity
to the target method or resource" — so `{secured: {tokenName: token}}` on the
method beats `{secured: {tokenName: access_token}}` from the resource type, and
the trait is applied exactly once.

For each surviving trait: resolve the definition lexically, inject the reserved
parameters (`resourcePath`, `resourcePathName`, `methodName`), validate the
parameter set both ways (no undeclared supplied, none declared missing),
substitute, and merge the compiled body underneath the operation's body.

`methodName` is per-operation; `resourcePath`/`resourcePathName` are per-resource
and are built **once per resource**, not once per trait application — they are
read-only scalars never inserted by pointer, so sharing is safe and saves an
allocation per application.

### 5.3 `resourcePathName`

Spec: "the rightmost of the non-URI-parameter-containing path fragments", with
`ext` and its braces omitted. Scan from the right, skipping trailing slashes and
skipping any segment of the form `{…}`:

```
/users/{userId}/addresses  → "addresses"
/users/{userId}            → "users"
/bom/{itemId}{ext}         → "bom"
```

## 6. Provenance: resolving names after the merge

### 6.1 The problem

After the merge, one operation's tree contains nodes authored in three places:

```yaml
# api.raml
/items:
  get:
    is: [paged: {responseType: types.PagedResult}]
```
```yaml
# traits/paged.raml
#%RAML 1.0 Trait
uses: {models: ../models.raml}
responses:
  200:
    body:
      application/json:
        type: <<responseType>>
        headers: {X-Total: models.Count}
```

In the merged tree:

- `headers: {X-Total: models.Count}` came from the trait → `models` must resolve
  through **the trait fragment's** `uses:`;
- `type: types.PagedResult` was substituted from the caller → `types` must
  resolve through **api.raml's** `uses:`;
- everything the operation wrote itself resolves in api.raml.

A single "current file" is wrong for all three at once.

### 6.2 The mechanism

```python
ProvenanceOverlay = dict[Node, ParseCtx]  # keyed by node IDENTITY
```

Sparse: only nodes whose scope differs from the enclosing unit's default are
recorded. Two writers:

**`compile_source_provenance`** — during parameter substitution:

- a static scalar (no variable in it) is left **unmarked** → it keeps the
  declaration scope;
- a complex (non-scalar) parameter value replaces the node entirely; the spliced
  subtree root is marked with the **caller's** scope;
- a scalar that received at least one substitution is copied and the copy is
  marked with the **caller's** scope.

**`mark_graft`** — during the structural merge, for every source-only subtree
grafted into the result: mark the whole subtree with the **source's** scope
(the trait's / resource type's declaration namespace).

Both writers mark **set-if-absent**. A node that already carries a mark defines
its own scope domain, so `mark_graft` neither overwrites that mark nor descends
into the subtree beneath it. A caller-supplied value spliced inside a trait body
therefore keeps the caller's namespace even though it sits inside a grafted
subtree. This rule is what resolves all three cases in § 6.1 correctly.

`mark_graft` marks the whole subtree, not only its root, because a stage-2
decoder may need the scope of a node several levels below a merge-synthesised
container that carries no mark of its own.

### 6.3 Reading the overlay in stage 2

Four methods on `Raml`, because the overlay lives there and every reader already
holds one:

```python
with raml.active_overlay(source.provenance), _body_scope(raml, source):
    for key, value in pairs(source.body):
        with raml.provenance_scope(value):
            decode_field(raml, key, value, location)
```

- **`active_overlay(overlay)`** makes one unit's marks readable. Saved and
  restored rather than set: an endpoint's own body decode encloses each of its
  operations'.
- **`_body_scope`** picks the base scope. Normally the unit's own — but the body
  *root* may itself be a boundary, as it is for an operation with no body of its
  own whose whole body was grafted from a trait, and then the trait's scope is
  what every unmarked node beneath it inherits.
- **`provenance_scope(node)`** pushes the mark for one facet value, if it has one.

Two more lookups feed off the active overlay, both one layer deeper than that
loop — which is what makes them survive the containers the merge synthesised:

- **`scope_for(node)`**, called by `make_shape`. It checks the `type:`/`schema:`
  facet *value* of a mapping first, then the mapping itself. Most-specific wins:
  a caller-substituted `type:` scalar inside a grafted body must beat the graft's
  own mark. The scope is pushed around the *whole* shape build, so nested facets
  inherit it.
- **`location_of(node, default)`**, called by every entity constructor and
  structural helper (`make_property_map`, `_decode_responses`, `_decode_bodies`,
  `make_shape`). It answers "which file does this node belong to?", so an error
  inside a trait-contributed response reports the trait's path, not the API's.

**The two deliberately disagree, and a test that asserts otherwise is wrong.**
`body: {application/json: {type: <<item>>}}` inside a library's resource type
produces a shape whose `location` is the library — the body was authored there —
and whose `anchor` is the applying document, because `<<item>>` came from the
caller and its value names a type in the caller's namespace. Both are right;
that is what most-specific-first means.

One thing the overlay does *not* do is establish a scope where none existed.
`build_endpoints` runs after the API's own decode has popped its context, so the
driver pushes `ParseCtx(anchor=resolver_at(api.location))` around both stages.
Without it every endpoint shape is built with `anchor=None` and leans on P7's
`resolver_at` fallback — which gives the same answer for a document that declares
everything itself, and the wrong one for anything a template contributed.

### 6.4 Granularity limit: one scope per shape

Scope boundaries are honoured at **facet-value granularity**. Because a single
`BaseShape` carries a single anchor, a body that mixes a declaration-scoped media
type with a grafted one under the same facet resolves both under one scope.

This limitation is documented in go-raml ("D3, minimal-first"), and fastRAML
accepts it. Removing it would require splitting a shape's anchor per facet. That
work is deferred until a fixture requires it; a test records the current
behaviour so that any change is visible.

### 6.5 Python note: identity keys

`dict[Node, ParseCtx]` keyed by identity works because `Node` defines no
`__eq__` or `__hash__` and therefore inherits identity semantics
([03](03-yaml-and-io.md) § 2). Do **not** substitute `dict[id(node), ...]`. An
`id()` value is unique only among live objects, and the overlay must keep its
keys alive. Using the node itself as the key does both.

## 7. Template variables

### 7.1 Indexing

A template body is scanned **once, at declaration time**, producing:

- `declared_variables: set[str]`
- `node_variable_index: dict[Node, list[VariableInfo]]`

Scanning once is the point: a resource type applied to 200 endpoints scans its
body once, and every application looks the results up.

**The key is the node itself, and go-raml does this differently.** It keys by a
*positional index* computed as "a node has index `idx`, its i-th child has
`idx + i`". That has two faults, and fastRAML's earlier design fixed only the
first:

1. **The numbering is not injective.** A node and its own first child both
   receive `idx`; a trait body of 17 nodes collapses onto 7 indices. Substitution
   tolerates it, because replacing an absent substring is a no-op, but
   `collect_required_variables` returns *names* from a subtree, so a collision
   makes it demand a parameter the template never used. A unique preorder
   sequence fixes this, and fastRAML used one until the second fault surfaced.

2. **Any numbering is invalidated by § 5.1 step 3.** Optional-method filtering
   removes whole subtrees from the body *between* the scan and its use, so every
   node after the removal shifts. The index then describes a tree that no longer
   exists. This is not theoretical: it is the spec's own `corpResource` /
   `/queues` example, and go-raml fails it in both directions —

   ```
   missing required parameter: parameter: TextAboutPost
   ```

   for a resource with no `post` at all, and, once that parameter is supplied to
   silence the error, `<<TextAboutGet>>` survives *unsubstituted* into the model,
   because the index entry it looks up now belongs to a different node.
   Recorded as `KNOWN-ISSUES.md` entry 6.

Keying by node identity removes both at once. There are no two walks to keep in
agreement, so the `docs/15` risk register entry "the two index walks drift apart"
no longer describes anything; and filtering a subtree out cannot disturb the
entries for the subtrees that remain. `Node` already hashes by identity, for the
provenance overlay's sake (§ 6.5), so the map costs one pointer per
variable-bearing scalar and nothing per application.

`iter_nodes` is the one traversal both `collect_variables_index` and
`collect_required_variables` use. It is iterative, so template depth cannot reach
CPython's recursion limit.

`VariableInfo` is `(name, substring, actions)` where `substring` is the literal
`<<name | !action>>` text, so substitution is `str.replace(substring, value, 1)`
with no re-parsing.

### 7.2 Parsing `<<name | !action | !action>>`

Scan for `<<`, find `>>`, split the content on `|`, strip spaces:

- the first part is the name; a leading `!` there is `action without variable name`;
- every later part must start with `!` and be one of the ten known actions;
- unclosed `<<` is an error.

### 7.3 The ten actions

| Action | Implementation |
|--------|----------------|
| `!uppercase` / `!lowercase` | `str.upper()` / `str.lower()` |
| `!uppercamelcase` / `!lowercamelcase` | split on ` _-`, recase |
| `!upperunderscorecase` / `!lowerunderscorecase` | insert `_` before internal capitals, then case |
| `!upperhyphencase` / `!lowerhyphencase` | same with `-` |
| `!singularize` / `!pluralize` | `pluralizer`, plus four irregular rules |

Eight of the ten are rules. The other two need a **dictionary**, because English
supplies no rule that turns `criterion` into `criteria` — so the only way to
agree with go-raml is to share its dictionary, not to patch
a different one.

go-raml uses `go-pluralize`, a port of Blake Embrey's JavaScript `pluralize`.
fastRAML uses `pluralizer`, a port of the *same* library, so the two agree by
construction. An earlier draft of this section instead paired `inflect` with a
three-word override table, chosen because three TCK fixtures named those three
words. Measured by running go-raml's own transform over go-pluralize's
whole irregular and uncountable tables, that pairing was wrong on **298 of 758
answers** — `index→indexes`, `cactus→cactuses`, `radii→radii`,
`curriculum→curriculums` — while passing every test that named only the three
words it had been built around.

Four irregular rules are registered on top. Three are the ones go-raml adds:
`medium↔media`, `memorandum↔memoranda`, `vortex↔vortices`. The fourth,
`sms↔sms`, is in go-pluralize's own irregular table and absent from the
Python port's, which tracks an earlier release of the shared JavaScript source.
With those four, the two implementations agree on every one of the 618 answers
in `tests/unit/data/pluralize_parity.tsv` — a table generated from go-raml
itself, and the test that reads it is the guard against this drifting again.

`!singularize`/`!pluralize` on an empty string return the empty string.

### 7.4 Where parameters are forbidden

Spec § Resource Type and Trait Parameters: "Parameters cannot be used within any
file location … defined in the `!include` tag or as a value of any of the `uses`
or `extends` nodes." Structurally enforced: `uses:` is stripped before the
template body is captured, and an `!include` tag's value is resolved at decode
time, before any substitution ever runs.

## 8. Endpoint construction

### 8.1 URI computation

`full_uri` is the concatenation of ancestors' relative URIs, computed during
stage-1 decode (`parent + key`). The base URI is *not* prepended — it is exposed
separately on the API, per spec § Resources and Nested Resources, and trailing
slashes on `baseUri` are stripped only when a consumer joins them.

Duplicate absolute URIs are rejected (`/users: {/foo:}` plus `/users/foo:`), with
comparison done on the template text without expanding parameters — so
`/users/{userId}` and `/users/{username}` and `/users/me` all coexist.
The linter's overlap check uses `simple_parameter_segment` from the same URI
template parser. It compares literal segments and segments made entirely from
Level-1 expansions. Mixed literal/expression segments and Level-2 reserved or
fragment expansions are skipped: they can produce delimiters, and treating them
as one wildcard segment would be a guess rather than a fact from the model.

### 8.2 URI parameters

For each endpoint, the URI template is parsed (RFC 6570 Level 1 and 2: `{var}`,
`{+var}`, `{#var}`) and:

- every variable without an explicit `uriParameters` entry gets a synthesised
  required `string` shape, retained as `Parameter.synthesized = True` so an
  opt-in style rule can require authored declarations without changing RAML's
  valid shorthand;
- every declared parameter not present in the template is an error
  (`uri parameter is not used`);
- constraint values (`default`, `enum`, `example`, `examples`) may not contain
  `/`, since spec § Template URIs says matched values must not contain slashes.

Malformed templates report at the exact byte: unclosed `{`, nested `{`,
unexpected `}`, empty expression, invalid characters in a varname (RFC 6570
`varname = varchar *( "." 1*varchar )`), malformed pct-encoding.

The same routine parses `baseUri`, at the API root's own decode: `http://{myapi.com`
is an unclosed expression, not a hostname. Only the *template* half is shared —
`baseUriParameters` are not cross-checked against it the way a resource's are,
because `{version}` is legal there with nothing declaring it.

**Propagation** (P6): each endpoint's parameter map is rewritten to
ancestor-declared parameters first, then its own. A nested resource therefore
exposes the full set needed to build its URL, in path order.

The map holds `Parameter`, not `Property` ([05](05-type-model.md) § 5), and an
inherited entry is the **same object** at the ancestor and at every descendant —
the rewrite is `{**inherited, **own}`, so nothing is copied. That is what makes
its position cite the resource that actually declared it, and it means a
consumer indexing parameters by identity gets one entry per declaration rather
than one per resource that inherits it. A synthesised variable is created at the
endpoint whose template named it, so it is not shared.

`baseUriParameters` binds as `uri` too, and is the one parameter map with no
template to check against — § 8.2 above says why.

### 8.3 Bodies and default media types

`decode_media_type_node` handles the two spellings of `body:`:

- a mapping whose keys all contain `/` → each key is a media type;
- anything else (a scalar, or a mapping of facets) → **the API's default media
  types** apply, and the same declaration is instantiated once per default media
  type. With no `mediaType:` at the API root, this is an error:
  `explicit media type is required`.

A mapping that mixes media-type keys and non-media-type keys is an error listing
each offending key — this catches the common
`body: {application/json: ..., type: Foo}` mistake.
