# 07 — Resolution, inheritance, unwrap

Three distinct operations, often conflated:

| Operation | Pass | Question it answers |
|-----------|------|---------------------|
| **resolve** | P7 | What *kind* is this declaration? Which declaration does this name refer to? |
| **unwrap** | P9 (opt-in) | What does this type look like with all inheritance flattened? |
| **check / validate** | P10 (opt-in) | Is the declaration self-consistent? Does this data conform? ([10](10-validation.md)) |

## 1. Resolution: the worklist

During decoding, a declaration whose kind cannot be determined yet becomes an
`UnknownShape` carrying its undigested facet list, and the `BaseShape` is pushed
onto `Raml.unresolved_shapes`.

```python
def resolve_shapes(raml: Raml) -> None:
    acc = Accumulator()
    q = raml.unresolved_shapes
    while q:
        base = q.popleft()
        try:
            resolve_shape(raml, base)
        except RamlError as e:
            acc.add(wrap("resolve shape", e, base.location, base.key_pos))
    acc.raise_if_any()
```

These are **free functions in `types/resolve.py`, not methods on `Raml`**, which
is where the Go original puts them. `registry.py` imports nothing from `types/`
at runtime, and that is what keeps the import graph acyclic without indirection
(§ 3).

The queue is **not** a snapshot. Two things lengthen it while it is being read:
a type expression like `(A|B)[]` allocates anonymous inner shapes, and a kind's
declaration facets — an `items:` or `properties:` that had to be left undigested
while the kind was unknown — are only built once the kind is settled, so each of
those may be unresolved in turn. That is why it is a `while q:` over a `deque`,
not a `for` over a list.

The property that makes this fast: **there is no second traversal of the model.**
The YAML decoder already walked every node once; along the way it registered
exactly the shapes that need more work. Resolution touches those and nothing
else. A naive parser re-walks the whole type graph looking for unresolved
references; this is the single biggest structural win in go-raml's design after
the two-stage endpoint build.

### 1.1 `resolve_shape`

```
shape is not UnknownShape        → done (already resolved, possibly by a referrer)
base._visiting is True           → error: cyclic type reference
base.link is not None            → resolve the linked fragment's shape,
                                   then build a shape of its kind here
base.type == "composite"         → multiple inheritance: resolve each parent,
                                   then build a shape of parents[0].type here
otherwise                        → parse base.type as an RDT expression (cached)
                                   and run the AST builder (doc 06 §3)
```

`_visiting` is a plain boolean flag on the shape, set on entry and cleared in a
`finally`. No visited-set allocation per call. It detects *type cycles that go
through resolution* — `A: B` / `B: A` — which are genuine errors. Cycles that go
through a property (`Node: {properties: {next: Node}}`) are legal and are handled
much later, by recursion marking (§4).

Note the re-entrancy: the AST builder calls `resolve_shape(ref)` on a referenced
shape that may still be unknown, resolving it out of queue order. When the queue
later reaches it, the first check short-circuits. This is why resolution is
idempotent and why the queue may contain already-resolved entries.

## 2. Three kinds of edge between shapes

| Edge | Set by | Meaning | Survives unwrap? |
|------|--------|---------|------------------|
| `inherits: list[BaseShape]` | `type:` with sibling facets; `type:` as a sequence | subtyping — the child narrows the parents | flattened into the child |
| `alias: BaseShape` | `type:` as a bare scalar reference | "this *is* that type, under a new name" | resolved, facets copied |
| `link: DataTypeFragment` | `type: !include foo.raml` | an included declaration | rewritten to `inherits` |

`link` is rewritten to inheritance at the very start of unwrap:

```python
if base.link is not None:
    base.inherits = [base.link.shape]
    base.link = None
```

so the normal inheritance path applies and the chain stays visible to validation.
go-raml's comment: "An `!include` link is not inheritance at parse time, but the
linked shape is the semantic parent."

## 3. Unwrap

`unwrap` flattens the inheritance chain **in place**, producing a shape that
carries every facet and property it inherits. It is opt-in
(`ParseOptions(unwrap=True)`) because the un-flattened model is what a consumer
wants when it needs to see the declared structure (a formatter, a doc generator
showing "inherits from X").

### 3.1 Driver

```python
def unwrap_shape(self, base: BaseShape) -> BaseShape:
    if base.shape is None:
        raise ...
    if base._unwrapped:
        return base  # idempotent, cheap
    base._unwrapped = True  # set BEFORE recursing

    if base.link:
        self._link_to_inherits(base)
    if base.alias:
        return base.alias_to(self.unwrap_shape(base.alias))

    source = self._unwrap_parents(base)  # None | the merged parent
    self._unwrap_children(base.shape)  # items / properties / anyOf
    self._unwrap_custom_facet_defs(base)
    if source is not None:
        base = base.inherit(source)  # may return a *different* base
    self.shapes.append(base)
    return base
```

`base.inherit(source)` returning a different object is not a quirk to paper over
— it happens when a target inherits from a union and the result collapses to a
single member (§3.4). Callers must use the return value.

### 3.2 What gets unwrapped

Not the whole graph — the flat per-file index:

```python
for location, shapes in self.fragment_typedefs.items():
    for shape in shapes:
        self.unwrap_shape(shape)
```

`fragment_typedefs` was populated during decoding with *every* top-level shape:
declared types, annotation types, request/response bodies, headers, query
parameters, query strings, URI parameters, base-URI parameters. Nested shapes are
reached from their parents by `_unwrap_children`. No graph traversal, no visited
set at the top level.

Fragments additionally get their `traits`/`resourceTypes`/`securitySchemes`
definition maps rewritten so that a `!include`d definition is replaced by the
linked definition object — collapsing the indirection for consumers. Those three
are still undecoded `_raw_*` nodes until Phases 6 and 7, so P9 has nothing to
rewrite yet; the step belongs here rather than there, and is written when the
maps exist.

### 3.3 Multiple inheritance

With one parent, the parent is the merge source directly. With several:

1. Unwrap **all** parents first.
2. Allocate a **synthetic empty shape** of the first parent's kind, with its
   collection facets pre-initialised to empty (not `None`).
3. Fold each parent into it with `inherit`.
4. Use the result as the merge source for the child.

Steps 2 and 3 prevent a specific corruption bug. If the child merged its parents
directly, the first `inherit` would take the shortcut
`if target.properties is None: target.properties = source.properties`, aliasing
the first parent's `properties` dict into the child. The second `inherit` would
then mutate that dict in place, corrupting the parent for every other subtype
that inherits from it. Pre-initialising the collections to empty forces the merge
loop to run and prevents the aliasing.

`ArrayShape` needs the same treatment one level down. The synthetic shape gets a
synthetic `items`, built from the first parent that declares one. Skip any
self-referential `items` (where `A.items is A`): at this stage it has not yet
been replaced by a recursion marker, so following it would not terminate.

Spec § Multiple Inheritance also forbids inheriting from different primitive
kinds (`[number, string]`); that falls out of `inherit`'s kind check (§3.5).

### 3.4 Union interaction

Spec § Union Type and § Multiple Inheritance define the combinatorics. Three
cases in `BaseShape.inherit(source)`:

**source is `any`** → return the target unchanged. Nothing to narrow.

**source is a union, target is not**: for each union member whose kind matches the
target's, deep-copy the target, merge the member into the copy, keep the ones that
survive.
- zero survivors → `failed to find compatible union member`, with each member's
  failure attached as detail;
- one survivor → the target *becomes* that single shape (simplification);
- several → the target becomes a union of the survivors.

**target is a union, source is not**: merge the source into every member; any
member that fails makes the whole inheritance fail.

**both unions** → if the target declares no members of its own it takes the
source's outright, which is the same rule every other facet follows when the
child is silent about it. Otherwise, for each source member, merge it into each
type-compatible target member (on a detached copy) and keep the survivors; a
source member with no compatible target is an error.

The empty case is not an edge case to tidy away: `T: {type: SomeUnion, …}` gives
`T` the *union* kind, because P7 takes the referent's kind, but no `anyOf` of
its own. So a child that merely narrows a union reaches this branch, not the
"source is a union, target is not" one above — which arises only where the two
kinds genuinely differ.

Detached copies with fresh IDs are essential here — these are genuinely new
shapes, and reusing the originals would corrupt the declared types.

### 3.5 Per-kind inheritance rules

Common to all: a kind mismatch is `cannot inherit from different type`, with both
kind names in the message.

Before dispatching, `BaseShape.inherit` handles the facets that live on the base:

- `description` — inherited if absent.
- `custom_facets` — union, target wins per key.
- `enum` — inherited if absent; otherwise the target's enum **must be a subset**
  of the source's, else `enum constraint violation`. (Narrowing only.)

Then the kind-specific rules. The invariant across all of them: **a subtype may
only narrow.**

| Kind | Facet | Rule when both present |
|------|-------|------------------------|
| string | `minLength` | target ≥ source, else violation |
| | `maxLength` | target ≤ source |
| | `pattern` | target's pattern wins; two different patterns in *multiple* inheritance is an error (spec § Multiple Inheritance) |
| number/integer | `minimum` | target ≥ source |
| | `maximum` | target ≤ source |
| | `multipleOf` | target must be an integer multiple of source |
| | `format` | must be equal (by size class for integers) |
| array | `items` | recursive `inherit` |
| | `minItems` | target ≥ source |
| | `maxItems` | target ≤ source |
| | `uniqueItems` | source `true` requires target `true` |
| object | `properties` | per name: recursive `inherit`; **a required parent property may not become optional**; new names are added |
| | `patternProperties` | per pattern key: recursive `inherit`; new ones added |
| | `minProperties` | target ≥ source |
| | `maxProperties` | target ≤ source |
| | `additionalProperties`, `discriminator` | inherited if absent |
| file | `fileTypes` | target must be a subset of source |
| json | schema | inheriting from a *different* schema is an error; identical is a no-op |
| any | — | absorbs anything |
| recursive | — | cannot be inherited from |
| unknown | — | cannot be inherited from (means P7 was skipped — a bug) |

`RecursiveShape` on the source side is unwrapped to its head before the kind
check, so a property typed as a recursive back-reference still merges.

### 3.6 Aliases

An alias is a second name for one type. `alias_to(target, source)` gives the
target the source's `display_name`, `description`, `example(s)`, `inherits`,
`default`, `required`, `enum`, custom facets, custom facet declarations,
annotations and `xml`, plus every field of the kind object.

Two halves, and both are load-bearing:

- **The target keeps its own `name`, `id`, `location` and positions.** That is
  what makes it an alias rather than a rename: a diagnostic about `X` reports at
  `X`, and the property `next: Node` reports where `next` was written.
- **The contents are taken as pointers, not copies.** `X.shape.properties` *is*
  `Base.shape.properties`. There is one type here under two names, so a later
  change to the referent has to show through the alias; copying would let the
  two drift into two types a reader believes are one.

Sharing mutable containers between shapes is otherwise something this document
warns about — § 3.3's whole reason for a synthetic shape is to stop an
inheritance merge aliasing a parent's `properties` dict. The difference is that
inheritance produces a *distinct* type that then gets narrowed, so sharing there
is a latent corruption; an alias produces the *same* type, so sharing is the
specification.

The one place that has to know: recursion marking mutates these slots, so it
must never descend into an alias whose referent is already on the walk — see
§ 4.

## 4. Recursion marking

After unwrap, `Node: {properties: {next: Node}}` is an object that leads back to
itself. Any consumer that walks the model naively will recurse forever.

Not *literally* the same object. `next: Node` is a bare reference and therefore
an **alias** ([06](06-type-expressions.md) § 3.1): a base of its own, sharing
`Node`'s contents (§ 3.6).

**The DFS must resolve an alias before descending into it.** If `base.alias` is
already on the walk, return a marker headed by the *referent* and stop there:

```python
if base.alias is not None and base.alias._visiting:
    return make_recursive(base.alias)
```

Without it, the walk enters the alias and iterates the very `properties` dict it
is already inside — and the substitution it makes there lands in the referent's
dict, because they are one dict. For `Node: {properties: {kids: Node[]}}` that
replaces `Node.kids` with a marker, so the array it was declared as is gone from
the model and reachable only through `marker.head`. go-raml has no such check
and reports exactly that. It is the sharing that makes the mistake reachable,
but the sharing is not the mistake: copying the dict hides this one bug and
costs the propagation § 3.6 exists for.

`mark_recursions` runs a DFS from every declared type using the same `_visiting`
flag. On re-entry it does not error (unlike resolution) — it returns a
`RecursiveShape` that the caller substitutes into the slot it came from:

```python
class RecursiveShape:
    __slots__ = ("base", "head")  # head: the BaseShape the cycle returns to
```

Substitution happens in `items`, `properties[*].base`,
`pattern_properties[*].base`, `anyOf[*]` and custom facet declarations. Validation
of a `RecursiveShape` delegates to `head`, so behaviour is unchanged; only the
object graph becomes a DAG plus explicit back-edges.

One subtlety carried over from go-raml: the flag is cleared *before* descending
into custom facet declarations, because a facet declaration may reference the very
type that declares it and that is not a recursion worth marking (facets cannot
nest).

Python-specific: these DFS walks are the ones most likely to blow the 1000-frame
recursion limit on a deeply nested schema. See [12](12-performance.md) § 11 —
they use an explicit stack, or a depth guard that reports
`type nesting too deep` with a position rather than raising `RecursionError`.

## 5. Copying

Three copy operations with different costs; picking the wrong one is a
performance bug, and picking a too-shallow one is a correctness bug.

| Method | Copies | Use |
|--------|--------|-----|
| `clone(memo)` | deep, but **structure-preserving**: `memo: dict[int, BaseShape]` keyed by shape id, so a diamond stays a diamond and a cycle stays a cycle | the default deep copy |
| `clone_detached()` | `clone({})` — a fresh memo, so parents, links and aliases are copied too and the result shares nothing | union member merging; validating without mutating the declared model |

`copy.deepcopy` is never used: it would copy the `Raml` back-pointer, the compiled
regexes and the YAML nodes. A test asserts that no module in `pyraml/` imports
the `copy` module at all.

**There is no `clone_shallow`.** Earlier drafts of this table listed one, for
"swapping a shape's kind" — but P7 swaps a kind by building a fresh kind object
on the same base (`attach_kind`), so it never needs a copy, and no other caller
appeared. go-raml defines `CloneShallow` and calls it from nowhere outside its
own tests. Seventeen `clone_shallow` methods for an operation with no caller is
cost without a reader; add it when something needs it.

### 5.1 What a clone shares, and why

Only three things are copied: the `BaseShape`, the kind object, and the
containers unwrap mutates (`custom_facets`, `annotations`, `custom_facet_defs`,
`inherits`, and the property/items/anyOf children).

Every facet is shared by reference. A `ScalarFacet` is never mutated in place —
`inherit` only ever rebinds the field that holds one — so copying them would be
pure cost, and the compiled `re.Pattern` on a pattern property is immutable.

Per-kind `clone` is written out only for the four kinds that hold something a
copy must follow: `ObjectShape`, `ArrayShape`, `UnionShape` and
`RecursiveShape` (whose `head` is a back-edge, and so goes through the memo —
cloning it afresh would unroll the very cycle the marker exists to close). The
other thirteen use one implementation on `KindBase` that copies field by field
off `__slots__`. That is sound only because `__slots__` on every model class is
a project rule rather than a convention, so the field list cannot go stale.

### 5.2 Identity, and the one thing a clone rewrites

A clone **keeps the original's `id`**, which is what lets `memo` be keyed on it.
A caller needing a distinct identity assigns a fresh one from `Raml.next_id()`;
union member merging (§ 3.4) is the one that does. So `id` is unique per parse
among shapes the *parser* built, not among all shapes that exist.

A clone of a shape with a `link` comes out with `inherits` instead, exactly as
§ 2's rewrite would produce. go-raml instead shallow-copies the
`DataTypeFragment` so the copy can hold a cloned shape; here that would mean two
fragment objects for one file, which invariant I3 rules out. Since unwrap is the
only reader of `link` and its first act is this rewrite, doing it at copy time
costs nothing and keeps the fragment cache honest.

Validation (P10) uses this discipline: for each declared shape, if it is not
already unwrapped, `clone_detached()` then unwrap the copy, caching the result by
the original's id. That is how `validate=True, unwrap=False` gives a validated
model whose declared types are still un-flattened — at the cost of one copy per
type, which is why the two options are recommended together.
