from __future__ import annotations

import json
import math
import re
from typing import Any

MAX_DEPTH = 64
LINE_LIMIT = 80

_SAFE_ID = re.compile(r"[A-Za-z_./][A-Za-z0-9_\-./@]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_RESERVED_STRINGS = {"T", "F", "~", "{}", "[]"}


class TerseFormatError(ValueError):
    pass


def loads(text: str) -> Any:
    text = text.strip()
    if not text:
        return {}
    if _looks_like_document(text):
        return parse_document(text)
    return parse_value(text)


def dumps(data: Any) -> str:
    if isinstance(data, dict):
        return serialize_document(data)
    return serialize_value(data) + "\n"


def parse_document(text: str) -> dict[str, Any]:
    parser = _Parser(text)
    parser.skip_ws_lines()
    data: dict[str, Any] = {}
    while not parser.eof():
        key = parser.parse_key()
        parser.skip_hws()
        parser.expect(":")
        parser.skip_hws()
        if parser.cur() in {"\n", "\r"}:
            parser.skip_ws_lines()
        value = parser.parse_value()
        if key in data:
            raise TerseFormatError(f"Duplicate TERSE key: {key!r}")
        data[key] = value
        parser.skip_ws_lines()
    return data


def parse_value(text: str) -> Any:
    parser = _Parser(text)
    parser.skip_ws_lines()
    value = parser.parse_value()
    parser.skip_ws_lines()
    if not parser.eof():
        raise TerseFormatError(f"Unexpected TERSE content at position {parser.pos}")
    return value


def serialize_document(data: dict[str, Any]) -> str:
    if not data:
        return ""
    lines: list[str] = []
    for key, value in data.items():
        rendered_key = _serialize_key(key)
        schema_keys = _schema_keys(value) if isinstance(value, list) else None
        if schema_keys is not None:
            lines.append(
                f"{rendered_key}:\n{_indent(_serialize_schema_array(value, schema_keys), '  ')}"
            )
            continue

        inline = _try_inline(value)
        if inline is not None and len(rendered_key) + len(inline) + 2 <= LINE_LIMIT:
            lines.append(f"{rendered_key}: {inline}")
        else:
            lines.append(f"{rendered_key}:\n{_indent(serialize_value(value, 1), '  ')}")
    return "\n".join(lines) + "\n"


def serialize_value(value: Any, depth: int = 0) -> str:
    if depth > MAX_DEPTH:
        raise TerseFormatError("Maximum TERSE nesting depth exceeded")
    if _is_primitive(value):
        return _serialize_primitive(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        schema_keys = _schema_keys(value)
        if schema_keys is not None:
            return _serialize_schema_array(value, schema_keys)
        inline = _try_inline(value, depth)
        if inline is not None and len(inline) <= LINE_LIMIT:
            return inline
        indent = " " * (depth + 1)
        close_indent = " " * depth
        items = [f"{indent}{serialize_value(item, depth + 1)}" for item in value]
        return "[\n" + "\n".join(items) + f"\n{close_indent}]"
    if isinstance(value, dict):
        _validate_mapping_keys(value)
        if not value:
            return "{}"
        inline = _try_inline(value, depth)
        if inline is not None and len(inline) <= LINE_LIMIT:
            return inline
        indent = " " * (depth + 1)
        close_indent = " " * depth
        lines = []
        for key, item in value.items():
            rendered = serialize_value(item, depth + 1)
            rendered = rendered.replace("\n", "\n" + indent)
            lines.append(f"{indent}{_serialize_key(key)}: {rendered}")
        return "{\n" + "\n".join(lines) + f"\n{close_indent}}}"
    raise TerseFormatError(f"Cannot serialize {type(value).__name__} as TERSE")


def _looks_like_document(text: str) -> bool:
    parser = _Parser(text)
    parser.skip_ws_lines()
    return parser.is_kv_start()


def _is_safe_id(value: str) -> bool:
    return (
        bool(_SAFE_ID.fullmatch(value))
        and value not in _RESERVED_STRINGS
        and _NUMBER.fullmatch(value) is None
    )


def _serialize_key(value: Any) -> str:
    if not isinstance(value, str):
        raise TerseFormatError("TERSE object keys must be strings")
    return _serialize_string(value)


def _serialize_string(value: str) -> str:
    if _is_safe_id(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _serialize_primitive(value: Any) -> str:
    if value is None:
        return "~"
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TerseFormatError(f"Cannot serialize non-finite TERSE number: {value}")
        return repr(value)
    if isinstance(value, str):
        return _serialize_string(value)
    raise TerseFormatError(f"Cannot serialize {type(value).__name__} as a TERSE primitive")


def _schema_keys(value: Any) -> list[str] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    if not all(isinstance(item, dict) for item in value):
        return None
    keys = list(value[0])
    if not keys or not all(isinstance(key, str) for key in keys):
        return None
    for item in value:
        if list(item) != keys:
            return None
        if not all(_is_primitive(child) for child in item.values()):
            return None
    return keys


def _serialize_schema_array(values: list[dict[str, Any]], keys: list[str]) -> str:
    header = "#[" + " ".join(_serialize_key(key) for key in keys) + "]"
    rows = [
        "  " + " ".join(_serialize_primitive(item[key]) for key in keys)
        for item in values
    ]
    return "\n".join([header, *rows])


def _try_inline(value: Any, depth: int = 0) -> str | None:
    if depth > MAX_DEPTH:
        return None
    if _is_primitive(value):
        return _serialize_primitive(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if _schema_keys(value) is not None:
            return None
        items = [_try_inline(item, depth + 1) for item in value]
        if any(item is None for item in items):
            return None
        return "[" + " ".join(item for item in items if item is not None) + "]"
    if isinstance(value, dict):
        _validate_mapping_keys(value)
        if not value:
            return "{}"
        pieces: list[str] = []
        for key, item in value.items():
            rendered = _try_inline(item, depth + 1)
            if rendered is None:
                return None
            pieces.append(f"{_serialize_key(key)}:{rendered}")
        return "{" + " ".join(pieces) + "}"
    return None


def _validate_mapping_keys(value: dict[Any, Any]) -> None:
    for key in value:
        if not isinstance(key, str):
            raise TerseFormatError("TERSE object keys must be strings")


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


class _Parser:
    def __init__(self, text: str) -> None:
        tab = text.find("\t")
        if tab >= 0:
            raise TerseFormatError(f"Tab character is not allowed in TERSE at position {tab}")
        self.text = text
        self.pos = 0
        self.depth = 0

    def eof(self) -> bool:
        return self.pos >= len(self.text)

    def cur(self) -> str:
        return "" if self.eof() else self.text[self.pos]

    def peek(self, offset: int = 1) -> str:
        index = self.pos + offset
        return "" if index >= len(self.text) else self.text[index]

    def expect(self, value: str) -> None:
        if self.cur() != value:
            got = "EOF" if self.eof() else repr(self.cur())
            raise TerseFormatError(f"Expected {value!r} at TERSE position {self.pos}, got {got}")
        self.pos += 1

    def skip_hws(self) -> None:
        while self.cur() == " ":
            self.pos += 1

    def skip_ws_lines(self) -> None:
        while not self.eof():
            if self.cur() in {" ", "\n", "\r"}:
                self.pos += 1
            elif self.cur() == "/" and self.peek() == "/":
                self.skip_comment()
            else:
                break

    def skip_comment(self) -> None:
        while not self.eof() and self.cur() not in {"\n", "\r"}:
            self.pos += 1

    def is_kv_start(self) -> bool:
        saved = self.pos
        try:
            if self.cur() == '"':
                self.parse_quoted_string()
            elif _safe_id_start(self.cur()):
                self.parse_safe_id()
            else:
                return False
            self.skip_hws()
            return self.cur() == ":"
        finally:
            self.pos = saved

    def parse_key(self) -> str:
        if self.cur() == '"':
            return self.parse_quoted_string()
        return self.parse_safe_id()

    def parse_value(self) -> Any:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise TerseFormatError("Maximum TERSE nesting depth exceeded")
        try:
            self.skip_hws()
            current = self.cur()
            if not current:
                raise TerseFormatError("Unexpected end of TERSE input")
            if current == "~":
                self.pos += 1
                return None
            if current == '"':
                return self.parse_quoted_string()
            if current == "{":
                return self.parse_object()
            if current == "[":
                return self.parse_array()
            if current == "#":
                return self.parse_schema_array()
            if current == "-" or current.isdigit():
                return self.parse_number()
            if _safe_id_start(current):
                value = self.parse_safe_id()
                if value == "T":
                    return True
                if value == "F":
                    return False
                return value
            raise TerseFormatError(f"Unexpected TERSE character {current!r} at position {self.pos}")
        finally:
            self.depth -= 1

    def parse_primitive(self) -> Any:
        current = self.cur()
        if current in {"{", "[", "#"}:
            raise TerseFormatError(f"Expected TERSE primitive at position {self.pos}")
        return self.parse_value()

    def parse_object(self) -> dict[str, Any]:
        self.expect("{")
        data: dict[str, Any] = {}
        self.skip_ws_lines()
        while self.cur() != "}":
            if self.eof():
                raise TerseFormatError("Unterminated TERSE object")
            key = self.parse_key()
            self.skip_hws()
            self.expect(":")
            self.skip_hws()
            if self.cur() in {"\n", "\r"}:
                self.skip_ws_lines()
            value = self.parse_value()
            if key in data:
                raise TerseFormatError(f"Duplicate TERSE key: {key!r}")
            data[key] = value
            self.skip_ws_lines()
        self.expect("}")
        return data

    def parse_array(self) -> list[Any]:
        self.expect("[")
        values: list[Any] = []
        self.skip_ws_lines()
        while self.cur() != "]":
            if self.eof():
                raise TerseFormatError("Unterminated TERSE array")
            values.append(self.parse_value())
            self.skip_ws_lines()
        self.expect("]")
        return values

    def parse_schema_array(self) -> list[dict[str, Any]]:
        header_indent = self.current_line_indent()
        self.expect("#")
        self.expect("[")
        fields: list[str] = []
        self.skip_hws()
        while self.cur() != "]":
            if self.eof():
                raise TerseFormatError("Unterminated TERSE schema array header")
            fields.append(self.parse_key())
            self.skip_hws()
        self.expect("]")
        if not fields:
            raise TerseFormatError("TERSE schema arrays need at least one field")

        rows: list[dict[str, Any]] = []
        minimum_indent = header_indent if header_indent > 0 else 1
        while not self.eof() and self.cur() in {"\n", "\r"}:
            line_start = self.pos
            self.consume_newline()
            spaces = self.consume_spaces()
            if self.eof():
                break
            if self.cur() in {"\n", "\r"}:
                continue
            if self.cur() == "/" and self.peek() == "/":
                self.skip_comment()
                continue
            if spaces < minimum_indent or self.is_kv_start():
                self.pos = line_start
                break

            row: dict[str, Any] = {}
            for index, field in enumerate(fields):
                if index > 0:
                    self.require_row_space()
                row[field] = self.parse_primitive()
            self.skip_hws()
            if self.cur() not in {"", "\n", "\r"}:
                raise TerseFormatError(f"TERSE schema row has too many values at {self.pos}")
            rows.append(row)
        return rows

    def parse_safe_id(self) -> str:
        match = _SAFE_ID.match(self.text, self.pos)
        if match is None:
            raise TerseFormatError(f"Expected TERSE identifier at position {self.pos}")
        self.pos = match.end()
        return match.group(0)

    def parse_number(self) -> int | float:
        match = _NUMBER.match(self.text, self.pos)
        if match is None:
            raise TerseFormatError(f"Expected TERSE number at position {self.pos}")
        value = match.group(0)
        self.pos = match.end()
        if "." in value or "e" in value.lower():
            return float(value)
        return int(value)

    def parse_quoted_string(self) -> str:
        start = self.pos
        self.expect('"')
        escaped = False
        while not self.eof():
            current = self.cur()
            if escaped:
                escaped = False
                self.pos += 1
                continue
            if current == "\\":
                escaped = True
                self.pos += 1
                continue
            if current == '"':
                source = self.text[start : self.pos + 1]
                self.pos += 1
                try:
                    value = json.loads(source)
                except json.JSONDecodeError as exc:
                    raise TerseFormatError(f"Invalid TERSE string at position {start}") from exc
                if not isinstance(value, str):
                    raise TerseFormatError(f"Invalid TERSE string at position {start}")
                return value
            self.pos += 1
        raise TerseFormatError(f"Unterminated TERSE string at position {start}")

    def current_line_indent(self) -> int:
        line_start = max(self.text.rfind("\n", 0, self.pos), self.text.rfind("\r", 0, self.pos))
        index = 0 if line_start < 0 else line_start + 1
        indent = 0
        while index + indent < len(self.text) and self.text[index + indent] == " ":
            indent += 1
        return indent

    def consume_newline(self) -> None:
        if self.cur() == "\r" and self.peek() == "\n":
            self.pos += 2
            return
        if self.cur() in {"\n", "\r"}:
            self.pos += 1
            return
        raise TerseFormatError(f"Expected TERSE newline at position {self.pos}")

    def consume_spaces(self) -> int:
        start = self.pos
        self.skip_hws()
        return self.pos - start

    def require_row_space(self) -> None:
        if self.cur() != " ":
            raise TerseFormatError(f"Expected TERSE schema row separator at {self.pos}")
        self.skip_hws()


def _safe_id_start(value: str) -> bool:
    return bool(value) and bool(re.match(r"[A-Za-z_./]", value))
