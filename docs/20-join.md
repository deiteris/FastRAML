# 20 - Join

This document owns `fastraml join`: combining several RAML API documents into
one. It covers which inputs are accepted, how their declarations and endpoints
are combined, how conflicts are reported, how root defaults and the base URI are
reconciled, and how the result is written.

Status: design. Nothing here is implemented yet.

## 1. Principles

- **Only API documents are joined.** Every input starts with `#%RAML 1.0`.
  Overlays, Extensions, Libraries and other fragments are rejected.
- **Nothing is merged into an existing node.** Every definition an input
  declares is added to the result next to the other inputs' definitions. Two
  entries with the same name are either identical, and kept once, or a conflict.
- **Nothing is renamed.** Because no name changes, no reference in any input is
  rewritten.
- **Every conflict is reported.** The join collects all conflicts, reports them
  together, and writes no output if there are any.
- **No input changes meaning.** Where combining would change what an input's own
  content means, the join rewrites that content so its meaning is kept (§ 5,
  § 6). If it cannot, it reports an error.

The join is not an extension merge ([19](19-overlays-and-extensions.md) § 3). It
reuses no merge rule from there or from the template merge
([08](08-templates-and-endpoints.md) § 1).

## 2. Inputs

The first input is the **primary input**. Where this document says an output
follows input order, the primary input comes first, then the others in the
order given.

Each input is parsed on its own with `ParseOptions(retain_source=True)`. An
input that fails to parse stops the join, and its diagnostics are reported as
the parser produced them, so they point into that input's files. An input
with a header other than `#%RAML 1.0` reports `unexpected fragment kind` with
`expected: API` in `info`.

The join reads each input's composed source trees, `Raml.source_nodes`, and
consults the input's parsed model where § 5 and § 6.4 say so. It composes no
file itself, so each file is still composed once per input parse (invariant I2).

## 3. What is combined

### 3.1 Root properties

| Property | Rule |
|---|---|
| `title`, `version`, `description` | The `--title`, `--version` or `--description` option if given. Otherwise the value all inputs share. If the inputs differ and no option is given: `join root value differs`. An empty `--description` omits the key. |
| `baseUri`, `baseUriParameters` | § 6 |
| `protocols`, `mediaType`, `securedBy` | § 5 |
| `documentation` | Items are added by `title` (§ 4). |
| `uses` | Aliases are added (§ 3.2). |
| `types` and `schemas`, `annotationTypes`, `traits`, `resourceTypes`, `securitySchemes` | Names are added (§ 3.2). |
| Annotation applications `(name)` | Added by annotation name (§ 4). |
| Resources `/…` | § 3.3 |

### 3.2 Declarations and libraries

Each name map is combined by name. `types` and `schemas` are one namespace, as
they are for the parser: a `User` under `schemas` in one input and under `types`
in another is the same entry. The output uses `types`.

A `uses` alias is an entry whose value is the library's resolved URI
([03](03-yaml-and-io.md) § 4.1). The same alias naming the same library is
identical. The same alias naming a different library reports
`library namespace conflict`, with `library` and both URIs in `info`, the same
key as in [19](19-overlays-and-extensions.md) § 5.2.

A name map written as `types: !include types.raml` is read through the include:
its entries are combined like inline ones and written inline. Each entry's own
includes are then rewritten relative to the output (§ 7.2).

Output order: each map lists the primary input's entries in declaration order,
then each later input's new entries in its declaration order (invariant I8).

### 3.3 Endpoints and operations

An endpoint is identified by its **full path**: the keys from the root down to
it, joined together, after § 6 has placed the input's resources. `/users/{id}`
written flat and `/{id}` nested under `/users` are the same endpoint.

- An **operation** is identified by its endpoint and method. Operations are
  entries (§ 4).
- An **endpoint's own properties** are every key except its methods and nested
  resources: `displayName`, `description`, `type`, `is`, `securedBy`,
  `uriParameters` and annotations. When two inputs have the same endpoint, its
  own properties, taken together, are one entry (§ 4). An endpoint created by
  § 6.3 has no own properties, so it never conflicts on them.

Output structure: an endpoint that is already in the output receives the later
input's operations and nested resources. A new endpoint is written under the
output node of its parent endpoint in the same input, using the key that input
wrote. Its nesting as authored is therefore kept.

## 4. Identical entries and conflicts

Two entries with the same identity are **identical** when their source trees
compare equal under `node_value_equal` (`parser/structural_merge.py`), extended
by two rules:

1. **Includes compare their targets.** An `!include` scalar is resolved against
   the file that wrote it. Two includes that resolve to the same URI are equal
   without further reading. Otherwise, or when only one side is an include,
   the included content is compared with the same rules: composed nodes by
   structure, text includes by text.
2. **`uses` values compare their resolved URIs**, as in § 3.2.

The comparison is strict. Key order matters. Spellings that the parser treats as
equivalent, such as `name: string` and `name: {type: string}`, are different.
Positions are ignored.

Comparing source text is enough, even though an entry may refer to other
names. Every name an entry refers to is itself an entry, compared by the same
rule. If `User` is identical in two inputs and refers to `Address`, either
`Address` is identical too, or it is a conflict and no output is written. A
library-qualified name is covered by the alias check. A template parameter's
value is part of the operation or endpoint that applies the template, which is
compared on its own. When the join succeeds, every shared name therefore means
the same thing in every input.

A non-identical pair reports `join conflict` at the later input's entry, with
this `info`:

| Key | Value |
|---|---|
| `kind` | `type`, `annotationType`, `trait`, `resourceType`, `securityScheme`, `documentation`, `annotation`, `endpoint` or `operation` |
| `name` | The name, documentation title, full path, or `METHOD path` |
| `other` | Location of the earlier input's entry |
| `at` | Key path, from the entry, to the first differing node |

Finding `at` needs a form of `node_value_equal` that returns the first differing
pair instead of a boolean.

## 5. Root defaults

`securedBy`, `protocols` and `mediaType` at the API root are defaults: they apply
wherever a method, resource or body does not set its own
([09](09-security-and-annotations.md) § A4). Putting two inputs under one root
would otherwise change what one input's methods accept.

### 5.1 Placement

For each of the three properties separately:

- If every input has an identical value, or none has one, the output root
  keeps that value.
- Otherwise the output root omits the property. Each input that had a root
  value gets it written onto its own content, on the targets in § 5.2. An
  input with no root value needs nothing written: its methods had no default
  and still have none.

The root value is never the union of the inputs' values. A union would apply
one input's schemes to another input's methods. An input with no `securedBy`
would then be secured by another input's schemes.

### 5.2 Targets

| Property | Written onto |
|---|---|
| `securedBy` | Each method node authored in the input whose method and resource both omit `securedBy`. |
| `protocols` | Each method node authored in the input that omits `protocols`. |
| `mediaType` | Each request and response `body` authored in the input that has no media-type keys ([08](08-templates-and-endpoints.md) § 6.3). It becomes a map with one key per media type, each holding the original body. |

### 5.3 Defaults reaching templates

A default also reaches content that templates contribute. The join does not
write into a template. Writing onto a method that a template also sets changes
the method's effective value, because the template merge combines the two
([08](08-templates-and-endpoints.md) § 1). A `securedBy` list becomes the union
of both lists, and a body written with media-type keys no longer matches a
template body written without them. The join reports
`join default reaches template` instead, with `property`, `template` and
`reason` in `info`, when an input that needs § 5.2 applies a trait or resource
type (directly, or through another template) that:

- sets the property anywhere in its definition (`reason: sets`);
- for `mediaType`, has a `body` with no media-type keys (`reason: body`); or
- for `securedBy` and `protocols`, contributes a method the resource does not
  write itself, so there is no authored node to write onto (`reason: method`).
  For `securedBy`, a resource that sets its own `securedBy` is exempt.

The check reads template definitions from the input's parsed model, including
those declared in libraries.

## 6. Base URI

### 6.1 Each input's base URI

An input's base URI is, in order of precedence:

1. `--base-uri INPUT=URI`, or `baseUri` for that input in the `join` config
   section (§ 8);
2. the input's own `baseUri`.

An override replaces `baseUri`. A config override may also give
`baseUriParameters`, which replace the input's. A CLI-only override keeps the
input's declarations of the variables that remain in the new URI and drops the
others, since every declared name must occur in `baseUri`
([08](08-templates-and-endpoints.md) § 6.2).

If no input has a base URI, the output has none and § 6.3 does nothing. If only
some have one: `join missing base uri`, naming each input without one.

`{version}` is replaced by the input's own `version` before anything is
compared. The output keeps `{version}` only where every input's version equals
the output's version; elsewhere it writes the substituted text.

### 6.2 The common base URI

Each base URI is split into its scheme and authority, and its path segments.

- The scheme and authority must be identical in every input:
  otherwise `join base uri conflict`, with each input's value in `info`. A
  template variable there is compared as text, and its `baseUriParameters`
  declaration is an entry (§ 4).
- The **common path** is the longest run of leading path segments that all
  inputs share. Segments compare as text, so `/v1/user` and `/v1/users` share
  only `/v1`. A variable in the common path is handled like one in the
  authority.

The output `baseUri` is the shared scheme and authority plus the common path.

### 6.3 Created endpoints

The segments after the common path are the input's **remainder**. An input
with an empty remainder places its resources at the root. Otherwise its root
resources go under endpoints created from its remainder:

- Remainders form a tree by segment. Inputs whose remainders share leading
  segments share the created endpoints for them.
- A chain of created endpoints, each with one child and no resources of its
  own, is written as one key: `/orders/v2`, not `/orders` then `/v2`.
- A variable in a remainder segment moves: its `baseUriParameters` declaration
  becomes a `uriParameters` declaration on the created endpoint whose key holds
  it. An undeclared variable needs nothing.

A created endpoint then takes part in § 3.3 like any other. If it has the same
full path as an endpoint another input wrote, the created one contributes no
own properties, and the two inputs' operations and resources are combined
under it.

### 6.4 `<<resourcePath>>`

`resourcePath` is an endpoint's path relative to the base URI
([08](08-templates-and-endpoints.md) § 3.1). Under a created endpoint, every
moved endpoint's `resourcePath` gains the remainder as a prefix, so a template
reading it produces different text. An input with a non-empty remainder that
applies a template reading `resourcePath` reports `join resource path changes`,
with `input` and `template` in `info`. `resourcePathName` is unaffected.

## 7. Output

### 7.1 Writing RAML

The output is written from the combined node tree with a `#%RAML 1.0` header.
The writer:

- keeps key order and `!include` tags;
- writes each scalar so that `compose` reads it back with the same tag and
  value under YAML 1.2 rules ([03](03-yaml-and-io.md) § 2.1), quoting where it
  must;
- writes multi-line strings as literal blocks.

Comments and the original scalar styles are not kept.

Root keys follow this order: `title`, `description`, `version`, `baseUri`,
`baseUriParameters`, `protocols`, `mediaType`, `securedBy`, `documentation`,
`uses`, `types`, `annotationTypes`, `traits`, `resourceTypes`,
`securitySchemes`, annotation applications, then resources.

### 7.2 Paths

Every `!include` argument and `uses` value is resolved against the file that
wrote it ([03](03-yaml-and-io.md) § 4.1), then written relative to the output's
directory. With no `-o`, the current directory is used. An HTTP(S) URL is kept
as is. A path that cannot be written relative to the output directory, such as
one on another Windows drive, reports `join path not relative`, with `path` and
`output` in `info`.

### 7.3 Checking the result

The joined text is parsed again before it is written, with the workspace root
set to the deepest directory that contains the output and every file it
refers to. A diagnostic from that parse is a defect in the join. It is reported
wrapped in a `join output` frame, and nothing is written.

## 8. Command line and configuration

```text
fastraml join INPUT INPUT... [-o OUTPUT] [--title TEXT] [--version TEXT]
    [--description TEXT] [--base-uri INPUT=URI]...
```

It accepts the common configuration and workspace options
([13](13-public-api.md) § 5). It exits 1 when any error is reported, and then
writes nothing.

The configuration file gains a `join` section, checked against
`fastraml/config.raml`:

```yaml
join:
  title: Example API
  version: v2
  description: Orders and users.
  inputs:
    inputs/orders.raml:
      baseUri: https://api.example.com/{tenant}/orders
      baseUriParameters:
        tenant: {type: string, pattern: '^[a-z]+$'}
```

An `inputs` key is resolved relative to the configuration file. A CLI option
takes precedence over the same setting in the configuration file. The
configuration checks only that `baseUriParameters` is a mapping. Its values are
checked as RAML when the output is parsed again (§ 7.3), so an error in them is
reported against the output.

## 9. Placement

The join runs on source trees before decoding, so it is neither a parser pass
nor a view ([16](16-graph.md) § 1). It lives in `fastraml/join/`:

- It may import the model, the parser and `yamlnode`. It does not import
  `fastraml.views`.
- Nothing imports `fastraml.join` except `cli.py`. A test in
  `tests/unit/test_views.py` enforces this, next to the view-layer checks.

Tests: `tests/unit/test_join.py`, with one test per rule in §§ 3 to 7 that names
the rule it protects. The benchmark suite gains a join workload
([12](12-performance.md) § 4).

## 10. Not covered

- Overlays, Extensions and Libraries as inputs.
- Renaming entries to resolve a conflict. It would need every reference to
  a renamed entry to be found and rewritten.
- Writing a root default into a template (§ 5.3).
- Writing a template's `<<resourcePath>>` so that it keeps its value (§ 6.4).
