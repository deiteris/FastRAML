# 11 — Diagnostics

A RAML file that fails to parse should tell the author *what* is wrong, *where*,
and *how the parser got there*. The last part matters more than it sounds: an
error like "cannot inherit from different type: string vs integer" is useless
without the chain `unwrap shapes → unwrap shape (common.raml:15) → merge shapes →
inherit property 'a' (common.raml:17)`.

## 1. Model

```python
@dataclass(slots=True, frozen=True)
class Position:
    line: int
    column: int
    end_line: int = 0
    end_column: int = 0  # 1-based, end exclusive


class ErrorKind(StrEnum):
    PARSING = "parsing"
    READING = "reading"
    LOADING = "loading"
    RESOLVING = "resolving"
    UNWRAPPING = "unwrapping"
    VALIDATING = "validating"


class Trace:
    """One frame: a message, where it happened, and optional key/value info."""

    __slots__ = ("message", "location", "position", "kind", "info", "cause")


class RamlError(Exception):
    """A trace chain plus zero or more sibling chains."""

    __slots__ = ("head", "siblings")
```

Two composition operations, matching go-raml's `stacktrace`:

- **wrap** — `err = wrap("merge shapes", cause, location, position)` pushes a
  frame. This is the vertical dimension: how we got here.
- **append** — `err.append(other)` records an independent failure. This is the
  horizontal dimension: several things went wrong.

Rendering flattens both: a list of chains, each a list of frames, innermost last.

## 2. Accumulation

```python
class Accumulator:
    __slots__ = ("_errors",)

    def add(self, err: RamlError | None) -> None: ...
    def result(self) -> RamlError | None: ...
    def raise_if_any(self) -> None: ...
```

Passes that can tolerate a local failure use an accumulator:

| Pass | Granularity of tolerance |
|------|-------------------------|
| P3 resolve `uses:` | per library |
| P4a directive resolution | per endpoint, per trait application |
| P4b materialization | per facet, per operation, per nested endpoint |
| P7 shape resolution | per shape |
| P8 annotation resolution | per annotation |
| P9 unwrap | per declared type |
| P10 validation | per declared type, per annotation |

Everything else fails fast. The rule: **tolerate what is independent, stop at what
invalidates the rest**. Four failures are fail-fast, because continuing past any
of them produces a model that misrepresents the source: an unreadable entry file,
a missing or unrecognised RAML header, a non-mapping root, and a fragment whose
kind does not match the context.

Accumulation improves more than the error messages. A broken file still yields a
usable partial model, which is what an editor integration needs on every
keystroke — and `parse_lenient` ([13](13-public-api.md) § 1) is what hands that
model back.

**The table above is the whole of the tolerance, and deliberately so.** Tolerance
is *within* a pass, at the granularity each pass can defend. It is not between
passes: a pass that ran on state an earlier one reported as broken re-derives the
same fault instead of finding a new one, and measurement puts the cost at 41
diagnostics for one missing library ([13](13-public-api.md) § 1). So
`parse_lenient` stops where a strict parse stops and returns the half-built model
— which is the rule at the top of this section applied one level up: tolerate
what is independent, stop at what invalidates the rest.

## 3. Positions everywhere

Every entity carries `location` (a URI) plus `key_pos` and `value_pos`. They come
from the YAML node and are never recomputed by scanning text.

- `key_pos` points at the identifier (`minLength`, `/users`, `get`), which is
  what an author looks for.
- `value_pos` spans the value including composite children, so an editor can
  underline a whole block.
- For a node with a custom tag the span covers the tag too: `!include foo.raml`
  underlines all 18 characters, not the filename alone.

Sub-token positioning is used where it helps:

- **URI templates** — an error in `/bom/{item Id}` points at the space, computed
  as `uri_pos.column + byte_offset`.
- **Type expressions** — a bad name in `(Manager | Admn)[]` points at `Admn`,
  computed as `type_expr.value_pos.column + expression_column`.

## 4. Location attribution across the merge

After the structural merge a node's file is not the file being decoded
([08](08-templates-and-endpoints.md) § 6). One function answers the question:

```python
def location_of(self, node: Node | None, default: str) -> str:
    if node is None or self._active_overlay is None:
        return default
    scope = self._active_overlay.get(node)
    return scope.anchor.location if scope and scope.anchor else default
```

It is called at the top of every entity constructor and structural helper, not at
the loop that walks facets — because the merge synthesises intermediate container
nodes that carry no overlay mark of their own, while their grafted children do.
Calling it one layer deeper is what makes "error in the trait's 200 response"
report the trait's path.

## 5. YAML backend errors

PyYAML's messages embed positions and context in prose. They are normalised:

- the `MarkedYAMLError` marks become a `Position` (converted to 1-based);
- the message keeps the problem text but drops the `in "<unicode string>", line
  N, column M` tail, which duplicates the position we already carry;
- the result is wrapped in a `Trace` with `kind=PARSING` and the file URI.

## 6. Message style

Lowercase, no trailing period, no interpolated positions (the position is
structured data). Values that vary go in `info`, not the message:

```python
raise RamlError.new("cannot redefine built-in type", location, key_pos, info={"type": name})
```

Rendering produces `cannot redefine built-in type: type: string`. Keeping
variables out of the message string means diagnostics group cleanly and can be
matched in tests without brittle substring assertions.

## 7. Rendering

Two renderers ship:

```python
str(err)  # human: indented chains, "file:line:col message"
err.to_dict()  # machine: nested dicts, mirrors go-raml's JSON output
```

The `to_dict()` shape follows the reference implementation so that go-raml's CLI
output and fastRAML's are comparable during TCK work:

```json
{"traces": [{"stack": [
  {"message": "unwrap shapes", "position": "/tmp/library.raml:1",
   "severity": "error", "type": "parsing"},
  {"message": "cannot inherit from different type: source: string: target: integer",
   "position": "/tmp/common.raml:17:10", "severity": "error", "type": "unwrapping"}
]}]}
```

A third renderer — LSP `Diagnostic[]` — is trivial from the same data (the
`end_line`/`end_column` fields exist for it) and is left to a future package.

## 8. Cost on the success path

Diagnostics are built on the error path only. Two rules keep them off the hot
path:

- No f-string is evaluated unless an error is being constructed. Helpers take
  `info` as a dict, not a pre-formatted string.
- No traceback capture, no `inspect`, no stack walking. A `Trace` frame is four
  slots and is created only when a pass explicitly wraps.
