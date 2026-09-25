"""Small readings of a shape shared by request decoding and response generation."""

from __future__ import annotations

from fastraml import BaseShape, FileShape, projected

from raml_mock.media import base_media_type

__all__ = ['concrete_shape', 'file_type_accepts', 'shape_name']


def concrete_shape(base: BaseShape) -> object | None:
    """The concrete kind a consumer should narrow against."""
    return projected(base).shape


def shape_name(base: BaseShape) -> str:
    """A stable diagnostic label without interpreting the RAML type string."""
    return base.name or (type(base.shape).__name__ if base.shape is not None else 'shape')


def file_type_accepts(shape: FileShape, media_type: str) -> bool:
    """Whether a transport media type satisfies a file shape's `fileTypes`."""
    return shape.file_types is None or any(
        base_media_type(item.value) in {'*/*', media_type} for item in shape.file_types
    )
