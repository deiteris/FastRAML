"""Resource loading.

This is the only module that touches the filesystem or the network. Everything
above it works with URIs and bytes.

The default file loader confines reads to a workspace root, because a RAML
document may come from an untrusted source and `!include` takes an arbitrary
path. See docs/03-yaml-and-io.md § 5 for the threat model and the limits of the
protection.
"""

from __future__ import annotations

import errno
import inspect
import os
import stat
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from fastraml.uris import file_uri_to_path, path_to_file_uri, uri_scheme

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
    """A resource could not be loaded. Callers wrap this with position context.

    One from the OS keeps its `errno`, which `RamlError.wrap` keys; any other
    has a message key for its text and its variables in `info` (docs/11 § 6).
    """

    #: Set per instance by `_keyed`, as for `WorkspaceEscapeError`.
    info: Mapping[str, Any]


class WorkspaceEscapeError(LoaderError):
    """A path resolved outside the workspace root.

    `info` holds `path`, `root` and `suggested_root`: the nearest directory
    holding both, or `''` when that would be a filesystem or drive root. The CLI
    turns `suggested_root` into advice about its own flag.
    """

    #: Set per instance by `_escaped`. Not a constructor argument, because
    #: `OSError` renders every constructor argument in `str()`.
    info: Mapping[str, Any] = {}


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
            raise LoaderError(err.errno, err.strerror) from err


#: Flags a platform lacks read as 0: `O_NOFOLLOW` and `O_NONBLOCK` are absent on
#: Windows, `O_BINARY` everywhere else. `_ELOOP` is `None` where the errno does
#: not exist, so comparing an `int` errno against it is simply false.
#:
#: `O_NONBLOCK` makes the regular-file check reachable: without it `os.open` on
#: a FIFO blocks until a writer appears, so a named pipe in the workspace would
#: hang the parse before `fstat` could refuse it. It has no effect on a regular
#: file.
_OPEN_FLAGS: Final = (
    os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
)
_ELOOP: Final = getattr(errno, 'ELOOP', None)


class SafeFileLoader:
    """Reads `file://` URIs, refusing anything outside `root`.

    Python has no portable `openat2(RESOLVE_BENEATH)`, so this combines four
    checks:

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

    def contains(self, uri: str) -> bool:
        """Whether `uri` is lexically beneath the root: a `file://` URI that
        `load` would not refuse for its path alone.
        """
        try:
            self._check_beneath(file_uri_to_path(uri), self.root, uri)
        except (ValueError, WorkspaceEscapeError):
            return False
        return True

    def files(self, suffix: str) -> list[str]:
        """The URI of every regular file beneath the root whose name ends in
        `suffix`, in path order.

        A symlink, and a directory whose name starts with `.`, is not entered:
        what the listing finds is what `load` would read.
        """
        found: list[str] = []
        for directory, names, files in os.walk(self.root):
            names[:] = sorted(
                name for name in names if not name.startswith('.') and not os.path.islink(os.path.join(directory, name))
            )
            found.extend(
                path_to_file_uri(path)
                for name in sorted(files)
                if name.endswith(suffix) and not os.path.islink(path := os.path.join(directory, name))
            )
        return found

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
                raise _keyed(WorkspaceEscapeError, 'refusing to follow a symlink', path=path) from err
            raise LoaderError(err.errno, err.strerror) from err

    def _verify(self, fd: int, path: str) -> None:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LoaderError('not a regular file')
        # A symlink at an intermediate component would have been followed by
        # os.open, so containment is re-checked against the resolved path.
        self._check_beneath(os.path.realpath(path), self._real_root, path)

    @staticmethod
    def _check_beneath(candidate: str, root: str, reported: str) -> None:
        try:
            relative = os.path.relpath(candidate, root)
        except ValueError as err:
            # Windows raises when the two are on different drives.
            raise _escaped(reported, root) from err
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            raise _escaped(reported, root)


def _escaped(reported: str, root: str) -> WorkspaceEscapeError:
    """The refusal, with paths in `info` rather than the message (docs/11 § 6)."""
    suggested = _common_root(root, reported)
    return _keyed(
        WorkspaceEscapeError, 'path is outside the workspace root', path=reported, root=root, suggested_root=suggested
    )


def _keyed[E: LoaderError](cls: type[E], key: str, **info: Any) -> E:
    """A loader error whose text is a message key, its variables in `info`.

    Not constructor arguments, because `OSError` renders every one in `str()`.
    """
    error = cls(key)
    error.info = info
    return error


def _common_root(root: str, path: str) -> str:
    """The nearest directory holding both, or `''` when suggesting one is no help.

    A filesystem or drive root is never suggested: widening to it disables the
    sandbox. Paths on two Windows drives have no common directory, and
    `commonpath` raises.
    """
    try:
        shared = os.path.commonpath((root, os.path.dirname(path)))
    except ValueError:
        return ''
    return '' if shared == os.path.dirname(shared) else shared


#: The refusal for an asynchronous client. A parse resolves each `!include`
#: synchronously where it is found, so there is nowhere to await; left alone,
#: an async client fails with `'coroutine' object has no attribute
#: 'status_code'`.
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
    satisfy that, so fastRAML depends on neither. `fastraml[http]` installs one.

    The client must be synchronous (docs/03 § 5). An async client is refused at
    construction when its `get` is a coroutine function, and per call when
    `get` returns an awaitable.
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
            raise _keyed(LoaderError, 'http request failed', error=str(err)) from err

        # A `get` that returns a coroutine passes the check in `__init__`. Close
        # it before raising to avoid an un-awaited coroutine warning.
        if inspect.isawaitable(response):
            close = getattr(response, 'close', None)
            if close is not None:
                close()
            raise LoaderError(_ASYNC_CLIENT)

        status = int(response.status_code)
        if not (200 <= status < 300):  # noqa: PLR2004 - the HTTP success range
            raise _keyed(LoaderError, 'http request failed', status=status)

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
            registered = sorted(self.loaders)
            raise _keyed(UnsupportedSchemeError, 'no loader for URI scheme', scheme=scheme, registered=registered)
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
