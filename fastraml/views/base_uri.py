"""The base URI a caller sends requests to (docs/16 § 8).

`APIFragment.base_uri` is `baseUri` as written, and stays so: it is what the
document says. A caller needs it with `{version}` bound to the API's
`version:`, the one base URI variable RAML binds itself. Every other variable
is a value the caller supplies, declared under `baseUriParameters`, and stays a
variable here.

`version` declared as a base URI parameter is a variable like the rest, which
is how `views/openapi.py` reads it too: the author has made it the caller's
to choose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastraml.parser.fragments import APIFragment

__all__ = ['bound_base_uri']

#: The template variable RAML binds to the root `version:`.
VERSION = '{version}'


def bound_base_uri(api: APIFragment) -> str | None:
    """`baseUri` with `{version}` filled in from `version:`; `None` when there is no `baseUri`.

    Without a `version:` there is nothing to bind, and `{version}` stays
    written: a lint rule reports that document, and this does not guess.
    """
    if api.base_uri is None:
        return None
    written = str(api.base_uri.value)
    if api.version is None or 'version' in api.base_uri_parameters:
        return written
    return written.replace(VERSION, str(api.version.value))
