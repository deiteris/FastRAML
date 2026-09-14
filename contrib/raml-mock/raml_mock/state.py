from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastraml import ArrayShape

from raml_mock.generate import generate
from raml_mock.shapes import concrete_shape
from raml_mock.status import is_success
from raml_mock.values import detach_mapping

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from fastraml import BaseShape

    from raml_mock.config import RouteKey, StatefulResource
    from raml_mock.request import DecodedRequest
    from raml_mock.routes import MockRoute

__all__ = ['MockState']


@dataclass(slots=True, frozen=True)
class StateResult:
    value: object = None
    #: A bare status the operation must declare, in place of a value. Usually a
    #: failure, but a delete of something absent may be configured to succeed.
    status: int | None = None
    commit: Callable[[], None] | None = None


@dataclass(slots=True)
class _Store:
    config: StatefulResource
    initial: tuple[dict[str, object], ...]
    values: dict[object, dict[str, object]]
    item_shape: BaseShape | None


class MockState:
    """Isolated in-memory resources owned by one mock application."""

    __slots__ = ('_bindings', '_stores')

    def __init__(self, resources: tuple[StatefulResource, ...], routes: Mapping[RouteKey, MockRoute]) -> None:
        self._stores: dict[str, _Store] = {}
        self._bindings: dict[RouteKey, tuple[_Store, str]] = {}
        for config in resources:
            if config.name in self._stores:
                raise ValueError(f'stateful resource name is configured twice: {config.name!r}')
            initial = tuple(detach_mapping(value) for value in config.initial)
            store = _Store(config, initial, {}, _item_shape(config, routes))
            self._stores[config.name] = store
            self._reset_store(store)
            _check_delete_success(config, routes)
            for action in ('collection_get', 'item_get', 'create', 'update', 'delete'):
                key = cast('RouteKey | None', getattr(config, action))
                if key is not None:
                    normalized = (key[0].upper(), key[1])
                    if normalized in self._bindings:
                        raise ValueError(f'stateful route is configured twice: {normalized[0]} {normalized[1]}')
                    self._bindings[normalized] = (store, action)

    @property
    def routes(self) -> set[RouteKey]:
        return set(self._bindings)

    def handle(self, key: RouteKey, values: DecodedRequest) -> StateResult | None:
        bound = self._bindings.get(key)
        if bound is None:
            return None
        store, action = bound
        return self._handle_bound(store, action, values)

    def _handle_bound(  # noqa: PLR0911 - each configured resource action has one explicit outcome
        self,
        store: _Store,
        action: str,
        values: DecodedRequest,
    ) -> StateResult:
        if action == 'collection_get':
            return StateResult([detach_mapping(value) for value in store.values.values()])
        if action == 'create':
            return self._create(store, values)
        item_key = values.path.get(store.config.key_parameter)
        if action == 'item_get':
            found = store.values.get(item_key)
            return (
                StateResult(status=store.config.item_missing_status)
                if found is None
                else StateResult(detach_mapping(found))
            )
        if action == 'update':
            if item_key not in store.values:
                return StateResult(status=store.config.update_missing_status)
            item = _require_mapping(values.body)
            if item.get(store.config.key_field) != item_key:
                return StateResult(status=store.config.key_mismatch_status)
            _validate_item(store, item)
            return StateResult(detach_mapping(item), commit=lambda: store.values.__setitem__(item_key, item))
        if item_key not in store.values:
            return StateResult(status=store.config.delete_missing_status)
        return StateResult(commit=lambda: _forget(store.values, item_key))

    def reset(self, name: str | None = None) -> None:
        stores = self._stores.values() if name is None else (self._stores[name],)
        for store in stores:
            self._reset_store(store)

    def snapshot(self) -> dict[str, tuple[dict[str, object], ...]]:
        return {
            name: tuple(detach_mapping(value) for value in store.values.values())
            for name, store in self._stores.items()
        }

    def _create(self, store: _Store, values: DecodedRequest) -> StateResult:
        item = _require_mapping(values.body)
        key = _hashable_key(item.get(store.config.key_field), store.config.key_field)
        path_key = values.path.get(store.config.key_parameter)
        if path_key is not None and path_key != key:
            return StateResult(status=store.config.key_mismatch_status)
        if key in store.values:
            return StateResult(status=store.config.conflict_status)
        _validate_item(store, item)
        return StateResult(detach_mapping(item), commit=lambda: store.values.__setitem__(key, item))

    @staticmethod
    def _reset_store(store: _Store) -> None:
        store.values = {}
        initial = list(store.initial)
        if not initial and store.config.seed_from_example and store.item_shape is not None:
            initial.append(_require_mapping(generate(store.item_shape)))
        for value in initial:
            item = detach_mapping(value)
            key = _hashable_key(item.get(store.config.key_field), store.config.key_field)
            if key in store.values:
                raise ValueError(f'duplicate initial state key for {store.config.name!r}: {key!r}')
            _validate_item(store, item)
            store.values[key] = item


def _require_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError('a stateful create or update body must be an object')
    return detach_mapping(cast('Mapping[str, object]', value))


def _hashable_key(value: object, field: str) -> object:
    if value is None:
        raise ValueError(f'stateful value has no {field!r} key')
    try:
        hash(value)
    except TypeError as error:
        raise ValueError(f'stateful key {field!r} is not scalar') from error
    return value


def _forget(values: dict[object, dict[str, object]], key: object) -> None:
    values.pop(key, None)


def _item_shape(config: StatefulResource, routes: Mapping[RouteKey, MockRoute]) -> BaseShape | None:
    for key in (config.item_get, config.create, config.update):
        if key is None:
            continue
        route = routes.get((key[0].upper(), key[1]))
        found = None if route is None else _success_shape(route)
        if found is not None:
            return found
    if config.collection_get is not None:
        key = config.collection_get
        route = routes.get((key[0].upper(), key[1]))
        if route is not None:
            found = _success_shape(route)
            shape = None if found is None else concrete_shape(found)
            if isinstance(shape, ArrayShape):
                return shape.items
    return None


def _validate_item(store: _Store, item: dict[str, object]) -> None:
    if store.item_shape is None:
        return
    error = store.item_shape.validate(item)
    if error is not None:
        raise ValueError(f'item for stateful resource {store.config.name!r} does not match its response type: {error}')


def _check_delete_success(config: StatefulResource, routes: Mapping[RouteKey, MockRoute]) -> None:
    """A delete configured to succeed on a missing item must say so in RAML.

    A *failure* status the document does not declare still has a generic problem
    response to fall back on. A success has none: the mock would be answering
    with a status the API never promised, and for 204 it cannot even carry an
    explanation. A missing route is left to `create_app_from_raml`, which names
    it better.
    """
    if config.delete is None or not is_success(config.delete_missing_status):
        return
    method, path = config.delete[0].upper(), config.delete[1]
    route = routes.get((method, path))
    if route is not None and str(config.delete_missing_status) not in route.operation.responses:
        raise ValueError(f'delete_missing_status is not declared for {method} {path}: {config.delete_missing_status}')


def _is_success(code: str) -> bool:
    return code.isdigit() and is_success(int(code))


def _success_shape(route: MockRoute) -> BaseShape | None:
    return next(
        (
            body.shape
            for code, response in route.operation.responses.items()
            if _is_success(code)
            for body in response.bodies.values()
            if body.shape is not None
        ),
        None,
    )
