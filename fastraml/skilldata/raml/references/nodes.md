# Node reference

The exact spelling of every key, by the construct that accepts it. A `?` marks
an optional node.

## Root

| Node | Value |
| --- | --- |
| `title` | string. The only required root node. |
| `description?` | string, Markdown |
| `version?` | string, such as `v1` |
| `baseUri?` | a URI, or a template URI |
| `baseUriParameters?` | a properties declaration, for the template variables in `baseUri` |
| `protocols?` | a non-empty array of `HTTP` and `HTTPS`, case-insensitive |
| `mediaType?` | one media-type string, or an array of them |
| `documentation?` | an array of maps, each with exactly `title` and `content` |
| `types?` | a map of type name to type declaration |
| `schemas?` | deprecated alias for `types`, mutually exclusive with it |
| `traits?` | a map of trait name to trait declaration |
| `resourceTypes?` | a map of resource type name to declaration |
| `annotationTypes?` | a map of annotation type name to declaration |
| `securitySchemes?` | a map of scheme name to declaration |
| `securedBy?` | an array of scheme names |
| `uses?` | a map of namespace to library location |
| `(annotationName)?` | an annotation application |
| `/<relativeUri>?` | a resource |

`version` is available as `{version}` in `baseUri`. Trailing slashes on
`baseUri` are dropped before the resource paths are appended; slashes elsewhere
in it are not.

## Resource

| Node | Value |
| --- | --- |
| `displayName?` | string |
| `description?` | string, Markdown |
| `get?` `patch?` `put?` `post?` `delete?` `head?` `options?` | a method |
| `is?` | an array of trait names, or of single-key maps passing parameters |
| `type?` | a resource type name, or a single-key map passing parameters |
| `securedBy?` | an array of scheme names |
| `uriParameters?` | a properties declaration |
| `(annotationName)?` | an annotation application |
| `/<relativeUri>?` | a nested resource |

A URI parameter that is not declared is a **required string**. A URI parameter
value may not contain `/`. Two resources may not resolve to the same absolute
URI; the comparison leaves parameters unexpanded, so `/users/{id}` and
`/users/me` do not collide but `/users: /foo:` and `/users/foo:` do.

Reserved URI parameter names:

| Name | Value |
| --- | --- |
| `version` | the root `version` node |
| `ext` | the media type the client wants, through the URL rather than `Accept` |

## Method

| Node | Value |
| --- | --- |
| `displayName?` | string |
| `description?` | string, Markdown |
| `headers?` | a properties declaration |
| `queryParameters?` | a properties declaration; mutually exclusive with `queryString` |
| `queryString?` | a type name or declaration; mutually exclusive with `queryParameters` |
| `body?` | a body declaration |
| `responses?` | a map of status code to response |
| `protocols?` | an array of `HTTP` and `HTTPS` |
| `is?` | an array of trait names |
| `securedBy?` | an array of scheme names |
| `(annotationName)?` | an annotation application |

An **array type** on a header or a query parameter permits more than one
instance, with the member type applying to each. Any other type permits exactly
one.

A `queryString` type must expand to a scalar or an object at every level of its
hierarchy. An object's properties become the query parameters.

## Body

A map keyed by media type, or — when the root declares `mediaType` — a type
declaration on its own.

```yaml
body:
  type: User
body:
  application/json:
    type: User
  text/xml:
    type: !include user.xsd
```

## Response

| Node | Value |
| --- | --- |
| `description?` | string, Markdown |
| `headers?` | a properties declaration |
| `body?` | a body declaration |
| `(annotationName)?` | an annotation application |

Status-code keys are read as strings, so `200` and `'200'` are one key and may
not both appear.

## Trait and resource type

Everything a method and a resource accept respectively, plus:

| Node | Value |
| --- | --- |
| `usage?` | string. Documents the template; never inherited. |

A resource type may not contain nested resources, and applying one never
reaches a resource's existing children. A method name suffixed with `?` in a
resource type applies only where the resource already declares that method.

### Reserved template parameters

| Parameter | Value |
| --- | --- |
| `resourcePath` | the resource's full URI relative to `baseUri` |
| `resourcePathName` | the rightmost path fragment that holds no URI parameter |
| `methodName` | the inheriting method's name; traits only |

Both path parameters omit any `ext` parameter and its braces, so
`/bom/{itemId}{ext}` gives `resourcePath: /bom/{itemId}` and
`resourcePathName: bom`.

### Parameter functions

Append with a pipe: `<<resourcePathName | !singularize>>`.

| Function | Example |
| --- | --- |
| `!singularize` | `users` to `user` |
| `!pluralize` | `user` to `users` |
| `!uppercase` | `userId` to `USERID` |
| `!lowercase` | `userId` to `userid` |
| `!lowercamelcase` | `UserId` to `userId` |
| `!uppercamelcase` | `userId` to `UserId` |
| `!lowerunderscorecase` | `userId` to `user_id` |
| `!upperunderscorecase` | `userId` to `USER_ID` |
| `!lowerhyphencase` | `userId` to `user-id` |
| `!upperhyphencase` | `userId` to `USER-ID` |

The only locale is United States English. A parameter may not appear in an
`!include` path or a `uses` value.

### Merge order

1. An explicit node beats an inherited one.
2. Collections merge by value. A trait's `enum: [win, mac]` beside a method's
   `enum: [mac, unix]` yields `[mac, unix, win]`.
3. Nearer wins: the method's own traits, then the resource's, then the traits
   on the matching method of the first resource type, then that resource type's
   own, and so outward. The same trait applied twice with different parameters
   is not a duplicate, and the nearer application's parameters survive.

## Security scheme

| Node | Value |
| --- | --- |
| `type` | one of the six below. Required, exact, case-sensitive. |
| `displayName?` | string |
| `description?` | string, Markdown |
| `describedBy?` | `headers`, `queryParameters` or `queryString`, `responses`, annotations |
| `settings?` | per type, below |

| `type` | Settings |
| --- | --- |
| `OAuth 1.0` | `requestTokenUri`, `authorizationUri`, `tokenCredentialsUri`, `signatures?` |
| `OAuth 2.0` | `authorizationUri`, `accessTokenUri`, `authorizationGrants`, `scopes?` |
| `Basic Authentication` | none |
| `Digest Authentication` | none |
| `Pass Through` | none; put the credentials in `describedBy` |
| `x-<other>` | none |

`signatures` holds any of `HMAC-SHA1`, `RSA-SHA1`, `PLAINTEXT`.
`authorizationGrants` holds any of `authorization_code`, `password`,
`client_credentials`, `implicit`, or an absolute URI. `authorizationUri` is
required only for the `authorization_code` and `implicit` grants.

### Applying

`securedBy` on a method overrides the resource's, which overrides the root's.
`null` in the array means the operation may also be called unsecured.

```yaml
securedBy: [ null, oauth_2_0 ]
securedBy: [ oauth_2_0: { scopes: [ ADMINISTRATOR ] } ]
```

## Annotation type

| Node | Value |
| --- | --- |
| every type-declaration facet | an annotation type is a data type |
| `allowedTargets?` | one target name, or an array of them |

With neither `type:` nor `properties:`, the annotation type is `string`.
Annotation types may extend data types; nothing may extend an annotation type,
and an annotation type may not be used where a data type is expected.

### Targets

`API`, `DocumentationItem`, `Resource`, `Method`, `Response`, `RequestBody`,
`ResponseBody`, `TypeDeclaration`, `Example`, `ResourceType`, `Trait`,
`SecurityScheme`, `SecuritySchemeSettings`, `AnnotationType`, `Library`,
`Overlay`, `Extension`.

Omit `allowedTargets` and every target is allowed.

### Annotating a scalar node

Rewrite the node as a map with a `value` key, then add the annotation:

```yaml
baseUri:
  value: https://api.example.com
  (redirectable): true
```

The nodes this works on: `displayName`, `description`, `type`, `schema`,
`default`, `example`, `usage`, `required`, `content`, `strict`, `minLength`,
`maxLength`, `uniqueItems`, `minItems`, `maxItems`, `discriminator`,
`minProperties`, `maxProperties`, `discriminatorValue`, `pattern`, `format`,
`minimum`, `maximum`, `multipleOf`, `requestTokenUri`, `authorizationUri`,
`tokenCredentialsUri`, `accessTokenUri`, `title`, `version`, `baseUri`,
`mediaType`, `extends`.

### Inheritance

An annotation on a type is **not** inherited by its subtypes. An annotation on
or inside a trait or resource type **is** applied to whatever inherits it. An
inheritor that applies an annotation of the same type replaces every inherited
application of that type.

## Fragments

The first line declares the kind, and the rest of the file is that construct's
body.

| Identifier | Contains |
| --- | --- |
| `#%RAML 1.0 DataType` | one type declaration |
| `#%RAML 1.0 NamedExample` | an `examples` map |
| `#%RAML 1.0 ResourceType` | one resource type declaration |
| `#%RAML 1.0 Trait` | one trait declaration |
| `#%RAML 1.0 SecurityScheme` | one security scheme declaration |
| `#%RAML 1.0 AnnotationTypeDeclaration` | one annotation type declaration |
| `#%RAML 1.0 DocumentationItem` | one `documentation` entry |
| `#%RAML 1.0 Library` | a library |
| `#%RAML 1.0 Overlay` | an overlay; **fastraml does not parse this** |
| `#%RAML 1.0 Extension` | an extension; **fastraml does not parse this** |

Every fragment may also carry `uses:`.

## Library

| Node | Value |
| --- | --- |
| `usage?` | string, Markdown |
| `types?` `schemas?` `resourceTypes?` `traits?` `securitySchemes?` `annotationTypes?` | as at the root |
| `uses?` | a map of namespace to library location |
| `(annotationName)?` | an annotation application |

Reach a library's declarations through the namespace `uses` bound it to:
`files.File`. The namespace is local to the file that wrote the `uses` node,
and **it does not chain** — `files.file-type.File` is invalid. Import the inner
library under its own name instead.

## Includes

`!include` is a YAML tag and takes the place of a node's value. It may not
appear in a type expression, in a multiple-inheritance sequence, or anywhere
other than a value position.

| Argument | Resolves against |
| --- | --- |
| a path with a leading `/` | the root RAML file |
| any other path | the file that wrote the `!include` |
| an absolute URL | itself |

A `.raml`, `.yml` or `.yaml` file — or one served as `application/raml+yaml`,
`text/yaml`, `text/x-yaml`, `application/yaml` or `application/x-yaml` — is
parsed and spliced in as structure. Anything else arrives as a string.

The argument must be a literal: no template parameters. YAML anchors do not
cross a file boundary in either direction.
