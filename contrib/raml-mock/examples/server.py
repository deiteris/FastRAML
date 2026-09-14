from __future__ import annotations

import argparse
import pathlib

from aiohttp import web
from pyraml import ParseOptions

from raml_mock import create_app

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / 'fixtures'
SAMPLE = FIXTURES / 'sample' / 'api.raml'
OPTIONS = ParseOptions(workspace_root=FIXTURES)


def build() -> web.Application:
    """Serve the same worked bookstore document as the other consumers."""
    return create_app(SAMPLE, options=OPTIONS)


def main() -> None:
    parser = argparse.ArgumentParser(description='Run the sample bookstore as a RAML-backed mock server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    web.run_app(build(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
