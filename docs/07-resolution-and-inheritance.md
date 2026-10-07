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
While settling a declaration, P7 restores the namespace of the file that wrote
its deferred facets and the captured annotation target. For a headerless include
with no namespace of its own, it uses the declaration's captured namespace, so
children resolve as if written in the includer while staying located in the
included file (docs/04 § 4.1). This also keeps a typed trait's static facets in
the trait's namespace when the caller supplied its `type:` (docs/08 § 4.2).

P7 resolves these forms:

- a data-type `!include` takes the linked root's kind but retains `link`;
- multiple inheritance resolves every parent and initially takes the first
  parent's kind;
- an RDT expression is parsed through the per-parse cache and built as described
  in [06](06-type-expressions.md#3-building-shapes).

After draining the worklist, P7 publishes a lazy binding index for supplied
custom-facet `DataNode`s in `Raml.custom_facet_refs`. Its first read indexes
already-resolved parents and declarations; it resolves no name. The shared ancestor
walk in `types/custom_facets.py` starts at semantic parents, follows aliases
and DataType links, and preserves breadth-first declaration order. Unknown
values remain unbound; multiple declarations remain candidates for tooling.
P10 owns missing, unknown, duplicate and invalid-value diagnostics, and never
creates or mutates these bindings. They are available with `validate=False`.

## 3. Shape edges

| Edge | Created by | Meaning before P9 |
|---|---|---|
| `inherits` | mapping `type:` reference or `type:` sequence | child narrows parent constraints |
| `alias` | bare scalar reference | second declaration identity for one type |
| `link` | `type: !include` | included data-type declaration |

An alias keeps its own ID, name, authored location, and positions. After P9 it
shares the referent's kind fields and common-facet containers; it is not a
subtype. Its `alias` edge names the effective referent, which is the replacement
when the referent collapsed to a union member (§ 4). Traversals that need type
structure must follow aliases.

## 4. Unwrap and narrowing

`unwrap_shapes(raml)` iterates the flat `fragment_typedefs` index, recursively
reaches nested declarations, and rebuilds `raml.shapes` with effective shapes.
It is idempotent. Unwrapping a declaration may return a replacement base,
particularly when a union merge collapses; callers must use that return value.
P9 refreshes both declaration-name indices and typed fragments' root pointers
with replacement shapes, so a fragment and every inclusion site expose the same
effective root after a collapse.
P9 also replaces the binding index with one over the effective public shapes,
including facet values materialized while distributing union siblings.
Private copies used for validation do not publish binding changes. When no
consumer reads P7's index before P9, it is replaced without being materialized.
The index is a mapping, built once on first access, so ordinary parsing and
validation do not allocate editor references that nobody reads.
Its declaration sequences are read-only. Internally a single-candidate binding
stores the property directly; only ambiguous values retain a candidate tuple,
avoiding a one-element container per supplied scalar.

All parents are unwrapped before a child. Multiple inheritance folds parents
into a fresh shape (`fold_parents` in `types/inherit.py`), so merges cannot mutate or
share a parent container. The fold holds a declaration it took from one parent
by reference. When a later parent declares a like-named property, pattern
property or `items`, the two declarations are folded in turn rather than the
first being narrowed in place, which would write into the first parent.
A subtype's own declaration that is an alias of an object or array type
(`n1: Bar` over a parent's `n1: Foo`) is folded the same way: it shares
`Bar`'s containers (§ 3), so narrowing it in place would give the declared
`Bar` `Foo`'s properties. Custom facet values a merge adds are written to a
new dict for the same reason.
A subtype may only narrow, with one exception, an explicit property over an
inherited pattern (below, and [01](01-scope-and-coverage.md) § 4.6):

| Kind | Narrowing contract |
|---|---|
| common | inherit absent description; custom facets union with child values winning; enum is inherited or becomes a subset, compared with the semantic equality of enum membership ([10](10-validation.md) § 5); annotation target restrictions are inherited when absent and an explicit list may only narrow the parent's ([09](09-security-and-annotations.md) § B4) |
| string | increase `minLength`, decrease `maxLength`; child pattern replaces parent pattern; two parents' patterns conflict (below) |
| number/integer | increase minimum, decrease maximum, use a compatible `multipleOf` and format |
| datetime | format must agree |
| file | increase/decrease length bounds; each `fileTypes` entry is admitted by a parent's: itself, its `type/*`, or `*/*`, case-insensitively, whose parameters are a subset of its own; parameter names are case-insensitive, whitespace around `;` and quoting are ignored (`media_parts` in `parser/facets.py`) |
| array | recursively narrow `items`; tighten counts; a unique parent requires a unique child |
| object | recursively narrow shared properties and patterns; inherited patterns stand before the child's own (below); required cannot become optional; tighten counts; inherit absent `additionalProperties` and discriminator |
| union | merge compatible members as described below |
| any | contributes no constraint |
| json | only an identical schema can be inherited |

Different concrete kinds cannot merge. Unknown and recursive targets cannot be
inherited. A recursive source is compared through its head.

Where several parents meet as equals, a second `pattern` is an invalid type
declaration (*spec section Multiple Inheritance*: inheriting "a `pattern`
facet when a parent type already declares a `pattern` facet"), reported as
`conflicting pattern from multiple parents` with both patterns. That covers the
fold of a type's parents, the like-named property, pattern property or `items`
two parents both declare, and the variants a union among the parents expands
to (§ 5), with the asymmetry § 5 already has for any failed merge. In
`[A | B, C]` every member must merge with `C`, so one conflicting member makes
the declaration invalid. In `[C, A | B]` a member that conflicts with `C` is
dropped like any incompatible member, and only a union with no member left
fails, as `failed to find compatible union member`. Identical pattern text, or one
pattern two parents reached through a shared ancestor, is no conflict. A child
narrowing its one parent is not a meeting of equals: its own pattern replaces
the parent's, including a member of a union the child declares where its
parent declares a single type (§ 5).

A subtype's effective pattern properties list the inherited ones first, in
the parents' `type: [..]` order, then its own. A pattern of the same regex text
narrows the inherited one, as a like-named property does, and keeps the
inherited position. Within that order the first matching pattern prevails
(*spec section Property Declarations*: "If two or more pattern property
regular expressions match a property name ..., the first one prevails"), and
only it validates the value ([05](05-type-model.md) § 4). So a subtype's own
pattern governs only keys no inherited pattern matches: parent `//: string`
with child `/^n/: number` rejects `n1: 5` and accepts `n1: "s"`. An explicit
property prevails over every pattern, inherited ones included, and is not
compared with them, so parent `/^n/: string` with child `n1: number` accepts
`n1: 5`, which the parent rejects: the exception to "only narrow" above
([01](01-scope-and-coverage.md) § 4.6).

`example`, `examples` and `default` are not inherited. Each describes the
declaration that wrote it, and a subtype that narrows a facet or adds a
required property may reject its parent's values. An alias is not a subtype
and shares them (§ 3).

## 5. Union inheritance

When a non-union child inherits a union, P9 clones the child graph for each
compatible member and retains the survivors. No survivor is an error, one
survivor replaces the child, and several form a union. Examples, named examples,
and defaults written on the child remain on that resulting union, not its cloned
members.

When a union inherits a non-union, every member must narrow successfully. When
two unions merge, a memberless child adopts parent members; otherwise every
parent member must find a compatible child member. In both cases each variant
is a fold of the members it pairs, and no member is narrowed in place: a member
may be an alias sharing its referent's containers (§ 3), or one adopted from a
parent union.

A variant records the parents it took. The spec expands every union in a
type's hierarchy (*spec section Union Type*), so `type: [HasHome, Cat | Dog]`
has one variant inheriting `[HasHome, Cat]` and one inheriting
`[HasHome, Dog]`, and `type: [HasHome | OnFarm, Cat | Dog]` has four, one per
pair. Each union among the declared parents is replaced by the member of it
the variant took, in the declared order. The union itself keeps its declared
parents. A member narrowed by a property merge, which has no declared parents
to replace, inherits from the members it pairs.

A variant is anonymous. When a non-union child inherits a union, each variant
starts as a copy of the child, and the child's name, display name and
description are cleared from it, because they describe the union. A sole
survivor replaces the child and keeps them.

P10's custom facet walk (docs/10 § 4) follows a variant's parents, so a facet
`Dog` requires is required of the `Dog` variant, and one only `Cat` declares
is unknown to it.

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

An `enum` inside such a declaration follows the spec's union rule: every value
must meet all restrictions of at least one member (*spec section Union Type*).
Each member's copy keeps only the values that its own declaration at the same
place (the same property name, pattern, or `items`) validates. The subset rule
of § 4 then holds for every member. Giving each member the whole list would
break that rule for every member. Dropping the rule would let a member accept a
value its own type rejects, because validation stops at enum membership. Where
the member declares nothing at that place, it keeps every value.

- A value that no remaining member keeps is reported as
  `enum value matches no member of the union` with its `index`, at the value's
  position.
- A member left with no value in a non-empty `enum` is dropped from the union,
  and the values it kept no longer count as placed. The spec gives an empty
  `enum` no meaning. This also drops the member when the property is optional,
  so an instance that omits the property no longer matches that member.
- A member that is itself a union reports the values its own members kept to
  the enclosing union rather than raising, because a value that fits no nested
  member may still fit an enclosing one.

An `enum` written directly beside `type: A | B` stays on the union and is
validated against the union as a whole ([10](10-validation.md) § 3).

## 6. Recursion and cloning

After flattening, `finish_unwrap()` replaces every child edge that closes a
cycle with `RecursiveShape(head)`. It covers array items, object and pattern
properties, union members, and custom-facet declaration shapes. Validation of a
marker delegates to its head. A marker is placed where the edge it replaces was
written, so `next: Node` keeps the property's key position, not `Node`'s. Alias edges are resolved before recursion marking
so a shared alias container is not corrupted. A cycle reached through aliases
closes on the first shape along the alias chain that the walk is inside: with
`Chain: Link`, `next: Chain` under `Link` is headed by `Link`, never by the
anonymous copy, which has no address. A marker replaces the declaration in
its container, a `Property` or `PatternProperty` built by `with_base`, and
never edits it: a subtype shares its parent's declarations, and the marker
belongs to the parent's cycle. Recursive walks use the parse's shared depth
limit.

The walk visits a shape once per path to it, not once in all: whether a
marker is placed below a shape depends on which shapes are on the stack, so a
shape reached again is walked again. A type whose levels each hold two
properties of the next level's type therefore costs time exponential in its
depth (the `diamonds` workload, [12](12-performance.md) § 4). Each union's
dispatch table is still built once, however many paths reach it.

Within a type cycle the result depends on declaration order: the walk can
reach a type while it is still merging, and an alias, subtype or union member
reached then takes its fields as they stand ([15](15-implementation-plan.md)
§ 2).

Marking runs even when a declaration fails to flatten, before P9 reports the
failure, because `parse_lenient()` returns that model and its consumers walk it
(docs/11 § 2). A declaration the failure passed through, and every shape
enclosing or inheriting from it, is left unmerged: its `_unwrapped` flag is
cleared, it is added to `Raml.shapes` as it stands, and it is marked in
`Raml.broken` (docs/13 § 1). A second route to it during the walk fails the
referrer with the stored error, so the referrer is marked whichever route
came first, and nothing is reported again. A shape that reached it before it
failed, by closing a cycle back to it while it was still being walked or
through a finished shape that did, is marked after the walk in the same way,
until no more shapes change.

`clone(memo)` makes a structure-preserving copy keyed by `BaseShape.id`; cycles
and diamonds remain cycles and diamonds, and the clone retains IDs. A caller that
needs a fresh identity assigns one. `clone_detached()` uses a fresh memo for an
independent mutable shape graph, used by P10 private unwrap. A copy counts
every edge it follows, names included, against `max_depth`
([12](12-performance.md) § 3). Union merging
does not detach: a non-union child inheriting a union is copied with its
parents seeded into the memo, so they stay shared, and every other variant is
a fold (§ 4, § 5).
Scalar facets, data nodes, compiled patterns, and the `Raml` back-pointer are
intentionally shared because they are not mutated in place. `copy.deepcopy` is
not used.

Implementation: `types/resolve.py`, `types/unwrap.py`, `types/inherit.py`, and
`types/base.py`. Tests: `tests/unit/test_resolve.py`, `test_inherit.py`,
`test_unwrap.py`, and `test_clone.py`.
