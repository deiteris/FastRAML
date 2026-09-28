"""The shared plan, with the one reading that is only a server's.

Everything else comes from `targets/shared/plan.py` unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..shared.plan import Reserved, plan
from .annotate import make_annotator

if TYPE_CHECKING:
    from ....reader import Tree
    from ....targets import Settings
    from ..shared.plan import Model, Package

__all__ = ['plan_server']

#: `BaseModel`'s public names in pydantic 2. A field named after a method is a
#: model that fails to build (`model_dump`, `model_config`); one named after the
#: rest shadows it, which pydantic warns about and mypy rejects as an override.
ATTRIBUTES = frozenset(
    {
        'construct',
        'copy',
        'dict',
        'from_orm',
        'json',
        'model_computed_fields',
        'model_config',
        'model_construct',
        'model_copy',
        'model_dump',
        'model_dump_json',
        'model_extra',
        'model_fields',
        'model_fields_set',
        'model_json_schema',
        'model_parametrized_name',
        'model_post_init',
        'model_rebuild',
        'model_validate',
        'model_validate_json',
        'model_validate_strings',
        'parse_file',
        'parse_obj',
        'parse_raw',
        'schema',
        'schema_json',
        'update_forward_refs',
        'validate',
    }
)

#: What an operation's method and its route already take, beside the document's
#: parameters: the method's `self`, the route's `implementation` it calls
#: through, and the `body`, `credential` and `response` either may be handed.
PARAMETERS = frozenset({'self', 'implementation', 'body', 'credential', 'response'})

#: What the server's own code already names. The modules are the ones at the
#: generated package root, which no declared type may share.
RESERVED = Reserved(
    modules=frozenset({'app', 'runtime', 'security'}),
    attributes=ATTRIBUTES,
    parameters=PARAMETERS,
)


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
    keeps a plain `str` (docs/17 § 1).
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
