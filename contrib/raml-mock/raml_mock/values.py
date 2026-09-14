"""Copying a value out of the parse model, once rather than in three modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping


def detach(value: object) -> object:
    """A copy sharing no container with *value*."""
    if isinstance(value, dict):
        return {key: detach(item) for key, item in cast('Mapping[str, object]', value).items()}
    if isinstance(value, list):
        return [detach(item) for item in cast('list[object]', value)]
    return value


def detach_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """`detach` where the caller already knows it holds a mapping."""
    return {name: detach(item) for name, item in value.items()}
