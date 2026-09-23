"""Trait definitions, and applying them to an operation.

A trait is an operation template. Its body is captured as YAML at declaration
time and scanned once for `<<variables>>`; applying it means substituting the
parameters and merging the result *underneath* the operation's own body, which
therefore wins wherever the two disagree (docs/08 section 5.2).

The reference to a trait — the `is:` entry — is a `DirectiveRef` and lives in
`directives.py`, because stage 1 decodes all three directive kinds before this
module exists (docs/02 section 3).

Two things here are easy to get subtly wrong:

* **The four priority classes.** A method's own traits beat the resource's,
  which beat the resource type's method-level ones, which beat its
  resource-level ones. They are deduplicated by *name*, closest occurrence
  winning, and each surviving trait is applied exactly once.
* **Which namespace the merged tree resolves in.** Static trait content resolves
  in the trait's declaration scope; a value the caller supplied resolves in the
  caller's. Both marks land in the operation's provenance overlay, and the
  set-if-absent rule keeps the more specific one (docs/08 section 6.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING

from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, RamlError
from fastraml.parser.facets import make_string_facet
from fastraml.parser.includes import note_include_ref
from fastraml.parser.structural_merge import merge_structural
from fastraml.parser.templates import (
    RESERVED_PARAMETERS,
    collect_variables_index,
    compile_source_provenance,
    parameter_node,
)
from fastraml.parser.uritemplates import resource_path_name
from fastraml.positions import UNKNOWN, Position
from fastraml.registry import ParseCtx
from fastraml.yamlnode import TAG_INCLUDE, Node, NodeKind, is_null, node_error, pairs, with_content

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.parser.directives import DirectiveRef
    from fastraml.parser.fragments import ReferenceResolver
    from fastraml.parser.source_ir import SourceEndPoint, SourceOperation
    from fastraml.parser.templates import VariableIndex
    from fastraml.registry import Raml
    from fastraml.types.base import ScalarFacet

__all__ = [
    'TraitDefinition',
    'apply_traits',
    'check_parameters',
    'make_trait_definition',
    'merge_trait_into',
]

FACET_USAGE = 'usage'


@dataclass(slots=True, eq=False)
class TraitDefinition:
    """One entry of a `traits:` map, or the whole of a Trait fragment."""

    id: int
    name: str
    location: str
    usage: ScalarFacet[str] | None = None
    #: The body as written, minus `usage:`. `None` for an empty or linked trait.
    source: Node | None = None
    declared_variables: set[str] = field(default_factory=set)
    variable_index: VariableIndex = field(default_factory=dict)
    #: `traits: {paged: !include ...}` — the target's own definition, resolved by
    #: `fragments.py`, which owns fragment parsing (docs/02 section 3).
    link: TraitDefinition | None = None
    link_uri: str | None = None
    #: The namespace the *body* resolves its type names in: this trait's
    #: declaration site, never the site it is applied at.
    anchor: ReferenceResolver | None = None
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'TraitDefinition({self.name!r})'

    def resolved(self) -> TraitDefinition:
        """Itself, or the definition an `!include` pointed at."""
        return self if self.link is None else self.link


def make_trait_definition(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> TraitDefinition:
    """Decode one trait declaration. Everything but `usage:` is kept as YAML."""
    definition = TraitDefinition(
        id=raml.next_id(),
        name=key_node.value if key_node is not None else '',
        location=location,
        anchor=raml.current_ctx().anchor,
        key_pos=(key_node if key_node is not None else value_node).position,
        value_pos=value_node.full_position,
    )
    if is_null(value_node):
        return definition
    if value_node.tag == TAG_INCLUDE:
        definition.link_uri = note_include_ref(raml, value_node, location)
        return definition
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('trait definition must be a mapping', location, value_node)

    kept: list[Node] = []
    for key, value in pairs(value_node):
        if key.value == FACET_USAGE:
            definition.usage = make_string_facet(raml, key, value, location)
        else:
            kept.append(key)
            kept.append(value)
    definition.source = _body(value_node, kept)
    if definition.source is not None:
        definition.declared_variables, definition.variable_index = collect_variables_index(definition.source, location)
    return definition


def _body(model: Node, content: list[Node]) -> Node | None:
    """The retained keys as a fresh mapping, or `None` when nothing was kept.

    Fresh, so that a merge into it cannot reach the declaring document — the
    same reason stage 1 rebuilds an endpoint's body (`source_ir.py`).
    """
    return with_content(model, content) if content else None


# -- section 5.2: applying traits ---------------------------------------------


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
    """The four classes of docs/08 section 5.2, closest first.

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
    """Resolve a trait name in the namespace of the document that wrote it.

    Lexical, with no application-site fallback: a name written inside a fragment
    resolves against that fragment's own `traits:` and `uses:` only, which is
    what keeps a typed fragment self-contained (docs/04 section 4).
    """
    anchor = ref.scope.anchor if ref.scope is not None else None
    if anchor is None:
        raise RamlError.new('no scope to resolve a trait name in', ref.location, ref.value_pos)
    try:
        definition = anchor.trait_definition(ref.name)
    except LookupError as err:
        raise RamlError.wrap('get trait definition', err, ref.location, ref.value_pos) from err
    if definition is None:
        raise RamlError.new('trait not found', ref.location, ref.value_pos, info={'trait': ref.name})
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


def check_parameters(
    declared: set[str],
    params: dict[str, Node],
    location: str,
    position: Position,
    *,
    required: set[str] | None = None,
) -> None:
    """Both directions: nothing supplied undeclared, nothing required unsupplied.

    The two sets differ for a resource type. Everything the template mentions is
    accepted as a parameter, but only what survives optional-method filtering is
    *required* — otherwise `/queues` would have to supply the `<<TextAboutPost>>`
    that only appears inside the `post?` it does not have (docs/08 section 5.1).
    For a trait the two coincide.

    Reserved parameters are always accepted and never required: the parser
    injects them at every application site.
    """
    accumulator = Accumulator()
    for name in params:
        if name not in RESERVED_PARAMETERS and name not in declared:
            accumulator.add(RamlError.new('unexpected parameter', location, position, info={'parameter': name}))
    for name in declared if required is None else required:
        if name not in RESERVED_PARAMETERS and name not in params:
            accumulator.add(RamlError.new('missing required parameter', location, position, info={'parameter': name}))
    accumulator.raise_if_any()
