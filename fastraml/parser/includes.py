"""`!include` resolution, its cache, and its limits.

`!include` is a YAML tag on a scalar, so the composer leaves it alone and this
module decides what it means. Two entry points exist and choosing between them
matters:

* `note_include_ref` records the directive and resolves its URI without reading
  anything. Use it when a *fragment* parser will load the target through its own
  cache — an `!include` of a DataType, a Trait, a NamedExample.
* `resolve_include` reads the target and splices its content into the current
  tree. Use it only for data.

Calling `resolve_include` where `note_include_ref` suffices doubles the I/O for
every typed-fragment include. See docs/03-yaml-and-io.md § 4.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastraml.errors import ErrorKind, RamlError
from fastraml.uris import path_to_file_uri, resolve_uri_ref
from fastraml.yamlnode import TAG_INCLUDE, TAG_STR, Node, NodeKind, compose, decode_source, node_error

if TYPE_CHECKING:
    from fastraml.positions import Position
    from fastraml.registry import Raml

__all__ = [
    'IncludeInfo',
    'IncludeRef',
    'note_include_ref',
    'resolve_include',
    'resolve_include_uri',
    'resolve_ref_uri',
    'strip_uri_suffix',
]

#: Include arguments composed as YAML. Everything else becomes a string scalar,
#: which is how `content: !include legal.md` works (spec section Includes).
_YAML_EXTENSIONS: Final = frozenset({'.raml', '.yaml', '.yml', '.json'})


@dataclass(frozen=True, slots=True)
class IncludeInfo:
    """The `!include` directive that provided one value."""

    #: The literal argument, as written.
    path: str
    #: The resolved absolute URI of the target.
    abs_uri: str


@dataclass(frozen=True, slots=True)
class IncludeRef:
    """One resolved `!include`, recorded in `Raml.include_refs` for tooling.

    Nothing in the parser reads these; an editor integration can emit document
    links from them.
    """

    source_uri: str
    path: str
    abs_uri: str
    position: Position


#: What opens a template variable. Spec section Resource Type and Trait
#: Parameters: "Parameters cannot be used within any file location that is used
#: in the context of modularization, that is, any file location defined in the
#: `!include` tag or as a value of any of the `uses` or `extends` nodes."
#:
#: The opening marker alone, not `parse_template_variables`: the rule is about
#: a parameter being present, not well-formed, and `<<` without `>>` is not a
#: file name anyone meant either.
_PARAMETER_OPENS: Final = '<<'


def resolve_ref_uri(raml: Raml, ref: str, location: str, position: Position | None = None) -> str:
    """Resolve a RAML path reference against `location`, or the workspace root.

    A reference beginning with `/` is *RAML-absolute*: the spec resolves it
    against the workspace root, not the filesystem root, which is what makes
    such a path portable. Everything else is ordinary RFC 3986 resolution
    against the referring file. Used for `!include` arguments and for `uses:`
    values, which have the same three argument forms — and which are also the
    two places the spec forbids a template parameter, so the check belongs here
    rather than at either call site.

    Includes resolve before templates expand, so without this check
    `!include <<version>>.raml` would reach the loader as a literal file name:
    reported as missing, or accepted if such a file happened to exist.
    """
    if _PARAMETER_OPENS in ref:
        raise RamlError.new(
            'path must not contain a template parameter',
            location,
            position,
            kind=ErrorKind.PARSING,
            info={'path': ref},
        )
    root = raml.workspace_root_uri
    if ref.startswith('/') and root:
        base = root if root.endswith('/') else root + '/'
        # The leading slash is stripped so that the workspace root is treated as
        # a base directory rather than being replaced by an absolute path.
        return resolve_uri_ref(base, ref[1:])
    return resolve_uri_ref(path_to_file_uri(location), ref)


def resolve_include_uri(raml: Raml, node: Node, location: str) -> str:
    """The absolute URI an `!include` node points at.

    The single authoritative URI computation for every include dispatch site.
    """
    return resolve_ref_uri(raml, node.value, location, node.position)


def _append_include_ref(raml: Raml, node: Node, location: str) -> str:
    abs_uri = resolve_include_uri(raml, node, location)
    raml.include_refs.setdefault(location, []).append(
        IncludeRef(source_uri=location, path=node.value, abs_uri=abs_uri, position=node.position)
    )
    return abs_uri


def note_include_ref(raml: Raml, node: Node, location: str) -> str:
    """Record an `!include` and return its URI, without reading the target.

    Returns `''` for a node that is not an include, so a caller can branch on
    the result.
    """
    if node.tag != TAG_INCLUDE:
        return ''
    return _append_include_ref(raml, node, raml.document_location(node, location))


def resolve_include(raml: Raml, node: Node, location: str) -> tuple[str, Node]:
    """Read an `!include` target and return `(target_uri, content)`.

    A node that is not an include is returned unchanged with an empty URI, so
    every value-decoding site can call this unconditionally.

    The composed content is cached per parse: a target referenced from five
    places is read and composed once. The caller must keep the *original* node
    for positions and provenance, and use the returned node only for the value.
    """
    if node.tag != TAG_INCLUDE:
        return '', node

    # Relative to the file that wrote the `!include`, which in a target tree
    # need not be the document being decoded (docs/19 § 5.3).
    location = raml.document_location(node, location)
    try:
        target = _append_include_ref(raml, node, location)
    except ValueError as err:  # a malformed location or argument
        raise RamlError.wrap('include: resolve URI', err, location, node.full_position) from err

    cached = raml.include_nodes.get(target)
    if cached is not None:
        return target, cached

    data = _load(raml, node, target, location)
    content = _compose_include(raml, node, data, target)
    raml.include_nodes[target] = content
    return target, content


def _load(raml: Raml, node: Node, target: str, location: str) -> bytes:
    limit = raml.max_include_size
    try:
        data = raml.loader.load(target, max_bytes=limit if limit > 0 else None)
    except OSError as err:
        raise RamlError.wrap(
            'include', err, location, node.full_position, kind=ErrorKind.LOADING, info={'path': target}
        ) from err
    # The loader was asked for limit + 1 bytes, so an oversized file is detected
    # without ever being read whole.
    if 0 < limit < len(data):
        raise node_error('include file exceeds size limit', location, node, info={'path': target, 'limit': limit})
    return data


def _compose_include(raml: Raml, node: Node, data: bytes, target: str) -> Node:
    text = decode_source(data)
    extension = posixpath.splitext(strip_uri_suffix(node.value))[1].lower()
    if extension in _YAML_EXTENSIONS:
        # YAML 1.2 is a superset of JSON, so .json composes correctly too.
        return compose(text, uri=target, max_depth=raml.max_depth)
    # Spec section Resolving Includes: any other file is included as a scalar.
    return Node(NodeKind.SCALAR, TAG_STR, text)


def strip_uri_suffix(ref: str) -> str:
    """Drop a `#fragment` or `?query` before taking a file extension.

    `schemas/order.json#/definitions/Item` is a JSON include, not an include of
    something with the extension `.json#`.
    """
    for separator in ('#', '?'):
        index = ref.find(separator)
        if index >= 0:
            ref = ref[:index]
    return ref
