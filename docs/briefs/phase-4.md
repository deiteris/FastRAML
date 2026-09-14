# Phase 4 brief — inheritance and unwrap (P9)

A working brief for a fresh session. Read this, then the document it names. It
exists so you do not have to re-derive what earlier sessions already settled.

---

## 1. Where the project stands

Master is at the Phase 3 merge. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy fastraml/ && uv run pytest -q
# 1694 passed, 42 skipped
```

**Phases 0 to 3 are complete.** Phase 0: positions, errors, uris, loaders,
yamlnode. Phase 1: the fragment layer and the pass driver (P0–P3). Phase 2: the
whole `types/` package. Phase 3: `types/resolve.py` (P7) — every declaration now
has a concrete kind, and `inherits` / `alias` / `link` edges are recorded.

| Module | What you will use from it |
|---|---|
| `fastraml.types.base` | `BaseShape` (all 27 slots), `Property`, `PatternProperty`, `KindBase`, `Shape`, `ScalarFacet[T]`, `TYPE_*` |
| `fastraml.types.scalars` | the eleven scalar kinds and their facet fields |
| `fastraml.types.complex_` | `ObjectShape`, `ArrayShape`, `UnionShape`, `JsonShape`, `RecursiveShape` |
| `fastraml.types.shape` | `attach_kind`, `KIND_TO_CLASS` |
| `fastraml.types.resolve` | `resolve_shapes`, `resolve_shape` — P9's precondition |
| `fastraml.registry` | `Raml`: `next_id()`, `shapes`, `fragment_typedefs`, `put_shape`, `put_typedef`, `unwrapped` |
| `fastraml.errors` | `RamlError.new/.wrap`, `Accumulator`, `ErrorKind.UNWRAPPING` |

### 1.1 The seams you pick up

Five methods raise `NotImplementedError` naming this phase's doc section
(`grep -rn 'NotImplementedError' fastraml/types/`):

| On `KindBase` | Doc |
|---|---|
| `inherit(source)` | 07 § 3.5 |
| `alias_to(source)` | 07 § 3.6 |
| `clone(base, memo)` | 07 § 5 |

`check` and `validate` are Phase 8's — leave them raising.

Two fields exist and are never read: `BaseShape._unwrapped` and
`Raml.unwrapped`. `RecursiveShape` is defined and never constructed.
`ParseOptions.unwrap` is accepted and ignored; `entry.py` has the P9 slot
commented in the driver.

`link` is *resolved* but deliberately not rewritten to `inherits` — doing that
is the first step of unwrap, and Phase 3 left it alone on purpose
(`test_resolve.py::TestLinkedFragments`).

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding rules. Two are about this phase specifically:
   **never `copy.deepcopy`**, and `__slots__` on every model class.
2. **`docs/07-resolution-and-inheritance.md` §§ 3–5** — your whole
   specification. § 3.3 and § 3.4 are the two that break naive
   implementations; read them twice.
3. **`docs/02-architecture.md`** § 4 — invariant **I6**, which this phase
   establishes, and § 2's layering rules, which still hold.

Skim only: doc 10 § 2 (what validation will expect of an unwrapped shape),
doc 12 § 11 (why the DFS walks need an explicit stack), doc 15 Phase 4.

---

## 3. What to build

Doc 15's order is dependency order; keep it.

### 3.1 The three clone operations (doc 07 § 5)

First, because unwrap is defined in terms of them. `clone_shallow`,
`clone(memo)` and `clone_detached()`, plus `Shape.clone(base, memo)` on all
seventeen kinds.

The memo is keyed on `BaseShape.id`, not on `id()`. That is what makes a
diamond stay a diamond and a cycle stay a cycle — and this model has both.

`copy.deepcopy` is forbidden and the reason is not style: it would copy the
`Raml` back-pointer, the compiled `re.Pattern` objects and the YAML `Node`s.

### 3.2 Per-kind `inherit` and `alias_to` (doc 07 §§ 3.5–3.6)

The invariant across every rule: **a subtype may only narrow.** The table in
§ 3.5 is the specification; each row wants a test that names it.

Note what `BaseShape.inherit` does *before* dispatching to the kind:
`description`, `custom_facets` and `enum` live on the base, and `enum` is the
one with a real rule — the target's must be a subset of the source's.

go-raml guards re-entry with `sourceBase.ShapeVisited` and returns **the
source** when it fires (`shape.go` 140–146). That is not the same as the
resolution guard, which errors; get it right.

### 3.3 Multiple inheritance (doc 07 § 3.3)

The synthetic shape exists to prevent one specific corruption: merging parents
directly into the child lets the first `inherit` alias the first parent's
`properties` dict into the child, and the second `inherit` then mutates the
parent in place — for every other subtype that inherits from it. Doc 15's
done-criterion names this; write that test before the code.

`ArrayShape` needs the same treatment one level down, and the self-referential
`items` guard (`A.items is A`) is load-bearing: at this stage the cycle has not
been marked yet, so following it does not terminate.

### 3.4 Union interaction (doc 07 § 3.4)

Four cases, and the one that catches people is *source is a union, target is
not*: the target is deep-copied **per compatible member**, and the result is
one shape, a union of the survivors, or an error naming every member's failure.
`clone_detached` with a fresh id, not `clone` — these are genuinely new shapes.

### 3.5 `unwrap_shape` and the drivers (doc 07 §§ 3.1–3.2)

Iterate `fragment_typedefs`, which Phase 2 populated with every top-level
shape. No graph traversal at the top level.

`base.inherit(source)` may return a **different** object; callers must use the
return value. Doc 07 § 3.1 says so and it is the easiest line to skim past.

One thing doc 07 does not mention: go-raml's `UnwrapShapes` **clears
`r.shapes` and re-populates it** during the walk (`unwrap.go` 304–307), because
the old entries no longer describe the model. Decide deliberately whether to
copy that, and say so in the doc — `tests/tck/test_invariants.py` iterates
`raml.shapes` for I1, I4 and I5, so the choice is visible to those tests.

### 3.6 Recursion marking (doc 07 § 4)

Runs after unwrap, over the same roots. On re-entry it does **not** error — it
returns a `RecursiveShape` the caller substitutes into the slot it came from.
Substitution sites: `items`, `properties[*].base`, `pattern_properties[*].base`,
`any_of[*]`, and custom facet declarations.

The `_visiting` flag is cleared *before* descending into custom facet
declarations. A facet declaration may reference the type that declares it, and
that is not a recursion worth marking.

**Use an explicit stack or a depth guard, not Python recursion.** Doc 12 § 11:
a deeply nested schema will otherwise raise `RecursionError` instead of a
positioned `type nesting too deep`. `ParseOptions.max_type_depth` is 200 and
already exists.

---

## 4. Decisions already settled — do not re-litigate

1. **Unwrap is opt-in** (`ParseOptions(unwrap=True)`). The un-flattened model is
   what a formatter or a doc generator wants; flattening is lossy about where a
   facet came from.
2. **Unwrap is in place**, and idempotent. `_unwrapped` is set *before*
   recursing, which is what stops a cycle.
3. **`link` becomes `inherits` at the start of unwrap**, not at resolution.
   Phase 3 left it alone deliberately.
4. **An alias is not a source.** It is unwrapped and returned as is; `alias_to`
   copies facets and adopts the source's `inherits`, but the target keeps its
   own `name`, `id`, `location` and positions — that is the whole point.
5. **Never `copy.deepcopy`.** Three clone operations, chosen per site; the wrong
   one is a performance bug, the too-shallow one a correctness bug.
6. **Validation (Phase 8) depends on this phase's copies.** With
   `validate=True, unwrap=False` it clones each declared shape detached and
   unwraps the copy. Do not make the clones cheaper by sharing what unwrap
   mutates.
7. **`__slots__` everywhere still applies** to `RecursiveShape` and to anything
   new.

---

## 5. Reference source — read ranges, not files

go-raml is at `../go-raml-main`. Go is installed: when the question is what it
*does*, run it (`go test -run <name> .` against a throwaway `zz_*_test.go`,
deleted afterwards). Its comments have been wrong about its own behaviour.

| Need | File and lines |
|---|---|
| `BaseShape.Inherit`, the union cases | `shape.go` 140–260 |
| `AliasTo` | `shape.go` 290–310 |
| The three clone operations | `shape.go` 313–400 |
| `MakeRecursiveShape` | `shape.go` 560–573 |
| `UnwrapShapes`, the driver | `unwrap.go` 304–320 |
| `FindAndMarkRecursion` and its four substitution sites | `unwrap.go` 352–455 |
| `unwrapObjShape` / `unwrapArrayShape` / `unwrapUnionShape` | `unwrap.go` 456–580 |
| `unwrapParents`, `unwrapLink`, `UnwrapShape` | `unwrap.go` 580–709 |
| `makeMultipleInheritanceShape` | `unwrap.go` (grep the name) |
| Per-kind `inherit` | `complex.go`, `scalars.go` (grep `) inherit(`) |

---

## 6. Definition of done

From `docs/15-implementation-plan.md` Phase 4, made concrete:

- Every row of doc 07 § 3.5's table has a test that names the rule.
- A test proves a parent's `properties` dict is **not** mutated when two
  children inherit from it (§ 3.3's corruption bug).
- The four union × inheritance cases of § 3.4, including zero survivors and the
  collapse-to-one simplification.
- Recursion marking: `Node: {properties: {next: Node}}` unwraps to a graph whose
  `next` is a `RecursiveShape` whose `head` is `Node`.
- **Invariant I6** asserted over the corpus in `tests/tck/test_invariants.py`,
  beside I1/I4/I5: after P9 every reachable shape has `_unwrapped is True` and
  `link is None`. Its `corpus` fixture parses with a bare `ParseOptions()`, so
  I6 needs a second fixture that passes `unwrap=True` — do not change the
  existing one, since I4 and I5 are about the *un*-unwrapped model.
- `unwrap(unwrap(x)) == unwrap(x)` under hypothesis — idempotence is the
  property most likely to be broken by a later change.
- The TCK ratchet moves; **read the diff before committing it**. It stands at
  584 pass / 346 fail, and `tests/tck/test_tck.py:78` already parses with
  `ParseOptions(validate=True, unwrap=True)` — so P9 goes live for the whole
  corpus the moment it stops being a no-op, and unlike P7 it can move fixtures
  in *both* directions. Expect to have to read every regression.
- The full gate passes.

Unit tests: `tests/unit/test_inherit.py` (the rule sets) and
`tests/unit/test_unwrap.py` (the drivers, recursion marking, idempotence).

---

## 7. Scope boundary

Phase 4 flattens. It does not check or validate: `check()` and `validate()` stay
raising `NotImplementedError` naming Phase 8, and no example, default or enum
*value* is examined here — only the enum subset rule, which is an inheritance
constraint rather than a data check.

Likewise: no endpoints (Phase 5), no traits or resource types (Phase 6), no
security schemes (Phase 7). Unwrap rewrites fragment `traits`/`resourceTypes`/
`securitySchemes` maps to collapse `!include` indirection (doc 07 § 3.2), but
those maps are still `_raw_*` seams until Phases 6 and 7 — so that step has
nothing to do yet. Leave it, with a comment naming the phase.

---

## 8. Working method

Branch: `git checkout -b phase-4-inheritance`. One logical change per commit. If
the code must diverge from a document, **amend the document in the same
commit** — Phase 3 owed five such amendments and every one of them was a real
defect in the plan, not a formality.
