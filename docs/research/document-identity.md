# Document identity

**Status: open. This document settles nothing.** It records a problem, what was
measured about it, which routes were tried and why they failed, and where the
remaining choices lie. The numbered documents in `docs/` are normative; this one
is not, and nothing in the code depends on it.

## 1. The problem

**A document's identity is its retrieval path.** Two byte-identical copies of a
library at two paths are two different documents, declaring two different types
with two different addresses, and nothing in the parse notices.

That makes two things impossible:

- **Deduplication.** A library vendored into two places, or reached by two
  routes in a monorepo, cannot be recognised as one thing.
- **Collision detection.** Two genuinely different libraries that a consumer
  might conflate — same name, same shape, different meaning — look exactly like
  the case above.

The question arrived from a narrower one: how a viewer should interpret a
particular annotation, `(deprecated)`, differently from the rest (§ 6). Every
mechanism for that reduces to *recognising a shared vocabulary across
documents*, which reduces to this.

## 2. What was measured

Two byte-identical libraries, included by one document under two prefixes:

```raml
# /tmp/dup/a/hints.raml  and  /tmp/dup/b/hints.raml — identical
#%RAML 1.0 Library
types:
  Badge:
    type: string
    facets:
      tone: string
```
```raml
#%RAML 1.0
title: Dup
uses:
  one: !include a/hints.raml
  two: !include b/hints.raml
annotationTypes:
  x: { type: one.Badge, tone: warn }
  y: { type: two.Badge, tone: warn }
```
```
validate=0
files: ['a/hints.raml', 'b/hints.raml']
  a/hints.raml/Badge -> fastraml://id/a%2Fhints.raml#/declarations/types/Badge
  b/hints.raml/Badge -> fastraml://id/b%2Fhints.raml#/declarations/types/Badge
  annotation x inherits {'$ref': '…a%2Fhints.raml…/Badge'}
  annotation y inherits {'$ref': '…b%2Fhints.raml…/Badge'}
```

The parse is valid. `x` and `y` are typed by what a reader would call the same
type, and the model holds two, related by nothing.

## 3. Why RAML offers no help

A Library's node table is the declaration sections plus `usage?` — there is no
identity key, and no equivalent of JSON Schema's `$id`. Nor is there an
inference rule: the spec never says what makes two documents the same document.

`uses:` is not identity either, and the spec is unusually explicit
(`raml-10.md:3370`):

> Namespaces defined in a **uses** statement in a specific file are only
> consumable within that file and serve only to disambiguate the included
> libraries from each other. Therefore, any processor MUST NOT allow any
> composition of namespaces using "." across multiple libraries.

A prefix is a local alias, scoped to one file, explicitly non-composable. It
says how to write a name here; it says nothing about what the name denotes
anywhere else.

**The comparison that frames this:** JSON Schema can do both — infer an
identifier from the retrieval URI, or take a declared `$id` that overrides it.
RAML does neither. Anything fastRAML offers is therefore an addition, and the two
halves are separable: inference is available to a parser, declaration is not
without a key to put it in.

## 4. Where identity is bound today

| | | |
|---|---|---|
| `registry.py:304` | `self.fragments[uri] = fragment` | the retrieval URI *is* the identity |
| `views/walk.py:234` | `relative_to(location, self.root)` | an address is workspace-relative |
| `views/tree.py` | `_relative(raml, uri)` | so are the `types` / `annotation_types` / `security_schemes` keys |
| `views/walk.py` | `workspace_of(raml)` | the root everything is relative to |

The model holds absolute URIs throughout — that is an invariant (`docs/02` § 4:
every `location` is a `file://` or `http(s)://` URI). The *views* make them
relative, deliberately, for readability (`docs/16` § 3). Neither layer holds
anything path-independent.

## 5. What inference could be

Content is the only path-independent thing the parser has, so an inferred
identity is a digest. Two things about the level it is taken at:

- **Not raw bytes.** CRLF, trailing whitespace and indentation would make two
  identical documents differ. The composed node tree, canonicalised, is the
  level that means *the same document modulo formatting*.
- **It must fold in the dependencies.** A library's meaning includes what its
  own `uses:` resolves to, so two libraries with identical text importing
  different files are not the same document. That makes it a Merkle digest over
  the include graph, not a digest of one file.

### 5.1 Identity for dedup is not identity for addressing

`docs/16` § 3 pins an address as structural and stable across re-parses. A
content-derived address churns on every edit — which is exactly why positions
were split into their own projection (`docs/16` § 11.5) rather than folded into
the tree. So these stay two things: the address stays path-based and readable,
and a digest is a new datum beside it, one per file.

### 5.2 Catch, don't merge

Reporting that two fragments share a digest invents nothing; it is an
observation about the input.

*Merging* them is a different act. It changes `inherits` targets, changes what
P9's `cannot inherit from different type` compares, and changes addresses.
Nowhere does RAML say that two identical documents are one document, so merging
would be fastRAML inventing language semantics — which the project's own rule
forbids (`CLAUDE.md`: a rule that belongs to the language belongs in a pass, and
`docs/` is normative). If dedup is ever wanted it belongs behind a
`ParseOptions` flag, with `docs/01` recording it as a deliberate deviation.

### 5.3 What inference cannot reach

A digest catches *identical* duplicates. It cannot say that `hints v1` and
`hints v1.1` are the same vocabulary at two revisions — they hash differently,
correctly, and a consumer wanting "the same thing, newer" gets nothing.

That is precisely what `$id` buys and inference cannot, and RAML has no key to
put it in. **The declared half stays missing**, and the only way to smuggle it in
is an annotation the author writes by hand, which § 6.3 rejects for a different
reason that applies here too.

## 6. The routes tried, and why they failed

Recorded so the reasoning is not repeated. Each was verified against the parser
before being rejected; none required a parser change to try.

### 6.1 A presentation vocabulary written into the API description

An annotation on the annotation type, carrying `tone` / `placement` / `label`:

```raml
annotationTypes:
  badge:
    allowedTargets: [AnnotationType]
    type: object
    properties: { tone: string, label?: string }
  deprecated:
    type: string
    (badge): { tone: warn, label: Deprecated }
```

Legal, decoded, and present in the tree — `AnnotationType` is a valid
`allowedTargets` value (`domains.py`), and `annotation_types.deprecated.annotations`
carries the application with its value.

**Rejected: it puts presentation vocabulary into the API description.** A code
generator or a gateway reading the same file carries a field that means nothing
to it, and the document becomes coupled to one renderer's words — `tone: warn`
is a CSS class with a RAML syntax. RAML describes an API; how a viewer paints it
is not part of the API.

### 6.2 The same, namespaced

`uses: viewer: !include viewer.raml` giving `(viewer.badge)`. The spec sanctions
ignoring it (`raml-10.md:2897`):

> Processors **MAY ignore any and all annotations.**

and the corpus already namespaces this way — `(lib.important)`,
`(decls.important)`, `(alsoLib.bindingDefinition)`.

**Partly survives, with a condition.** The argument holds only while the hint is
*optional*. If a document renders badly without hints, "optional" is a fiction
and every document has to carry the viewer's vocabulary. That makes the ordering
matter more than the mechanism: see § 7.

### 6.3 Identity as an inherited facet value

A vocabulary library whose types carry a URI in a facet, inherited by whatever
declares against them:

```raml
#%RAML 1.0 Library                    # anywhere, called anything
types:
  TextHint:
    type: string
    facets: { vocabulary: string }
  Badge:
    type: TextHint
    vocabulary: https://viewer.example/hints/v1#Badge
    facets:
      tone: string
      placement?: string
```
```raml
uses:
  whateverTheyCalledIt: !include hints.raml
annotationTypes:
  deprecated:
    type: whateverTheyCalledIt.Badge
    tone: warn
    placement: headline
```
```
validate=0
deprecated  value-type=string  {"tone":"warn","placement":"headline",
                                "vocabulary":"https://viewer.example/hints/v1#Badge"}
rateLimit   value-type=object  {"unit":"requests per minute",
                                "vocabulary":"https://viewer.example/hints/v1#Measured"}
```

This works, and it works well. Properties worth keeping in mind if it is ever
revisited:

- The identity is declared **once**, on the vocabulary type, and rides down the
  inheritance chain into the subtype's `custom_facets`. A consumer gets identity
  and payload in one map, one hop from the applied annotation's `type` address —
  no `inherits` walk.
- Prefix, filename and type name are all free. Only the string matters.
- It is **enforced**. A missing required facet is `required custom facet is
  missing: facet: tone`; a typo is `unknown facet: facet: placment`. An
  annotation carrying the hint could not be *required* at all, which is the
  decisive advantage of facets over § 6.1.
- `facets:` beats annotations here for a second reason: it does not stack the
  annotation mechanism on itself.

**One mechanical wart.** `type: [Vocabulary, string]` fails —
`cannot inherit from different type: source: string: target: any` — so an
identity-carrying base must be declared per value kind (`TextHint: type: string`,
`RecordHint: type: object`, …). The alternative is putting the identity on an
annotation applied to `Badge`, costing the consumer one extra hop
(`inherits[0].$ref` → `index.shape` → `Badge.annotations`), which always
resolves because `Badge` is a declaration.

**Rejected as the primary mechanism: the author writes the identity by hand.**
It is a convention every document has to know and maintain, which is the burden
this whole line of work exists to remove. Kept on record because it is the only
thing verified to give a *declared* identity in RAML today, and § 5.3 says
inference can never supply that.

### 6.4 Exposing the absolute source URI

The projection discards what the model holds (§ 4), so a `sources` map from
relative path to absolute URI would let a consumer key on where a file actually
came from:

```json
"sources": { "hints.raml": "https://viewer.example/hints/v1.raml" }
```

**Rejected as an identity mechanism, worth doing anyway.** Two costs pull
opposite ways:

- A `file://` URI is machine state, not data. `_SKIP` already drops `validator`
  for exactly this reason — its `repr` carries an absolute path and leaked the
  machine into the view. The golden layer's stability rests on `fastraml://id`
  plus relative paths, so absolute local paths would churn every golden across
  machines and across CI.
- Restricting it to non-`file:` schemes fixes that but makes identity conditional
  on *publishing*: the vocabulary must be included by URL, which means
  `fastraml[http]`, network at parse time, and the serialisation recorded as
  `docs/15` After-v1 item 7. Vendoring a local copy puts you straight back to a
  declared identity, because the file genuinely is a different file.

Separately from identity, a consumer that cannot tell where a declaration's file
came from is missing something the model holds. Every comparable omission has
been closed — the scheme projected through its link, the example carrying its
metadata, the facet carrying its type (`docs/16` § 11.4, § 11.4b). This is the
same omission one level up, and it is additive.

## 7. What bounds how much of this is needed

The originating question was how a viewer renders one annotation specially. Most
of it needs no identity at all.

**The structural default.** An annotation's value can be rendered *against its
own annotation type*, which the tree already carries in full — `rateLimit` has
`properties: { perMinute: integer }`, a `description`, a `displayName`. So
`{"perMinute": 60}` becomes the same attribute rows every other typed thing gets,
with no contract, no namespace, no configuration, for every annotation anyone
ever writes including ones the consumer has never heard of. A scalar annotation
is already structurally equivalent to what is drawn, which is why `deprecated`
looks right today and `rateLimit` does not.

That is the honest general answer to *how is a consumer supposed to interpret an
annotation*: **it is not. It displays the value against its declared type**,
which is the only thing a general consumer can do without inventing a reading.

**What is left over is genuine interpretation**, and it is per-consumer:
`(deprecated)` belonging beside a heading and struck through in a navigation
pane is a *reading* of what the annotation means, not a rendering of its value.
No structural default reaches it. That is the only part needing a hook, and a
hook living in the consumer needs no cross-document identity — the consumer
knows its own document.

Cross-document identity becomes load-bearing only when the interpretation is
meant to **travel**: one registry serving many documents, or a hint written once
and honoured by another instance. That is the case § 1 is about, and it is worth
knowing that it is the narrower case rather than the general one.

## 8. Open questions

1. Is dedup wanted at all, or only detection (§ 5.2)? Detection is cheap and
   invents nothing; dedup is a semantic deviation needing a flag and a
   `docs/01` entry.
2. If a digest is added: over the composed node tree alone, or Merkle over the
   include graph (§ 5)? The second is correct and the first is cheaper; nothing
   yet measures how often the difference shows.
3. Does `sources` (§ 6.4) go in regardless of identity, restricted to non-`file:`
   schemes, or with the machine path normalised for the goldens?
4. Is a *declared* identity (§ 6.3) acceptable for the narrow published-vocabulary
   case, given inference can never supply versioned identity (§ 5.3)?
