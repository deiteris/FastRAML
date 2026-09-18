---
name: diff
description: Gate CI on RAML API compatibility with fastraml diff, and regrade changes under your own policy. Covers the exit-code gate, comparing two git revisions, the workspace-root mistake that fabricates breaking changes, the full severity table, and every field in the --json change record. Use when checking whether an API change is backwards compatible, wiring a compatibility check into CI, or disagreeing with how fastraml graded a change.
license: MIT
allowed-tools: Bash(fastraml:*) Bash(git:*) Read
---

# Gate CI on API compatibility with `fastraml diff`

Read this to set up a CI check, or to grade changes under your own policy
instead of the built-in one.

## Set up the gate

`fastraml diff` exits 1 when any change is breaking and 0 otherwise, so your CI
job needs no output parsing:

```bash
fastraml diff --no-workspace-guard "$BASE/api.raml" "$HEAD/api.raml"
```

Get the base version from git without a second checkout:

```bash
git worktree add /tmp/base "$BASE_SHA"
fastraml diff --no-workspace-guard /tmp/base/api/api.raml api/api.raml
git worktree remove /tmp/base
```

Add `--breaking-only` to keep the job log short. Leave it off if you want the
log to record the safe changes that shipped as well.

## Do not share one `-w` between the two versions

fastraml addresses included files relative to the workspace root. One root
covering both `old/` and `new/` gives the same include two different addresses,
so every included file looks removed and then re-added:

```
breaking  entity-removed
    fastraml:  id old/book.raml
safe      entity-added
    fastraml:  id new/book.raml
```

Those lines are not real changes, and they raise the breaking count. On one
measured pair of documents, a shared root reported 4 breaking changes where the
correct answer was 3.

Pass `--no-workspace-guard` instead. You can also omit `-w`, which roots each
version in its own folder, but that only works when each document is
self-contained there. If a document includes files from a parent folder, use
`--no-workspace-guard`.

## Read the severity grades

Some grades are the opposite of what you might expect, because fastraml grades
requests and responses from the caller's point of view:

- Removing a response property is **breaking**. Callers read a field that is now
  gone.
- Making a response property optional is **breaking**, for the same reason:
  callers relied on it always being there.
- Loosening a response constraint is **breaking**. A value now arrives that
  callers cannot handle. Loosening is only safe on the request side.
- Removing a request property needs **review**, not an automatic break. The server stops
  reading a value that callers still send.
- Adding a value to a response enum needs **review**. A caller that switches
  exhaustively has no branch for it.
- Adding security is **breaking**; removing it is **compatible**.

The full list:

| Severity | Rules |
| --- | --- |
| breaking | `entity-removed`, `request-property-required`, `request-property-added-required`, `request-constraint-tightened`, `request-enum-value-removed`, `response-property-removed`, `response-property-optional`, `response-constraint-loosened`, `security-added`, `security-alternative-removed`, `base-uri-changed`, `protocol-removed`, `type-changed`, `format-changed` |
| review | `request-property-removed`, `response-enum-value-added`, `reference-retargeted`, `other` |
| compatible | `entity-added`, `request-property-optional`, `request-property-added`, `request-constraint-loosened`, `request-enum-value-added`, `response-property-required`, `response-property-added`, `response-constraint-tightened`, `response-enum-value-removed`, `security-removed`, `security-alternative-added`, `protocol-added` |
| cosmetic | `documentation-changed` |

`other` covers changes no rule matched. fastraml grades it `review` so you read it
yourself rather than miss it.

## Override project policy

All parsing commands read the same configuration. Regrade a known deployment
transition without hiding it:

```yaml
compatibility:
  rules:
    - id: protocol-removed
      impact: compatible
      match:
        attribute: protocols
        before: [HTTP, HTTPS]
        after: [HTTPS]
```

Use `disabled: true` only when the change should disappear from both output and
the exit decision. A temporary CLI override is
`--rule protocol-removed=compatible`; file policy runs first, CLI policy last.

## Grade changes under your own policy

When the task requires a policy other than the built-in grades, run `diff` with
`--json` and decide from the operation, typed location, payload path and attribute:

```bash
fastraml diff --no-workspace-guard --json old/api.raml new/api.raml
```

Each line contains one change:

```json
{"kind": "removed",
 "scope": "schema",
 "operation": {"path": "/books", "method": "get"},
 "location": {"kind": "ResponseBody", "status": "200", "media_type": "application/json"},
 "path": [{"kind": "PropertySegment", "name": "title"}],
 "subject": "property",
 "attribute": null,
 "before": {"type": "string", "required": true},
 "after": null,
 "rule": "response-property-removed",
 "impact": "breaking"}
```

The fields:

- `operation` — the effective method and resource path whose caller is affected.
- `scope` — `operation` for changes to the method contract and its owned
  entities, or `schema` for changes found while walking a body or parameter
  shape.
- `location` — request body, response body/status, parameter, security, transport
  or the operation itself, with media type/status/binding carried as fields.
- `path` — present only for `scope: schema`; `[]` is the schema root and property,
  array-item and union-member segments identify a nested shape.
- `subject` — property, constraint, enum, type, security setting and so on.
- `attribute`, `before`, `after` — which facet changed and its two values.
  Operation additions/removals use their own record kinds and subsume children.
- `rule` and `impact` — the built-in policy result.

Ignore `impact` when applying the replacement policy. Add `--severity` only
when the task excludes part of the change set.

The JSON output is not grouped. Grouping appears only in the human-readable
output. Markdown descriptions are first-line summaries; use JSON whenever the
complete display name, description or before/after value is required. In
Markdown paths, identifier-like properties use `.name` and names containing
punctuation use JSON bracket notation such as `$["user.name"]`.
