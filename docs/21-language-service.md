# 21 - Language service

This document owns `fastraml/service/`: a workspace of editor buffers, one
lenient parse per root, and the queries an editor asks of them. It holds no
RAML rule. Every answer is read from a parse (`docs/13` § 1) or a view
(`docs/16`). The LSP adapter and the MCP verb are protocols over it.

The design and its decisions are in `research/language-server.md` and
`research/language-service-plan.md`, which this document supersedes where they
differ.

## 1. Place

`fastraml/service/` is a composition root, like `cli.py`: it imports the model
and several views, and only `cli.py` imports it (`docs/02` § 2;
`tests/unit/test_views.py`). It reads the folder only through
`SafeFileLoader` (`docs/03` § 5), so OS paths stay in `loaders.py`.

| Module | Holds |
|---|---|
| `service/workspace.py` | buffers, the overlay loader, roots, snapshots, staleness |
| `service/text.py` | converting a column between fastRAML and a protocol |
| `service/queries.py` | the queries, in fastRAML positions |
| `service/lsp.py` | the LSP adapter, over pygls (§ 5) |

## 2. Workspace

A `Workspace` has one or more folders, given as `file://` URIs. Each folder is
a sandbox root: a file is read through the innermost folder holding it, and a
file in no folder is not read.

**Buffers.** `open`, `change` and `close` keep the editor's text for a URI.
A parse reads a buffer instead of its file, but only where the folder's
sandbox would read the file: an open tab does not widen what a document may
include. `canonical(uri)` gives one spelling to the percent-encodings editors
send (`file:///c%3A/...`).

**Roots.** A root is an API, Overlay or Extension document, found by its
header (`docs/03` § 3) among the `.raml` files `SafeFileLoader.files` lists,
and among unsaved buffers. `roots=[...]` globs over folder-relative paths
replace discovery. Discovery runs again when a buffer's header line changes,
a buffer opens, or a file changes on disk.

**Snapshots.** A snapshot is one `parse_lenient` of one root, with
`unwrap=True, validate=True, retain_text=True` and the configuration's
`parser:` limits. It keeps the YAML trees too, `retain_source=True`, only when
an enabled lint rule reads them (`deprecated-schemas`, in the default set).
It records the set of files it read: every retained text, every
fragment, and every include it tried, found or not. It is built when a query
first asks for it and kept until one of those files changes. A file that
appears, a buffer opened or a file created, also drops every snapshot that
ended in an error, since the error may be the missing file.

**Memory.** A server defers full collections for its whole run (`docs/12`
§ 6), and a dropped snapshot is cyclic garbage the size of a model, which only
a full collection frees. So before it parses, the workspace collects once if
it dropped a snapshot since the last parse, whether or not automatic
collection is on. Without that, the model an edit replaced stays alive beside
the next one: on `large`, a run of edits settles at 98 MB instead of 75 MB.

`snapshots(uri)` serves a file from every root that read it, or else from a
parse of the file alone. `affected(uri)` names the roots whose diagnostics a
change to `uri` can move.

A snapshot whose entry is not RAML at all, one `parse_lenient` re-raises for
(`docs/13` § 1), has `raml is None` and the error.

**Lint** is the second tier: `Snapshot.findings` runs the linter the
configuration's `lint:` section describes, on first request, and only on a
model that reached unwrap.

## 3. Positions

fastRAML counts lines and columns from 1, in code points, with exclusive ends
(`docs/11` § 1). A protocol counts from 0, in the units of its position
encoding: UTF-16 code units by default in LSP, or UTF-8 or UTF-32 when
negotiated. `Lines(text)` converts both ways. A protocol offset inside a code
point lands on that code point, and one past the end of a line on its end.

LSP breaks lines at `\n`, `\r\n` and `\r`. YAML also breaks at NEL, LS and PS,
so on a line holding one of those the two number lines differently. That is a
known limit.

## 4. Queries

Each query takes a snapshot and fastRAML positions and answers in them. None
resolves a name. The occurrence index (`docs/16` § 9) answers every question
about names, so a name it does not hold has no answer.

| Query | Reads |
|---|---|
| `definition` | the `DEFINITION` occurrence of the target; for a path, the fragment it decoded to |
| `references`, `highlights` | the target's occurrences |
| `hover` | `render` for a type, annotation type, property or facet; the kind, parameters, `usage` and `description` otherwise |
| `document_symbols` | the fragment's declaration tables, documentation items, and resources with their methods, from `key_pos` and `value_pos` |
| `workspace_symbols` | the declarations of every snapshot, matched case-insensitively, once each |
| `links` | `include_refs` and `uses:` links, placed at their `LINK` occurrence |
| `folding_ranges`, `selection_ranges` | the buffer's composed `Node` tree alone |
| `type_at`, `supertypes`, `subtypes` | a type's `inherits` and `alias`, and the declarations naming it |
| `diagnostics` | `RamlError.chains()` and the lint findings |

**Stopped parses.** A query on a snapshot that stopped early answers from the
stages it completed. A declaration is an occurrence after decoding; a type
name used in an expression is one only once P7 bound it. No query raises on a
snapshot that stopped at any stage (`test_service_queries.py`).

**Outline.** A method is listed under a resource only when it is written
inside the resource's span. A method a resource type contributed is written in
the resource type.

**Hierarchy items across snapshots.** A type hierarchy item is found again by
where its name is written, never by its id: ids do not survive a reparse
(`research/language-server.md` § 3.2).

### 4.1 Diagnostics

Each chain becomes one diagnostic at its innermost frame with a known
position. Its outer frames with positions become related information. `code`
is the innermost message key and `info` its variables (`docs/11` § 6). A chain
with no position is placed at the start of its innermost frame's file.

A lint finding becomes a diagnostic with its rule as `code` and its severity,
under the source `fastraml-lint`. `suppression` gives the line that suppresses
it: the directive of `docs/18` § 4, at the finding line's indentation, inserted
before it. A parser diagnostic cannot be suppressed.

`diagnostics(snapshot)` has an entry for every file the snapshot read, empty
where it holds none, so a client clears what it showed before.

## 5. The LSP adapter

`fastraml lsp [--config FILE] [-r]` serves LSP on stdin and stdout. pygls is
the extra `fastraml[lsp]`, imported inside the verb. The adapter converts
positions (§ 3) and shapes, and nothing else: every answer is a query's.

**Run.** The whole server runs under `tuned_gc` (`docs/12` § 6), and on one
event loop, so the workspace takes no lock. The folders are the client's
workspace folders, or its root URI; the `roots` globs of § 2 come as
`initializationOptions.roots`. A change of folders builds a new workspace
holding the open buffers. A URI that is not `file:` is not served.

**Sync.** pygls applies incremental edits to its copy of a document, and the
adapter hands the whole text to `Workspace.change`. File changes come from
`workspace/didChangeWatchedFiles`, registered for `**/*` where the client
allows it.

**Diagnostics.** A change publishes 0.3 s after the last one. Parser
diagnostics go first; the lint tier follows on the next turn of the loop, and
a change that comes in between postpones it. A request never waits: a query
parses whatever is stale.

A file shows the diagnostics of every root that reads it (`affected`),
merged and deduplicated; a file no root reads shows its own only while it is
open. Each root remembers the files it published, so a file it stopped
reading is cleared. `data` is the diagnostic's `info`.

**Quick fix.** A code action on a lint diagnostic inserts `suppression`'s line
(§ 4.1). A parser diagnostic gets none.

**Features.** Definition, references, highlight, hover, document and
workspace symbols, links, folding and selection ranges, and type hierarchy.
A request answers from every snapshot serving the file, once each.

**Latency.** On `large`, an edit costs 477 ms and allocates 48.8 MB before
its parser diagnostics, against 360 ms for a plain `unwrap+validate` parse
(`python -m bench run --bench large --config service`). About a quarter of
the parse composes the unchanged libraries: the most a compose cache (G8)
could save.

## 6. Verification

- `test_service_text.py`: conversion in each encoding, both ways.
- `test_service_workspace.py`: roots, buffers over the disk, the sandbox, and
  when a snapshot is stale.
- `test_service_queries.py`: each query on one document with a library, a
  DataType include, a trait and a resource type; and every query on a parse
  stopped at each stage.
- `test_loaders.py`: `SafeFileLoader.contains` and `files`.
- `test_lsp.py`: `fastraml lsp` driven over stdio by pygls' client, one
  request per feature, the column after an astral character, clearing, the
  quick fix, and that the run is tuned.
