"""Immutable-by-contract records, without the frozen dataclass's construction cost.

A frozen dataclass assigns every field through `object.__setattr__`, which is
most of its construction time. `record` generates the ordinary slotted
dataclass, with value equality and hash, and tells the type checker the
instances are frozen: mypy rejects a write to a field, and nothing guards one
at run time, as for `Node` (docs/12 § 2). `identity_record` is the same
without generated equality, for a record compared and hashed by identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import dataclass_transform

__all__ = ['identity_record', 'record']


@dataclass_transform(frozen_default=True, field_specifiers=(field,))
def record[T: type](cls: T, /) -> T:
    """Decorate a class as a slotted value record whose fields are never assigned."""
    return dataclass(slots=True, unsafe_hash=True)(cls)  # type: ignore[return-value]


@dataclass_transform(frozen_default=True, eq_default=False, field_specifiers=(field,))
def identity_record[T: type](cls: T, /) -> T:
    """Decorate a class as a slotted record compared by identity, fields never assigned."""
    return dataclass(slots=True, eq=False)(cls)  # type: ignore[return-value]
