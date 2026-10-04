"""Scalar facets, and the annotated-scalar form.

Spec section Annotating Scalar-valued Nodes lets *any* scalar-valued node be
written as a map with a `value` key, so that annotations can be attached:

    baseUri:
      value: http://www.example.com/api
      (redirectable): true

Because every scalar facet in the language is built by `make_scalar_facet`, that
form works at all thirty-odd nodes the spec lists without a line of per-facet
code. The same is true of `!include` at a facet position.

The builders live here rather than in `types/` because both of those features
need the parser: an include has to be read through the cache, and an annotation
has to become a `DomainExtension`. The `ScalarFacet` class itself belongs to the
type model and lives in `fastraml.types.base`. This module is the one part of
`parser/` that `types/` may import at runtime; see docs/02-architecture.md § 2
and docs/03-yaml-and-io.md § 7.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final

from fastraml.datanode import included_data_node, make_data_node, parse_int
from fastraml.facet_names import FACET_VALUE
from fastraml.parser.annotations import add_domain_extension, is_annotation_key
from fastraml.parser.includes import IncludeInfo, resolve_include
from fastraml.positions import UNKNOWN
from fastraml.types.base import ScalarFacet
from fastraml.yamlnode import (
    TAG_BOOL,
    TAG_FLOAT,
    TAG_INCLUDE,
    TAG_INT,
    TAG_NULL,
    Node,
    NodeKind,
    bool_text,
    node_error,
    pairs,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastraml.datanode import DataNode
    from fastraml.parser.annotations import DomainExtension
    from fastraml.registry import Raml

__all__ = [
    'MEDIA_RANGE',
    'MEDIA_TYPE',
    'annotated_scalar_value',
    'compile_pattern',
    'make_annotated_data_facet',
    'make_bool_facet',
    'make_fraction_facet',
    'make_int_facet',
    'make_pattern_facet',
    'make_scalar_facet',
    'make_seq_facet',
    'make_string_facet',
    'media_parts',
    'regex_engine',
    'resolve_annotated_scalar',
    'scalar_bool',
    'scalar_fraction',
    'scalar_int',
    'scalar_str',
]


#: RFC 6838 § 4.2 `restricted-name`: ASCII, a letter or digit, then up to 126
#: of those or `!#$&-^_.+`.
_RESTRICTED_NAME: Final = r'[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}'

#: RFC 9110 § 5.6.6 `parameters`, each `token "=" (token / quoted-string)`,
#: with optional whitespace around the `;` and an empty one allowed, as there.
_TOKEN: Final = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"  # noqa: S105 - an HTTP token grammar, not a credential
_QUOTED: Final = r'"(?:[\t \x21\x23-\x5b\x5d-\x7e\x80-\xff]|\\[\t \x21-\x7e\x80-\xff])*"'
_PARAMETERS: Final = rf'(?:[ \t]*;[ \t]*(?:{_TOKEN}=(?:{_TOKEN}|{_QUOTED}))?)*'

#: A media type, `type/subtype` plus parameters: what the root `mediaType`
#: takes (spec section Default Media Types names RFC 6838). Use `fullmatch`.
MEDIA_TYPE: Final = re.compile(rf'{_RESTRICTED_NAME}/{_RESTRICTED_NAME}{_PARAMETERS}')

#: A media range, RFC 9110 § 12.5.1: a media type, `type/*` or `*/*`. What
#: `fileTypes` takes; the spec requires `*/*` there (docs/10 § 2).
MEDIA_RANGE: Final = re.compile(rf'(?:\*/\*|{_RESTRICTED_NAME}/(?:\*|{_RESTRICTED_NAME})){_PARAMETERS}')

_MEDIA_HEAD: Final = re.compile(rf'\*/\*|{_RESTRICTED_NAME}/(?:\*|{_RESTRICTED_NAME})')
_PARAMETER: Final = re.compile(rf'[ \t]*;[ \t]*(?:({_TOKEN})=({_TOKEN}|{_QUOTED}))?')
_QUOTED_PAIR: Final = re.compile(r'\\(.)', re.DOTALL)


def media_parts(text: str) -> tuple[str, frozenset[tuple[str, str]]]:
    """A media type or range as `(type/subtype, parameters)`, for comparison.

    `type/subtype` and parameter names are lowercased, as RFC 9110 § 8.3.1
    makes them case-insensitive; whitespace around `;` is dropped and a
    quoted value is unquoted, so `a="b"` and `a=b` are one parameter. Text
    outside `MEDIA_RANGE` is returned lowercased, whole, with no parameters:
    P10 reports it, and a comparison before then must not fail on it.
    """
    if MEDIA_RANGE.fullmatch(text) is None:
        return text.lower(), frozenset()
    head = _MEDIA_HEAD.match(text)
    assert head is not None  # noqa: S101 - `MEDIA_RANGE` matched, and starts with this
    parameters = set()
    for found in _PARAMETER.finditer(text, head.end()):
        name, value = found.group(1), found.group(2)
        if name is None:
            continue
        if value[:1] == '"':
            value = _QUOTED_PAIR.sub(r'\1', value[1:-1])
        parameters.add((name.lower(), value))
    return head.group().lower(), frozenset(parameters)


def scalar_bool(node: Node, location: str) -> bool:
    """A scalar node as a boolean. Anything but `!!bool` is an error.

    So is an explicit `!!bool` on text outside the YAML 1.2 core schema:
    `!!bool yes` has no reading (docs/03 § 2.1).
    """
    truth = bool_text(node.value) if node.kind is NodeKind.SCALAR and node.tag == TAG_BOOL else None
    if truth is None:
        raise node_error('expected a boolean value', location, node)
    return truth


def scalar_int(node: Node, location: str) -> int:
    """A scalar node as an integer. Anything but `!!int` is an error.

    The tag can resolve where the text will not convert — `08` is tagged `!!int`
    by the YAML 1.2 table but has no octal reading — so the conversion is
    guarded rather than allowed to escape as a `ValueError`.
    """
    if node.kind is not NodeKind.SCALAR or node.tag != TAG_INT:
        raise node_error('expected an integer value', location, node)
    try:
        return parse_int(node.value)
    except ValueError as err:
        raise node_error('expected an integer value', location, node) from err


def scalar_fraction(node: Node, location: str) -> Fraction:
    """A numeric scalar as an exact `Fraction`, built from the written text.

    Never through `float`: `Fraction(1.1)` embeds the binary-float error, and
    `multipleOf: 1.1` would then reject `2.2`. Infinity and NaN are not numbers
    a bound may take.
    """
    if node.kind is not NodeKind.SCALAR or node.tag not in (TAG_INT, TAG_FLOAT):
        raise node_error('expected a number value', location, node)
    text = node.value.replace('_', '')
    if text.lstrip('+-').lower() in ('.inf', '.nan'):
        raise node_error('expected a finite number value', location, node)
    try:
        if node.tag == TAG_INT:
            return Fraction(parse_int(node.value))
        return Fraction(text)
    except (ValueError, ZeroDivisionError) as err:
        raise node_error('expected a number value', location, node) from err


def regex_engine(raml: Raml) -> Any:
    """The `re`-compatible module this parse compiles patterns with.

    Every RAML regex goes through here, so that `regex_engine='re2'` means what
    it says; `tests/unit/test_layering.py` lists the patterns that are not
    RAML's. Raises `ImportError` when `re2` was asked for and the
    package is absent; each caller turns that into a diagnostic positioned where
    it actually is, which is why this does not do it for them.

    What it cannot cover: the regexes *inside* an external JSON Schema, at
    validation time. The schema library calls `re.search` directly and offers no
    hook to replace it (docs/01 § 4.2).
    """
    if raml.regex_engine != 're2':
        return re
    import re2  # noqa: PLC0415 - optional dependency, imported on demand

    return re2


def compile_pattern(raml: Raml, text: str, node: Node, location: str) -> re.Pattern[str]:
    """Compile a RAML pattern with the engine this parse asked for.

    `re2` is linear-time and is what untrusted input should use; it is optional,
    so a parse that asks for it without the package installed says so rather
    than quietly backtracking (docs/13-public-api.md § 2).
    """
    try:
        engine = regex_engine(raml)
    except ImportError as exc:
        raise node_error('re2 engine requested but google-re2 is not installed', location, node) from exc
    try:
        compiled: re.Pattern[str] = engine.compile(text)
    except Exception as exc:
        raise node_error('invalid pattern', location, node, info={'pattern': text, 'reason': str(exc)}) from exc
    return compiled


def scalar_str(node: Node, location: str) -> str:
    """A scalar node as text. An empty (null) node reads as `''`.

    The literal text is used whatever the resolved tag: `version: 1` is the
    string `'1'`, which is what go-raml's YAML decoder does for a string
    destination.
    """
    if node.kind is not NodeKind.SCALAR:
        raise node_error('expected a scalar value', location, node)
    if node.tag == TAG_NULL:
        return ''
    return node.value


def annotated_scalar_value(node: Node) -> Node | None:
    """Find a scalar wrapper's value without decoding or registering annotations.

    A non-mapping is its own value. A mapping without `value` returns `None`;
    `resolve_annotated_scalar` owns validation of the wrapper's other fields.
    """
    if node.kind is not NodeKind.MAPPING:
        return node
    content = node.content
    for index in range(0, len(content), 2):
        if content[index].value == FACET_VALUE:
            return content[index + 1]
    return None


def resolve_annotated_scalar(raml: Raml, node: Node, location: str) -> tuple[Node, dict[str, DomainExtension]]:
    """Unwrap the annotated-scalar form, returning the value node and any annotations.

    A scalar is returned unchanged. A mapping must carry `value`, may carry
    `(annotation)` keys, and may carry nothing else.
    """
    if node.kind is NodeKind.SCALAR:
        return node, {}
    if node.kind is not NodeKind.MAPPING:
        raise node_error('expected scalar or mapping node', location, node)

    value_node: Node | None = None
    extensions: dict[str, DomainExtension] = {}
    for key, value in pairs(node):
        if key.value == FACET_VALUE:
            value_node = value
        elif is_annotation_key(key.value):
            add_domain_extension(raml, extensions, location, key, value)
        else:
            raise node_error('unknown field in annotated scalar', location, key, info={'field': key.value})

    if value_node is None:
        raise node_error('missing value key in annotated scalar', location, node)
    return value_node, extensions


def make_annotated_data_facet(raml: Raml, key: Node, node: Node, location: str) -> DataNode:
    """Read a data-valued facet's annotated scalar without interpreting object data.

    Only a scalar `value` plus annotation keys identifies this wrapper. Ordinary
    data maps, including ones with a `value` property, remain data (docs/09 § B4).
    """
    location = raml.document_location(node, location)
    if node.kind is NodeKind.SCALAR and node.tag != TAG_INCLUDE:
        return make_data_node(raml, key, node, location)
    target, resolved = resolve_include(raml, node, location)
    target = target or location
    if _is_annotated_data_scalar(resolved):
        value, _annotations = resolve_annotated_scalar(raml, resolved, target)
        data = make_data_node(raml, key, value, target)
        if node.tag == TAG_INCLUDE:
            data.include = IncludeInfo(path=node.value, abs_uri=target)
            data.value_pos = node.position
        return data
    if node.tag == TAG_INCLUDE:
        return included_data_node(raml, key, node, target, resolved)
    return make_data_node(raml, key, node, location)


def _is_annotated_data_scalar(node: Node) -> bool:
    """Distinguish an annotated scalar from an ordinary object-valued facet."""
    if node.kind is not NodeKind.MAPPING:
        return False
    value = annotated_scalar_value(node)
    if value is None or value.kind is not NodeKind.SCALAR:
        return False
    annotated = False
    for index in range(0, len(node.content), 2):
        name = node.content[index].value
        if name == FACET_VALUE:
            continue
        if not is_annotation_key(name):
            return False
        annotated = True
    return annotated


def make_scalar_facet[T](
    raml: Raml,
    key_node: Node | None,
    value_node: Node,
    location: str,
    convert: Callable[[Node, str], T],
) -> ScalarFacet[T]:
    """Build one scalar facet: resolve an include, unwrap annotations, convert.

    `key_node` is `None` for a sequence item, which has no key of its own.
    """
    location = raml.document_location(value_node, location)
    target, resolved = resolve_include(raml, value_node, location)
    resolved, extensions = resolve_annotated_scalar(raml, resolved, location)
    return ScalarFacet(
        value=convert(resolved, location),
        location=location,
        key_pos=key_node.position if key_node is not None else UNKNOWN,
        value_pos=value_node.full_position,
        include=IncludeInfo(path=value_node.value, abs_uri=target) if target else None,
        annotations=extensions,
    )


def make_string_facet(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> ScalarFacet[str]:
    """`make_scalar_facet` for the common case of a string-valued facet."""
    return make_scalar_facet(raml, key_node, value_node, location, scalar_str)


def make_bool_facet(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> ScalarFacet[bool]:
    """`make_scalar_facet` for a boolean facet: `required`, `wrapped`, `strict`."""
    return make_scalar_facet(raml, key_node, value_node, location, scalar_bool)


def make_int_facet(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> ScalarFacet[int]:
    """`make_scalar_facet` for a counting facet: `minLength`, `maxItems`, …"""
    return make_scalar_facet(raml, key_node, value_node, location, scalar_int)


def make_fraction_facet(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> ScalarFacet[Fraction]:
    """`make_scalar_facet` for a numeric bound: `minimum`, `maximum`, `multipleOf`."""
    return make_scalar_facet(raml, key_node, value_node, location, scalar_fraction)


def make_pattern_facet(
    raml: Raml, key_node: Node | None, value_node: Node, location: str
) -> ScalarFacet[re.Pattern[str]]:
    """`make_scalar_facet` for `pattern:`, compiled where it is written."""

    def convert(node: Node, loc: str) -> re.Pattern[str]:
        return compile_pattern(raml, scalar_str(node, loc), node, loc)

    return make_scalar_facet(raml, key_node, value_node, location, convert)


def make_seq_facet[T](raml: Raml, value_node: Node, location: str, convert: Callable[[Node, str], T]) -> ScalarFacet[T]:
    """A facet built from a sequence item, which has no key node."""
    return make_scalar_facet(raml, None, value_node, location, convert)
