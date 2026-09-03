# 03 — YAML layer and I/O

Everything above this layer sees `Node`, never a YAML library type. That
boundary buys three things: a node object we control (identity-hashable, slotted,
cheap), freedom to swap the YAML backend, and a single place where source
positions are computed.

## 1. Why RAML cannot use a plain `yaml.safe_load`

- **Order matters.** Spec § The Root of the Document: "Processors MUST preserve
  the order of nodes of the same kind." A `dict` from `safe_load` preserves it,
  but a load also discards…
- **Positions.** Every diagnostic must point at a line and column.
- **Tags.** `!include` is a YAML tag on a scalar; it must be intercepted, not
  resolved to a string.
- **Comments.** The first line — `#%RAML 1.0 Trait` — is a comment and is
  semantically required. Spec § Markup Language: "processors SHALL NOT completely
  ignore all YAML comments."
- **Repeated structural work.** The trait/resource-type merge operates on the
  *tree*, so the tree must survive decoding (see [02](02-architecture.md) § 6).

So the parser composes to a node tree and walks it itself.

## 2. The node model

```python
class NodeKind(IntEnum):
    SCALAR = 0
    MAPPING = 1
    SEQUENCE = 2
    ALIAS = 3


class Node:
    __slots__ = ("kind", "tag", "value", "content", "line", "column", "end_line", "end_column")
```

- `content` is a flat `list[Node]`. For a mapping it is `[k0, v0, k1, v1, …]` —
  **not** a list of pairs. This mirrors go-raml (`yaml.Node.Content`) and matters:
  the decoders iterate `range(0, len(content), 2)` with zero tuple allocation, and
  the structural merge can splice key/value pairs positionally.
- `tag` is the resolved YAML tag string (`!!str`, `!!int`, `!!null`,
  `!!timestamp`, or a custom `!include`).
- Positions are **1-based**, matching YAML tooling and LSP-1 conventions;
  `end_line`/`end_column` span the token so an editor underlines the whole thing.
- No `__eq__` is defined, so `Node` keeps identity hashing. This is a
  **requirement**, not an accident: the provenance overlay
  ([08](08-templates-and-endpoints.md)) is a `dict[Node, ParseCtx]` keyed by
  object identity, and the merge preserves node identity so those lookups work.

### 2.1 Backend adapter

```python
def compose(text: str, *, uri: str) -> Node
```

Implementation: `yaml.compose(text, Loader=_RamlLoader)`, then a single recursive
conversion of PyYAML's `ScalarNode`/`MappingNode`/`SequenceNode` into our `Node`.
`_RamlLoader` subclasses `yaml.CSafeLoader` when libyaml is present and
`yaml.SafeLoader` otherwise, and replaces the implicit-resolver table — see
§ 2.2.

Notes on that conversion:

- PyYAML's `MappingNode.value` is a list of `(key, value)` tuples; flatten it.
- `start_mark`/`end_mark` are 0-based; add 1 to line and column.
- PyYAML resolves `2015-05-23` to `tag:yaml.org,2002:timestamp` under
  `SafeLoader`. RAML wants the **literal text** for `date-only` examples, so the
  scalar converter keeps `node.value` (the raw string) for `!!timestamp` and lets
  the type layer parse it. go-raml does the same
  (`scalarNodeToNodeValue`: `case TagStr, TagTimestamp: return node.Value`).
- Duplicate keys: PyYAML's composer permits them. RAML forbids duplicates in
  several places explicitly (types, libraries, status codes) and it is never
  meaningful elsewhere. The converter records duplicates per mapping and the
  decoders raise a positioned `duplicate key` diagnostic.
- **Anchors and aliases:** spec § Includes forbids anchors crossing a file
  boundary. Within a file YAML aliases are legal. The converter keeps `ALIAS`
  nodes with a resolved target pointer; `datanode` conversion **rejects** aliases
  (go-raml: `alias nodes are not supported`) except in `enum:`, where go-raml
  dereferences them (`MakeEnum`). pyRAML dereferences aliases uniformly at
  compose time, with a depth/expansion budget to stop billion-laughs expansion,
  and records the fact so error positions still point at the alias site.

### 2.2 Scalar resolution is YAML 1.2, not PyYAML's 1.1

RAML 1.0 is defined over YAML 1.2. PyYAML implements YAML 1.1. Left alone that
is not a nicety — it changes values:

| Written | PyYAML (1.1) | YAML 1.2, and go-yaml v3 |
|---|---|---|
| `no`, `yes`, `on`, `off` | boolean | **string** |
| `12:30:00` | integer 45000 | **string** |
| `190:20:30.15` | float | **string** |
| `1e3` | string | **float** |
| `0o17` | string | **integer 15** |
| `017`, `1_000` | integer | integer (both agree) |
| `2015-05-23` | timestamp | timestamp (both agree) |

`example: no` on a string type would become `False`, and the spec's own
`lunchtime: 12:30:00` on a `time-only` type would become a number. Two TCK
fixtures contain exactly that.

`_RamlLoader` therefore replaces three entries of PyYAML's implicit-resolver
table — `bool`, `int` and `float` — with `ruamel.yaml`'s YAML 1.2 patterns.
`null`, `str`, `seq`, `map` and `timestamp` are left alone because they already
agree. Timestamps stay implicit, as in go-yaml, and the scalar converter keeps
their text.

**Only the resolver changes; the scanner does not.** That is deliberate. PyYAML
ships a current libyaml, which since 0.2.2 accepts a colon inside a plain scalar
in flow context — `authorizationGrants: [ http://example.com ]`, which real RAML
writes. Implementations that carry an older scanner reject it.

The reference for "what is correct" is the pair of implementations RAML is
scored against: the spec, and `gopkg.in/yaml.v3`, which go-raml uses. go-yaml is
1.2 core for booleans, nulls and base-60, and keeps 1.1's underscores and
timestamps. Our table matches it on every form tested.

The claim is checked, not asserted. `tests/conformance` composes every document
in the TCK corpus, plus a table of scalar forms in four syntactic positions,
through both pyRAML and `ruamel.yaml` in YAML 1.2 mode, and fails on any
disagreement in shape, tag or text. Ruamel is a dev dependency; it never ships,
and nothing outside that test imports it.

Three gaps remain, all in the scanner and all shared with go-yaml:

- `[ ::vector ]` — a flow scalar beginning with a colon. Valid YAML 1.2, rejected
  by PyYAML. Not a construct RAML uses.
- U+2028/U+2029 inside a scalar. YAML 1.1 treats them as line breaks; 1.2 does
  not. PyYAML rejects them.
- `title:<TAB>value` parses under libyaml and is **rejected** by the pure-Python
  scanner. This one is a divergence between our own two backends, not a version
  question; see [12](12-performance.md) § 19.

### 2.3 Empty documents

A file that is empty after its RAML header line composes to an empty mapping
node rather than an error; the fragment decoder then decides whether an empty
body is legal (a `Trait` fragment with nothing in it is; an API without `title`
is not). go-raml does this in `decodeYAMLNode`.

## 3. Fragment identification

The first line is read *before* YAML composition:

```python
HEADS = {
    "#%RAML 1.0": FragmentKind.API,
    "#%RAML 1.0 Library": FragmentKind.LIBRARY,
    "#%RAML 1.0 DataType": FragmentKind.DATA_TYPE,
    "#%RAML 1.0 NamedExample": FragmentKind.NAMED_EXAMPLE,
    "#%RAML 1.0 DocumentationItem": FragmentKind.DOCUMENTATION_ITEM,
    "#%RAML 1.0 ResourceType": FragmentKind.RESOURCE_TYPE,
    "#%RAML 1.0 Trait": FragmentKind.TRAIT,
    "#%RAML 1.0 AnnotationTypeDeclaration": FragmentKind.ANNOTATION_TYPE,
    "#%RAML 1.0 SecurityScheme": FragmentKind.SECURITY_SCHEME,
    "#%RAML 1.0 Overlay": FragmentKind.OVERLAY,  # v1.1
    "#%RAML 1.0 Extension": FragmentKind.EXTENSION,  # v1.1
}
```

Matching is exact after stripping the trailing newline and trailing spaces.
Two accommodations, both from go-raml:

- A `.json` file loaded where a `DataType` is expected skips the header check
  entirely and is treated as an external JSON Schema.
- When a `DataType` is expected and an `AnnotationTypeDeclaration` header is
  found, it is accepted: the two are structurally identical.

Since we read the whole file into memory anyway (we must, for the size limit),
the head is taken by slicing to the first `\n`, not by a buffered reader.

## 4. `!include`

### 4.1 Resolution

```python
def resolve_ref_uri(raml: Raml, ref: str, location: str) -> str
def resolve_include_uri(raml: Raml, node: Node, location: str) -> str  # = resolve_ref_uri(raml, node.value, location)
```

Per spec § Includes there are three argument forms:

| Form | Rule |
|------|------|
| relative path (`types/user.raml`, `../x.raml`) | resolved against the **including file's** URI, RFC 3986 |
| absolute path (`/traits/pageable.raml`) | resolved against the **workspace root**, not the filesystem root |
| URL (`https://…`) | used as-is; requires an HTTP loader |

The workspace-root rule is what makes an "absolute" RAML path portable. Default
root is the directory of the entry file; `ParseOptions(workspace_root=...)` widens
it when an API spans sibling directories.

The registry is a parameter because the workspace root lives on it.

A `uses:` value is a path but not an `!include`, and the same three forms apply
to it, so it resolves through `resolve_ref_uri` as well. go-raml instead resolves
a `uses:` value with plain RFC 3986, which sends `/libs/a.raml` to the filesystem
root. Sharing one rule is a deliberate divergence from the reference
implementation, and a small one: a `uses:` path that starts with `/` is rare.

### 4.2 What an include produces

The extension of the *include argument* decides:

- `.raml`, `.yaml`, `.yml`, `.json` → composed to a `Node` tree and spliced in.
  (YAML 1.2 is a JSON superset, so `.json` composes correctly.)
- anything else → a single `!!str` scalar node holding the file's bytes decoded as
  UTF-8. This is spec § Resolving Includes: "the contents of the file are included
  as a scalar." It is how `content: !include docs/legal.markdown` works.

### 4.3 Caching, limits, and cycles

- `Raml.include_nodes: dict[str, Node]` — an include target is read and composed
  **once per parse** no matter how many times it is referenced. This is the same
  cache go-raml keeps and it is the difference between linear and quadratic on
  a project where 500 types each `!include` the same `common.raml`.
- `max_include_size` is enforced by reading at most `limit + 1` bytes and failing
  if the extra byte materialises — no need to stat, works for HTTP too.
- **Cycle detection** for scalar-include chains uses a `visited` set threaded
  through the value conversion, keyed by resolved URI, and reports
  `circular include detected` with the position of the offending tag.
  Fragment-level cycles (`a.raml` uses `b.raml` uses `a.raml`) are *legal* and are
  handled by the fragment cache: the second parse returns the in-progress fragment
  object, producing a cyclic object graph. Consumers that traverse the model must
  do their own cycle detection — this is documented in the public API
  ([13](13-public-api.md)).
- Every resolved include is recorded in `Raml.include_refs[source_uri]` with the
  literal argument, the resolved URI and the tag's position. Nothing in the parser
  reads it; it exists so a future LSP can emit document links, and it is cheap.

There are two entry points for a reason:

| Function | Reads the file? | Use when |
|----------|-----------------|----------|
| `note_include_ref(raml, node, location) -> str` | no | the target will be loaded by a *fragment* parser that has its own cache (`!include` of a DataType, Trait, NamedExample…) |
| `resolve_include(raml, node, location) -> (uri, Node)` | yes, cached | the target's content is spliced into the current tree as data |

Calling `resolve_include` where `note_include_ref` suffices doubles the I/O for
every typed-fragment include. Both return an empty URI for a node that is not an
include, so a decoder can call them unconditionally; `resolve_include` then
returns the node it was given.

The extension is taken after any `#fragment` or `?query` is stripped, so
`!include schema.json#/definitions/Item` is still a JSON include.

## 5. Resource loaders

```python
class ResourceLoader(Protocol):
    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes: ...
```

`max_bytes` carries the size limit of § 4.3. An implementation that honours it
returns at most `max_bytes + 1` bytes; returning more wastes memory, returning
less than the resource holds is wrong, because the caller cannot then tell a
truncated file from a complete one.

| Loader | Behaviour |
|--------|-----------|
| `FileLoader` | plain `open()`; no sandbox. For trusted CLI use only. |
| `SafeFileLoader(root)` | **default.** Refuses any path not beneath `root`, including via symlinks. |
| `HTTPLoader(client)` | `http`/`https`; only registered when a client is supplied. |
| `SchemeLoader({...})` | dispatches on URI scheme; `Raml.loader` holds one of these. |

`SafeFileLoader` enforces the sandbox. Go uses `safeopen.OpenBeneath`
(`openat2` with `RESOLVE_BENEATH` on Linux, `FILE_OPEN_REPARSE_POINT` on
Windows). Python has no direct equivalent, so `SafeFileLoader` combines four
checks:

1. Reject lexically: `os.path.relpath(target, root)` must not start with `..`.
2. Open with `os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))` where
   available, then `os.fstat` the descriptor.
3. Verify containment on the *realpath* of the opened file
   (`os.path.realpath`), not on the requested path — this closes the
   symlink-swap window for the common case.
4. Reject non-regular files (`stat.S_ISREG`) so a FIFO cannot hang the parser.

Against a local attacker who can modify the filesystem during the parse, these
checks are weaker than `openat2`. The loader's docstring states this limitation.
They do cover the threat model that matters here: an untrusted RAML document
reading `/etc/passwd` through `!include ../../../../etc/passwd` or through a
planted symlink.

`ParseOptions(file_loader=...)` accepts a custom loader; an LSP uses this to
shadow unsaved buffers. When you supply a loader, **you own the sandbox**. The
workspace root then affects only path resolution, not what the loader may open.

## 6. `DataNode`: structured user data

RAML has places where an arbitrary user value appears: `example`, `examples.*`,
`default`, `enum` members, annotation values, custom-facet values,
`discriminatorValue`. These are *data*, not declarations — but they still need
positions, because "this example does not validate" must point at the offending
key three levels down.

```python
class ValueNode:  # exactly one of the first three is set
    __slots__ = ("scalar", "mapping", "sequence", "raw")


class MappingValue:
    entries: list[MappingEntry]  # key, key_pos, value, value_pos


class SequenceValue:
    items: list[SequenceItem]  # value, value_pos


class DataNode:
    __slots__ = ("value", "include", "location", "key_pos", "value_pos")
```

`scalar` holds the value itself rather than a wrapper object. A YAML null is
therefore stored as `None`, which is also what the field holds when the value is
a mapping or a sequence; `ValueNode.is_scalar` is what distinguishes the two
cases.

A scalar's Python value comes from its **tag and its literal text**.
`!!timestamp` and any unrecognised tag keep the text, because RAML needs the
written form of a `date-only` example.

`make_data_node(raml, key_node, value_node, location)` is the single constructor;
`key_node` is `None` where there is no key, as in a sequence item.

`ValueNode.raw` holds the plain Python projection (`dict`/`list`/scalar) computed
**once** during construction. Validation and serialization use `raw`; diagnostics
use the position-bearing structure. Computing `raw` lazily was considered and
rejected: every value that is stored is validated at least once when
`OptWithValidate` is on, so laziness only adds a branch.

Two special cases in construction:

- A scalar whose text begins with `{` or `[` is parsed as **inline JSON**. This is
  how `type: '{"type":"object"}'` and inline JSON examples work.
- A `!include` produces a `DataNode` whose `location` is the *included* file and
  whose `include` records the argument, so a bad example inside an included file
  reports that file's path.

## 7. The annotated-scalar form

Spec § Annotating Scalar-valued Nodes: any scalar-valued node may be written as a
map with a `value` key so annotations can be attached:

```yaml
baseUri:
  value: http://www.example.com/api
  (redirectable): true
```

This is handled in exactly one function, `resolve_annotated_scalar(raml, node,
location) -> (Node, dict[str, DomainExtension])`, called by the generic
scalar-facet builder. It:

- returns the node unchanged if it is a scalar;
- if it is a mapping, extracts `value`, parses every `(annotation)` key into a
  `DomainExtension`, and rejects any other key;
- errors if `value` is absent.

Because every scalar facet goes through one builder

```python
def make_scalar_facet(raml, key_node, value_node, location, convert: Callable[[Node, str], T]) -> ScalarFacet[T]
```

the form is supported at all 30+ nodes the spec lists without per-facet code.
`!include` at a facet position is supported the same way, by the same builder.

`convert` supplies what Go takes from its type parameter — how to turn the
resolved node into a `T`. `scalar_str` serves every string facet; each later
facet type adds one function. The resulting extensions ride on
`ScalarFacet.annotations`.

The builder lives in `parser/facets.py` because it needs the include cache and
the domain-extension constructor; the `ScalarFacet` class it returns belongs to
the type model and lives in `types/base.py` ([02](02-architecture.md) § 2).

## 8. URI utilities

```python
path_to_file_uri(os_path)  -> str    # idempotent; cleans the path first
file_uri_to_path(uri)      -> str    # raises on a non-file URI
resolve_uri_ref(base, ref) -> str    # RFC 3986; normalises \ to / first
uri_base(uri)              -> str    # last path segment
uri_scheme(uri)            -> str    # "file" | "http" | "https" | ""
```

Windows specifics, all learned from go-raml's `uri.go`:

- `C:\a\b` → `file:///C:/a/b` (note the third slash).
- Backslashes in a *reference* are normalised to `/` before RFC 3986 resolution,
  because `os.path.relpath` produces them and they are not URL separators.
- Paths are `os.path.normpath`-ed before encoding so `a/b/../c` and `a/c` yield
  the same cache key.
