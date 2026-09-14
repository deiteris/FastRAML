# fastraml-viewer

The [fastRAML](https://github.com/deiteris/FastRAML) tree viewer, packaged as
static assets so any Python server can mount it.

`viewer/` in the repository is a React app that reads `fastraml tree` output. In
a checkout you build it and serve `viewer/dist` yourself. This package exists so
that someone who installed from PyPI, and has no checkout, can serve the same
thing.

```bash
pip install fastraml-viewer
```

## Use it

The package holds no code that knows about RAML — a directory of files and one
function that says where they are:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastraml_viewer import static_dir

app = FastAPI()
app.mount('/raml-viewer', StaticFiles(directory=static_dir(), html=True))
```

Anything that can serve a directory works the same way — aiohttp, Starlette,
`python -m http.server`.

## Point it at a document

The viewer reads a `fastraml tree` projection. Give it one with `?src=`:

```
/raml-viewer/?src=/raml.json
```

With no `src` it looks for `api.json` beside `index.html`. The bundle is built
with vite `base: './'`, so it runs from any mount path without being told which
one.

`fastapi-raml` wires this up for you: install it with its `viewer` extra and it
mounts this package and links to it from `/raml-docs`.

## Building

The assets are generated, so they are not in git. `hatch_build.py` copies
`viewer/dist` into the package at build time, and fails if there is no bundle:

```bash
cd viewer && npm ci && npm run build
cd ../contrib/fastraml-viewer && uv build
```

## Licence

MIT.
