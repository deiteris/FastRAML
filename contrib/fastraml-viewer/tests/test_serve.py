"""The server: the document route in front of the bundle, and the bundle around it.

The document is an opaque JSON value here on purpose: this package states no
opinion about its shape, so the tests use one it cannot possibly mistake for
the sample it ships.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from fastraml_viewer import serve, static_dir

DOCUMENT = {'hello': 'world', 'count': 7}


def _start(document: object):
    server = serve(document, host='127.0.0.1', port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _request(port: int, path: str, method: str = 'GET'):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        return response, response.read()
    finally:
        conn.close()


def test_the_document_is_served_at_api_json():
    server, port = _start(DOCUMENT)
    try:
        response, body = _request(port, '/api.json')
        assert response.status == 200
        assert response.getheader('Content-Type') == 'application/json'
        assert json.loads(body) == DOCUMENT
    finally:
        server.shutdown()


def test_the_document_shadows_the_sample_the_bundle_ships():
    """The bundle carries its own `api.json` so it demos bare; serving must not
    let it answer for the document that was passed.
    """
    sample = (static_dir() / 'api.json').read_text(encoding='utf-8')
    server, port = _start(DOCUMENT)
    try:
        _, body = _request(port, '/api.json')
        assert body.decode('utf-8') != sample
        assert json.loads(body) == DOCUMENT
    finally:
        server.shutdown()


def test_the_entry_point_is_served_at_the_root():
    server, port = _start(DOCUMENT)
    try:
        response, body = _request(port, '/')
        assert response.status == 200
        assert './assets/' in body.decode('utf-8')
    finally:
        server.shutdown()


def test_the_assets_it_references_are_served():
    import re

    html = (static_dir() / 'index.html').read_text(encoding='utf-8')
    referenced = re.findall(r'(?:src|href)="\./([^"]+)"', html)
    server, port = _start(DOCUMENT)
    try:
        for name in referenced:
            response, body = _request(port, f'/{name}')
            assert response.status == 200, name
            assert body == (static_dir() / name).read_bytes(), name
            assert response.getheader('Content-Type'), name
    finally:
        server.shutdown()


def test_a_missing_file_is_a_404():
    server, port = _start(DOCUMENT)
    try:
        response, _ = _request(port, '/absent.json')
        assert response.status == 404
    finally:
        server.shutdown()


def test_a_directory_is_not_listed():
    server, port = _start(DOCUMENT)
    try:
        response, _ = _request(port, '/assets/')
        assert response.status == 404
    finally:
        server.shutdown()


def test_a_traversal_cannot_leave_the_bundle():
    """Encoded dots are the vector: a raw `/../` is normalised away by most
    clients before it leaves the socket, which is what hides the bug.

    `translate_path` drops every `..` component, so a storm of them cleans up
    to the bundle root, which the base class answers with a redirect to itself
    (its apache behaviour); following the redirect must land on the app's own
    entry point, and a traversal that names nothing is a 404.
    """
    server, port = _start(DOCUMENT)
    try:
        response, _ = _request(port, '/' + '%2e%2e%2f' * 8)
        assert response.status == 301
        location = response.getheader('Location')
        assert location is not None
        assert location.startswith('/')
        follow, body = _request(port, location)
        assert follow.status == 200
        assert './assets/' in body.decode('utf-8')
        response, _ = _request(port, '/%2e%2e%2fnope')
        assert response.status == 404
    finally:
        server.shutdown()


def test_head_of_the_document_names_its_length():
    server, port = _start(DOCUMENT)
    try:
        response, body = _request(port, '/api.json', method='HEAD')
        assert response.status == 200
        assert body == b''
        assert int(response.getheader('Content-Length')) == len(json.dumps(DOCUMENT, indent=2) + '\n')
    finally:
        server.shutdown()


def test_two_servers_keep_their_own_documents():
    """The handler is a class per call; state on one shared class would let the
    second document leak into the first server.
    """
    first, first_port = _start({'which': 'first'})
    second, second_port = _start({'which': 'second'})
    try:
        _, first_body = _request(first_port, '/api.json')
        _, second_body = _request(second_port, '/api.json')
        assert json.loads(first_body) == {'which': 'first'}
        assert json.loads(second_body) == {'which': 'second'}
    finally:
        first.shutdown()
        second.shutdown()


def test_an_unserialisable_document_fails_before_anything_is_bound():
    with pytest.raises(TypeError):
        serve({'x': object()})


def test_the_bundle_is_required(monkeypatch, tmp_path):
    """Same broken-install state as `static_dir`; `serve` must not reach the
    socket to find out.
    """
    import fastraml_viewer

    monkeypatch.setattr(fastraml_viewer, '_STATIC', tmp_path)
    with pytest.raises(RuntimeError, match='viewer bundle is missing'):
        serve(DOCUMENT)
