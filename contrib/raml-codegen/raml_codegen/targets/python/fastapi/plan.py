"""The shared plan, with the one reading that is only a server's.

Everything else comes from `targets/shared/plan.py` unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..shared.plan import plan
from .annotate import make_annotator

if TYPE_CHECKING:
    from ....reader import Tree
    from ....targets import Settings
    from ..shared.plan import Model, Package

__all__ = ['plan_server']

#: The modules the generated package root already uses, so no declared type may
#: be given one of their names.
RESERVED = frozenset({'app', 'runtime', 'security'})


def plan_server(tree: Tree, settings: Settings) -> Package:
    package = plan(tree, settings, make_annotator, RESERVED)
    return replace(package, models=tuple(_tagged(model) for model in package.models))


def _tagged(model: Model) -> Model:
    """Narrow the discriminator property to the value this type states.

    `Magazine` states `discriminatorValue: monthly`, so its `kind` is not a
    `str` that happens to hold `monthly` -- it is `monthly`, and nothing else.
    Spelling it `Literal['monthly']` is reading what the document says, and it
    is what lets a union over these members be tagged.

    Only where the document states a value. RAML defaults an unstated one to the
    type name and this does not apply that default, so a type that states none
    keeps a plain `str` (docs/17 § 2).
    """
    if model.discriminator is None:
        return model
    marker, value = model.discriminator
    return replace(
        model,
        fields=tuple(
            replace(
                one,
                annotation=replace(
                    one.annotation,
                    spelling=f'Literal[{value!r}]',
                    bare='',
                    imports=one.annotation.imports | {'Literal'},
                ),
            )
            if one.wire == marker
            else one
            for one in model.fields
        ),
    )
