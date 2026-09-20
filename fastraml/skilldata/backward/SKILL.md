---
name: backward
description: Compare RAML API versions for backward compatibility and breaking changes with `fastraml compat`. Use when explaining a compatibility report, gating CI on API breaks, or configuring compatibility policy. Covers model-native method and schema changes, exit behavior, JSON records, project overrides, and git-revision workflows. Do not use for textual diffs.
license: MIT
allowed-tools: Bash(fastraml:*) Bash(git:*) Read
---

# Check backward compatibility

Use this guide when the question is whether a new RAML API remains compatible
with callers of an old version. Run `fastraml compat`; it compares the two
effective API models after resolving includes, libraries, traits, resource
types, security inheritance, and type inheritance.

Do not infer compatibility from a textual YAML diff or from graph IRIs. Those
represent authored structure, not the callable contracts this command grades.

## Usage

Pass the old document first and the new document second:

```bash
fastraml compat old/api.raml new/api.raml
```

1. Put the caller's existing contract first.
2. Put the proposed contract second.
3. Run the comparison before judging the source edit.
4. Read every `review` result before approving the change.

Exit status:

- `0`: no configured change has impact `breaking`.
- `1`: at least one configured change is `breaking`, or fastraml fails to read a
  document or configuration.
- `2`: the command line is invalid.

The exit decision uses the complete configured change list. `--breaking-only`
and `--severity` filter output only; they do not alter pass or fail.

Write the report to a file with `-o`, not with a shell redirect:

```bash
fastraml compat -o report.md old/api.raml new/api.raml
```

`compat` exits 1 whenever anything is breaking, so a redirect leaves a failed
command and no way to distinguish a report that was written from one that was
not. `-o` writes UTF-8 with LF newlines on every platform, reports an unwritable
path on stderr instead of the verdict, and leaves the exit code and the breaking
count as they are. It carries `--json` output too.

## Choose the workspace boundary

Each document defaults to its own directory as workspace root. That is enough
when all of its includes stay below that directory.

If either version includes a parent or sibling path, choose one of these:

```bash
fastraml compat -w /repo old/api.raml new/api.raml
fastraml compat --no-workspace-guard old/api.raml new/api.raml
```

Use a common `-w` only when it is the intended file-access boundary for both
versions. Use `--no-workspace-guard` only for trusted documents; it permits both
parses to read any path available to the process. Workspace choice affects file
access, not change identity: the compatibility walk does not compare source-file
IRIs.

## Read the Markdown report

Read the top-level sections in order:

- **Every operation**: declared once at the API root, so it reaches every
  operation — `baseUri`, `baseUriParameters`, `protocols`, `securedBy`. Read
  these first; no operation below repeats them.
- **Operations added and removed**: each entry subsumes its own request and
  responses. Treat a removal plus an addition as two facts, not a rename.
- **Several operations**: one edit that reaches more than one operation, usually
  a shared type. The `Operations` column names every operation it reaches. Decide
  it once; the sections below do not repeat it.
- One section per remaining operation, headed `` `METHOD /path` ``, holding only
  what is unique to that operation.

Within an operation, and within **Every operation**, rows are grouped by side of
the wire:

- **Request**: what the caller must send — transport, security, path and query
  parameters, request headers, request bodies, and the schemas below them.
- **Response**: what the caller will receive — status codes, response headers,
  response bodies, and the schemas below them.
- **Documentation**: prose that changed on neither side.

Each side is split again into **Removed**, **Changed** and **Added**, worst kind
first, and a report opens with a **How to read this** legend defining them.

Columns vary by table. Always present are `Where` and `Compatibility`. A
`Changed` table adds `Change` and states its transition in one `Detail` cell as
`old -> new`; `Removed` and `Added` have no `Change` column, because the heading
is the verb.

Read the value column's header before its cells: it is `Type` for a property or
parameter, `Value` for an enum member, `Scheme` for a security alternative, and
`Detail` where a table mixes them -- in which case a `What` column names each
row's subject. An enum row is the one place `Where` and `Path` address the
property rather than the thing that moved, so its `Value` cell is the member
that left or arrived. A code-fenced cell value is what the document states; an unfenced word --
`Absent`, `Required`, `Optional`, `None` -- is this report's name for a state and
appears in no document. Read a pattern, a format or a type from inside the fence
verbatim, including backslashes; a `\|` inside one is a table escape for `|`.

`Path` appears where some row reaches inside a shape
(`$.customer.email`, `$.items[].sku`, `$[/^x-/]`, `$.result<Error>`); a row with
no path addresses the coordinate in `Where` itself. `Description` appears where
the author described the entity that arrived or left. Under **Response**,
`Where` opens at the status code, because the heading has already said Response.
Rows are ordered by impact, most severe first.

Do not infer the column count; read the header row of the table you are in. Use
`--json` when a program consumes the result -- its `path` is always present on a
schema record, `[]` at a shape root.

Impacts are caller-oriented:

- `breaking`: the change stops at least one existing caller from working.
- `review`: compatibility depends on behavior the RAML model does not prove.
- `compatible`: the reported change preserves existing callers.
- `cosmetic`: documentation metadata changed without changing the wire contract.

Direction matters. Tightening a request schema rejects some existing callers;
loosening a response schema admits values some existing callers do not handle.
Read mixed enum replacements as separate additions and removals; the safer half
never hides the riskier half.

## Use JSON for automation or full values

```bash
fastraml compat --json old/api.raml new/api.raml
```

The command emits one JSON object per change. Markdown is grouped and truncates
long descriptions to a first-line summary; JSON retains complete values.

Common fields:

- `impact`, `rule`, `kind`, `subject`, `attribute`, `before`, `after`
- `location` on every change, naming the coordinate it sits at
- `operation` only where one owns the change; absent for an API-level default
- `path` only where the change reaches inside a shape; `[]` at a shape root

`scope` names the combination of those last two, so a consumer can filter on one
field instead of testing two:

| `scope` | `operation` | `path` | what it is |
| --- | --- | --- | --- |
| `api` | absent | absent | an API value such as `baseUri` |
| `api-schema` | absent | present | a `baseUriParameters` shape |
| `operation` | present | absent | a method contract |
| `schema` | present | present | an operation-owned shape |

Read `subject` and `attribute` together. `subject` names what changed and comes
from a fixed vocabulary: `base-uri`, `body`, `constraint`, `custom-facet`,
`documentation`, `enum-value`, `operation`, `parameter`, `pattern-property`,
`property`, `protocol`, `required`, `response`, `security`,
`security-alternative`, `security-setting`, `type`, `union-member`. `attribute`
names the field within it and may be `null`. Determine a value's meaning from
its subject, not from its JSON type: `true` under `required` is a requiredness,
and `true` under `constraint` is a facet value.

Read `kind` before reading `before` and `after`. On `added` and `removed` one
side is `null` by construction; that `null` records which side is populated, not
that anything else vanished. An `enum-value` record lists only the members that
left or arrived, so its `null` side means no member moved that way.

A populated side carries only what the coordinate omits, as an object. A
property and a parameter carry `type` and `required`; a security alternative
carries `name`. A `response`, `body` or `union-member` carries none of those --
its status, media type or member type is already in `location` or `path`. Any of
them may also carry `description`, the author's own prose, which no coordinate
states. An added or removed operation carries no values at all; address it by
`operation` and `rule`.

Records arrive in the order the documents declare things in. Sort them yourself
if your consumer wants them by severity; the Markdown report already does.

JSON is not rolled up. A shared type edited once yields one record per operation
that carries it, because each is a separate contract and an override matches each
on its own `operation`. The Markdown impact counts are of rolled-up entries and
will be lower than the record count; the exit code follows the records.

Read schema paths as structured arrays in JSON. Segment kinds are
`PropertySegment`, `PatternPropertySegment`, `ItemsSegment`, and
`UnionMemberSegment`. Do not parse the rendered Markdown path when JSON is
available.

Example schema record:

```json
{"scope":"schema","operation":{"path":"/books","method":"get"},"kind":"removed","location":{"kind":"ResponseBody","status":"200","media_type":"application/json"},"path":[{"kind":"PropertySegment","name":"discount"}],"subject":"property","attribute":null,"before":{"type":"number","required":false},"after":null,"impact":"breaking","rule":"response-property-removed"}
```

## Configure project policy

Use the common fastraml configuration file for persistent policy:

```yaml
compatibility:
  rules:
    - id: protocol-removed
      impact: compatible
      match:
        location: TransportLocation
        attribute: protocols
        before: [HTTP, HTTPS]
        after: [HTTPS]
```

Omit `operation` when the change comes from the API root, as an inherited
`protocols:` or `securedBy:` does — those changes have no operation to match,
and supplying one makes the rule match nothing. Add `operation` to narrow an
override to the method that declared it. `location` is the coordinate's class
name and works at every scope, including the API root.

Pass that file to the comparison:

```bash
fastraml compat --config fastraml.yaml old/api.raml new/api.raml
```

Apply rules in file order. A setting applies only when its `id` and every supplied
match field agree. Match fields are `operation`, `location`, `path`, `subject`,
`attribute`, `before`, and `after`. `operation` is a regular expression;
`before` and `after` are typed YAML values. An unknown `id` or `subject` makes
the command exit 1 rather than silently matching nothing; take the exact values
from `--json`.

Set `disabled: true` only to remove the matching change from both the report and
exit decision. Prefer regrading when the transition remains useful evidence.

Temporary CLI overrides run after file policy:

```bash
fastraml compat --rule protocol-removed=compatible old/api.raml new/api.raml
fastraml compat --rule documentation-changed=off old/api.raml new/api.raml
```

Use `--json` to obtain the exact rule ID and match fields before writing a
narrow override. An unknown rule ID or malformed override makes the command
exit 1 with an explanation.

## Compare git revisions

Use a temporary worktree when the base document is not already on disk:

```bash
git worktree list
git worktree add --detach ../fastraml-base "$BASE_SHA"
```

Run the comparison and record its exit status:

```bash
fastraml compat --no-workspace-guard ../fastraml-base/api/api.raml api/api.raml
```

Then clean up, whether the comparison passed or failed:

```bash
git worktree remove ../fastraml-base
```

Use a fresh path: do not remove or overwrite an existing directory to make room
for the worktree. Record the comparison's exit status before cleanup; a CI script
needs to return that status rather than the later cleanup command's status. Remove
the worktree even when the comparison finds a break. Do not overwrite the
current working tree or infer the old API by reversing an uncommitted patch.

## Failure handling

If parsing fails, fix or report that failure before interpreting compatibility;
there is no comparison result for an unreadable model. If the report contains
`review`, inspect the named operation, location, subject, and before/after values
rather than silently treating it as compatible.

## Completion

- Verify that the old and new arguments are in the intended order.
- Preserve the command's exit status in CI.
- Inspect every breaking and review result that remains after configuration.
- Keep compatibility overrides as narrow as the deployment exception.
- Prefer JSON when another program consumes the result.
- Report parse or configuration failures separately from compatibility findings.
- Stop only after the report and exit status answer the requested compatibility
  question.
