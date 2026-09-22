# 18 — Linting

A parser answers *is this document legal RAML*. A linter answers a different
question — *is this document a good one* — and the two are not the same question
asked at different strengths. This document settles which judgements belong here
at all, where the line runs, and what the extension mechanism is for everything
on the other side of it.

Normative for `fastraml/views/lint/`. It sits on top of
[16](16-graph.md)'s walk and graph, holds no pass, and decides no RAML rule.

## 1. The line, and why it is not "lint versus parse"

[16](16-graph.md) § 10.3 drew a line for `compat`: policy above RAML conformance
belongs to a consumer, and its backward-compatibility grading is admitted
because it *follows from the spec's own semantics* — from `required`, and from
which side of the wire consumes a value. It named the counter-example in the
same sentence: "every operation must be documented", which is genuinely
org-specific.

Read literally that sentence excludes a linter, because "every operation must be
documented" is a lint rule. Read for what it means, it does not: **the test is
the judgement's provenance, not its genre.** § 10.3's own rule, applied to lint
rules rather than to compatibility rules, splits them into three:

1. **Derived from the language.** A `$ref` carrying siblings inside an included
   JSON Schema has constraints the draft-07 resolver silently ignores;
   `schemas:` and `schema:` are explicitly deprecated in favour of `types:` and
   `type:`; and a body whose media type cannot carry any value admitted by its
   shape gives consumers two contradictory decoding instructions. None of those
   is taste.
2. **Derived from a published standard.** OWASP API Security and the OAuth
   RFCs, RFC 9110 HTTP semantics, RFC 9457 problem details and the RFC 7493
   I-JSON profile. Not one organisation's house style, but not RAML's either:
   each rule states a requirement the standard makes and the parser does not
   enforce, and names the clause in `references` (§ 2.2).
3. **Taste.** Kebab-case paths, notation preferences, declarations sorted,
   descriptions required. § 10.3's example lives here.

Group 1 ships in `fastraml/views/lint/` and is the default ruleset. Group 2
ships beside it as the `security`, `http`, `problem-details` and `i-json`
rulesets, and group 3 as `style`, all **off by default**. The built-in style
set covers RAML-wide authoring conventions; § 6 remains the mechanism for
organisation-specific policy.

A company API guideline is group 3 however widely it is followed: it is one
organisation's choices, and it ships as a plugin. The linter is also not a
security scanner. It judges the contract a document states, reports in its own
formats (§ 7), and leaves exchange formats such as SARIF to a tool built for
that job.

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
    references: tuple[str, ...] = ()   # the published sources it follows from


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

### 2.2 `references` name the source, not the rationale

A rule derived from a published standard (§ 1 group 2) names it in
`references`, one citation per entry, and a `spec` rule may name the RAML or
JSON Schema clause it follows from. Six spellings are accepted:

| Spelling | Example |
|---|---|
| OWASP API Security Top 10 category | `OWASP API4:2023` |
| OWASP document, by title | `OWASP File Upload Cheat Sheet` |
| RFC, or one of its clauses | `RFC 6749`, `RFC 9110 § 15.5.2`, `RFC 9457 Appendix B` |
| CWE weakness | `CWE-770` |
| RAML 1.0 section, by title, since the spec does not number them | `RAML 1.0 § File` |
| JSON Schema draft-07 (`draft-handrews-json-schema-01`) section | `JSON Schema draft-07 § 8.3` |

The rationale explains *why* in prose and does not repeat the identifiers.
Keeping them as data lets `--explain` list them, and lets a reader find every
rule one clause produced without searching prose. The suite asserts that every
`security`, `http`, `problem-details` and `i-json` rule has at least one
reference, and that every rule's references match the spellings above.
`unused-type` and `unused-trait` cite nothing: they follow from the document's
own reachability, not from a clause. RFC citations are to the current document:
RFC 9110, not the RFC 7231 it obsoletes, and RFC 9457, not RFC 7807.

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

A rule builds a finding with `ctx.on(meta, message, entity, iri=..., **info)`,
which takes the entity's `location` and `key_pos`, or with `ctx.at(meta,
message, location=..., position=..., ...)` for a place that is not a model
entity, such as a fragment's root key. Either fills in the rule id and default
severity; the engine applies configured severities afterwards.

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
| Negation | `unused-types` | forward reachability from the effective API, excluding `declares` |
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
| `get-with-request-body` | `meaningless-request-body` (formerly `get-with-body`) |
| `untyped-payloads` | `untyped-payload` |
| `multiple-inheritance` | `multiple-inheritance` |

Two implementations of one judgement is the state [16](16-graph.md) § 6.2
describes: a query that is wrong, runs, and returns plausible rows — now with a
code version beside it, silently disagreeing. Keeping both and testing that they
agree was considered and rejected: it pins the weaker form in place forever to
protect a duplicate nobody needs.

`unused-type` and `unused-trait` run only when the entry point is an API. Their
negation is a closed-world judgement: an API graph can show that nothing in the
effective API references a declaration, but a standalone library, data type or
trait is an export whose consumers are outside that graph. Calling such an
export unused would be a false positive.
For types this is reachability, not merely an incoming-edge test: if unused
`DeadParent` has a property of type `DeadChild`, both are unused. Starting at the
effective API and following use edges marks a declaration only when an endpoint,
operation, parameter or payload can actually reach it.

`type-fan-in` and `error-response-types` stay queries despite being close to
judgements, because neither has a threshold that is not arbitrary. "Ranked by
blast radius" is a report; "more than ten" would be taste.

## 5. Configuration

```yaml
lint:
  extends: [recommended]            # or: all, spec, security, style
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

Persistent rule selection, options and message filters belong in the
configuration file. For one run, repeat `--rule ID` to enable a rule,
`--rule ID=SEVERITY` to enable and regrade it, or `--rule ID=off` to disable it.
These overrides run after the file configuration and duplicate IDs are rejected.
`--severity` remains only a display threshold and does not reconfigure a rule.

Rulesets: `spec` (group 1); `security`, `http`, `problem-details` and
`i-json` (group 2); `style` (group 3); `recommended` = `spec`; `all` = every built-in plus
every enabled plugin. Each built-in ruleset has the category of the same name,
so `categories: {http: {severity: error}}` grades exactly the rules
`extends: [http]` enables.

### 5.1 Built-in policy

The Speakeasy OpenAPI catalogue is translated by intent, not by field name.
Rules that duplicate RAML validation are omitted, and rules for OpenAPI-only
constructs such as `operationId`, global tags and Link objects have no RAML
version. The translated security set uses the effective model after traits,
resource types, type inheritance and `securedBy` inheritance have run.

The additional security rules cover HTTPS-only operations; Basic authentication;
typed `401`, `429`, `500` and `400`/`422` responses; numeric URI parameters;
rate-limit headers; bounded arrays and integers; restricted strings; and closed
or size-bounded objects.
Each security rule's `references` names the OWASP API Security Top 10 (2023)
category it follows from, using the assignments in Stoplight's
`spectral-owasp-ruleset` (`https://github.com/stoplightio/spectral-owasp-ruleset`),
the ruleset Speakeasy's rules came from. That ruleset places them as follows:
API1 `numeric-resource-id`; API2 `unsecured-operation` and
`insecure-basic-authentication`; API3 `no-additional-properties`; API3 and API4
`bounded-additional-properties`; API4 the rate-limit, `429`, string, array and
integer rules; API8 `https-only` and the `401`, `500` and validation-error
responses. The rationale then gives OWASP's own prevention advice, not a
paraphrase of the ruleset. API1 asks for random identifiers only as an extra
layer of defence, and `numeric-resource-id` says so, since only an authorization
check on each access actually fixes API1.

The security set also holds rules no OpenAPI catalogue supplied:

- `credential-in-query` reads a security scheme's `describedBy` query
  parameters and query string. Their placement makes them credentials, so this
  is the one credential rule that needs no name guess (below), and it follows
  RFC 6750 § 2.3's advice against tokens in the URI.
- `oauth2-insecure-grant` reports the `password` grant (RFC 9700 § 2.4, MUST
  NOT) and the `implicit` grant (§ 2.1.2, SHOULD NOT), naming the clause in
  `info`. `oauth-endpoint-https` reports an `http:` authorization, token or
  OAuth 1.0 endpoint (RFC 6749 §§ 3.1–3.2 require TLS); a relative URI is not
  reported, since only an explicit `http:` states a cleartext endpoint.
  `oauth1-scheme` reports OAuth 1.0, which RFC 6749 obsoletes.
- `unanchored-string-pattern` exists because RAML matches `pattern:` with
  `search` (docs/10 § 5.4): on input, `[a-z]+` accepts any value containing a
  letter. Every top-level alternative must start at `^` or `\A` and end at `$`,
  `\Z` or `\z`; a leading `(?m)` makes the anchors line anchors and is reported.
  The pattern-property counterpart stays in `style`, because a property name is
  not a value a client supplies.
- `nested-quantifier-pattern` is the one heuristic in the set, which is why it
  reports at `info`. It reports an unboundedly repeated group whose content
  also repeats and has no separator — `(a+)+`, `(\w+\s?)*` — and accepts
  `(-[a-z]+)*`, where the separator fixes each repetition's start. It is silent
  under `regex_engine='re2'`, whose matching is linear. A tokenizer reads both
  pattern rules' expressions, with a run of literals as one token (docs/12 § 12).
- `bounded-number`, `bounded-file` and `restricted-file-types` extend the input
  bounds to `number` and `file`; `fileTypes: ['*/*']` counts as no list.
  `restricted-request-media-type` reports a wildcard request body media type.
- `base-uri-userinfo` reports a `baseUri` whose authority holds
  `user:password`, a form RFC 3986 § 3.2.1 deprecates; a user name alone is
  not reported. It lives in `rules/uris.py` beside the path rules below.

The `http` set states what RFC 9110 requires of a response and RAML does not
check. `no-content-body` reports a body on a 1xx, 204 or 304 response or on any
HEAD response, naming the clause in `info`. `allow-header-405`,
`proxy-authenticate-407` and `www-authenticate-401` report a missing header a
server MUST send. `redirect-location` covers 301, 302, 307 and 308, the codes
whose sections say SHOULD; 303 is left out because its section describes
Location without requiring it. `content-range-header` accepts a 206 whose body
is `multipart/byteranges` instead of a Content-Range header (§ 15.3.7.2). The
parser does not copy a scheme's `describedBy` into the operations it secures,
so `www-authenticate-401` reads both: an operation's 401 passes when a securing
scheme's `describedBy` 401 declares the header, and a `describedBy` 401 without
it is reported on the scheme. `meaningless-request-body` stays in `spec`,
where its GET-only predecessor `get-with-body` was: moving it would turn off a
default rule. It covers the four methods RFC 9110 gives no request content
meaning — GET, HEAD and DELETE in the same words (§§ 9.3.1, 9.3.2, 9.3.5), and
TRACE, whose client MUST NOT send content (§ 9.3.8) — and names the clause in
`info`. The old identifier is not kept as an alias: a configuration naming it
fails with `unknown rule`, which says what changed rather than silently
running a wider rule under the old name.

The header-field rules live in `rules/headers.py`, in the same `http` set.
`header-field-name` requires a field name to be a token (§§ 5.1, 5.6.2);
`duplicate-header` reports two keys in one header map that differ only in
case, since field names are case-insensitive and RAML keys are not; and
`hop-by-hop-header` reports `Connection`, `Keep-Alive`, `Proxy-Connection`,
`TE`, `Transfer-Encoding` and `Upgrade`, which intermediaries remove
(§ 7.6.1). `http-date-header` requires `Date`, `Expires`, `Last-Modified`,
`If-Modified-Since`, `If-Unmodified-Since` and `Sunset` to be `datetime` with
`format: rfc2616`, the IMF-fixdate of § 5.6.7; RAML's default `datetime` is
RFC 3339 and the `-only` types are not HTTP dates. A `string` is not reported,
because it constrains nothing rather than contradicting the format, and
`Retry-After`, which may be delta-seconds, keeps its own rule.
`content-type-header` reports a declared `Content-Type` whose enumeration
names a media type no body has, or one declared where there is no body
(§ 8.3); media-type parameters are ignored in the comparison. The per-header
rules visit the effective operations, so a header a trait adds is reported
once for each operation the trait applies to, each finding naming its
operation's IRI.

Three status rules close the set. `obsolete-status-code` reports 305
(deprecated, § 15.4.6), 306 (§ 15.4.7) and 418 (§ 15.5.19), both reserved.
`not-modified-headers` reports a 304 that omits any of `Cache-Control`,
`Content-Location`, `Date`, `ETag`, `Expires` or `Vary` its operation's 200
declares, since the server MUST send them (§ 15.4.5). `unreachable-status`
reports, at `info`, a response the declared request can never produce: a 304
outside GET and HEAD or without `If-None-Match`/`If-Modified-Since`; a 412
without any precondition header; a 206 or 416 outside GET or without `Range`
(§ 14.2); and a 413 or 415 on an operation with no request body. It is `info`
because a client may send headers the contract omits; the finding usually
means a request header is missing from the contract, not that the response is
wrong.

RFC 9110 § 4.2 defines HTTP URIs through RFC 3986, and § 8.3 media types
through RFC 6838, so the rules for both belong to the `http` set.
`uri-path-characters` reports a resource segment holding a character outside
RFC 3986's `pchar` once template expressions are removed; percent-encoded
octets are accepted and a non-ASCII letter is not. `dot-segment-path` reports a
`.` or `..` segment, which reference resolution removes (§ 5.2.4). Both read
the segments a resource adds, not its full path, so a parent's fault is not
repeated on every child. `json-charset` reports a `charset` parameter on
`application/json` or a `+json` type: `utf-8` has no effect (RFC 8259 § 11)
and anything else breaks § 8.1's MUST, and `info` names which.
`duplicate-media-type` reports two body keys in one map naming one media type:
type, subtype and parameter names are compared without case, parameter values
exactly, since RFC 6838 § 4.2 makes only the names case-insensitive.

The `problem-details` set is for APIs that adopt RFC 9457, which obsoletes RFC
7807 and keeps its media types, so it judges documents written against either.
Adopting the format is the author's choice, so the set is opt-in, but once it
is on `problem-media-type` expects every error response with a body to offer
`application/problem+json` or `application/problem+xml`. `problem-member-types`
checks the five standard members' JSON types (§ 3.1), tolerating a nilable
member; `problem-status` reports an enumerated `status` that excludes the
response's own code (§ 3.1.2). Both read RAML-typed bodies only: a body typed by
an included JSON Schema is skipped rather than half-checked. Extension members
are unconstrained, so a problem details object is open by design and
`require-closed-object` in `style` will disagree with it; a project using both
disables one for those types.

The `i-json` set is for APIs that adopt RFC 7493, a profile of JSON that every
receiver can process exactly. It reads `application/json` and `+json` bodies,
RAML-typed ones only, walking properties, pattern properties, array items and
union members. `i-json-top-level` reports a body that is neither an object
nor an array (§ 4.1); a union reports if any member is a scalar, and `nil`,
`any` and a JSON Schema body are left alone. `i-json-integer-range` reports an
`integer` with `format: int64` or `long`, or a bound beyond ±(2⁵³−1), which a
receiver reading doubles cannot hold exactly (§ 2.2); RAML's `int` is 32-bit
and is not reported. `i-json-datetime` reports `datetime-only`, which has no
offset (RFC 3339 § 4.4), and `datetime` with `format: rfc2616` (§ 4.3).
`i-json-binary` reports a `file` at `info`: RAML § File represents it as
base64, and § 4.4 recommends base64url. The three rules that look inside a
body are document rules keyed by source position, so a named type used by
several JSON bodies is reported once, where it was written.

The spec set also holds the RAML 1.0 SHOULDs the parser accepts, and the
constructs the specification leaves without a meaning. They live in
`rules/spec.py` and each cites its section. `empty-path-segment` reports an
optional URI parameter that is a whole segment, `/{id}/`, which § Template URIs
says should be required, and a path with an empty segment between two others,
including a nested resource under a parent that ends in `/`; a trailing slash is
not reported. `unnested-resource` reports `/bom/items` written beside `/bom`
rather than nested in it (§ Resources and Nested Resources), naming the longest
declared resource the key extends. `base-uri-protocol` reports an explicit
`protocols` that omits the `http` or `https` scheme of `baseUri`, since
`protocols` overrides it (§ Protocols). `undefined-version` reports `{version}` in
`baseUri` or a resource with no root `version` to supply it.
`undescribed-security-scheme` reports a scheme with no `describedBy`, which
§ Security Scheme Declaration asks for "even for standard security schemes".
`non-scalar-parameter` reports a header or query parameter typed as an object, a
union with a non-scalar member, or an array of either, and for a header an array
of arrays: § Headers and § Query Parameters say RAML defines no validation for
them. URI parameters are not reported, because § Template URIs defaults them to
JSON. `non-standard-method` reports `trace` and `connect`, which fastRAML accepts
as an extension ([01](01-scope-and-coverage.md) § 3) and § Methods does not
list. The MUSTs next to these (a `baseUri` that is not a URI, a
`baseUriParameters` name the base URI does not use, and a `queryString` typed as
an array) are parse or P10 errors, not rules (§ 1.1).

`deprecated-schemas` reports the root `schemas:` key and the `schema:` facet of
any type declaration, a body's included. The facet is read from
`raml.source_info`, which indexes only type declarations, so a property named
`schema` is not mistaken for it.

`json-ref-siblings` inspects parsed JSON Schema, which retains structure but not
token positions. Each finding therefore names the schema document and an RFC
6901 `schemaPath` ending at the offending `$ref`. External schemas are reported
at their own URI without a fabricated line; inline schemas retain their RAML
position. One document-level pass deduplicates schema URI plus pointer, so each
offending `$ref` is reported once however many payloads use its type.
Markdown safety is renderer policy, not a document property: raw HTML may be
passed through, escaped or sanitised, and code spans containing `<script>` are
not executable HTML. The built-in set therefore does not guess at script safety;
that check belongs beside the renderer whose behaviour is known.
RAML accepts exact status codes rather than OpenAPI response classes, so the
validation-error rule accepts `400` or `422` and never invents `4XX` support.
`bounded-additional-properties` uses `maxProperties`, the bound RAML actually
has, and describes itself as a total object-property bound rather than claiming
RAML can bound only additional properties.

The opt-in style set covers the four concise type/property spellings,
`additionalProperties: false`, avoiding `uniqueItems`, anchored and constrained
pattern properties, descriptions, examples, display names, explicit URI
parameter declarations, and three legal but review-worthy type designs:
multiple inheritance, optional-and-nilable properties, and discriminators with
no local subtype. Optional plus nil is a
real three-state contract — omitted, null, or a value — and is not a default
warning because PATCH-like APIs use it intentionally. An operation has no legal
top-level `example` facet in RAML; `missing-example` therefore asks whether one
of its request or response payload shapes carries an example, and says nothing
when the operation has no payload.
Type description/example findings apply to named declarations, not every
anonymous property, item and union-member shape under them; those produced
duplicate low-information advice rather than actionable findings.
`unanchored-pattern-property` does not change RAML matching: pattern properties
continue to use `search`, and the warning exists precisely because authors must
write anchors when they intend a whole-name match.

Syntax rules read `raml.source_info`, keyed by shape id. They do not search the
retained YAML tree per finding: on `fixtures/sample`, replacing those searches
reduced an `all` run from about 59 ms to 9.5 ms, and reduced each of the three
source-spelling rules from about 18 ms to below 0.2 ms. The index and source text
exist only under `retain_source=True`; normal parsing allocates neither.
Source-spelling rules apply only to RAML notation. In particular,
`prefer-inline-alias` does not reinterpret an included JSON Schema object such
as `{"type":"string"}` as RAML's mapping form of `type: string`.

No built-in infers meaning from a declaration's name. RAML has no semantic
marker for an API key or credential parameter, so those OpenAPI rules are not
translated by matching words such as `token` or `secret`. `numeric-resource-id`
needs no name guess: every numeric `uriParameters` shape is reported, including
one named `year`, while numeric query parameters are not. The endpoint's own URI
template selects the declarations to inspect, avoiding duplicate findings for
parameters propagated to descendants.

`no-ambiguous-paths` compares routes one segment at a time, which is sound
because simple expansion percent-encodes `/` (RFC 6570 § 3.2.2). A literal
segment overlaps a template only if every parameter's type accepts the text it
would have to take: `/users/me` beside `/users/{id}` with `id: integer` or an
`enum` without `me` is not reported, while `/users/42` is. The text is tried as a
string, a number (through `Decimal`) and a boolean, because a path carries text
that a typed parameter reads as its own kind. A segment mixing literals and
parameters, such as `{name}.json`, is matched as a pattern against literals and
compared with another template by its literal prefix and suffix, so
`{name}.json` meets `{id}` but not `{name}.xml`. Two templates are otherwise
assumed to overlap; their types are not intersected. Reserved and fragment
expansions may span segments, so a route containing one is not compared.

`meaningless-media-type-schema` reads every request and response body. JSON
scalars are valid JSON and are accepted. A `file` in a JSON or XML body is
accepted too: RAML 1.0 § File says file content "SHOULD be a base64-encoded
string" in JSON, and the validator accepts one (docs/10 § 5), so the earlier
exclusion contradicted the language. Octet-stream, PDF, ZIP, gzip and CBOR
(RFC 8949 § 9.3); the `audio`, `font` (RFC 8081), `image` and `video` top-level
types; and the binary structured syntax suffixes `+ber`, `+der`,
`+fastinfoset`, `+wbxml`, `+zip` (RFC 6839 §§ 3.2–3.6), `+gzip` (RFC 8460
§ 6.3) and `+cbor` (RFC 8949 § 9.5) require a file shape. URL-encoded and multipart forms require an object shape, and
`text/plain` requires a scalar or file shape without pretending every `text/*`
format is plain text. A file's `fileTypes` must include the body's media type,
including wildcard entries, and every union member must be compatible. `any`
is left to `untyped-payload` so one omission does not produce two findings.
Media-type parameters are ignored because they do not change the representation
family.

Response status classes are numeric comparisons inside rules, after the parser
has established a concrete 100–599 code. Rate-limit metadata is requested only
for 2xx and 429 responses, not unrelated 4xx responses. A recognized rate-limit
header must have a usable integer/date/string shape for its particular spelling;
`Retry-After` accepts integer delay-seconds or `datetime` explicitly formatted
as `rfc2616` for an HTTP date. `https-only` also respects an HTTPS `baseUri` when
no `protocols` facet overrides it.
`required-401-response` applies only where authentication is mandatory. An
operation with no effective scheme, or with `null` as an alternative, does not
receive a cascading 401 warning in addition to `unsecured-operation`.
`validation-error-response` likewise applies only when the operation has a
request body, header, query input, query string or URI parameter that can fail
validation; an input-free status operation has no validation failure contract to
document.
The recognized rate-limit names are the current HTTPAPI draft's `RateLimit` and
`RateLimit-Policy`, plus the established legacy `X-RateLimit-Limit`,
`X-RateLimit-Remaining` and `X-RateLimit-Reset` family. Speakeasy's source rule
also lists `X-Rate-Limit-Limit` and obsolete `RateLimit-Limit`/`RateLimit-Reset`
draft spellings; those are deliberately not copied.
Shape rules motivated by attacker-controlled allocation or value domains run
only on shapes reachable from request bodies, request headers, query strings,
query parameters and URI parameters. Response-only schemas and dead exported
declarations are not attacker input. The input IRI set is derived once from the
graph by `Graph.request_shape_iris()` and shared across those rules.

### 5.2 Source suppression

Place a standalone directive immediately above the line a finding points to:

```yaml
# fastraml: ignore missing-description,missing-example
User: string
```

Use `*` to suppress every rule at that site. Inline comments are deliberately
unsupported: PyYAML does not retain comments, and searching for `#` inside a
line would confuse comments with quoted or block-scalar content. Suppression is
applied after a rule runs, so metrics still report the work and findings it
produced before filtering. The linter scans each retained source once per run
with one multiline regex that finds every directive line, and indexes the line
each one covers; checking a finding is then a dictionary lookup rather than a
read of its preceding line. A finding without a real source position is not suppressible, and
directives in included files apply using that file's own URI and line numbers.
This is intentionally lexical: after leading indentation is removed, a whole
preceding line with the directive spelling is treated as a comment. PyYAML does
not retain comment tokens, so the linter does not reconstruct YAML lexical state
to distinguish that spelling when it appears as block-scalar content.

`fastraml/config.raml` is the common configuration's root data type. Lint policy
lives under its `lint:` property, whose shape is still declared by
`views/lint/config.raml`; parser settings and compatibility overrides are sibling
sections. The schemas check closed structure, fields and accepted severity or
impact spellings before a decoder reads them. Checks that depend on the running
process remain semantic validation: lint rules, rulesets, categories and plugins
must exist in the active registry, and every regular expression must compile.

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

Findings sort by location, position, then by rule id. Four renderers serve four
readers: `human` (the default) is a terminal report grouped by source, naming a
file under the working directory relatively and a finding's `info` in
parentheses; `text` is
one compact, stable, uncoloured record per finding; JSON is the integration
contract; and `summary` is a table of complete counts by rule.

```
fastraml lint [--config FILE] [--severity S] [--fail-on S] [--rule ID[=SEVERITY|off]]
              [--format human|text|json|summary]
              [--max-findings N] [--max-findings-per-rule N]
              [--no-color] [--list-rules] [--explain RULE] [--metrics]
              [-o FILE] FILE [FILE ...]
```

`human` follows the shape of Vale's CLI reporter: one underlined source heading,
then `line:column`, severity, message and rule columns, followed by complete
severity counts. Errors are red, warnings yellow and info findings blue, but the
labels carry the same information without colour. Colour is emitted only to an
interactive stdout; `NO_COLOR`, `--no-color`, a pipe, and `-o` all disable it.
The final status word agrees with the exit code: `FAIL` means the run exits 1,
`WARN` that it exits 0 with warnings reported, and `OK` that there are no errors
or warnings — not that the report is empty, matching Vale's treatment of
suggestions. ASCII status words keep the default usable in Windows
consoles whose output encoding cannot represent Vale's check and cross marks.

`text` is the representation to put directly in an agent's context or consume a
line at a time. Each record is self-contained and has no alignment or ANSI state:

```
WARNING unused-type file:///workspace/api.raml:28:3 declared type is never referenced: type: LegacyUser
```

JSON is for a program that parses the report before using it, not inherently an
"AI format". Its envelope carries `schemaVersion: 1`; each finding retains the
canonical location and formatted `position` and also exposes numeric `line`,
`column`, `endLine` and `endColumn` fields. Unknown numeric positions are null.

Exit 1 if any finding is at the `--fail-on` severity or worse (default
`error`; `warning` is the other choice), else 0 — and **every file is linted before
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

The CLI shows at most 1,000 findings across the run and 100 from any one rule.
Either bound accepts `0` to disable it. Rules still run to completion: human
output states the omitted count, truncated text adds a compact `SUMMARY` record,
JSON carries `total`, `shown`, `truncated`, complete severity counts and
`omittedByRule`, and summary output reports complete per-rule totals. Exit status
also uses the complete findings, so truncation can never hide an error from CI.
Selection is by severity first: errors, then warnings, then info, each in
reading order, and the survivors are then shown in reading order. A bound
therefore never shows an info finding while an error or warning is omitted;
before this, 1,000 info findings early in a large document hid a later error
that still set the exit code. The per-rule bound counts per source file, so a
rule cannot spend its allowance in one file and vanish from the next, and it
applies before the global bound, preventing one broad style rule from consuming
the entire report. Human output names the flags that lift both bounds.

`Linter.run(raml)` remains complete and unbounded for compatibility. Consumers
that display findings use `Linter.report(raml) -> LintReport`, whose defaults are
the same 1,000/100 bounds and whose `total_findings`, `omitted_findings`,
`severity_counts` and `rule_counts` make truncation explicit. Passing `None` for
either Python bound disables it.

### 7.1 What the run cost

`--metrics` writes a second report: the graph, then one row per enabled rule,
then one per provider when more than one contributed.

```
graph           1.130 ms  402 nodes, 647 edges (built)
rules           0.448 ms  8 enabled, 168 calls, 8 produced
engine          0.352 ms  dispatch, filters and sort
total           1.930 ms

        ms    calls   found  kind      rule
     0.294        1       8  document  unused-type
     0.059        1       0  document  no-ambiguous-paths
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
  first for that reason. On `fixtures/sample` it is 1.13 ms against 0.45 ms for
  all eight default rules together — the same proportion § 4 measured at scale.
  A reader who skips the line will blame whichever rule sorts to the top.
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

The measured Python surface is `Linter.measure(raml) -> LintRun`, beside
`Linter.run(raml) -> list[Finding]` and bounded `Linter.report(raml)`. Separate
entry points rather than one with a
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
