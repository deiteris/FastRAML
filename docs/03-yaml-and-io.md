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
    __slots__ = ('kind', 'tag', 'value', 'content', 'line', 'column', 'end_line', 'end_column')
```

- Mapping `content` is flat: `[key0, value0, key1, value1, ...]`.
- Tags use short YAML names such as `!!str`, `!!int`, `!!null`, and
  `!!timestamp`; `!include` is the only RAML local tag.
- Positions are 1-based and include token end positions. `full_position`
  extends through a node's descendants.
- `Node` defines neither equality nor hashing, so identity is preserved. The
  endpoint provenance overlay is keyed by node identity.
- YAML aliases are expanded to independent nodes. Recursive anchors are
  rejected, and alias expansion is bounded by the document node limit.
- Duplicate keys remain in the tree. Decoders call `duplicate_keys()` where
  their RAML construct forbids duplicates.

## 2. Composition and source decoding

`compose(text, uri=...)` uses a PyYAML loader configured for YAML 1.2 scalar
resolution, then converts the result to `Node`. It rejects unknown local tags,
syntax errors, excessive nesting, recursive anchors, and excessive alias
expansion.

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

Overlay and Extension headers are recognized and reported as unsupported.
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
Other targets become UTF-8 string scalar nodes. URI query and fragment suffixes
are ignored when determining the extension.

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
non-regular files. It protects against document-controlled path escape, but it
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
