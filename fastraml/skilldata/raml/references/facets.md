# Facet reference

Every facet each built-in type accepts, and its default. A facet is legal on a
type only if that type appears in the facet's row below, or if the facet is in
the first table, which every type accepts.

## Facets every type declaration accepts

| Facet | Value | Notes |
| --- | --- | --- |
| `type` | a type name, a type expression, or an inline declaration | What this type extends or wraps. Mutually exclusive with `schema`. |
| `schema` | as `type` | Deprecated RAML 0.8 alias for `type`. Write `type`. |
| `default` | an instance | Used when the instance is entirely absent. For a URI parameter, the client substitutes it. |
| `example` | an instance, or a map with `value` | Mutually exclusive with `examples`. |
| `examples` | a map of name to example | Mutually exclusive with `example`. |
| `enum` | an array of instances | The instance must equal one of them. |
| `displayName` | string | For documentation only. |
| `description` | string, GitHub-flavoured Markdown | |
| `facets` | a map of facet name to declaration | Restrictions that **subtypes** must supply. |
| `xml` | a map | XML serialisation. See below. |
| `(annotationName)` | an instance of that annotation type | |

The `example` map form takes `value` (the instance itself), `displayName`,
`description`, `strict` and annotations. `strict: false` skips validating that
one example. Use the map form whenever the instance has a property named
`value`, or the parser reads the instance as the wrapper.

## `any`

No facets beyond the table above. `any` is the root of every inheritance tree
and imposes no restriction.

## `object`

| Facet | Value | Default |
| --- | --- | --- |
| `properties` | a map of property name to type name or inline declaration | |
| `minProperties` | integer | |
| `maxProperties` | integer | |
| `additionalProperties` | boolean | `true` |
| `discriminator` | the name of a declared scalar property | |
| `discriminatorValue` | scalar | the type's own name |

A property declaration accepts every facet of its own type, plus:

| Facet | Value | Default |
| --- | --- | --- |
| `required` | boolean | `true` |

A property name wrapped in slashes — `/^note\d+$/` — is a pattern property and
constrains matching additional keys. `//` matches every additional key. An
explicit property beats a pattern that also matches; among patterns, the first
written wins. Pattern properties are illegal where `additionalProperties` is
`false`.

`discriminator` and `discriminatorValue` are illegal in an inline type
declaration and on a union type.

## `array`

| Facet | Value | Default |
| --- | --- | --- |
| `items` | a type name or inline declaration | |
| `minItems` | integer, 0 or more | `0` |
| `maxItems` | integer, 0 or more | `2147483647` |
| `uniqueItems` | boolean | `false` |

`type: Email[]` and `type: array` with `items: Email` declare the same thing.

## `string`

| Facet | Value | Default |
| --- | --- | --- |
| `pattern` | a regular expression | |
| `minLength` | integer, 0 or more | `0` |
| `maxLength` | integer, 0 or more | `2147483647` |

`pattern` is a **search**, not a full match. Write `^` and `$` yourself when
you mean the whole string.

## `number`

| Facet | Value | Default |
| --- | --- | --- |
| `minimum` | number | |
| `maximum` | number | |
| `multipleOf` | number | |
| `format` | `int`, `int8`, `int16`, `int32`, `int64`, `long`, `float`, `double` | |

## `integer`

Every facet of `number`. An instance must be a whole number.

## `boolean`

No facets beyond the common table.

## `date-only`, `time-only`, `datetime-only`

No facets beyond the common table, and **no `format` facet**.

| Type | Notation | Example |
| --- | --- | --- |
| `date-only` | RFC 3339 full-date, `yyyy-mm-dd` | `2015-05-23` |
| `time-only` | RFC 3339 partial-time, `hh:mm:ss[.ff]` | `12:30:00` |
| `datetime-only` | the two joined by `T`, no offset | `2015-07-04T21:00:00` |

## `datetime`

| Facet | Value | Default |
| --- | --- | --- |
| `format` | `rfc3339` or `rfc2616` | `rfc3339` |

`rfc3339` is `2016-02-28T16:41:41.090Z`. `rfc2616` is
`Sun, 28 Feb 2016 16:41:41 GMT`, and you must set `format` to use it.

## `file`

| Facet | Value | Default |
| --- | --- | --- |
| `fileTypes` | an array of media-type strings; `*/*` is valid | |
| `minLength` | integer, in bytes | `0` |
| `maxLength` | integer, in bytes | `2147483647` |

## `nil`

No facets beyond the common table. Accepts only a null value: YAML `null` or
`~`, JSON `null`, XML `xsi:nil`. In a header, a URI parameter or a query
parameter, it accepts only the literal string `nil`.

## union

A union has no facets of its own. It may carry a facet only when **every**
member accepts that facet, so `number | integer` accepts `minimum` and
`number | integer | string` does not. A member type may make a facet legal by
declaring it in its own `facets:` block.

An `enum` on a union must have every value satisfy at least one member.

## `xml`

| Node | Value | Default |
| --- | --- | --- |
| `attribute` | boolean; scalar types only | `false` |
| `wrapped` | boolean; not on a scalar, not with `attribute: true` | `false` |
| `name` | string | the type's or property's name |
| `namespace` | string | |
| `prefix` | string | |

## User-defined facets

`facets:` declares restrictions that **subtypes** must supply. A declaration
there uses property-declaration syntax, so a trailing `?` makes the facet
optional:

```yaml
types:
  CustomDate:
    type: date-only
    facets:
      noHolidays: boolean         # every subtype must supply it
      onlyFutureDates?: boolean   # a subtype may
```

Rules:

- The declaring type does **not** supply a value for its own facet. Doing so is
  an unknown-facet error.
- A facet name may not begin with `(`.
- A facet name may not collide with a built-in facet of that type, nor with any
  facet an ancestor declared.
- Two parents declaring the same user-defined facet makes the subtype invalid.

## Type inference

When a declaration names no `type:` and no `schema:`:

1. A facet unique to one type infers that type — `properties:` means `object`.
2. Otherwise the type is `string`.
3. A `body` node carrying none of `properties:`, `type:` or `schema:` is `any`.
