# 12 — Performance

go-raml parses **7124 types across 148 libraries in ~280 ms using ~48 MB**, and a
small project in ~4 ms using ~12 MB. AMF (TypeScript) takes ~17 s and ~870 MB on
the same input. Most of that gap comes from a small number of structural
decisions rather than from the language. Those decisions are language-independent,
and pyRAML adopts all of them.

This document lists every technique the reference implementation uses, states
whether it transfers to Python, and gives its Python form. It also identifies the
places where Go advice must be **inverted** for CPython.

## Part 1 — Techniques that transfer directly

### 1. Two-stage endpoint construction

*What:* directives are merged on YAML trees; type-bearing content is decoded
exactly once, after the merge ([08](08-templates-and-endpoints.md)).

*Why it is fast:* the alternative decodes a resource, decodes each applied
resource type and trait, builds model objects for all of them, then merges the
models and re-resolves types. For a resource with a resource type and three
traits that is 5 decodes and 4 model merges instead of 1 decode and 4 tree merges.
On go-raml's benchmark corpus this change alone is the single largest factor.

*Python:* identical. If anything the win is larger, because Python object
allocation is more expensive than Go's relative to tree pointer manipulation.

### 2. One compose per file, one decode per fragment

`Raml.include_nodes` and `Raml.fragments`, both keyed by canonical `file://` URI.

A project where 500 types each `!include common.raml` reads and parses that file
**once**. Without the cache the parse is quadratic in the number of references,
and diamond-shaped `uses:` graphs re-parse whole subtrees.

Canonicalising the key is part of the technique. `./a/../b.raml` and `b.raml`
must reach the same cache entry, or the cache misses on every alternate spelling.

### 3. Node identity as a key, not deep copies

The structural merge allocates new mapping/sequence nodes but **reuses child
pointers**. Consequences:

- merging a 200-line trait into 50 operations does not copy 10 000 nodes;
- node identity survives the merge, so the provenance overlay can be a sparse
  `dict[Node, ParseCtx]` instead of a parallel annotated tree.

*Python:* `Node` deliberately has no `__eq__`, so it is identity-hashable and can
be a dict key directly. Do not use `id()` as the key — it is only unique among
live objects.

### 4. The unresolved worklist

Shapes whose kind cannot be determined during decoding push themselves onto a
`deque`. P7 drains it. **There is no traversal of the model to find work.**

The YAML decoder already visited every node; recording the handful that need more
work costs one append each and saves a full second traversal of the type graph.
The same idea appears three more times:

| Flat index | Replaces |
|-----------|----------|
| `fragment_typedefs[uri] -> [BaseShape]` | walking the model to find every declared type (used by unwrap **and** validation) |
| `domain_extensions -> [DomainExtension]` | walking the model to find annotations |
| `include_refs[uri] -> [IncludeRef]` | re-scanning files for `!include` (tooling) |

### 5. Single-pass, allocation-lean decoding

- Mapping content is a **flat list** `[k0, v0, k1, v1, …]`; decoders iterate
  `range(0, n, 2)` with no tuple allocation.
- Unrecognised facets are collected into **one flat list** and handed to the
  concrete shape, which iterates it once. No intermediate dict per declaration.
- Type inference (`identify_shape_type`) is one pass over that same list with a
  module-level lookup table — no set construction, no sorting.

### 6. Lazy/optional side indices

`retain_source` (raw node trees + entity→node index) is **off by default**. A
validator or code generator never allocates them; an LSP turns them on. In
go-raml this is `OptWithRawSource`; the `store_entity_node` helper is a no-op when
the index is absent, so the call sites are unconditional and free.

### 7. Copy discipline

Three copy operations with different costs, chosen deliberately
([07](07-resolution-and-inheritance.md) § 5): `clone_shallow`, `clone(memo)`
(structure-preserving, so a diamond stays a diamond), `clone_detached`.

`copy.deepcopy` is banned: it would copy the registry back-pointer, the compiled
regexes and the YAML nodes, and it has no notion of "stop here".

Validation clones **only** shapes that are not already unwrapped, and caches the
result by the original's id — so `validate=True, unwrap=True` costs zero extra
copies while `validate=True, unwrap=False` costs one per declared type. The public
docs say which to use.

### 8. Flag-based cycle detection

`_visiting: bool` on the shape, set/cleared around the recursive step. No set
allocation per traversal, no membership hashing. The cost is that traversals are
not re-entrant across threads — which is fine, the parser is single-threaded by
contract, and that contract is documented rather than defended with locks.

### 9. Exact arithmetic without the slow path

go-raml uses `big.Int`/`big.Rat` everywhere for numeric facets, because
`multipleOf: 1.1` must not be a float.

*Python:* `int` is already arbitrary-precision, so integer bounds are free.
`Fraction` is exact but slow (every operation normalises via `gcd`). So:

- construct `Fraction` from **text**, never from a `float`;
- validation takes an `int`-only fast path first: if the value is an `int`, both
  bounds are `int`, and there is no `multipleOf`, do plain comparisons;
- only fall back to `Fraction` when a bound is non-integral or the value is a
  `float`/`Decimal` needing exact comparison.

### 10. Size-adaptive duplicate detection

`uniqueItems` uses pairwise comparison for ≤ 20 items (no allocation) and a
hash-bucket approach above, with full comparison on collision. Both branches are
kept; the threshold is the reference's and is not worth re-tuning without data.

### 11. Shared JSON Schema registry

One registry per parse. A `$ref` target shared by 40 schemas is fetched and
compiled once, and the converted RAML view of a compiled schema is cached by
schema identity. Without this, a schema-heavy project recompiles the same
definitions dozens of times.

## Part 2 — Where Go advice must be inverted

These are the places where copying go-raml literally would make the Python slower.

### 12. Do not write per-character loops

go-raml's `toUpperCamelCase`, `toUnderscoreCase`, `resourcePathName` and the
template-variable scanner all loop over bytes. In Go that compiles to tight code;
in CPython every iteration is bytecode.

**Rule:** prefer operations that run in C — `str.replace`, `str.split`,
`str.rpartition`, slicing, and **compiled regexes** — over Python-level character
loops, even when the loop looks "simpler".

Concretely:

- the RDT tokenizer uses one compiled alternation with `finditer`, not a char loop;
- `!upperunderscorecase` etc. use a single precompiled `re.sub` for the
  capital-letter boundary;
- `resource_path_name` uses `rstrip("/")` + `rpartition("/")` in a small loop over
  *segments*, not characters;
- `<<var>>` scanning uses `str.find` on `"<<"`/`">>"` — this one **is** already
  index-based in the reference and stays that way, because a regex that handles
  the `| !action` grammar is less readable for no measurable gain.

### 13. Ordered maps are free

go-raml carries `github.com/wk8/go-ordered-map` because Go maps are unordered and
the spec demands order preservation. Python `dict` guarantees insertion order.

Use plain `dict`. Do not port the ordered-map wrapper, and do not use
`collections.OrderedDict` (heavier, and its `__eq__` is order-sensitive in ways
that surprise).

### 14. Recursion depth

Go grows stacks dynamically; CPython's default limit is ~1000 frames and each
frame is expensive. Deeply nested schemas (JSON Schema conversions are the usual
culprit) will hit it.

**Rule:** any traversal whose depth is bounded only by user input uses an explicit
stack, or carries a depth counter and raises a positioned
`type nesting too deep` diagnostic before CPython raises `RecursionError`.

The traversals in question: `mark_graft`, structural merge, `Node`→`ValueNode`
conversion, `mark_recursions`, `unwrap_shape`, and JSON-Schema→shape conversion.
Of these, `mark_graft` and the value conversion are the easiest to make iterative
and the most likely to be deep, so they are iterative from the start; the rest get
a depth guard with a configurable ceiling (default 200).

### 15. Interfaces vs protocols

Go's interface dispatch is cheap. Python attribute lookup on an ABC with
`__getattr__` fallbacks is not. So:

- concrete shape classes define their methods directly; no ABC with default
  implementations that call back into the subclass;
- `Protocol` types are `typing`-only (structural, `runtime_checkable` avoided
  where a plain `isinstance` against a concrete class will do);
- the `Shape` "interface" is a Protocol for type-checking, and dispatch at
  runtime is ordinary method calls on concrete classes.

## Part 3 — Python-specific wins with no Go counterpart

### 16. `__slots__` on everything

Every model class uses `__slots__`. On a 7000-type corpus the parser allocates
on the order of 10⁵–10⁶ small objects; a `__dict__` per object is ~100 bytes of
pure overhead each.

Use `@dataclass(slots=True, eq=False, repr=False)` where a dataclass is
convenient. **`eq=False` is mandatory** for `Node` (identity hashing is required)
and desirable elsewhere: a generated `__eq__` on a recursive model is a
correctness hazard as well as a cost.

### 17. Interning hot keys

`sys.intern` the strings that become dict keys millions of times: facet names,
built-in type names, media types, HTTP method names, property names read from
YAML. PyYAML does not intern scalar values, so property names from a 7000-type
corpus are thousands of distinct-but-equal string objects. Interning turns dict
lookups into pointer comparisons and cuts memory.

Apply it in exactly one place — the `Node` converter, on mapping **keys** only —
so the cost is one `intern` call per key and the benefit is global.

### 18. Module-level lookup tables

Every dispatch that go-raml writes as a `switch` becomes a module-level `dict` or
a `match` statement, never a chain of `if key == "...":`. The tables:
`FACET_TYPE_HINT`, `COMMON_FACETS`, `TYPE_SPECIFIC_FACETS`, `INTEGER_FORMATS`,
`NUMBER_FORMATS`, `DATETIME_FORMATS`, `TEMPLATE_ACTIONS`, `HEADS`, `HTTP_METHODS`.

For per-facet decoding inside a shape, a dict of `{facet_name: method}` built once
per class beats a long `match` when the arm count exceeds ~8.

### 19. libyaml

`yaml.CSafeLoader` (libyaml) is roughly an order of magnitude faster than the
pure-Python composer. It is optional but strongly recommended, and selected
automatically:

```python
try:
    from yaml import CSafeLoader as _Loader
except ImportError:
    from yaml import SafeLoader as _Loader
```

The chosen backend is reported by `pyraml.backend_info()` so a user can tell why
their parse is slow.

**The backend is not purely a speed choice, and that is a defect.** The two
scanners do not accept the same documents: `title:<TAB>My API` — a tab where a
space would do — parses under libyaml and is rejected by the pure-Python
scanner. So a file can parse on one installation and fail on another.

Until that is closed, the mitigation is to run the suite under **both** backends
in CI, so the set of divergences cannot grow unnoticed. The scalar-resolution
half of the YAML layer is pinned by `tests/conformance`
([03](03-yaml-and-io.md) § 2.2); this is the syntax half, and it has no oracle
yet.

### 20. Expression AST cache

Type expressions are memoised on their text ([06](06-type-expressions.md) § 2.3).
`string`, `integer`, `object` and a handful of user types account for the
overwhelming majority of `type:` values in any real corpus; go-raml re-runs its
ANTLR parser for every one of them. This has no Go counterpart because ANTLR's
generated parser is not trivially memoisable — it is a straight win from writing
the parser by hand.

### 21. Deferred string formatting

Diagnostics carry structured `info` dicts and format at render time
([11](11-diagnostics.md) § 8). No f-string is evaluated on a success path.

## Part 4 — Budgets and measurement

None of the above is worth anything unmeasured.

### Benchmarks (ship with the repo, run in CI)

| Bench | Input | Measures |
|-------|-------|----------|
| `bench_small` | ~100 types, 1 library | fixed overhead |
| `bench_large` | generated: 7000 types, 150 libraries | scaling — mirrors go-raml's published corpus |
| `bench_endpoints` | 500 resources × 4 methods × 3 traits | the two-stage build |
| `bench_validate` | 1000 types each with a 50-key example | P10 |
| `bench_jsonschema` | 200 schemas sharing 20 `$ref` targets | the shared registry |

Each runs in four configurations (`parse only`, `+unwrap`, `+validate`,
`+unwrap+validate`) and records wall time and peak RSS
(`tracemalloc` for allocation counts, `resource`/`psutil` for RSS).

### Targets

- **Linearity is the hard requirement.** `bench_large` must be within 15 % of
  linear against a half-size corpus. A superlinear result means a cache is being
  missed and is treated as a bug, not a tuning opportunity.
- **Absolute time:** aim within 10× of go-raml on the same corpus with libyaml
  present. This is a goal, not a gate.
- **Memory:** peak RSS under 400 MB on `bench_large`.

### Profiling protocol

Before optimising anything not listed above: `cProfile` for call counts,
`tracemalloc` snapshots at pass boundaries for allocation attribution, and a
`py-spy` flamegraph for wall-clock. Optimisations land with a benchmark delta in
the commit message. Nothing in this document licenses a micro-optimisation that
is not backed by one of those numbers.
