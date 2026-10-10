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

`bench/` generates deterministic corpora and measures thirty-four workloads:

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
| `service-session`, `service-source-first` | representative mixed editor requests over three root-buffer versions: parser diagnostics, occurrences, outline, links, lens enumeration, 120-line viewport inlays, folding, three sparse hovers and warm queries; source-first additionally requests folding before each snapshot |
| `service-navigation` | three root-buffer versions, each with diagnostics/occurrences, six fixed sparse hierarchy preparations and parent/child queries, three typed-data definitions before hover, two outlines and two workspace-symbol searches |

The six general workloads are `small`, `large`, `endpoints`, `extensions`,
`validate` and `jsonschema`. The other twenty-eight are feature workloads:
each exists because no general workload runs the code it covers. Their reach
tests under `tests/bench/` count calls or check bound results, and fail
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
For `service-session` and `service-source-first`, `unwrap` runs the three-version
mixed request sequences of § 4.2; their other configurations keep their ordinary
parse/view meanings. Input texts and sparse probes are prepared outside measurement.
For `service-navigation`, `unwrap` runs the sparse three-version navigation
sequence. Requests are fixed as file width grows; only the latest workspace/model
and caches remain live, not request answers. Generation and probe selection are
outside measurement; there is no lint, source-only request or protocol conversion.

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

### 4.1 Describe the usage scenario before measuring

For a performance investigation, record the scenario before selecting a profiler
or proposing an optimization. Keep the description with the workload, or in the
dated report for an investigation using an existing workload. It must identify:

- **User operation and goal.** What the user waits for, or what memory the host
  keeps. State the acceptance criterion before interpreting results. Separate
  measured client behavior from a representative scenario whose request order or
  frequency is an assumption; do not invent an editor-wide average.
- **Input dimensions.** Bytes, files/roots, distinct shapes/nodes/edges, template
  applications, data width/depth and output size, as relevant. State which grow
  with scale and which stay fixed. A 300 KiB entry and 150 small libraries stress
  different ownership and invalidation paths even at equal total bytes.
- **Ordered actions.** For example: open, source-only folding, snapshot build,
  parser diagnostics, lint, outline, viewport inlays, one cursor hover, repeated
  selection, change, and rebuild. Name automatic versus explicit requests and
  whether a request arrives before the debounce expires. Use only the actions
  the scenario needs; the example is not a required request sequence.
- **Measured boundary and result lifetime.** State what setup is outside timing,
  whether collection, discovery, position conversion and protocol serialization
  are inside it, and which results remain live when memory is read. Distinguish
  server processing time from user-visible latency, which can also include
  debounce, queueing and transport. Include lint only when the scenario reaches
  that tier rather than cancelling it with another edit.
- **Cache lifecycle.** For every relevant cache, name its owner, key, population
  trigger, shared consumers, invalidation and release. Identify whether a changed
  dependency discards the entire root snapshot, whether unchanged files survive
  that boundary, and whether several roots retain separate copies. Check actual
  input/version and composition-policy compatibility (backend and limits), not
  just the URI: current buffer text and snapshot text can differ. Check whether
  returned views or old answers keep an evicted owner alive. Describe successful,
  empty and failed results separately where they change whether another request
  repeats the work.

“Cold” must name a boundary: process/import, workspace discovery, semantic
snapshot, file source/index, or presentation. A warm workspace can still hold a
cold snapshot after an edit; a warm snapshot can still receive the first query
for a library. Separate setup, snapshot rebuild, first-use construction and warm
queries, then measure the scenario's complete sequence. Warm is not synonymous
with free: folding can still traverse a cached tree, and returning hints still
costs work proportional to their output.

Trace the measured driver and its service calls against this description.
Reach tests must protect meaningful boundaries, not just the presence of a
function call: for example, one population shared by multiple consumers, a miss
after an edit, or no grammar construction for a model-only query. Use targeted
counters to check a disputed boundary before attributing a delta to it.

### 4.2 What the service workloads establish

The suite has both total-cost workloads and focused mechanism workloads. Read
the configuration as well as the corpus name:

| Workload/configuration | Measured scenario | Limit of the evidence |
|---|---|---|
| `large/service` (and other `service` rows) | One persistent workspace receives successive root-buffer comment edits; each measurement changes the text, collects discarded snapshots, rebuilds the root, reads parser diagnostics without lint, and builds occurrences. | Includes snapshot-side source capture on implementations that request it, but no hover, inlays, outline, folding, lint or protocol publication. Unchanged libraries are still part of each full reparse. |
| `hover/unwrap` | A new workspace and cold validated/unwrapped snapshot, followed by fourteen hovers per generated family and custom-facet definitions; answers and the snapshot remain live. | Combines parsing, first-use indices and bulk query reuse. It is coverage/load evidence, not the latency of one hover in an already parsed editor buffer or an observed hover frequency. |
| `inlays/unwrap` | A new workspace and cold validated/unwrapped snapshot, followed by a whole-file hint request; the snapshot and returned hints remain live. | Does not isolate first viewport latency, scrolling, range-cache reuse or a persistent edit/request mix. |
| `effective-types/unwrap` | A cold snapshot, lens enumeration, then full-depth rendering of every named type and annotation type. | Enumeration and explicit rendering are different user operations; an editor listing lenses does not necessarily render any type. |

The parked-branch cost review in `reports/2026-10-10/service-authoring-cost-review.md`
also describes diagnostic workloads specific to that implementation. They are
not part of this tree's benchmark suite or evidence of features shipped here.

These limits are not reasons to discard the workloads. They tell you which
question each can answer. A change to source ownership also needs a persistent
mixed-request scenario if the claim concerns editor use: exercise source before
and after snapshot creation, automatic requests and sparse cursor requests across
edits. Include dependency edits, literal includes and shared-root cases when they
reach the changed code. Add the missing workload and reach test rather than
inferring that cost from unrelated rows. `service-session` and
`service-source-first` supply this mix over the single-file hover corpus, with
and without source preceding semantics. Each version requests an outline, links,
lenses, viewport hints and folding, then three sparse hovers, a second outline,
hint and folding request and eight selections. Generation and edit-text preparation
are outside timing; workspace construction, collection and all requests are inside.
The returned workspace retains the latest model and caches, but request answers
and previous snapshots are released. These are representative service-core
sequences, not observed client traces; they exclude lint, protocol conversion,
transport and debounce. `tests/bench/test_service_session.py` protects their
rebuild and reuse boundaries. They do not establish multi-root or dependency-edit
costs.

Do not explain an implementation using a report's shorthand alone. Verify where
it composes, captures, interprets and presents source now. Reading captured
records directly is different from constructing a full node view; recomposing
text is different from building grammar and token indices over the result.
Attribute each to its actual population trigger and owner.

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
   shows there and nowhere else. A workload the base revision cannot run is
   reported as not comparable; one this tree cannot run fails the command.
3. Where the question is a leaf function's constant factor, add or run a
   `bench micro` case at sizes that cover the function's range.
4. For a new feature, the base revision has no comparable number. Run
   `python -m bench linearity --bench NAME` for time and memory instead.

Include the workload, both deltas, and the noise in the commit message.

`bench compare` against the committed baseline is only a coarse check for
large regressions: the baseline and the comparison run at different times, and
the machine drifts in between. Follow § 5.1 before optimizing and § 5.2 when
interpreting the comparison.

### 5.1 Investigate multiplicity before constant factors

1. **Establish the cost without instrumentation.** Use the scenario of § 4.1
   and an existing comparable workload, or add the missing workload and reach
   test. Identify the expensive phase with coarse elapsed-time boundaries. Keep
   parsing, first-use construction, warm queries and collection distinguishable;
   measuring only warmed queries can hide work moved into each reparse. Do not
   optimize a phase that has not been shown to matter to the stated goal.
2. **Form a falsifiable explanation.** Name the suspected work and its expected
   multiplicity. For example, “this file's source should populate once for these
   identical texts, but folding after hover composes it again.” Check the callers,
   input ownership and invalidation routes. A high call count or cumulative-time
   row is a question, not a diagnosis.
3. **Choose the measurement that answers that question.** Prefer a supported
   sampling profiler for elapsed-time attribution across the relevant scenario;
   check its ability to attribute native work such as libyaml and distinguish
   waiting from CPU work. Use scoped counters for compositions, node visits,
   index populations and cache hits/misses. Use `tracemalloc` snapshots for
   Python allocation attribution, and inspect live owners to explain retention;
   native allocation and RSS need separate evidence. Use scoped `cProfile` when
   Python call paths or counts are the unresolved question. It adds overhead per
   instrumented event and can exaggerate the cost of tiny, frequent accessors or
   wrappers. Do not choose an optimization from its timing ranking alone, or
   report its timings as wall-time improvements. If a suitable profiler is
   unavailable, use coarse timers, counters and source inspection and record the
   attribution limit.
4. **Check calls against the work that justifies them.** Normalize counts by the
   relevant files, distinct nodes, edges, materialized declarations, requests,
   output records or cache misses. Distinguish primitive calls from recursive
   totals and self time from cumulative time; nested cumulative rows overlap and
   cannot be added as independent costs. Trace the caller responsible for excess
   work before changing its callee. Check at more than one size when growth is in
   question; vary query count independently of input width, and root count
   independently of shared-file size, when both can multiply the work.
5. **Repair unjustified work first.** Look for a full-file scan per cursor, a
   full-model index rebuilt per request, visits per path through a shared graph,
   duplicate source owners, or repeated failed cache population. Some repetition
   is required: template materializations have separate semantic contexts, roots
   can bind a library differently, and output itself can grow. State that bound
   instead of removing necessary work. A million accessor calls can be legitimate
   over a million nodes, or evidence of repeated traversal over a thousand;
   making the accessor cheaper does not resolve the second case.
6. **Optimize the remaining constant factor only if it matters.** Once the
   multiplicity and cache boundaries are justified, use a representative
   microbenchmark for a leaf cost if needed. Bound the possible end-to-end gain by
   that phase's share of unprofiled time. Validate the change with uninstrumented
   A/B, allocations, reach/scaling checks and the correctness gate. A faster leaf
   is not an accepted optimization when its construction, retention or work in
   another phase makes the real scenario worse.

Instrumentation is diagnostic and runs separately from acceptance timing. Do not
run concurrent benchmark comparisons on the same machine or compare one side
with tracing/profiling to the other without it. Keep warmup and cache population
symmetrical, except where their difference is the change being measured; in that
case include both costs in the same user operation.

### 5.2 Compare behavior, phases and retained ownership

Compare identical inputs and the same observable work, not matching flag names
or internal representation. The A/B runner copies this tree's `bench/` to the
base checkout, so an API-specific driver can fail even when the base performs the
same user operation through another API. Inspect that distinction: absent
historical behavior has no base number; an unavailable interface may need a
behavior-equivalent driver. Never turn a failed or skipped worker into a speedup,
or omit costly work merely to make the older worker run.

For source/cache changes, report snapshot rebuild, first-use and warm-query costs
alongside the total request sequence. Parse-time capture is paid on rebuild even
when no query uses it. Lazy work is paid by the first consumer after its own cache
boundary, which may differ from the snapshot boundary. Repeated queries can
amortize that work within a version; an edit can make it recur. Eliminating one
composition does not establish a win if capture, value selection, wrapper reads
or index construction cost more. Likewise, a cold bulk-query regression does not
show which portion is parsing or first use without separate evidence.

Report memory at the points the scenario actually retains: after diagnostics,
after automatic editor queries, after optional cursor queries, and across
repeated edits where relevant. Idle text-only snapshots can save memory that is
spent again on lazily built source trees and indices. State which model, source
owners and answer lists remain live; a result retaining thousands of formatted
hovers is different from an editor discarding each answer. Keep traced peak,
post-collection retained Python allocation and process RSS distinct. A
single-build allocation result is not evidence of steady-state RSS or successful
release of old versions.

Keep three decisions separate: correctness, complexity and absolute-cost
acceptance. Linearity can pass while every edit becomes slower by a large
constant factor. A memory gain is a trade-off, not automatic permission for a
latency regression. State both costs and the goal they serve; do not change the
goal after seeing the winning row. Accept an architectural replacement against
the user scenarios and original relevant baseline, not only against an earlier,
already-regressed prototype. Record revisions, corpus/driver, configuration,
cache states, commands, time/noise, allocations and remaining measurement gaps
in the dated report. Once the goal passes and no new evidence exposes a problem,
stop rather than chasing the next profiler row.

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
