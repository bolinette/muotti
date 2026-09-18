import datetime
import decimal
import pathlib
import uuid
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from enum import Enum
from types import NoneType
from typing import Any, Final, Protocol, cast

from mirino import ExpressionNode
from peritype import TWrap, wrap_type

from muotti._spec import FieldSpec
from muotti._utils import dict_value_type, element_type, main_class
from muotti.errors import ConversionError

_TRUE_STRINGS: Final = frozenset({"true", "t", "yes", "y", "on", "1"})
_FALSE_STRINGS: Final = frozenset({"false", "f", "no", "n", "off", "0"})
_SEQUENCE_TYPES: Final = (list, tuple, set, frozenset)


class ValueConverter(Protocol):
    def convert(self, value: Any, spec: FieldSpec, dest: ExpressionNode, src: ExpressionNode) -> Any: ...


class _CoercionError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BasicValueConverter:
    def __init__(self, *, strict: bool = False) -> None:
        self._strict = strict

    def convert(self, value: Any, spec: FieldSpec, dest: ExpressionNode, src: ExpressionNode) -> Any:
        try:
            return self._coerce(value, spec.type)
        except _CoercionError as err:
            raise ConversionError(
                f"Could not convert {value!r} to {spec.type} ({err.reason})",
                target=spec.type,
                dest=dest,
                src=src,
            ) from None

    def _coerce(self, value: Any, t: TWrap[Any]) -> Any:
        if t.match(Any).is_exact:
            return value
        if value is None:
            if t.nullable:
                return None
            raise _CoercionError("value is None")
        if t.union:
            return self._coerce_union(value, t)

        cls = main_class(t)
        if cls is None or cls is NoneType:
            raise _CoercionError(f"no concrete type to convert to in {t}")

        if issubclass(cls, Mapping):
            return self._coerce_mapping(value, t, cast(type[Any], cls))
        if issubclass(cls, _SEQUENCE_TYPES):
            return self._coerce_sequence(value, t, cast(type[Any], cls))
        return self._coerce_scalar(value, cls)

    def _coerce_union(self, value: Any, t: TWrap[Any]) -> Any:
        members = [wrap_type(node.origin) for node in t.nodes if node.inner_type is not NoneType]
        for member in members:
            if member.is_type_of(value):
                try:
                    return self._coerce(value, member)
                except _CoercionError:
                    pass
        for member in members:
            try:
                return self._coerce(value, member)
            except _CoercionError:
                pass
        raise _CoercionError(f"no member of {t} accepted the value")

    def _coerce_mapping(self, value: Any, t: TWrap[Any], cls: type[Any]) -> Any:
        if not isinstance(value, Mapping):
            raise _CoercionError("expected a mapping")
        items = cast(Mapping[Any, Any], value)
        value_t = dict_value_type(t)
        converted = {key: self._coerce(item, value_t) for key, item in items.items()}
        if isinstance(value, cls):
            return converted
        try:
            return cls(converted)
        except (TypeError, ValueError) as err:
            raise _CoercionError(f"{cls.__name__}() rejected the mapping: {err}") from None

    def _coerce_sequence(self, value: Any, t: TWrap[Any], cls: type[Any]) -> Any:
        if isinstance(value, (str, bytes)) or not isinstance(value, (Sequence, AbstractSet)):
            raise _CoercionError("expected a sequence")
        items: Sequence[Any] = list(cast("Sequence[Any] | AbstractSet[Any]", value))
        elem_t = element_type(t)
        converted = [self._coerce(item, elem_t) for item in items] if elem_t is not None else items
        try:
            return cls(converted)
        except (TypeError, ValueError) as err:
            raise _CoercionError(f"{cls.__name__}() rejected the elements: {err}") from None

    def _coerce_scalar(self, value: Any, cls: type[Any]) -> Any:
        if cls is bool:
            return self._to_bool(value)
        if isinstance(value, cls) and not isinstance(value, bool):
            return value
        if self._strict:
            raise _CoercionError(f"expected {cls.__name__}, got {type(value).__name__}")
        return self._convert_scalar(value, cls)

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if self._strict:
            raise _CoercionError(f"expected bool, got {type(value).__name__}")
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE_STRINGS:
                return True
            if lowered in _FALSE_STRINGS:
                return False
            raise _CoercionError(f"{value!r} is not a boolean")
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise _CoercionError(f"expected bool, got {type(value).__name__}")

    def _convert_scalar(self, value: Any, cls: type[Any]) -> Any:
        if issubclass(cls, Enum):
            try:
                return cls(value)
            except ValueError:
                raise _CoercionError(f"{value!r} is not a member of {cls.__name__}") from None
        if issubclass(cls, (int, float, complex, decimal.Decimal)) and isinstance(
            value, (int, float, str, decimal.Decimal)
        ):
            return self._call(cls, value)
        if issubclass(cls, str) and isinstance(value, bytes):
            return self._call(cls, value.decode())
        if issubclass(cls, bytes) and isinstance(value, str):
            return self._call(cls, value.encode())
        if issubclass(cls, (uuid.UUID, pathlib.PurePath)) and isinstance(value, str):
            return self._call(cls, value)
        if issubclass(cls, datetime.timedelta) and isinstance(value, (int, float)):
            return datetime.timedelta(seconds=value)
        if issubclass(cls, (datetime.datetime, datetime.date, datetime.time)) and isinstance(value, str):
            return self._from_isoformat(cls, value)
        raise _CoercionError(f"expected {cls.__name__}, got {type(value).__name__}")

    @staticmethod
    def _call(target: type[Any], value: Any) -> Any:
        try:
            return target(value)
        except (TypeError, ValueError, ArithmeticError) as err:
            raise _CoercionError(str(err)) from None

    @staticmethod
    def _from_isoformat(target: type[Any], value: str) -> Any:
        try:
            return target.fromisoformat(value)
        except ValueError as err:
            raise _CoercionError(str(err)) from None
