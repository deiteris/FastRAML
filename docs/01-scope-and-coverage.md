# 01 - Scope and spec coverage

## 1. Scope

fastRAML is a RAML 1.0 parser and typed model for Python 3.12 and later. It
parses API documents and supported typed fragments, retains source positions,
resolves references, and can unwrap inheritance and validate declared values.

The public entry points are `parse_from_path`, `parse_from_string`, and
`parse_lenient`; `ParseOptions` controls unwrapping, validation, source
retention, loading, regex compilation, and recursion limits. See
[13](13-public-api.md).

The parser is single-threaded per `Raml` instance. Parallel work requires
separate parse instances.

## 2. Supported coverage

### 2.1 API documents

- Root metadata: `title`, `description`, `version`, `baseUri`,
  `baseUriParameters`, `protocols`, `mediaType`, and `documentation`. `baseUri`
  rejects invalid URI characters, malformed percent escapes, and malformed
  schemes. Every declared base URI parameter must name a template variable, and
  media types must use valid `type/subtype` syntax.
- Declarations: `types` (and the deprecated `schemas` alias),
  `annotationTypes`, `traits`, `resourceTypes`, `securitySchemes`, and `uses`.
- Domain extensions at supported RAML targets.
- Resources, methods, requests, bodies, responses, URI/query/header
  parameters, media types, protocols, and security inheritance. `queryString`
  and `queryParameters` are mutually exclusive, and a `queryString` type must
  not admit an array value.
- Resource types and traits, including parameters, transform functions,
  optional methods, chaining, and structural merge.

### 2.2 Types and values

- RAML scalar, object, array, union, `any`, and `nil` types.
- Type expressions, references, aliases, multiple inheritance, recursive
  types, inline declarations, properties, pattern properties, and custom
  facets.
- Examples, defaults, enums, XML serialization facets, discriminators, and
  declaration and instance validation.
- Facets beside a union type expression, including `properties` and `items`,
  are applied to every union member during unwrap. An `enum` beside the union
  is checked against the union. An `enum` inside a distributed declaration is
  narrowed per member ([07](07-resolution-and-inheritance.md) § 5).
- External JSON Schema types, including JSON Pointer targets. Supported drafts
  are determined by the installed `jsonschema` package.

### 2.3 Modularization

- `!include` supports relative paths, workspace-absolute paths, and opt-in
  HTTP(S) URLs. Non-YAML includes become string values.
- Libraries and `uses:` namespaces support one library hop; namespace chaining
  is rejected.
- Supported typed fragments are `Library`, `DataType`,
  `AnnotationTypeDeclaration`, `NamedExample`, `DocumentationItem`,
  `ResourceType`, `Trait`, and `SecurityScheme`.
- `Overlay` and `Extension` documents, as the entry document. Their `extends`
  chain is merged into the root API and decoded once; Overlays are checked
  against the spec's allowed differences. See
  [19](19-overlays-and-extensions.md), including its deliberate deviations
  (§ 7).

## 3. Unsupported features

- XML Schema external types. A `.xsd` target where a type fragment is expected
  reports `xml schema external types are not supported`.
- Applying several Overlays or Extensions that each extend the same master.
  Only the chain reached through the entry document is applied.
- XML serialization output. The `xml:` facet is retained on the model.
- Code generation, HTTP middleware, AMF conversion, and an LSP server. Views,
  bindings, and repository consumers are separate layers; see
  [16](16-graph.md) and [17](17-consumers.md).

## 4. Compatibility behavior

These behaviors define the current parser contract where RAML, YAML, or common
implementations leave room for interpretation.

### 4.1 Numeric formats

`number` accepts `float` and `double`. `integer` accepts `int8`, `int16`,
`int32`, `int`, `int64`, and `long`; `int` aliases `int32` and `long` aliases
`int64`. Formats do not cross between the two types.

### 4.2 Regex engines

`ParseOptions(regex_engine='re')` is the default. `regex_engine='re2'` uses
the optional `google-re2` package and rejects patterns it cannot compile.
RAML `pattern:` facets and pattern-property keys use the selected engine.
Patterns evaluated inside an external JSON Schema remain controlled by the
schema library and use Python `re`.

### 4.3 Fragment namespaces

Typed fragments resolve names in their own declarations and own `uses:` map;
they never inherit the namespace of a document that includes them. Template
parameter values resolve in the calling document because that is where their
text is authored. See [04](04-fragments-and-namespaces.md).

### 4.4 Loading and YAML

- File reads are confined to the workspace root by default. A custom
  `file_loader` replaces that protection and makes the caller responsible for
  path safety.
- A single `!include` target is limited to 64 KiB by default; `0` disables the
  limit. HTTP(S) loading requires `ParseOptions.http_client`.
- PyYAML is configured for YAML 1.2 scalar resolution. Unquoted U+2028/U+2029
  and a flow scalar beginning with `:` are not accepted by the underlying
  scanner. A tab immediately after a mapping colon is backend-dependent:
  libyaml accepts it and the pure-Python scanner rejects it.

### 4.5 JSON Schema and discriminators

JSON Schema types may be used in properties, arrays, unions, and parameters.
They cannot be specialized through RAML inheritance unless the raw schemas are
identical. A uniformly discriminated union dispatches by its discriminator;
an absent discriminator and non-uniform unions use ordinary member matching.

## 5. Dependencies

| Dependency | Purpose | Required |
|---|---|---|
| `PyYAML` | YAML composition | yes |
| `jsonschema`, `referencing` | external JSON Schema types | yes |
| `pluralizer` | template pluralization transforms | yes |
| `google-re2` | opt-in RE2 regex engine | optional |
| `httpx` or another synchronous `get(url)` client | HTTP(S) includes | optional |

`ruamel.yaml` is a development dependency used by YAML conformance tests.
