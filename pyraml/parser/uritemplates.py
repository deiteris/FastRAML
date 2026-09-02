"""URI template parsing and validation (RFC 6570 Levels 1 and 2).

An endpoint's URI is a template that may contain `{var}` (simple expansion),
`{+var}` (reserved expansion) or `{#var}` (fragment expansion) expressions.
This module only parses and validates the template text; it does not build or
cross-check `uriParameters` shapes, because that needs a shape model that does
not exist yet (`docs/08-templates-and-endpoints.md` section 8.2).

Every diagnostic here points at the exact offending byte within the template,
computed by shifting the scalar's own position (`Position.shifted`) rather than
reporting the position of the enclosing `uri:` node. See docs/11-diagnostics.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyraml.errors import ErrorKind, RamlError

if TYPE_CHECKING:
    from pyraml.positions import Position

__all__ = [
    'UriTemplateExpression',
    'extract_uri_template_params',
    'resource_path_name',
]

# RFC 6570 Level 2 operators this parser recognises. Anything else that opens
# an expression (e.g. `;`, `?`, `/`) is Level 3+ and out of scope: it is simply
# not stripped, so it falls through to varname validation and is reported as
# an invalid character.
_LEVEL_2_OPERATORS = ('+', '#')

# A segment that is nothing but a parameter must be at least "{x}" — three
# characters — to be treated as one; this also keeps a malformed bare "{}"
# (which extract_uri_template_params would already have rejected) from being
# silently skipped here too.
_MIN_PARAMETER_SEGMENT_LEN = 2

_ERR_UNCLOSED_BRACE = "unclosed '{'"
_ERR_NESTED_BRACE = "nested '{'"
_ERR_UNEXPECTED_BRACE = "unexpected '}'"
_ERR_EMPTY_EXPRESSION = 'empty expression'
_ERR_INVALID_CHARACTER = 'invalid character in variable name'
_ERR_INVALID_PCT_ENCODING = 'invalid pct-encoded sequence in variable name'


@dataclass(frozen=True, slots=True)
class UriTemplateExpression:
    """One `{...}` expression found in a URI template.

    `operator` is `''` for simple expansion, `'+'` for reserved expansion, or
    `'#'` for fragment expansion.
    """

    operator: str
    name: str


def _template_error(message: str, location: str, uri_pos: Position, offset: int, **info: str) -> RamlError:
    return RamlError.new(message, location, uri_pos.shifted(offset), kind=ErrorKind.PARSING, info=info or None)


def _is_varchar(char: str) -> bool:
    return ('a' <= char <= 'z') or ('A' <= char <= 'Z') or ('0' <= char <= '9') or char == '_'


def _is_hex_digit(char: str) -> bool:
    return ('0' <= char <= '9') or ('a' <= char <= 'f') or ('A' <= char <= 'F')


def _validate_varname(name: str, location: str, uri_pos: Position, name_start: int) -> None:
    """Validate `varname = varchar *( "." 1*varchar )`; `varchar = ALPHA / DIGIT / "_" / pct-encoded`.

    `name_start` is the byte offset of `name`'s first character within the
    enclosing URI template, so diagnostics land on the exact offending byte.
    """
    length = len(name)
    i = 0
    while i < length:
        char = name[i]
        if char == '%':
            if i + 2 >= length or not _is_hex_digit(name[i + 1]) or not _is_hex_digit(name[i + 2]):
                raise _template_error(_ERR_INVALID_PCT_ENCODING, location, uri_pos, name_start + i)
            i += 3
        elif char == '.':
            if i == 0 or i == length - 1 or name[i - 1] == '.':
                raise _template_error(_ERR_INVALID_CHARACTER, location, uri_pos, name_start + i, character=char)
            i += 1
        elif _is_varchar(char):
            i += 1
        else:
            raise _template_error(_ERR_INVALID_CHARACTER, location, uri_pos, name_start + i, character=char)


def extract_uri_template_params(uri: str, location: str, uri_pos: Position) -> list[UriTemplateExpression]:
    """Parse `uri` and return its RFC 6570 Level 1/2 template expressions.

    Raises `RamlError`, positioned at the exact offending byte, for: an
    unclosed `{`, a nested `{`, an unexpected `}`, an empty expression `{}`,
    an invalid character in a variable name, and a malformed percent-encoded
    sequence. See docs/08-templates-and-endpoints.md section 8.2.
    """
    expressions: list[UriTemplateExpression] = []
    length = len(uri)
    i = 0
    while i < length:
        char = uri[i]
        if char == '{':
            j = i + 1
            while j < length and uri[j] not in '{}':
                j += 1
            if j == length:
                raise _template_error(_ERR_UNCLOSED_BRACE, location, uri_pos, i)
            if uri[j] == '{':
                raise _template_error(_ERR_NESTED_BRACE, location, uri_pos, j)

            content = uri[i + 1 : j]
            operator = ''
            operator_len = 0
            if content and content[0] in _LEVEL_2_OPERATORS:
                operator = content[0]
                content = content[1:]
                operator_len = 1

            if not content:
                raise _template_error(_ERR_EMPTY_EXPRESSION, location, uri_pos, i)

            _validate_varname(content, location, uri_pos, i + 1 + operator_len)
            expressions.append(UriTemplateExpression(operator, content))
            i = j + 1
        elif char == '}':
            raise _template_error(_ERR_UNEXPECTED_BRACE, location, uri_pos, i)
        else:
            i += 1
    return expressions


def resource_path_name(full_uri: str) -> str:
    """The rightmost path segment of `full_uri` that is not a URI template parameter.

    Scans from the right over *segments* (`rstrip` + `rpartition`, not
    characters — docs/12-performance.md section 12), skipping trailing slashes
    and any segment that is entirely template expressions (`{...}`, possibly
    several concatenated, e.g. `{itemId}{ext}`). Returns `''` when every
    segment is such a parameter, or when `full_uri` is empty.

    >>> resource_path_name('/users/{userId}/addresses')
    'addresses'
    >>> resource_path_name('/users/{userId}')
    'users'
    >>> resource_path_name('/bom/{itemId}{ext}')
    'bom'
    """
    remainder = full_uri
    while remainder:
        remainder = remainder.rstrip('/')
        if not remainder:
            return ''
        remainder, _sep, segment = remainder.rpartition('/')
        if not (len(segment) > _MIN_PARAMETER_SEGMENT_LEN and segment[0] == '{' and segment[-1] == '}'):
            return segment
    return ''
