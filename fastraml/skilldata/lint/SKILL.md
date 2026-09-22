---
name: lint
description: Check a RAML document against named lint rules with fastraml lint, configure which run and how severely, and write your own rules as a plugin. Covers the rule catalogue and its three categories, the config file, suppressing one finding without disabling its rule, the CI gate and exit codes, --metrics, and the two rule shapes a plugin implements. Use when auditing a document for style or security problems, wiring a lint gate into CI, silencing a noisy rule, or adding a house rule.
license: MIT
allowed-tools: Bash(fastraml:*) Read Write
---

# Lint a RAML document with `fastraml lint`

`fastraml lint` reports judgements that sit *above* whether a document is valid.
`fastraml validate` answers "is this legal RAML"; `lint` answers "is this a good
one".

Run `validate` first. A document that fails to parse produces no findings at
all, because there is nothing to lint and a rule saying so would only be noise
beside the parse error.

When running lint, pass `--format text`. Use another format only when the task
requires that representation.

## Run it

```bash
fastraml lint -w . api.raml --format text
fastraml lint -w . api.raml --format text --max-findings 200
fastraml lint --list-rules                   # what is available
fastraml lint --explain meaningless-media-type-schema  # one rule, with examples
```

`--list-rules` and `--explain` need no document and no parse, so they work in a
checkout with nothing to lint yet.

The CLI shows at most 1,000 findings overall and 100 per rule per file by
default, choosing errors, then warnings, then info, so a truncated report never
omits an error it could have shown. Read
the final `SUMMARY` record when text output is truncated. Use
`--max-findings 0` or `--max-findings-per-rule 0` only when the task requires
every finding.

## Six categories, and only one is on by default

| Category | What it means | Default |
| --- | --- | --- |
| `spec` | Follows from RAML's own semantics | **enabled** |
| `security` | Follows from OWASP API Security and the OAuth RFCs | disabled |
| `http` | Follows from RFC 9110 HTTP semantics | disabled |
| `problem-details` | Follows from RFC 9457, for APIs that use problem details | disabled |
| `i-json` | Follows from RFC 7493, for APIs that adopt the I-JSON profile | disabled |
| `style` | Consistent RAML notation and documentation | disabled |

Each category is also the ruleset that turns it on. `--explain RULE` lists the
sources a standards rule cites, such as `RFC 9110 § 15.5.2` or `OWASP API4:2023`.

`spec` rules are not style preferences. `json-ref-siblings` fires because a
draft-07 resolver silently ignores keys beside a `$ref`; `empty-path-segment`,
`unnested-resource` and `undescribed-security-scheme` follow a SHOULD in the RAML
1.0 specification, and `--explain` names the section. `optional-and-nil` is
instead opt-in style: omitted, present-null and present-with-value are distinct
states, useful in PATCH-like contracts but worth reviewing elsewhere.

Turn the security set on with a config file:

```yaml
lint:
  extends: [recommended, security]
```

Add `style` when you also want notation and documentation conventions:

```yaml
lint:
  extends: [recommended, security, style]
```

Add `http` for HTTP-level contradictions, and `problem-details` or `i-json`
only when the API has adopted RFC 9457 error bodies or the RFC 7493 profile:

```yaml
lint:
  extends: [recommended, security, http, problem-details]
```

`recommended` is the `spec` set. `all` is every rule registered, including any
a plugin contributed.

## Configure it

Use `--rule` for a temporary override:

```bash
fastraml lint -w . api.raml --format text --rule explicit-uri-parameter
fastraml lint -w . api.raml --format text --rule unused-type=error
fastraml lint -w . api.raml --format text --rule unused-type=off
```

Use a configuration file for persistent project policy, categories, plugins,
rule options, or message filters:

```bash
fastraml lint --config lint.yaml -w . api.raml --format text
```

Do not use CLI `--severity` for configuration; it only filters displayed
findings. CLI `--rule` overrides the file for that run.

```yaml
lint:
  extends: [recommended, security]

  categories:
    security:
      severity: error        # fail CI on any security finding

  rules:
    - id: explicit-uri-parameter  # enable this opt-in style rule

    - id: multiple-inheritance
      disabled: true         # we use it deliberately

    - id: unbounded-string
      severity: error

    - id: unused-type
      match: '.*Legacy.*'    # silence only these findings
      disabled: true
```

Three tiers, most specific winning: ruleset, then category, then rule.

**`match:` is the one to reach for before disabling a rule.** It is a regular
expression over the finding's message, so it silences *some* of a rule's
findings and leaves the rest working. Disabling the rule outright gives up the
findings you have not seen yet.

Suppress a finding at one source location with a standalone comment immediately
above the reported line:

```yaml
# fastraml: ignore missing-description,missing-example
User: string
```

Use `*` instead of rule names to suppress every finding at that site. Inline
suppression comments are not supported.

## Gate CI on it

```bash
fastraml lint -w . api.raml --format text --severity error
```

Exit codes:

- `0` — no finding at the `--fail-on` severity or worse.
- `1` — at least one such finding, or a file failed to parse.
- `2` — the command line was wrong.

`--fail-on` defaults to `error`, so `warning` and `info` are reported and do not
fail the run, and a default gate passes until you raise something to `error` in
the config. `--fail-on warning` fails on warnings too. Human output ends in
`FAIL` exactly when the run exits 1, `WARN` when it exits 0 with warnings.

**Every file is linted before it exits**, so pass the whole directory and read
the full report in one run rather than fixing one file at a time.

`--severity S` is a **threshold** — S and everything worse — and means the same
on `fastraml compat`. It filters the report; it does not change the exit code.

## Find out what a run cost

```bash
fastraml lint --metrics -w . api.raml --format text
```

Read findings on stdout and metrics on stderr. Metrics contain one block per
file: the graph, rule rows, and provider rows when more than one contributed.

Read the graph line first. Building the projection every rule reads is normally
the largest single cost in the run — larger than every rule put together — so a
reader who skips it will blame whichever rule sorts to the top.

## Lint is not the audit catalogue

`fastraml query` answers whole-document questions that have no severity and no
right answer: `endpoint-tree`, `media-types`, `enums`, `type-fan-in`,
`scheme-usage`, `annotation-usage`, `recursive-types`, `error-response-types`,
`required-query-parameters`. Those are reports.

A lint rule is a *defect* with a severity and an exit code. If you are asking
"what is in this document", use `query`. If you are asking "what is wrong with
it", use `lint`.

## Write your own rule

The built-in `style` set covers format-wide RAML conventions. Organisation-specific
rules such as kebab-case paths and naming conventions remain plugins.

### The two shapes

A rule is either a **visitor** or a **document** rule, never both.

A **visitor** implements one or more methods named after what it wants to see,
and is called once per matching node. The engine runs one traversal and fans it
out, so a visitor costs its own body and nothing else. Prefer this shape.

```python
from fastraml.views.lint import Category, Context, Finding, RuleMeta, Severity


class PathsAreKebabCase:
    meta = RuleMeta(
        id='style-paths-kebab-case',
        category=Category.STYLE,
        summary='resource paths should be kebab-case',
        rationale='Mixed path casing makes a URL hard to guess and hard to grep for.',
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/user-profiles:\n  get:\n',
        bad='#%RAML 1.0\ntitle: t\n/userProfiles:\n  get:\n',
    )

    def endpoint(self, ctx: Context, iri: str, endpoint):
        segment = endpoint.uri.lstrip('/')
        if segment and segment != segment.lower():
            yield ctx.on(self.meta, 'resource path is not kebab-case', endpoint, iri=iri, path=endpoint.uri)


RULES = [PathsAreKebabCase()]
```

The visitor methods available: `unit`, `api`, `type_`, `property_`,
`pattern_property`, `parameter`, `payload`, `request`, `response`, `operation`,
`endpoint`, `trait`, `resource_type`, `security_scheme`. Implement only the ones
you need.

A **document** rule implements `run(ctx)` and is called once. Use it only for
what cannot be judged one node at a time — counting, or walking *backwards* from
a node to find what references it:

```python
    def run(self, ctx: Context):
        for iri, node in ctx.graph.nodes.items():
            if not ctx.graph.into(iri, ('inherits',)):
                ...
```

### Build findings with `ctx.on`

`ctx.on(meta, message, entity, iri=iri)` places the finding at the entity's
location and key position; pass `position=` for a more precise one. Use
`ctx.at(..., location=..., position=...)` for something that is not a model
entity, such as a fragment root. Both fill in the rule id and severity for you,
so a rule never consults the config. Keyword arguments become the finding's
`info` dict — put the variable
parts there rather than interpolating them into the message, so findings group
cleanly and tests can assert on them.

### Ship it

Export a sequence of rule instances, and declare an entry point:

```toml
[project.entry-points."fastraml.lint_rules"]
house-style = "myorg.ramlrules:RULES"
```

Installing the package makes the rules *discoverable*. **None of them runs until
your config names the plugin**, so an unrelated dependency cannot change what
your CI reports:

```yaml
lint:
  plugins: [house-style]
  extends: [recommended, house-style]
```

`fastraml lint --list-rules` prints the providing distribution for every rule,
so a surprising finding is traceable to the package that shipped it.

### Two rules for writing a good rule

**`good` and `bad` are parsed, not decorative.** Point them at RAML that
satisfies and violates the rule. A test that parses both is the cheapest way to
find out your rule fires on the wrong thing.

**Do not re-implement a rule the language already states.** A path parameter
with no declaration, a facet that does not belong to its type, an enum member
that contradicts its own type — the parser rejects all of these, so a rule for
them can never fire on a document that reaches you. If your rule needs an
invalid document to trigger, it is the wrong rule.

## Troubleshooting

- **No findings at all** — the default ruleset is `spec` only. Add `security`,
  or check `--severity` is not filtering (`info` is the default and shows
  everything).
- **`lint needs an unwrapped model`** — you are calling the Python API directly.
  Parse with `ParseOptions(unwrap=True, retain_source=True)`.
- **A plugin's rules never run** — discovery is not activation. Name the plugin
  under `plugins:` in the config.
- **`duplicate rule id`** — two rules claim one name and registration refuses
  rather than overwriting. Rename yours; a plugin silently replacing a built-in
  would change what a judgement means.
