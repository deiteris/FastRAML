# 12 - Performance

## 1. Performance contracts

The parser must remain linear in the size of its input graph. In particular:

- A source file is composed at most once and a fragment is decoded at most once
  per parse. Caches use canonical URIs, so equivalent paths share an entry.
- Endpoint templates are structurally merged as YAML nodes and materialized once.
  The merge does not deep-copy child nodes and preserves their identity for the
  provenance overlay.
- Shapes awaiting resolution are recorded in a worklist. Passes use registry
  indices rather than repeatedly traversing the completed model to rediscover
  declarations, annotations, or includes.
- `copy.deepcopy` is not permitted. `BaseShape.clone(memo)` preserves graph
  structure; `clone_detached()` is for an isolated mutable copy.
- Declaration order uses ordinary `dict` insertion order. Do not add an ordered
  mapping wrapper.

These are correctness properties as well as performance properties. A cache miss
can duplicate declarations; copying a node can lose its provenance.

## 2. Hot-path rules

- Every model class is slotted. Dataclasses use `slots=True, eq=False` where
  applicable; identity-based nodes must not acquire generated equality.
- YAML mapping content is stored as a flat alternating list. Hot decoders should
  index it directly instead of allocating tuples or temporary dictionaries.
- Allocate per distinct value, not per use. A childless `Node` shares one
  empty `content` list. A `BaseShape` with no parents, custom facets, facet
  declarations, annotations or type-expression references shares `EMPTY_LIST`
  and `EMPTY_DICT`, which raise on any in-place edit; a writer takes its own
  container through `owned` first, and a copy keeps an empty one shared
  ([05](05-type-model.md) § 1). `Node.position` is built once per node, and the
  composer takes short tags and line numbers from tables. Equal mapping keys
  share strings through a parse-local pool, never Python's process-global
  intern table. A global table resize can otherwise add fixed retained
  allocation to a small parse and make memory scaling depend on previous
  parses. `Raml` keeps one `ParseCtx` per anchor and target, and the scope
  managers decoders enter per construct are small classes rather than
  generators. Template application shares a trait's nodes by pointer, so
  every entity decoded from them shares their `Position`. Long-lived objects
  are what the cyclic GC re-scans, so each one avoided also shortens every
  later collection.
- Prefer compiled regular expressions and C-level string operations to
  per-character Python loops.
- Numeric validation keeps an `int` value an `int` (`as_exact`), so comparing
  it with an integer bound is one C operation. A float converts through its
  decimal text: `Decimal(repr(v))`, whose exact `as_integer_ratio` is half the
  cost of `Fraction` parsing the text.
- A `RamlError` renders its message only when it is read. Validating a union
  member that fails, or testing whether a member admits an enum value, builds a
  diagnostic nobody reads.
- Optional state stays optional: source retention, JSON Schema compilation,
  uncommon dependencies, and the pluralization dictionary are created only when
  requested.
- Regexes compiled by fastRAML use the selected `re` or `re2` engine. JSON Schema
  validation is external and is not controlled by `ParseOptions.regex_engine`.

## 3. Input depth

`ParseOptions.max_depth` is the one configurable ceiling for recursion driven by
user input. It is carried by `Raml.max_depth` and used by document composition,
type resolution, type unwrap and recursion marking, shape copies, and JSON
Schema processing.
Resolution counts the referents it resolves out of queue order on one path:
`T0: T1`, `T1: T2`, and so on, declared top-down. A declaration that error
passed through raises the same error on any later route, so a long chain costs
time linear in its length. Each guarded path
reports its own positioned diagnostic and includes the configured limit.

In a type graph, a level is a property, pattern property, array item, union
member, parent or facet declaration. The name a bare reference such as
`p: Node` uses is not a level, so a chain of types fails at the same depth
whichever end is declared first. Unwrap counts the names it follows on one
path separately, against the same limit, because each is still a frame.
A shape copy (`BaseShape.clone`) counts every edge it follows, names
included, because it copies the graph before anything is flattened. A chain of
named types therefore reaches half the levels in a copy. Two paths copy: P10
copies each declaration when `validate` is on and `unwrap` is off, and unwrap
copies a type that has a union among its parents, and the declarations written
beside a union, for each member (`types/inherit.py`, `types/unwrap.py`).

Do not replace a guard with reliance on Python's recursion limit. A user document
must receive a parser diagnostic rather than `RecursionError`.

## 4. Benchmark suite

`bench/` generates deterministic corpora and measures thirty workloads:

| Bench | Primary coverage |
|---|---|
| `small` | fixed parser overhead |
| `large` | large type/library graph and cache canonicalization |
| `endpoints` | template and endpoint construction |
| `extensions` | the `endpoints` corpus under an Overlay and an Extension: chain load, merge, overlay check, and document provenance |
| `validate` | declaration and example validation |
| `jsonschema` | shared JSON Schema references, and five examples per schema validated through them |
| `datatype-fragments` | canonical DataType and annotation fragment roots, repeated inclusions with colliding basenames, body uses, roots replaced by union collapse, and the shared graph, tree and position projections (docs/16 § 2 and § 6.1) |
| `schema-export` | project and export each JSON Schema in the `jsonschema` corpus as a standalone RAML document |
| `schema-allof` | project `allOf` into a graph: neutral members, numeric bounds and multiples, enums, required properties, array-item lengths and uniqueness, UUID formats intersected with length bounds, reversed member orders, cached numeric and recursive children, and diamond-shaped shared conjunction references ([10](10-validation.md) § 7) |
| `raml-schema` | export each effective RAML type in the `validate` corpus as a JSON Schema document |
| `projections` | OpenAPI use-site narrowing, closed-object members, unchanged and equivalently redeclared named uses, and aliases; JSON Schema explicit-property and ordered-pattern precedence and capture-scope loss reporting; and typed enum values in the reading renderer |
| `enums` | enum narrowing and enum membership at 5, 20, 100, and 1000 values, and `uniqueItems` examples at 10, 50, and 500 items, for string, integer, and number |
| `unions` | `properties` and `items` beside unions of 2, 4, and 8 members, flat and nested, with an enum each member narrows differently ([07](07-resolution-and-inheritance.md) § 5) |
| `facets` | custom facets declared up every parent of types that inherit from 2, 4, and 8 parents, and a diamond ([10](10-validation.md) § 4) |
| `inheritance` | a union of 2 and 4 members among a type's parents: after an object, first, and paired with a second union; and a property, pattern property and items property that every parent declares, folded on each merge ([07](07-resolution-and-inheritance.md) § 4 and § 5) |
| `diamonds` | chains of diamonds, each level two properties of the next level's type: a nested type reached by two routes at every level, so a walk that visits it once per path is exponential ([07](07-resolution-and-inheritance.md) § 6, [10](10-validation.md) § 1) |
| `templates` | resource types and a trait with parameters and transforms: a collection per resource and an item child, applied as real APIs apply them ([08](08-templates-and-endpoints.md) § 5) |
| `sequence-merge` | a trait's query-parameter `enum` of 5, 100, and 1000 values merged into the method's own, half of them shared: the structural merge's union by value ([08](08-templates-and-endpoints.md) § 1) |
| `template-scopes` | literal included library resource types using their own security scheme, a literal included trait with parameter annotations restricted to `TypeDeclaration`, and a standalone trait using a scheme from its own imports ([09](09-security-and-annotations.md) § A6, § B4) |
| `lenient-recovery` | shared recovery from unknown root/endpoint fields, malformed inline/included scheme definitions, and unknown security/annotation references, with sound types reaching resolution, unwrap and validation ([11](11-diagnostics.md) § 2) |
| `reference-namespaces` | substituted resource-type, trait, security-scheme, annotation and data-type names with caller/library collisions, forwarded nested arguments, and a static annotation name with a substituted value ([08](08-templates-and-endpoints.md) § 4.2) |
| `includes` | an example per resource from a `.json` and a `.yaml` file each: data includes, the header check, and the `.json` whitespace rule, one in four tab-indented and one in sixteen with a tab before `{`; and a Trait fragment per resource, whose header is read to tell it from content ([03](03-yaml-and-io.md) § 4.2) |
| `include-content` | each resource, each trait, and the `types:` map, written in a file of its own and included as literal content ([03](03-yaml-and-io.md) § 4.2) |
| `inline-json` | JSON-encoded strings in examples, enums, defaults, annotations, and custom facets; one decoding step per value ([03](03-yaml-and-io.md) § 6) |
| `annotation-targets` | included annotation restrictions, query strings and nested body declarations, literal and substituted template-root annotations, annotated substituted type names, and annotated default and discriminator scalars ([09](09-security-and-annotations.md) § B4) |
| `doc-links` | description-link resolution in the tree and lint ([16](16-graph.md) § 11) |
| `non-strict-examples` | lint warnings for single and included named examples with `strict: false`, with aliases and shared includes testing source-site deduplication ([18](18-linting.md) § 2) |
| `hover` | a cold language-service snapshot, fourteen authoring hovers per type family and custom-facet definition queries: inherited summaries, facet explanations, built-ins, custom-facet declarations and supplied keys, nested custom-facet and annotation keys and values, property presence, methods and response statuses ([21](21-language-service.md) § 4.2) |
| `effective-types` | a cold service snapshot, code-lens enumeration and full-depth RAML rendering for every named type and annotation type, including nested arrays, unions, scalar item constraints and recursive references ([21](21-language-service.md) § 4.3) |
| `inlays` | a cold snapshot, inferred declaration types, expected types at supplied custom-facet/annotation roots and nested data keys, and compact inherited constraints anchored to type references ([21](21-language-service.md) § 4.4) |
| `source-structure` | three folding requests and twelve selection requests at scattered keys, served from the workspace's composed source tree, composed once per current text ([21](21-language-service.md) § 4) |

The six general workloads are `small`, `large`, `endpoints`, `extensions`,
`validate` and `jsonschema`. The other twenty-five are feature workloads:
each exists because no general workload runs the code it covers. Their reach
tests (`tests/bench/test_corpus.py`) count calls or check bound results, and fail
if a corpus stops reaching that code at every size it covers.

A feature added to the language has no baseline on `master`, where the corpus
fails or skips the work. Measure it by linearity instead: at `--scale 0.5` the
time should halve.

Each workload supports `parse`, `unwrap`, `validate`, `unwrap+validate`,
`unwrap+graph`, `unwrap+lint`, `unwrap+occurrences`, which builds the
occurrence index ([16](16-graph.md) § 9), and `service`: one edit to the root's
buffer in the language service, run tuned as its host is, with the reparse, the
diagnostics and the occurrence index ([21](21-language-service.md) § 2). Like
every configuration's, its peak RSS includes tracemalloc's overhead, which
grows with the allocation, here nearly twice `unwrap+validate`'s. Without
tracemalloc, a run of edits on `large` settles at 75 MB. Corpus generation is outside the
timed region.
For `non-strict-examples`, `unwrap+lint` disables finding limits so retained
memory scales with all produced findings rather than a fixed output cap.
For `schema-export`, `unwrap` additionally exports each schema as RAML after
parsing; its other configurations keep their ordinary meanings. The reach test
guards both sides, so `parse` cannot accidentally measure the export.
`raml-schema` does the same for effective RAML types exported as JSON Schema.
For `projections`, `unwrap` additionally exports OpenAPI, exports every declared
type as JSON Schema, and renders each type as reading YAML.
For `datatype-fragments`, `unwrap` additionally builds the graph, tree and
positions; the tree reuses the graph's address map. Its reach test checks that
every shared root is projected and each inclusion references that root.
The small corpus-validity tests run in the ordinary test suite.
For `hover`, `unwrap` builds a service snapshot with unwrap and validation,
then queries every generated hover site. It measures the cold indices and their
reuse; probe selection and corpus generation are outside the measured region.
For `source-structure`, `unwrap` opens the corpus as a buffer and serves the
folding and selection requests from the workspace's composed source tree;
probe selection and corpus generation are outside the measured region.

```bash
python -m bench run
python -m bench run --bench large --scale 0.5
python -m bench baseline
python -m bench compare
python -m bench linearity
python -m bench startup
python -m bench micro [PATTERN]
```

`bench micro` times single leaf functions, such as `same_value`, enum
membership, the enum subset check, and `unique_items`, at sizes from 5 to 1000.
It also times public `validate()` per kind: string pattern, integer and number
bounds, dates, objects, pattern properties, arrays of objects, and unions with
and without a discriminator. No corpus validates such values, but a consumer
validates one per request ([17](17-consumers.md)). Use it when the question is
a function's constant factor, which a corpus dilutes. It looks up each target by name when it runs, so the same cases can
run against an older checkout. `tests/bench/test_micro.py` fails if a target
no longer exists in this tree.

Measurements run in fresh subprocesses. Wall time is the best of repeated
untraced runs; allocation tracing runs separately; RSS is a process high-water
mark. `bench/baselines.json` is fingerprinted by Python version, platform, and
YAML backend. A comparison across fingerprints is intentionally not a result.

## 5. Gates and local policy

CI does not compare absolute benchmark times or committed baselines. Its `bench`
job runs `tests/bench` with `FASTRAML_BENCH=1`. For `large` and each feature
workload, that test asserts that time and the allocation peak are both within
15 percent of linear against a half-size corpus. Linearity is portable;
absolute duration and RSS are not.

A performance claim must rest on a measurement that runs the changed code. For
a change to a hot path, or to any code a claim is made about:

1. Name the workload that reaches the changed code. If no workload does, add a
   feature workload and its reach test first. A general workload that never
   calls the code shows only noise.
2. Run `python -m bench ab BASE --bench NAME`. It alternates the base revision
   and this tree over one corpus. A time delta counts only if it is larger than
   the reported noise. Record the allocation delta whether or not the time
   moved, because it is nearly deterministic. A field added to every shape
   shows there and nowhere else.
3. Where the question is a leaf function's constant factor, add or run a
   `bench micro` case at sizes that cover the function's range.
4. For a new feature, the base revision has no comparable number. Run
   `python -m bench linearity --bench NAME` for time and memory instead.

Include the workload, both deltas, and the noise in the commit message.

A service workload's measured region is a cold snapshot: the parse, plus the
first query of each kind, then reuse of the built indices. Read its delta in
two parts. The parse side is where the base revision built and retained every
file's tree and the comparison does not. The first-use side is where a query
composes on demand the tree the base kept for free, one composition per file
per snapshot, about a third of a parse of the file; repeated queries of the
same kind are cached and show no delta. When a change moves composition from
parse time to first use, name which side the delta is on before attributing
it to the change's hot path (docs/21 § 2, docs/21 § 4).

`bench compare` against the committed baseline is only a coarse check for
large regressions: the baseline and the comparison run at different times, and
the machine drifts in between. Profile before optimizing: use `cProfile` for
call counts, `tracemalloc` for allocation attribution, and a wall-clock
profiler for elapsed-time evidence.

## 6. Garbage collection

A parse, a graph, a lint run, an OpenAPI export and an occurrence index each
build a large set of long-lived objects, and none of them creates cyclic
garbage: at 2000 resources no collection during a parse freed anything. CPython's young
collections scan only new objects, so their cost is linear. A full collection
re-scans every tracked object, and the heap grows throughout the operation,
so repeated full collections make the operation superlinear. At the default
thresholds they took 1.0 s of a 2.2 s `endpoints` parse at 2000 resources.

`fastraml.gctuning.tuned_gc` wraps each of those operations. It raises the
generation-2 threshold to 1000 and leaves the young thresholds alone, so
cyclic garbage is still collected promptly and nothing accumulates. The
alternatives measured worse:

| Setting, 2000 resources | Parse | GC time |
|---|---|---|
| default `700, 10, 10` | 2201 ms | 994 ms |
| `50000, 10, 10` | 1445 ms | 248 ms |
| `700, 10, 1000` | 1336 ms | 136 ms |
| collector disabled | 1198 ms | 0 ms |

A large generation-0 threshold promotes survivors to generation 1 in large
batches, which are expensive to scan, and still allows full collections.
Disabling the collector, or `gc.freeze()`, would stop the host's own cyclic
garbage from being collected. Python 3.14 measured the same. The free-threaded
build has no generations and ignores the setting.

A process hosting the language service stays tuned for its whole run:
`fastraml lsp` enters `tuned_gc` before it serves ([21](21-language-service.md) § 5).
What it discards is a whole model, which is cyclic garbage that only a full
collection frees. So the workspace runs one full collection before a parse
whenever it has dropped a snapshot ([21](21-language-service.md) § 2).

The collector's thresholds are process-wide, so `tuned_gc` follows these rules:

- It only raises the threshold. A host threshold already at 1000 or above is
  kept.
- It does nothing while the host has disabled the collector.
- Nested and concurrent operations share one change. The first to enter
  saves the host's thresholds, and the last to leave restores them.
- A threshold the host changed in the meantime is left as the host set it.
- `fastraml.set_gc_tuning(False)` turns tuning off for the process
  ([13](13-public-api.md) § 3).

The tuning is applied only where it measured a gain. The tree projection and
`backward` build little and are not tuned. Import-time or CLI-wide settings
are not used, because the host application owns its process.
