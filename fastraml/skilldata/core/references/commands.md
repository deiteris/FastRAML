# Complete flag reference

Every command and every flag. The core guide covers what to reach for; this
covers what each flag does.

## Flags shared by every command

These work on every command that takes a FILE (`skills` parses no RAML, so it
ignores them):

- `-w ROOT`, `--workspace-root ROOT` — confine file reads to this directory.
  Defaults to the folder holding the file you named, which is why an `!include`
  pointing at a parent folder fails until you set this.
- `--no-workspace-guard` — read any path the process can reach. Use only on
  files you trust.
- `-r`, `--remote` — allow `!include` over HTTP and HTTPS. Needs `httpx` or
  `requests` installed; fastraml depends on neither, so it builds a client from
  whichever it finds.

## `fastraml validate FILE [FILE ...]`

Parse, unwrap and validate. Prints nothing for a valid file.

- `-v`, `--verbose` — report each file and its timing.
- `-vv` — also report the YAML backend and the model counts.
- `--json` — one JSON object per file, as JSON Lines. Writes nothing to stderr.

Checks every file and exits 1 at the end, rather than stopping at the first
failure.

## `fastraml lint FILE [FILE ...]`

Check the effective document against named rules. Exits 1 when any finding is at
`error` severity; `warning` and `info` do not fail the run.

- `--config FILE` — lint configuration in YAML: which rulesets, categories and
  rules are enabled, and at what severity.
- `--severity S` — show this severity **and everything worse**. Values: `error`,
  `warning`, `info`. Default `info`, which shows everything. Filters the report;
  does not change the exit code.
- `--rule ID[=SEVERITY|off]` — enable one rule, enable and regrade it, or disable
  it for this run. Repeat for different rules. Overrides the configuration file;
  duplicate IDs are errors.
- `--format human|text|json|summary` — a grouped terminal report, compact
  uncoloured records, a versioned integration document, or counts per rule.
  Default `human`. Pass `--format text` unless the task explicitly requires
  another representation.
- `--no-color` — disable severity colours in human output. Colour is already
  disabled by `NO_COLOR`, a pipe, and `-o`.
- `--max-findings N` — show at most `N` findings across the run. Default 1,000;
  `0` disables the bound.
- `--max-findings-per-rule N` — show at most `N` findings from one rule. Default
  100; `0` disables the bound. Truncated reports retain complete counts.
- `--list-rules` — every rule with its category, default severity and providing
  distribution, then exit. Needs no document.
- `--explain RULE` — one rule's summary, rationale and its good and bad RAML,
  then exit. Needs no document.
- `--metrics` — what each rule and provider cost, written to **stderr** so
  stdout stays parseable.

Only the `spec` rules run by default. The OWASP `security` set and any plugin
rules are opt-in through `--config`. See `fastraml skills get lint`.

Lints every file before exiting, rather than stopping at the first with
findings.

## `fastraml info FILE`

Print the YAML backend, the parse time, and counts of fragments, types, shapes,
endpoints and annotations. Takes only the shared flags.

## `fastraml list FILE [PATTERN]`

List what the document declares, as `KIND  file:line  NAME`.

- `PATTERN` — keep only names containing this. Substring match, ignores case.
- `--kind KIND` — keep only this kind. Repeat the flag to allow several.
- `--json` — one JSON object per entry, with `kind`, `name`, `iri` and `at`.

Kinds are `Type`, `EndPoint`, `Operation`, `Trait`, `ResourceType` and
`SecurityScheme`. Exits 1 and prints `nothing` to stderr when nothing matches.

## `fastraml show FILE NAME`

Print one type, endpoint or operation with everything merged in, as YAML.

- `NAME` — a declared name, or a whole node IRI.
- `--depth N` — how many levels of nested type to expand. Default 1, which names
  nested types instead of opening them.

Exits 1 for a trait or resource type, which has no merged form of its own, and
tells you where it was declared and how many places apply it.

## `fastraml refs FILE NAME` and `fastraml deps FILE NAME`

One traversal in two directions. `refs` finds everything that can carry the
name; `deps` finds everything the name is built from. Each result is a route.

- `NAME` — a declared name, or a whole node IRI.
- `--kind KIND` — keep only results of this kind. Repeat to allow several.
- `--depth N` — stop the walk after N hops. Default: no limit.
- `--limit N` — print at most N results. Default 50; `0` means all. The
  remainder is reported on stderr.
- `--json` — one JSON object per result, with `kind`, `iri`, `at` and `route`.

## `fastraml compat OLD NEW`

Compare two versions and grade each change. Exits 1 when any change is breaking.

- `--breaking-only` — report only breaking changes. Still exits 1 if any.
- `--severity S` — report this severity **and everything worse**. Values:
  `breaking`, `review`, `compatible`, `cosmetic`. Default `cosmetic`, which shows
  everything. `--breaking-only` is `--severity breaking` said shorter.
- `--json` — one JSON object per change, carrying the grading inputs.
- `--rule ID=IMPACT|off` — temporarily regrade or hide a compatibility rule;
  repeat for more. File configuration runs first.
- `-o FILE`, `--output FILE` — write the report to a file instead of stdout, as
  UTF-8 with LF newlines. Prefer it to a redirect here for a second reason: the
  command exits 1 by design when anything is breaking, so `> report.md` leaves a
  failed command and no way to tell a report that was written from one that was
  not. An unwritable path is reported instead of the verdict. Carries `--json`
  output too.

Workspace roots control which files each version may read; they do not identify
changes. See `fastraml skills get backward`.

## `fastraml tree FILE`

Print the whole effective document as addressed JSON.

- `--positions` — print the source span of every declaration instead of the
  document.

## `fastraml serve FILE`

Serve the document in a browser over the viewer bundle. Needs `fastraml-viewer`
(`fastraml[serve]`); without it the command names the package and exits 1.

- `--host H` — the interface to bind. Default `127.0.0.1`, loopback only.
- `--port P` — the port. Default 8000.

Parses the way the view verbs do, so a bad example does not stop it. The chosen
URL is printed on stderr; Ctrl-C stops the server.

## `fastraml graph FILE`

Project the model as a graph.

- `--format nt|turtle|dot|json` — N-Triples, Turtle, Graphviz DOT, or plain
  JSON. Default: `turtle`.

## `fastraml openapi FILE`

Convert the effective API to OpenAPI 3.0.3.

- `--format yaml|json` — YAML (default) or JSON.
- `-o FILE`, `--output FILE` — write the document to a file instead of stdout.
  The file is UTF-8 with LF newlines regardless of shell or platform, which is
  why this flag exists rather than a shell redirect.

The exit code stays 0 when the conversion drops information. Each dropped or
substituted piece is reported as a `warning:` line on stderr.

## `fastraml query [FILE ...]`

Run SPARQL over the graph. Needs `pyoxigraph`.

Give the query in exactly one of three ways:

- `-q SPARQL` — the query text.
- `-Q FILE.rq` — a file holding the query.
- `-n NAME` — a query from the built-in catalogue. Checked before the document
  is opened, so a typo reports the typo.

Other flags:

- `--list` — list the catalogue and exit. Needs no document and no `pyoxigraph`.
- `--show NAME` — print one catalogue query rather than running it. Same.
- `--json` — JSON Lines rather than a table.

`SELECT` prints TSV, `ASK` prints `true` or `false`, and `CONSTRUCT` and
`DESCRIBE` print N-Triples.

## `fastraml skills ACTION [NAME ...]`

Print the agent guides this CLI ships with.

- `fastraml skills list` — the available guides and what each covers.
- `fastraml skills get NAME` — print one guide. Accepts several names.
- `--full` — also print each guide's reference files.
- `--json` — structured output rather than Markdown.

Parses no RAML, so it ignores the shared flags.

## `fastraml --version`

Print the version and exit.

## Exit codes

- `0` — valid, or the command produced its answer.
- `1` — invalid document, unresolved name, ambiguous name, nothing matched, a
  breaking change in `compat`, an `error` finding in `lint`, or a missing optional
  package.
- `2` — the command line was wrong, such as an unknown command or a missing
  argument.
