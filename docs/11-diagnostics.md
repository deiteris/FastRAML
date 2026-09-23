# 11 - Diagnostics

Diagnostics tell a caller what failed, where it failed, and which parser
operations led to the failure. A parse can report several independent failures,
and each failure can contain a chain of contextual frames.

## 1. Data model

`fastraml.positions.Position` stores a source span:

```python
@dataclass(slots=True, frozen=True)
class Position:
    line: int
    column: int
    end_line: int = 0
    end_column: int = 0
```

Lines and columns are 1-based. End positions are exclusive. A position with no
known end uses the default zero values. `UNKNOWN` represents a synthesized or
otherwise unavailable source position.

`fastraml.errors` defines the diagnostic graph:

```python
class ErrorKind(StrEnum):
    PARSING = 'parsing'
    READING = 'reading'
    LOADING = 'loading'
    RESOLVING = 'resolving'
    UNWRAPPING = 'unwrapping'
    VALIDATING = 'validating'


class Trace:
    __slots__ = ('cause', 'info', 'kind', 'location', 'message', 'position')


class RamlError(Exception):
    __slots__ = ('head', 'siblings')
```

A `Trace` is one contextual frame. `Trace.cause` links to the next inner frame.
`RamlError.head` is the outermost frame, and `RamlError.siblings` contains
independent errors.

Use these operations to compose diagnostics:

- `RamlError.new(...)` starts a chain.
- `RamlError.wrap(...)` adds an outer frame. Wrapping a `RamlError` preserves its
  sibling chains. Wrapping another exception records its text as the innermost
  frame.
- `RamlError.append(...)` returns a new error with another independent failure.
  It does not mutate either input.
- `RamlError.frames()` returns one chain from outermost to innermost.
- `RamlError.chains()` returns the main chain followed by all sibling chains.
- `RamlError.messages()` returns the rendered innermost message from each chain.

## 2. Accumulation and partial results

`Accumulator` collects independent `RamlError` instances. `result()` returns
`None`, the single original error, or one error whose siblings contain the other
failures. `raise_if_any()` raises that combined result.

Accumulation is local and nested. A decoder catches a failure only when it can
continue without misreading the enclosing construct. The current boundaries are:

| Pipeline area | What can continue independently |
|---|---|
| P1-P3 fragment decoding | Library links, declarations, fields, and list entries where the decoder has a local recovery boundary |
| P4 and P6 endpoint construction | Resources, directive applications, endpoint fields, operations, responses, nested resources, and URI parameters |
| P5 security | Scheme fields, settings, required settings, and scheme references |
| P7 shape resolution | Each queued shape |
| Discriminator declaration check | Each offending discriminator facet |
| P8 annotation resolution | Each annotation application |
| P9 unwrap | Each declared type |
| P10 validation | Declarations and annotation applications, with nested accumulation for facets, examples, defaults, and values |

This table describes recovery boundaries, not a promise that every malformed
child survives. Invalid container structure or a missing prerequisite can abort
the current enclosing construct.

`parse_lenient()` runs the same passes as `parse_from_path()` and stops at the
same failing pass. It returns the registry built up to that point and the error
that strict parsing would raise. Successfully decoded siblings remain available;
the construct that failed can be absent or incomplete.

`parse_lenient()` re-raises an entry-level failure when no trustworthy entry
model can be returned. An entry load failure raises before parsing starts.
During parsing, fatal classification requires both an outermost message key and
the entry URI as that frame's location:

| Message key | Meaning |
|---|---|
| `unknown fragment kind` | The entry header is missing or unrecognized |
| `fragment kind not supported` | The entry names an unsupported fragment kind |
| `extends is required`, `extends must be a string` | The entry Overlay or Extension names no master |
| `resolve extends` | The entry's `extends` chain could not be loaded; the frame is repeated once per document of the chain |
| `unexpected fragment kind` | A fragment header conflicts with the context that loaded it |
| `must be map` | The entry root is not a mapping |

The same failure in an included fragment remains local even when a type-fragment
include surfaces it without an extra frame, because its location is not the entry
URI. `parse_lenient()` accepts paths only; there is no string-input lenient entry
point.

## 3. Source positions

Source-backed declarations generally carry `location` and a `key_pos`, a
`value_pos`, or both. Synthesized and programmatically created entities may use
`UNKNOWN` or reuse the enclosing construct's position.

- `key_pos` identifies a declaration key such as `minLength`, `/users`, or
  `get`.
- `value_pos` usually spans the complete value, including child nodes.
- A tagged node's full span includes the tag, so `!include file.raml` can be
  highlighted as one source range.

YAML node spans come from the composer's marks. Some parsers derive a more
specific position within a scalar:

- URI-template diagnostics add a Python character offset to the URI scalar's
  start position.
- Type-expression diagnostics rebase a lexer token's zero-based column onto the
  type-expression scalar. This is exact for plain scalars. Quoting and block
  scalar prefixes can shift the reported column because the YAML composer does
  not expose their content offset.

`Position.shifted(offset)` returns a one-character span at the shifted column.

## 4. Locations after structural merge

Stage 2 endpoint decoding can read nodes supplied by traits or resource types.
The active provenance overlay maps those node identities to the `ParseCtx` under
which they must be decoded.

```python
def location_of(self, node: Node, default: str) -> str:
    documents = self._document_provenance
    if documents:
        anchor = documents.get(node)
        if anchor is not None:
            return anchor.location
    overlay = self._active_overlay
    if overlay is None:
        return default
    scope = overlay.get(node)
    if scope is not None and scope.anchor is not None:
        return scope.anchor.location
    return default
```

Provenance-aware decoders call `Raml.location_of()` at the boundaries where a
node can come from a merged source. The returned location is one of these, in
order:

1. the authoring document's URI, when an Overlay or Extension wrote the node
   ([19](19-overlays-and-extensions.md) § 5.3);
2. the overlay scope's anchor location;
3. the decoder's default location.

An anchor identifies the namespace used to resolve names and can differ from a
shape's authored `location`; callers must not assume they are interchangeable.

`node_error` applies the first rule itself. During a parse, a diagnostic built
for a node an extension document wrote is located in that document, whatever
location the caller passed. The lookup runs only once a failure exists.

Merge-created container nodes may have no overlay entry. Decoders therefore ask
for the location of the specific child that produced an entity or diagnostic,
not only the enclosing mapping.

## 5. YAML errors

`yamlnode.compose()` converts a `yaml.MarkedYAMLError` into one `RamlError`:

- `problem_mark`, or `context_mark` when no problem mark exists, becomes a
  1-based start position.
- `problem`, or `context` when no problem text exists, becomes the message.
- When both problem and context text exist, `Trace.info['context']` retains the
  context.
- PyYAML's generated `in "<unicode string>", line ..., column ...` text is not
  copied because the trace already carries the location and position.

An unmarked `yaml.YAMLError` keeps its raw text and has no source position.
Additional YAML-layer diagnostics cover recursive anchors, depth and alias
expansion limits, unknown local tags, and unsupported unquoted line-separator
characters. [YAML layer and I/O](03-yaml-and-io.md) defines those rules.

## 6. Message conventions

New parser-authored diagnostics MUST use a stable message key:

- lowercase;
- no trailing period;
- no source position in the message;
- varying values in `Trace.info` rather than interpolated into `Trace.message`.

```python
raise RamlError.new(
    'cannot redefine built-in type',
    location,
    key_pos,
    info={'type': name},
)
```

This keeps `Trace.message` suitable for grouping and for test assertions.
Tests assert the message key and `info` separately, not assembled display text.
Wrapped external exceptions and some existing diagnostics can contain free-form
text; the diagnostic model does not enforce the convention.

`Trace.rendered_message()` appends `info` entries in insertion order, producing
`cannot redefine built-in type: type: string` for the example above.

## 7. Rendering

Two renderers are part of the public error model:

- `str(error)` prints numbered chains. Each frame is indented and contains its
  location, optional start line and column, and rendered message.
- `error.to_dict()` returns a `traces` list whose entries contain flattened
  `stack` lists.

Each serialized frame contains `message`, `position`, `severity`, and `type`:

```json
{
  "traces": [
    {
      "stack": [
        {
          "message": "unwrap shapes",
          "position": "file:///tmp/library.raml",
          "severity": "error",
          "type": "parsing"
        },
        {
          "message": "cannot inherit from different type: source: string: target: integer",
          "position": "file:///tmp/common.raml:17:10",
          "severity": "error",
          "type": "unwrapping"
        }
      ]
    }
  ]
}
```

This is a projection, not a lossless serialization of the object graph.
`position` is a string, `info` is folded into the rendered message, and end
positions are omitted. A renderer that needs source ranges, such as an LSP
adapter, must read the in-memory `Trace.position` objects.

## 8. Success-path cost

Parser code constructs trace frames only after a failure occurs. It does not
capture Python tracebacks, inspect the call stack, or walk frames to build parser
context. Each parser operation that adds context calls `RamlError.wrap()`
explicitly. Variable values are passed as `info` when the diagnostic is created,
so the success path does not format the rendered diagnostic.
