from __future__ import annotations

import dataclasses
import enum
import sys
import types
from collections.abc import Iterable, Mapping
from typing import Any, get_args, get_origin

from ruamel.yaml.scalarstring import LiteralScalarString

from . import sessions


class Optional:
    @classmethod
    def to_python_value(cls, deserialized_data: Any, **kwargs: Any) -> Any:
        if deserialized_data is None:
            return None
        return super().to_python_value(deserialized_data, **kwargs)  # type: ignore[misc]

    @classmethod
    def to_preserialization_data(cls, python_value: Any, **kwargs: Any) -> Any:
        if python_value is None:
            return None
        return super().to_preserialization_data(python_value, **kwargs)  # type: ignore[misc]


class Serializer:
    TYPE: type = object
    DEFAULT: Any = None

    @classmethod
    def as_generic(cls, subtypes: list[type["Serializer"]]) -> type["Serializer"]:
        name = "Generic" + "".join(subtype.__name__ for subtype in subtypes) + cls.__name__
        return type(name, (cls,), {"SERIALIZERS": subtypes})

    @classmethod
    def as_optional(cls) -> type["Serializer"]:
        return type(f"Optional{cls.__name__}", (Optional, cls), {"DEFAULT": None})

    @classmethod
    def to_python_value(cls, deserialized_data: Any, **kwargs: Any) -> Any:
        return cls.to_preserialization_data(deserialized_data, **kwargs)

    @classmethod
    def to_preserialization_data(cls, python_value: Any, **_kwargs: Any) -> Any:
        if python_value is None:
            return cls.DEFAULT
        if cls.TYPE is object:
            return python_value
        return cls.TYPE(python_value)


class Boolean(Serializer):
    TYPE = bool
    DEFAULT = False
    _FALSY = {"false", "f", "no", "n", "disabled", "off", "0"}

    @classmethod
    def to_python_value(cls, deserialized_data: Any, **_kwargs: Any) -> bool:
        if isinstance(deserialized_data, str):
            return deserialized_data.strip().lower() not in cls._FALSY
        return bool(deserialized_data)

    @classmethod
    def to_preserialization_data(cls, python_value: Any, **_kwargs: Any) -> bool:
        return bool(python_value)


class Integer(Serializer):
    TYPE = int
    DEFAULT = 0

    @classmethod
    def to_python_value(cls, deserialized_data: Any, **_kwargs: Any) -> int:
        return cls.to_preserialization_data(deserialized_data)

    @classmethod
    def to_preserialization_data(cls, python_value: Any, **_kwargs: Any) -> int:
        value = python_value or 0
        try:
            return int(value)
        except ValueError as exc:
            try:
                return int(float(value))
            except ValueError:
                raise exc from None


class Float(Serializer):
    TYPE = float
    DEFAULT = 0.0

    @classmethod
    def to_python_value(cls, deserialized_data: Any, **_kwargs: Any) -> float:
        return float(deserialized_data or 0.0)

    @classmethod
    def to_preserialization_data(cls, python_value: Any, **_kwargs: Any) -> float:
        return float(python_value or 0.0)


class Number(Float):
    @classmethod
    def to_preserialization_data(cls, python_value: Any, **_kwargs: Any) -> int | float:
        value = super().to_preserialization_data(python_value)
        if int(value) == value:
            return int(value)
        return value


class String(Serializer):
    TYPE = str
    DEFAULT = ""

    @classmethod
    def to_python_value(cls, deserialized_data: Any, **_kwargs: Any) -> str:
        return "" if deserialized_data is None else str(deserialized_data)

    @classmethod
    def to_preserialization_data(cls, python_value: Any, **_kwargs: Any) -> str:
        return "" if python_value is None else str(python_value)


class Text(String):
    @classmethod
    def to_python_value(cls, deserialized_data: Any, **_kwargs: Any) -> str:
        value = cls.to_preserialization_data(deserialized_data).strip()
        if "\n" in value:
            value += "\n"
        return value

    @classmethod
    def to_preserialization_data(cls, python_value: Any, **_kwargs: Any) -> str:
        value = super().to_preserialization_data(python_value).strip()
        if "\n" in value:
            return LiteralScalarString(value + "\n")
        return value


class List(Serializer):
    SERIALIZER: type[Serializer] = Serializer

    @classmethod
    def of_type(cls, serializer: type[Serializer]) -> type["List"]:
        return type(f"{serializer.__name__}{cls.__name__}", (cls,), {"SERIALIZER": serializer})

    @classmethod
    def to_python_value(cls, deserialized_data: Any, *, target_object: Any = None) -> list[Any]:
        value: list[Any]
        if target_object is None:
            value = []
        else:
            value = target_object
            value.clear()

        convert = cls.SERIALIZER.to_python_value
        if deserialized_data is None:
            return value
        if isinstance(deserialized_data, Iterable) and not isinstance(deserialized_data, str):
            items = list(deserialized_data)
            if all(item is None for item in items):
                return value
            value.extend(convert(item, target_object=None) for item in items)
            return value
        if isinstance(deserialized_data, str):
            value.extend(convert(item, target_object=None) for item in deserialized_data.split(","))
            return value
        value.append(convert(deserialized_data, target_object=None))
        return value

    @classmethod
    def to_preserialization_data(
        cls,
        python_value: Any,
        *,
        default_to_skip: Any = None,
        minimal_diffs: bool | None = None,
        **_kwargs: Any,
    ) -> list[Any]:
        data: list[Any] = []
        convert = cls.SERIALIZER.to_preserialization_data

        if python_value is None:
            pass
        elif isinstance(python_value, Iterable) and not isinstance(python_value, str):
            items = sorted(python_value, key=str) if isinstance(python_value, set) else python_value
            data.extend(convert(item, default_to_skip=None) for item in items)
        else:
            data.append(convert(python_value, default_to_skip=None))

        if data == default_to_skip:
            data.clear()
        if minimal_diffs is None:
            minimal_diffs = sessions.MINIMAL_DIFFS
        if minimal_diffs:
            return data or [None]
        return data


class Set(List):
    @classmethod
    def to_python_value(cls, deserialized_data: Any, *, target_object: Any = None) -> set[Any]:
        value: set[Any]
        if target_object is None:
            value = set()
        else:
            value = target_object
            value.clear()

        convert = cls.SERIALIZER.to_python_value
        if deserialized_data is None:
            return value
        if isinstance(deserialized_data, Iterable) and not isinstance(deserialized_data, str):
            items = list(deserialized_data)
            if all(item is None for item in items):
                return value
            value.update(convert(item, target_object=None) for item in items)
            return value
        if isinstance(deserialized_data, str):
            value.update(convert(item, target_object=None) for item in deserialized_data.split(","))
            return value
        value.add(convert(deserialized_data, target_object=None))
        return value


class Dictionary(Serializer):
    @classmethod
    def of_mapping(cls, key: Any, value: Any) -> type["Dictionary"]:
        key_name = getattr(key, "__name__", "Any")
        value_name = getattr(value, "__name__", "Any")
        return type(f"{key_name}{value_name}Dict", (cls,), {})

    @classmethod
    def to_python_value(cls, deserialized_data: Any, *, target_object: Any = None) -> dict[Any, Any]:
        data = dict(deserialized_data) if isinstance(deserialized_data, Mapping) else {}
        if target_object is None:
            return data
        target_object.clear()
        target_object.update(data)
        return target_object

    @classmethod
    def to_preserialization_data(cls, python_value: Any, *, default_to_skip: Any = None, **_kwargs: Any) -> dict[Any, Any]:
        data = dict(python_value) if python_value else {}
        if data == default_to_skip:
            data.clear()
        return data


class Dataclass(Serializer):
    DATACLASS: type = object
    SERIALIZERS: dict[str, type[Serializer]] = {}

    @classmethod
    def of_mappings(cls, dataclass: type, serializers: dict[str, type[Serializer]]) -> type["Dataclass"]:
        return type(
            f"{dataclass.__name__}Serializer",
            (cls,),
            {"DATACLASS": dataclass, "SERIALIZERS": serializers},
        )

    @classmethod
    def to_python_value(cls, deserialized_data: Any, *, target_object: Any = None) -> Any:
        if dataclasses.is_dataclass(deserialized_data) and not isinstance(deserialized_data, type):
            data = dataclasses.asdict(deserialized_data)
        elif isinstance(deserialized_data, Mapping):
            data = dict(deserialized_data)
        else:
            data = {}

        kwargs: dict[str, Any] = {}
        fields = {field.name: field for field in dataclasses.fields(cls.DATACLASS)}
        for name, serializer in cls.SERIALIZERS.items():
            if name in data:
                kwargs[name] = serializer.to_python_value(data[name], target_object=None)
            else:
                kwargs[name] = _default_for_field(fields[name])
        value = cls.DATACLASS(**kwargs)
        if target_object is not None:
            target_object.__dict__.update(value.__dict__)
            return target_object
        return value

    @classmethod
    def to_preserialization_data(cls, python_value: Any, *, default_to_skip: Any = None, **_kwargs: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for name, serializer in cls.SERIALIZERS.items():
            if isinstance(python_value, Mapping):
                value = python_value.get(name)
            else:
                value = getattr(python_value, name, None)
            if default_to_skip is not None and value == getattr(default_to_skip, name, object()):
                continue
            data[name] = serializer.to_preserialization_data(value, default_to_skip=None)
        return data


class Enumeration(Serializer):
    ENUM: type[enum.Enum] = enum.Enum

    @classmethod
    def of_type(cls, enum_type: type[enum.Enum]) -> type["Enumeration"]:
        return type(f"{enum_type.__name__}Serializer", (cls,), {"ENUM": enum_type})

    @classmethod
    def to_python_value(cls, deserialized_data: Any, **_kwargs: Any) -> enum.Enum:
        return cls.ENUM(deserialized_data)

    @classmethod
    def to_preserialization_data(cls, python_value: enum.Enum, **_kwargs: Any) -> Any:
        return python_value.value


_DEFAULT_SERIALIZERS: dict[type | str, type[Serializer]] = {
    bool: Boolean,
    "bool": Boolean,
    int: Integer,
    "int": Integer,
    float: Float,
    "float": Float,
    "Number": Number,
    str: String,
    "str": String,
    "Text": Text,
    list: List,
    "list": List,
    dict: Dictionary,
    "dict": Dictionary,
    set: Set,
    "set": Set,
}


def normalize_serializers(
    serializers: Mapping[type | str, type[Serializer]] | None,
) -> dict[type | str, type[Serializer]]:
    if serializers is None:
        return {}
    normalized: dict[type | str, type[Serializer]] = {}
    for type_, serializer in serializers.items():
        if not isinstance(type_, (type, str)):
            raise TypeError("serializer keys must be types or strings")
        if not isinstance(serializer, type) or not issubclass(serializer, Serializer):
            raise TypeError("serializer values must be Serializer subclasses")
        normalized[type_] = serializer
        if isinstance(type_, type):
            normalized[type_.__name__] = serializer
    return normalized


def serializer_for_hint(
    hint: Any,
    *,
    serializers: Mapping[type | str, type[Serializer]] | None = None,
) -> type[Serializer] | None:
    if isinstance(hint, str):
        hint = _normalize_string_hint(hint)
        if not isinstance(hint, str):
            return serializer_for_hint(hint, serializers=serializers)
        if serializers is not None and hint in serializers:
            return serializers[hint]
        if hint in _DEFAULT_SERIALIZERS:
            return _DEFAULT_SERIALIZERS[hint]
        return None
    if isinstance(hint, type) and issubclass(hint, Serializer):
        return hint
    origin = get_origin(hint)
    if origin is not None and _contains_serializer_annotation(hint):
        try:
            return map_type(hint)
        except TypeError:
            return None
    if isinstance(hint, type):
        if serializers is not None and hint in serializers:
            return serializers[hint]
        if hint in _DEFAULT_SERIALIZERS and hint not in {list, dict, set}:
            return _DEFAULT_SERIALIZERS[hint]
        local_items = list(serializers.items()) if serializers is not None else []
        for type_, serializer in reversed(local_items):
            if (
                isinstance(type_, type)
                and type_ not in {list, dict, set, int}
                and issubclass(hint, type_)
            ):
                return serializer
    return None


def map_type(cls: Any, *, name: str = "", item_cls: type | None = None) -> type[Serializer]:
    if cls in _DEFAULT_SERIALIZERS:
        return _DEFAULT_SERIALIZERS[cls]
    if dataclasses.is_dataclass(cls):
        return Dataclass.of_mappings(
            cls,
            {
                field.name: map_type(field.type, name=field.name)
                for field in dataclasses.fields(cls)
                if field.init
            },
        )
    if isinstance(cls, types.UnionType):
        return _map_union(get_args(cls))

    origin = get_origin(cls)
    args = get_args(cls)
    if origin is not None:
        if origin is list:
            if not args and item_cls is None:
                raise TypeError("Type is required with 'List' annotation")
            return List.of_type(map_type(item_cls or args[0]))
        if origin is set:
            if not args and item_cls is None:
                raise TypeError("Type is required with 'Set' annotation")
            return Set.of_type(map_type(item_cls or args[0]))
        if isinstance(origin, type) and issubclass(origin, Mapping):
            if item_cls is not None:
                return Dictionary.of_mapping(String, map_type(item_cls))
            if len(args) < 2:
                raise TypeError("Types are required with 'Dict' annotation")
            return Dictionary.of_mapping(map_type(args[0]), map_type(args[1]))
        if origin in {types.UnionType, getattr(types, "UnionType", object())}:
            return _map_union(args)
        if str(origin) == "typing.Union":
            return _map_union(args)
        if isinstance(origin, type) and issubclass(origin, Serializer):
            return origin.as_generic([map_type(arg) for arg in args])
        raise TypeError(f"Unsupported container type: {origin}")

    if isinstance(cls, str):
        serializer = serializer_for_hint(cls)
        if serializer is None:
            raise TypeError(f"Annotation is not a type: {cls!r}")
        return serializer
    if not isinstance(cls, type):
        raise TypeError(f"Annotation is not a type: {cls!r}")
    if issubclass(cls, Serializer):
        return cls
    if issubclass(cls, enum.Enum):
        return Enumeration.of_type(cls)
    if issubclass(cls, dict):
        return Dictionary.of_mapping(String, Any)
    raise TypeError(f"Could not map type: {cls}")


def _map_union(args: tuple[Any, ...]) -> type[Serializer]:
    non_none = [arg for arg in args if arg is not type(None)]
    if len(non_none) == 1 and len(non_none) != len(args):
        return map_type(non_none[0]).as_optional()
    if str in args:
        return map_type(str)
    if set(args) == {int, float}:
        return Number
    raise TypeError(f"Unsupported union type: {args}")


def _default_for_field(field: dataclasses.Field[Any]) -> Any:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:  # type: ignore[comparison-overlap]
        return field.default_factory()  # type: ignore[misc]
    return None


def _normalize_string_hint(hint: str) -> str:
    normalized = hint.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        return normalized[1:-1]
    return normalized


def _contains_serializer_annotation(hint: Any) -> bool:
    origin = get_origin(hint)
    if isinstance(origin, type) and issubclass(origin, Serializer):
        return True
    if isinstance(hint, type) and issubclass(hint, Serializer):
        return True
    return any(_contains_serializer_annotation(arg) for arg in get_args(hint))


def _install_serializer_submodule(
    suffix: str, names: dict[str, type[Serializer]]
) -> None:
    module = types.ModuleType(f"snapclass.serializers.{suffix}")
    module.__dict__.update(names)
    module.__all__ = tuple(names)
    sys.modules[module.__name__] = module
    setattr(sys.modules[__name__], suffix, module)


_install_serializer_submodule("_bases", {"Serializer": Serializer})
_install_serializer_submodule(
    "builtins",
    {
        "Boolean": Boolean,
        "Float": Float,
        "Integer": Integer,
        "String": String,
    },
)
_install_serializer_submodule(
    "containers",
    {
        "Dataclass": Dataclass,
        "Dictionary": Dictionary,
        "List": List,
        "Set": Set,
    },
)
_install_serializer_submodule("enumerations", {"Enumeration": Enumeration})
_install_serializer_submodule("extensions", {"Number": Number, "Text": Text})
