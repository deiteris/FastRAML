"""A compiled JSON Schema as one self-contained document (docs/10-validation.md § 7).

The walk behind `JsonShape.as_schema`: every reference out of the document is
pulled into its `definitions`, and every reference within it stays a pointer.
The documents walked are the registry's and are never edited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastraml.types.schema_compile import DATA_KEYWORDS, SCHEMA_MAPS, escape_json_pointer_segment, ref_target
from fastraml.uris import uri_stem

if TYPE_CHECKING:
    from referencing._core import Resolver

    from fastraml.types.schema_compile import CompiledSchema


#: Where a pulled-in reference is hung. `definitions` rather than `$defs`: every
#: draft understands it as a place to put subschemas, and a `$ref` into it is
#: the same pointer in all of them.
_BUNDLE_KEY = 'definitions'


@dataclass(frozen=True, slots=True)
class _Bundling:
    """What every level of the walk shares: where to resolve from, and into."""

    resolver: Resolver[Any]
    #: Name -> the pulled-in subschema, in encounter order.
    pulled: dict[str, Any]
    #: The identity of a resolved schema -> the local reference that stands for
    #: it, so a second reference points at the first copy, a cycle terminates,
    #: and the bundled root answers `#`.
    named: dict[int, str]
    #: Every name in use, including the ones the document already had.
    taken: set[str]
    #: True while walking a whole document the bundle is *of*. A pointer there
    #: is a pointer into the result and stands. Inside anything pulled in, or
    #: in a subschema bundled on its own, it is a pointer into a file the
    #: result is not: left alone it names whatever the result happens to have
    #: at that path, and a schema that validates something else is worse than
    #: one that is opaque.
    root: bool
    #: The file the bundle is of, where it is a whole file of its own: a
    #: reference back into it, from anywhere, is a pointer into the result.
    document: str | None = None
    #: The bundled location of this document, for aliases in its definitions.
    slot: str = '#'

    def at(self, resolver: Resolver[Any], slot: str) -> _Bundling:
        return _Bundling(resolver, self.pulled, self.named, self.taken, root=False, document=self.document, slot=slot)


def bundle(compiled: CompiledSchema, canonical: str | None) -> Any:
    """`compiled`'s document with every reference out of it pulled in.

    `canonical` is the schema's identity (`JsonShape.canonical_uri`); without
    a pointer it names the whole file, which references back into it then
    point into rather than copy.
    """
    document = compiled.contents
    taken = (
        set(document[_BUNDLE_KEY])
        if isinstance(document, dict) and isinstance(document.get(_BUNDLE_KEY), dict)
        else set()
    )
    whole, _, pointer = (canonical or '').partition('#')
    selected = compiled.uri.partition('#')[2]
    context = _Bundling(
        compiled.resolver,
        {},
        {id(document): '#'},
        taken,
        root=not selected,
        document=None if pointer else whole or None,
    )
    bundled = _bundle_root(context, document)
    if not context.pulled or not isinstance(bundled, dict):
        return bundled
    existing = bundled.get(_BUNDLE_KEY)
    merged = {**existing, **context.pulled} if isinstance(existing, dict) else context.pulled
    return {**bundled, _BUNDLE_KEY: merged}


def _definition_aliases(context: _Bundling, document: Any) -> dict[str, Any]:
    """Claim exact external aliases at the document's bundled location.

    Without this first pass, `definitions: {uuid: {$ref: "uuid.json"}}`
    reserves `uuid`, then the ordinary pull has to call the target `uuid2`.
    Claim aliases in every document before walking its properties, including
    documents pulled into a definition of the entry schema.
    """
    from referencing.exceptions import Unresolvable  # noqa: PLC0415 - deferred for startup cost

    if not isinstance(document, dict) or not isinstance(document.get(_BUNDLE_KEY), dict):
        return {}
    aliases: dict[str, Any] = {}
    for name, node in document[_BUNDLE_KEY].items():
        if not isinstance(node, dict) or set(node) != {'$ref'}:
            continue
        reference = node.get('$ref')
        if not isinstance(reference, str) or reference.startswith('#'):
            continue
        try:
            resolved = context.resolver.lookup(reference)
        except Unresolvable:
            continue
        local = f'{context.slot}/{_BUNDLE_KEY}/{escape_json_pointer_segment(name)}'
        if id(resolved.contents) in context.named:
            # Another document already claimed the target. Keep this alias as
            # a reference to it, but local pointers must still find this slot.
            context.named[id(node)] = local
            continue
        context.named[id(resolved.contents)] = local
        # A local pointer resolves to the alias node, whereas a direct external
        # reference resolves to its target. Both already occupy this one slot.
        context.named[id(node)] = local
        aliases[name] = resolved
    return aliases


def _bundle_root(context: _Bundling, document: Any) -> Any:
    """Bundle one document, expanding its claimed definition aliases in place."""
    if not isinstance(document, dict):
        return _bundle_node(context, document)
    aliases = _definition_aliases(context, document)
    bundled: dict[str, Any] = {}
    for key, value in document.items():
        if key == _BUNDLE_KEY and isinstance(value, dict):
            bundled[key] = {
                name: _bundle_root(
                    context.at(
                        aliases[name].resolver, f'{context.slot}/{_BUNDLE_KEY}/{escape_json_pointer_segment(name)}'
                    ),
                    aliases[name].contents,
                )
                if name in aliases
                else _bundle_node(context, node)
                for name, node in value.items()
            }
        else:
            bundled[key] = _bundle_member(context, key, value)
    return bundled


def _bundle_node(context: _Bundling, node: Any) -> Any:
    """`node` rebuilt, with each reference out of the document rewritten local.

    Rebuilt and not edited: the documents being walked are the registry's, shared
    with the validator and with every other type that names the same schema.
    """
    if isinstance(node, list):
        return [_bundle_node(context, item) for item in node]
    if not isinstance(node, dict):
        return node
    reference = node.get('$ref')
    if not isinstance(reference, str) or (context.root and reference.startswith('#')):
        return {key: _bundle_member(context, key, value) for key, value in node.items()}
    local = _pull(context, reference)
    if local is None:
        return {key: _bundle_member(context, key, value) for key, value in node.items()}
    # `$ref` first, where the author wrote it, and its siblings after: draft 2019
    # onward gives a schema beside a `$ref` meaning, so they are not dropped.
    rest = {key: _bundle_member(context, key, value) for key, value in node.items() if key != '$ref'}
    return {'$ref': local, **rest}


def _bundle_member(context: _Bundling, key: str, value: Any) -> Any:
    """One keyword's value, read as `_prefetch` reads it.

    A data keyword's value is a value, kept as written even where it looks like
    a reference; a map of subschemas is bundled member by member, so a property
    named `default` is still a schema.
    """
    if key in DATA_KEYWORDS:
        return value
    if key in SCHEMA_MAPS and isinstance(value, dict):
        return {name: _bundle_node(context, member) for name, member in value.items()}
    return _bundle_node(context, value)


def _pull(context: _Bundling, reference: str) -> str | None:
    """Resolve `reference` and answer with the local reference that replaces
    it: a pointer into the result where it lands in the bundled file itself,
    else a `definitions` entry holding what it names, registered on first use.

    `None` where it does not resolve, which leaves the reference as the author
    wrote it. `_prefetch` has already resolved every reference the bundle
    follows, skipping the same data keywords (`_bundle_member`), so this is the
    arm that should not be reachable rather than a fallback that is expected to
    fire.
    """
    from referencing.exceptions import Unresolvable  # noqa: PLC0415 - deferred for startup cost

    document, fragment = ref_target(context.resolver, reference)
    if document == context.document:
        return f'#{fragment}'
    try:
        resolved = context.resolver.lookup(reference)
    except Unresolvable:
        return None
    known = context.named.get(id(resolved.contents))
    if known is not None:
        return known
    name = _bundle_name(reference, context.taken)
    local = f'#/{_BUNDLE_KEY}/{escape_json_pointer_segment(name)}'
    # Registered before the walk into it, so a reference that leads back here
    # finds the name rather than descending again.
    context.named[id(resolved.contents)] = local
    context.pulled[name] = None
    context.pulled[name] = _bundle_root(context.at(resolved.resolver, local), resolved.contents)
    return local


def _bundle_name(reference: str, taken: set[str]) -> str:
    """A local name for what `reference` points at, unique within the document.

    The pointer's last segment where it has one, so `money.json#/definitions/
    Amount` stays `Amount`; otherwise the file's own stem.
    """
    stem = _pointer_tail(reference) or uri_stem(reference.partition('#')[0]) or 'schema'
    name = stem
    at = 2
    while name in taken:
        name = f'{stem}{at}'
        at += 1
    taken.add(name)
    return name


def _pointer_tail(reference: str) -> str | None:
    """The last segment of a reference's JSON Pointer, unescaped, if it has one.

    A *key*, not a type name: `_bundle_name` wants something short and unique
    per document. `subschema_name` is the one that decides what a subschema is
    called, and it names only the forms a `$ref` can address; both unescape
    per RFC 6901, `~1` before `~0`.
    """
    pointer = reference.partition('#')[2]
    if not pointer.startswith('/'):
        return None
    segment = pointer.rsplit('/', 1)[-1]
    return segment.replace('~1', '/').replace('~0', '~') or None
