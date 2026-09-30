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
from typing import TYPE_CHECKING, ClassVar

from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, RamlError
from fastraml.facet_names import FACET_IS, FACET_SECURED_BY
from fastraml.parser.directives import decode_secured_by, decode_trait_refs
from fastraml.parser.source_ir import note_failure
from fastraml.parser.structural_merge import merge_structural
from fastraml.parser.templates import (
    TRAIT_PARAMETERS,
    TemplateDefinition,
    check_parameters,
    compile_source_provenance,
    find_template_definition,
    make_template_definition,
    parameter_node,
)
from fastraml.parser.uritemplates import resource_path_name
from fastraml.registry import ParseCtx
from fastraml.yamlnode import pairs, with_content

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

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

    reserved: ClassVar[frozenset[str]] = TRAIT_PARAMETERS


def make_trait_definition(
    raml: Raml, key_node: Node | None, value_node: Node, location: str, *, attach: Callable[[TraitDefinition], None]
) -> TraitDefinition:
    """Decode one trait declaration. Everything but `usage:` is kept as YAML."""
    return make_template_definition(TraitDefinition, raml, key_node, value_node, location, what='trait', attach=attach)


# -- applying traits (docs/08 § 3.2) ------------------------------------------


def apply_traits(raml: Raml, endpoint: SourceEndPoint) -> None:
    """Apply every trait that reaches each of `endpoint`'s operations.

    Every name resolves through its own reference's scope, and every merge
    target is reachable from `endpoint`; `raml` only records where each
    substituted value came from (docs/08 § 5.1). Errors accumulate — one
    unresolvable trait must not discard the rest of the resource.
    """
    # `resourcePath` and `resourcePathName` are constant across every operation
    # and every trait of this resource, so their nodes are built once. They are
    # read-only scalars that substitution never inserts by pointer, so sharing
    # them is safe and saves an allocation per application.
    path = parameter_node(endpoint.full_uri)
    path_name = parameter_node(resource_path_name(endpoint.full_uri))

    accumulator = Accumulator()
    looked_up: set[DirectiveRef] = set()
    for method, operation in endpoint.operations.items():
        method_name = parameter_node(method)
        seen: set[str] = set()
        # Spec section Algorithm of Merging Traits and Methods: one distance at
        # a time, the traits the previous distance's traits apply coming next.
        queue = list(_in_priority_order(endpoint, operation))
        while queue:
            nested: list[DirectiveRef] = []
            for ref in queue:
                if ref.name in seen:
                    # Spec section Effect on Collections: "priority is given to the
                    # trait in closest proximity to the target method or resource",
                    # and the trait is applied exactly once. This also ends a cycle.
                    continue
                seen.add(ref.name)
                looked_up.add(ref)
                params = {
                    **ref.params,
                    'resourcePath': path,
                    'resourcePathName': path_name,
                    'methodName': method_name,
                }
                try:
                    definition = _definition_for(ref)
                    nested += merge_trait_into(
                        operation,
                        definition,
                        params,
                        caller_scope=endpoint.scope,
                        application=ref,
                        raml=raml,
                    )
                except RamlError as err:
                    wrapped = _wrap(ref, err)
                    # The operation it was merging into lacks its contribution.
                    note_failure(operation, wrapped)
                    accumulator.add(wrapped)
            operation.nested_traits += nested
            queue = nested

    # A reference the name rule skipped, or one on a resource with no methods,
    # is applied nowhere but still names a trait: bind it, so a consumer can
    # follow it, and report a name that matches nothing (docs/08 § 3.2). One
    # written on an operation, or on a trait applied to it, is noted there; the
    # caller notes the resource.
    for operation in endpoint.operations.values():
        _bind_unapplied(
            chain(operation.traits, operation.rt_traits, operation.nested_traits), looked_up, accumulator, operation
        )
    _bind_unapplied(chain(endpoint.traits, endpoint.rt_traits), looked_up, accumulator, None)
    accumulator.raise_if_any()


def _bind_unapplied(
    refs: Iterable[DirectiveRef], looked_up: set[DirectiveRef], acc: Accumulator, operation: SourceOperation | None
) -> None:
    for ref in refs:
        if ref in looked_up:
            continue
        # Once: a resource's reference is reached from each of its operations.
        looked_up.add(ref)
        try:
            _definition_for(ref)
        except RamlError as err:
            wrapped = _wrap(ref, err)
            if operation is not None:
                note_failure(operation, wrapped)
            acc.add(wrapped)


def _wrap(ref: DirectiveRef, err: RamlError) -> RamlError:
    return RamlError.wrap('apply trait', err, ref.location, ref.value_pos, info={'trait': ref.name})


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


def merge_trait_into(  # noqa: PLR0913 - the application, and where its values are recorded
    operation: SourceOperation,
    definition: TraitDefinition,
    params: dict[str, Node],
    *,
    caller_scope: ParseCtx | None,
    application: DirectiveRef,
    raml: Raml,
) -> list[DirectiveRef]:
    """Substitute `params`, written at `application`, into the trait body and
    merge it under the operation.

    Returns the traits the body's own `is:` applies, for the next distance.
    """
    definition = definition.resolved()
    if definition.source is None:
        return []
    check_parameters(definition, params, application)

    compiled = compile_source_provenance(
        definition.source,
        params,
        definition.variable_index,
        caller_scope if caller_scope is not None else ParseCtx(),
        operation.provenance,
        written_in=application.location,
        substitutions=raml.substitutions,
        reserved=definition.reserved,
        param_scopes=application.param_scopes,
    )
    trait_scope = ParseCtx(anchor=definition.anchor, target=DomainLocation.TRAIT)
    with raml.active_overlay(operation.provenance):
        body, nested = _take_directives(raml, operation, compiled, definition.location, trait_scope)
    operation.body = merge_structural(operation.body, body, trait_scope, operation.provenance)
    return nested


def _take_directives(
    raml: Raml, operation: SourceOperation, compiled: Node, location: str, scope: ParseCtx
) -> tuple[Node | None, list[DirectiveRef]]:
    """Decode the directives a trait body holds, as stage 1 does a method's.

    The rest of the body is returned for the merge, with the traits its `is:`
    names, which resolve in the trait's namespace (docs/08 § 3.2).

    `securedBy:` is taken only by an operation with none of its own: the
    method's is explicit, and wins as any of its nodes wins over a trait's.
    Traits are applied closest first, so the closest trait's is the one taken.
    Each name follows its own authored or substituted provenance (docs/09 § A6).
    """
    kept: list[Node] = []
    nested: list[DirectiveRef] = []
    for key, value in pairs(compiled):
        if key.value == FACET_IS:
            nested = decode_trait_refs(raml, value, location, scope)
        elif key.value == FACET_SECURED_BY:
            refs = decode_secured_by(raml, value, location, scope)
            if not operation.explicit_secured_by:
                operation.secured_by = refs
                operation.explicit_secured_by = True
        else:
            kept.append(key)
            kept.append(value)
    if len(kept) == len(compiled.content):
        return compiled, nested
    return (with_content(compiled, kept) if kept else None), nested
