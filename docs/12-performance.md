# 12 — Performance

A RAML parser's speed is decided by a small number of structural choices, not by
its language. The evidence: on one corpus of 7124 types across 148 libraries,
go-raml takes ~280 ms and ~48 MB while AMF (TypeScript) takes ~17 s and ~870 MB.
Three orders of magnitude do not come from Go being faster than TypeScript.

This document names each of those choices, gives its Python form, and marks the
places where advice that holds for a compiled language must be **inverted** for
CPython.

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
validator or code generator never allocates them; an LSP turns them on. The
`store_entity_node` helper is a no-op when the index is absent, so the call sites
are unconditional and free.

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
kept; the threshold is go-raml's and is not worth re-tuning without data.

### 11. Shared JSON Schema registry

One registry per parse. A `$ref` target shared by 40 schemas is fetched and
compiled once, and the converted RAML view of a compiled schema is cached by
schema identity. Without this, a schema-heavy project recompiles the same
definitions dozens of times.

## Part 2 — Where Go advice must be inverted

These are the places where copying go-raml literally would make the Python slower.

### 12. Do not write per-character loops

Case conversion, resource-path naming and the template-variable scanner all
invite a loop over characters, and go-raml writes them that way. In Go that
compiles to tight code; in CPython every iteration is bytecode.

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
  index-based in go-raml and stays that way, because a regex that handles
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
stack, or carries a depth counter and raises a positioned diagnostic before
CPython raises `RecursionError`.

The traversals in question: `mark_graft`, structural merge, `Node`→`ValueNode`
conversion, `finish_unwrap`, `unwrap_shape`, and JSON-Schema→shape conversion.
Of these, `mark_graft` and the value conversion are the easiest to make iterative
and the most likely to be deep, so they are iterative from the start; the rest get
a depth guard with a configurable ceiling (default 200).

**One ceiling, not one per pass.** They all defend the same C stack, so one
number governs them: `DEFAULT_MAX_DEPTH` in `yamlnode.py`, surfaced as
`ParseOptions.max_depth`, carried on `Raml.max_depth`, and read at each guard.
It lives in `yamlnode` because that is the lowest layer needing it, not because
nesting is a YAML idea. Phase 9 reconciled three separate 200s into it; before
that, raising the option raised the type ceiling and left the document and
schema ones where they were.

Each guard keeps its **own message key**, so a document that trips one says
which traversal refused it, and every one of them carries the limit in `info`:

| Message | Pass | What it bounds |
|---------|------|----------------|
| `document nesting too deep` | P0 | YAML levels in one composed file |
| `type nesting too deep` | P9 | levels of `unwrap_shape` / `finish_unwrap` |
| `JSON schema nesting too deep` | P2/P7 | levels of a decoded schema, and of a `$ref` chain |

Three details that are not obvious and each cost something to find:

- **The document guard fires first for anything written inline.** One level of
  inline type nesting costs at least two YAML levels (`properties:` and the
  property name), so a 66-deep inline type already exceeds 200 document levels.
  The type guard is reachable only through a *flat* document whose declarations
  name each other — which is what the test for it has to generate.
- **The JSON Schema guard measures the document before anything walks it.**
  A 200-level schema exhausts the stack inside the schema library's own
  meta-schema validation, which is not a recursion this parser can guard from
  the inside; it surfaced as a raw `RecursionError`, which this section forbids.
  Measuring the decoded document is one iterative pass and makes the library's
  recursion, `_prefetch` and the § 6.3 projection safe at once. A `$ref` target
  is decoded through the same path, so a shallow schema cannot reach the stack
  by pointing at a deep one.
- **Documents visited and levels open are different counts.** `_prefetch`'s
  `seen` set stops a `$ref` cycle and never shrinks; a schema naming 300 distinct
  targets is ordinary and nests two levels. Comparing the size of that set
  against the ceiling rejects valid input, and a test says so.

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

The chosen backend is reported by `fastraml.backend_info()` so a user can tell why
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

### 19a. The composition callbacks

Profiling `bench_validate` — 1000 types, 50 properties and a 50-key example each
— puts **42 % of the whole parse in the YAML layer**, and almost none of it in
the places a reader would guess. Per *node*, 208 000 times:

| Was | Now |
|-----|-----|
| `Resolver.resolve`, a Python callback from libyaml, doing two dict lookups and `resolvers + wildcard_resolvers` — a fresh list per scalar, to concatenate one that is always empty here | `_RamlLoader.resolve`, specialised: one lookup, `value[:1]` folding in the empty-string case, no concatenation |
| `_short_tag` then `_check_local_tag`: three `startswith` calls to answer a question the first had already settled, since a tag in the standard namespace cannot be an unknown local tag | `_Converter._tag_of`, one pass |
| `_mark_position(node)`, a call building a 4-tuple the caller immediately unpacked | six additions inline |

Measured end to end: **-7 to -8 %** on `bench_validate`, -2 to -4.5 % on
`bench_large`, ~-1 % on `bench_endpoints`. The spread is the point — the win
scales with scalar density, which is what a per-node change should do, and is
how you tell it from noise.

Overriding `resolve` is safe only because the table has no wildcard (`None`) key
and no path resolver is registered. Both are asserted at **import**, not per
scalar: if a future PyYAML adds either, the short path would quietly stop
consulting it and a scalar would resolve to the wrong tag. `tests/conformance`
would catch that across the corpus; `TestSpecialisedResolver` catches it at the
function, by checking our `resolve` against `BaseResolver.resolve` over a table
of scalars in every `implicit` combination.

### 19b. Two hypotheses the profile refuted

Recorded because both are plausible, both were proposed, and the measurement
says no — which is worth more than the two paragraphs it costs.

**The date and time regexes are not a hot path.** `datetime.date.fromisoformat`
really is about twice as fast as `DATE_ONLY.fullmatch` (76 ns against 138 ns),
and `datetime.datetime.fromisoformat` beats `_RFC3339.fullmatch` 83 ns to 220 ns.
But composing the YAML scalar that carries the value costs ~3 µs — fifty times
the difference. On a corpus of 6000 date, time, datetime-only and datetime
examples, no date function appears anywhere in the profile, and the whole
substitution would be worth **0.09 %**. Against that, `fromisoformat` accepts
forms RFC 3339 does not, so the change would trade conformance for nothing.

**Replacing a regex with a Python algorithm is usually the wrong direction**
anyway; that is § 12's rule, and it applies to our own regexes as much as to
go-raml's byte loops. What the profile rewarded was removing *calls and
allocations* around the regexes, not the matching itself.

*Corollary for the corpora*: neither `bench_large` nor `bench_validate` contains
a date, so the suite could not have answered this. A benchmark suite is only
evidence about the code its corpora exercise.

### 19c. Allocation cleanup in flat-list consumers

A later audit found several consumers that defeated § 5's flat-list layout with
small temporary containers. `list += (key, value)` built a tuple for every pair;
some hot mapping walks used `pairs()` and therefore yielded a tuple for every
entry; and directive decoding converted those tuples into a list only to require
one entry. These paths now append nodes separately or index `Node.content`
directly. Trait priority uses a chained iterator instead of copying four lists.

Two success paths also allocated state they rarely needed. Ordinary data values
created an empty include-cycle set even when they contained no `!include`, and
the pairwise `uniqueItems` branch copied `items[:index]` on every comparison.
The set is now created on the first nested include, and the pairwise branch uses
indices. Both retain the same traversal and diagnostic order.

Measured against the preceding baseline on the same Windows / CPython 3.12 /
libyaml machine, the full suite's minimum-of-three parse times moved by -4.6 %
for `large`, -1.7 % for `endpoints`, and -0.5 % for `validate`. Endpoint traced
peak allocation moved from 34.6 MB to 34.5 MB; the other traced peaks were
unchanged because most removed objects were short-lived.

### 19d. Right-sized adjacency lists in the graph projection

`setdefault(key, []).append(value)` is the idiomatic way to fill a multimap and
the wrong one where most keys hold a single value. The empty list it inserts
grows to capacity 4 on the first append and occupies 88 bytes; `[value]` is
exact at 64. The graph's two adjacency indices are almost entirely singletons —
on `bench_endpoints`, **100 % of `incoming` (36 509 keys) and 73 % of `outgoing`
(18 002 of 24 505)** — so `_GraphSink.edge` writes the branch out instead.

Measured: traced peak for `build_graph` alone moved from 21.90 MB to 20.67 MB on
`endpoints` and from 24.93 MB to 24.23 MB on `large`, which is 54 505 singletons
× 24 bytes and matches the predicted 1.31 MB.

**It is not faster.** The intuition that `setdefault` wastes time building a
default it discards is wrong on CPython: empty lists come off a freelist and
cost almost nothing. An A/B of `build_graph` alone is 77.1 ms against 77.2 ms on
`endpoints` — inside the noise.

**Time the untraced run; trace the untimed one.** `tracemalloc`'s overhead scales
with allocation count, so leaving it running across a timed region reports any
allocation reduction as a speedup: it makes this change look 2.4× faster than it
is.

### 19e. A cheap name accessor on the graph nodes

`GraphNode.attributes` builds a fresh dictionary per call, by design (§ 2.8 of
[16](16-graph.md)): the projection derives its literals rather than storing them.
For a `TypeNode` that means projecting the shape, walking every facet its kind
declares, converting each to a literal, and relativising the path — and the two
commonest reads in the whole layer want **one key** out of it.

`Graph.find` compared `node.attributes.get('name')` once per node. Over the
36 510 nodes of `bench_endpoints` that is 30.1 ms against 2.6 ms for reading the
entity: **12x**, and the discarded work included 30 506 calls to `relative_to`
doing path arithmetic nobody asked for.

So `GraphNode.name` is a property, overridden per kind, and `attributes` now
reads *it* rather than repeating the expression. Measured, minimum of five:

| | before | after | |
|---|---|---|---|
| `find` on `endpoints` | 32.0 ms | 5.5 ms | 5.8x |
| `find` on `large` | 39.2 ms | 6.2 ms | 6.3x |
| `entries` on `endpoints` | 10.4 ms | 7.0 ms | 1.5x |
| `entries` on `large` | 28.7 ms | 15.6 ms | 1.8x |

`label` takes the same short cut: `name` is the first of the seven keys its
fallback chain tries and the answer for everything except an anonymous shape, so
it returns early and never builds the dictionary. The structural test has to run
first — a `Type` whose name merely repeats its own IRI segment is precisely the
case the chain exists to fall through.

**`diff` does not benefit and was not expected to** (168 ms before, 168 ms
after): `_altered` genuinely compares whole attribute dictionaries, and diff's
own hot spot is `_sides` at 41% of its time. That one is a reverse walk per
node, measured linear on both corpora — 2.08x, 2.10x, 2.10x across four
doublings — so it is a constant factor, not a complexity defect, and it is left
alone.

**A name is stated twice, which is the shape that rots.** The rule that holds it
together is that `attributes` reads the property; the test is
`TestNameIsTheCheapPathToTheSameAnswer`, which asserts the two agree for every
node in two documents and pins the kinds each exercises.

Six kinds do not spell `name` as a literal key and are the ones an override
misses: `EndPointNode`, `OperationNode`, `ResponseNode` and `ParameterNode` take
theirs through `_named`, and `DeclaredNode` and `UnresolvedNode` are bases the
module's own graph fixture never builds — which is why the test reads a second
document.

### 19f. The diff's side-of-the-wire label is carried down, not computed up

Grading a change needs to know which side of the wire the node sits on:
`note?: string` becoming `note: string` is breaking in a request and harmless in
a response, and the same edit therefore gets opposite verdicts
([16](16-graph.md) § 10.2). Containment points *downwards* — operation, request
or response, payload, schema, property — so the side is a fact about a node's
**ancestors**, and the obvious implementation asks each node to walk back
towards the API.

That is what `_sides` did, once per node, and it was **41% of a diff**: 36 510
independent reverse traversals on `bench_endpoints`, each allocating its own
frontier and `seen` set, all of them re-deriving the same ancestor chains. Every
property of a type re-walked that type's whole ancestry from scratch.

It is a union over parents, which makes it a forward propagation:

```
sides(n) = union over incoming e of ( side_of(e.predicate) | sides(e.subject) )
```

so one worklist over the edges settles every node at once. `_side_map` is
computed per graph and `diff` reads it.

| | per node | one pass | |
|---|---|---|---|
| `small` | 1.1 ms | 0.1 ms | 8.1x |
| `endpoints` | 59.6 ms | 20.1 ms | 3.0x |
| `large` | 75.0 ms | 11.7 ms | 6.4x |

**The mask is not an optimisation detail, it is what makes the pass worth
doing.** A set-based union — `carried | {side}` — builds two sets per edge and
costs 71.5 ms against the per-node walk's 62.5 ms, so the better algorithm loses.
The lattice has four states, so it is two bits: `carried | bit` allocates
nothing, and the four possible answers are interned and handed out by index.
Asymptotics do not survive contact with a per-edge allocation.

Cycles need no special case; a fixpoint over a four-state lattice stops
widening, where the walk needed its own `seen` set to terminate at all.

**The walk is kept, in the test suite, as the specification.**
`TestTheSideMapIsAForwardPropagation` asserts the two agree for every node,
including across a recursive type. A fixpoint that is merely plausible is
exactly the failure [16](16-graph.md) § 6.2 describes.

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

### 22. Deferred uncommon dependencies

`jsonschema` and `referencing` are imported only when a JSON Schema is compiled.
Most RAML files never declare one, and importing those packages accounted for
about 59 ms of a 136 ms package import on Windows / CPython 3.12. A
schema-bearing parse pays the same cost later, when it first needs the libraries;
ordinary imports and parses do not. The standard-library JSON decoder is
likewise loaded on first inline JSON value or JSON Schema rather than for every
library import.

The pluralization dictionary is also built only when `!pluralize` or
`!singularize` is applied. Those are two of ten template actions and cost about
4 ms to import and initialise even in a document with no templates.

The top-level package uses module `__getattr__` to load each public export on
first access and then caches it in the module. `__all__`, wildcard imports,
`dir(fastraml)` and ordinary attribute access retain their normal behaviour; the
parallel `__init__.pyi` exposes the same eager surface to type checkers without
executing it. The CLI similarly imports parser, graph and query modules only in
the commands that use them. On the same machine, median cold `import fastraml`
moved from 158.5 ms to 23.2 ms, and `fastraml --version` from 168.7 ms to 48.1 ms.
Importing the actual parse entry points remains about 96 ms, as expected: a
parse needs PyYAML and the model even though a bare package import does not.

### 23. Release and reuse narrow working state

P4 clears the API fragment's private endpoint-node buffer only after endpoint
materialization succeeds. The final model then owns the result without also
retaining the original endpoint YAML tree; a failed lenient parse keeps the
buffer with its other partial state. On `bench_endpoints` this reduced retained
traced memory from 30.6 MB to 22.3 MB.

Mapping merge removes matched keys from its existing source-value dictionary
rather than allocating a second target-key set. The same dictionary then answers
which source keys remain, preserving source order and duplicate source-only
keys. Measured end to end on `bench_endpoints`: -1.1 % wall time and -0.42 MB
peak traced allocation.

Two feature-specific walks use equally narrow state. The registry records only
shapes that actually wrote a discriminator facet, so the pre-P9 rule is
proportional to those declarations rather than to the whole reachable type
graph. The former full walk cost about 14 ms on `bench_validate` and 8 ms on
`bench_large`, both of which contain no discriminators. Graph projection caches
escaped IRI segments for the lifetime of one builder: the endpoint graph used
30,507 segments but only 518 distinct strings, and the cache reduced its
projection time by about 7 % without retaining anything across projections.

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

Each runs in five configurations (`parse`, `unwrap`, `validate`,
`unwrap+validate`, `unwrap+graph`) and records wall time and peak RSS
(`tracemalloc` for allocation counts, `resource`/`psutil` for RSS).

`unwrap+graph` measures the graph projection of [16](16-graph.md) on top of the
parse. Subtract `unwrap` from it to get the projection's own cost.

It is not a gate: the projection is a consumer rather than a pass, and no CI job
fails on it. It belongs in the harness anyway, because a number worth publishing
is a number the harness produced. The projection's cost was first reported from
a script that ran two benchmarks in one interpreter. That inflated the parse it
was compared against by 2.5x, and made the projection look like a quarter of the
parse when it is about two thirds.

Built in Phase 9 as the `bench/` package. `corpus.py` generates, `harness.py`
measures, `__main__.py` drives:

```bash
python -m bench run                          # every bench, every configuration
python -m bench run --bench large --scale .5
python -m bench baseline                     # record bench/baselines.json
python -m bench compare                      # fail on a >25 % regression
python -m bench linearity                    # the hard requirement, measured
python -m bench startup                      # cold package and CLI startup
```

The parse benches time work after imports. `startup` instead launches a fresh
interpreter for each repeat and reports both total time and time beyond a bare
Python process. It covers `import fastraml`, importing the parse entry points, and
the CLI's `--version` path.

Three implementation decisions the specification above did not settle, each of
which a simpler harness gets wrong:

- **Time and allocations are measured in separate runs.** `tracemalloc` hooks
  every allocation and roughly triples the wall time of a parse, which is
  allocation-bound. One run reporting both numbers reports one true number.
- **Wall time is the minimum of the repeats, not the mean.** Scheduling noise is
  one-sided. The minimum estimates the parser; the mean mostly estimates the
  machine. A `--repeat 1` run additionally measures the cold file cache, which
  on the 150-file corpus is worth more than the parse.
- **Each measurement runs in a fresh subprocess.** `ru_maxrss` is a process
  high-water mark, monotonic and never reset, so two benches in one interpreter
  cannot both report their own peak. The subprocess also stops a warm intern
  table or a filled expression cache from flattering whichever bench ran second.

RSS comes from `resource` on POSIX and from `K32GetProcessMemoryInfo` through
`ctypes` on Windows, rather than from `psutil`: the numbers are identical and it
keeps the dev dependency list where it is.

### Targets

- **Linearity is the hard requirement.** `bench_large` must be within 15 % of
  linear against a half-size corpus. A superlinear result means a cache is being
  missed and is treated as a bug, not a tuning opportunity.
- **Absolute time:** aim within 10× of go-raml on the same corpus with libyaml
  present. This is a goal, not a gate.
- **Memory:** peak RSS under 400 MB on `bench_large`.

**All three are met.** Measured at the end of Phase 9, Windows / CPython 3.12 /
libyaml, 7000 types across 150 libraries:

| Bench | parse | +unwrap | +validate | +both | alloc (parse) | peak RSS (parse) |
|-------|-------|---------|-----------|-------|---------------|------------------|
| `small` | 5 ms | 5 ms | 10 ms | 6 ms | 0.6 MB | 27 MB |
| `large` | 353 ms | 385 ms | 638 ms | 429 ms | 30 MB | 98 MB |
| `endpoints` | 336 ms | 349 ms | 439 ms | 363 ms | 35 MB | 114 MB |
| `validate` | 1074 ms | 1125 ms | 1523 ms | 1159 ms | 129 MB | 425 MB |
| `jsonschema` | 89 ms | 89 ms | 93 ms | 88 ms | 1.3 MB | 30 MB |

- Linearity: **1.040**, +4.0 % against a half-size corpus. Inside 15 %.
- Memory on `bench_large`: **98 MB**, against a 400 MB ceiling.
- Absolute: **5.1× to 10.3× go-raml** on the same corpora and the same machine.
  Inside the goal on `large`, on the line elsewhere.

#### The go-raml comparison, measured rather than quoted

go-raml's *published* ~280 ms is for its own 7124-type corpus on its own
machine. It is not comparable to a figure from this one and must not be quoted
beside one: doing so understates the gap roughly fourfold.

go-raml is checked out at `../go-raml-main` and Go is installed, so the figure is
measured now rather than quoted: the same generated corpora, the same Windows
box, **one process per measurement on both sides**. That last part is not
incidental — running nine configurations in one interpreter inflates the later
ones by up to 2× through heap pressure alone, which is why the harness spawns a
subprocess per cell and why a hand-rolled comparison that does not will overstate
the gap. Both parsers were checked to build the same model: 19 904 / 51 000 /
12 003 shapes, identical on both sides.

| Corpus / config | go-raml | fastRAML | ratio |
|---|---|---|---|
| `large` parse | 67.0 ms | 343.0 ms | 5.1× |
| `large` +unwrap | 70.0 ms | 366.4 ms | 5.2× |
| `large` +unwrap+validate | 71.2 ms | 419.9 ms | 5.9× |
| `validate` parse | 107.8 ms | 1073.6 ms | 10.0× |
| `validate` +unwrap | 113.9 ms | 1079.3 ms | 9.5× |
| `validate` +unwrap+validate | 116.3 ms | 1143.1 ms | 9.8× |
| `endpoints` parse | 37.0 ms | 329.5 ms | 8.9× |
| `endpoints` +unwrap | 36.6 ms | 376.5 ms | **10.3×** |
| `endpoints` +unwrap+validate | 38.0 ms | 374.9 ms | 9.9× |

The goal — within 10× on the same corpus — is met on `large` and sits on the line
elsewhere, with one cell over it. That is a materially different claim from
"1.5×", and the honest reading is that Parts 1–3 bought an algorithmically
comparable parser running at CPython's speed, not one competitive with Go.

Two asymmetries there are worth more than the headline. go-raml's `unwrap` and
`validate` are nearly free — +6 % on `large`, +8 % on `validate` — where
fastRAML's cost +22 % and +6 %. And the ratio is **worst on `endpoints`**, the
corpus built around trait application, which says the remaining distance is in
the two-stage merge rather than in the type system.

**Quoting a published number beside a local one is not a measurement.** Where a
comparison against go-raml appears in these documents it is run, per the rule
`AGENTS.md` already states for questions about its behaviour.

Two numbers in that table are worth reading rather than skimming.

**`+validate` alone is slower than `+unwrap +validate`** — 638 ms against
429 ms on `bench_large`. That is § 7's copy discipline showing up as a
measurement: `validate=True, unwrap=False` clones every declaration it checks,
and `unwrap=True` costs less than the clones it saves. It is the reason
[13](13-public-api.md) § 2 tells a caller to pass both.

It is also the whole of the "validation doubles the time and the memory" effect,
and it is worth being precise about, because the headline invites the wrong
conclusion. Validation *itself* is cheap: `+unwrap` is 385 ms and
`+unwrap+validate` is 429 ms, so P10 costs **44 ms — 11 % — and zero extra
allocations** (30.3 MB either way). The doubling belongs entirely to the private
copy taken when `unwrap=False`, which is one `clone_detached` per declared type:
allocations go 30 MB → 62 MB and RSS 98 MB → 170 MB. Both numbers are the
documented cost of a documented option, not a defect in P10.

**`bench_validate` peaks at 425 MB of RSS for 129 MB of traced allocations.**
The corpus is 1000 types × (50 properties + a 50-key example), so ~100 000 live
model objects and about the same number of `Node`s; the gap between the two
numbers is allocator arenas that were freed and not returned to the OS, not live
data. It is above the 400 MB figure, which is a `bench_large` ceiling and not a
general one — but it is the one bench where a memory question is worth asking
first if one ever arises.

### Profiling protocol

Before optimising anything not listed above: `cProfile` for call counts,
`tracemalloc` snapshots at pass boundaries for allocation attribution, and a
`py-spy` flamegraph for wall-clock. Optimisations land with a benchmark delta in
the commit message. Nothing in this document licenses a micro-optimisation that
is not backed by one of those numbers.
