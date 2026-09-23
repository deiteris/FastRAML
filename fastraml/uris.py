"""Path and URI handling.

Every location inside the parser is a URI, never an OS path. A path entering the
parser is canonicalised by `path_to_file_uri` at the boundary; conversion back
happens only in `fastraml.loaders`. Two consequences:

* diamond includes hit the cache no matter how the path was spelled, because
  `./a/../b.raml` and `b.raml` canonicalise identically;
* local and remote fragments share one namespace, so `uses:` may point at a URL
  with no special-casing downstream.

See docs/03-yaml-and-io.md § 8.
"""

from __future__ import annotations

import os
import posixpath
from urllib.parse import quote, unquote, urljoin, urlsplit

__all__ = [
    'file_uri_to_path',
    'is_file_uri',
    'path_to_file_uri',
    'relative_to',
    'resolve_uri_ref',
    'uri_base',
    'uri_scheme',
    'uri_stem',
]

_FILE_PREFIX = 'file://'

# Index of the ':' in a URI path holding a Windows drive, as in '/C:/x'.
_DRIVE_COLON = 2

# Characters left unescaped in a URI path. Colon must survive so that a Windows
# drive letter reads as `file:///C:/x` rather than `file:///C%3A/x`.
_PATH_SAFE = "/:@$&+,;=~!*'()"

# Additionally left unescaped when quoting an !include argument, which may carry
# a JSON Pointer fragment (`schema.json#/definitions/Foo`) or a query string.
_REF_SAFE = _PATH_SAFE + '#?%'


def is_file_uri(value: str) -> bool:
    return value.startswith(_FILE_PREFIX)


def path_to_file_uri(os_path: str | os.PathLike[str]) -> str:
    """Convert an OS path to a canonical `file://` URI.

    Idempotent: a value that is already a `file://` URI is returned unchanged,
    so call sites need no guard. The path is normalised before encoding, so
    equivalent spellings produce the same URI and therefore the same cache key.

    On Windows an absolute path is `C:/foo`, which needs a third slash to be a
    valid URI: `file:///C:/foo`.
    """
    text = os.fspath(os_path)
    if is_file_uri(text):
        return text

    slash_path = os.path.normpath(text).replace('\\', '/')
    if not slash_path.startswith('/'):
        # A Windows drive path, or a relative path the caller chose not to
        # resolve. Either way a URI path must start with a slash.
        slash_path = '/' + slash_path
    return _FILE_PREFIX + quote(slash_path, safe=_PATH_SAFE)


def file_uri_to_path(uri: str) -> str:
    """Convert a `file://` URI back to an OS path.

    Raises `ValueError` for anything else — a bare path, an `http://` URI, or a
    malformed value. Code that hands the result to the operating system must use
    this rather than string-slicing, so a non-file value cannot be silently
    treated as a path.
    """
    if not is_file_uri(uri):
        msg = f'not a file URI: {uri!r}'
        raise ValueError(msg)

    parts = urlsplit(uri)
    path = unquote(parts.path)

    if os.name == 'nt':
        # 'file:///C:/foo' splits to '/C:/foo'; drop the leading slash so the
        # result is a real Windows path. A UNC URI ('file://server/share')
        # carries the host in `netloc` and is rebuilt as '\\server\share'.
        if parts.netloc:
            path = f'//{parts.netloc}{path}'
        elif len(path) > _DRIVE_COLON and path[0] == '/' and path[_DRIVE_COLON] == ':':
            path = path[1:]
        path = path.replace('/', '\\')

    return os.path.normpath(path)


def resolve_uri_ref(base: str, ref: str) -> str:
    """Resolve `ref`, which may be relative, against the absolute `base` URI.

    Standard RFC 3986 resolution. On Windows a reference may arrive with OS
    separators — `os.path.relpath` produces them — so backslashes are converted
    before parsing; they are not URI path separators.
    """
    normalised = ref.replace('\\', '/')
    return urljoin(base, quote(normalised, safe=_REF_SAFE))


def relative_to(location: str, root: str) -> str:
    """`location` relative to `root`, for display.

    A location outside `root` is spelled with `../` ascents from the nearest
    common ancestor rather than as a full URI; one with no common ancestor is
    returned unchanged.
    """
    if location.startswith(root):
        return location.removeprefix(root) or location
    base = root.rstrip('/')
    ups = 0
    while base and not location.startswith(base + '/'):
        base, _, _ = base.rpartition('/')
        ups += 1
    return '../' * ups + location.removeprefix(base + '/') if base else location


def uri_base(uri: str) -> str:
    """The last path segment of a URI, as `os.path.basename` does for paths."""
    path = urlsplit(uri).path if '://' in uri else uri
    return unquote(posixpath.basename(path))


def uri_stem(uri: str) -> str:
    """The last path segment without its extension, as a document's short name."""
    return uri_base(uri).rsplit('.', 1)[0]


def uri_scheme(uri: str) -> str:
    """The scheme of a URI, or `''` when it has none.

    Only the schemes this parser produces are recognised. A bare OS path — which
    on Windows starts with a drive letter and could otherwise be mistaken for a
    scheme — returns `''`.
    """
    if uri.startswith(_FILE_PREFIX):
        return 'file'
    if uri.startswith('https://'):
        return 'https'
    if uri.startswith('http://'):
        return 'http'
    return ''
