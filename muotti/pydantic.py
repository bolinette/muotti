from collections.abc import Mapping
from typing import Any, override

import pydantic
from mirino import ExpressionNode
from peritype import TWrap
from pydantic import BaseModel, TypeAdapter
from pydantic_core import PydanticUndefined

from muotti._absence import ABSENT, Maybe
from muotti._protocol import ObjectProtocol
from muotti._spec import FieldSpec
from muotti._utils import main_class
from muotti.errors import ConversionError


class PydanticValueConverter:
    def __init__(self, *, strict: bool = False) -> None:
        self._strict = strict
        self._adapters: dict[TWrap[Any], TypeAdapter[Any]] = {}

    def _adapter(self, t: TWrap[Any]) -> TypeAdapter[Any]:
        if t not in self._adapters:
            self._adapters[t] = TypeAdapter(t.value_type)
        return self._adapters[t]

    def convert(self, value: Any, spec: FieldSpec, dest: ExpressionNode, src: ExpressionNode) -> Any:
        try:
            return self._adapter(spec.type).validate_python(value, strict=self._strict)
        except pydantic.ValidationError as err:
            details = err.errors()
            reason = str(details[0]["msg"]) if details else "invalid"
            raise ConversionError(
                f"Could not convert {value!r} to {spec.type} ({reason})",
                target=spec.type,
                dest=dest,
                src=src,
            ) from err


class PydanticProtocol(ObjectProtocol):
    priority = 90

    @override
    def matches(self, t: TWrap[Any]) -> bool:
        cls = main_class(t)
        return cls is not None and issubclass(cls, BaseModel)

    @override
    def fields(self, t: TWrap[Any]) -> dict[str, FieldSpec]:
        cls = main_class(t)
        if cls is None or not issubclass(cls, BaseModel):
            return {}
        specs: dict[str, FieldSpec] = {}
        for key, info in cls.model_fields.items():
            has_default = info.default is not PydanticUndefined or info.default_factory is not None
            specs[key] = FieldSpec.from_type(key, info.annotation, has_default=has_default)
        return specs

    @override
    def read_field(self, obj: Any, spec: FieldSpec) -> Maybe[Any]:
        if not isinstance(obj, BaseModel):
            return ABSENT

        if spec.key not in obj.model_fields_set:
            return ABSENT
        return getattr(obj, spec.key, ABSENT)

    @override
    def construct(self, t: TWrap[Any], values: Mapping[str, Any]) -> Any:
        cls = main_class(t)
        if cls is None:
            raise TypeError(f"{t} is not a pydantic model")
        return cls(**values)
