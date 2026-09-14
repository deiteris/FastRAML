from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer

from raml_mock import create_app

API = """#%RAML 1.0
title: Codec
/rows:
  post:
    body:
      text/csv: string[]
    responses:
      200:
        body:
          text/csv:
            type: string[]
            minItems: 2
            example: [a, b]
"""


class CsvCodec:
    async def decode(self, request):
        return (await request.text()).split(',')

    def encode(self, value):
        return ','.join(value)


async def test_custom_codec_handles_both_directions(tmp_path):
    source = tmp_path / 'api.raml'
    source.write_text(API, encoding='utf-8')
    client = TestClient(TestServer(create_app(source, codecs={'text/csv': CsvCodec()})))
    await client.start_server()
    try:
        response = await client.post('/rows', data='one,two', headers={'Content-Type': 'text/csv'})
        text = await response.text()
    finally:
        await client.close()
    assert response.status == 200
    assert text == 'a,b'
