"""File uploads: RAML's `file` type, and the part that carries one.

A handler declaring an `UploadedFile` parameter takes a `multipart/form-data`
body. Other body parameters beside it become the form's other fields.

    async def post(
        self,
        meta: Book,
        cover: Annotated[UploadedFile, File(file_types=['image/png'], max_size=5_000_000)],
    ) -> Annotated[web.Response, Responds(201, Book)]: ...

**Nothing is buffered.** aiohttp reads a multipart body as a stream, and an
upload in an aiohttp application should stay one -- a handler receives an
`UploadedFile` that has read no bytes, and the part is pulled off the wire when
the handler asks for it.

The cost is ordering: a sequential reader cannot produce the second part before
the first, so **a client must send parts in the order the signature declares
them**, and any non-file field must come before the files. RAML has no node for
that requirement, so it is in this package's README and not in the document.

`file_types`, `max_size` and `min_size` are RAML's `fileTypes`, `maxLength` and
`minLength`, and each is enforced as well as written -- `fileTypes` off the
part's header before a byte is read, the two lengths as a running count while it
is. That is a spelling and not a rule (`docs/17` § 1): RAML says what the
facet constrains and this says the same thing in Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiohttp import web

from aiohttp_raml.errors import STATUS, describe

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ['File', 'PartRejected', 'UploadedFile', 'is_upload']


@dataclass(frozen=True, slots=True)
class File:
    """The RAML `file` facets a part is declared with, and checked against."""

    #: RAML `fileTypes`: the media types this part accepts.
    file_types: Sequence[str] = ()
    #: RAML `maxLength`, in bytes.
    max_size: int | None = None
    #: RAML `minLength`, in bytes.
    min_size: int | None = None
    description: str | None = None


def refusal(name: str, message: str) -> dict[str, object]:
    """One `RequestError` about a part, as the dict that goes on the wire."""
    return describe('body', message, kind='file_rejected', loc=[name])


class PartRejected(Exception):  # noqa: N818 - a refusal, carried as a detail
    """A part broke a facet it was declared with.

    Carries the detail rather than a response, because it is raised from two
    places. The injector is still assembling the request when a *field* breaks a
    facet; a *file* can only break one once the handler is reading it, which is
    after the injector has handed control over. Both end up as the same 400.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        self.detail = detail
        super().__init__(str(detail.get('msg', '')))

    def response(self) -> web.Response:
        return web.json_response([self.detail], status=STATUS)


class PartOutOfOrder(RuntimeError):  # noqa: N818 - a programming error, not a response
    """A handler asked for a part the client has not sent yet, or sent already."""


class Parts:
    """The multipart reader, advanced one declared part at a time."""

    __slots__ = ('_expected', '_reader')

    def __init__(self, reader: Any, expected: Sequence[str]) -> None:
        self._reader = reader
        self._expected = list(expected)[::-1]

    async def next(self, name: str) -> Any:
        """The next part, which must be the one named."""
        if not self._expected:
            raise PartOutOfOrder(f'{name!r} was already read, or was never declared')
        wanted = self._expected.pop()
        if wanted != name:
            raise PartOutOfOrder(f'{name!r} was asked for before {wanted!r}; parts arrive in declaration order')
        part = await self._reader.next()
        if part is None:
            raise PartRejected(refusal(name, 'the part is missing from the request'))
        if part.name != name:
            raise PartRejected(
                refusal(name, f'the part arrived as {part.name!r}; parts must be sent in declaration order')
            )
        return part


class UploadedFile:
    """One `multipart/form-data` part, read from the wire on demand.

    Nothing is read until `read` or `read_chunk` is called, and `read_chunk`
    keeps memory to one chunk. `max_size` is enforced as the count passes it,
    and `min_size` when the part ends.
    """

    __slots__ = ('_facets', '_part', '_parts', '_read', 'name')

    def __init__(self, parts: Parts, name: str, facets: File) -> None:
        self.name = name
        self._parts = parts
        self._facets = facets
        self._part: Any = None
        self._read = 0

    async def open(self) -> UploadedFile:
        """Advance to this part and check `file_types`, without reading a byte.

        `filename` and `content_type` are headers, so they are unknown until the
        reader reaches the part -- and they read as `None` before that, which is
        a wrong answer rather than an error. Call this to look at a part before
        deciding whether to read it. `read` and `read_chunk` do it for you, and
        calling it twice is harmless.
        """
        if self._part is None:
            self._part = await self._parts.next(self.name)
            kinds = self._facets.file_types
            if kinds and self.content_type not in kinds:
                raise PartRejected(
                    refusal(self.name, f'content type {self.content_type!r} is not one of {list(kinds)}')
                )
        return self

    @property
    def opened(self) -> bool:
        """Has the reader reached this part? `filename` says nothing until it has."""
        return self._part is not None

    @property
    def filename(self) -> str | None:
        """The client's name for the file. `None` until `open`."""
        return getattr(self._part, 'filename', None)

    @property
    def content_type(self) -> str | None:
        """The part's media type. `None` until `open`."""
        part = self._part
        return part.headers.get('Content-Type') if part is not None else None

    @property
    def size(self) -> int:
        """Bytes read so far; the whole part once it has been exhausted."""
        return self._read

    def _count(self, chunk: bytes) -> bytes:
        self._read += len(chunk)
        limit = self._facets.max_size
        if limit is not None and self._read > limit:
            raise PartRejected(refusal(self.name, f'is over the {limit} bytes allowed'))
        if not chunk:
            floor = self._facets.min_size
            if floor is not None and self._read < floor:
                raise PartRejected(refusal(self.name, f'is {self._read} bytes, under the {floor} required'))
        return chunk

    async def read(self, *, decode: bool = False) -> bytes:
        """The whole part at once. `read_chunk` is the streaming form."""
        await self.open()
        data: bytes = await self._part.read(decode=decode)
        self._count(data)
        self._count(b'')  # the empty count is what checks `min_size`
        return data

    async def read_chunk(self, size: int = 8192) -> bytes:
        """One chunk, or `b''` at the end of the part."""
        await self.open()
        return self._count(await self._part.read_chunk(size))


def is_upload(annotation: Any) -> bool:
    """Is this annotation an `UploadedFile`, however it is wrapped?"""
    return isinstance(annotation, type) and issubclass(annotation, UploadedFile)
