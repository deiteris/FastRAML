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
otherwise unavailable source position. `is_known` compares by value, so a node
built with no source, which spans `UNKNOWN`'s empty `1:1` too, is not known.

Span arithmetic lives on `Position` and nowhere else. `contains(inner)` tests
whether one span lies within another, `holds(line, column)` whether a point
lies within one (its end included, as an editor places a cursor just after a
token), and `Position.covering(spans)` gives the least span holding them all.

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
    __slots__ = ('cause', 'info', 'kind', 'location', 'message', 'origin', 'position')


class RamlError(Exception):
    __slots__ = ('head', 'siblings')
```

A `Trace` is one contextual frame. `Trace.cause` links to the next inner frame.
`RamlError.head` is the outermost frame, and `RamlError.siblings` contains
independent errors. `Trace.origin` is a second place that explains a frame,
with its own message: the constraint a value broke (§ 3.1).

A chain narrows. Each frame is at a node inside, or reached from, the one
outside it, and the innermost frame is at the node at fault, not at a
construct that holds it.

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

A `RamlError` pickles, so an error raised in a worker process reaches its
parent intact, and `copy` works on it. It is rebuilt from `head` and
`siblings`, not from `args`: `args` is the rendered message, which is text
and cannot rebuild a chain. Every value the parser puts in `info` pickles; a
caller-built error with an unpicklable `info` value does not.

## 2. Accumulation and partial results

`Accumulator` collects independent `RamlError` instances. `result()` returns
`None`, the single original error, or one error whose siblings contain the other
failures. `raise_if_any()` raises that combined result.

When it combines several errors, `result()` keeps a chain only if no chain
already kept equals it frame for frame (message, location, position, kind,
`info`, and where its origin is). One mistake reaches a pass once per copy of
the construct that holds it: a trait applied three times resolves three shapes
written at the trait's line, and a root `securedBy:` is bound once per level
that inherits it. Those chains are identical, so only the first is reported. Chains that differ in any
frame stay distinct.

Accumulation is local and nested. A decoder catches a failure only when it can
continue without misreading the enclosing construct. The current boundaries are:

| Pipeline area | What can continue independently |
|---|---|
| P1-P3 fragment decoding | Library links, declarations, a NamedExample fragment's examples, fields, and list entries where the decoder has a local recovery boundary. A fragment's `uses:` is resolved even when its body failed |
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

Reporting a child independently is not the same as retaining its siblings in
the model. The five declaration maps (`types:` or `schemas:`,
`annotationTypes:`, `traits:`, `resourceTypes:` and `securitySchemes:`) retain
them: each decoder fills the fragment's own map before it raises, so the good
declarations stay in the fragment and agree with the registry. A failed
declaration of any of the five kinds is kept there too, marked in
`Raml.broken` (docs/13 § 1). A declaration key written twice is a
`duplicate key` (docs/03 § 1).

The endpoint tree retains everything but a top-level resource whose full URI
an earlier resource took: a resource, an operation and a response are
attached before their content is decoded. A bad key in one response keeps
the response, its operation and every enclosing resource, each marked in
`Raml.broken`, and leaves their siblings unmarked (docs/13 § 1). A nested
resource whose full URI an earlier one took stays in its parent's
`endpoints`, outside `Raml.endpoints`, and it and every resource enclosing it
are marked; a top-level one is in neither, so it is absent, and so are the
resources beneath it. P6 still runs
over a kept resource, so an unused URI parameter on it is reported as its own
mistake. Below a declaration, a failed property, `items` or `anyOf` member
is absent from its declaration, which keeps its kind and the children that
built, and is marked.

`parse_lenient()` runs the same passes as `parse_from_path()` and stops at the
same failing pass. It returns the registry built up to that point and the error
that strict parsing would raise. Successfully decoded siblings remain available;
the construct that failed can be absent or incomplete.

When P9 fails, recursion is still marked over every declaration, so a
consumer's walk of the returned model terminates. A declaration whose merge
failed is not flagged unwrapped, and `Raml.unwrapped` stays `False`
(docs/07 § 6).

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

The same failure in an included fragment remains local: a library's arrives
under the `uses:` frame, and an `!include`d fragment's, in a type, example,
template or documentation position, under an `include` frame at the include,
neither of them a fatal key. That frame also places the failure in the
including file, where one that fails to load has no position of its own.
`parse_lenient()` accepts paths only; there is no string-input lenient entry
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
- A collection's full span ends at its last leaf, not at the line break
  PyYAML ends a block at; a flow collection's ends past its bracket, when
  that is on the last leaf's line.

YAML node spans come from the composer's marks. Some parsers derive a more
specific position within a scalar:

- URI-template diagnostics add a Python character offset to the URI scalar's
  text.
- Type-expression diagnostics rebase a lexer token's zero-based column onto the
  type-expression scalar's text.

Inside a caller's value that a template substituted, a type-expression
diagnostic is placed where the caller wrote the value, in the application's
file ([08](08-templates-and-endpoints.md) § 5.1). A transformed value is
written nowhere, so it is placed at the template's `<<...>>`.

Both find the text with `Position.within(text)`. A quoted flow scalar on one
line spans its text plus two quotes, so its text starts one column in. The
composer does not report a scalar's style, so a scalar whose span fits neither
form, such as a block scalar or a quoted one with an escape, keeps its node's
start, and a column inside it can be off.

`Position.shifted(offset, length)` returns a span of `length` characters, one
by default, at the shifted column. An unresolved or self-referential name spans
the whole name, so an editor underlines it; a lexer or URI-template error, the
offending character. A YAML syntax error is a point, and spans the character
at it.

A diagnostic is placed at the node at fault, and a construct that merely
holds it is context. So an unknown OAuth signature or grant is its item in
the list, two keys that exclude each other are reported at the one written
second, a value a URI parameter can never match is that value, a missing
required custom facet is the name of the type that lacks it, with the
facet's declaration as origin, and a missing `title` is the document's
header. A value of the wrong kind, a mapping where a string belongs, is
still the whole value.

### 3.1 Values against constraints

`validate_at` sees Python values, not nodes, so it raises each failure at the
constraint: the facet's key and value (`minLength: 5`), in the file that
wrote the facet, which may be a parent's; or, for a failure no one facet
states (a wrong type, a missing property, no union member), at the name the
type is declared under. `enum` is placed at its members.

Each P10 check of a value, an example, default, annotation value, custom
facet value or enum member, then moves those frames to the value
(`at_value`). The frame's `info['path']` is found in the value's `DataNode`,
and a frame naming `info['property']` is placed at that key. Where the frame
was becomes its `origin`, `declared here`. The wrapping frame is at the value
as written: an included example's is the `!include`, and the frames inside
are in the included file. A value with no position, a path inside an inline
JSON string or the root of an included file, leaves the frame unplaced, so
the chain is reported at its wrapper.

A template application is checked the same way. An unexpected parameter is
placed at its value in the application, with the template's name as its
origin, `declared here`. Its key would be exact, but an application keeps
only its parameters' values, and keeping the keys cost 2.6% of what the
`templates` bench retains. A missing one is placed at the application's
parameters, or its name when it has none, with the first `<<parameter>>` in
the template as its origin, `used here`.

### 3.2 Placement law

Every entity the model positions, over the TCK and the fixtures:

1. is keyed on one line, where the text of its `location` is its name as
   written: a property as `name?`, a documentation item as its title, a body
   without a media type as `body`, a template's key as its `<<parameter>>`;
2. has a `value_pos` that ends after it starts and starts after its key does;
3. and, for a resource nested in one written in the same file, lies inside
   that resource's span.

Rule 3 is checked for resources only, which no template contributes: under a
method, what a template wrote lies in the template, outside the method's span.
Exempt, each for its reason: a request, which has the method's key; a URI
parameter P6 synthesized, which was never written; a shape with no name, or
one standing for a type it names (`Book[]`'s items, a recursive reference),
placed at the key it is written under; and an unknown position.

`tests/unit/test_placement_law.py` checks it, as `test_occurrence_law.py`
checks names. A violation is a parser defect, fixed in its pass, so a consumer
converts a position without guarding it. The authorship view's span test
relies on it ([16](16-graph.md) § 10).

## 4. Locations after structural merge

Stage 2 endpoint decoding can read nodes supplied by traits or resource types.
The active provenance overlay maps those node identities to the `ParseCtx` under
which they must be decoded.

```python
def location_of(self, node: Node, default: str) -> str:
    anchor = self.document_anchor(node)
    if anchor is not None:
        return anchor.location
    overlay = self._active_overlay
    scope = None if overlay is None else overlay.get(node)
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
Additional YAML-layer diagnostics cover repeated mapping keys, recursive
anchors, depth and alias expansion limits, unknown local tags, and unsupported
unquoted line-separator characters. [YAML layer and I/O](03-yaml-and-io.md)
defines those rules.

## 6. Message conventions

New parser-authored diagnostics MUST use a stable message key:

- lowercase;
- no trailing period;
- no markup: an editor shows a diagnostic's message as plain text;
- no source position in the message;
- varying values in `Trace.info` rather than interpolated into `Trace.message`.

```python
raise RamlError.new(
    'cannot redefine a built-in type',
    location,
    key_pos,
    info={'type': name},
)
```

This keeps `Trace.message` suitable for grouping and for test assertions.
Tests assert the message key and `info` separately, not assembled display text.

`info` never repeats the text a diagnostic is placed at: not a value
(an example's, a facet's, a discriminator's), not a document's header line,
not a JSON Schema validator's message, which quotes the instance. That text
may come from any file an `!include` names, and a diagnostic travels further
than the file: to a log, a CI report, an editor. The position already shows
it to whoever may read the file. `info` holds names and constraints: the
facet, the bound, the pattern, the known discriminator values, the
`schema_path` or `keyword`.

A wrapped exception follows it too. An `OSError` with an `errno`, from any
loader, a caller's included, becomes `file not found`, `permission denied`,
`not a regular file`, or `cannot read file` with `info['error']`: its text
holds the OS path and the platform's wording. `LoaderError` and
`UnresolvedReferenceError` carry a key as their text and their variables as
`info` (`no loader for URI scheme` with `scheme` and `registered`,
`reference not found` with `missing`). Any other exception keeps its text,
which the model does not police.

`Trace.rendered_message()` appends `info` entries in insertion order, producing
`cannot redefine a built-in type: type: string` for the example above.

## 7. Rendering

Two renderers are part of the public error model:

- `str(error)` prints numbered chains. Each frame is indented and contains its
  location, optional start line and column, and rendered message.
- `error.to_dict()` returns a `traces` list whose entries contain flattened
  `stack` lists.

Each serialized frame contains `message`, `position`, `severity`, and `type`,
and `origin` when the frame has one: an object with its own `message` and
`position` (§ 1, § 3.1):

```json
{
  "traces": [
    {
      "stack": [
        {
          "message": "unwrap shape",
          "position": "file:///tmp/library.raml:12:3",
          "severity": "error",
          "type": "unwrapping"
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
