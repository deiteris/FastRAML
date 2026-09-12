"""Resource loading.

This is the only module that touches the filesystem or the network. Everything
above it works with URIs and bytes.

The default file loader confines reads to a workspace root, because a RAML
document may come from an untrusted source and `!include` takes an arbitrary
path. See docs/03-yaml-and-io.md section 5 for the threat model and the limits
of the protection.
"""

from __future__ import annotations

import errno
import inspect
import os
import stat
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from pyraml.uris import file_uri_to_path, uri_scheme

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    'FileLoader',
    'HTTPLoader',
    'LoaderError',
    'ResourceLoader',
    'SafeFileLoader',
    'SchemeLoader',
    'UnsupportedSchemeError',
    'WorkspaceEscapeError',
    'build_loader',
]


class LoaderError(OSError):
    """A resource could not be loaded. Callers wrap this with position context."""


class WorkspaceEscapeError(LoaderError):
    """A path resolved outside the workspace root."""


class UnsupportedSchemeError(LoaderError):
    """No loader is registered for this URI scheme.

    Raised for `http(s)://` when no HTTP client was supplied, which is the
    default: remote includes are opt-in.
    """


@runtime_checkable
class ResourceLoader(Protocol):
    """Loads the bytes of a resource named by an absolute URI.

    `max_bytes` is a size ceiling. An implementation that honours it must return
    at most `max_bytes + 1` bytes, so the caller can detect an oversized
    resource without the implementation having read all of it. Returning more is
    permitted but wastes memory; returning less than the resource contains is
    not.
    """

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes: ...


def _read_fd(fd: int, max_bytes: int | None) -> bytes:
    with os.fdopen(fd, 'rb') as handle:
        if max_bytes is None:
            return handle.read()
        return handle.read(max_bytes + 1)


class FileLoader:
    """Reads `file://` URIs with no sandbox.

    Appropriate when every URI is trusted, or when the surrounding process is
    itself confined — a developer running the CLI over their own files. For
    untrusted input use `SafeFileLoader`.
    """

    __slots__ = ()

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        path = file_uri_to_path(uri)
        try:
            with open(path, 'rb') as handle:
                return handle.read() if max_bytes is None else handle.read(max_bytes + 1)
        except OSError as err:
            raise LoaderError(err.errno, str(err), path) from err


#: What a platform offers, established once. `O_NOFOLLOW` is absent on Windows
#: and `O_BINARY` everywhere else, so both are read through a default rather
#: than named directly — but which ones exist is a property of the interpreter,
#: not of the file being opened.
#:
#: `_ELOOP` is `None` where the platform has no such errno, and an `errno` is an
#: `int`, so the comparison that reads it is false rather than wrong.
_OPEN_FLAGS: Final = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
_ELOOP: Final = getattr(errno, 'ELOOP', None)


class SafeFileLoader:
    """Reads `file://` URIs, refusing anything outside `root`.

    Go uses `safeopen.OpenBeneath` (`openat2` with `RESOLVE_BENEATH` on Linux,
    `FILE_OPEN_REPARSE_POINT` on Windows). Python has no direct equivalent, so
    this combines four checks:

    1. the path must be lexically beneath `root`;
    2. the file is opened with `O_NOFOLLOW` where the platform provides it, so a
       symlink at the final component is refused outright;
    3. containment is re-checked against the resolved path, which catches a
       symlink at any intermediate component;
    4. non-regular files are refused, so a FIFO cannot stall the parser.

    Against an attacker who can modify the filesystem *during* the parse these
    checks are weaker than `openat2`. They do cover the case that matters here:
    a RAML document reaching outside the workspace through `../../etc/passwd` or
    a planted symlink.
    """

    __slots__ = ('_real_root', 'root')

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = os.path.abspath(os.fspath(root))
        self._real_root = os.path.realpath(self.root)

    def __repr__(self) -> str:
        return f'SafeFileLoader({self.root!r})'

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        path = file_uri_to_path(uri)
        self._check_beneath(path, self.root, path)

        fd = self._open(path)
        try:
            self._verify(fd, path)
        except BaseException:
            os.close(fd)
            raise
        return _read_fd(fd, max_bytes)

    @staticmethod
    def _open(path: str) -> int:
        try:
            return os.open(path, _OPEN_FLAGS)
        except OSError as err:
            # ELOOP means the final component is a symlink and O_NOFOLLOW
            # refused it. Report that as an escape rather than a missing file,
            # because the path may well exist.
            if err.errno == _ELOOP:
                msg = f'refusing to follow symlink: {path}'
                raise WorkspaceEscapeError(msg) from err
            raise LoaderError(err.errno, str(err), path) from err

    def _verify(self, fd: int, path: str) -> None:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            msg = f'not a regular file: {path}'
            raise LoaderError(msg)
        # A symlink at an intermediate component would have been followed by
        # os.open, so containment is re-checked against the resolved path.
        self._check_beneath(os.path.realpath(path), self._real_root, path)

    @staticmethod
    def _check_beneath(candidate: str, root: str, reported: str) -> None:
        try:
            relative = os.path.relpath(candidate, root)
        except ValueError as err:
            # Windows raises when the two are on different drives.
            msg = f'path {reported!r} is outside workspace root {root!r}'
            raise WorkspaceEscapeError(msg) from err
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            msg = f'path {reported!r} is outside workspace root {root!r}'
            raise WorkspaceEscapeError(msg)


#: What a caller is told when they hand over an `httpx.AsyncClient`.
#:
#: A parse is one synchronous recursive descent — an `!include` is resolved
#: where it is found, four dozen decoders deep — so there is no point at which
#: this could await anything. Left to fail on its own, an async client produces
#: `'coroutine' object has no attribute 'status_code'` and an un-awaited
#: coroutine warning, neither of which names the mistake.
_ASYNC_CLIENT = (
    'the HTTP client is asynchronous and a parse is synchronous. '
    'Pass a synchronous client (httpx.Client, requests.Session); '
    'to keep an event loop free, run the whole parse in a thread '
    '(asyncio.to_thread), which is what its CPU-bound work needs anyway.'
)


class HTTPLoader:
    """Reads `http://` and `https://` URIs through a caller-supplied client.

    The client is duck-typed: it needs a `get(url)` method returning an object
    with `status_code` and `content`. Both `httpx.Client` and `requests.Session`
    satisfy that, so pyRAML depends on neither. `pyraml[http]` installs one.

    **Synchronous, by the same decision that makes a parse single-threaded**
    (docs/01 § 2, docs/13 § 6). An async client is refused rather than
    mishandled — at construction where the client says what it is, and again
    per call for one that only reveals it by returning an awaitable.
    """

    __slots__ = ('client',)

    def __init__(self, client: Any) -> None:
        if inspect.iscoroutinefunction(getattr(client, 'get', None)):
            raise LoaderError(_ASYNC_CLIENT)
        self.client = client

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        try:
            response = self.client.get(uri)
        except Exception as err:
            msg = f'http get {uri}: {err}'
            raise LoaderError(msg) from err

        # A wrapper whose `get` is an ordinary function returning a coroutine
        # passes the check in `__init__`. Closed before raising, or the refusal
        # arrives with an un-awaited coroutine warning stapled to it.
        if inspect.isawaitable(response):
            close = getattr(response, 'close', None)
            if close is not None:
                close()
            msg = f'http get {uri}: {_ASYNC_CLIENT}'
            raise LoaderError(msg)

        status = int(response.status_code)
        if not (200 <= status < 300):  # noqa: PLR2004 - the HTTP success range
            msg = f'http get {uri}: status {status}'
            raise LoaderError(msg)

        content: bytes = response.content
        if max_bytes is not None:
            return content[: max_bytes + 1]
        return content


class SchemeLoader:
    """Dispatches by URI scheme. This is what a parser instance holds."""

    __slots__ = ('loaders',)

    def __init__(self, loaders: Mapping[str, ResourceLoader]) -> None:
        self.loaders = dict(loaders)

    def __repr__(self) -> str:
        return f'SchemeLoader({sorted(self.loaders)})'

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        scheme = uri_scheme(uri)
        loader = self.loaders.get(scheme)
        if loader is None:
            known = ', '.join(sorted(self.loaders)) or 'none'
            msg = f'no loader for URI scheme {scheme!r} (registered: {known})'
            raise UnsupportedSchemeError(msg)
        return loader.load(uri, max_bytes=max_bytes)


def build_loader(
    workspace_root: str | os.PathLike[str],
    *,
    file_loader: ResourceLoader | None = None,
    http_client: Any | None = None,
) -> SchemeLoader:
    """Assemble the loader for one parse.

    `file_loader` replaces the sandboxed file loader. Supplying one makes the
    caller responsible for path safety; the workspace root then governs only how
    RAML-absolute include paths resolve, not what may be opened.

    HTTP and HTTPS are registered only when a client is supplied, so remote
    includes are off unless asked for.
    """
    loaders: dict[str, ResourceLoader] = {'file': file_loader or SafeFileLoader(workspace_root)}
    if http_client is not None:
        remote = HTTPLoader(http_client)
        loaders['http'] = remote
        loaders['https'] = remote
    return SchemeLoader(loaders)
