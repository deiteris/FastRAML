# 18 — Linting

A parser answers *is this document legal RAML*. A linter answers a different
question — *is this document a good one* — and the two are not the same question
asked at different strengths. This document settles which judgements belong here
at all, where the line runs, and what the extension mechanism is for everything
on the other side of it.

Normative for `fastraml/views/lint/`. It sits on top of
[16](16-graph.md)'s walk and graph, holds no pass, and decides no RAML rule.

## 1. The line, and why it is not "lint versus parse"

[16](16-graph.md) § 10.3 drew a line for `diff`: policy above RAML conformance
belongs to a consumer, and `diff`'s backward-compatibility grading is admitted
because it *follows from the spec's own semantics* — from `required`, and from
which side of the wire consumes a value. It named the counter-example in the
same sentence: "every operation must be documented", which is genuinely
org-specific.

Read literally that sentence excludes a linter, because "every operation must be
documented" is a lint rule. Read for what it means, it does not: **the test is
the judgement's provenance, not its genre.** § 10.3's own rule, applied to lint
rules rather than to diff rules, splits them into three:

1. **Derived from the language.** RAML has two orthogonal ways to say a value
   may be absent — `property?:` on the name and `nil` in the type — so
   `property?: string?` double-encodes absence and no consumer can distinguish
   *missing* from *present and null*. Nothing about that is taste. Neither is a
   `$ref` carrying siblings inside an included JSON Schema, which the draft-07
   resolver this parser itself runs will silently ignore; nor `schemas:`, which
   the spec deprecates in favour of `types:`.
2. **Derived from a published standard.** The OWASP API Security rules. Not one
   organisation's house style, but not RAML's either.
3. **Taste.** Kebab-case paths, no verbs in a path, declarations sorted,
   descriptions required. § 10.3's example lives here.

Group 1 ships in `fastraml/views/lint/` and is the default ruleset. Group 2
ships beside it as a named ruleset that is **off by default**. Group 3 ships
nowhere in this repository; § 6 is the mechanism it uses instead.

That is an amendment to § 10.3 rather than a reinterpretation of it, and § 10.3
is amended to say so — the same move § 10.3 itself performed on § 7.

### 1.1 The rule a linter may not hold

The mirror of [17](17-consumers.md) § 2.1, and the trap this area walks into
first. **A judgement the parser already makes is not a lint rule.** A path
template variable with no declaration, a facet that does not belong to its
kind, an enum member that contradicts its own type — each is a parse or P10
error here, and porting the equivalent rule from another implementation's
catalogue would put a rule of the language into a view.

The tell is that the rule would never fire: it needs a document the parser
rejects, so no `Raml` a linter is handed can reach it. Surveying speakeasy's
62 OpenAPI rules against this parser, **13 fail that test** — `path-params`,
`path-declarations`, `typed-enum`, `duplicated-enum` and `oas-schema-check`
among them. They are not ported, and a rule proposed here is checked against
this paragraph before it is written.

## 2. The model

```python
class Severity(StrEnum):
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


@dataclass(frozen=True, slots=True)
class RuleMeta:
    id: str
    category: Category
    summary: str          # one line, what it checks
    rationale: str        # why it is worth checking
    severity: Severity    # the default; config overrides it
    good: str = ''        # RAML showing the rule satisfied
    bad: str = ''         # RAML showing it violated


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    severity: Severity
    message: str
    location: str
    position: Position
    iri: str = ''
    info: dict[str, object] = field(default_factory=dict)
```

`Finding` is **not** a `RamlError`. Three reasons, and the third is the one that
decides it:

- `Trace.to_dict()` writes `'severity': 'error'` as a literal, to keep go-raml's
  JSON shape for TCK comparison ([11](11-diagnostics.md) § 7). A warning has
  nowhere to go in that structure.
- A `RamlError` is a *chain* — how the parser got here — and a finding has no
  chain. It is one judgement at one place.
- `RamlError` is an `Exception`. A linter produces hundreds of findings on a
  healthy document and raises none of them.

Message style follows [11](11-diagnostics.md) § 6 unchanged: lowercase, no
trailing period, variables in `info` rather than interpolated. A test asserts on
the rule id and `info`, never on assembled text.

### 2.1 `good` and `bad` are on the rule, not in a document

Every rule carries the RAML that satisfies it and the RAML that violates it,
following speakeasy's `DocumentedRule`. Two things fall out that a prose
reference page does not give:

- `fastraml lint --explain RULE` prints them, so the tool documents itself and
  the documentation cannot drift from the registry.
- The suite **parses `good` and `bad` and asserts the rule is silent on the
  first and fires on the second**. A rule whose examples are wrong fails its own
  test. [16](16-graph.md) § 6.2 is why this is not optional: three catalogue
  queries were wrong, ran, and returned plausible rows.

## 3. Two rule shapes

```python
class VisitorRule(Protocol):
    """A subset of views.walk.Sink, fanned out over the completed graph nodes."""
    meta: ClassVar[RuleMeta]
    # any subset of Sink's methods, each returning findings
    def type_(self, ctx: Context, iri: str, base: BaseShape, kind: str) -> Iterable[Finding]: ...
    def operation(self, ctx: Context, iri: str, op: Operation) -> Iterable[Finding]: ...


class DocumentRule(Protocol):
    """For aggregation and reverse reachability."""
    meta: ClassVar[RuleMeta]
    def run(self, ctx: Context) -> Iterable[Finding]: ...
```

Most rules are visitors. Building the graph performs the model walk once; the
engine then fans one flat loop over its projected nodes out to every visitor
rule. It never performs one model traversal per rule. A visitor also gets the
IRI free, which is exactly what a `Finding` needs in order to point at something
a reader can then `show`.

`DocumentRule` exists for the handful that cannot be expressed one node at a
time — anything counting, or walking *backwards*: `unused-*`,
`duplicate-display-name`. It receives the graph and asks `into()` or `walk(...,
reverse=True)`.

This split is not speakeasy's. Its engine has one `Run(ctx, doc, config)` per
rule and runs them in goroutines, which is correct in Go and wrong here twice
over: [13](13-public-api.md) § 7 contract 3 says a `Raml` instance is
single-threaded, and N independent traversals of a large document cost N times
one shared traversal in a language with no cheap parallelism to hide it.

### 3.1 The context

```python
@dataclass(frozen=True, slots=True)
class Context:
    raml: Raml
    graph: Graph
    options: Mapping[str, object]   # this rule's configured options
```

`graph` is built once for the whole run, not per rule. A rule that only visits
does not have to touch it.

## 4. Rules are code, not SPARQL

The catalogue ([16](16-graph.md) § 6) answers eight questions that are
lint-shaped, and the obvious economy is to run them as the rules. Measured, it
is not an economy.

| | Cost |
|---|---|
| parse + unwrap + build graph (`bench_endpoints`) | **545 ms** |
| serialising that graph to N-Triples | 152 ms |
| loading the store | 421 ms |

A SPARQL-backed rule set **more than doubles the cost of the whole pipeline**
before the first rule runs, and makes `pyoxigraph` a hard dependency of the
default behaviour. § 6.3's advice — hold one store rather than run the command
repeatedly — does not rescue it: a linter is one invocation per file per CI run,
so it pays the load every time.

Nor does SPARQL win on these particular questions. [16](16-graph.md) § 6.1 names
three things it is genuinely better at, and each has a direct `Graph` primitive:

| § 6.1's win | The query | In code |
|---|---|---|
| Negation | `unused-types` | `any(e.predicate != 'declares' for e in graph.into(iri))` |
| Outer join | `trait-usage` | `dict.fromkeys(traits, 0)` |
| Closure + aggregation | `type-fan-in` | `graph.walk(iri, USE_EDGES, reverse=True)` |

§ 6.1's claim that the negation generalisation is unavailable to hand-written
code is true of code that enumerates the reference predicates and false of code
that asks `into()`. Both forms survive a new edge kind equally.

The rest of the overlap — `undocumented-operations`, `unsecured-operations`,
`unbounded-strings`, `get-with-request-body`, `untyped-payloads` — is what
§ 6.1 already calls the loop's territory.

### 4.1 What happens to the catalogue

It keeps the nine queries that are **not judgements**: `endpoint-tree`, `enums`,
`media-types`, `scheme-usage`, `annotation-usage`, `type-fan-in`,
`error-response-types`, `recursive-types`, `required-query-parameters`. There is
no severity to attach to a table of contents and no exit code that means "here
are your media types". They answer a human asking once, which is what the
catalogue is for.

The lint-shaped ones become rules and **leave the catalogue**, rather than
existing twice:

| Query removed | Rule |
|---|---|
| `unused-types` | `unused-type` |
| `trait-usage` | `unused-trait` |
| `undocumented-operations` | removed; description requirements are plugin policy (§ 1 group 3) |
| `unsecured-operations` | `unsecured-operation` |
| `unbounded-strings` | `unbounded-string` |
| `get-with-request-body` | `get-with-body` |
| `untyped-payloads` | `untyped-payload` |
| `multiple-inheritance` | `multiple-inheritance` |

Two implementations of one judgement is the state [16](16-graph.md) § 6.2
describes: a query that is wrong, runs, and returns plausible rows — now with a
code version beside it, silently disagreeing. Keeping both and testing that they
agree was considered and rejected: it pins the weaker form in place forever to
protect a duplicate nobody needs.

`type-fan-in` and `error-response-types` stay queries despite being close to
judgements, because neither has a threshold that is not arbitrary. "Ranked by
blast radius" is a report; "more than ten" would be taste.

## 5. Configuration

```yaml
extends: [recommended]            # or: all, spec, security
plugins: [house-style]            # nothing from a plugin runs until named

categories:
  security: { severity: error }

rules:
  - id: unbounded-string
    severity: error
  - id: unused-type
    match: '.*internal.*'         # suppress a subset, keep the rule
    disabled: true
```

Three tiers, most specific winning: ruleset → category → rule. Taken from
speakeasy's `linter/config.go`, and `match:` is the piece worth taking
verbatim — a regex over the finding's message suppresses *some* of a rule's
findings without disabling the rule, which is the difference between a linter
people tune and one people turn off.

Rulesets: `spec` (group 1), `security` (group 2), `recommended` = `spec`,
`all` = `spec` + `security` + every enabled plugin.

## 6. Extension

**Discovery through entry points, activation through config.** Both, and the
split is the point.

```toml
[project.entry-points."fastraml.lint_rules"]
house-style = "myorg.ramlrules:RULES"
```

Entry points alone mean that installing an unrelated package silently changes
what CI reports. Config alone means no discovery, so `--list-rules` cannot say
what is available. So every group member is *found* at startup and **none of it
runs until `plugins:` names it**; `--list-rules` reports the providing
distribution per rule, so a surprising finding is traceable to the package that
shipped it.

A plugin exports a sequence of rule instances. It implements the same two
protocols, gets the same `Context`, and is configured by the same three tiers —
there is no second-class rule.

This replaces speakeasy's `customrules`, which embeds a TypeScript runtime to
load `./rules/*.ts`. That solves a problem Go has and Python does not.

## 7. Output and exit codes

Findings sort by location, position, then by rule id. Three renderers: text (default),
JSON, and a summary table by rule.

```
fastraml lint [--config FILE] [--severity S] [--format text|json|summary]
              [--list-rules] [--explain RULE] [--metrics] [-o FILE] FILE [FILE ...]
```

Exit 1 if any finding is at `error`, else 0 — and **every file is linted before
exiting**, matching `validate` ([13](13-public-api.md) § 8) and for the same
reason: the case the tool exists for is a directory in CI.

`--list-rules` and `--explain` need no document and no parse, so they work in a
checkout with nothing to lint.

`lint` parses with `unwrap=True, validate=False, retain_source=True`. Unwrap because every rule
reads the effective model — a `minLength` on a parent is not visible on the
child until it runs ([13](13-public-api.md) § 7 contract 4). Not `validate`,
because a document with a bad example still has style worth reporting, and
because a conformance failure is `validate`'s job to report, not this verb's.
Source retention lets syntax-aware rules distinguish forms that intentionally
decode to the same model, notably deprecated `schemas:` from `types:`.

A document that fails to *parse* produces no findings at all; the error is
reported and the file exits 1. There is nothing to lint and nothing a rule
could say that would not be noise beside the parse error.

### 7.1 What the run cost

`--metrics` writes a second report: the graph, then one row per enabled rule,
then one per provider when more than one contributed.

```
graph           1.161 ms  400 nodes, 645 edges (built)
rules           0.786 ms  9 enabled, 627 calls, 4 produced
engine          0.261 ms  dispatch, filters and sort
total           2.208 ms

        ms    calls   found  kind      rule
     0.607        1       4  document  unused-type
     0.038      130       0  visitor   json-ref-siblings
```

**§ 6 is why this exists.** Entry-point discovery means a project's lint run can
contain rules from a distribution nobody chose directly, so "which rules are
costing me this" has to be answerable without reading anyone's source. That is
what `PluginMetric` aggregates and what `RuleMetric.source` names.

It goes to **stderr**, so stdout stays exactly the findings report and a
`--format json` run remains pipeable ([13](13-public-api.md) § 8). One block per
file rather than a total for the run: on a directory the question is *which*
file is slow, and a sum cannot answer it.

Four decisions in it are not obvious:

- **The graph is normally the largest single cost**, and the report puts it
  first for that reason. On `fixtures/sample` it is 1.16 ms against 0.79 ms for
  all nine rules together — the same proportion § 4 measured at scale, and a
  reader who skips the line will blame whichever rule sorts to the top.
- **A supplied graph reports `-`, not `0`.** The CLI can hand `lint` a graph
  another verb already built; this run then genuinely does not know what it
  cost, and a zero would say it was free.
- **A rule's output is materialised inside its timed region.** Every rule in
  `rules/document.py` is a generator, so timing the bare call would measure
  building the generator and none of the work — reporting near-zero for exactly
  the rules most likely to be slow.
- **`findings` counts what a rule produced, before filtering.** A rule whose
  findings are all being suppressed by a `match:` entry is still paying for
  them, and that is the thing worth seeing.

`engine` is a residual — the total minus the graph minus every rule — so it
absorbs the instrumentation's own overhead. That is the honest place for it:
instrumenting the rules is what created it. It follows that **these timings
describe a measured run and are not a benchmark.** The proportions are the
useful part; `bench/` is what holds this project to an absolute number
([12](12-performance.md)).

The Python surface is `Linter.measure(raml) -> LintRun`, beside
`Linter.run(raml) -> list[Finding]`. Two entry points rather than one with a
flag, because the unmeasured path then pays nothing at all: the timing wrappers
are installed once at fan-out construction, so an ordinary run has no per-node
branch testing whether to time.

## 8. What this is not

- **Not a formatter.** speakeasy's `linter/fix/` applies quick fixes to the live
  `yaml.Node` tree and re-serialises. There is no writable YAML tree here:
  `retain_source` keeps references into the source, not an editable document,
  and nothing in this project writes RAML. Auto-fix needs a writer first, and
  the writer is not this.
- **Not a second validator.** § 1.1.
- **Not a place for house style.** § 1 group 3, § 6.
