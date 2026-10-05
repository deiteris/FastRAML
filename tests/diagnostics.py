"""What a test reads off a `RamlError`: message keys and `info` dicts, never
rendered text (docs/11 § 6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml.errors import RamlError, Trace

__all__ = ['infos', 'keys', 'leaves', 'messages', 'problems', 'traces']


def traces(error: RamlError) -> list[Trace]:
    """Every frame of every chain, chain by chain, outermost first."""
    return [trace for chain in error.chains() for trace in chain]


def leaves(error: RamlError) -> list[Trace]:
    """The innermost frame of each chain: the problem it reports."""
    return [chain[-1] for chain in error.chains()]


def keys(error: RamlError) -> list[str]:
    """The message key of every frame, in `traces` order."""
    return [trace.message for trace in traces(error)]


def messages(error: RamlError) -> set[str]:
    """The message keys anywhere in `error`."""
    return set(keys(error))


def problems(error: RamlError) -> set[str]:
    """The message keys of `leaves`."""
    return {trace.message for trace in leaves(error)}


def infos(error: RamlError) -> list[Mapping[str, Any]]:
    """The non-empty `info` of every frame, in `traces` order."""
    return [trace.info for trace in traces(error) if trace.info]
