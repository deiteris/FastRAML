# 21 - Language service

This document owns `fastraml/service/`: a workspace of editor buffers, one
lenient parse per root, and the queries an editor asks of them. It holds no
RAML rule. Every answer is read from a parse (`docs/13` § 1) or a view
(`docs/16`). The LSP adapter is a protocol over it; the MCP verb will be
another (M7 of `research/language-service-plan.md`).

The design and its decisions are in `archive/language-server.md`,
`archive/language-service-architecture.md` and
`research/language-service-plan.md`, which this document supersedes where they
differ. The plan orders what comes next.

## 1. Place

`fastraml/service/` is a composition root, like `cli/`: it imports the model
and several views, and only `cli/` imports it (`docs/02` § 2;
`tests/unit/test_views.py`). It reads the folder only through
`SafeFileLoader` (`docs/03` § 5), so OS paths stay in `loaders.py`.

| Module | Holds |
|---|---|
| `service/workspace.py` | buffers, the overlay loader, roots, snapshots, staleness |
| `service/text.py` | converting a column between fastRAML and a protocol |
| `service/queries.py` | the queries, in fastRAML positions |
| `service/outline.py` | the outline, over the authorship view (`docs/16` § 10) |
| `service/hover.py`, `service/hoverdocs.py` | author-facing hover, source-key indices and explanatory prose (§ 4.2) |
| `service/datahover.py` | typed `DataNode` key and value spans, shared across value-bearing sites (§ 4.2) |
| `service/lenses.py` | code-lens sites and on-demand effective RAML type rendering (§ 4.3) |
| `service/inlays.py` | inline type/facet labels over the shared authoring indices (§ 4.4) |
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

`serving(uri)` yields the snapshots serving a file, lazily: its own root's
first when it is one, then every other root that reads it, then a parse of the
file alone when none does. A root is brought current only when the iteration
reaches it, so a request that reads the first snapshot parses one root. Which
roots read a file is known only from their snapshots, so the workspace keeps
the read set of each snapshot it drops, and tries the roots that read the file
last time before those not yet parsed, and those before the ones known not to
read it: after an edit to a library, the first root parsed reads it.
`readers()` maps every file a root read to those roots, the ones whose
diagnostics a change to it can move; it brings every root current.

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
resolves a name. The occurrence index (`docs/16` § 9) answers questions about
bound names. Source-only hover (§ 4.2), folding and selection also read the
composed YAML and the parser's structural grammar.

| Query | Reads |
|---|---|
| `definition` | the `DEFINITION` occurrence of the target; for a path, the file its `Link` resolved to |
| `references`, `highlights` | the target's occurrences |
| `hover` | source keys in the parser's grammar; occurrences and typed data targets; declaration descriptions and type signatures |
| `document_symbols` | the fragment's metadata, declaration tables, documentation and resources, grouped by section, from `key_pos` and `value_pos`; `type_expr`, else `type_name`, for a type's detail |
| `workspace_symbols` | the declarations of every snapshot, matched case-insensitively, once each |
| `links` | each `Link` occurrence in the file, with the file it resolved to |
| `folding_ranges`, `selection_ranges` | the buffer's composed `Node` tree alone |
| `type_at`, `supertypes`, `subtypes` | a type's `inherits` and `alias`, and the declarations naming it |
| `diagnostics` | `RamlError.chains()` and the lint findings |
| `tree` | `build_tree` (`docs/16` § 6) as JSON text, only on an unwrapped model |

**Stopped parses.** A query on a snapshot that stopped early answers from the
stages it completed. A declaration is an occurrence after decoding; a type
name used in an expression is one only once P7 bound it. No query raises on a
snapshot that stopped at any stage (`test_service_queries.py`).

**Outline.** Every entry is read from the model, and grouped as the file
groups it, the way a code outline reads: `title`, `version` and `baseUri` with
their values, then one section per declaration table (`uses`, `types`,
`annotationTypes`, `traits`, `resourceTypes`, `securitySchemes`), the
documentation, and the resources. A resource holds what it applies (`type`,
`is`, `securedBy`, each one entry naming them), its `uriParameters` and its
methods and resources; a method, what it applies, its `queryParameters`,
`headers`, `queryString`, `body` by media type and responses; a response, its
`headers` and `body`; a security scheme, its `describedBy`.

Declarations only. A type holds its properties, pattern properties, an inline
`items` and a `facets` section, not its examples, annotations or facet values.
An optional property or parameter is named `name?`. A type's detail is its
type as written (`common.Address`, `Book[] | Review`), `type_expr` from the
model; `JSON schema` for a `JsonShape`; or where nothing was written,
`type_name`, as hover names it; its icon
is its kind: object, array, union, enum or a scalar's. A resource's, method's
or response's detail is its `displayName`, a response's else its description.

The model keeps no position for a section's key (`types:`, a method's
`headers:`), so a section spans its entries and selects the first.

What each entry lists is the authorship view's (`docs/16` § 10): a type's own
members, not those it inherits, so an inherited property is outlined under the
type that declared it; an array's `items` only where it wrote them
(`items_written`), not for `Book[]` nor for a parent's; the bodies of one
`body:` with no media type as one entry under all their names
(`media_type_written`). A member written in another file, an `!include`d
type's, is that file's. A method, response, body or parameter a template
contributed is written in the template, and is listed under nothing: it lies
outside its parent's span, which the view tests, since no template is declared
inside a resource or method (`docs/16` § 10).

A file outlines what it wrote, selected by `location` over the model its
snapshot parsed. An Extension or Overlay lists the types and other
declarations it added to the master's tables, and, under a master resource's
path, the methods and resources it added there: a section spanning them, since
the resource's key it wrote is not in the model. The master lists its own.

A trait or resource type is listed by name alone. Its body is decoded only
where it is applied (`docs/08` § 5), and the model keeps it undecoded, so there
is nothing else to list. A documentation item has no key and selects its title.

A fragment file that is one declaration is that declaration, named after the
file, so its outline holds the body at the top: a DataType's or annotation
type's members, a SecurityScheme's `describedBy`, a DocumentationItem's title,
beside its `uses:`. A Trait or ResourceType file lists its `uses:` only, and a
NamedExample file its `uses:` only, since examples are not outlined.

**Hierarchy items across snapshots.** A type hierarchy item is found again by
where its name is written, never by its id: ids do not survive a reparse
(`archive/language-server.md` § 3.2).

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

An included value's diagnostic belongs to the file containing the value, so a
JSON example error appears in the included `.json` file rather than the API
file. An invalid UTF-8 include instead points to its directive (docs/03 § 4.2).

`diagnostics(snapshot)` has an entry only for a file holding a diagnostic.
Clearing what a client showed before is the adapter's (§ 5).

### 4.2 Author-facing hover

Hover leads with what the source token means: a data type, object property,
bound parameter, library namespace, template, annotation or RAML field.
Descriptions and template usage are rendered as Markdown, with all their
paragraphs. A type alias is identified separately from a specializing mapping;
a property or parameter using a named type says that it uses that type.
Presence is shown as required or optional, separately from nullable values.
Parameter roles distinguish base-URI, resource URI, query and header bindings.

Hover focuses on the declaration's complete documentation and compact type
information. It does not repeat the supplied data value, declaration location
or effective member/constraint listing. Go-to-definition supplies locations;
the effective-type lens (§ 4.3) opens the reading view on demand. Type signatures
name array items and union alternatives without expanding their structures.
When one authored site has several materializations, hover identifies that
the type shown is the first.

RAML keys are selected by exact source spans and the structural positions in
`parser/syntax.py`, shared with extension merge (`docs/19` § 3.1). Thus `type`
explains data-type specialization, a resource-type application or an
authentication mechanism according to its context. Methods and response status
codes have HTTP reference links. Data beneath examples, defaults, annotation
values and application arguments is opaque to keyword help; so are unknown
facet values. Expanded YAML alias children are not reclassified at their
anchor's authored position. Typed fragments use their own root grammar.
Headerless includes inherit their receiving structural position, including
through nested includes. Conflicting receiving contexts suppress keyword help.
External JSON Schema keywords are not presented as RAML keywords.

Built-in type tokens have explanations, type-specific facet names and format
choices from the parser's tables. Field and built-in help explains the effect
of a declaration, illustrates its use with concrete examples, and distinguishes
related constructs such as presence versus nullability or unions versus multiple
inheritance. It is documentation rather than a one-line restatement of the key.

User-defined facet declarations show their full authored description and the
type of the facet value. Supplied facet keys show that same declaration
documentation. P7 records their bindings
(`Raml.custom_facet_refs`, docs/07 § 2); hover does not walk ancestors to decide
which declaration accepts a name. Required custom facets are labeled as
requirements on subtypes, not payload-property presence. A known facet's
documentation remains available when its supplied value is invalid; unknown
facets get no invented field documentation.

Typed data hover starts from a `DataNode` and its bound shape, not the RAML
keyword grammar. Custom-facet values use P7's binding; annotation values use
P8's `defined_by`. Examples (including named and included examples), defaults
and enum members use their owning type. The same traversal shows declaration
descriptions and expected types on nested field keys and scalar
values. Array elements follow `items`; aliases and recursive shapes follow
their model edges while the finite data bounds traversal.

`ObjectShape.property_for` decides explicit and pattern-property matching;
`UnionShape.select` supplies discriminator selection (docs/05 § 4, docs/05 § 6).
An invalid value does not hide a known field's documentation. Without a
discriminator selection, hover shows the possible field declarations rather
than choosing whichever alternative happens to validate. An unknown
discriminator selects none. Additional fields with no governing declaration
have no typed-field help. Data keys named `type`, `properties` or other RAML
keywords remain data keys; they show only their type's field documentation.

Every positioned value carries its own URI, so nested data includes are
indexed in the file that authored the token (docs/03 § 6). Scalar and key
ranges exclude surrounding comments and whitespace. A collection's whole
extent is not a fallback for an unknown child. Inline JSON has only the
encoded scalar's root span; hover does not invent spans for decoded children.

Source-only primitive tokens, including those
in an unapplied template, are read through the type-expression parser and
checked against the source text. No source query binds a reference. Explanatory
prose is a documentation catalogue, not a table that accepts fields or overrides
parser diagnostics.

Hover indices and formatted subjects are lazy per snapshot. Source keys are indexed once per queried
file from retained nodes, or a composition of its retained text when source
trees were not retained. These nodes and indices die with the snapshot.
The typed-data token index is also lazy and built once per snapshot; formatted
data targets are cached. The same typed-data targets supply go-to-definition
for nested field keys and scalar values.

### 4.3 Effective-type code lenses

`code_lenses(snapshot, uri)` lists authored type and annotation-type declaration
sites with an available effective model. It does not render them. The adapter
returns a **Show effective type** command only to a client that advertises
`fastraml.showEffective` in its initialization `commands` list.

The command sends `fastraml/effectiveType` with `textDocument`, `root`,
`position` and `name`. The server checks the current source site and name,
requires completed unwrap and an unmarked subject, and uses the existing
`views.render.render` reading view (docs/16 § 4). Source positions, not model
IDs, survive between requests. A stale name/site or unavailable effective
model returns null. The parse root preserves the context of a library file.
Rendering uses `depth=None`: nested objects, array items, union alternatives
and custom-facet declaration shapes are fully expanded. Recursive references
remain named instead of being unfolded indefinitely.

The VS Code client opens the result beside the source in a read-only
`fastraml-effective:` RAML document. Clicking again refreshes that document's
content from the current snapshot. A code lens is a clickable action, not a
container for expanded prose or data. The full API viewer still uses
`fastraml/tree` independently.

### 4.4 Inlay hints

Inlay hints are the inline labels beside source text, distinct from the
clickable code lenses above declarations. `inlay_hints(snapshot, uri, span)`
returns hints only within the requested source range. It reuses hover's
authored source sites and typed `DataNode` token index, and never resolves a
name or repeats type matching in the adapter.

A declaration without an authored type can show its inferred type, such as
`[object]`. Explicit types, including a multiple-inheritance list, are not
repeated. Supplied custom-facet keys and annotation, example and default roots
show their expected value type; nested typed data fields do too. For example,
`stewardedBy` can show `[string]`, and `sources` can show `[string[]]`.
These facts remain available when the supplied value is invalid. Fields with
no governing declaration get no invented hint.

Unwrapped declarations can also show concrete inherited constraints after
their authored type reference: `isbn: Isbn` can show `[length: 13]`, rather
than inserting a facet dump between the field name and its colon. Length,
item/property counts and numeric bounds use an exact value, an inclusive
range (`2..40`), or `≥`/`≤` for a one-sided bound. Short remaining facets can
fit too. Authored constraints and unpositioned defaults are not repeated.
Long regexes and structured values are never cut mid-value into an inline
preview, nor replaced with a generic badge. A tooltip preserves every inherited
constraint in full; the effective view remains the place for a complete listing.

Type components and constraint summaries are bounded to 32 characters. An
ambiguous union shows one type expression, such as `[string | integer]`,
abbreviating excess alternatives with `…` while retaining all their
documentation in its tooltip. Each displayed type part carries its declaration
location only when that target is unambiguous; same-type candidates retain all
their documentation without choosing an arbitrary navigation target.

Source ranges are filtered by the actual hint anchor, after a key or type
reference, including when the range starts inside that token. Declaration
hints are cached per file and range queries use sorted anchor indices.
The adapter reports type-kind hints for types, leaves the kind unset for
constraint summaries, converts all
positions in the negotiated encoding and adds display padding without editing
the source. VS Code uses its standard **Editor: Inlay Hints** setting.

## 5. The LSP adapter

`fastraml lsp [--config FILE] [-r]` serves LSP on stdin and stdout. pygls is
the extra `fastraml[lsp]`, imported inside the verb. The adapter converts
positions (§ 3) and shapes, and nothing else: every answer is a query's.

**Run.** The whole server runs under `tuned_gc` (`docs/12` § 6), and on one
event loop, so the workspace takes no lock. The linter is built once, before
anything is served, and every workspace shares it: every parse reads it, so a
`lint:` section naming a rule set, rule, category or plugin that is not
registered, which its type cannot check, stops `fastraml lsp` with the error. The folders are the client's
workspace folders, or its root URI; the `roots` globs of § 2 come as
`initializationOptions.roots`. A change of folders builds a new workspace
holding the open buffers. A URI that is not `file:` is not served, and no
diagnostic is published to one or links to one as related information: a
remote document `--remote` read holds no lines here.

Navigation also omits remote locations: definitions, references, workspace
symbols and type-hierarchy items require a local file whose positions the
service can convert. `textDocument/documentLink` still exposes the remote
target URL on an `!include` or `uses:` path written in a local file.

**Sync.** pygls applies incremental edits to its copy of a document, and the
adapter hands the whole text to `Workspace.change`. File changes come from
`workspace/didChangeWatchedFiles`, registered for `**/*` where the client
allows it.

**Diagnostics.** A change publishes 0.3 s after the last one; an open, at
once. Parser diagnostics go first; the lint tier follows on the next turn of
the loop, and a change that comes in between postpones it. A request never
waits: a query parses whatever is stale.

The parser tier publishes a file without its lint findings, so they are
cleared for that turn and shown again by the lint tier: the findings of the
model the edit replaced are not the new model's. A known limit, not a rule.

A file shows the diagnostics of every root that reads it (`readers`),
merged and deduplicated; a file no root reads shows its own only while it is
open. Each root remembers the files it published diagnostics in. A file is
sent only if it holds diagnostics now or held some then, so a file a root
stopped reading is cleared and a clean file is never sent. `data` is the
diagnostic's `info`.

**Quick fix.** A code action on a lint diagnostic inserts `suppression`'s line
(§ 4.1). A parser diagnostic gets none.

**Features.** Definition, references, highlight, hover, document and
workspace symbols, links, folding and selection ranges, type hierarchy, and
effective-type code lenses (§ 4.3), and inlay hints (§ 4.4).
Each reads the snapshots `serving(uri)` yields (§ 2) as follows, and brings
current only those it reads:

| Request | Reads |
|---|---|
| document symbols, links, code lenses, inlay hints, `fastraml/tree` | the first snapshot |
| `fastraml/effectiveType` | the requested parse root, checked against the current declaration site and name |
| definition, highlight, hover, type hierarchy, supertypes | the first snapshot that answers |
| references, subtypes | every snapshot, answers deduplicated |
| workspace symbols | every root |
| folding, selection | the buffer's text alone |

A file that roots bind differently (a master an Overlay merges into, a
template applied with different arguments) answers from the first. References
and subtypes span roots, because a use in any root is a use.

**Tree.** `fastraml/tree`, with `{textDocument: {uri}}`, answers `tree`'s
text, or `null` for a parse that stopped before unwrap. A root answers for
itself; any other file, for the first root reading it. Text rather than a
value, so an integer larger than a double reaches a JavaScript client as
written. It is the preview's source in `contrib/fastraml-vscode`
(`docs/17` § 4).

**Latency.** On `large`, an edit costs 475 ms and allocates 48.8 MB before
its parser diagnostics, against 366 ms for a plain `unwrap+validate` parse
(`python -m bench run --bench large --config service`). About a quarter of
the parse composes the unchanged libraries: the most a compose cache (G8)
could save.

## 6. Verification

- `test_service_text.py`: conversion in each encoding, both ways.
- `test_service_workspace.py`: roots, buffers over the disk, the sandbox, and
  when a snapshot is stale.
- `test_service_queries.py`: each query on one document with a library, a
  DataType include, a trait and a resource type; every query on a parse
  stopped at each stage; an Extension's outline; and, over the TCK, that every
  outline entry holds its selection and lies in its parent.
- `test_service_hover.py`: contextual field meanings, full Markdown prose,
  authored and inherited summaries, aliases, optional versus nullable values,
  source-only template help, token boundaries, opaque data, educational examples,
  and user-defined facet descriptions at declarations and supplied keys.
- `test_service_data_hover.py`: shared nested-field and scalar-value help for
  custom facets, annotations, examples, defaults and enums; array items,
  inheritance, patterns, discriminated and ambiguous unions, recursive types,
  nested includes, invalid values and unavailable inline-JSON child spans.
- `test_custom_facet_bindings.py`: all unwrap/validate combinations and P10's
  independence from binding publication.
- `test_service_lenses.py`: on-demand effective rendering, unavailable models
  and stale source sites; `test_lsp.py` covers the code-lens command round trip.
- `test_service_inlays.py`: inferred declaration types, expected data-field
  and supplied-facet types, concrete inherited constraints at type references,
  explicit-type suppression, ambiguity, range filtering and navigation;
  `test_lsp.py` checks UTF-16 positions and clickable inlay-label locations.
- `test_loaders.py`: `SafeFileLoader.contains` and `files`.
- `test_lsp.py`: `fastraml lsp` driven over stdio by pygls' client, one
  request per feature, the column after an astral character, clearing, the
  quick fix, `fastraml/tree`, that the run is tuned, and which roots a
  request parses.
