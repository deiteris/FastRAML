"""The built-in lint rules and their named rulesets."""

from __future__ import annotations

from fastraml.views.lint.engine import Registry, Rule
from fastraml.views.lint.rules.document import UnusedTrait, UnusedType
from fastraml.views.lint.rules.operations import GetWithBody, UnsecuredOperation
from fastraml.views.lint.rules.schema import (
    DeprecatedSchemas,
    DiscriminatorWithoutSubtypes,
    JsonRefSiblings,
    MultipleInheritance,
    OptionalAndNil,
    UnboundedString,
    UntypedPayload,
)

__all__ = ['builtin_registry']


def builtin_registry() -> Registry:
    registry = Registry()
    spec_rules: tuple[Rule, ...] = (
        DeprecatedSchemas(),
        DiscriminatorWithoutSubtypes(),
        GetWithBody(),
        JsonRefSiblings(),
        MultipleInheritance(),
        OptionalAndNil(),
        UntypedPayload(),
        UnusedTrait(),
        UnusedType(),
    )
    for rule in spec_rules:
        registry.add(rule, sets=('spec', 'recommended'))
    security_rules: tuple[Rule, ...] = (UnboundedString(), UnsecuredOperation())
    for rule in security_rules:
        registry.add(rule, sets=('security',))
    return registry
