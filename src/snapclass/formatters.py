from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any, ClassVar, IO

from ruamel.yaml import YAML as RuamelYAML


def _empty_if_not_mapping(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


class FileFormatter(ABC):
    extensions: ClassVar[set[str]] = set()

    @classmethod
    @abstractmethod
    def loads(cls, text: str) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        raise NotImplementedError


class Formatter(ABC):
    """Application-level file format adapter."""

    @classmethod
    @abstractmethod
    def extensions(cls) -> set[str]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def deserialize(cls, file_object: IO[str]) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def serialize(cls, data: Any) -> str:
        raise NotImplementedError


Format = Formatter


class YAMLFormatter(FileFormatter):
    extensions = {"", ".yml", ".yaml"}

    @classmethod
    def loads(cls, text: str) -> dict[str, Any]:
        yaml = RuamelYAML()
        yaml.preserve_quotes = True
        data = yaml.load(text)
        return _empty_if_not_mapping(data)

    @classmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        yaml = RuamelYAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        stream = StringIO()
        yaml.dump(data, stream)
        text = normalize_yaml_text(stream.getvalue())
        if text == "{}\n":
            return ""
        return text


class TextFormatter(FileFormatter):
    extensions = {".txt"}

    @classmethod
    def loads(cls, text: str) -> dict[str, Any]:
        return {"content": text}

    @classmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        values = list(data.values())
        if len(values) != 1:
            raise ValueError("TextFormatter expects exactly one serialized field")
        value = values[0]
        return "" if value is None else str(value)


class TypedTextFormatter(FileFormatter):
    extensions = {".txt"}
    divider = "#-=-=-=-=-DO-NOT-EDIT-THIS-LINE-PLEASE-=-=-=-=-#"

    _types = {
        "bool": bool,
        "float": float,
        "int": int,
        "NoneType": type(None),
        "str": str,
    }

    @classmethod
    def loads(cls, text: str) -> dict[str, Any]:
        data: dict[str, Any] = {}
        current_key: str | None = None
        current_type: str | None = None
        current_value: list[str] = []

        for line in text.splitlines():
            if line == cls.divider:
                if current_key is None or current_type is None:
                    raise ValueError("Misformatted typed text: divider without field header")
                data[current_key] = cls._coerce("\n".join(current_value), current_type)
                current_key = None
                current_type = None
                current_value = []
                continue

            if current_key is None:
                try:
                    key, type_name = line.split("|", 1)
                except ValueError as exc:
                    raise ValueError(f"Misformatted typed text field header: {line!r}") from exc
                key = key.strip()
                type_name = type_name.strip()
                if not key:
                    raise ValueError("Misformatted typed text: empty field name")
                if type_name not in cls._types:
                    raise ValueError(f"Unsupported typed text field type: {type_name!r}")
                current_key = key
                current_type = type_name
                current_value = []
            else:
                current_value.append(line)

        if current_key is not None:
            raise ValueError(f"Misformatted typed text: missing divider for {current_key!r}")

        return data

    @classmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        sections: list[str] = []
        for key, value in data.items():
            type_name = type(value).__name__
            if type_name not in cls._types:
                raise ValueError(f"Unsupported typed text field type: {type_name!r}")
            rendered = "" if value is None else str(value)
            sections.append(f"{key}|{type_name}\n{rendered}\n{cls.divider}\n")
        return "".join(sections)

    @classmethod
    def _coerce(cls, value: str, type_name: str) -> Any:
        if type_name == "NoneType":
            return None
        if type_name == "bool":
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
            raise ValueError(f"Cannot parse bool typed text value: {value!r}")
        return cls._types[type_name](value)


class JSONFormatter(FileFormatter):
    extensions = {".json"}

    @classmethod
    def loads(cls, text: str) -> dict[str, Any]:
        data = json.loads(text)
        return _empty_if_not_mapping(data)

    @classmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        return json.dumps(data, indent=2) + "\n"


class JSON5Formatter(FileFormatter):
    extensions = {".json5"}

    @classmethod
    def loads(cls, text: str) -> dict[str, Any]:
        import json5

        data = json5.loads(text)
        return _empty_if_not_mapping(data)

    @classmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        import json5

        return json5.dumps(data, indent=2) + "\n"


class TOMLFormatter(FileFormatter):
    extensions = {".toml"}

    @classmethod
    def loads(cls, text: str) -> dict[str, Any]:
        import tomlkit

        data = tomlkit.loads(text)
        return _plain_mapping(data)

    @classmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        import tomlkit

        return tomlkit.dumps(data)


_DEFAULT_FILE_FORMATTERS: dict[str, type[FileFormatter]] = {
    "": YAMLFormatter,
    ".yml": YAMLFormatter,
    ".yaml": YAMLFormatter,
    ".json": JSONFormatter,
    ".json5": JSON5Formatter,
    ".toml": TOMLFormatter,
    ".txt": TextFormatter,
}


def from_formatter(formatter: type) -> type[FileFormatter]:
    class FormatterAdapter(FileFormatter):
        extensions = set(formatter.extensions())

        @classmethod
        def loads(cls, text: str) -> dict[str, Any]:
            data = formatter.deserialize(StringIO(text))
            if data is None:
                return {}
            if not isinstance(data, dict):
                return {}
            return data

        @classmethod
        def loads_path(cls, path: Path, text: str) -> dict[str, Any]:
            with path.open("r", encoding="utf-8") as file_object:
                data = formatter.deserialize(file_object)
            if data is None:
                return {}
            if not isinstance(data, dict):
                return {}
            return data

        @classmethod
        def dumps(cls, data: dict[str, Any]) -> str:
            return formatter.serialize(data)

    FormatterAdapter.__name__ = f"{formatter.__name__}FileFormatter"
    return FormatterAdapter


def normalize_formatters(
    formatters: Mapping[str, type[FileFormatter] | type[Formatter]] | None,
) -> dict[str, type[FileFormatter]]:
    if formatters is None:
        return {}
    normalized: dict[str, type[FileFormatter]] = {}
    for extension, formatter in formatters.items():
        if not isinstance(extension, str):
            raise TypeError("formatter extensions must be strings")
        normalized[extension] = as_file_formatter(formatter)
    return normalized


def as_file_formatter(
    formatter: type[FileFormatter] | type[Formatter],
) -> type[FileFormatter]:
    if not isinstance(formatter, type):
        raise TypeError("formatter must be a formatter class")
    if issubclass(formatter, FileFormatter):
        return formatter
    if issubclass(formatter, Formatter):
        return from_formatter(formatter)
    raise TypeError("formatter must be a Formatter subclass")


def formatter_for(
    path: Path,
    explicit: type[FileFormatter] | None = None,
    *,
    formatters: Mapping[str, type[FileFormatter]] | None = None,
) -> type[FileFormatter]:
    if explicit is not None:
        return explicit
    suffix = path.suffix
    if formatters is not None and suffix in formatters:
        return formatters[suffix]
    if suffix in _DEFAULT_FILE_FORMATTERS:
        return _DEFAULT_FILE_FORMATTERS[suffix]
    raise ValueError(f"Unsupported file extension: {suffix!r}")


class JSON(Formatter):
    @classmethod
    def extensions(cls) -> set[str]:
        return {".json"}

    @classmethod
    def deserialize(cls, file_object: IO[str]) -> dict[str, Any]:
        return _empty_if_not_mapping(json.load(file_object))

    @classmethod
    def serialize(cls, data: Any) -> str:
        return json.dumps(data, indent=2)


class JSON5(Formatter):
    @classmethod
    def extensions(cls) -> set[str]:
        return {".json5"}

    @classmethod
    def deserialize(cls, file_object: IO[str]) -> dict[str, Any]:
        import json5

        return _empty_if_not_mapping(json5.load(file_object))

    @classmethod
    def serialize(cls, data: Any) -> str:
        import json5

        return json5.dumps(data, indent=2)


class TOML(Formatter):
    @classmethod
    def extensions(cls) -> set[str]:
        return {".toml"}

    @classmethod
    def deserialize(cls, file_object: IO[str]) -> dict[str, Any]:
        import tomlkit

        return _plain_mapping(tomlkit.loads(file_object.read()))

    @classmethod
    def serialize(cls, data: Any) -> str:
        import tomlkit

        return tomlkit.dumps(data)


class YAML(Formatter):
    @classmethod
    def extensions(cls) -> set[str]:
        return {"", ".yml", ".yaml"}

    @classmethod
    def deserialize(cls, file_object: IO[str]) -> dict[str, Any]:
        yaml = RuamelYAML()
        yaml.preserve_quotes = True
        return _empty_if_not_mapping(yaml.load(file_object))

    @classmethod
    def serialize(cls, data: Any) -> str:
        yaml = RuamelYAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        stream = StringIO()
        yaml.dump(data, stream)
        text = normalize_yaml_text(stream.getvalue())
        if text == "{}\n":
            return ""
        return text


_DEFAULT_FORMATTERS: dict[str, type[Formatter]] = {
    "": YAML,
    ".yml": YAML,
    ".yaml": YAML,
    ".json": JSON,
    ".json5": JSON5,
    ".toml": TOML,
}


def deserialize(
    path: Path,
    extension: str,
    *,
    formatter: type[Formatter] | None = None,
) -> dict[str, Any]:
    formatter = formatter or _formatter_adapter_for(extension)
    with path.open("r", encoding="utf-8") as file_object:
        return _empty_if_not_mapping(formatter.deserialize(file_object))


def serialize(
    data: Any,
    extension: str = ".yml",
    *,
    formatter: type[Formatter] | None = None,
) -> str:
    formatter = formatter or _formatter_adapter_for(extension)
    return formatter.serialize(data)


def _formatter_adapter_for(extension: str) -> type[Formatter]:
    if extension in _DEFAULT_FORMATTERS:
        return _DEFAULT_FORMATTERS[extension]
    raise ValueError(f"Unsupported file extension: {extension!r}")


def _plain_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain_mapping(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_mapping(item) for item in value]
    return value


def normalize_yaml_text(text: str) -> str:
    text = text.replace("- \n", "-\n")
    if text.startswith("  -"):
        lines = (
            line[2:] if line.startswith("  ") else line
            for line in text.splitlines()
        )
        return "\n".join(lines) + "\n"
    return text


_normalize_yaml_text = normalize_yaml_text
