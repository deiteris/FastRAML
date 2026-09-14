---
name: raml
description: Write and read RAML 1.0 itself - the language, not the fastraml CLI. Covers the document header and root nodes, the type system, the type RAML infers when you declare none, optional and nilable properties, type expressions and multiple inheritance, resources and URI parameters, methods, bodies and responses, traits and resource types with their parameters and merge order, the six security scheme types, annotations, and !include, typed fragments and libraries. Use when writing a .raml file from scratch, adding a type, endpoint, trait or security scheme to one, turning an OpenAPI document or a set of models into RAML, reviewing RAML someone else wrote, or working out why a parser rejects a construct that looks correct. Triggers include "write a RAML spec for this", "add an endpoint to this RAML", "how do I express X in RAML", "is this RAML right". To run the fastraml CLI over a document that already exists, read the core guide instead.
license: MIT
allowed-tools: Bash(fastraml:*) Read
---

# RAML 1.0, the language

RAML 1.0 is a YAML 1.2 dialect for describing an HTTP API. This guide is the
language: what the keys mean, and which constructs a parser rejects.

To ask questions about a document that already exists — what it declares, what
one type resolves to, what a change breaks — read the CLI guide instead:

```bash
fastraml skills get core
```

Two reference files hold the tables this guide summarises. Load them when you
need an exact facet or an exact key, not before:

```bash
fastraml skills get raml --full   # this guide plus both reference files
```

- `references/facets.md` — every facet each built-in type accepts, and its
  default.
- `references/nodes.md` — every node the root, a resource, a method, a
  response, a security scheme and an annotation type accept, plus the fragment
  identifiers, the reserved template parameters and their functions, and the
  annotation target names.

## The document

The first line is a YAML comment and is **required**. A parser that does not
find it stops there.

```yaml
#%RAML 1.0
title: Book Library API
version: v1
baseUri: https://api.example.com/{version}
mediaType: application/json
```

`title` is the only required root node. Nodes may appear in any order, but a
processor preserves the order you wrote them in, so declaration order reaches
the reader and any generated output.

No order is required. A conventional one, if you have no reason to differ:
identity (`title`, `version`, `baseUri`, `protocols`, `mediaType`), then
declarations (`uses`, `types`, `traits`, `resourceTypes`, `securitySchemes`,
`annotationTypes`), then `securedBy`, then the resources.

Set `mediaType` at the root whenever the API speaks one format. Every `body`
node may then drop its media-type key, which removes a level of nesting from
every operation in the document.

## Types

Declare types under the root `types` node, keyed by name:

```yaml
types:
  Person:
    type: object
    properties:
      firstname: string
      lastname:  string
      age?:      number
  Phone:
    type: string
    pattern: ^[0-9-]+$
  Manager:
    type: Person
    properties:
      reports: Person[]
      phone:   Phone
```

A type references another type, extends one by adding facets, or is a type
expression. `type:` names what it extends; leave it out and RAML infers one.

### The type RAML infers when you declare none

Three rules, in this order:

1. A facet that belongs to only one type infers that type. `properties:` means
   `object`.
2. Otherwise, with no `type:` and no `schema:`, the type is `string`.
3. A `body` node carrying none of `properties:`, `type:` or `schema:` is `any`.

```yaml
types:
  Person:
    properties:
      name:                 # string, by rule 2
  Tag:
    enum: [ red, green ]    # string, by rule 2
  Blob:
    properties:             # object, by rule 1
```

Leaving a property blank is idiomatic RAML for "a required string", not an
oversight.

### Optional, required and nilable

Three different things, spelled close together:

```yaml
properties:
  name:                  # required, string
  age?:       number     # optional
  comment:    nil        # required, and must be null
  note:       string?    # required, but may be null
  extra?:     string?    # optional, and may be null when present
```

`T?` expands to `nil | T`. It works on a scalar type and on a reference to a
user-defined type, and **not** inside a type expression — write `nil | Person`
there, not `Person?`.

**A trailing `?` in a property name is the optional marker only while
`required:` is absent.** State `required:` and the `?` becomes part of the
name:

```yaml
properties:
  preference?:
    required: true     # a property literally named 'preference?'
  preference??:        # 'preference?', optional
```

Prefer `age?: number` over `required: false`. It is shorter and it is what
most RAML in the wild uses.

### Type expressions

A type expression may appear anywhere a type is expected:

| Expression | Means |
| --- | --- |
| `Person` | that type |
| `Person[]` | an array of `Person` |
| `string[][]` | an array of arrays of strings |
| `string \| Person` | either one |
| `(Person \| Notebook)[]` | an array whose members are either |
| `Person[] \| Notebook[]` | an array of one **or** an array of the other |

Naming a complex expression makes an alias, which may carry a description and
annotations of its own: `Devices: (Phone | Notebook)[]`.

**`!include` cannot appear in a type expression**, and neither can a template
parameter. Both are values; a type expression is parsed as text.

### Inheritance

`type:` takes one type, or a sequence for multiple inheritance:

```yaml
types:
  Teacher: [ Person, Employee ]
  Number1: { type: number, minimum: 4 }
  Number2: { type: number, maximum: 2 }
  Number3: [ Number1, Number2 ]   # invalid: no value satisfies both
```

The subtype keeps every restriction from every parent, so multiple inheritance
narrows, and an impossible narrowing is an error rather than last-writer-wins.
Two parents that each declare `pattern`, or that declare the same user-defined
facet, are likewise an error. Inheriting from two primitives —
`[ number, string ]` — is invalid.

A subtype may narrow a property it inherits and may not widen one: a required
property cannot become optional, and a property's type can only move to a
subtype of what the parent declared.

### Objects

**`additionalProperties` defaults to `true`**, and an object with no
`properties:` facet at all is unconstrained.

A property name wrapped in slashes is a pattern property, constraining every
additional key that matches:

```yaml
properties:
  name: string
  /^note\d+$/: string    # note1, note17 must be strings
  //:          string    # every other additional key must be a string
```

An explicitly declared property beats a pattern that also matches it. Among
patterns, the first written wins. `additionalProperties: false` and a pattern
property in the same declaration is invalid — the first forbids what the
second describes.

`discriminator` names the property carrying the concrete type's identity, and
`discriminatorValue` defaults to the subtype's own name:

```yaml
types:
  Person:
    discriminator: kind
    properties:
      kind: string
  Employee:
    type: Person
    discriminatorValue: employee   # without this, the value is 'Employee'
```

`discriminator` must name a scalar property the type declares. It is illegal
in an inline type declaration and on a union.

### Arrays, unions and scalars

`type: Email[]` and `type: array` with `items: Email` declare the same thing.
Use the first when there is nothing to add, the second when you want facets on
the member type inline. Arrays take `minItems`, `maxItems` and `uniqueItems`.

A union joins types with `|`. An instance is valid if it satisfies **at least
one** member; a processor matches the members left to right and deserialises
against the first that fits, so order them most specific first. A union may
carry a facet only if **every** member accepts it — `number | integer` accepts
`minimum`, and `number | integer | string` does not.

The scalars are `string`, `number`, `integer`, `boolean`, `date-only`,
`time-only`, `datetime-only`, `datetime`, `file` and `nil`. The four date types
are distinct and not interchangeable: `datetime` takes `format: rfc3339` (the
default) or `format: rfc2616`, and the other three take no `format` at all.
Per-type facets are in `references/facets.md`.

**`pattern:` is a search, not a full match.** Write the anchors yourself —
`pattern: ^\w{16}$`, not `pattern: \w{16}`, which matches any string that
*contains* sixteen word characters.

### User-defined facets

`facets:` declares a restriction that **subtypes** must supply. It does not
restrict the declaring type:

```yaml
types:
  CustomDate:
    type: date-only
    facets:
      noHolidays: boolean          # required of every subtype
      onlyFutureDates?: boolean    # optional
  PossibleMeetingDate:
    type: CustomDate
    noHolidays: true               # supplied here, in the subtype
```

**Do not give a value for a facet in the declaration that declares it.**
`CustomDate` may not write `noHolidays: true` beside its own `facets:` block;
that reads as an unknown facet, because the chain satisfying a `facets:` block
starts one level below it.

A facet name may not begin with `(`, and may not collide with a built-in facet
or with one an ancestor already declared.

### Examples

`example` is one, `examples` is a named map of many, and **the two are
mutually exclusive** on the same declaration:

```yaml
types:
  User:
    properties:
      name: string
    examples:
      minimal:
        name: Bob
      annotated:
        strict: false            # skip validation of this one
        value:
          name: Doe Enterprise
```

The `value:` wrapper turns an example into a map that can also carry
`displayName`, `description`, `strict` and annotations. **Use the wrapper
whenever the instance itself has a property named `value`**, or the parser
reads your data as the wrapper.

### External JSON Schema

```yaml
types:
  Person: !include person.json
```

A type defined by an external schema is a leaf. It **cannot** take part in
inheritance or in any type expression, so `Person[]`, `type: Person`, and a
property typed `Person` are all invalid once `Person` came from an include.
Wrapping it to add a `description`, `displayName`, examples or annotations is
allowed, and nothing else is. Reach an inner element with a fragment:
`!include person.json#/definitions/Address`.

## Resources and methods

Every key beginning with `/` is a resource. Nest them to match the URL:

```yaml
/users:
  /{userId}:
    uriParameters:
      userId:
        type: integer
    get:
      queryParameters:
        fields?: string
      responses:
        200:
          body:
            type: User
        404:
```

A URI parameter you do not declare is a **required string**. Declare one only
to give it a type, a description or a constraint. `version` and `ext` are
reserved: `version` takes the root `version` node's value, and `ext` lets a
client ask for a media type through the URL.

Two resources must not resolve to the same absolute URI. The comparison
ignores parameter names, so `/users: /foo:` collides with `/users/foo:`, while
`/users/{userId}:` and `/users/me:` do not.

The methods are `get`, `patch`, `put`, `post`, `delete`, `head` and `options`.
`queryParameters` and `queryString` are mutually exclusive: use the first
unless the parameters constrain each other, and the second with a type when
they do — `queryString: { type: [ paging, lat-long | loc ] }` admits lat and
long, or location, and never both.

**An array type on a header or a query parameter means the caller may send it
more than once**, with the member type applying to each instance. Any other
type means exactly one.

A `body` is a map keyed by media type — `application/json:` then the type — or,
when the root sets `mediaType`, the type declaration alone. Response keys are
status codes read as strings, so `200` and `'200'` are one key and may not both
appear.

## Traits and resource types

A trait adds nodes to a method; a resource type adds nodes to a resource.
Declare them at the root, apply them with `is:` and `type:`:

```yaml
traits:
  paged:
    queryParameters:
      start?: number
  secured:
    headers:
      <<tokenName>>:
        description: A valid <<tokenName>> is required
resourceTypes:
  collection:
    usage: Any collection of items
    get:
      description: List all <<resourcePathName>>
    post:
      description: Create one <<resourcePathName | !singularize>>
/books:
  type: collection
  is: [ paged ]                              # every method here
  get:
    is: [ secured: { tokenName: access_token } ]
```

`<<name>>` is a template parameter, substituted on application.
`resourcePath`, `resourcePathName` and `methodName` come from the processor;
every other parameter you pass yourself. Append a function with a pipe, as in
`<<resourcePathName | !singularize>>`; `references/nodes.md` lists all ten.

**A template parameter cannot appear in an `!include` path or a `uses` value.**
Those resolve before substitution happens.

### What wins when they merge

- **An explicit node beats an inherited one.** A `description` written on the
  method keeps its value and the trait's is discarded.
- **Collections merge by value rather than replacing.** A trait's
  `enum: [win, mac]` beside a method's `enum: [mac, unix]` yields
  `[mac, unix, win]`. A trait therefore cannot narrow an enum.
- **Nearer wins.** The stack runs outward: the method's own traits, the
  resource's, the traits on the matching method of the first resource type,
  that resource type's own, and so on. The same trait applied twice with
  different parameters is not a duplicate, and the nearer application's
  parameters survive.

A method name suffixed with `?` in a resource type is **conditional**: it
applies where the resource already declares that method and does nothing where
it does not.

```yaml
resourceTypes:
  corpResource:
    post?:
      headers:
        X-Chargeback:
          required: true
/servers:
  type: corpResource
  get:
  post:              # gets X-Chargeback
/queues:
  type: corpResource
  get:               # gets nothing; post is never created
```

A resource type may not contain nested resources, and applying one never
reaches a resource's existing children. `usage:` documents the template and is
inherited by nothing.

## Security schemes

Six types, and the strings are exact and case-sensitive: `OAuth 1.0`,
`OAuth 2.0`, `Basic Authentication`, `Digest Authentication`, `Pass Through`,
and `x-<anything>` for a scheme RAML does not model.

```yaml
securitySchemes:
  oauth_2_0:
    type: OAuth 2.0
    describedBy:
      headers:
        Authorization: string
      responses:
        401:
          description: Bad or expired token
    settings:
      authorizationUri: https://example.com/oauth2/authorize
      accessTokenUri:   https://example.com/oauth2/token
      authorizationGrants: [ authorization_code, implicit ]
securedBy: [ oauth_2_0 ]
```

Only the two OAuth types take `settings`, and `references/nodes.md` gives the
required keys for each. `Pass Through` puts every credential it needs in
`describedBy`; the remaining three take neither.

Write `describedBy` even for a standard scheme. It states the headers, query
parameters and responses the scheme adds to every operation it protects, and
it is what makes that requirement visible in generated output.

`securedBy` on a method overrides the resource's, which overrides the root's.
**`null` in the list means "may also be called unsecured"** — a member of the
array, not a way of clearing it:

```yaml
securedBy: [ null, oauth_2_0 ]                          # optional auth
securedBy: [ oauth_2_0: { scopes: [ ADMINISTRATOR ] } ] # narrowed scopes
```

## Annotations

An annotation is metadata a processor may act on or ignore. Declare the type,
then apply it in parentheses:

```yaml
annotationTypes:
  deprecated: nil
  clearanceLevel:
    allowedTargets: [ Resource, Method ]
    properties:
      level:
        enum: [ low, medium, high ]
/users:
  (clearanceLevel):
    level: high
  get:
    (deprecated):
```

An annotation type with neither `type:` nor `properties:` is a `string`.
`allowedTargets` restricts where the annotation may be applied; omit it and
every target is allowed. `references/nodes.md` lists the target names.

**To annotate a scalar node, rewrite it as a map with a `value` key:**

```yaml
baseUri:
  value: https://api.example.com
  (redirectable): true
```

This works on a fixed list of about thirty nodes — `description`, `type`,
`example`, `pattern`, `baseUri` and the rest — given in `references/nodes.md`.

Annotations on a type are **not** inherited by its subtypes. Annotations on a
trait or resource type **are** applied to whatever inherits it, unless the
inheritor applies an annotation of the same type, which replaces every
inherited application of that type.

## Includes, fragments and libraries

`!include` is a YAML tag and takes the place of a node's value:

```yaml
types:
  Person: !include types/person.raml
traits: !include patterns/traits.raml
documentation:
  - title: Legal
    content: !include docs/legal.md
```

A path with a leading `/` resolves against the **root** RAML file. Any other
path resolves against the **file that wrote it**, not the root. The argument
must be a literal: no template parameters.

A `.raml`, `.yml` or `.yaml` file is parsed and spliced in as structure.
Anything else arrives as a string, which is what makes `content: !include
legal.md` work. YAML anchors do not cross a file boundary in either direction.

A fragment declares its kind on the first line and then holds only that
construct's body:

```yaml
#%RAML 1.0 ResourceType
usage: Use this for a collection
get:
  description: Retrieve all items
```

The identifiers are `DataType`, `NamedExample`, `ResourceType`, `Trait`,
`SecurityScheme`, `AnnotationTypeDeclaration`, `DocumentationItem`, `Library`,
`Overlay` and `Extension`. A fragment may carry `uses:` and nothing else beyond
its own construct.

A `#%RAML 1.0 Library` fragment gathers types, traits, resource types, security
schemes and annotation types under one namespace. Bind it with `uses:` and
reach its declarations through a dot:

```yaml
uses:
  files: libraries/files.raml
types:
  Upload:
    properties:
      target: files.File
```

The namespace is local to the file that wrote the `uses:` node, and **it does
not chain**. If `files` itself uses `file-type`, then `files.file-type.File` is
invalid here; import the inner library under its own name.

## Five ways to write RAML that is wrong

Each is stated in full above. Check for them before you call a document done —
the first three are accepted and read differently from how you meant them.

1. An unanchored `pattern`, where you meant `^...$`.
2. A trailing `?` on a property name beside an explicit `required:`.
3. An `enum` in a trait, written to narrow the method's.
4. An external JSON Schema type used in a type expression.
5. A `facets:` entry given a value in the declaration that declares it.

## What fastraml does not parse

These are valid RAML 1.0 and `fastraml` rejects them. Do not author them into a
document this toolchain has to read:

- **Overlays and Extensions** — a file headed `#%RAML 1.0 Overlay` or
  `#%RAML 1.0 Extension`, and the root `extends` node they require. Put the
  content in the document itself, or in a library it uses.
- **XML Schema external types** — `type: !include schema.xsd`. JSON Schema
  through `!include` works.

`schemas:` and `schema:` are the deprecated RAML 0.8 aliases for `types:` and
`type:`. They parse, and each is mutually exclusive with the name it aliases.
Write `types:` and `type:`.

## Check what you wrote

```bash
fastraml validate -w . api.raml
```

Silence and exit 0 means valid. Set `-w` to a directory containing every file
the document reaches, or an `!include` pointing at a parent folder fails. The
`core` guide covers the rest of the CLI.
