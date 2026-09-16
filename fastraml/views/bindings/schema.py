"""Language-neutral schema of the ``fastraml tree`` wire contract.

The schema reads the emitter and model source once. Backends consume its plain
records and decide only how those facts are represented in their target
language; no backend needs to understand Python's AST.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['ContractSchema', 'Emitted', 'Facet', 'ShapeKind', 'Vocabulary', 'contract_schema']

_ROOT: Final = pathlib.Path(__file__).resolve().parent.parent.parent.parent

_NOT_EMITTED: Final = frozenset(
    {
        'anchor',
        'id',
        'key_pos',
        'raml',
        'value_pos',
        '_raml',
        '_unwrapped',
        '_visiting',
        'type_expr_refs',
        'pending_facets',
        'link',
        'alias',
        'is_annotation_type',
        'shape',
        'from_mapping',
        'validator',
        'location',
    }
)


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """One closed wire vocabulary, independent of a target language."""

    name: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShapeKind:
    """One emitted shape discriminator and the model class implementing it."""

    name: str
    model: str


@dataclass(frozen=True, slots=True)
class Emitted:
    """What one ``_Projector`` method puts in its result."""

    required: tuple[str, ...]
    optional: tuple[str, ...]
    delegates: tuple[str, ...]
    dynamic: bool


@dataclass(frozen=True, slots=True)
class Facet:
    """One kind-specific field and how the wire encodes its value."""

    name: str
    annotation: str
    wire_form: Literal['annotation', 'exact_decimal', 'reference']


@dataclass(frozen=True, slots=True)
class ContractSchema:
    """All source-derived facts shared by language backends."""

    projector: dict[str, Emitted]
    shape_kinds: tuple[ShapeKind, ...]
    shape_facets: dict[str, tuple[Facet, ...]]
    vocabularies: tuple[Vocabulary, ...]
    exact_decimal_slots: frozenset[str]

    def delegated_fields(self, caller: Emitted) -> tuple[str, ...]:
        """Literal fields merged into ``caller`` by non-dynamic delegates."""
        names: list[str] = []
        for method in caller.delegates:
            found = self.projector.get(method)
            if found is None:
                raise LookupError(f'a projector method merges in `{method}`, which is not a _Projector method')
            if found.dynamic:
                continue
            names.extend(name for name in (*found.required, *found.optional) if name not in names)
        return tuple(names)


@cache
def contract_schema() -> ContractSchema:
    """Read and cache the complete language-neutral contract."""
    kinds = _shape_kinds()
    exact = _name_set('_EXACT')
    back = _name_set('_BACK_POINTERS')
    return ContractSchema(
        projector=_projector(),
        shape_kinds=kinds,
        shape_facets=_shape_facets(kinds, exact, back),
        vocabularies=(
            Vocabulary('ShapeType', tuple(kind.name for kind in kinds)),
            Vocabulary('HttpMethod', _set_values('fastraml/parser/source_ir.py', 'METHODS')),
            Vocabulary(
                'FragmentKind',
                _mapped_enum_values('fastraml/parser/fragments.py', '_FRAGMENT_CLASSES', 'FragmentKind'),
            ),
            Vocabulary('AnnotationTarget', _enum_values('fastraml/domains.py', 'DomainLocation')),
        ),
        exact_decimal_slots=exact,
    )


def _projector() -> dict[str, Emitted]:
    source = _tree('fastraml/views/tree.py')
    projector = next(node for node in ast.walk(source) if isinstance(node, ast.ClassDef) and node.name == '_Projector')
    return {
        method.name: _emitted(method)
        for method in projector.body
        if isinstance(method, ast.FunctionDef) and not method.name.startswith('__')
    }


def _emitted(method: ast.FunctionDef) -> Emitted:
    required: list[str] = []
    optional: list[str] = []
    delegates: list[str] = []
    dynamic = False
    loops: dict[str, list[str]] = {}
    for node in ast.walk(method):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Tuple):
            loops.setdefault(node.target.id, []).extend(_strings(node.iter.elts))

    for node, conditional in _writes(method):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    (optional if conditional else required).append(key.value)
                else:
                    dynamic = True
        elif isinstance(node, ast.Subscript):
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                (optional if conditional else required).append(index.value)
            elif isinstance(index, ast.Name) and index.id in loops:
                optional.extend(loops[index.id])
            else:
                dynamic = True
        elif isinstance(node, ast.Call):
            delegates.extend(_delegates(node))

    at_hand = set(required)
    return Emitted(
        required=tuple(dict.fromkeys(required)),
        optional=tuple(name for name in dict.fromkeys(optional) if name not in at_hand),
        delegates=tuple(dict.fromkeys(delegates)),
        dynamic=dynamic,
    )


def _writes(method: ast.FunctionDef) -> Iterator[tuple[ast.AST, bool]]:
    for node in ast.walk(method):
        conditional = _under_a_branch(method, node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
            yield node.value, conditional
        elif isinstance(node, ast.Return) and node.value is not None:
            display = _record(node.value)
            if display is not None:
                yield display, conditional
        elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'update'
        ):
            yield node, conditional


def _record(value: ast.expr) -> ast.Dict | None:
    if isinstance(value, ast.Dict):
        return value
    if isinstance(value, (ast.ListComp, ast.SetComp)) and isinstance(value.elt, ast.Dict):
        return value.elt
    if isinstance(value, ast.List) and len(value.elts) == 1 and isinstance(value.elts[0], ast.Dict):
        return value.elts[0]
    return None


def _under_a_branch(method: ast.FunctionDef, target: ast.AST) -> bool:
    for node in ast.walk(method):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.While, ast.Try)):
            for branch in ast.iter_child_nodes(node):
                if (
                    branch is not getattr(node, 'test', None)
                    and branch is not getattr(node, 'iter', None)
                    and (target is branch or any(target is inner for inner in ast.walk(branch)))
                ):
                    return True
    return False


def _delegates(node: ast.Call) -> list[str]:
    return [
        inner.func.attr
        for argument in node.args
        for inner in ast.walk(argument)
        if isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and isinstance(inner.func.value, ast.Name)
        and inner.func.value.id == 'self'
    ]


def _shape_facets(
    shape_kinds: tuple[ShapeKind, ...], exact: frozenset[str], back: frozenset[str]
) -> dict[str, tuple[Facet, ...]]:
    kinds = tuple(dict.fromkeys(kind.model for kind in shape_kinds))
    declared: dict[str, dict[str, str]] = {}
    for path in sorted((_ROOT / 'fastraml' / 'types').glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ClassDef) and node.name in kinds:
                declared[node.name] = _annotations(node)
    missing = [name for name in kinds if name not in declared]
    if missing:
        raise LookupError(f'kinds not found in fastraml/types: {", ".join(missing)}')

    out: dict[str, tuple[Facet, ...]] = {}
    for kind in kinds:
        facets = []
        for slot, annotation in declared[kind].items():
            if slot in _NOT_EMITTED or slot.startswith('_'):
                continue
            wire_form: Literal['annotation', 'exact_decimal', 'reference'] = (
                'exact_decimal' if slot in exact else 'reference' if slot in back else 'annotation'
            )
            facets.append(Facet(slot, annotation, wire_form))
        out[kind] = tuple(facets)
    return out


def _annotations(node: ast.ClassDef) -> dict[str, str]:
    slots = _slots(node)
    found: dict[str, str] = {}
    for statement in node.body:
        if not isinstance(statement, ast.FunctionDef) or statement.name != '__init__':
            continue
        for inner in ast.walk(statement):
            if isinstance(inner, ast.AnnAssign) and isinstance(inner.target, ast.Attribute):
                found[inner.target.attr] = ast.unparse(inner.annotation)
        for argument in statement.args.kwonlyargs:
            if argument.annotation is not None and argument.arg in slots:
                found.setdefault(argument.arg, ast.unparse(argument.annotation))
    return {slot: found[slot] for slot in slots if slot in found}


def _slots(node: ast.ClassDef) -> tuple[str, ...]:
    for statement in node.body:
        if (
            isinstance(statement, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == '__slots__' for target in statement.targets)
            and isinstance(statement.value, ast.Tuple)
        ):
            return tuple(_strings(statement.value.elts))
    return ()


def _shape_kinds() -> tuple[ShapeKind, ...]:
    value = _assigned('fastraml/types/shape.py', 'KIND_TO_CLASS')
    if not isinstance(value, ast.Dict):
        raise TypeError('fastraml/types/shape.py:KIND_TO_CLASS is not a mapping literal')
    return tuple(
        ShapeKind(key.value, model.id)
        for key, model in zip(value.keys, value.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(model, ast.Name)
    )


def _name_set(name: str) -> frozenset[str]:
    for node in ast.walk(_tree('fastraml/views/tree.py')):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            call = node.value
            arguments = call.args if isinstance(call, ast.Call) else []
            return frozenset(item for argument in arguments for item in _strings(getattr(argument, 'elts', None)))
    raise LookupError(f'{name} not found in tree.py')


@cache
def _tree(path: str) -> ast.Module:
    return ast.parse((_ROOT / path).read_text(encoding='utf-8'))


def _assigned(path: str, name: str) -> ast.expr:
    for node in _tree(path).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets) and node.value is not None:
                return node.value
    raise LookupError(f'{name} not found in {path}')


def _set_values(path: str, name: str) -> tuple[str, ...]:
    value = _assigned(path, name)
    if not isinstance(value, ast.Call) or not value.args or not isinstance(value.args[0], (ast.Set, ast.Tuple)):
        raise TypeError(f'{path}:{name} is not a set literal')
    values = [
        item.value for item in value.args[0].elts if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    return tuple(sorted(values))


def _enum_values(path: str, name: str) -> tuple[str, ...]:
    return tuple(_enum_members(path, name).values())


def _enum_members(path: str, name: str) -> dict[str, str]:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                statement.targets[0].id: statement.value.value
                for statement in node.body
                if isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            }
    raise LookupError(f'{name} not found in {path}')


def _mapped_enum_values(path: str, mapping: str, enum: str) -> tuple[str, ...]:
    value = _assigned(path, mapping)
    if not isinstance(value, ast.Dict):
        raise TypeError(f'{path}:{mapping} is not a mapping literal')
    members = _enum_members(path, enum)
    names = [key.attr for key in value.keys if isinstance(key, ast.Attribute) and isinstance(key.value, ast.Name)]
    return tuple(members[name] for name in names)


def _strings(elements: object) -> list[str]:
    if not isinstance(elements, list):
        return []
    return [item.value for item in elements if isinstance(item, ast.Constant) and isinstance(item.value, str)]
