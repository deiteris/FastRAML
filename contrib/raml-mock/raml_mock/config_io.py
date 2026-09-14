"""Declarative mock behaviour, read from JSON.

Every error names the field it came from, because a configuration file is edited
by hand and `must be a string` on its own does not say which string. Carrying
that path is the only thing `_Object` does that a plain `dict` does not, and it
is why there is a class here rather than a validator per field per type.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from raml_mock.config import (
    Authentication,
    BasicCredentials,
    BearerToken,
    GenerationOptions,
    MockOptions,
    RouteBehavior,
    RouteKey,
    StatefulResource,
)

if TYPE_CHECKING:
    import os
    from collections.abc import Iterator, Mapping

__all__ = ['load_options']

_MISSING = object()


class _Object:
    """A JSON object being read, and the path that names it in errors."""

    __slots__ = ('_path', '_value')

    def __init__(self, value: object, path: str) -> None:
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise ValueError(f'{path} must be an object')
        self._value = cast('dict[str, Any]', value)
        self._path = path

    @property
    def raw(self) -> dict[str, Any]:
        """The underlying mapping, for a value this layer does not interpret."""
        return self._value

    def known(self, *allowed: str) -> None:
        unknown = set(self._value) - set(allowed)
        if unknown:
            raise ValueError(f'{self._path} contains unknown field {min(unknown)!r}')

    def _typed(self, key: str, kinds: type | tuple[type, ...], label: str, default: object) -> Any:
        value = self._value.get(key, _MISSING)
        if value is _MISSING:
            if default is _MISSING:
                raise TypeError(f'{self._path}.{key} must be {label}')
            return default
        # `bool` is an `int` in Python, and never what a numeric field means.
        if isinstance(value, bool) is not (kinds is bool) or not isinstance(value, kinds):
            raise TypeError(f'{self._path}.{key} must be {label}')
        return value

    def string(self, key: str, default: object = _MISSING) -> str:
        return cast('str', self._typed(key, str, 'a string', default))

    def integer(self, key: str, default: object = _MISSING) -> int:
        return cast('int', self._typed(key, int, 'an integer', default))

    def number(self, key: str, default: object = _MISSING) -> float:
        return float(cast('float', self._typed(key, (int, float), 'a number', default)))

    def boolean(self, key: str, *, default: object = _MISSING) -> bool:
        return cast('bool', self._typed(key, bool, 'a boolean', default))

    def optional_string(self, key: str) -> str | None:
        return None if self._value.get(key) is None else self.string(key)

    def optional_integer(self, key: str) -> int | None:
        return None if self._value.get(key) is None else self.integer(key)

    def route(self, key: str) -> RouteKey | None:
        return None if self._value.get(key) is None else _route_key(self.string(key), f'{self._path}.{key}')

    def child(self, key: str) -> _Object | None:
        value = self._value.get(key)
        return None if value is None else _Object(value, f'{self._path}.{key}')

    def objects(self, key: str) -> Iterator[_Object]:
        """Each element of an array of objects, numbered in its path."""
        value = self._value.get(key, [])
        if not isinstance(value, list):
            raise TypeError(f'{self._path}.{key} must be an array')
        for index, item in enumerate(cast('list[object]', value)):
            yield _Object(item, f'{self._path}.{key}[{index}]')

    def strings(self, key: str) -> list[str]:
        value = self._value.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise TypeError(f'{self._path}.{key} must be an array of strings')
        return cast('list[str]', value)

    def entries(self) -> Iterator[tuple[str, _Object]]:
        """Each value of a map whose keys the author chose."""
        for key, value in self._value.items():
            yield key, _Object(value, f'{self._path}.{key}')

    def lists(self) -> Iterator[tuple[str, Iterator[_Object]]]:
        """Each value of a map whose values are arrays of objects."""
        for key in self._value:
            yield key, self.objects(key)


def load_options(path: str | os.PathLike[str]) -> MockOptions:
    """Load declarative mock behavior from a JSON file."""
    with Path(path).open(encoding='utf-8') as stream:
        root = _Object(json.load(stream, parse_constant=_reject_constant), 'configuration')
    root.known('generation', 'routes', 'authentication', 'resources')
    return MockOptions(
        _generation(root.child('generation')),
        _routes(root.child('routes')),
        _authentication(root.child('authentication')),
        tuple(_resource(item) for item in root.objects('resources')),
    )


def _generation(item: _Object | None) -> GenerationOptions:
    if item is None:
        return GenerationOptions()
    item.known('seed', 'collectionSize', 'optionalProbability')
    seed = item.raw.get('seed')
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, (int, str))):
        raise ValueError('generation.seed must be an integer or string')
    return GenerationOptions(
        seed=seed,
        collection_size=item.optional_integer('collectionSize'),
        optional_probability=item.number('optionalProbability', 0.0),
    )


def _routes(item: _Object | None) -> dict[RouteKey, RouteBehavior]:
    if item is None:
        return {}
    result: dict[RouteKey, RouteBehavior] = {}
    for raw_key, behavior in item.entries():
        behavior.known('status', 'example', 'delay')
        result[_route_key(raw_key, 'routes')] = RouteBehavior(
            status=behavior.optional_string('status'),
            example=behavior.optional_string('example'),
            delay=behavior.number('delay', 0.0),
        )
    return result


def _authentication(item: _Object | None) -> Authentication | None:
    if item is None:
        return None
    item.known('basic', 'bearer')
    basic = {name: tuple(_credentials(e) for e in entries) for name, entries in _schemes(item, 'basic')}
    bearer = {name: tuple(_token(e) for e in entries) for name, entries in _schemes(item, 'bearer')}
    return Authentication(basic=basic, bearer=bearer)


def _schemes(item: _Object, key: str) -> Iterator[tuple[str, Iterator[_Object]]]:
    schemes = item.child(key)
    return iter(()) if schemes is None else schemes.lists()


def _credentials(item: _Object) -> BasicCredentials:
    item.known('username', 'password')
    return BasicCredentials(item.string('username'), item.string('password'))


def _token(item: _Object) -> BearerToken:
    item.known('token', 'scopes')
    return BearerToken(item.string('token'), frozenset(item.strings('scopes')))


def _resource(item: _Object) -> StatefulResource:
    item.known(
        'name',
        'keyField',
        'keyParameter',
        'initial',
        'seedFromExample',
        'collectionGet',
        'itemGet',
        'create',
        'update',
        'delete',
        'itemMissingStatus',
        'updateMissingStatus',
        'deleteMissingStatus',
        'conflictStatus',
        'keyMismatchStatus',
    )
    initial: tuple[Mapping[str, object], ...] = tuple(entry.raw for entry in item.objects('initial'))
    return StatefulResource(
        name=item.string('name'),
        key_field=item.string('keyField'),
        key_parameter=item.string('keyParameter'),
        initial=initial,
        seed_from_example=item.boolean('seedFromExample', default=False),
        collection_get=item.route('collectionGet'),
        item_get=item.route('itemGet'),
        create=item.route('create'),
        update=item.route('update'),
        delete=item.route('delete'),
        item_missing_status=item.integer('itemMissingStatus', 404),
        update_missing_status=item.integer('updateMissingStatus', 404),
        delete_missing_status=item.integer('deleteMissingStatus', 404),
        conflict_status=item.integer('conflictStatus', 409),
        key_mismatch_status=item.integer('keyMismatchStatus', 400),
    )


def _route_key(value: str, path: str) -> RouteKey:
    method, separator, route = value.partition(' ')
    if not separator or not method or not route.startswith('/'):
        raise ValueError(f'{path} key must be "METHOD /path", got {value!r}')
    return method.upper(), route


def _reject_constant(value: str) -> object:
    raise ValueError(f'{value} is not valid JSON')
