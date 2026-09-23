"""Default-type inference: what kind is a declaration that never says?

Spec section Determine Default Types. A declaration with no `type:` and no
`schema:` takes its kind from the facets it does write — `minItems` means array,
`fileTypes` means file — and from the caller's default when it writes none that
hint at anything. See docs/05-type-model.md § 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastraml.types.base import (
    TYPE_ARRAY,
    TYPE_FILE,
    TYPE_NUMBER,
    TYPE_OBJECT,
    TYPE_STRING,
)
from fastraml.yamlnode import node_error

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml.yamlnode import Node

__all__ = [
    'FACET_TYPE_HINT',
    'identify_shape_type',
]

#: Facets that belong to exactly one built-in type. Every other facet name is
#: either common to all kinds or a user-defined one, and hints at nothing.
FACET_TYPE_HINT: Final[Mapping[str, str]] = {
    'minLength': TYPE_STRING,
    'maxLength': TYPE_STRING,
    'pattern': TYPE_STRING,
    'minimum': TYPE_NUMBER,
    'maximum': TYPE_NUMBER,
    'multipleOf': TYPE_NUMBER,
    'minItems': TYPE_ARRAY,
    'maxItems': TYPE_ARRAY,
    'uniqueItems': TYPE_ARRAY,
    'items': TYPE_ARRAY,
    'properties': TYPE_OBJECT,
    'minProperties': TYPE_OBJECT,
    'maxProperties': TYPE_OBJECT,
    'additionalProperties': TYPE_OBJECT,
    'discriminator': TYPE_OBJECT,
    'fileTypes': TYPE_FILE,
}

#: `pattern` hints at string like `minLength` does, but unlike `minLength` it is
#: string-only: a file has no pattern, so seeing one blocks the reconciliation
#: below for the rest of the declaration.
_STRING_ONLY_FACET: Final = 'pattern'


def identify_shape_type(facets: list[Node], default_type: str, location: str) -> str:
    """The kind implied by a flat `[k0, v0, k1, v1, …]` facet list.

    Four rules (docs/05 § 3): a facet unique to a
    type settles it; conflicting hints are an error; `string` and `file` are
    reconciled to `file`, because `minLength` and `maxLength` belong to both,
    unless a `pattern` has been seen; and a declaration that hints at nothing
    takes the caller's default.
    """
    detected = ''
    string_only = False
    for index in range(0, len(facets), 2):
        key = facets[index]
        hint = FACET_TYPE_HINT.get(key.value)
        if hint is None:
            continue
        if key.value == _STRING_ONLY_FACET:
            string_only = True
        elif not string_only:
            if detected == TYPE_STRING and hint == TYPE_FILE:
                detected = TYPE_FILE
            elif detected == TYPE_FILE and hint == TYPE_STRING:
                hint = TYPE_FILE
        if detected and hint != detected:
            raise node_error(
                'detected types by facets are not equal',
                location,
                key,
                info={'detected': detected, 'conflicting': hint, 'facet': key.value},
            )
        detected = hint
    return detected or default_type
