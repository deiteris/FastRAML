"""What the app and each route say about themselves, beside their shapes.

The document's root -- title, description, base URI, and the contact,
licence and tag descriptions RAML's `documentation:` carries -- and each
operation's deprecation, tags and operation id, which RAML says through the
annotations `raml_document.annotations` declares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raml_document import UNSET, Document, Documentation, TypeDecl

if TYPE_CHECKING:
    from raml_document import Method
    from raml_document.from_pydantic import Walk

__all__ = ['document', 'operation']


def document(app: Any, walk: Walk) -> Document:
    """The document's root, from the app's own metadata.

    RAML has one `description`, so a `summary` leads it. The contact, the
    licence, the terms of service and each tag's description are reference
    documentation, which is what RAML's `documentation:` holds.
    """
    description = '\n\n'.join(part for part in (app.summary, app.description) if part) or None
    root = Document(title=app.title, version=app.version or None, description=description)
    _base_uri(app, root, walk)
    root.documentation = [
        Documentation(title, content)
        for title, content in (
            ('Contact', _contact(app.contact)),
            ('License', _license(app.license_info)),
            ('Terms of service', app.terms_of_service),
            ('Tags', _tags(app.openapi_tags)),
        )
        if content
    ]
    return root


def operation(route: Any, method: Method, walk: Walk) -> None:
    """A route's deprecation, tags and explicit operation id, as annotations on `method`."""
    if route.deprecated:
        walk.annotate(method.annotations, 'deprecated')
    if route.tags:
        walk.annotate(method.annotations, 'tags', [str(getattr(tag, 'value', tag)) for tag in route.tags])
    if route.operation_id:
        walk.annotate(method.annotations, 'operationId', route.operation_id)


def _base_uri(app: Any, root: Document, walk: Walk) -> None:
    """`baseUri`, from the first server -- or the `root_path`, which FastAPI's schema lists first.

    Behind a proxy that strips a prefix, `root_path` is where the paths
    actually are; `get_openapi` puts it first in `servers` unless a server
    already names it.
    """
    servers = list(app.servers or ())
    root_path = getattr(app, 'root_path', '')
    if root_path and getattr(app, 'root_path_in_servers', True) and root_path not in {s['url'] for s in servers}:
        servers.insert(0, {'url': root_path})
    if not servers:
        return
    root.base_uri = servers[0]['url']
    for name, spec in (servers[0].get('variables') or {}).items():
        root.base_uri_parameters[name] = TypeDecl(type='string', default=spec.get('default', UNSET))
    if len(servers) > 1:
        walk.drop('servers', f'{len(servers) - 1} extra server(s); RAML has one baseUri')


def _contact(contact: dict[str, str] | None) -> str | None:
    if not contact:
        return None
    parts = [contact.get('name'), contact.get('url'), f'<{contact["email"]}>' if contact.get('email') else None]
    return '\n\n'.join(part for part in parts if part) or None


def _license(license_info: dict[str, str] | None) -> str | None:
    if not license_info:
        return None
    name = license_info.get('name', '')
    if license_info.get('url'):
        return f'[{name}]({license_info["url"]})'
    if license_info.get('identifier'):
        return f'{name} ({license_info["identifier"]})'
    return name or None


def _tags(tags: list[dict[str, Any]] | None) -> str | None:
    """Each tag with a description, as a list; a tag with none says nothing here."""
    lines = [f'- **{tag["name"]}**: {tag["description"]}' for tag in tags or () if tag.get('description')]
    return '\n'.join(lines) or None
