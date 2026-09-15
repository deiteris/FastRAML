---
name: lint
description: Check a RAML document against named lint rules with fastraml lint, configure which run and how severely, and write your own rules as a plugin. Covers the rule catalogue and its two categories, the config file, suppressing one finding without disabling its rule, the CI gate and exit codes, --metrics, and the two rule shapes a plugin implements. Use when auditing a document for style or security problems, wiring a lint gate into CI, silencing a noisy rule, or adding a house rule fastraml does not ship.
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

## Run it

```bash
fastraml lint -w . api.raml                  # the default ruleset
fastraml lint -w . api.raml --format summary # counts per rule
fastraml lint -w . api.raml --format json    # for a script
fastraml lint --list-rules                   # what is available
fastraml lint --explain optional-and-nil     # one rule, with good and bad RAML
```

`--list-rules` and `--explain` need no document and no parse, so they work in a
checkout with nothing to lint yet.

## Two categories, and only one is on by default

| Category | What it means | Default |
| --- | --- | --- |
| `spec` | Follows from RAML's own semantics | **enabled** |
| `security` | Follows from OWASP API Security | disabled |

`spec` rules are not style preferences. `optional-and-nil` fires because RAML
has two orthogonal ways to say a value may be absent and using both leaves a
consumer unable to tell "key omitted" from "key present and null".
`json-ref-siblings` fires because a draft-07 resolver silently ignores keys
beside a `$ref`.

Turn the security set on with a config file:

```yaml
extends: [recommended, security]
```

`recommended` is the `spec` set. `all` is every rule registered, including any
a plugin contributed.

## Configure it

```bash
fastraml lint --config lint.yaml -w . api.raml
```

```yaml
extends: [recommended, security]

categories:
  security:
    severity: error        # fail CI on anything OWASP flags

rules:
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

## Gate CI on it

```bash
fastraml lint -w . api.raml --severity error
```

Exit codes:

- `0` — no finding at `error` severity.
- `1` — at least one `error`, or a file failed to parse.
- `2` — the command line was wrong.

Only `error` affects the exit code. `warning` and `info` are reported and do not
fail the run, so a default gate passes until you raise something to `error` in
the config.

**Every file is linted before it exits**, so pass the whole directory and read
the full report in one run rather than fixing one file at a time.

`--severity S` is a **threshold** — S and everything worse — and means the same
on `fastraml diff`. It filters the report; it does not change the exit code.

## Find out what a run cost

```bash
fastraml lint --metrics -w . api.raml
```

Writes a second report to **stderr**, so stdout stays exactly the findings and
`--format json` stays parseable when piped. One block per file: the graph, then
one row per rule with its calls and findings, then one per provider when more
than one contributed.

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

fastraml ships no house-style rules — kebab-case paths, required descriptions,
naming conventions — on purpose. Those are yours, and a plugin is how you add
them.

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
            yield ctx.at(
                self.meta,
                'resource path is not kebab-case',
                location=endpoint.location,
                position=endpoint.key_pos,
                iri=iri,
                path=endpoint.uri,
            )


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

### Build findings with `ctx.at`

It fills in the rule id and severity for you, so a rule never consults the
config. Keyword arguments become the finding's `info` dict — put the variable
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
