"""A route's path, query and header parameters, read off its dependency tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Iterator

    from raml_document import Method, Parameters
    from raml_document.from_pydantic import Walk

__all__ = ['parameters']


def _declared(dependant: Any) -> dict[str, list[Any]]:
    """Every parameter the dependency tree declares, by where it travels, in FastAPI's order."""
    found: dict[str, list[Any]] = {'path': [], 'query': [], 'header': [], 'cookie': []}
    pending = [dependant]
    while pending:
        current = pending.pop()
        for place, params in found.items():
            params.extend(getattr(current, f'{place}_params'))
        pending.extend(reversed(current.dependencies))
    return found


def _flattened(place: str, params: list[Any], at: str, walk: Walk) -> Iterator[tuple[str, Any]]:
    """`(wire name, FieldInfo)` for each parameter, a parameter model opened up.

    A lone parameter whose type is a model -- `Annotated[Filters, Query()]` --
    is FastAPI's parameter model, and its fields are the parameters. A header
    field is named as it travels, with the underscores its `Header()` converts.
    """
    first = params[0].field_info if len(params) == 1 else None
    model = first.annotation if first is not None else None
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        for param in params:
            yield param.alias, param.field_info
        return
    if model.model_config.get('extra') == 'forbid':
        walk.drop(at, f'{model.__name__} refuses {place} parameters it does not declare, which RAML cannot say')
    convert = getattr(first, 'convert_underscores', True)
    for name, info in model.model_fields.items():
        wire = info.alias or name
        if place == 'header' and convert and info.alias is None:
            wire = wire.replace('_', '-')
        yield wire, info


def parameters(route: Any, method: Method, at: str, walk: Walk) -> Parameters:
    """Put the route's query and header parameters on `method`; return the path ones."""
    uri: Parameters = {}
    targets = {'path': uri, 'query': method.query_parameters, 'header': method.headers}
    for place, params in _declared(route.dependant).items():
        if not params:
            continue
        for wire, info in _flattened(place, params, at, walk):
            if place == 'cookie':
                walk.drop(at, f'cookie parameter {wire!r} has no RAML form')
                continue
            targets[place][wire] = walk.parameter(info, f'{at}.{wire}')
    return uri
