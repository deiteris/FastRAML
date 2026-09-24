"""What an input's traits and resource types contribute (docs/20 § 5.3, § 6.4).

Walks an input's authored resource tree, resolves every `type:` and `is:` it
applies through the parsed model, and follows the templates those apply in
turn. The questions asked of the result are structural: does a template set a
key, write a body with no media type, contribute a method, or read
`<<resourcePath>>`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fastraml.errors import RamlError
from fastraml.parser.annotations import is_annotation_key
from fastraml.parser.directives import decode_trait_refs, decode_type_ref
from fastraml.parser.resourcetypes import ResourceTypeDefinition
from fastraml.parser.source_ir import METHODS
from fastraml.yamlnode import Node, NodeKind, pairs

if TYPE_CHECKING:
    from fastraml.parser.fragments import ReferenceResolver
    from fastraml.parser.templates import TemplateDefinition

__all__ = ['Application', 'Applied', 'body_without_media_type', 'is_media_type_map', 'template_applications']

#: Values that hold user data, not RAML: a key inside them is not a facet.
_DATA_KEYS: Final = frozenset({'example', 'examples', 'default', 'enum'})


@dataclass(slots=True, eq=False)
class Applied:
    """One template reached from an input, directly or through another."""

    definition: TemplateDefinition
    #: The name as the input or the applying template wrote it.
    name: str


@dataclass(slots=True, eq=False)
class Application:
    """What one authored resource applies."""

    #: The resource's authored node.
    resource: Node
    #: Every template reached from the resource or its methods.
    applied: list[Applied] = field(default_factory=list)
    #: Methods the resource's resource-type chain adds that it does not write.
    contributed_methods: set[str] = field(default_factory=set)
    #: A name holding a template parameter, which cannot be resolved here.
    unresolved: list[str] = field(default_factory=list)


def is_media_type_map(node: Node) -> bool:
    """`body:` keyed by media types; the slash test of docs/08 § 6.3."""
    content = node.content
    if node.kind is not NodeKind.MAPPING or not content:
        return False
    return all('/' in content[index].value for index in range(0, len(content), 2))


def body_without_media_type(method: Node) -> bool:
    """Whether a method-shaped mapping has a request or response body with no media type."""
    if method.kind is not NodeKind.MAPPING:
        return False
    for key, value in pairs(method):
        if key.value == 'body' and not is_media_type_map(value):
            return True
        if key.value == 'responses' and value.kind is NodeKind.MAPPING:
            for _code, response in pairs(value):
                if response.kind is not NodeKind.MAPPING:
                    continue
                for response_key, body in pairs(response):
                    if response_key.value == 'body' and not is_media_type_map(body):
                        return True
    return False


def sets_key(node: Node, name: str) -> bool:
    """Whether `name` is a key anywhere in `node` outside user data."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.kind is NodeKind.SEQUENCE:
            stack.extend(current.content)
            continue
        if current.kind is not NodeKind.MAPPING:
            continue
        for key, value in pairs(current):
            if key.value == name:
                return True
            if key.value not in _DATA_KEYS and not is_annotation_key(key.value):
                stack.append(value)
    return False


def _method_nodes(definition: TemplateDefinition) -> list[Node]:
    """The method bodies a template holds: itself for a trait, its methods for a resource type."""
    source = definition.source
    if source is None or source.kind is not NodeKind.MAPPING:
        return []
    if not isinstance(definition, ResourceTypeDefinition):
        return [source]
    return [value for key, value in pairs(source) if key.value in METHODS]


def template_bodies_without_media_type(definition: TemplateDefinition) -> bool:
    return any(body_without_media_type(method) for method in _method_nodes(definition))


def reads_resource_path(definition: TemplateDefinition) -> bool:
    return any(info.name == 'resourcePath' for infos in definition.variable_index.values() for info in infos)


class _Walker:
    __slots__ = ('_location',)

    def __init__(self, location: str) -> None:
        self._location = location

    def _resolve(
        self, anchor: ReferenceResolver, node: Node, *, resource_type: bool, found: Application, seen: set[int]
    ) -> list[TemplateDefinition]:
        refs = [decode_type_ref(node, self._location)] if resource_type else decode_trait_refs(node, self._location)
        out: list[TemplateDefinition] = []
        for ref in refs:
            if '<<' in ref.name:
                found.unresolved.append(ref.name)
                continue
            try:
                definition: TemplateDefinition = (
                    anchor.resource_type_definition(ref.name) if resource_type else anchor.trait_definition(ref.name)
                )
            except RamlError:
                found.unresolved.append(ref.name)
                continue
            definition = definition.resolved()
            if definition.id in seen:
                continue
            seen.add(definition.id)
            found.applied.append(Applied(definition, ref.name))
            out.append(definition)
        return out

    def applications(self, anchor: ReferenceResolver, resource: Node) -> Application:
        """Every template `resource` reaches, and the methods its type chain adds."""
        found = Application(resource)
        if resource.kind is not NodeKind.MAPPING:
            return found
        seen: set[int] = set()
        authored = {key.value for key, _ in pairs(resource) if key.value in METHODS}
        pending = _directives(anchor, resource)
        while pending:
            scope, node, is_type = pending.pop()
            for definition in self._resolve(scope, node, resource_type=is_type, found=found, seen=seen):
                source = definition.source
                if source is None or source.kind is not NodeKind.MAPPING:
                    continue
                inner = definition.anchor or scope
                if isinstance(definition, ResourceTypeDefinition):
                    found.contributed_methods.update(
                        key.value
                        for key, _ in pairs(source)
                        if key.value in METHODS
                        and key.value not in definition.optional_methods
                        and key.value not in authored
                    )
                    pending += _directives(inner, source)
                else:
                    pending += [(inner, value, False) for key, value in pairs(source) if key.value == 'is']
        return found


def _directives(scope: ReferenceResolver, resource: Node) -> list[tuple[ReferenceResolver, Node, bool]]:
    """A resource-shaped mapping's `type:`, its `is:`, and each of its methods' `is:`."""
    found: list[tuple[ReferenceResolver, Node, bool]] = []
    for key, value in pairs(resource):
        if key.value in {'type', 'is'}:
            found.append((scope, value, key.value == 'type'))
        elif key.value in METHODS and value.kind is NodeKind.MAPPING:
            found += [(scope, item, False) for name, item in pairs(value) if name.value == 'is']
    return found


def template_applications(anchor: ReferenceResolver, location: str, resources: list[Node]) -> list[Application]:
    """One `Application` per authored resource node in `resources`."""
    walker = _Walker(location)
    return [walker.applications(anchor, resource) for resource in resources]
