# Language server and MCP server

**Status: archived, 2026-09-28.** `docs/21` describes the service as built and
supersedes this document where they differ, and
`archive/language-service-architecture.md` records the review that followed.
Kept as the design record.

**As accepted.** The § 7 options were decided as
recommended (`research/language-service-plan.md`, D3–D7). It proposes a design for a
RAML language server (LSP), and an MCP server on the same core, built from
scratch on fastRAML's parser. The numbered documents in `docs/` are normative;
this one is not, and nothing in the code depends on it. When decisions are
taken, the rules move into a numbered document (a new
`21-language-service.md`, plus the amendments listed in § 4), and this file
becomes history.

## 1. Summary

- **Requirements come from users, the design comes from the parser.** go-raml's
  `cmd/raml-lsp` is an evolving draft. It is used here only as an inventory of
  the features users expect (§ 2), not as a design to follow. The design is
  derived from how fastRAML's passes, identities, positions, scopes and
  templates work (§ 3).
- **No second model.** Every feature reads the parsed model. One derived
  structure is added: an *occurrence index* (§ 5.2), which records where each
  name is written and which entity it names. It is rebuilt from each parse
  and decides nothing.
- **One protocol-neutral service, thin adapters.** A `Workspace` owns buffers,
  parsing and snapshots. Its queries return plain records. LSP, MCP and CLI
  translate those records to their protocols (§ 5).
- **Only the parser states a rule** (`docs/17` § 1). Completion needs to know
  which keys a construct takes and which names are visible at a location.
  Both become parser-owned data (§ 4).
- **The largest prerequisite is a trustworthy partial model** (§ 4, G1;
  `research/partial-models.md`). An edited document is almost always invalid
  somewhere. The parse keeps stopping where a strict parse stops, which was
  measured to be necessary. The model it returns must then state how far the
  parse got and which entities are broken, and must keep what was built, so
  that each feature can gate on the pass it needs (§ 3.1).

## 2. Features users expect

The go-raml draft supplies the baseline users expect (✓ below). The standard
LSP features that fit RAML are added to it. *Source*: **G** means it is in the
go-raml draft, **L** means it is a standard LSP feature not in the draft, and
**F** means it is specific to fastRAML's views.

| Area | Feature | Source |
|---|---|---|
| Diagnostics | parse, resolution and validation errors, in included files too | G ✓ |
| | lint findings as warnings, with a suppress quick fix (`docs/18` § 4) | F |
| Navigation | go to definition: type names in expressions, `lib.` prefixes, traits, resource types, security schemes, annotations, custom facets, keys inside annotation values, `!include` and `uses:` targets | G ✓ |
| | find references, document highlight | G ✓ / L |
| | type hierarchy: supertypes and subtypes of a type | L |
| | workspace symbols | L |
| | document links for `!include`, `uses:` and `extends` | G ✓ (no `extends`) |
| Information | hover: types (effective form), built-ins, traits, resource types, security schemes, libraries, keys, HTTP methods, status codes | G ✓ |
| | hover on `<<param>>` in a template: its values at each application | F |
| | inlay hints: the inherited or contributed members an entity does not show | L |
| | code lens: use counts | L |
| Completion | keys for the construct at the cursor | G ✓ |
| | type expressions, including `lib.`-qualified names | G ✓ |
| | trait, resource type, security scheme and annotation names; annotations filtered by `allowedTargets` | G ✓ (no filter) |
| | enumerated values (`format`, `protocols`, `mediaType`, security scheme `type`, booleans) and status codes | G ✓ |
| | `!include` and `uses:` paths | G ✓ |
| | keys inside annotation, custom facet and example values | G ✓ |
| | template parameters: `<<resourcePathName>>` and declared variables | L |
| | signature help: a trait's or resource type's parameters in `is: [paged: {…}]` | L |
| Editing | rename, with prepare | L |
| | folding and selection ranges | L |
| | semantic tokens | L |
| Workspace | many root documents; unsaved buffers visible to dependents | G ✓ (a pinned root) |
| | workspace sandbox; remote includes opt-in | G ✓ |
| Extras | effective view of a type, endpoint or operation (virtual document) | F (`fastraml show`) |
| | export to OpenAPI or JSON Schema, list types | G ✓ (JSON-LD is out of scope) |

## 3. What the parser's construction implies

Each subsection states a property of the parser and the design consequence
for the server.

### 3.1 Features depend on passes

The pipeline is P0–P10 (`docs/02` § 1). A feature can serve only what the
passes it needs have produced:

| Feature | Needs |
|---|---|
| document links, folding, selection ranges, key completion (syntax) | P1 (composed `Node` trees), P3 (`uses:`) |
| document symbols for declarations | P2 |
| document symbols for endpoints, trait and resource type navigation | P4 |
| security scheme navigation | P5 |
| type-name definition, references, rename, semantic tokens | P7 |
| annotation navigation, annotation-value keys | P8 |
| effective hover, type hierarchy with inherited members, inlay hints | P9 |
| validation diagnostics | P10 |

**Consequence:** each feature gates on the last pass that completed, so the
model must record that pass (G1, signal S1 in `research/partial-models.md`).
Stopping at the failing pass stays: continuing was measured to multiply
diagnostics. What the server needs is a partial model that keeps what was
built, marks what is broken, and says how far it got. Features then degrade
by pass, and a later refinement may let contained errors pass
(`research/partial-models.md` § 7).

### 3.2 Identity is per parse

Entity IDs come from one counter per parse (`docs/02` § 3). `clone` keeps
them (`docs/07` § 6). P9 rebuilds `Raml.shapes` and can replace a declaration
with a flattened copy under the same ID (`unwrap_shapes`), so an object
reference taken before P9 can be stale while its ID is not.

**Consequences:**

- Every index keys on entity ID and is resolved against the post-P10 model. It
  is built once, after the last pass. For example, a `TypeExprRef.resolved`
  may hold a pre-P9 object, so read its `id`.
- IDs are not stable across parses. A request that spans two snapshots
  (`prepareRename` followed by `rename`, a code lens resolved later) carries a
  URI and a position, never an ID, and resolves them again in the new
  snapshot.

### 3.3 Positions and locations

- Positions are 1-based code points with exclusive ends (`docs/11` § 1).
  `Node` has exact end marks from PyYAML, so ranges need no re-scanning of
  text. LSP defaults to UTF-16, so the adapter converts, or negotiates
  `positionEncoding: utf-32` (LSP 3.17).
- A type-expression column is rebased onto the scalar's start and is off by
  the quote for a quoted scalar (`docs/11` § 3) (G3).
- An entity's `location` and its anchor's location may differ legitimately
  (`docs/08` § 4.2). The file a *name* was written in is a third fact. For
  `type: <<item>>`, the substituted scalar keeps the template's position
  (`docs/11` § 3), while `location_of` names the caller's namespace. A
  `TypeExprRef` has no URI of its own (G2).
- Document provenance for Overlays and Extensions is cleared when the parse
  ends (`Raml.authorship`, `docs/19` § 5.3). Only what entities recorded
  during decoding survives, so any authored file the server needs must be
  recorded then.

**Consequence:** every occurrence carries an explicit authored URI, fixed by
the parser at decode time. Each occurrence is checked by one law: the source
text at its span equals the name it records (§ 9).

### 3.4 Templates are YAML until applied

A trait or resource type body stays a `Node` tree (`TemplateDefinition.source`)
and is decoded only where it is applied, in the scope rules of `docs/08` § 4.
It carries `declared_variables` and a `variable_index`. Each application is a
`DirectiveRef` with the caller's `params` nodes and `resolved`.

**Consequences:**

- Inside a template body there is no semantic model at the template's own
  position, only in its applications. Features there come from three sources:
  1. syntax: keys from the grammar for a method or resource;
  2. names in the template's anchor scope;
  3. its applications: which values each `<<param>>` receives, and what the
     applied result became.
- An unapplied template yields no occurrences and no diagnostics (G7).
- A type written in a template and applied N times produces N shapes that
  share one span. Occurrences are deduplicated on `(uri, span, target)`.
- Signature help and parameter completion read `declared_variables` at the
  definition and `params` at the application. Hover on `<<param>>` lists the
  values from `params` at every application.
- An endpoint's effective members may come from templates in other files. The
  outline shows authored structure, and inlay hints or the effective view show
  what was contributed, tagged with its contributor, as `render` already does.

### 3.5 Scopes and name visibility

A name resolves in the anchor captured by `ParseCtx` (`docs/04` § 4). This
anchor belongs to the construct's authored file, not to the site where the
construct is applied. `ReferenceResolver` looks names up but cannot list them
(G5).

**Consequence:** completion lists names from the resolver of the scope that
holds the cursor. That is the fragment's resolver, except inside a template
body, where it is the template's anchor. It never builds its own scope
model.

### 3.6 Annotation targets are already a vocabulary

`DomainLocation` (`domains.py`) names every place an annotation can be
applied, and decoders push it with `Raml.target_scope` (`docs/09` § B4).

**Consequence:** the construct that holds the cursor should be expressed in
the same vocabulary. Completing an annotation name then reduces to "annotation
types whose `allowedTargets` include this `DomainLocation`". No second
classification of constructs is needed.

### 3.7 Values are checked against shapes

Examples, defaults, annotation values and custom facet values are `DataNode`
trees with positions, which P10 walks together with an unwrapped shape
(`docs/10`).

**Consequence:** keys inside those values (for completion, hover and
definition) are found by the same descent: path in the value to the
property of the shape. The descent should be shared with P10, not written
again in the server (G10).

### 3.8 Diagnostics already have LSP's structure

`RamlError.chains()` gives independent failures. Each `Trace` carries a
location, a position with its end, a stable message key and `info`
(`docs/11` § 1, § 6). Where recovery is possible is defined per construct
(`docs/11` § 2). Lint `Finding`s carry a rule ID, a severity and a position.

**Consequences:**

- Each chain becomes one diagnostic at its innermost frame with a known
  position, with the outer frames as `relatedInformation`. `code` is the
  message key and `data` is `info`.
- A diagnostic is published at its own URI. In addition, one `Information`
  diagnostic, "N problems in `lib.raml`", goes at the `uses:` or `!include`
  site that pulled the file in, found through the reverse of `include_refs`.
- The recovery boundaries in `docs/11` § 2 also decide how much of a document
  keeps working while it has errors. G1 extends them to the pass level.

### 3.9 Source retention is partial

`retain_source` keeps each fragment's root `Node`, its text, and
`source_info` (ID to key and value nodes), but `source_info` covers **shapes
only** (G9). Ranges for traits, resource types, security schemes, endpoints,
operations, responses and bodies come only from their `key_pos` and
`value_pos`, which carry the full span; M3.3 closed G9 on that.

### 3.10 Cost and concurrency

| Workload | unwrap + validate | + lint |
|---|---|---|
| shared fixture | 6–9 ms | – |
| `large` (7,000 types, 150 libraries) | 431 ms | 824 ms |
| `endpoints` (500 resources) | 389 ms | 790 ms |

Source: `bench/baselines.json`, Windows, libyaml.

A `Raml` is single-threaded while it is built and safe to read afterwards
(`docs/13` § 3). `Node` trees never change after they are built (`docs/03`
§ 1).

**Consequences:**

- A full reparse on each debounced change is affordable. Lint runs as a
  second tier that the next edit cancels.
- One worker thread parses, and a finished snapshot is published whole.
- Because `Node` trees never change, a compose cache shared across parses is
  possible, keyed by URI and text digest (G8). Adding it requires a bench
  workload first (`docs/12` § 5).

## 4. Parser gaps

Each gap is parser work under the owning document's rules, not server code.

| # | Gap | Owner | Unblocks |
|---|---|---|---|
| G1 | **A trustworthy partial model** (`research/partial-models.md`). Keep what was built when a sibling fails. Record the completed pass and the entities that are broken. Collapse inherited duplicate diagnostics. Stopping at the failing pass is unchanged | `docs/11` § 2, `docs/13` § 1, `docs/02` § 4 | every feature while the document has errors |
| G2 | **Authored URI per occurrence.** `TypeExprRef` (and any new occurrence record) gets the file its name was written in, fixed at decode time. Whole-value template substitution needs a decision (§ 7, O5) | `docs/06` § 3, `docs/08` § 4 | definition, references, rename |
| G3 | **Columns in quoted scalars.** Rebase using the scalar's style: +1 for a single-line quoted flow scalar | `docs/11` § 3 | every occurrence and diagnostic column |
| G4 | **Grammar tables.** For each construct (a `DomainLocation`): the keys it takes, each key's value kind (scalar, name of a kind, type expression, map of X, sequence of X), and the open-key rules (`/…`, HTTP methods, three-digit status codes, media types, `(…)`, `<<…>>`). The decoders' `unknown field` branches read the same tables | `docs/04`, `docs/05`, `docs/08`, `docs/09` | key completion, value completion, key hover, cursor classification |
| G5 | **Name enumeration.** `ReferenceResolver` lists the names visible in it, for each kind, together with the `alias.` names from its `uses:` | `docs/04` § 2 | name completion |
| G6 | **Sandboxed listing.** `loaders.py` lists a directory URI under the `SafeFileLoader` root, because OS paths appear only there | `docs/03` § 5 | path completion |
| G7 | **Unapplied templates.** Either scan template bodies' `type:` values in the template's anchor scope as a P4 by-product, or document the limitation | `docs/08` § 2 | references, diagnostics in template libraries |
| G8 | **Cross-parse compose cache** (optional, measure first) | `docs/03` § 4.3, `docs/12` | latency on large workspaces |
| G9 | **Source info beyond shapes**, for trait, resource type and security scheme definitions, endpoints, operations, responses, bodies and documentation items | `docs/02` § 3, `docs/13` § 2 | symbol and folding ranges, hover ranges |
| G10 | **A shared value descent.** "The shape at this path inside a value", used by P10 and by the server | `docs/10` | completion, hover and definition inside values |

## 5. Architecture

```
┌──────────────── adapters (protocol only) ─────────────────┐
│  LSP (pygls)        MCP (mcp SDK)        CLI additions     │
└──────────────┬───────────────┬──────────────┬─────────────┘
               ▼               ▼              ▼
┌──────────────── service: fastraml/service/ ───────────────┐
│ Workspace: buffers, overlay loader, roots, dependents,     │
│   debounce, versioned snapshots                            │
│ Queries by position: at · definition · references · rename│
│   hover · complete · signature · hierarchy · symbols       │
│ Queries by name: find(name) → entity → the same queries    │
└──────────────┬────────────────────────────────────────────┘
               ▼ imports views and the model, like cli.py
┌──────────────── views (pure functions of a Raml) ─────────┐
│ occurrences (new) · cursor (new) · render · walk · graph   │
│ lint · severity                                            │
└──────────────┬────────────────────────────────────────────┘
               ▼
┌──────────────── model: parser/, types/ ───────────────────┐
│ passes P0–P10 + G1 · grammar tables (G4) · resolver        │
│ enumeration (G5) · retained source (G9) · value descent    │
└────────────────────────────────────────────────────────────┘
```

The service imports several views. One view may import another only through
`walk`, `graph` and `severity`, so the service is a *composition root*, in the
same position as `cli.py`. `docs/02` § 2 and `tests/unit/test_views.py` would
gain one allowed importer.

### 5.1 How the features connect

Four derived structures and the model serve every feature. Each structure is
listed with its source:

- **The model.** Entities, resolved links and positions.
- **Occurrences.** Where a name is written, and the ID of what it names.
- **Cursor context.** The key path to the cursor in the buffer's `Node` tree,
  and the construct (`DomainLocation`) plus facet it is in.
- **Graph.** Effective relations (`views/graph.py`).
- **Walk.** Containment and addresses (`views/walk.py`).

| Feature | Model | Occurrences | Cursor | Graph / walk |
|---|---|---|---|---|
| definition | target's `key_pos` | hit test → target | – | – |
| references, highlight, code lens | – | per-target list | – | – |
| rename | – | per-target list + definition | – | – |
| semantic tokens | – | per-URI list | – | – |
| hover (entity) | `render`, description | hit test | – | – |
| hover (key) | grammar documentation | – | construct + key | – |
| hover (`<<param>>`) | `DirectiveRef.params` of applications | – | template + variable | – |
| type hierarchy | `inherits` (declared) | hit test | – | `into`, `TYPE_EDGES` (subtypes) |
| inlay hints, effective view | unwrapped entity, contributor | – | – | `walk` addresses |
| completion | resolver enumeration, shapes for values | – | construct + facet | – |
| signature help | `declared_variables` | hit test on directive | – | – |
| document symbols | `key_pos`, source info | definitions in the file | – | `walk` nesting |
| workspace symbols | – | definitions | – | `Graph.entries()` |
| links, folding, selection | `include_refs`, `uses`, `Node` spans | – | – | – |
| diagnostics | `RamlError`, lint `Finding` | – | – | – |
| MCP name queries | – | per-target list | – | `Graph.find` |

### 5.2 The occurrence index

```python
@dataclass(slots=True, frozen=True)
class Occurrence:
    uri: str            # authored file (G2)
    span: Position      # the name token only: `User` in `lib.User`
    role: Role          # DEFINITION | REFERENCE | ALIAS_PREFIX | BUILTIN | LINK
    target: int | None  # entity ID; None for BUILTIN
    kind: Kind          # type | annotation_type | trait | resource_type |
                        # security_scheme | library | property | facet |
                        # parameter | file
    written: str        # the text at `span`
```

It is built after P10 from the flat registries:

- **Definitions:** entries of `fragment_types` and `fragment_annotations`,
  traits, resource types, security schemes, `uses:` aliases, properties,
  `facets:` entries, and template variables.
- **References:** `type_expr_refs` of every shape in `Raml.shapes`, `is:` and
  `type:` `DirectiveRef`s, `securedBy:` `SecurityScheme`s, the name in each
  `DomainExtension` key, custom facet use keys, and keys inside values (G10).
- **Links:** `include_refs`, `LibraryLink.value_pos`, and `extends`.

It keeps two orientations of one list: per URI, sorted by span, for a hit
test by `bisect`; and per target, for references. Duplicate spans from
templates collapse on `(uri, span, target)`.

It is a view. Without the service, `fastraml refs --sites FILE NAME` can print
`file:line:col` for each place a name is written. That serves users in its
own right and tests the index without any protocol.

### 5.3 Cursor context

1. **Path.** Compose *only* the current buffer, with no full parse, and
   descend `Node` spans to the key path. If the buffer does not compose,
   retry once with a placeholder at the cursor (`x:` in key position, `x` in
   value position).
2. **Construct.** Walk the path through the grammar tables (G4). The result
   is a `DomainLocation`, the facet the cursor is in, and whether the cursor
   is on the key or the value side.
3. **Scope.** The fragment's resolver, or the template's anchor inside a
   template body (§ 3.5).

The last good snapshot supplies *names* (types, traits, and so on) while the
current buffer is broken. It never supplies *positions*.

### 5.4 Workspace and snapshots

- **Loader.** `ParseOptions(file_loader=...)` gets an overlay: open buffers
  first, then `SafeFileLoader(root)`. `workspace_root` is the workspace folder
  that contains the root; with nested folders, the longest prefix wins.
  Remote includes stay off unless configured.
- **Options.** `unwrap=True, validate=True, retain_source=True`. Lint runs as a
  second tier.
- **Snapshot.** `(buffer versions, Raml, error, occurrences, findings, line
  tables)`, immutable once published. Results older than the current buffers
  are dropped. Before answering a request for a buffer with a pending change,
  the service parses it immediately rather than waiting for the debounce.
- **Roots and dependents** (§ 7, O2). A snapshot records what it read:
  `Raml.fragments` keys, `include_refs` and JSON Schema loads. A change to any
  of those files reschedules every root that read it.

### 5.5 Rename

1. Collect the edits from the occurrence index. Each edit replaces the name
   part only; a `lib.` prefix is its own occurrence.
2. Apply them to in-memory copies of the buffers.
3. Parse the result in the background.
4. Require that every renamed occurrence still resolves to the entity at the
   same position.

The parser checks the rename, so no rename-specific resolution rule is
written. `prepareRename` refuses where a use cannot be edited as text, such as
a partial substitution like `<<item>>Response`.

## 6. MCP

`contrib/fastmcp-raml` serves *an API described in RAML* as MCP tools, for
calling that API. This server is for *reading and authoring RAML*, so it
needs a distinct name, such as `fastraml mcp`.

It uses the same `Workspace`, addressed by name through `Graph.find`, which
prefers declarations and keeps real ambiguity:

| Tool | Backed by |
|---|---|
| `validate`, `lint` | snapshot diagnostics and findings, as records |
| `outline`, `list`, `find` | document symbols, `Graph.entries`, `Graph.find` |
| `show` | `render` (the effective view) |
| `definition`, `references` | occurrence index |
| `uses`, `deps`, `hierarchy` | `Graph.walk`, `Graph.into` |
| `openapi`, `json_schema`, `compat` | existing views |

Resources: the effective tree (`fastraml tree`) per root.

Over the CLI and `fastraml skills`, MCP adds three things: a warm parse
between calls, awareness of unsaved buffers when an editor hosts it, and
answers with positions. It follows the LSP on the same service.

## 7. Decisions

Each option below was taken as recommended; the plan records them as D3–D7.

**O1 Where the server lives.**

- (a) `fastraml/service/` in the package, with `fastraml lsp` and
  `fastraml mcp` verbs behind the extras `fastraml[lsp]` (pygls) and
  `fastraml[mcp]`. Each extra is imported inside its verb.
- (b) Projects under `contrib/`. The service and the occurrence index would
  have to become stable public API first, and cursor classification in a
  consumer risks holding a rule.
- (c) Views in the package, with the workspace and adapters in `contrib/`.

*Recommendation: (a).* The service moves in lock-step with the grammar tables
and the passes.

**O2 Roots.**

- (a) Discover roots from their headers: API, Overlay and Extension documents
  are roots. Parse each root, and serve each file from the roots that reach
  it. A file no root reaches is parsed on its own.
- (b) A root pinned by the user.
- (c) Each file parsed on its own.

*Recommendation: (a), with a `raml.roots` glob setting to override it.* A
library's diagnostics depend on how it is used, which only its roots show.

**O3 Where the syntax for completion comes from.**

- (a) Grammar tables owned by the parser and read by its decoders (G4).
- (b) Tables in the views, checked against the parser by a test: every listed
  key decodes without `unknown field`, and one unlisted key does not.

*Recommendation: (a).* (b) is acceptable only as a first step.

**O4 Recovery.**

- (a) Stop at the failing pass, as today, and make the partial model
  trustworthy (G1).
- (b) (a), plus continuation gated by containment
  (`research/partial-models.md` § 7).
- (c) Serve the last snapshot that parsed without error.

*Recommendation: (a) now, and (b) only if the mutation corpus shows it adds no
duplicated diagnostic.* (c) is a fallback for text that does not compose,
not a policy.

**O5 Whole-value template substitution (G2).**

- (a) In retained-source mode, P4 records the caller's parameter node for each
  whole-value substitution, and the occurrence is placed there.
- (b) A whole-value substitution copies the caller's node, so both the
  diagnostic and the occurrence point at `item: Usr`. This needs amendments to
  `docs/08` § 5 and `docs/11` § 3 and changes positions the TCK can observe.
- (c) Drop such occurrences.

*Recommendation: (b) if the TCK allows it, else (a).* A partial substitution
stays at the template position under every option.

**O6 LSP library.** pygls (with lsprotocol), or a hand-written JSON-RPC loop.
*Recommendation: pygls.*

**O7 MCP library.** The official `mcp` SDK, or `fastmcp`.
*Recommendation: the official SDK.* Only tools and resources are needed.

## 8. Which gap blocks what

Not every gap blocks the start. The occurrence law (§ 9) lets the index drop
and count any occurrence whose span text is wrong. So G2 and G3 improve
coverage rather than gate correctness. Which gap blocks what:

| Gap | Blocks |
|---|---|
| G1 | a useful server of any kind (M4) |
| G2, G3 | complete references and rename; without them, affected occurrences are dropped, not wrong |
| G9 | accurate ranges for non-type symbols; `key_pos` is enough at first |
| G4, G5, G6, G10 | completion (M5) |
| G7, G8 | nothing; each is an improvement |

The ordered milestones (M1–M7), with the decisions each one needs, are in
`research/language-service-plan.md`.

## 9. Verification

- **Occurrence law:** for every occurrence, `source_texts[uri]` sliced at
  `span` equals `written`. For a reference, `written` is the name its target
  is declared under, or that name qualified by an alias. Run it over the TCK
  and the fixtures. It catches G2 and G3 defects without a client.
- **Round trip:** the definition of each reference is a `DEFINITION`
  occurrence, and the references of that definition include the original
  reference.
- **Recovery:** for each pass at which the parse can stop, a test asserts
  which features still answer. The mutation corpus
  (`research/partial-models.md` § 8) drives every feature and must not raise.
- **Grammar:** each table key decodes, one unlisted key reports
  `unknown field`, and completion offers exactly the table.
- **Protocol:** pygls' test client drives each LSP feature on the fixtures,
  asserting on message keys and `info`, never on message text.

## 10. Open questions

- Should a type written only in an unapplied template be a reference (G7)?
- JSON Schema `$ref` navigation inside `.json` includes is out of scope at
  first.
- With several extension documents over one master (`docs/15` § 2), root
  discovery treats each Overlay as a root and its master as reached.
- Pull diagnostics (LSP 3.17) or push. Push matches the snapshot model; pull
  fits clients that ask per file.
