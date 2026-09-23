"""Trait definitions, and applying them to an operation.

A trait is an operation template. Its body is captured as YAML at declaration
time and scanned once for `<<variables>>`; applying it means substituting the
parameters and merging the result *underneath* the operation's own body, which
therefore wins wherever the two disagree (docs/08 § 3.2).

The reference to a trait (an `is:` entry) is a `DirectiveRef` in
`directives.py`, because stage 1 decodes all three directive kinds
(docs/08 § 2.1).

Two things here are easy to get subtly wrong:

* **The four priority classes.** A method's own traits beat the resource's,
  which beat the resource type's method-level ones, which beat its
  resource-level ones. They are deduplicated by *name*, closest occurrence
  winning, and each surviving trait is applied exactly once.
* **Which namespace the merged tree resolves in.** Static trait content resolves
  in the trait's declaration scope; a value the caller supplied resolves in the
  caller's. Both marks land in the operation's provenance overlay, and the
  set-if-absent rule keeps the more specific one (docs/08 § 4.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING

from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, RamlError
from fastraml.parser.structural_merge import merge_structural
from fastraml.parser.templates import (
    TemplateDefinition,
    check_parameters,
    compile_source_provenance,
    find_template_definition,
    make_template_definition,
    parameter_node,
)
from fastraml.parser.uritemplates import resource_path_name
from fastraml.registry import ParseCtx

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.parser.directives import DirectiveRef
    from fastraml.parser.source_ir import SourceEndPoint, SourceOperation
    from fastraml.registry import Raml
    from fastraml.yamlnode import Node

__all__ = [
    'TraitDefinition',
    'apply_traits',
    'make_trait_definition',
    'merge_trait_into',
]


@dataclass(slots=True, eq=False)
class TraitDefinition(TemplateDefinition):
    """One entry of a `traits:` map, or the whole of a Trait fragment.

    A trait's body is not checked at all: a trait *is* a method body.
    """


def make_trait_definition(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> TraitDefinition:
    """Decode one trait declaration. Everything but `usage:` is kept as YAML."""
    return make_template_definition(TraitDefinition, raml, key_node, value_node, location, what='trait')


# -- applying traits (docs/08 § 3.2) ------------------------------------------


def apply_traits(endpoint: SourceEndPoint) -> None:
    """Apply every trait that reaches each of `endpoint`'s operations.

    No registry parameter: every name resolves through its own reference's
    scope, and every merge target is reachable from `endpoint`. Errors
    accumulate — one unresolvable trait must not discard the rest of the
    resource.
    """
    # `resourcePath` and `resourcePathName` are constant across every operation
    # and every trait of this resource, so their nodes are built once. They are
    # read-only scalars that substitution never inserts by pointer, so sharing
    # them is safe and saves an allocation per application.
    path = parameter_node(endpoint.full_uri)
    path_name = parameter_node(resource_path_name(endpoint.full_uri))

    accumulator = Accumulator()
    for method, operation in endpoint.operations.items():
        method_name = parameter_node(method)
        seen: set[str] = set()
        for ref in _in_priority_order(endpoint, operation):
            if ref.name in seen:
                # Spec section Effect on Collections: "priority is given to the
                # trait in closest proximity to the target method or resource",
                # and the trait is applied exactly once.
                continue
            seen.add(ref.name)
            params = {
                **ref.params,
                'resourcePath': path,
                'resourcePathName': path_name,
                'methodName': method_name,
            }
            try:
                definition = _definition_for(ref)
                merge_trait_into(operation, definition, params, caller_scope=endpoint.scope)
            except RamlError as err:
                accumulator.add(
                    RamlError.wrap('apply trait', err, ref.location, ref.value_pos, info={'trait': ref.name})
                )
    accumulator.raise_if_any()


def _in_priority_order(endpoint: SourceEndPoint, operation: SourceOperation) -> Iterator[DirectiveRef]:
    """The four classes of docs/08 § 3.2, closest first.

    The `traits` / `rt_traits` split on the IR exists solely to keep these
    distinguishable after the resource-type merge has flattened everything else.
    """
    return chain(
        operation.traits,  # 1. the method's own
        endpoint.traits,  # 2. the resource's own
        operation.rt_traits,  # 3. the resource type's method-level
        endpoint.rt_traits,  # 4. the resource type's resource-level
    )


def _definition_for(ref: DirectiveRef) -> TraitDefinition:
    """The trait `ref` names, recorded on the reference for consumers."""
    definition = find_template_definition(
        ref, lambda anchor, name: anchor.trait_definition(name), what='trait', info_key='trait'
    )
    ref.resolved = definition
    return definition


def merge_trait_into(
    operation: SourceOperation,
    definition: TraitDefinition,
    params: dict[str, Node],
    *,
    caller_scope: ParseCtx | None,
) -> None:
    """Substitute `params` into the trait body and merge it under the operation."""
    definition = definition.resolved()
    if definition.source is None:
        return
    check_parameters(definition.declared_variables, params, definition.location, definition.value_pos)

    compiled = compile_source_provenance(
        definition.source,
        params,
        definition.variable_index,
        caller_scope if caller_scope is not None else ParseCtx(),
        operation.provenance,
    )
    trait_scope = ParseCtx(anchor=definition.anchor, target=DomainLocation.TRAIT)
    operation.body = merge_structural(operation.body, compiled, trait_scope, operation.provenance)
