---
name: diff
description: Gate CI on RAML API compatibility with fastraml diff, and regrade changes under your own policy. Covers the exit-code gate, comparing two git revisions, the workspace-root mistake that fabricates breaking changes, the full severity table for all 24 rules, and every field in the --json change record. Use when checking whether an API change is backwards compatible, wiring a compatibility check into CI, or disagreeing with how fastraml graded a change.
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
- Removing a request property is **risky**, not breaking. The server stops
  reading a value that callers still send.
- Adding a value to a response enum is **risky**. A caller that switches
  exhaustively has no branch for it.
- Adding security is **breaking**; removing it is **safe**.

The full list:

| Severity | Rules |
| --- | --- |
| breaking | `entity-removed`, `request-property-required`, `request-constraint-tightened`, `request-enum-value-removed`, `response-property-removed`, `response-property-optional`, `response-constraint-loosened`, `security-added`, `type-changed` |
| risky | `request-property-removed`, `response-enum-value-added`, `reference-retargeted`, `reference-dropped`, `other` |
| safe | `entity-added`, `request-property-optional`, `request-property-added`, `request-constraint-loosened`, `request-enum-value-added`, `response-property-added`, `response-constraint-tightened`, `response-enum-value-removed`, `security-removed` |
| cosmetic | `documentation-changed` |

`other` covers changes no rule matched. fastraml grades it `risky` so you read it
yourself rather than miss it.

## Grade changes under your own policy

`--json` prints one object per change and includes every fact the built-in
policy used, so you can reach a different verdict from the same data:

```json
{"kind": "removed",
 "iri": "fastraml://id#/declarations/types/Book/property/title",
 "node_kind": "Property",
 "directions": ["request", "response"],
 "attribute": null,
 "before": null,
 "after": null,
 "rule": "response-property-removed",
 "severity": "breaking",
 "because": "callers read a field that has gone"}
```

The fields:

- `kind` — `added`, `removed` or `changed` for a node. `linked` or `unlinked`
  for a reference that now points somewhere else. Switching a `securedBy` from
  OAuth 2 to an API key moves no node and changes no attribute, so fastraml
  reports it as `linked`.
- `iri` — the node's address. `fastraml graph` and `fastraml tree` assign the same
  address to the same node, so you can look it up in either.
- `node_kind` — `Property`, `Payload`, `Operation`, `EndPoint`, `Parameter` and
  so on.
- `directions` — a list, not a single value. One type can be a request body and
  a response body at once, and fastraml grades it on the worse side. A record
  naming only one side would contradict its own `rule`.
- `attribute`, `before`, `after` — which facet changed and its two values.
  All three are `null` when the node itself was added or removed.
- `rule`, `severity`, `because` — the built-in verdict and the reason for it.

To apply your own policy, ignore `severity` and decide from `kind`, `node_kind`,
`directions` and `attribute`. Narrow the input first with `--severity` if you
only care about part of the space.

The JSON output is not grouped. Grouping appears only in the human-readable
output.
