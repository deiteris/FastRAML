# 14 — Testing strategy

Five layers, each answering a question the others cannot.

| Layer | Question | Size |
|-------|----------|------|
| Unit | does this function do what the doc says? | many, fast |
| Golden | does the whole model come out right for this input? | 10 cases |
| TCK | do we agree with the spec's own compliance kit? | 965 fixtures |
| Property | do the algebraic laws hold on generated input? | ~10 properties |
| Benchmark | is it linear, and how fast? | 5 benches |

Runner: `pytest` for layers 1–4. Layer 5 is a standalone `bench/` package driven
by `python -m bench`, for the reason § 5 gives.

## 1. The TCK is the primary compliance gate

`raml-tck` is the RAML organisation's compliance kit. It reaches this
repository as the **submodule** `tests/tck/raml-tck`, from
[deiteris/raml-tck](https://github.com/deiteris/raml-tck) — a **fork** of the
archived `raml-org/raml-tck`, whose fixtures it replaces with the customised
copy vendored in `acronis/go-raml`, plus three fixture corrections.

```bash
git clone --recurse-submodules <this repo>
git submodule update --init            # if you already cloned
uv run pytest tests/tck -q
```

The fixtures sit at `tests/tck/raml-tck/tests/raml-1.0/` — upstream's layout,
which the fork keeps. The submodule's own root holds its README, CONTRIBUTING
and the record of those corrections.

Forking rather than copying is what makes the divergence legible. Measured
against upstream, the go-raml fixture set is **63 files modified, 38 removed and
57 added** — not the "light customisation" its README claims. Every processor
using that copy was running a different suite from one using upstream, and
nothing recorded the difference; now it is a diff.

`FASTRAML_TCK_DIR` still overrides, for running against a different checkout.
With neither the submodule nor the variable, the TCK tests **skip** — an
uninitialised submodule is a checkout that was not finished, not a regression,
and the harness checks the directory is non-empty rather than merely present,
because an uninitialised submodule leaves an empty one behind.

**A submodule and not a vendored copy.** The licensing question that deferred
vendoring is not answered by pinning a commit, but it is contained by it.
Upstream was archived on 19 January 2024 and **states no licence**, so the
fixtures are referenced at a commit rather than copied into this tree, and
`[tool.hatch.build.targets.sdist]` excludes the directory so no release of this
package redistributes them.

It also fixes something the previous arrangement got wrong. CI fetched
`acronis/go-raml` and pointed `FASTRAML_TCK_DIR` at its `raml-tck/`, which
resolves to that project's **default branch** — and the three corrections the
ratchet is keyed to exist on no branch of it. CI was measuring inputs no local
run used. One pinned commit means a developer and CI see the same fixtures, or
the submodule diff says which commit each saw.

Convention (from its README):

- `*valid*.raml` → must parse, unwrap and validate without error;
- `*invalid*.raml` → must produce at least one error.

Current inventory, counted at the end of Phase 9: **495 valid** and **470
invalid** fixtures, 965 in total, out of 1172 `.raml` files — the remainder are
includes and libraries rather than entry points. They span Annotations,
EdgeCases, Examples, Fragments, Libraries, MethodResponses, Methods, Overlays,
Resources, ResourceTypes, Responses, Root, SecuritySchemes, TemplateFunctions,
Types and spec-examples.

Note when counting these yourself: `invalid` contains `valid` as a substring, so
a `*valid*` glob returns all 965. The harness filters the negative fixtures out
of the positive set, and a test asserts the two sets do not overlap.

### 1.1 Harness

```python
@pytest.mark.parametrize("fixture", collect_tck("valid"), ids=str)
def test_tck_valid(fixture: Path) -> None:
    opts = TCK_SPECIAL_CASES.get(fixture.as_posix(), DEFAULT_OPTS)
    parse_from_path(fixture, opts)  # must not raise
```

```python
@pytest.mark.parametrize("fixture", collect_tck("invalid"), ids=str)
def test_tck_invalid(fixture: Path) -> None:
    with pytest.raises(RamlError):
        parse_from_path(fixture, DEFAULT_OPTS)
```

`DEFAULT_OPTS` is `ParseOptions(unwrap=True, validate=True)` — an invalid fixture
frequently only fails at validation.

Two facilities go-raml's harness needs and so will fastRAML:

- **Offline network fixtures.** `Root/include-02/valid-https.raml` includes a
  resource-type fragment over HTTPS. It is run with a stub HTTP client that
  serves a fixed body for that one URL and 404s everything else, so the HTTPS
  code path is exercised without network access.
- **A skip list with reasons.** Fixtures for unimplemented areas
  (`Overlays/`, `Extensions/`, XSD external types) are skipped **by category
  prefix**, with the reason in the skip message. Every skip is a line item in
  [15-implementation-plan.md](15-implementation-plan.md); the list only shrinks.

### 1.2 The ratchet

A JSON file records the currently-expected outcome per fixture. CI fails when:

- a fixture that passed now fails (regression), **or**
- a fixture that was expected to fail now passes and the file was not updated
  (progress must be recorded).

The ratchet makes coverage progress verifiable rather than asserted, and it lets
the full TCK run from the first commit instead of waiting for the parser to be
complete.

**A `fail` entry means work outstanding, and nothing else.** The list is not a
place to park a fixture we disagree with: a disagreement is either a bug in the
suite, which gets fixed there, or a bug here. Three cases have come up, and each
was fixed at its cause rather than recorded:

- `Annotations/target-locations/valid-response.raml` declared
  `allowedTargets: Method` while applying the annotation under a `200:` key —
  a copy of `valid-method.raml` with the site changed and the declaration left
  behind. The spec's Target Locations table defines `Response` as "a declaration
  of the responses node, whose key is an HTTP status code". **Fixed in the
  fixture.**
- `Fragments/namedexample-01/examples/{invalid-one-example,valid-multiple-examples}.raml`
  are `!include` targets, not documents; the first is invalid only in the
  context of its parent. Named by the `*valid*` convention, a name-driven
  harness picked them up as entry points. **Renamed in the suite**, includers
  updated.
- `EdgeCases/overlay-overrides-resources/valid.raml` is an Overlay filed outside
  `Overlays/`. **Fixed here**: `skip_reason` now also matches on the RAML header,
  so an Overlay or Extension is skipped wherever it sits. That caught ten more,
  four of which had been *passing* — an unsupported-fragment-kind rejection
  scoring as a correct rejection of an `invalid-` fixture, which is credit for
  the wrong reason.

Skipping by header rather than by path is why the ratchet holds 915 of the 965,
with 50 skipped. The fiftieth is not a category: `spec-examples/APIs/external-
type-extend-invalid.raml` is a correct fixture that a deliberate deviation
contradicts (`SKIPPED_FIXTURES`, with D11 as its reason). It is skipped rather
than ratcheted to `fail`, because a `fail` entry means work outstanding (§ 1.2)
and a deviation is a decision.

### 1.3 Cross-checking against go-raml

**Not built, and not required.** The design was: a developer-only script runs
`raml validate --json` from go-raml and `fastraml validate
--json` over the same fixture, then diffs the trace chains, and each
disagreement is triaged as a fastRAML bug, a go-raml bug, or a documented
deviation ([01](01-scope-and-coverage.md) § 4). It needs a Go toolchain, so it
would not run in CI.

It was meant to be the fastest way to diagnose a TCK failure, and there are none
— the ratchet is 915 of 915. Every disagreement that did arise was settled by
running go-raml directly against a throwaway Go test, which is what `CLAUDE.md`
prescribes and which needs no script and no agreed output format. That is the
standing method; this section describes an alternative to it that was never
needed, and the decision is to leave it unbuilt rather than to keep it on a list.

Build it if the premise changes — a divergence that a single fixture does not
isolate, or a second implementation to diff against. Both halves it needs exist
(`fastraml validate --json` since Phase 9), so it stays a short job.

### 1.4 Differential conformance: the YAML 1.2 oracle

`tests/conformance` answers a question the TCK cannot: **does our YAML layer read
scalars the way YAML 1.2 says?** The TCK scores whether a document parses, not
what it parses to, so `example: 12:30:00` becoming the integer 45000 is invisible
to it — the fixture still passes.

The oracle is `ruamel.yaml` in YAML 1.2 mode (`typ='safe', pure=True`). Each
input is composed twice and the two node trees are compared on shape, tag and
text:

- a table of scalar forms in four syntactic positions — plain value, quoted
  value, mapping key, flow-sequence item — so a resolver change cannot fix one
  position and break another;
- structural documents covering includes, anchors, block scalars, flow
  collections and complex keys;
- **every** `.raml`/`.yaml`/`.yml` file in the TCK corpus, not only the entry
  documents: an included library is exactly where an odd scalar hides.

Rules that keep it honest:

- `KNOWN_DIVERGENCES` is keyed by reason, so an exception cannot be added without
  writing down why.
- `MAX_UNCOMPARABLE` bounds the documents neither side can compose. The TCK ships
  deliberately malformed fixtures, so it is not zero — but a change that quietly
  stopped comparing most of the corpus would otherwise look like a pass.
- Ruamel is a **dev dependency**. It never ships, and nothing outside this suite
  imports it. The pure-Python loader is required, not incidental: ruamel's C
  extension carries a pre-0.2.2 libyaml scanner that rejects
  `[ http://example.com ]`.

This suite covers resolution. The scanner half has no oracle; see
[12](12-performance.md) § 19 for the one known backend divergence.

## 2. Golden tests

For inputs where "no error" is too weak an assertion. Each case is a directory:

```
tests/golden/traits-merge-order/
  api.raml
  traits/paged.raml
  expected.json          # the serialised model
```

The model is serialised by a test-only walker that emits a stable, position-free
JSON projection (positions live in a separate `expected.pos.json` for the cases
that test positions specifically — otherwise a one-line edit churns every file).

`pytest --update-golden` regenerates. A regenerated golden must be read in the
diff before it is committed; the contributing guide says so.

The cases worth pinning as goldens are exactly the ones this design set argues
about:

- trait/resource-type merge order and the four priority classes;
- the provenance three-way example from [08](08-templates-and-endpoints.md) § 6.1;
- optional-method filtering and the deferred required-variable check;
- collection merge (`enum` union) producing `[mac, unix, win]`;
- unwrap of multiple inheritance, including the synthetic-shape aliasing hazard;
- union × inheritance expansion;
- recursion marking on self-referential objects, arrays and unions;
- default-type inference for every rule in spec § Determine Default Types;
- the `preference?` / `preference??` property-name corner cases;
- `securedBy: [null]` overriding an inherited scheme.

**Built**, as `tests/golden/`, with a case per bullet above. Each case is a
directory holding its own RAML and an `expected.json`; every case parses with
`unwrap=True, validate=True`, because a golden of an un-flattened model would
pin the declaration rather than the type, and the declaration is what the unit
tests already cover.

`tests/golden/project.py` is the walker. **It is driven off `__slots__`**, using
the same `copyable_slots` walk as `KindBase.clone`, for the reason docs/05 § 1
gives there: `__slots__` on every model class is a project rule rather than a
convention, so the field list cannot go stale. A facet added to a kind and not
wired in by hand would otherwise be invisible to the goldens — which is the
exact failure this layer exists to catch, so the layer must not have it.

Two things a reader of this section should know, both learned by getting them
wrong first:

- **A golden must contain the thing its case is named after.** The first draft
  projected endpoints as lists of parameter *names*, so
  `collection-merge-enum` — the case that exists to pin the spec's own
  `[mac, unix, win]` — asserted nothing at all, because the merged `enum` lives
  on the query parameter's shape. It passed, and it was cover rather than a
  test. The projection now carries parameter and body shapes in full.
- **`getattr(x, 'name', default)` in a projector turns a wrong field name into a
  plausible answer.** `SecurityScheme` has `compiled_params`, not `scopes`, so
  every OAuth scope narrowing projected as `[]` and looked deliberate.

The goldens are checked against mutation like the corpus properties (§ 4.2):
disabling sequence deduplication in the structural merge, and disabling
optional-method filtering, each turn them red.

Regenerate with `pytest tests/golden --update-golden`. **Read the diff before
committing it.** A regenerated golden accepted unread asserts whatever the code
happened to do that day, which is worse than having no golden, because it looks
like one.

## 3. Unit tests

Per-module, exercising the documented contract. The ones that matter most,
because they encode decisions rather than behaviour:

| Module | Pinned behaviour |
|--------|------------------|
| `yamlnode` | flat mapping content; identity hashing; 1-based positions; `!!timestamp` keeps raw text; duplicate-key detection; alias expansion budget |
| `uris` | Windows drive URIs; idempotent `path_to_file_uri`; backslash normalisation in references; `a/b/../c` == `a/c` |
| `loaders` | `SafeFileLoader` refuses `../` escape, symlink escape, non-regular files |
| `includes` | node cache hit count == 1 for N references; size limit off-by-one; scalar include for non-YAML extensions; circular scalar include |
| `references` | last-dot split; no namespace chaining; annotation→type fallback |
| `inference` | every rule and every conflict in § 4.2 of doc 05 |
| `expressions` | the full adopted expression corpus; cache identity; alias-vs-inherit discrimination |
| `structural_merge` | inputs unmutated; node identity preserved; opaque data facets not recursed; sequence dedup |
| `security` | one test per rejection rule of doc 09 § A2; the three inheritance levels; `securedBy: [null]` removing an inherited scheme; scope narrowing without touching the shared definition |
| `traits` / `resourcetypes` | the four priority classes; deduplication by name; optional-method filtering in both directions; which namespace a merged node resolves in |
| `templates` | the index survives a subtree being filtered out; all ten actions; the 618-row pluralization parity table generated from go-raml; unclosed `<<`; a substituted node is marked caller-scoped and a static one is not |
| `jsonschema` | a relative `$ref` resolves against the RAML file; an offline parse refuses a remote `$ref`; a shared `$ref` target is read once; a `$ref` inside a `default` is data; one test per error row of doc 10 § 6.3 |
| `inherit` | one test per row of the table in doc 07 § 3.5, both directions |
| `validate` | `bool` rejected as `integer`; `Fraction` exactness for `multipleOf: 1.1`; `uniqueItems` at n=20 and n=21 |
| `depth_guard` | one option raises all four ceilings; each guard's own message key; a deep type graph needs a *flat* document to be reachable at all; a `$ref` to a deep schema; 300 references are not 300 levels |
| `regex_engine` | `re2` is selected and used for facets, pattern properties and the § 6.3 projection; a backreference is accepted by `re` and refused by `re2`; validation *inside* a JSON Schema is not covered |
| `lenient` | the error equals a strict parse's, chain for chain; a decode-time failure still yields an entry point; the same failure in an included file is not fatal |
| `cli` | exit codes; diagnostics on stderr and nothing else there under `--json`; every file reported, not only the first; the reference trace shape survives |
| `deviations` | one class per D in doc 01 § 4 that has no more natural home — the `.xsd` message, the two numeric-format tables being disjoint, the 64 KiB default, declaration order |
| `public_api` | every name in `__all__` resolves; no concrete kind is missing from it; the docstring claims no phase |
| `errors` | wrap/append composition; `to_dict()` shape matches go-raml's |

## 4. Property-based tests

The laws. Section 4.1 records where each is checked and over what input —
`hypothesis` for some, the TCK corpus for others.

1. **Idempotence** — `unwrap(unwrap(x)) == unwrap(x)`.
2. **Merge identity** — `merge(t, None) is t` and `merge(None, s) is s`.
3. **Merge target-wins** — every key in the target survives the merge with its
   own value or a merge of it; no target key is lost. Note what this does *not*
   say: a target mapping recurses and a target sequence unions, so only a target
   *scalar* keeps its value untouched.
4. **Merge purity** — a deep structural snapshot of both inputs is unchanged
   afterwards. Alongside it, **node identity**: every scalar in the result is one
   of the inputs' own objects, because a copied one would silently drop its
   provenance mark and the merge would still look correct.

5. **Order preservation** — declaration order of properties, types, endpoints,
   methods and responses round-trips.
6. **Cache soundness** — parsing with a counting loader reads each file exactly
   once.
7. **Inheritance narrowing** — if `child` inherits `parent` and a value validates
   against `child`, it validates against `parent`. (Generated over scalar shapes
   with random compatible constraints.)
8. **Position sanity** — every entity's `key_pos` is within the file and
   `key_pos <= value_pos`.
9. **Determinism** — two parses of the same input produce identical golden
   projections.
10. **No `RecursionError`** — generated nesting up to `max_depth + 50`
    produces a positioned diagnostic, never a `RecursionError`.
11. **The two validation paths agree** — `validate=True` with and without
    `unwrap=True` reaches the same verdict on the same document. Not in the
    original list; added in Phase 9 because it is the only check that compares
    the private-copy path of docs/13 § 2 against the real one. Every other test
    picks a configuration and stays in it, so a copy that had diverged would be
    invisible — each configuration agreeing with itself.
12. **Every model that parses has a sound graph** — `build_graph` raises on no
    unwrapped model in the corpus; no projection returns nodes with no edges; no
    edge touches a node that does not exist; and no two **shapes** share an IRI
    ([16](16-graph.md) §§ 3.1–3.2). Shapes, not entities: a linked declaration
    registers the entry and the fragment it resolves to against one node on
    purpose, so a reference bound to either finds it. Only the first of those four is about
    crashing. The other three are the ways a projection is **wrong while
    looking right**: dropping every relationship, ending a traversal early at a
    dangling edge, or merging two types into one node. Each was found by the
    check that names it, and the last was found because go-raml's converter
    carries the same regression net.
12a. **The projection stays a projection** — one test per clause of
    [16](16-graph.md) § 1, in `test_graph.py`. A node stores only its IRI, its
    entity and the root; `attributes` is computed, never a field; an application
    edge lands on the declaration the pass resolved, not on a same-named one in
    another library. The clauses were prose for long enough to be violated three
    times without failing anything, which is why each now has a test that names
    it. The first is the one to keep: adding a stored field to a node class is
    how the layer becomes a second copy of the model, and it fails there.
13. **Every type and every endpoint has an effective view** — `render` raises on
    nothing the corpus declares, and its output **loads as YAML**
    ([16](16-graph.md) § 9). The second half is the one that rots quietly: a
    renderer that emits a key it forgot to quote produces something that looks
    right and will not parse. It has caught two, both of which the unit fixtures
    missed — `//:`, the spec's way to constrain every additional property, whose
    pattern is empty and so whose key was empty; and an explanatory `#` written
    inside a `securedBy` flow sequence, where it is a syntax error rather than a
    comment.

14. **One facet vocabulary for every emitter** — `facets_of` is the only
    enumeration of a kind's constraints, so a facet added to a kind reaches
    `render` and the graph's attributes without either being edited. Asserted as
    *agreement* between the two views, in `test_render.py`: the failure is one
    emitter growing a private list and drifting, which no test of either alone
    can see. Two copies of the walk existed, and one carried an exception table
    every entry of which was already what plain camel case produced — dead code
    kept alive by nothing being able to see the other copy. Writing the law
    found a live gap as well: a `pattern:` on a type reached no node attribute,
    so `diff` reported no change when one was tightened.

15. **Every reference the effective projection emits resolves** — and resolves
    to the node the graph put at the same address ([16](16-graph.md) § 11.2), in
    `test_tree.py`. This is the check on the addressable set: a reference to
    something the walk never reached comes out as `null`, which reads exactly
    like "there was nothing to point at". The projection itself is pinned whole
    by the golden layer, which cannot see this — a golden agrees with whatever
    was generated, including a `null` where an address belonged.

16. **A documentation view has what a reader needs** — one test per item in
    [16](16-graph.md) § 11.4, in `test_tree.py`. Each was absent from every
    view, and none of them failed anything: a key that is not emitted looks
    exactly like a document that did not say it, so only a checklist written
    against what a renderer *renders* finds them. Six were found that way at
    once — `baseUriParameters`, `documentation`, a resource's own
    `displayName`/`description`, a scheme's `settings` and `describedBy`, and
    annotations attached to the site they were applied at rather than only to a
    document-wide list keyed by *kind*.

17. **A naive consumer terminates** — `test_consumer_traversal.py`. A walker
    with no ancestor set, no depth budget and no RAML knowledge descends
    containment and stops at a recursion marker; every shape of cycle — self,
    mutual, three-deep, through an array, through a union — is broken by one.
    This is the executable form of [16](16-graph.md) § 11.7, and the reason
    `unwrap=True` is the contract rather than a convenience. It carries one
    strict `xfail`: an alias is parser machinery that reads as a wrong answer
    (§ 11.8), and the law records that rather than hiding it.

18. **The views cannot reach into the passes' path** — `test_views.py`. Nothing
    under `parser/` or `types/` imports `fastraml.views`, and outside that package
    only `cli.py` does. Asserted over the import graph rather than by review:
    the layer was described as one-way in [02](02-architecture.md) § 2 and in
    `CLAUDE.md` long before anything could fail when it stopped being. An import
    the other way does not break a test on its own — it takes a rule about the
    language landing outside the pass order, months later, for the cost to
    show. The same file pins `__init__.pyi` against the lazy export table, which
    had drifted by five names: a stub that omits an export leaves it resolving
    at runtime and failing to type-check, so nothing in either suite sees it.

19. **Nothing arrives undeclared** — every key the projection emits over the
    corpus is in the generated contract ([16](16-graph.md) § 11.11), in
    `tests/tck/test_properties.py`. The generator reads source, and reading
    source is a hypothesis about what running it does; only output settles it.
    It earned its place immediately: it found `JsonShape` projecting its
    compiled validator — with an absolute path inside the `repr` — and found
    three keys the generator had dropped because two loops in `shape()` share a
    variable name. `tests/unit/test_bindings.py` asks the same of one document
    declaring every kind, so the check still runs without a TCK checkout.

### 4.1 Where each law lives, and why

| Laws | Where | Input |
|------|-------|-------|
| 2–4, node identity | `tests/property/test_merge_laws.py` | hypothesis |
| 1, 7 | `tests/unit/test_unwrap.py`, `tests/unit/test_validate.py` | hypothesis over a table of declarations |
| 10 | `tests/unit/test_depth_guard.py` | constructed, one case per guard |
| 5, 6, 8, 9, 11, 12, 13 | `tests/tck/test_properties.py` | **the corpus** |

The last row is a deliberate substitution for the hypothesis generator this
section originally called for. A generator writes the documents someone thought
to describe; the corpus holds the ones people actually wrote, including the
awkward ones nobody would think to generate. For a law that is a *comparison* —
parse it twice, parse it two ways — the corpus is both stronger and cheaper.
Laws needing input nobody wrote down, like the merge algebra, still need a
generator.

Laws 6 and 8 are checked in the form that fails **silently**, which is not the
form the sentence above suggests:

- Law 6 as **canonicalisation** — no file reachable under two URIs. A cache miss
  from `./a/../b.raml` not matching `b.raml` decodes the file twice and gives one
  declaration two shape identities; the parse still succeeds, and the model is
  quietly wrong. The counting loader in `test_includes.py` covers the other half.
- Law 8 as **positions inside their own file**. Every other test asserts on a
  diagnostic's message, so a position that is merely plausible — 1-based, wrong
  line — is invisible to all of them.

### 4.2 These are checked against mutation

A corpus test asserting "no offenders" passes just as quietly when the loop
iterates nothing or the comparison can never be non-empty.
`TestTheseChecksSeeSomething` pins that the fixtures are populated, that both
verdicts occur, and that law 11's one permitted difference is a live branch
rather than dead code.

Beyond that, each was confirmed to go **red** under a mutation of the parser
that breaks the property it watches — a position shifted past end of file, the
declaration order reversed, an ordering made to vary per parse, and the
validation copy stripped of its `inherits`. A green suite is evidence only if it
can go red.

## 5. Benchmarks

As specified in [12](12-performance.md) § Part 4, and built as `bench/`. They run
in CI on every PR with a generous threshold (fail at >25 % regression against the
stored baseline) and nightly with the full corpus and RSS measurement.

`bench_large`'s 7000-type corpus is **generated** by a script in the repo, not
vendored, so it stays a few kilobytes of source and can be regenerated at other
sizes to check linearity.

**Not `pytest-benchmark`.** Two of the three measurement decisions in
[12](12-performance.md) Part 4 are outside what it does: peak RSS needs a fresh
process per measurement, and corpus generation has to sit outside the timed
region rather than inside a fixture. A standalone runner does both and needs no
dev dependency.

Two things nevertheless run under `pytest`, because they are assertions rather
than measurements:

| File | Gate | When |
|------|------|------|
| `tests/unit/test_graph.py` | the graph projection: IRI stability, the edges that answer the questions it exists for, and both RDF serialisations **checked by a real RDF parser** ([16](16-graph.md)) | always; the RDF cases skip without `pyoxigraph` |
| `tests/unit/test_tree.py` | the document as containment: every reference resolves to the node the graph put at the same address, and a reader has what it needs ([16](16-graph.md) § 11) | always |
| `tests/unit/test_views.py` | the layer boundary: no pass imports a view, and the stub matches the export table ([02](02-architecture.md) § 2) | always |
| `tests/unit/test_render.py` | the effective view of a type and of an endpoint: everything merged in is present, each item attributed to the declaration that really supplied it, output loadable as YAML ([16](16-graph.md) § 9) | always |
| `tests/unit/test_diff.py` | the change list and the backward-compatibility rules, above all that **the same edit reads differently on each side of the wire** ([16](16-graph.md) § 10.2) | always |
| `tests/unit/test_queries.py` | every catalogue query is valid SPARQL, **returns rows on a fixture written to trigger all of them**, and answers the right question ([16](16-graph.md) § 6) | always; skips without `pyoxigraph` |
| `tests/bench/test_corpus.py` | every generated corpus is valid RAML in **all four** configurations, generation is deterministic, and `bench_large`'s diamond really does reach one `common.raml` | always; tiny scale, milliseconds |
| `tests/bench/test_linearity.py` | `bench_large` within 15 % of linear against a half-size corpus | `FASTRAML_BENCH=1` only |

The first of those is not ceremony. The first draft of `write_small` had a
required property its own example omitted: `parse` and `unwrap` were happy, and
the two configurations that exercise P10 were quietly measuring an exception.

Absolute wall-clock is **not** asserted in any test. It is a property of the
machine; it is recorded in `bench/baselines.json` against a fingerprint
(interpreter, platform, YAML backend), and `python -m bench compare` declines to
compare across a fingerprint change rather than reporting a "regression" that is
really a different computer. CI records its own baseline per matrix cell.
Superlinearity is the exception, and is asserted: it means a cache is being
missed, which is a bug on every machine.

## 6. CI matrix

| Axis | Values |
|------|--------|
| Python | 3.12, 3.13 |
| OS | Linux, Windows (path/URI handling differs materially) |
| YAML backend | libyaml **and** pure-Python. Not optional: the two scanners already disagree on `title:<TAB>value` ([12](12-performance.md) § 19), so a libyaml-only run would ship that divergence. |
| Regex engine | `re` always; `re2` in one job (`uv sync --all-extras`) |

Plus, on every PR: `ruff check`, `ruff format --check`, `mypy --strict fastraml/`,
the TCK ratchet, and `tests/bench` with `FASTRAML_BENCH=1` for the linearity gate.

The `re2` job installs `google-re2` rather than merely allowing it. Its tests
`importorskip`, so without a job that installs the package the whole option is
tested by reading it — the failure mode of every optional dependency.

## 7. What is deliberately not tested

- The `xml:` facet beyond "it parses and round-trips" — nothing consumes it.
- Exact error *message text* outside the `errors` module; tests assert the
  message key and the `info` dict, so rewording a message does not break 50
  tests ([11](11-diagnostics.md) § 6).
- Third-party JSON Schema draft conformance — that is the schema library's
  test suite, not ours. fastRAML tests only the integration: shared registry,
  `$ref` through the sandboxed loader, and the shape projection.
