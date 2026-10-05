"""Readings of a media type string shared by the lint rules (RFC 9110 § 8.3.1).

Type, subtype and parameter names are case-insensitive, so every comparison
goes through the casefolded forms here rather than each rule's own spelling.
"""

from __future__ import annotations

__all__ = ['is_json', 'media_essence']


def media_essence(media_type: str) -> str:
    """`type/subtype`, casefolded, without parameters.

    Tolerant of any text, so it also reads values that need not be media types,
    such as a `Content-Type` header's enum or a configured pattern. A body key
    is a media range, read whole by `media_parts` in `parser/facets.py`.
    """
    return media_type.partition(';')[0].strip().casefold()


def is_json(media_type: str) -> bool:
    """`application/json` or a `+json` structured syntax suffix (RFC 6839 § 3.1)."""
    essence = media_essence(media_type)
    return essence == 'application/json' or essence.endswith('+json')
