from __future__ import annotations

import pathlib
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

from raml_mock import create_app

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'examples'))

API = """#%RAML 1.0
title: Mock API
securitySchemes:
  apiKey:
    type: x-api-key
  basic:
    type: Basic Authentication
  oauth:
    type: OAuth 2.0
    settings:
      authorizationUri: https://example.test/authorize
      accessTokenUri: https://example.test/token
      authorizationGrants: [authorization_code]
      scopes: [read, write]
types:
  Item:
    type: object
    properties:
      id: integer
      name: string
      category?:
        type: string
        default: book
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
        enabled?:
          type: boolean
          default: true
        tag?: string[]
        at?: datetime
        date?: date-only
      headers:
        X-Trace?:
          type: string
          default: generated-trace
      responses:
        404:
          body:
            application/json:
              type: object
              examples:
                missing:
                  id: 0
                  name: missing
        499:
        200:
          headers:
            X-Generated: string
          body:
            application/json: Item
    post:
      body:
        application/json: Item
      responses:
        201:
          body:
            application/json: Item
        422:
    put:
      body:
        application/json: Item
      responses:
        200:
          body:
            application/json: Item
    delete:
      responses:
        204:
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
/scalar-text:
  get:
    responses:
      200:
        body:
          text/plain: boolean
  /number:
    get:
      responses:
        200:
          body:
            text/plain:
              type: number
              minimum: 0.5
              maximum: 0.9
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
/negative-integer:
  get:
    responses:
      200:
        body:
          application/json:
            type: integer
            maximum: -5
/negative-number:
  get:
    responses:
      200:
        body:
          application/json:
            type: number
            maximum: -5.5
/unique:
  get:
    responses:
      200:
        body:
          application/json:
            type: array
            minItems: 2
            uniqueItems: true
            items: integer
/unique-default:
  get:
    responses:
      200:
        body:
          application/json:
            type: array
            minItems: 2
            uniqueItems: true
            items:
              type: string
              default: preferred
/pattern-object:
  get:
    responses:
      200:
        body:
          application/json:
            type: object
            minProperties: 2
            properties:
              /^x-/:
                type: integer
                minimum: 5
/pattern-string:
  get:
    responses:
      200:
        body:
          application/json:
            type: string
            pattern: ^\\d{13}$
            minLength: 13
            maxLength: 13
/generated-list:
  get:
    responses:
      200:
        body:
          application/json: string[]
/implicit-items:
  get:
    responses:
      200:
        body:
          application/json:
            type: array
/optional:
  get:
    responses:
      200:
        body:
          application/json:
            type: object
            properties:
              id: integer
              note?: string
/default-max:
  post:
    body:
      application/json:
        type: object
        maxProperties: 0
        properties:
          note?:
            type: string
            default: generated
    responses:
      204:
/events:
  get:
    responses:
      200:
        body:
          text/event-stream:
            type: string
            example: hello
/lines:
  get:
    responses:
      200:
        body:
          application/x-ndjson:
            type: array
            example: [{id: 1}, {id: 2}]
/state-objects:
  get:
    responses:
      200:
        body:
          application/json:
            type: object[]
/protected:
  get:
    securedBy: [apiKey]
    responses:
      200:
        body:
          application/json:
            type: string
            example: protected
/open:
  get:
    securedBy: [oauth, null]
    responses:
      200:
        body:
          application/json:
            type: string
            example: open
/secure-write:
  post:
    securedBy: [oauth: {scopes: [write]}, basic, apiKey]
    body:
      application/json: Item
    responses:
      200:
        body:
          application/json: Item
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
