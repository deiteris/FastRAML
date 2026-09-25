# 18. Linting

Linting is policy over a successfully parsed RAML document. Parsing and
validation answer whether a document is legal RAML; lint rules report useful
judgements about an effective document. `fastraml/views/lint/` runs after parsing,
holds no parser pass, and decides no RAML conformance rule.

## 1. Scope

A lint rule must be one of:

- A judgement derived from RAML semantics.
- A judgement derived from a published external standard, with that source
  recorded in the rule metadata.
- An opt-in style or organisation policy.

A rule that rejects a document the parser already rejects is not a lint rule.
Put it in the appropriate parser pass. A rule that is specific to one
organisation belongs in a plugin, not in the default built-ins.

The linter runs over the effective, unwrapped model and graph. It can therefore
judge the result of inheritance, traits, resource types, security inheritance,
and resolution without reimplementing those operations.

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
| `recommended` | the built-in `spec` rules | enabled |
| `spec` | rules derived from RAML semantics | enabled through `recommended` |
| `security` | rules derived from published security guidance | disabled |
| `http` | HTTP semantics from RFC 9110 and related media/URI standards | disabled |
| `problem-details` | RFC 9457 problem-details contracts | disabled |
| `i-json` | RFC 7493 interoperable JSON profile | disabled |
| `style` | built-in review and notation rules | disabled |
| `all` | all built-ins and activated plugin rules | disabled |

The current rule registry is `fastraml/views/lint/rules/__init__.py`. Do not copy
the rule inventory here; use the CLI instead:

```bash
fastraml lint --list-rules
fastraml lint --explain unbounded-string
```

### 2.1 Rule shapes

A rule is exactly one of two forms:

- A visitor rule implements one or more graph-node visit methods. The engine
  fans one graph-node walk out to all enabled visitor rules.
- A document rule implements `run(context)` for aggregation or reverse
  reachability.

Both receive a `Context` containing the parsed `Raml`, its `Graph`, and the
rule's configured options. A finding includes its rule ID, severity, message,
location, position, optional IRI, and structured `info` values.

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
- `parse_config()` decodes lint configuration.
- `Linter.run(raml, graph=None)` returns every finding without output limits.
- `Linter.report(...)` returns a bounded `LintReport` with complete totals.
- `Linter.measure(...)` returns findings and `LintMetrics`.

The optional `graph` argument is for Python callers that already built one. The
CLI builds the graph as part of each lint invocation.

Lint requires an unwrapped model. Source-sensitive rules additionally require
`ParseOptions(retain_source=True)`.

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
