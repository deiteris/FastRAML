# 03 - YAML layer and I/O

Everything above this layer works with `Node`, never a YAML-library node. This
preserves declaration order, YAML tags, source positions, and the source tree
needed for structural merge.

## 1. Node model

```python
class NodeKind(IntEnum):
    SCALAR = 0
    MAPPING = 1
    SEQUENCE = 2


class Node:
    __slots__ = ('kind', 'tag', 'value', 'content', 'line', 'column', 'end_line', 'end_column', '_position')
```

- Mapping `content` is flat: `[key0, value0, key1, value1, ...]`.
- A node is not edited after it is built; a pass that changes a tree builds
  new containers with `with_content`. Every node without children therefore
  shares one empty `content` list, and `position` is built on first use and
  kept ([12](12-performance.md) § 2).
- Tags use short YAML names such as `!!str`, `!!int`, `!!null`, and
  `!!timestamp`; `!include` is the only RAML local tag.
- Positions are 1-based and include token end positions. `full_position`
  extends through a node's descendants, past the bracket of every flow
  collection that closes on its last leaf's line, so a block ending in
  `{ rt: {item: T} }` ends after the outer `}`. A container a merge, an
  Overlay or a substitution rebuilds holds nodes written elsewhere in the file
  or in another file, so `with_grafts` builds it spanning what the node it was
  rebuilt from spans: a container's extent is where it was written. A filter,
  which keeps some of a container's own children, uses `with_content`, and its
  extent is read from what it kept.
- `Node` defines neither equality nor hashing, so identity is preserved. The
  endpoint provenance overlay is keyed by node identity.
- YAML aliases are expanded to independent nodes. Recursive anchors are
  rejected, and alias expansion is bounded by the document node limit. A copy
  keeps its anchor's position, so a block whose last value is an alias ends
  at the alias's key: `full_position` never ends before it starts.
- A mapping key written twice is rejected as `duplicate key` at the repeat,
  with `info['key']`, as YAML 1.2 requires. Keys compare as text, so `200` and
  `'200'` are one key, which is how RAML reads a status code. A file with a
  repeated key composes to nothing, as one with a syntax error does. go-raml
  accepts a repeated key.

## 2. Composition and source decoding

`compose(text, uri=...)` uses a PyYAML loader configured for YAML 1.2 scalar
resolution, then converts the result to `Node`. It rejects unknown local tags,
syntax errors, repeated mapping keys, excessive nesting, recursive anchors, and
excessive alias expansion.

`decode_source(data)` decodes source bytes as UTF-8 with an optional BOM. Other
encodings raise `UnicodeDecodeError`.

### 2.1 YAML 1.2 scalar behavior

The loader resolves YAML 1.2 booleans, integers, and floats. Consequently,
`yes`, `no`, `on`, `off`, and sexagesimal-looking text are strings; `1e3` is a
float; and `0o17` is an integer. Timestamp-tagged scalars retain their written
text for RAML date and time validation.

The underlying scanner has three documented compatibility limits:

- Unquoted U+2028 and U+2029 are rejected with a positioned diagnostic; quoted
  values work.
- A flow scalar beginning with `:` is rejected.
- `key:<TAB>value` depends on the selected PyYAML backend: libyaml accepts it
  and the pure-Python scanner rejects it.

### 2.2 Empty documents

An empty YAML document composes to an empty mapping. The fragment decoder decides
whether that body is valid for its fragment kind.

## 3. Fragment identification

The first source line identifies a RAML fragment and remains in the composed
text so later source positions are unchanged. Supported headers are:

```text
#%RAML 1.0
#%RAML 1.0 Library
#%RAML 1.0 DataType
#%RAML 1.0 NamedExample
#%RAML 1.0 DocumentationItem
#%RAML 1.0 ResourceType
#%RAML 1.0 Trait
#%RAML 1.0 AnnotationTypeDeclaration
#%RAML 1.0 SecurityScheme
```

An Overlay or Extension header is accepted only on the entry document, whose
`extends` chain is loaded as described in [19](19-overlays-and-extensions.md)
§ 2. `extends` resolves like an `!include` argument.
When a DataType is expected, a `.json` file is treated as external JSON Schema
and an `AnnotationTypeDeclaration` header is accepted as structurally
equivalent.

## 4. `!include`

### 4.1 Resolution

`resolve_ref_uri()` resolves both `!include` arguments and `uses:` values.

| Reference form | Resolution |
|---|---|
| Relative path | Relative to the referring file URI. |
| `/path` | Relative to the workspace root URI. |
| HTTP(S) URL | Used directly; requires an HTTP loader. |

Template parameters (`<<`) are forbidden in these paths. A workspace root
defaults to the entry file directory and can be changed with
`ParseOptions(workspace_root=...)`.

### 4.2 Include result

Targets ending in `.raml`, `.yaml`, `.yml`, or `.json` are composed as nodes.
A `.json` target's tabs before and after its root value are read as spaces
first: there YAML does not let a tab start a token, while JSON allows it. One
space for one tab keeps every position.
Other targets become UTF-8 string scalar nodes. URI query and fragment suffixes
are ignored when determining the extension.
An included file with invalid UTF-8 produces a diagnostic at its `!include`
directive, including when the target is a non-YAML file or a typed fragment.
An entry file with invalid UTF-8 raises a reading diagnostic before parsing.

An included file either is content or declares a kind. A file whose first line
is a RAML header (`#%RAML ...`) is a typed fragment: it must be valid as its
kind, and its kind must be the one the position takes (spec section Typed
Fragments). A file without one is content, read as if written where it is
included. So where a value is data — an example, an annotation value, a scalar
facet — a file with a RAML header, known kind or not, is `fragment is not
allowed here`, with the header in `info`. The spec is silent on this; the rule
keeps a NamedExample, a map of named examples, out of the place of one.

Where a position takes a mapping or a sequence — a resource or method value,
the `types:`, `annotationTypes:`, `traits:`, `resourceTypes:` and
`securitySchemes:` maps, `documentation:`, `responses:` and a response,
parameter and property maps, `facets:`, `enum`, `allowedTargets`, `xml`,
`fileTypes`, `protocols`, `mediaType`, `describedBy:`, `settings:` and a
setting's list, `is:` and `securedBy:` — `inline_include` reads an `!include`
of a file without a header as its content, located in that file, and one with
a header as `fragment is not allowed here`. A file that is not YAML is left for
the position to reject at the `!include`. A resource or a response stays keyed
where its key is written; its content is decoded, and located, in the
included file. A declaration in an included `types:` map is named in the
declaring document's namespace and indexed for unwrap and validation by the
file it is written in. Content is not a fragment, so invariant I3 does not
cover it: a file included twice is decoded twice, as the same text written
twice would be.

A position that takes a typed fragment — a `traits:`, `resourceTypes:`,
`securitySchemes:`, `types:` or `annotationTypes:` entry, `type:`, a `body:`,
`examples:`, a documentation item — reads a file without a header as the
declaration, written in that file (`content_include`). The declaration at the
key links to it, as it would to a fragment's, so it keeps its key's place; a
`.json` file where a type goes is a JSON Schema, as before. Which namespace
each kind of include resolves in is [04](04-fragments-and-namespaces.md) § 4.1.
Telling the two apart reads the file's first line, and the read is handed to
whichever reader follows, so a typed fragment is still read once.

### 4.3 Caching, limits, and cycles

- A composed include target is read and composed once per parse through
  `Raml.include_nodes`.
- The default size limit is 64 KiB per include target. Loaders receive the limit
  and may return one additional byte so an oversized target is detected without
  reading it in full. `0` disables the limit.
- Scalar include cycles are rejected. Fragment cycles through `uses:` are legal
  and produce cyclic model graphs.
- Every include occurrence is recorded in `Raml.include_refs` for tooling.

`note_include_ref()` records a typed-fragment include without loading it.
`resolve_include()` records, loads, and composes a data include. Both return an
empty URI for a non-include node.

## 5. Resource loaders

```python
class ResourceLoader(Protocol):
    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes: ...
```

| Loader | Behavior |
|---|---|
| `FileLoader` | Reads `file://` URIs without a sandbox. |
| `SafeFileLoader(root)` | Default file loader; confines reads to `root`. |
| `HTTPLoader(client)` | Loads HTTP(S) through a caller-supplied synchronous client. |
| `SchemeLoader` | Dispatches to a loader by URI scheme. |

`SafeFileLoader` rejects lexical traversal, final-component symlinks where the
platform supports `O_NOFOLLOW`, paths resolving outside the root, and
non-regular files. `contains(uri)` is its lexical check alone, and
`files(suffix)` lists the regular files beneath the root, entering no symlink
and no directory whose name starts with `.`; the language service reads a
folder through these (`docs/21` § 2). It protects against document-controlled path escape, but it
cannot provide the atomic filesystem guarantees of `openat2` against concurrent
local filesystem mutation.

Supplying `ParseOptions(file_loader=...)` replaces the default sandbox; the
caller then owns path safety. HTTP(S) schemes are registered only when
`ParseOptions(http_client=...)` is supplied. The client must provide synchronous
`get(url)` returning `status_code` and `content`; asynchronous clients are
rejected.

## 6. Structured data

`DataNode` represents arbitrary RAML values in examples, defaults, enums,
annotation values, custom facets, and discriminator values. It retains a
position-bearing structure and a plain Python `raw` projection. Scalar values
are derived from their YAML tag and literal text; timestamp values retain text.

Scalar text beginning with `{` or `[` is parsed as inline JSON. An included data
value records both the included location and include metadata.

## 7. Annotated scalars

A scalar-valued RAML node may use a mapping with `value` plus annotation keys:

```yaml
baseUri:
  value: http://www.example.com/api
  (redirectable): true
```

`resolve_annotated_scalar()` accepts a scalar unchanged or extracts `value` and
domain extensions from this mapping. Other keys and a missing `value` are errors.
`make_scalar_facet()` applies this rule and data includes to every scalar facet.

## 8. URI utilities

All parser locations are URIs. `path_to_file_uri()` canonicalizes OS paths to
`file://` URIs; `file_uri_to_path()` is restricted to `file://` URIs and is used
only by loaders. `resolve_uri_ref()` applies RFC 3986 resolution and normalizes
backslashes in references. On Windows, `C:\\a\\b` becomes `file:///C:/a/b`.
