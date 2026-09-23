"""Resource-type definitions, and applying them to a resource.

A resource type is a resource template: the same machinery as a trait, one level
up, and with three differences that all come from methods being involved.

* **Its keys are checked.** A resource type may declare `displayName`,
  `description`, `uriParameters`, `type`, `is`, `securedBy`, annotations, and
  HTTP methods — nothing else. A trait's body is not checked at all, because a
  trait *is* a method body.
* **Optional methods.** `post?` applies only if the target resource already
  declares `post`. Filtering runs **before** substitution, and the required
  variables are recollected from the filtered tree afterwards — the spec's own
  `corpResource` declares `<<TextAboutPost>>` inside a `post?`, and `/queues`,
  which has no `post`, must not be asked to supply it (docs/08 section 5.1).
* **Chaining.** A resource type may itself have a `type:`. The parent is applied
  to the compiled child first, so the closer declaration still wins.

Traits arriving through a resource type are routed into `rt_traits`, never
`traits`. That split is the only thing that keeps the four priority classes of
section 5.2 distinguishable once the merge has flattened everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fastraml.domains import DomainLocation
from fastraml.parser.annotations import is_annotation_key
from fastraml.parser.source_ir import METHODS, make_source_endpoint
from fastraml.parser.structural_merge import copy_overlay, merge_structural
from fastraml.parser.templates import (
    TemplateDefinition,
    check_parameters,
    collect_required_variables,
    compile_source_provenance,
    find_template_definition,
    make_template_definition,
    parameter_node,
)
from fastraml.parser.uritemplates import resource_path_name
from fastraml.registry import ParseCtx
from fastraml.yamlnode import TAG_STR, Node, NodeKind, node_error, pairs, with_content, with_value

if TYPE_CHECKING:
    from fastraml.parser.directives import DirectiveRef
    from fastraml.parser.source_ir import SourceEndPoint
    from fastraml.parser.structural_merge import ProvenanceOverlay
    from fastraml.registry import Raml

__all__ = [
    'ResourceTypeDefinition',
    'apply_resource_type',
    'compile_resource_type',
    'make_resource_type_definition',
    'merge_resource_type_into',
]

#: The non-method keys a resource type may declare. `usage:` is consumed by the
#: shared decoder; the rest are kept and flow into the compiled endpoint
#: (docs/08 section 5.1).
RESOURCE_TYPE_FACETS: Final = frozenset({'displayName', 'description', 'uriParameters', 'type', 'is', 'securedBy'})


@dataclass(slots=True, eq=False)
class ResourceTypeDefinition(TemplateDefinition):
    """One entry of a `resourceTypes:` map, or the whole of a ResourceType fragment.

    Its `source` has the `?` chomped off every optional method key.
    """

    #: The methods written `get?`, by their plain name.
    optional_methods: set[str] = field(default_factory=set)


def make_resource_type_definition(
    raml: Raml, key_node: Node | None, value_node: Node, location: str
) -> ResourceTypeDefinition:
    """Decode one resource-type declaration, checking the keys it may carry."""
    return make_template_definition(
        ResourceTypeDefinition, raml, key_node, value_node, location, what='resource type', retain=_retained_key
    )


def _retained_key(definition: ResourceTypeDefinition, key: Node) -> Node:
    """A facet or an annotation as written; anything else must be a method."""
    if key.value in RESOURCE_TYPE_FACETS or is_annotation_key(key.value):
        return key
    return _method_key(definition, key)


def _method_key(definition: ResourceTypeDefinition, key: Node) -> Node:
    """Validate an HTTP-method key, recording and chomping a trailing `?`.

    The key is normalised so that nothing downstream has to rename it: the
    optional ones are named in `optional_methods` instead.
    """
    name = key.value
    optional = name.endswith('?')
    if optional:
        name = name[:-1]
    if name not in METHODS:
        raise node_error(
            'resource type method must be an HTTP method', definition.location, key, info={'key': key.value}
        )
    if not optional:
        return key
    definition.optional_methods.add(name)
    return with_value(key, name)


# -- section 5.1: applying a resource type ------------------------------------


def apply_resource_type(raml: Raml, endpoint: SourceEndPoint, ref: DirectiveRef, visited: set[str]) -> None:
    """Compile the resource type `ref` names and merge it underneath `endpoint`.

    `visited` guards the chain: a resource type that reaches itself through a
    `type:` stops rather than recursing.
    """
    if ref.name in visited:
        return
    visited.add(ref.name)

    definition = _definition_for(ref)
    params: dict[str, Node] = {
        **ref.params,
        'resourcePath': parameter_node(endpoint.full_uri),
        'resourcePathName': parameter_node(resource_path_name(endpoint.full_uri)),
    }
    compiled = compile_resource_type(
        raml,
        definition,
        params,
        existing_methods=set(endpoint.operations),
        caller_scope=endpoint.scope,
        location=endpoint.location,
        uri=endpoint.uri,
        parent_uri=endpoint.full_uri[: len(endpoint.full_uri) - len(endpoint.uri)],
    )
    if compiled is None:
        return
    if compiled.resource_type is not None:
        # The chain, closest first: the parent is merged into the compiled child
        # before the child is merged into the resource.
        apply_resource_type(raml, compiled, compiled.resource_type, visited)
    merge_resource_type_into(endpoint, compiled)


def _definition_for(ref: DirectiveRef) -> ResourceTypeDefinition:
    """The resource type `ref` names, recorded on the reference for consumers.

    Resolved exactly as a trait name is: a `type:` written inside a
    ResourceType fragment resolves against that fragment's own `uses:`, which
    keeps RT-to-RT inheritance self-contained.
    """
    definition = find_template_definition(
        ref,
        lambda anchor, name: anchor.resource_type_definition(name),
        what='resource type',
        info_key='resourceType',
    )
    ref.resolved = definition
    return definition


def compile_resource_type(  # noqa: PLR0913 - the six inputs of docs/08 section 5.1
    raml: Raml,
    definition: ResourceTypeDefinition,
    params: dict[str, Node],
    *,
    existing_methods: set[str],
    caller_scope: ParseCtx | None,
    location: str,
    uri: str,
    parent_uri: str,
) -> SourceEndPoint | None:
    """The six steps of docs/08 section 5.1, in the order the spec forces."""
    if definition.link is not None:
        # An `!include`d definition compiles in the *fragment's* own namespace,
        # so type references in its body resolve against the fragment's `uses:`
        # rather than the applying document's — deviation D4, self-containment.
        return compile_resource_type(
            raml,
            definition.link,
            params,
            existing_methods=existing_methods,
            caller_scope=caller_scope,
            location=definition.link.location,
            uri=uri,
            parent_uri=parent_uri,
        )
    if definition.source is None:
        return None

    source = _filter_optional_methods(definition, definition.source, existing_methods)
    check_parameters(
        definition.declared_variables,
        params,
        definition.location,
        definition.value_pos,
        required=collect_required_variables(source, definition.variable_index),
    )

    overlay: ProvenanceOverlay = {}
    compiled = compile_source_provenance(
        source, params, definition.variable_index, caller_scope if caller_scope is not None else ParseCtx(), overlay
    )

    key = Node(NodeKind.SCALAR, TAG_STR, uri, None, compiled.line, compiled.column, compiled.line, compiled.column)
    raml.push_ctx(ParseCtx(anchor=definition.anchor, target=DomainLocation.RESOURCE_TYPE))
    try:
        endpoint = make_source_endpoint(raml, key, compiled, location, parent_uri=parent_uri)
    finally:
        raml.pop_ctx()
    _distribute_overlay(endpoint, overlay)
    return endpoint


def _filter_optional_methods(definition: ResourceTypeDefinition, source: Node, existing: set[str]) -> Node:
    """Drop the `post?` the target resource has no `post` for.

    Before substitution, and therefore before the required variables are
    recollected — which is the whole reason step 4 asks the *filtered* tree.
    """
    if not definition.optional_methods:
        return source
    kept: list[Node] = []
    for key, value in pairs(source):
        if key.value in definition.optional_methods and key.value not in existing:
            continue
        kept.append(key)
        kept.append(value)
    if len(kept) == len(source.content):
        return source
    return with_content(source, kept)


def _distribute_overlay(endpoint: SourceEndPoint, overlay: ProvenanceOverlay) -> None:
    """Copy the compile-time marks into every unit beneath `endpoint`.

    Marks key on node identity, so an entry outside a given unit's body is never
    looked up by that unit's stage-2 decode. Copying the whole overlay into each
    is therefore safe, and avoids partitioning it by subtree ownership.
    """
    if not overlay:
        return
    copy_overlay(endpoint.provenance, overlay)
    for operation in endpoint.operations.values():
        copy_overlay(operation.provenance, overlay)
    for child in endpoint.endpoints.values():
        _distribute_overlay(child, overlay)


def merge_resource_type_into(target: SourceEndPoint, source: SourceEndPoint) -> None:
    """Merge a compiled resource type underneath the resource that applied it.

    The target's own declarations win. Static resource-type content is grafted
    under the *compiled endpoint's* scope — the resource type's declaration
    namespace, or the fragment's own when it arrived through an `!include` — so
    a type name in the template resolves where it was written.
    """
    copy_overlay(target.provenance, source.provenance)
    target.body = merge_structural(target.body, source.body, source.scope, target.provenance)
    # Endpoint-level traits from the resource type become RT-resource traits.
    target.rt_traits += source.traits
    target.secured_by += source.secured_by
    if target.resource_type is None:
        target.resource_type = source.resource_type

    for method, operation in source.operations.items():
        existing = target.operations.get(method)
        if existing is None:
            # A method contributed entirely by the resource type has no
            # "method-own" declarations at this site, so all of its traits are
            # RT-method traits. It keeps the scope it was compiled under.
            operation.rt_traits += operation.traits
            operation.traits = []
            target.operations[method] = operation
            continue
        existing.rt_traits += operation.traits
        copy_overlay(existing.provenance, operation.provenance)
        existing.body = merge_structural(existing.body, operation.body, operation.scope, existing.provenance)
        if not existing.explicit_secured_by and operation.explicit_secured_by:
            existing.secured_by = operation.secured_by
            existing.explicit_secured_by = True
