"""검색 필터 AST.

필터를 문자열로 이어 붙이지 않고 타입이 있는 노드로 만든다. 값은 노드 안에 값으로만
들어가므로, LLM 이 도구 인자로 넣은 날짜·이름이 필터 문법을 바꿀 수 없다. 필드 이름은
인덱스가 허용한 목록과 컴파일 시점에 대조한다.

권한 필터에서 빈 `In` 은 "아무것도 허용하지 않음"이다 — 범위가 비었다고 필터를 빼면
전체가 열린다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from chatcore.errors import InvalidRequest

Scalar = str | int | float | bool | date


@dataclass(frozen=True)
class Eq:
    field: str
    value: Scalar


@dataclass(frozen=True)
class In:
    field: str
    values: tuple[Scalar, ...]


@dataclass(frozen=True)
class Range:
    field: str
    gte: Scalar | None = None
    lte: Scalar | None = None


@dataclass(frozen=True)
class And:
    items: tuple[Filter, ...]


@dataclass(frozen=True)
class Or:
    items: tuple[Filter, ...]


@dataclass(frozen=True)
class Not:
    item: Filter


Filter = Eq | In | Range | And | Or | Not

Predicate = Callable[[Mapping[str, Any]], bool]


def all_of(*items: Filter | None) -> Filter | None:
    present = tuple(f for f in items if f is not None)
    if not present:
        return None
    return present[0] if len(present) == 1 else And(present)


def any_of(*items: Filter) -> Filter:
    return items[0] if len(items) == 1 else Or(tuple(items))


def in_(field: str, values: Iterable[Scalar]) -> In:
    return In(field, tuple(values))


def fields_of(f: Filter) -> set[str]:
    match f:
        case Eq(field=name) | In(field=name) | Range(field=name):
            return {name}
        case And(items=items) | Or(items=items):
            return set().union(*(fields_of(i) for i in items))
        case Not(item=item):
            return fields_of(item)


def _coerce(doc_value: Any, like: Scalar) -> Any:
    if isinstance(like, date) and isinstance(doc_value, str):
        return date.fromisoformat(doc_value[:10])
    return doc_value


def _matches_value(doc_value: Any, value: Scalar) -> bool:
    if isinstance(doc_value, list | tuple | set):
        return any(_coerce(v, value) == value for v in doc_value)
    return _coerce(doc_value, value) == value


def compile_predicate(f: Filter | None, allowed_fields: Iterable[str]) -> Predicate:
    if f is None:
        return lambda _doc: True
    unknown = fields_of(f) - set(allowed_fields)
    if unknown:
        raise InvalidRequest(f"filter on non-filterable fields: {sorted(unknown)}")
    return _compile(f)


def _compile(f: Filter) -> Predicate:
    match f:
        case Eq(field=name, value=value):
            return lambda d: name in d and _matches_value(d[name], value)
        case In(field=name, values=values):
            return lambda d: name in d and any(_matches_value(d[name], v) for v in values)
        case Range(field=name, gte=gte, lte=lte):

            def in_range(d: Mapping[str, Any]) -> bool:
                if d.get(name) is None:
                    return False
                like = gte if gte is not None else lte
                v = _coerce(d[name], like) if like is not None else d[name]
                return (gte is None or v >= gte) and (lte is None or v <= lte)

            return in_range
        case And(items=items):
            preds = [_compile(i) for i in items]
            return lambda d: all(p(d) for p in preds)
        case Or(items=items):
            preds = [_compile(i) for i in items]
            return lambda d: any(p(d) for p in preds)
        case Not(item=item):
            pred = _compile(item)
            return lambda d: not pred(d)
