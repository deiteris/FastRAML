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

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastraml.errors import ErrorKind, RamlError
from fastraml.positions import UNKNOWN

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Mapping

    from fastraml.positions import Position
    from fastraml.types.base import Parameter

__all__ = [
    'UriTemplateExpression',
    'check_uri_reference',
    'extract_uri_template_params',
    'resource_path_name',
    'simple_parameter_segment',
    'unused_uri_parameters',
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

#: Outside its template expressions, what a URI reference may not contain: a
#: character RFC 3986 § 2 does not allow, or a `%` that does not start a
#: pct-encoded octet. An expression matches first, so it is skipped whole.
_URI_REFERENCE_FAULT: Final = re.compile(r"\{[^}]*\}|%(?![0-9A-Fa-f]{2})|[^A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]")
#: A colon before any `/`, `?`, `#` or expression ends a scheme (RFC 3986 § 4.2).
_SCHEME_PREFIX: Final = re.compile(r'[^:/?#{]*:')
_SCHEME: Final = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*')


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


def check_uri_reference(uri: str, location: str, uri_pos: Position) -> None:
    """Spec § Base URI: `baseUri` MUST conform to the URI specification, or be a Template URI.

    Checks the text around the template expressions against RFC 3986, which
    obsoletes the RFC 2396 the spec cites: every character is one a URI may
    contain, every `%` starts a pct-encoded octet, and a scheme, if there is
    one, is well formed. A relative reference is accepted, as the spec's own
    `//api.test.com//common//` is one. Run after `extract_uri_template_params`,
    which has already rejected a malformed expression.
    """
    for match in _URI_REFERENCE_FAULT.finditer(uri):
        fault = match.group()
        if fault[0] == '{':
            continue
        if fault == '%':
            raise _template_error('invalid pct-encoded sequence in uri', location, uri_pos, match.start())
        raise _template_error('invalid character in uri', location, uri_pos, match.start(), character=fault)
    prefix = _SCHEME_PREFIX.match(uri)
    if prefix is not None and _SCHEME.fullmatch(uri, 0, prefix.end() - 1) is None:
        raise _template_error('invalid uri scheme', location, uri_pos, 0, scheme=uri[: prefix.end() - 1])


def unused_uri_parameters(
    declared: Mapping[str, Parameter], variables: Collection[str], uri: str, location: str
) -> Iterator[RamlError]:
    """Spec § Template URIs and URI Parameters: every declared name MUST be a variable in the URI.

    Shared by a resource's `uriParameters` and the root's `baseUriParameters`,
    which spec § Base URI gives the same structure.
    """
    for name, parameter in declared.items():
        if name not in variables:
            yield RamlError.new(
                'uri parameter is not used', location, parameter.base.key_pos, info={'parameter': name, 'uri': uri}
            )


def simple_parameter_segment(segment: str) -> bool:
    """Whether a validated path segment consists only of simple expansions.

    Reserved and fragment expansion may produce delimiters, so treating either
    as one wildcard segment would make overlap analysis claim false certainty.
    """
    expressions = extract_uri_template_params(segment, '', UNKNOWN)
    if not expressions or any(expression.operator for expression in expressions):
        return False
    return segment == ''.join(f'{{{expression.name}}}' for expression in expressions)


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
