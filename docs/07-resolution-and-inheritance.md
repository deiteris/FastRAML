# 07 - Resolution, inheritance, and unwrap

This document owns P7 resolution and P9 flattening. The shape model is in
[05](05-type-model.md); declaration and value checks are in
[10](10-validation.md).

## 1. Pass boundaries

| Operation | Pass | Result |
|---|---|---|
| resolve | P7 | settle unknown kinds and bind names |
| unwrap | P9, opt-in | flatten inheritance, mark recursion, build union dispatch |
| check / validate | P10, opt-in | validate declarations and embedded values |

P7 leaves `inherits`, `alias`, and `link` edges intact. P9 rewrites a data-type
`link` to its semantic inheritance parent, removes all reachable links, and
produces the effective model. Public data validation requires that effective,
unwrapped form.

## 2. Resolution

During decoding, a declaration whose kind is not yet known has an
`UnknownShape` and is queued in `Raml.unresolved_shapes`. `resolve_shapes(raml)`
drains that queue until empty because resolving an expression or deferred
declaration facet can create more shapes.

Resolution is idempotent and re-entrant: resolving a reference may resolve its
referent out of queue order. A cycle through type resolution (`A: B`, `B: A`) is
an error. A cycle through child declarations is legal and is marked in P9.

P7 resolves these forms:

- a data-type `!include` takes the linked root's kind but retains `link`;
- multiple inheritance resolves every parent and initially takes the first
  parent's kind;
- an RDT expression is parsed through the per-parse cache and built as described
  in [06](06-type-expressions.md#3-building-shapes).

## 3. Shape edges

| Edge | Created by | Meaning before P9 |
|---|---|---|
| `inherits` | mapping `type:` reference or `type:` sequence | child narrows parent constraints |
| `alias` | bare scalar reference | second declaration identity for one type |
| `link` | `type: !include` | included data-type declaration |

An alias keeps its own ID, name, authored location, and positions. After P9 it
shares the referent's kind fields and common-facet containers; it is not a
subtype. Traversals that need type structure must follow aliases.

## 4. Unwrap and narrowing

`unwrap_shapes(raml)` iterates the flat `fragment_typedefs` index, recursively
reaches nested declarations, and rebuilds `raml.shapes` with effective shapes.
It is idempotent. `unwrap_shape(raml, base)` may return a replacement base,
particularly when a union merge collapses; callers must use that return value.

All parents are unwrapped before a child. Multiple inheritance folds parents
into a fresh synthetic shape so merges cannot mutate or share a parent container.
A subtype may only narrow:

| Kind | Narrowing contract |
|---|---|
| common | inherit absent description; custom facets union with child values winning; enum is inherited or becomes a subset, compared with the semantic equality of enum membership ([10](10-validation.md) § 5) |
| string | increase `minLength`, decrease `maxLength`; child pattern replaces parent pattern |
| number/integer | increase minimum, decrease maximum, use a compatible `multipleOf` and format |
| datetime | format must agree |
| file | increase/decrease length bounds; `fileTypes` becomes a subset |
| array | recursively narrow `items`; tighten counts; a unique parent requires a unique child |
| object | recursively narrow shared properties and patterns; required cannot become optional; tighten counts; inherit absent `additionalProperties` and discriminator |
| union | merge compatible members as described below |
| any | contributes no constraint |
| json | only an identical schema can be inherited |

Different concrete kinds cannot merge. Unknown and recursive targets cannot be
inherited. A recursive source is compared through its head.

## 5. Union inheritance

When a non-union child inherits a union, P9 clones the child graph for each
compatible member and retains the survivors. No survivor is an error, one
survivor replaces the child, and several form a union. Examples, named examples,
and defaults written on the child remain on that resulting union, not its cloned
members.

When a union inherits a non-union, every member must narrow successfully. When
two unions merge, a memberless child adopts parent members; otherwise every
parent member must find a compatible child member.

Facets beside `type: A | B` are retained as YAML until these merges settle the
member list. P9 applies each to a fresh subtype of each member, then merges that
subtype with its member. This avoids mutating parent union members and permits
member-declared custom facets to validate the distributed value. A facet no
member accepts remains an ordinary unknown custom facet error in P10.

The declaration facets `properties` and `items` are also built when the union's
kind is attached, so P7 resolves the names inside them. Each is held in a holder
of the kind that defines it, and P9 unwraps the holder once. Every member whose
kind takes the facet receives a detached copy, and each copied property or
`items` shape gets a fresh id, because each member's merge narrows its copy in
place. A member that is itself a union passes the holders on to its own members.
A member whose kind does not take the facet receives the YAML pair and reports
it as an unknown facet.

## 6. Recursion and cloning

After flattening, `finish_unwrap()` replaces every child edge that closes a
cycle with `RecursiveShape(head)`. It covers array items, object and pattern
properties, union members, and custom-facet declaration shapes. Validation of a
marker delegates to its head. Alias edges are resolved before recursion marking
so a shared alias container is not corrupted. Recursive walks use the parse's
shared depth limit.

`clone(memo)` makes a structure-preserving copy keyed by `BaseShape.id`; cycles
and diamonds remain cycles and diamonds, and the clone retains IDs. A caller that
needs a fresh identity assigns one. `clone_detached()` uses a fresh memo for an
independent mutable shape graph, used by union merging and P10 private unwrap.
Scalar facets, data nodes, compiled patterns, and the `Raml` back-pointer are
intentionally shared because they are not mutated in place. `copy.deepcopy` is
not used.

Implementation: `types/resolve.py`, `types/unwrap.py`, `types/inherit.py`, and
`types/base.py`. Tests: `tests/unit/test_resolve.py`, `test_inherit.py`,
`test_unwrap.py`, and `test_clone.py`.
