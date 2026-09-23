"""Media type normalisation and JSON detection."""

from __future__ import annotations

__all__ = ['base_media_type', 'is_json_media_type']

_JSON_TYPES = frozenset({'application/json', 'text/json'})


def base_media_type(media_type: str) -> str:
    """Normalize a media type for matching while ignoring its parameters."""
    return media_type.split(';', maxsplit=1)[0].strip().lower()


def is_json_media_type(media_type: str) -> bool:
    """Whether a normalized media type carries JSON."""
    return media_type in _JSON_TYPES or media_type.endswith('+json')
