"""Readings of a media type string shared by the lint rules (RFC 9110 § 8.3.1).

Type, subtype and parameter names are case-insensitive, so every comparison
goes through the casefolded forms here rather than each rule's own spelling.
"""

from __future__ import annotations

__all__ = ['is_json', 'media_essence', 'split_media_type']


def media_essence(media_type: str) -> str:
    """`type/subtype`, casefolded, without parameters."""
    return media_type.partition(';')[0].strip().casefold()


def split_media_type(media_type: str) -> tuple[str, dict[str, str]]:
    """The essence, and the parameters by casefolded name with quotes removed."""
    essence, *parameters = media_type.split(';')
    found = {}
    for parameter in parameters:
        name, _, value = parameter.partition('=')
        found[name.strip().casefold()] = value.strip().strip('"')
    return media_essence(essence), found


def is_json(media_type: str) -> bool:
    """`application/json` or a `+json` structured syntax suffix (RFC 6839 § 3.1)."""
    essence = media_essence(media_type)
    return essence == 'application/json' or essence.endswith('+json')
