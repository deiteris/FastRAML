# Language service: target architecture

**Status: done, 2026-09-28; F2 and failure containment dropped (§ 12); reviewed against the code (§ 11, step 11).**
Proposed on 2026-09-27. This document records how the language
service should sit on the parser and its model, from a review of the service as
built against `docs/21`, `archive/language-server.md` (LS) and
`research/language-service-plan.md`. It is not normative: `docs/21` describes
the service as it is, and each change below amends its owning document in the
same commit. The order of work is § 11.

## 1. Findings

The service is sound where it matters most. It is a composition root
(`docs/02` § 2); every answer is read from a parse or a view; no query
resolves a name, and the occurrence index checks each name it keeps against
the text at its span (`docs/16` § 9). What follows is where it departs from
that shape.

**Queries infer what the parser knew.** Five answers are guesses from spans:

- the outline lists a member under an entity only when it lies in the same
  file and inside the entity's span, which stands for "this entity wrote it";
- `render`'s contributor note (`views/render.py`, `Sources`) names the
  template whose declaration span holds a member's line;
- `items` a type expression built (`Book[]`) is told apart by sharing its
  array's `key_pos`;
- the bodies one `body:` without a media type became are grouped by an equal
  `key_pos`;
- `links()` pairs each include with the path text written on its lines,
  because an include of a file that is not a fragment has no target id.

Two more read the wrong thing: the outline detects an inline JSON or XML
schema by its first character, where the model has `JsonShape`; hover reads
an entity's facets with `getattr` on an untyped value.

**Knowledge is repeated.** The five declaration tables (types, annotation
types, traits, resource types, security schemes) are listed in the occurrence
index, `walk.py`, `render.py`'s `Sources` and four times in
`service/queries.py`. The model has no iterator over them. The containment of
the model (a resource's methods, a method's request and responses, a
response's bodies) is walked separately by the outline, `render`, the tree,
the walk and the graph. Span arithmetic (holds, contains, covers) is written in
`service/queries.py` and again in `views/occurrences.py`.

**Work is repeated.**

- `Workspace.snapshots(uri)` brings every root current to learn which ones
  read `uri`. After an edit to a library, a request that needs one snapshot,
  the outline, reparses every root reading the library first.
- Definition, highlight and hover ask every snapshot serving the file, and
  each builds its occurrence index on first use (46 ms on `large`), to
  deduplicate answers that are the same in every root for a name a library
  declares.
- A buffer is encoded to bytes for the loader and decoded again by each parse,
  and each snapshot keeps its own copy of every text it read: a library read
  by N roots is held N times.

**Positions carry every feature, and nothing checks most of them.** One review
found an example's key placed at its value, a body without a media type with
no key at all, a documentation item whose key was its whole mapping, alias
copies that gave a block an end before its start, and security scheme
definitions whose positions are spelled `None` where every other entity's
are `UNKNOWN`. The occurrence law checks name tokens only.

**One failure costs the editor most of the model.** With `fixtures/sample`
opened alone, two includes outside the workspace root leave the model with no
endpoints and no unwrap: no resources in the outline, no rendered hover, no
lint. The pass that failed decoded everything else. This is the continuation
PM § 7 defers.

**The documents disagree with the code.** `docs/21` § 5 says a request answers
from every snapshot serving the file; the outline, links and the tree use one.
LS § 5.1 derives the outline from occurrences and `walk` nesting, and
workspace symbols and subtypes from the graph; the code reads model tables.
`docs/21` § 4 states the span test of the outline as a rule. The plan's status
predates the outline, and `docs/21` § 5's latency predates this round's
changes.

## 2. Principles

1. **Facts, not inference.** A query reads what the model records. Where a
   query would need to infer from positions, names or text what a pass knew,
   the pass records it, and the gap is listed here until it does.
2. **One definition per concern.** The declaration tables, who wrote an
   entity, span arithmetic and the snapshot a query reads each have one home,
   shared by the service and the views.
3. **Positions obey a law.** Every positioned entity satisfies § 7's
   placement law, tested over the TCK and the fixtures, so no consumer
   re-checks a position.
4. **The least parsing that answers.** A request brings current only the
   snapshots it reads, and reads the fewest that answer it (§ 5).
5. **Text is read for text features only.** Folding, selection and, later,
   cursor context (LS § 5.3) read the buffer's composed tree, because they must
   answer while the parse is stale or broken. Nothing else reads YAML; a node
   the model keeps as its record of what was written, such as `type_expr`, is
   read as that record.

## 3. Layers

| Layer | Holds | Change |
|---|---|---|
| Passes (`parser/`, `types/`) | the rules, and the facts of § 6 | record F3 to F5 |
| Model (`registry.py`, fragments, entities) | entities, positions, `broken`, the declaration iterator F1 | add F1 |
| Views (`views/`) | occurrences (names and links), the authorship view (§ 4), walk (effective addresses), render, tree | add the authorship view; F6 |
| Service (`service/`) | workspace, snapshot policy (§ 5), queries, outline | queries read substrates only |
| Adapter (`service/lsp.py`) | protocol shapes and position encoding | unchanged |

The outline moves to `service/outline.py`. Queries hold no traversal of the
model of their own: they ask the occurrence index about names, the authorship
view about structure, and `render` about text. The authorship view is a view,
not a substrate (`docs/16` § 1): the service reads it, and another view does
not, so `render`'s `Sources` keeps its own span test (§ 12).

## 4. The authorship view

`views/authored.py` answers one question for every consumer: what an entity,
or a file, wrote. It reads facts only:

- a fragment's declarations, through F1;
- a type's own members: its properties whose declaration ids are not among its
  parents' (unwrap keeps a property's declaration id, which
  `views/occurrences.py` already relies on), its
  written `items` (F4), its `facets:` entries;
- a resource's own URI parameters, methods and resources, and a method's own
  parameters, bodies and responses: those no template contributed, told by
  span, which is exact (§ 12);
- the bodies of one `body:` without a media type, as one entry (F3, recorded
  as `Body.media_type_written`).

The outline reads it now; completion, rename and semantic tokens read it
later (M5, M6). It holds the one span test the service applies, `wrote` and
the member and owner tests built on it, so no reader repeats one. It also
says what a fragment file that is one declaration wrote (a DataType's
shape, a SecurityScheme's definition, a DocumentationItem's item), and which
metadata, base URI parameters and `uses:` a file wrote.

## 5. Snapshot policy

`Workspace.serving(uri)` yields the snapshots serving a file lazily: the file's
own root first when it is one, then the other roots that read it, then a parse
of the file alone when none does. A root is brought current only when the
iteration reaches it. Which roots read a file is known only from a current
snapshot, so the workspace keeps the read set of a snapshot it drops and tries
the roots that read the file last time first: after an edit, the first root
tried is one that reads it. `readers()` still brings every root current, for
publishing (`docs/21` § 5).

| Query | Reads |
|---|---|
| outline, links, the tree | the first snapshot |
| definition, hover, highlight, type hierarchy | the first snapshot holding an occurrence at the cursor |
| references, subtypes | every snapshot, deduplicated |
| diagnostics | every root reading the file, merged (unchanged) |
| workspace symbols | every root (unchanged) |

A file read by roots that bind it differently (a master API an Overlay merges
into, a template applied with different arguments) answers from the first,
and says so in `docs/21` § 5. References span roots because a use in any root
is a use.

## 6. Facts the model records

| # | Fact | Owner | Removes | Read by |
|---|---|---|---|---|
| F1 | `declarations()` on a declaring fragment: `(key, name, entity)` over the five tables, in declaration order; `every_declaration(raml)` over every fragment | `docs/04` | seven table lists, and a class test in each reader | occurrences, walk, render, authorship, service |
| F2 | not recorded: which template contributed a member is told by span, which is exact (§ 12) | — | — | — |
| F3 | on `Body`, whether its media type was written | `docs/08` § 6.3 | grouping by `key_pos` | authorship |
| F4 | on an array shape, whether it wrote its `items` (`items_written`): not a type expression's, nor a parent's | `docs/06` § 3 | the `key_pos` comparison | authorship |
| F5 | a security scheme definition's, settings' and description's positions spelled `UNKNOWN`, never `None`, as every other entity's | `docs/09` | the `None` checks | every consumer |
| F6 | on each path occurrence, the URI the path resolved to | `docs/16` § 9 | `links()`'s line and text pairing; the fragment scan for a path's definition | links, definition, hover |

The schema kind needs no new fact: `JsonShape` says it. A section's key
(`types:`, a method's `headers:`) is not recorded: a section spans its
entries, as `docs/21` § 4 says, and one position per section is not worth its
allocation on every parse.

## 7. The placement law

For every entity the model positions, over the TCK and the fixtures:

1. `key_pos` is one line, and the text there is the entity's name as written
   (a documentation item's title, a body's media type or `body`);
2. `value_pos` ends after it starts, and the span from key through value holds
   both;
3. a child written in the same file lies inside its parent's span;
4. an entity's `location` is the file its key is written in.

`tests/unit/test_placement_law.py` checks it, as `test_occurrence_law.py`
checks names; a failure is a parser defect, fixed in its pass. The service
then converts positions without guarding them.

As built, it checks every entity the walk reports: declarations, shapes,
properties, parameters, resources, methods, responses and bodies, 5424 over
the 721 TCK documents that reach unwrap. Rule 4 is read through rule 1, whose
text is taken from the entity's location. Rule 3 is checked for resources
only, which no template contributes: under a method, what a template wrote
lies in the template, outside the method's span. A request has the method's
key; a URI parameter P6 synthesizes was never written; a shape with no name,
or one standing for a type it names (`Book[]`'s items, a recursive
reference, F4), is placed at the key it is written under; each is exempt.
Documentation items, examples and `uses:` entries, which the walk does not
report, are not checked yet.

It found three defects, fixed first: a container a merge or an Overlay
rebuilt ended where its grafted content did, before its own start or in
another file (`docs/03` § 1); what a library's resource type contributed was
located in the applying file (`docs/08` § 4.2); and a key built with no
source read as known (`docs/11` § 1).

## 8. Failure containment

**Dropped (§ 12):** parsing keeps stopping at the failing pass. What follows
is the proposal as it was.

A failed include is contained: the entity that includes the file is kept and
marked (`docs/13` § 1), and nothing else depends on the file's content except
through that entity. It is the first class PM § 7 would admit: decoding
completes with the marked entity, and later passes skip it and every reference
to it. The gate is PM § 7's: over the invalid TCK and the fixtures, continuing
adds no chain whose cause is already reported, and the 41-to-1 test holds.
The case to measure it on is `fixtures/sample` with its own directory as the
root, where two such includes cost the endpoints, unwrap and lint.

## 9. Stable identity

Ids do not survive a reparse (LS § 3.2), so a type hierarchy item is found
again by where its name is written. The walk's addresses (`docs/16` § 2) are
stable across parses. They could identify an item, and link the editor to the
preview: reveal the entity at the cursor, or go from a node in the viewer to
its source, which the tree already places. That waits for a feature that
needs it.

## 10. What stays

- Folding and selection compose the buffer (principle 5).
- A section's span is derived from its entries.
- Publishing parses every root (`readers()`), as decided for now.
- The byte round trip through the loader: the loader interface is bytes, and
  a file is decoded at most once per parse (`docs/02` § 4). Each snapshot's
  copy of the texts stays until a compose cache (G8) shares them.

## 11. Order

Each step is its own commit, amends its owning document, and passes the gate.

1. Done: `docs/21` § 5 states the snapshot rule as built; § 4 marks the
   outline's span test as a stand-in for F2. The plan's status and M4 notes
   catch up; the latency is measured again (475 ms, unchanged).
2. Done: snapshot policy (§ 5): `serving(uri)`, and each query reading as the
   table says. Tests pin how many roots a request brings current.
3. Done: F1, and every table list reading it.
4. Done: span helpers on `Position`, replacing the local ones. Only
   `service/queries.py` held any; `views/occurrences.py` bisects starts and
   compares text, which is not span arithmetic.
5. Done: the placement law (§ 7), with each defect it finds fixed first.
6. Done: F5.
7. Done: F6, and `links()`, path definitions and path hovers reading it.
8. The authorship view (§ 4) with F3 and F4; the outline and `render` read it;
   the outline moves to `service/outline.py`; hover dispatches on the entity's
   class. Done: F3, F4, hover's dispatch, the view (`docs/16` § 10) and the
   outline over it, with an Extension outlined by location and a JSON schema
   named by its `JsonShape`. The view holds the one span test, also for the
   properties and `items` a template merged into a declaration and for an
   inherited property recursion marking gave a shape of its own. `render`'s
   `Sources` keeps its own, which is exact for the same reason (§ 12), once
   it tests columns (step 11).
   Checking the outline's ranges over the TCK found a block ending in a flow
   collection ending before its bracket (`docs/03` § 1).
9. Dropped: F2 (§ 12).
10. Dropped: failure containment (§ 8, § 12).
11. Done, from a review of the built service against this document:
    `every_declaration`, so no reader tests a fragment's class; `render`'s
    `Sources` tests columns, not lines, which named the wrong trait for two
    declared on one line; the outline's remaining choices of what an entity
    wrote (responses, bodies, applied refs, facets, metadata) move into the
    authorship view, which fixes an alias listing its referent's facets; a
    fragment file that is one declaration outlines its body; the service's
    symbol kinds derive from the occurrence kinds.

## 12. Decisions

Taken on 2026-09-28.

- **A file outlines what it wrote.** An Overlay adds or changes only what
  `docs/19` § 4.2 allows (documentation, annotations, annotation types and new
  type declarations), never a resource or method; an Extension may add
  anything the merge allows. Either way, what an extension document adds is
  located in that document, so it is outlined there, and the master's outline
  lists the master's own declarations, whichever snapshot serves it. The
  authorship view (§ 4) selects by `location` over the merged model, not by
  fragment: a type an Extension declares sits in the master fragment's
  `types` with the Extension's location, and a method it adds to a master
  resource is listed in the Extension's outline under that resource's path.
  Today's outline reads the file's own fragment and misses both.
- **LS is archived** (`archive/language-server.md`); `docs/21` and this
  document replace it.
- **No compose cache for now.** G8 stays additive work, taken up only when a
  measured latency asks for it; nothing in § 11 depends on it.
- **F2 is not recorded.** Which template contributed a member is told by span,
  and that is exact, not inferred: a trait or resource type is declared in its
  table or its own fragment, never inside a resource, a method or a type, so
  what it contributed lies outside the parent's span even in the parent's
  file. The placement law (§ 7) makes spans hold what they were written with.
  Recording F2 would put a field on every operation, response, body and
  parameter, and through P4's merges and stage 2, for no answer the spans do
  not give. `render`'s `Sources` names a contributing template from spans for
  the same reason.
- **No failure containment.** Parsing keeps stopping at the failing pass
  (PM § 7), and the lenient model is made trustworthy instead; § 8 is not
  taken up.
