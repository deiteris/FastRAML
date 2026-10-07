"""Presentation text for source positions identified by the parser's grammar.

This catalogue documents constructs; it is not a table of accepted fields.
The parser's syntax projection selects the context, and the model supplies
local values and effective constraints (docs/21 § 4.2).
"""

from __future__ import annotations

from typing import Final

from fastraml.parser.syntax import Site

__all__ = ['BUILTINS', 'METHOD_DOCS', 'field_doc']

BUILTINS: Final = {
    'any': (
        'Accepts any value, including null, objects, arrays and scalars. It adds no constraints of its own.\n\n'
        'Use `any` when the contract deliberately leaves the value unspecified. Use a more specific type '
        'when clients need to know its structure; `any` does not mean an optional property.'
    ),
    'object': (
        'A value with named properties, such as a JSON object. Declare its fields with `properties`; '
        'each field has its own type and presence requirement.\n\n'
        'For example, `properties: {id: integer, name?: string}` requires `id` and allows `name` to be omitted. '
        'Undeclared properties are allowed unless `additionalProperties: false` closes the object.'
    ),
    'array': (
        'An ordered sequence of values. `items` declares the type of each element, while '
        '`minItems`, `maxItems` and `uniqueItems` constrain the collection.\n\n'
        '`type: User[]` is shorthand for `type: array` with `items: User`. '
        'An empty array is allowed unless a positive `minItems` requires elements.'
    ),
    'string': (
        'A sequence of Unicode characters, such as a name or identifier. '
        'Length facets count characters; `pattern` searches for a regular-expression match.\n\n'
        'For example, `minLength: 1` excludes the empty string. Use an anchored pattern such as '
        '`^[A-Z]+$` when the entire value must match, rather than merely contain matching text.'
    ),
    'number': (
        'A numeric value that may have a fractional part. `minimum`, `maximum` and `multipleOf` '
        'constrain its range and permitted increments.\n\n'
        'For example, `minimum: 0` permits zero and positive values, and `multipleOf: 0.01` '
        'requires exact hundredths. Use `integer` when the value must be a whole number.'
    ),
    'integer': (
        'A whole number. Numeric facets constrain its range and increments; integer `format` '
        'can additionally restrict it to a signed machine-integer range.\n\n'
        'For example, `minimum: 1` describes a positive count. `format: int8` restricts values '
        'to -128 through 127; use `number` when fractional values are meaningful.'
    ),
    'boolean': (
        'Accepts the logical values `true` and `false`. Use it for a two-state flag, not a string '
        'whose text happens to be "true" or "false".\n\n'
        '`false` is a value, not an absent property. Declare the property as optional separately '
        'if clients may omit the flag.'
    ),
    'date-only': (
        'A calendar date in RFC 3339 form, `yyyy-mm-dd`, without a time or time zone, '
        'for example `2026-10-07`.\n\n'
        'Use it for a birthday or another calendar day. It does not identify an instant; '
        'use `datetime` when a time-zone-aware timestamp is needed.'
    ),
    'time-only': (
        'A time of day in RFC 3339 form, `hh:mm:ss[.fraction]`, without a date or time zone, '
        'for example `09:30:00`.\n\n'
        'Use it for a recurring local time. It does not identify a particular day or an instant.'
    ),
    'datetime-only': (
        'A calendar date and local time separated by `T`, without a time-zone offset, '
        'for example `2026-10-07T09:30:00`.\n\n'
        'Use it when the contract intentionally describes local time. Use `datetime` when '
        'the value must identify an instant using `Z` or an explicit offset.'
    ),
    'datetime': (
        'A date and time with a time zone. The default RFC 3339 spelling uses `T` and either '
        '`Z` or an offset, for example `2026-10-07T09:30:00Z`.\n\n'
        '`format: rfc2616` selects the HTTP date spelling, such as `Sun, 06 Nov 1994 08:49:37 GMT`. '
        'Use `datetime-only` for a local date and time without an offset.'
    ),
    'file': (
        'File content, typically used for an upload or download body. `fileTypes` describes the '
        'permitted media types; `minLength` and `maxLength` constrain content length in bytes.\n\n'
        'For example, `fileTypes: [image/png]` documents a PNG file. A filename string is not '
        'the file content. Instance validation can check length, but has no media-type input '
        'with which to enforce `fileTypes`.'
    ),
    'nil': (
        'Accepts only null. Add it to a union when null is a meaningful value: '
        '`string | nil`, also written `string?`, accepts a string or null.\n\n'
        'Nullability and presence are separate. `name: string?` still requires the property, '
        'while `name?: string` permits omission but requires a string when present.'
    ),
}

METHOD_DOCS: Final = {
    'get': 'Retrieves a representation of the resource. GET is intended for reading, without requesting a change to server state.',
    'head': 'Requests the response headers of GET without a response body. It is useful for checking metadata without downloading the representation.',
    'post': 'Submits data for the resource to process, often to create a child resource or perform an action. Repeating a POST may have additional effects.',
    'put': 'Creates or replaces the state of the resource at the requested URI. Repeating the same PUT is intended to have the same effect as sending it once.',
    'patch': 'Requests a partial modification of a resource. The request body describes the change; repeating a PATCH is not necessarily idempotent.',
    'delete': 'Requests removal of the resource at this URI. This describes the HTTP operation, not a promise about how the server physically stores or deletes data.',
    'options': 'Requests information about the communication options available for a resource, such as supported methods. It is also used for CORS preflight requests.',
}

_COMMON: Final = {
    'displayName': (
        'A human-readable label for this declaration, shown in documentation and tools. '
        'It does not rename the declaration or change how references resolve.\n\n'
        'For example, a type named `UserId` can have `displayName: User identifier`.'
    ),
    'description': (
        'Documentation for this declaration. The text supports Markdown, including paragraphs, '
        'lists and links; hover shows the full text.\n\n'
        'Use it to explain the meaning of the value, how callers use it and any important constraints. '
        'A YAML block scalar (`description: |`) is convenient for multiple paragraphs.'
    ),
}

_TYPE: Final = {
    **_COMMON,
    'type': (
        'Declares the base data type or type expression. A mapping can specialize it with additional facets: '
        '`type: string` with `minLength: 1` describes a nonempty string.\n\n'
        'Use `User[]` for an array, `User | nil` for alternatives and `type: [A, B]` for multiple inheritance. '
        'Alternatives accept either member; multiple inheritance must satisfy both parents. '
        'A scalar declaration such as `Alias: User` gives another name to the same type.'
    ),
    'schema': 'Deprecated spelling of `type`. Prefer `type` for new declarations.',
    'properties': (
        'Declares object properties. Each entry names a field and describes its type; '
        '`name: string` is required, while `name?: string` permits omission.\n\n'
        'A mapping value can add constraints or documentation, for example '
        '`name: {type: string, minLength: 1}`. A `/regex/` key describes matching property names '
        'rather than one fixed name. Inherited properties remain part of the effective object.'
    ),
    'items': (
        'Declares the type of each array element. It can name a type or contain an inline declaration.\n\n'
        'For example, `items: User` requires every element to be a User; '
        '`items: {type: integer, minimum: 0}` requires nonnegative integers. '
        'Use `minItems` and `maxItems` to constrain the array length, not the individual elements.'
    ),
    'required': (
        'Controls whether a property or parameter must be present. This is different from permitting null as its value. '
        '`required: false` permits omission; `type: string | nil` permits a null value when present.\n\n'
        'Without explicit `required`, a trailing `?` makes the declaration optional. '
        'With explicit `required`, a trailing `?` in the key is part of the name. '
        'On a custom-facet declaration, this controls whether subtypes must supply the facet.'
    ),
    'default': (
        'Declares the value consumers should treat as the default. The parser checks it against the type; '
        'it does not insert it into examples or make a required property optional.\n\n'
        'For example, an optional `limit` parameter can declare `default: 20` to document the '
        'value used when the client omits it. Implementing that behavior belongs to the API.'
    ),
    'example': (
        'One example value, optionally with metadata. Use either `example` or `examples`, not both.\n\n'
        'Write the data directly, or use a wrapper such as `example: {value: Alice, description: A sample name}`. '
        'The wrapper can set `strict: false` to disable ordinary example validation. '
        'An example illustrates the contract; it is not a default or a restriction on allowed values.'
    ),
    'examples': (
        'Named example values, or an included NamedExample fragment. Names distinguish scenarios '
        'such as a normal result and an empty result; they are not properties of the example data.\n\n'
        'Each entry can contain raw data or a wrapper with `value`, `description` and `strict`. '
        'Example metadata can control validation; use `strict: false` for an intentionally '
        'nonconforming illustration. Use either `example` or `examples`, not both.'
    ),
    'enum': (
        'Restricts values to the listed alternatives, which must also satisfy the other type constraints. '
        'For example, `enum: [draft, published]` permits only those two string values.\n\n'
        'Values retain their data types. A subtype may narrow an inherited enum but cannot add '
        'values its parent forbids. Use `examples` when values are illustrative rather than exhaustive.'
    ),
    'facets': (
        'Declares custom facets for subtypes to supply. Each entry describes the type, documentation '
        'and requirement of one facet value; it does not supply values for those facets.\n\n'
        'For example, `facets: {label: string}` requires a subtype to provide `label: A display name` '
        'alongside its `type`. Declare `label?: string` to make it optional. The declaring type '
        'does not provide its own facet. Custom facets describe the type declaration, not payload fields.'
    ),
    'allowedTargets': (
        'Restricts the sites where this annotation type may be applied, such as `TypeDeclaration` or `Method`.\n\n'
        'Omitting it permits every supported target; an empty list permits none. '
        'This restricts where the annotation is written, while the annotation type itself '
        'describes the value supplied inside `(annotationName)`.'
    ),
    'additionalProperties': (
        'Controls undeclared object properties. It defaults to true; false closes the object to '
        'names outside its effective explicit properties.\n\n'
        'For example, `properties: {id: integer}` with `additionalProperties: false` rejects a '
        'separate `name` field. Explicit properties and pattern properties are separate declarations; '
        'RAML forbids combining pattern properties with `additionalProperties: false`.'
    ),
    'discriminator': (
        'Names the object property used to identify a concrete subtype, for example `discriminator: kind`. '
        'The property must exist and have a scalar type.\n\n'
        'Subtypes use `discriminatorValue` to claim a value such as `kind: dog`. This lets a consumer '
        'select a variant from the data itself rather than guess which object shape fits.'
    ),
    'discriminatorValue': (
        'The value of the discriminator property that identifies this subtype. With '
        '`discriminator: kind`, `discriminatorValue: dog` identifies values whose `kind` is `dog`.\n\n'
        'If omitted, the declared type name supplies the discriminator value. This facet '
        'requires an effective discriminator and does not declare a new property.'
    ),
    'minProperties': (
        'The minimum number of properties in an object value. `minProperties: 1` excludes the empty object.\n\n'
        'It counts present fields, not declarations. Use a required property declaration when '
        'a particular field must be present; this facet only sets the total count.'
    ),
    'maxProperties': (
        'The maximum number of properties in an object value. `maxProperties: 3` permits at most three present fields.\n\n'
        'It counts all present fields, including allowed undeclared properties. '
        'Use `additionalProperties: false` to restrict their names rather than just their count.'
    ),
    'minItems': (
        'The minimum number of elements in an array value. `minItems: 1` requires a nonempty array.\n\n'
        'This constrains the collection length. The `items` declaration separately constrains '
        'each element, and `required` separately controls whether the array-valued property is present.'
    ),
    'maxItems': (
        'The maximum number of elements in an array value. `maxItems: 10` permits up to ten elements, including none.\n\n'
        'Combine it with `minItems` for a bounded collection, or use equal minimum and maximum '
        'counts for a fixed-length array. Each element must still satisfy `items`.'
    ),
    'uniqueItems': (
        'When true, array elements must be distinct by their data values, not merely be different objects.\n\n'
        'For example, `[1, 2, 1]` fails. Numerically equal spellings such as `1` and `1.0` '
        'count as the same value; booleans remain distinct from numbers. '
        'This compares whole elements, not just one field of an object element.'
    ),
    'minLength': (
        'The minimum length of a string or file value: Unicode characters for a string, bytes for a file. '
        '`minLength: 1` excludes empty content.\n\n'
        'It does not require a property to be present. Combine it with a required declaration '
        'when clients must supply a nonempty value.'
    ),
    'maxLength': (
        'The maximum length of a string or file value: Unicode characters for a string, bytes for a file. '
        '`maxLength: 40` accepts values of length 40 or less.\n\n'
        'This is a validation constraint, not a request to truncate content. '
        'An empty value remains allowed unless another facet excludes it.'
    ),
    'pattern': (
        'A regular expression searched within a string value. A match anywhere is sufficient; '
        'use anchors to require a whole-value match.\n\n'
        '`pattern: cat` accepts `concatenate`, while `pattern: ^cat$` accepts only `cat`. '
        'Quote patterns when YAML punctuation or escapes would otherwise change the scalar text.'
    ),
    'minimum': (
        'An inclusive lower bound for a numeric value. `minimum: 0` permits zero but rejects negative values.\n\n'
        'Combine it with `maximum` for a range. A subtype may raise an inherited minimum '
        'to narrow the allowed values, but cannot lower it to admit values its parent rejects.'
    ),
    'maximum': (
        'An inclusive upper bound for a numeric value. `maximum: 100` permits 100 but rejects larger values.\n\n'
        'Combine it with `minimum` for a range. A subtype may lower an inherited maximum '
        'to narrow the allowed values, but cannot raise it beyond its parent limit.'
    ),
    'multipleOf': (
        'A numeric value must be an exact multiple of this positive number. '
        '`multipleOf: 0.5` permits 0, 0.5, 1 and other whole multiples, including negative ones.\n\n'
        'Use `minimum` to require nonnegative values. Decimal steps are compared exactly: '
        '`multipleOf: 1.1` accepts `2.2` without binary floating-point rounding.'
    ),
    'format': (
        'Selects a type-specific numeric or date-time format. The accepted names depend on the '
        'effective type; a format is not a general conversion instruction.\n\n'
        'Integer formats such as `int32` restrict signed value ranges. Number formats are '
        '`float` and `double`. For `datetime`, `rfc3339` is the default and `rfc2616` '
        'selects the HTTP date spelling.'
    ),
    'fileTypes': (
        'The permitted media types for file values, for example `fileTypes: [image/png, image/jpeg]`. '
        'Media ranges such as `image/*` are supported.\n\n'
        'This describes the content type, not a filename extension. It is retained for consumers; '
        'instance validation has no media-type input with which to enforce it.'
    ),
    'xml': (
        'Describes XML serialization, such as an attribute name, namespace or wrapping. '
        'For example, `xml: {attribute: true}` describes a property serialized as an XML attribute.\n\n'
        'The parser retains this metadata for consumers and projections. '
        'It does not serialize or deserialize XML wire content itself.'
    ),
    'anyOf': (
        'Declares alternatives for a union type. A value must satisfy at least one member; '
        'it may satisfy more than one.\n\n'
        '`type: A | B` is the usual expression spelling of a union. '
        'Use `type: [A, B]` for multiple inheritance, where both parents constrain the same value.'
    ),
    'uses': (
        'Imports libraries into this fragment. Each key is a namespace prefix, and its value is '
        'the library path. For example, `uses: {common: common.raml}` makes `common.User` available.\n\n'
        'Qualified names resolve in this fragment namespace; importing a library does not copy '
        'its declarations into the local `types` table.'
    ),
}

_ROOT: Final = {
    **_COMMON,
    'title': 'The human-readable title of the API, used in documentation and tools. It identifies the API for readers; it does not determine resource paths or type names.',
    'version': (
        'The API version, for example `version: v1`. When `baseUri` contains `{version}`, '
        'this value supplies that variable unless it is declared as a base-URI parameter.\n\n'
        'This describes the API version, not the RAML language version, which is written in the header.'
    ),
    'baseUri': (
        'The base URI for resource paths, for example `https://api.example.com/v1`. '
        'A resource `/users` is addressed relative to this base.\n\n'
        'It may contain URI-template variables such as `{host}` or `{version}`. '
        'Declare client-supplied variables in `baseUriParameters`; the root `version` '
        'can supply the special version variable.'
    ),
    'baseUriParameters': (
        'Declares parameters used in the base URI template, such as `host` in `https://{host}/api`. '
        'Each entry describes the value clients can supply, its constraints and its documentation.\n\n'
        'These values build the base URI; they are not query parameters or request headers. '
        'A declared `version` parameter is supplied by the caller rather than bound from the root version.'
    ),
    'protocols': (
        'Declares HTTP, HTTPS, or both, for example `protocols: [HTTPS]`. Methods can override '
        'the root protocols. If omitted, the base URI scheme can establish the effective protocol.\n\n'
        'This describes permitted transport schemes, not an authentication mechanism.'
    ),
    'mediaType': (
        'Default media types for bodies that do not declare their media type explicitly. '
        'For example, `mediaType: application/json` lets a body contain a data-type declaration directly.\n\n'
        'A body can instead list media-type keys explicitly, such as `application/xml: User`. '
        'The media type describes the wire representation; the body type describes its values.'
    ),
    'documentation': (
        'User-guide entries, each with a title and Markdown content. Use them for API-wide '
        'topics such as getting started, authentication or pagination.\n\n'
        'Each list entry contains `title` and `content`. Use a declaration `description` '
        'for documentation specific to one type, resource or method.'
    ),
    'types': (
        'Named data-type declarations available in this namespace. Reuse a declaration by '
        'name in properties, parameters, bodies or other type declarations.\n\n'
        'For example, `types: {UserId: integer}` makes `UserId` a reusable type. '
        'Types from an imported library use its prefix, such as `common.UserId`.'
    ),
    'schemas': 'Deprecated spelling of `types`. Prefer `types` for new declarations.',
    'annotationTypes': (
        'Named types describing annotation values and, optionally, their allowed application sites. '
        'Apply one using a parenthesized key, such as `(deprecated): true`.\n\n'
        'The annotation type validates that value; `allowedTargets` can restrict where it appears. '
        'Annotations describe declarations and do not become fields in an API payload.'
    ),
    'traits': (
        'Reusable method templates. Apply them with `is`, for example `is: [paged]`, '
        'to contribute query parameters, headers, responses or other method fields.\n\n'
        'Templates can contain `<<parameter>>` placeholders. An application such as '
        '`is: [paged: {size: 20}]` supplies values before the fields are merged into the method.'
    ),
    'resourceTypes': (
        'Reusable resource templates. Apply one with `type` on a resource, for example '
        '`type: collection`, to contribute resource fields and methods.\n\n'
        'This is structural reuse, not data-type inheritance. Placeholders can parameterize '
        'the template, and an optional method such as `get?` contributes only when the resource declares that method.'
    ),
    'securitySchemes': (
        'Named authentication mechanisms. Each declaration identifies its mechanism with '
        '`type` and can document protocol settings and authentication-related request or response fields.\n\n'
        'Apply schemes with `securedBy`, for example `securedBy: [basic]`. '
        'Declaring a scheme alone does not require authentication on a method.'
    ),
    'uses': _TYPE['uses'],
    'usage': (
        'Guidance for someone using this library or template, such as when to choose it and '
        'which parameters an application needs. The text can contain Markdown.\n\n'
        'Use `description` for the meaning of a declaration; use `usage` to explain how to reuse it.'
    ),
    'extends': (
        'The master API, Overlay or Extension this document builds on, expressed as a file or URI. '
        'The chain is loaded and merged before ordinary parsing.\n\n'
        'An Overlay adds documentation-oriented changes; an Extension can change the API contract. '
        'This is document composition, not the `type` inheritance of a data declaration.'
    ),
    'securedBy': (
        'Default security requirements for resources and methods. Entries name declared schemes, '
        'and each entry is an alternative way to authenticate.\n\n'
        'A null entry permits unauthenticated access, for example `securedBy: [basic, null]`. '
        'Resource and method declarations can provide their own security requirements.'
    ),
}

_RESOURCE: Final = {
    **_COMMON,
    'type': (
        'Applies a resource type template. Its contributions are merged with the declaration of this resource. '
        'For example, `type: collection` reuses the resource template named `collection`.\n\n'
        'An application can supply placeholders, such as `type: {collection: {item: User}}`. '
        'Here `type` names a resource template, not the data type of a request or response body.'
    ),
    'is': 'Applies traits to the methods of this resource. For example, `is: [paged]` contributes the trait method fields to each applicable method; application arguments supply its `<<parameter>>` placeholders.',
    'securedBy': 'Security requirements for this resource and its methods, overriding the inherited default. Entries name alternative authentication schemes; a null entry permits unauthenticated access. Use `securedBy: [null]` to document an explicitly public resource.',
    'uriParameters': (
        'Declares parameters named by the URI template of this resource, such as `id` in `/users/{id}`. '
        'Each value declares a type, constraints and documentation for that path segment.\n\n'
        'For example, `id: {type: integer, minimum: 1}` describes positive numeric IDs. '
        'These are path parameters, not query parameters following `?` in the URL.'
    ),
    'usage': _ROOT['usage'],
    'uses': _TYPE['uses'],
}

_METHOD: Final = {
    **_COMMON,
    'is': 'Applies reusable trait templates to this method. For example, `is: [paged: {size: 20}]` substitutes the trait parameters and merges its fields with the method declaration. Trait contributions become part of the effective request and responses.',
    'securedBy': 'Security requirements for this method, overriding the inherited default. Entries are alternative ways to authenticate, not schemes that must all be used together. A null entry permits unauthenticated access, for example `securedBy: [basic, null]`.',
    'protocols': 'Overrides the API protocols for this method. For example, `protocols: [HTTPS]` documents that this operation requires HTTPS even if the API permits HTTP elsewhere. Authentication is declared separately with `securedBy`.',
    'queryParameters': (
        'Declares individual query parameters, such as `limit` in `?limit=20`. Each entry describes '
        'one parameter type, its presence requirement and any constraints.\n\n'
        '`limit?: {type: integer, minimum: 1}` permits omission but validates a supplied limit. '
        'Use either this or `queryString`, not both; `queryString` describes the query as a whole.'
    ),
    'queryString': (
        'Declares the query string as a whole using a data type, rather than listing each '
        'parameter directly in the method. It can reuse a named object type or an inline declaration.\n\n'
        'For example, `queryString: SearchQuery` reuses that type for the query contract. '
        'Use either this or `queryParameters`, not both.'
    ),
    'headers': 'Declares request headers and their types. For example, `X-Request-Id?: string` documents an optional request header. The declaration describes header values and presence; it does not add them automatically to requests.',
    'body': (
        'Declares request bodies by media type, or uses the default media types of the API. '
        'For example, `body: {application/json: User}` describes a JSON representation of User.\n\n'
        'With a root `mediaType`, the body can contain a type declaration directly. '
        'The body declaration describes transmitted data, not query parameters or response data.'
    ),
    'responses': (
        'Response declarations indexed by HTTP status code, for example `200` for success or `404` '
        'when a resource is not found. Each response can describe headers, bodies and documentation.\n\n'
        'Use separate entries when outcomes have different data contracts. '
        'This documents possible server responses; it does not implement HTTP status selection.'
    ),
    'usage': _ROOT['usage'],
    'uses': _TYPE['uses'],
}

_RESPONSE: Final = {
    **_COMMON,
    'headers': 'Declares response headers and their types, such as `Location: string` on a creation response. These headers belong to this response status, not the incoming request. The declaration documents values and presence; it does not generate headers.',
    'body': 'Declares response bodies by media type, or uses the default media types of the API. For example, `body: {application/json: User}` describes the representation returned for this status. Different response statuses can have different body types.',
}

_SECURITY: Final = {
    **_COMMON,
    'type': (
        'Identifies the authentication mechanism, such as Basic Authentication or OAuth 2.0. '
        'Basic Authentication sends credentials in the Authorization header; OAuth 2.0 uses access tokens.\n\n'
        'It is not a data-type reference or a resource template. '
        'Declare protocol-specific options under `settings` and apply the scheme with `securedBy`.'
    ),
    'settings': 'Configuration specific to this authentication mechanism. For OAuth 2.0 this can describe authorization and token endpoints, grants and scopes. These are scheme settings, not request payload properties or credentials supplied by a particular client.',
    'describedBy': 'Headers, query parameters and responses used by this authentication mechanism. For example, it can document an Authorization header and a 401 response. These descriptions explain the authentication contract; applying it to operations uses `securedBy`.',
    'uses': _TYPE['uses'],
}

_FIELDS: Final = {
    Site.ROOT: _ROOT,
    Site.TYPE: _TYPE,
    Site.RESOURCE: _RESOURCE,
    Site.METHOD: _METHOD,
    Site.RESPONSE: _RESPONSE,
    Site.SECURITY_SCHEME: _SECURITY,
    Site.DOCUMENTATION: {
        'title': 'The title of this guide entry, used to identify the topic in API documentation. Choose a reader-facing topic such as Getting started or Pagination.',
        'content': 'The Markdown content of this guide entry. Use paragraphs, lists, links and code examples to explain an API-wide topic. A YAML block scalar (`content: |`) preserves multiline documentation.',
    },
}


def field_doc(site: Site, name: str) -> str | None:
    """Documentation for a field in a known structural context."""
    fields = _FIELDS.get(site)
    return None if fields is None else fields.get(name)
