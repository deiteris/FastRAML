"""The annotations an emitter writes for what RAML has no node of its own for.

RAML states a deprecation, a tag or an operation id nowhere, and an
annotation is how a document says something RAML does not: a type declared
once under `annotationTypes`, applied as `(name): value`. The vocabulary is
stated here once, so every integration writes the same names with the same
types, and a consumer reads one set.
"""

from __future__ import annotations

from typing import Final

from raml_document.model import TypeDecl, Yaml

__all__ = ['ANNOTATION_TYPES', 'annotate']

#: Each annotation's declaration, by name.
ANNOTATION_TYPES: Final[dict[str, TypeDecl]] = {
    'deprecated': TypeDecl(
        type='nil | string',
        description='Still served, and to be removed; the value, where there is one, says what to use instead.',
    ),
    'tags': TypeDecl(type='string[]', description='The groups an operation is listed under.'),
    'operationId': TypeDecl(type='string', description='The name the application gives this operation.'),
}


def annotate(annotations: dict[str, Yaml], declared: dict[str, TypeDecl], name: str, value: Yaml) -> None:
    """Apply `(name): value` to a node's `annotations`, and declare `name` in `declared`.

    `declared` is the document's `annotationTypes` as it is being built: an
    annotation applied without its type declared does not parse.
    """
    annotations[name] = value
    declared.setdefault(name, ANNOTATION_TYPES[name])
