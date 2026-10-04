"""Export a projected JSON Schema as a RAML DataType or Library (docs/16 § 8)."""

from __future__ import annotations

import re
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final

import yaml

from fastraml.types.base import BUILTIN_TYPES
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.examples import examples_of
from fastraml.types.shape import is_pattern_key
from fastraml.types.values import decimal_digits, decimal_text
from fastraml.uris import uri_stem

if TYPE_CHECKING:
    from fastraml.types.base import BaseShape
    from fastraml.types.jsonschema_ import JsonShape

__all__ = ['to_raml']

_UNSAFE_NAME: Final = re.compile(r'[^A-Za-z0-9_]')


class _Dumper(yaml.SafeDumper):
    """Write exact decimal facets as YAML numbers, never as Python objects."""


def _fraction(dumper: _Dumper, value: Fraction) -> yaml.ScalarNode:
    if decimal_digits(value) is None:
        raise ValueError(f'cannot export nonterminating decimal: {value}')
    tag = 'tag:yaml.org,2002:int' if value.denominator == 1 else 'tag:yaml.org,2002:float'
    return dumper.represent_scalar(tag, decimal_text(value))


_Dumper.add_representer(Fraction, _fraction)


class _Conversion:
    __slots__ = ('names', 'open')

    def __init__(self, names: dict[BaseShape, str]) -> None:
        self.names = names
        self.open: set[BaseShape] = set()

    def declaration(self, base: BaseShape, *, definition: bool = False) -> dict[str, Any]:
        if not definition and base in self.names:
            return {'type': self.names[base]}
        shape = base.shape
        if isinstance(shape, RecursiveShape):
            if shape.head not in self.names:
                raise ValueError('an anonymous recursive schema cannot be expressed as a RAML type')
            return {'type': self.names[shape.head]}
        if base in self.open:
            raise ValueError('an anonymous recursive schema cannot be expressed as a RAML type')
        self.open.add(base)
        try:
            return self._body(base)
        finally:
            self.open.remove(base)

    def _body(self, base: BaseShape) -> dict[str, Any]:
        shape = base.shape
        node: dict[str, Any] = {'type': base.type}
        self._common(base, node)
        if isinstance(shape, ObjectShape):
            self._object(shape, node)
        elif isinstance(shape, ArrayShape):
            self._array(shape, node)
        elif isinstance(shape, UnionShape):
            self._union(shape, node)
        elif shape is not None:
            _facets(node, shape, 'min_length', 'max_length', 'minimum', 'maximum', 'multiple_of')
            if (pattern := getattr(shape, 'pattern', None)) is not None:
                node['pattern'] = pattern.value.pattern
        return node

    @staticmethod
    def _common(base: BaseShape, node: dict[str, Any]) -> None:
        if base.display_name is not None:
            node['displayName'] = base.display_name.value
        if base.description is not None:
            node['description'] = base.description.value
        if base.default is not None:
            node['default'] = base.default.raw
        examples = [example.data.raw for example in examples_of(base) if example.data is not None]
        if examples:
            node['examples'] = {str(index): value for index, value in enumerate(examples)}
        if base.enum:
            node['enum'] = [member.raw for member in base.enum]

    def _object(self, shape: ObjectShape, node: dict[str, Any]) -> None:
        if (
            shape.pattern_properties
            and shape.additional_properties is not None
            and not shape.additional_properties.value
        ):
            raise ValueError('patternProperties with additionalProperties: false has no valid RAML representation')
        _facets(node, shape, 'min_properties', 'max_properties', 'additional_properties')
        properties: dict[str, Any] = {}
        for name, prop in (shape.properties or {}).items():
            candidate = name.removesuffix('?')
            if is_pattern_key(candidate):
                raise ValueError(f'literal JSON Schema property name {name!r} has no RAML spelling')
            declaration = self.declaration(prop.base)
            if name.endswith('?') or not prop.required:
                declaration['required'] = prop.required
            properties[name] = declaration
        for name, pattern_prop in (shape.pattern_properties or {}).items():
            properties[name] = self.declaration(pattern_prop.base)
        if properties:
            node['properties'] = properties

    def _array(self, shape: ArrayShape, node: dict[str, Any]) -> None:
        _facets(node, shape, 'min_items', 'max_items', 'unique_items')
        if shape.items is not None:
            node['items'] = self.declaration(shape.items)

    def _union(self, shape: UnionShape, node: dict[str, Any]) -> None:
        parts = []
        for member in shape.any_of or ():
            if member in self.names:
                parts.append(self.names[member])
            else:
                declaration = self.declaration(member)
                if set(declaration) != {'type'}:
                    raise ValueError('a constrained anonymous union member cannot be expressed as a RAML type')
                parts.append(declaration['type'])
        node['type'] = ' | '.join(parts)


def _facets(node: dict[str, Any], shape: object, *names: str) -> None:
    for name in names:
        facet = getattr(shape, name, None)
        if facet is not None:
            head, *tail = name.split('_')
            node[head + ''.join(word.title() for word in tail)] = facet.value


def _name_for(name: str, taken: set[str]) -> str:
    """A stable RAML type expression for a JSON Pointer key or file stem."""
    stem = _UNSAFE_NAME.sub('_', name) or 'Type'
    if stem[0].isdigit():
        stem = '_' + stem
    candidate, suffix = stem, 2
    while candidate in taken:
        candidate = f'{stem}{suffix}'
        suffix += 1
    taken.add(candidate)
    return candidate


def _references_root(root: BaseShape) -> bool:
    """A recursive root needs a Library name even without other definitions."""
    pending = [root]
    seen: set[BaseShape] = set()
    while pending:
        base = pending.pop()
        if base in seen:
            continue
        seen.add(base)
        shape = base.shape
        if isinstance(shape, RecursiveShape):
            if shape.head is root:
                return True
            pending.append(shape.head)
        elif isinstance(shape, ObjectShape):
            pending.extend(prop.base for prop in (shape.properties or {}).values())
            pending.extend(prop.base for prop in (shape.pattern_properties or {}).values())
        elif isinstance(shape, ArrayShape) and shape.items is not None:
            pending.append(shape.items)
        elif isinstance(shape, UnionShape):
            pending.extend(shape.any_of or ())
    return False


def to_raml(shape: JsonShape, *, name: str | None = None) -> str:
    """Return a self-contained RAML type document for a parsed JSON Schema.

    A schema with named definitions, recursion, or nested external references
    becomes a Library; otherwise it becomes a DataType. `name` overrides the
    schema file's stem for the root type (and is required for an inline schema
    needing a Library).

    Raises the schema's projection error (`JsonShape.projection_error`) where it
    has none: the projection is this export's whole content, so unlike the
    other views it has no opaque form to fall back to (docs/16 § 8).
    """
    root = shape.as_shape()
    if root is None:
        failure = shape.projection_error()
        if failure is not None:
            raise failure
        raise ValueError('JSON Schema has no compiled projection')
    defs = shape.as_shape_definitions()
    root_name = name or (uri_stem(shape.document_uri) if shape.canonical_uri and shape.document_uri else None)
    if root_name is not None and root_name in defs:
        raise ValueError(f'root type name conflicts with a definition: {root_name}')
    taken = set(BUILTIN_TYPES)
    root_name = _name_for(root_name, taken) if root_name is not None else None
    names: dict[BaseShape, str] = {}
    keys: dict[str, str] = {}
    for key, base in defs.items():
        keys[key] = _name_for(key, taken)
        names.setdefault(base, keys[key])
    root_alias = names.get(root)
    if defs or _references_root(root):
        if root_name is None:
            raise ValueError('a Library export needs a root type name')
        if root_alias is None:
            names[root] = root_name
        conversion = _Conversion(names)
        types = {
            keys[key]: conversion.declaration(base, definition=True)
            if names[base] == keys[key]
            else {'type': names[base]}
            for key, base in defs.items()
        }
        types[root_name] = {'type': root_alias} if root_alias else conversion.declaration(root, definition=True)
        document = {'types': types}
        header = '#%RAML 1.0 Library\n'
    else:
        document = _Conversion(names).declaration(root, definition=True)
        header = '#%RAML 1.0 DataType\n'
    return header + yaml.dump(document, Dumper=_Dumper, sort_keys=False, allow_unicode=True)
