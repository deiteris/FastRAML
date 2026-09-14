"""Serve a RAML-described API as an MCP server.

Reads RAML with fastRAML and builds MCP components from the `HTTPRoute` list it
produces. No OpenAPI document is constructed anywhere: FastMCP's request
director is already route-driven, so a description format other than OpenAPI
needs to supply routes and nothing else.
"""

from __future__ import annotations

from fastmcp_raml.flatten import flatten
from fastmcp_raml.provider import DEFAULT_TIMEOUT, DOCUMENT_SCHEME, NO_SPEC, RAMLProvider, raml_mcp
from fastmcp_raml.routes import (
    Document,
    Routes,
    base_url_of,
    description_of,
    documents_of,
    title_of,
    to_http_routes,
    unresolved_in,
    version_of,
)

__all__ = [
    'DEFAULT_TIMEOUT',
    'DOCUMENT_SCHEME',
    'NO_SPEC',
    'Document',
    'RAMLProvider',
    'Routes',
    'base_url_of',
    'description_of',
    'documents_of',
    'flatten',
    'raml_mcp',
    'title_of',
    'to_http_routes',
    'unresolved_in',
    'version_of',
]
