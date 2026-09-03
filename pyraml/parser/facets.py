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
type model and lives in `pyraml.types.base`. This module is the one part of
`parser/` that `types/` may import at runtime — see docs/02-architecture.md
section 2 and docs/03-yaml-and-io.md section 7.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final

from pyraml.datanode import parse_int
from pyraml.parser.annotations import is_annotation_key, unmarshal_domain_extension
from pyraml.parser.includes import IncludeInfo, resolve_include
from pyraml.positions import UNKNOWN
from pyraml.types.base import ScalarFacet
from pyraml.yamlnode import TAG_BOOL, TAG_FLOAT, TAG_INT, TAG_NULL, Node, NodeKind, node_error, pairs

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyraml.parser.annotations import DomainExtension
    from pyraml.registry import Raml

__all__ = [
    'compile_pattern',
    'make_bool_facet',
    'make_fraction_facet',
    'make_int_facet',
    'make_pattern_facet',
    'make_scalar_facet',
    'make_seq_facet',
    'make_string_facet',
    'resolve_annotated_scalar',
    'scalar_bool',
    'scalar_fraction',
    'scalar_int',
    'scalar_str',
]

#: The key that carries the value in the annotated-scalar form.
FACET_VALUE: Final = 'value'

#: Everything YAML 1.1 spells as true. Composition has already resolved the tag,
#: so reaching here with `!!bool` means one of these or its false counterpart.
_TRUE_SCALARS: Final = frozenset({'true', 'yes', 'on', 'y'})


def scalar_bool(node: Node, location: str) -> bool:
    """A scalar node as a boolean. Anything but `!!bool` is an error."""
    if node.kind is not NodeKind.SCALAR or node.tag != TAG_BOOL:
        raise node_error('expected a boolean value', location, node)
    return node.value.lower() in _TRUE_SCALARS


def scalar_int(node: Node, location: str) -> int:
    """A scalar node as an integer. Anything but `!!int` is an error."""
    if node.kind is not NodeKind.SCALAR or node.tag != TAG_INT:
        raise node_error('expected an integer value', location, node)
    return parse_int(node.value)


def scalar_fraction(node: Node, location: str) -> Fraction:
    """A numeric scalar as an exact `Fraction`, built from the written text.

    Never through `float`: `Fraction(1.1)` embeds the binary-float error, and
    `multipleOf: 1.1` would then reject `2.2`. Infinity and NaN are not numbers
    a bound may take.
    """
    if node.kind is not NodeKind.SCALAR or node.tag not in (TAG_INT, TAG_FLOAT):
        raise node_error('expected a number value', location, node)
    if node.tag == TAG_INT:
        return Fraction(parse_int(node.value))
    text = node.value.replace('_', '')
    if text.lstrip('+-').lower() in ('.inf', '.nan'):
        raise node_error('expected a finite number value', location, node)
    return Fraction(text)


def compile_pattern(raml: Raml, text: str, node: Node, location: str) -> re.Pattern[str]:
    """Compile a RAML pattern with the engine this parse asked for.

    `re2` is linear-time and is what untrusted input should use; it is optional,
    so a parse that asks for it without the package installed says so rather
    than quietly backtracking (docs/13-public-api.md section 2).
    """
    engine: Any = re
    if raml.regex_engine == 're2':
        try:
            import re2  # noqa: PLC0415 - optional dependency, imported on demand
        except ImportError as exc:
            raise node_error('re2 engine requested but google-re2 is not installed', location, node) from exc
        engine = re2
    try:
        compiled: re.Pattern[str] = engine.compile(text)
    except Exception as exc:
        raise node_error('invalid pattern', location, node, info={'pattern': text, 'reason': str(exc)}) from exc
    return compiled


def scalar_str(node: Node, location: str) -> str:
    """A scalar node as text. An empty (null) node reads as `''`.

    The literal text is used whatever the resolved tag: `version: 1` is the
    string `'1'`, which is what the reference implementation's YAML decoder does
    for a string destination.
    """
    if node.kind is not NodeKind.SCALAR:
        raise node_error('expected a scalar value', location, node)
    if node.tag == TAG_NULL:
        return ''
    return node.value


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
            extension = unmarshal_domain_extension(raml, location, key, value)
            extensions[extension.name] = extension
        else:
            raise node_error('unknown field in annotated scalar', location, key, info={'field': key.value})

    if value_node is None:
        raise node_error('missing value key in annotated scalar', location, node)
    return value_node, extensions


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
