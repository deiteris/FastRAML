from __future__ import annotations

import pathlib
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

from raml_mock import create_app

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'examples'))

API = """#%RAML 1.0
title: Mock API
types:
  Item:
    type: object
    properties:
      id: integer
      name: string
    examples:
      primary: {id: 7, name: Dune}
      alternate: {id: 8, name: Foundation}
  NarrowItem:
    type: Item
    properties:
      id:
        type: integer
        minimum: 10
  ExampleChoice:
    type: object
    properties:
      id: integer
    examples:
      invalid:
        strict: false
        value: {id: not-an-integer}
      valid: {id: 8}
  Form:
    type: object
    properties:
      count: integer
      enabled: boolean
  Upload:
    type: object
    properties:
      note: string
      data:
        type: file
        fileTypes: [image/png]
/items:
  get:
    responses:
      200:
        body:
          application/json: Item[]
  /fixed:
    get:
      responses:
        200:
          body:
            application/json:
              type: string
              example: fixed
  /endpoint-example:
    get:
      responses:
        200:
          body:
            application/json:
              type: Item
              example: {id: 9, name: Endpoint}
  /narrow:
    get:
      responses:
        200:
          body:
            application/json: NarrowItem
  /example-choice:
    get:
      responses:
        200:
          body:
            application/json: ExampleChoice
  /{id}:
    uriParameters:
      id: integer
    get:
      queryParameters:
        enabled?: boolean
        tag?: string[]
        at?: datetime
        date?: date-only
      headers:
        X-Trace?: string
      responses:
        200:
          headers:
            X-Generated: string
          body:
            application/json: Item
        404:
          body:
            application/json:
              type: object
              examples:
                missing:
                  id: 0
                  name: missing
    post:
      body:
        application/json: Item
      responses:
        201:
          body:
            application/json: Item
/form:
  post:
    body:
      application/x-www-form-urlencoded: Form
    responses:
      200:
        body:
          application/x-www-form-urlencoded:
            type: Form
            example: {count: 2, enabled: true}
/upload:
  post:
    body:
      multipart/form-data: Upload
    responses:
      204:
/binary:
  post:
    body:
      application/octet-stream:
        type: file
        fileTypes: [application/octet-stream]
        minLength: 2
    responses:
      200:
        body:
          application/octet-stream:
            type: file
            fileTypes: [application/octet-stream]
            minLength: 3
/text:
  get:
    responses:
      200:
        body:
          text/plain:
            type: string
            example: hello
/xml:
  get:
    responses:
      200:
        body:
          application/xml:
            type: string
            example: <ok/>
/structured-xml:
  get:
    responses:
      200:
        body:
          application/xml: Item
/raw:
  /{value}:
    get:
      responses:
        204:
/reserved:
  /{+value}:
    get:
      responses:
        204:
/any:
  post:
    body:
      application/json: any
    responses:
      204:
/decimal:
  get:
    responses:
      200:
        body:
          application/json:
            type: number
            minimum: 1.1
            maximum: 1.1
            multipleOf: 1.1
/multipart-response:
  get:
    responses:
      200:
        body:
          multipart/form-data:
            type: Upload
            example:
              note: cover
              data: cG5n
/bad-file-response:
  get:
    responses:
      200:
        body:
          application/pdf:
            type: file
            fileTypes: [image/png]
/bad-json-file-response:
  get:
    responses:
      200:
        body:
          application/json:
            type: file
            fileTypes: [image/png]
/wildcard-file-response:
  get:
    responses:
      200:
        body:
          application/octet-stream:
            type: file
            fileTypes: ['*/*']
            minLength: 1
/unsafe-multipart:
  get:
    responses:
      200:
        body:
          multipart/form-data:
            type: object
            properties:
              "bad\\nX-Evil": string
/precise:
  get:
    responses:
      200:
        body:
          application/json:
            type: number
            minimum: 1.123456789012345678901234567891
            maximum: 1.123456789012345678901234567891
            multipleOf: 1.123456789012345678901234567891
"""


@pytest.fixture
def source(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / 'api.raml'
    path.write_text(API, encoding='utf-8')
    return path


@pytest.fixture
async def client(source: pathlib.Path):
    connected = TestClient(TestServer(create_app(source)))
    await connected.start_server()
    try:
        yield connected
    finally:
        await connected.close()
