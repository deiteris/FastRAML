# 18. Linting

Linting is policy over an effective RAML model. Parsing and
validation answer whether a document is legal RAML; lint rules report useful
judgements about an effective document. `fastraml/views/lint/` runs after parsing,
holds no parser pass, and decides no RAML conformance rule.

## 1. Scope

A lint rule must be one of:

- A judgement derived from RAML semantics.
- A judgement derived from a published external standard, with that source
  recorded in the rule metadata.
- An authoring policy, normally opt-in; the remote-fragment portability and
  non-strict-example warnings are recommended by default (§ 2).

A rule that rejects a document the parser already rejects is not a lint rule.
Put it in the appropriate parser pass. A rule that is specific to one
organisation belongs in a plugin, not in the default built-ins.

The linter runs over the effective, unwrapped model and graph. It can therefore
judge the result of inheritance, traits, resource types, security inheritance,
and resolution without reimplementing those operations.

A lenient model can finish unwrap while retaining failed security references
(docs/11 § 2). The visitor walk skips unresolved reference graph
placeholders: they have no resolved entity to judge, and the parser has already
reported their failures. Sound entities still receive the ordinary policy checks.
An unknown graph-node kind remains an engine error.

## 2. Rules and rulesets

Every rule has `RuleMeta`: an ID, category, summary, rationale, default severity,
optional good/bad RAML examples, and a tuple of published `references`. `files`
holds the documents an example references, such as an Extension's master. The
test suite writes them beside the example, and `--explain` prints them.
`fastraml lint --explain RULE` prints that metadata. Standards-based rules cite
their RFC, OWASP, CWE, RAML, or JSON Schema source. `tests/unit/test_lint.py`
parses every built-in example and verifies that the good example is silent and
the bad example produces the rule.

Built-in rulesets are:

| Ruleset | Contents | Default |
|---|---|---|
| `recommended` | the built-in `spec` rules, `remote-fragment`, `non-strict-example` and `broken-doc-link` | enabled |
| `spec` | rules derived from RAML semantics | enabled through `recommended` |
| `security` | rules derived from published security guidance | disabled |
| `http` | HTTP semantics from RFC 9110 and related media/URI standards | disabled |
| `problem-details` | RFC 9457 problem-details contracts | disabled |
| `i-json` | RFC 7493 interoperable JSON profile | disabled |
| `style` | built-in review and notation rules | disabled |
| `documentation` | links in descriptions (docs/16 § 11) | enabled through `recommended` |
| `all` | all built-ins and activated plugin rules | disabled |

The current rule registry is `fastraml/views/lint/rules/__init__.py`. Do not copy
the rule inventory here; use the CLI instead:

```bash
fastraml lint --list-rules
fastraml lint --explain unbounded-string
```

`remote-fragment` warns by default at each HTTP(S) `!include` or `uses:`
reference, including references in imported fragments. Remote loading remains
valid when enabled; the warning concerns reproducibility and availability, and
is independently configurable through `lint.rules`.

`non-strict-example` warns by default when an example wrapper sets
`strict: false`, even if its value conforms to the type. This disables ordinary
example validation and can let the example drift from its declared type.
The rule covers `example:`, named `examples:`, included wrappers and
`NamedExample` fragments, and examples on nested types, parameters and bodies.
Omitted `strict`, `strict: true`, and a `strict` property inside the example's
data are silent. It reports each authored `strict:` field once across inherited
or reused examples, at that field's source location. `info['example']` is the
example's name, or `example` for a single unnamed example. This authoring policy
belongs to `style` and `recommended` and is configurable through `lint.rules`.

`optional-discriminator` warns by default when an effective discriminator
names an optional property. RAML 1.0 does not require the tag's presence, so
parsing and validation accept that declaration. Without the tag, structural
matching may admit several concrete types. The rule covers both optional-property
spellings and inherited properties or discriminators, reports the optional
property's authored location once across inherited uses, and is independently
configurable through `lint.rules`.

`unprojectable-json-schema` warns by default where a JSON Schema type has no
nearest RAML type (docs/10 § 7). The schema stays valid and validates values;
views read it as an opaque schema, so this finding is where the projection
failure is reported. The finding's message and `info` are the projection
error's message key and `info`, plus `pointer`: the JSON Pointer the type
included (`/definitions/A`), so two subschemas of one file are told apart,
and `''` for a whole file or an inline schema. It is reported once per schema, however many
types include or inherit it: for a schema file at that file, without a
position, and for an inline schema at its declaration.

`broken-doc-link` warns at an explicit link in prose that does not resolve to
one target: `` [`Book`] `` or `[the book][Book]` (docs/16 § 11.2). Its message
key says why: `link names nothing`, `link names more than one target` (with the
candidate addresses in `info['targets']`), or `library prose links into an
API`. `info['link']` is the label as written. A bare `[label]` is never
reported. A finding is placed on the `description:` or `content:` key that
wrote the text, once per label. Placing it on a line inside a block scalar
would put a suppression directive inside the prose. An inherited or
contributed description is reported once, at the place that wrote it. The
rule is a style rule in the `documentation` ruleset, because it checks
fastRAML's own convention rather than RAML. It is also in `recommended`, so
it warns by default; a bracket that names nothing renders as plain text.

### 2.1 Rule shapes

A rule is exactly one of two forms:

- A visitor rule implements one or more graph-node visit methods. The engine
  fans one graph-node walk out to all enabled visitor rules.
- A document rule implements `run(context)` for aggregation or reverse
  reachability.

Both receive a `Context` containing the parsed `Raml`, its `Graph`, and the
rule's configured options. A finding includes its rule ID, severity, message,
location, position, optional IRI, and structured `info` values.

A finding about a type names it in `info['type']` through
`views/lint/labels.py`. A type with a name is labelled by it. An anonymous
member of a union is labelled by what holds the union that declares it: a
declaration (`Input` for `string` in `Input: string | integer`), a property
(`p` for `p: string | integer`), or a body (its media type, for a union written
inline under `application/json:`). The declaring union is found by climbing
from the union in hand through aliases and supertypes that hold the same member
objects, so a body typed by an alias of `Input` or by `Sub: {type: Input}`
still says `Input`. A union nested in another anonymous union takes the outer
one's label. Anything else is `anonymous`. Graph rules call `type_label`, which
reads the union off the `anyOf` edge; the I-JSON rules walk body shapes
themselves and call `label_in` with the union they passed through and its
label.

`Finding` is not a `RamlError`: findings do not raise, do not carry parser trace
chains, and may have warning or info severity.

## 3. Configuration

Lint policy belongs in the common fastRAML configuration under `lint:`:

```yaml
lint:
  extends: [recommended, security, http]
  plugins: [house-style]

  categories:
    security: {severity: error}

  rules:
    - id: unbounded-string
      severity: error
    - id: unused-type
      match: '.*internal.*'
      disabled: true
```

Rule selection and severity precedence is: ruleset, category, then rule. A
rule-level entry without `disabled:` enables that rule even if its ruleset is not
selected. `match:` is a regex over a finding's rendered message; it filters that
rule's findings without changing findings from other rules.

For one invocation, repeat `--ruleset` and `--rule`:

```bash
fastraml lint --ruleset security --ruleset http api.raml
fastraml lint --rule unbounded-string=error api.raml
fastraml lint --rule explicit-uri-parameter api.raml
fastraml lint --rule unused-type=off api.raml
```

`--ruleset` adds to the configured `extends` and never removes from it, so the
configuration's categories and rules still apply after it. `--rule` applies
last.

`--severity` is a display threshold, not a rule configuration mechanism. It
shows the chosen severity and every more severe finding.

The configuration data type validates configuration structure. Registry-dependent
checks still run when configuration is decoded: named rulesets, categories,
plugins, rules, and regular expressions must be valid for the active registry.

### 3.1 Plugins

Plugins are discovered through the `fastraml.lint_rules` entry-point group:

```toml
[project.entry-points."fastraml.lint_rules"]
house-style = "myorg.ramlrules:RULES"
```

Discovery does not activate a plugin. A plugin runs only when its entry-point
name appears under `lint.plugins`. Plugin rules use the same rule shapes,
configuration precedence, filtering, output, and metrics as built-ins.

## 4. Source suppression

Place a standalone directive immediately before the source line a finding names:

```yaml
# fastraml: ignore missing-description,missing-example
User: string
```

Use `*` to suppress every rule at that site. Inline directives are unsupported.
Suppression is lexical because the YAML parser does not retain comments. It is
applied after a rule runs, so metrics retain the rule's produced-finding count.

A finding without a known source position cannot be suppressed. A directive in an
included file applies to findings at that file's URI and line.

## 5. CLI

```text
fastraml lint [--config FILE] [--severity S] [--ruleset NAME]
              [--rule ID[=SEVERITY|off]]
              [--fail-on error|warning]
              [--format human|text|json|summary]
              [--max-findings N] [--max-findings-per-rule N]
              [--no-color] [--list-rules] [--explain RULE] [--metrics]
              [-o FILE] FILE [FILE ...]
```

`--list-rules` and `--explain` require no input document. Other invocations
parse each file with `unwrap=True`, `validate=False`, and `retain_source=True`.
Validation remains the `validate` command's responsibility. A parse failure
produces no findings for that file, reports the parser error, and causes a
nonzero result; remaining files are still attempted.

Formats:

- `human`: grouped terminal report; color is used only for interactive stdout.
- `text`: one stable, uncoloured record per finding.
- `json`: an integration document with `schemaVersion: 1` and structured
  finding fields.
- `summary`: complete counts grouped by rule.

Findings sort by location, position, and rule ID. `-o` writes UTF-8 with LF
newlines.

The command exits 1 if any supplied file fails to parse or any complete finding
is at the `--fail-on` severity or worse. The default is `error`; `warning` is the
other accepted threshold. `--severity` remains an independent display filter.

### 5.1 Finding limits

The CLI shows at most 1,000 findings and 100 findings per rule per source file
by default. Pass `0` to disable either limit. Limits apply after the `--severity`
display filter. Within that eligible set, selection gives errors, then warnings,
then info findings priority; retained findings keep reading order. The per-rule
limit is applied before the global limit. Rules still run to completion; JSON
and summary output retain complete counts for the display-filtered set, and
omitted error or warning findings still affect the `--fail-on` result.

### 5.2 Metrics

`--metrics` writes per-file graph, rule, provider, and engine timing information
to stderr. Standard output remains the selected findings format. Metrics describe
the measured invocation and are diagnostic output, not a benchmark or performance
gate.

## 6. Python API

The public lint surface is in `fastraml.views.lint`:

- `builtin_registry()` returns built-in rules and rulesets.
- `parse_config()` decodes lint configuration from YAML text, and
  `decode_config()` from a loaded mapping, such as `FastRamlConfig.lint`.
  `configured_linter()` builds the linter a `lint:` section describes, over
  the built-in rules and the installed plugins.
- `Linter.run(raml, graph=None)` returns every finding without output limits.
- `Linter.report(...)` returns a bounded `LintReport` with complete totals.
- `Linter.measure(...)` returns findings and `LintMetrics`.

The optional `graph` argument is for Python callers that already built one. The
CLI builds the graph as part of each lint invocation.

Lint requires an unwrapped model. Source-sensitive rules additionally require
`ParseOptions(retain_source=True)`; `Linter.requires_source` says whether an
enabled rule is one.

## 7. What lint is not

- Not a formatter or auto-fixer.
- Not a second RAML validator.
- Not a replacement for organisation-specific policy plugins.

## 8. Verification

- Engine, configuration, rules, suppression, output, and metrics:
  `tests/unit/test_lint.py`
- CLI integration: `tests/unit/test_cli.py`
- View boundary: `tests/unit/test_views.py`

For durable design rationale that is not part of the current contract, see
`docs/archive/views-consumers-history.md`.
