"""The built `viewer/` SPA, as an installable package.

`viewer/` is a React app over `fastraml tree` output (docs/16 section 11). In a
checkout you build it and serve `viewer/dist` yourself. That advice is useless
to someone who installed from PyPI and has no checkout, which is why the assets
ship here instead.

This package holds **no code that knows about RAML**. It is a directory of
static files and one function that says where they are, so anything that can
mount a directory -- FastAPI, aiohttp, a plain file server -- can serve it:

    from fastraml_viewer import static_dir
    app.mount('/raml-viewer', StaticFiles(directory=static_dir(), html=True))

The bundle is built with vite `base: './'`, so it runs from any sub-path
without being told where it was mounted. Point it at a document with
`?src=<url>`; with no `src` it looks for `api.json` beside `index.html`.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ['__version__', 'index_html', 'static_dir']

__version__ = '0.1.0'

#: Populated at build time from `viewer/dist` by `hatch_build.py`. Absent in a
#: checkout until the viewer is built, which is why `static_dir` checks.
_STATIC = Path(__file__).parent / 'static'


def static_dir() -> Path:
    """The directory holding `index.html` and its assets.

    Raises if the package was built without the bundle. That is a broken
    install rather than a state to handle: the wheel cannot be produced without
    the assets, so reaching this means someone imported from a source tree.
    """
    if not (_STATIC / 'index.html').is_file():
        message = (
            f'the viewer bundle is missing from {_STATIC}. '
            'In a checkout, run `npm ci && npm run build` in viewer/ and reinstall this package.'
        )
        raise RuntimeError(message)
    return _STATIC


def index_html() -> Path:
    """The entry point, for a server that wants to name one file."""
    return static_dir() / 'index.html'
