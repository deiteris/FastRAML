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
replace discovery. Discovery runs again when a file changes on disk. A
buffer that opens, closes or changes its header line decides again for its
own file only: listing the folders on each open read every header, 0.23 s on
the TCK's 1011 files.

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
a full collection frees. So `collect()` collects once if the workspace
dropped a snapshot since the last call, whether or not automatic collection
is on, and the host calls it between the change that dropped one and the
parse it leads to: the adapter, after the pause and before the parser tier
(§ 5). Without that, the model an edit replaced stays alive beside the
next one: on `large`, a run of edits settles at 98 MB instead of 75 MB. A
parse never collects: collecting before one put a full collection over every
live snapshot in the request, 14 ms over the TCK's 1011.

`snapshots(uri)` serves a file from every root that read it, or else from a
parse of the file alone. `readers()` maps every file a root read to those
roots: the ones whose diagnostics a change to it can move.

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

`Workspace.lines(uri)` gives them for the text a parse reads: a buffer's,
split once per version and only when a position in it is first converted, or
a closed file's, split per call. A closed file's text is the one a current
snapshot kept, so its positions convert against what was parsed, without a
read from the disk.

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
| `document_symbols` | the fragment's declaration tables, documentation items, and resources with their methods, written in this file, from `key_pos` and `value_pos`; a documentation item has no key and selects its title |
| `workspace_symbols` | the declarations of every snapshot, matched case-insensitively, once each |
| `links` | `include_refs` and `uses:` links, placed at their `LINK` occurrence |
| `folding_ranges`, `selection_ranges` | the buffer's composed `Node` tree alone |
| `type_at`, `supertypes`, `subtypes` | a type's `inherits` and `alias`, and the declarations naming it |
| `diagnostics` | `RamlError.chains()` and the lint findings |
| `tree` | `build_tree` (`docs/16` § 6) as JSON text, only on an unwrapped model |

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
position. Its outer frames with positions, but for one at that same site,
become related information, followed by every frame's origin: the
constraint a value broke (`docs/11` § 3.1). `code`
is the innermost message key and `info` its variables (`docs/11` § 6). A chain
with no position is placed at the start of its innermost frame's file.

A lint finding becomes a diagnostic with its rule as `code` and its severity,
under the source `fastraml-lint`. `suppression(line, rule)` gives the line
that suppresses it, from the text of the finding's line: the directive of
`docs/18` § 4, at that line's indentation, inserted before it. The caller has
the line from `Lines`, so the document is not split again, and its lines are
numbered as the protocol numbers them. A parser diagnostic cannot be
suppressed.

`diagnostics(snapshot)` has an entry only for a file holding a diagnostic.
Clearing what a client showed before is the adapter's (§ 5).

## 5. The LSP adapter

`fastraml lsp [--config FILE] [-r]` serves LSP on stdin and stdout. pygls is
the extra `fastraml[lsp]`, imported inside the verb. The adapter converts
positions (§ 3) and shapes, and nothing else: every answer is a query's.

**Run.** The whole server runs under `tuned_gc` (`docs/12` § 6), and on one
event loop, so the workspace takes no lock. The folders are the client's
workspace folders, or its root URI; the `roots` globs of § 2 come as
`initializationOptions.roots`. A change of folders builds a new workspace
holding the open buffers. A URI that is not `file:` is not served, and no
diagnostic is published to one or links to one as related information: a
remote document `--remote` read holds no lines here.

**Sync.** pygls applies incremental edits to its copy of a document, and the
adapter hands the whole text to `Workspace.change`. File changes come from
`workspace/didChangeWatchedFiles`, registered for `**/*` where the client
allows it.

**Diagnostics.** A change publishes 0.3 s after the last one; an open, at
once. Parser diagnostics go first; the lint tier follows on the next turn of
the loop, and a change that comes in between postpones it. A request never waits: a query
parses whatever is stale.

A file shows the diagnostics of every root that reads it (`readers`),
merged and deduplicated; a file no root reads shows its own only while it is
open. Each root remembers the files it published diagnostics in. A file is
sent only if it holds diagnostics now or held some then, so a file a root
stopped reading is cleared and a clean file is never sent. `data` is the
diagnostic's `info`.

**Quick fix.** A code action on a lint diagnostic inserts `suppression`'s line
(§ 4.1). A parser diagnostic gets none.

**Features.** Definition, references, highlight, hover, document and
workspace symbols, links, folding and selection ranges, and type hierarchy.
A request answers from every snapshot serving the file, once each.

**Tree.** `fastraml/tree`, with `{textDocument: {uri}}`, answers `tree`'s
text, or `null` for a parse that stopped before unwrap. A root answers for
itself; any other file, for the first root reading it. Text rather than a
value, so an integer larger than a double reaches a JavaScript client as
written. It is the preview's source in `contrib/fastraml-vscode`
(`docs/17` § 4).

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
  quick fix, `fastraml/tree`, and that the run is tuned.
