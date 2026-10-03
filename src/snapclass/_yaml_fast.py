from __future__ import annotations

import copy
import dataclasses
import importlib
import importlib.metadata
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ruamel.yaml import YAML as RuamelYAML
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    SingleQuotedScalarString,
)


_INTEGER = re.compile(r"[-+]?(?:0|[1-9][0-9]*)\Z")
_FLOAT = re.compile(
    r"[-+]?(?:(?:0|[1-9][0-9]*)\.[0-9]+)(?:[eE][-+]?[0-9]+)?\Z"
)
_LINE_ENDING = re.compile(r"\r\n|\n|\r")


def _load_tree_sitter() -> tuple[Any, Any]:
    try:
        tree_sitter = importlib.import_module("tree_sitter")
        tree_sitter_yaml = importlib.import_module("tree_sitter_yaml")
        language = tree_sitter.Language(tree_sitter_yaml.language())
    except Exception:
        return None, None
    return tree_sitter.Parser, language


_PARSER_TYPE, _LANGUAGE = _load_tree_sitter()


def _detect_available() -> bool:
    if _LANGUAGE is None or _PARSER_TYPE is None:
        return False
    try:
        yaml = RuamelYAML(typ="safe")
    except Exception:
        return False
    parser = getattr(yaml, "Parser", None)
    return parser is not None and ".clib." in getattr(parser, "__module__", "")


_AVAILABLE = _detect_available()


@dataclasses.dataclass(frozen=True)
class ScalarRange:
    start: int
    end: int
    style: str


@dataclasses.dataclass(frozen=True)
class BlockScalarRange:
    start: int
    end: int
    style: str
    header: str
    content_indent: int | None
    line_ending: str
    trailing_newlines: int
    at_end: bool
    terminal_suffix: str


@dataclasses.dataclass
class YAMLState:
    text: str
    data: dict[str, Any]
    scalars: dict[tuple[str | int, ...], ScalarRange]
    block_scalars: dict[tuple[str | int, ...], BlockScalarRange]


def available() -> bool:
    return _AVAILABLE


def dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("ruamel.yaml.clib", "tree-sitter", "tree-sitter-yaml"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def load(text: str) -> tuple[dict[str, Any], YAMLState] | None:
    if not available():
        return None
    try:
        data = _safe_load(text)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None
        index = _index_scalars(text, data)
    except Exception:
        return None
    if index is None:
        return None
    scalars, block_scalars = index
    if (
        text
        and not text.endswith(("\n", "\r"))
        and not any(block.at_end for block in block_scalars.values())
    ):
        return None
    _apply_scalar_styles(data, scalars)
    return data, YAMLState(
        text=text,
        data=data,
        scalars=scalars,
        block_scalars=block_scalars,
    )


def copy_state(state: YAMLState) -> tuple[dict[str, Any], YAMLState]:
    data = copy.deepcopy(state.data)
    return data, dataclasses.replace(state, data=data)


def patch(state: YAMLState, data: dict[str, Any]) -> tuple[str, YAMLState] | None:
    changes: list[tuple[ScalarRange | BlockScalarRange, bytes]] = []
    if not _collect_changes(
        state.data,
        data,
        (),
        state.scalars,
        state.block_scalars,
        changes,
    ):
        return None
    if not changes:
        return state.text, state

    source = state.text.encode("utf-8")
    for scalar, replacement in sorted(changes, key=lambda item: item[0].start, reverse=True):
        source = source[: scalar.start] + replacement + source[scalar.end :]
    try:
        text = source.decode("utf-8")
        loaded = _safe_load(text)
    except Exception:
        return None
    if not isinstance(loaded, dict) or not semantic_equal(loaded, data):
        return None
    index = _index_scalars(text, loaded)
    if index is None:
        return None
    scalars, block_scalars = index
    _apply_scalar_styles(loaded, scalars)
    return text, YAMLState(
        text=text,
        data=loaded,
        scalars=scalars,
        block_scalars=block_scalars,
    )


def semantic_equal(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if list(left.keys()) != list(right.keys()):
            return False
        return all(semantic_equal(left[key], right[key]) for key in left)
    if _is_sequence(left) and _is_sequence(right):
        return len(left) == len(right) and all(
            semantic_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, int) or isinstance(right, int):
        return (
            isinstance(left, int)
            and not isinstance(left, bool)
            and isinstance(right, int)
            and not isinstance(right, bool)
            and left == right
        )
    if isinstance(left, float) or isinstance(right, float):
        return isinstance(left, float) and isinstance(right, float) and left == right
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    if left is None or right is None:
        return left is None and right is None
    return type(left) is type(right) and left == right


def _safe_load(text: str) -> Any:
    yaml = RuamelYAML(typ="safe")
    return yaml.load(text)


def _new_parser() -> Any:
    if _LANGUAGE is None or _PARSER_TYPE is None:
        raise RuntimeError("fast YAML dependencies are unavailable")
    return _PARSER_TYPE(_LANGUAGE)


def _index_scalars(
    text: str,
    data: dict[str, Any],
) -> tuple[
    dict[tuple[str | int, ...], ScalarRange],
    dict[tuple[str | int, ...], BlockScalarRange],
] | None:
    source = text.encode("utf-8")
    tree = _new_parser().parse(source)
    root = tree.root_node
    if root.has_error:
        return None
    if _contains_unsupported_syntax(root, source):
        return None
    document_nodes = [child for child in root.named_children if child.type == "document"]
    if len(document_nodes) != 1:
        return None
    node = _unwrap(document_nodes[0])
    if node is None or node.type != "block_mapping":
        return None
    scalars: dict[tuple[str | int, ...], ScalarRange] = {}
    block_scalars: dict[tuple[str | int, ...], BlockScalarRange] = {}
    if not _walk_value(node, data, (), source, scalars, block_scalars):
        return None
    return scalars, block_scalars


_UNSUPPORTED_SYNTAX_MARKERS = (b"&", b"*", b"!", b"%")


def _contains_unsupported_syntax(root: Any, source: bytes) -> bool:
    # Anchors, aliases, tags and directives each need one of these indicator
    # characters, so a document without any of them can skip the tree walk.
    if not any(marker in source for marker in _UNSUPPORTED_SYNTAX_MARKERS):
        return False
    unsupported = {
        "alias",
        "anchor",
        "tag",
        "tag_directive",
        "yaml_directive",
    }
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type in unsupported:
            return True
        pending.extend(node.named_children)
    return False


def _unwrap(node: Any) -> Any | None:
    while node.type in {"document", "block_node"}:
        named = node.named_children
        if len(named) != 1:
            return None
        node = named[0]
    return node


def _walk_value(
    node: Any | None,
    value: Any,
    path: tuple[str | int, ...],
    source: bytes,
    scalars: dict[tuple[str | int, ...], ScalarRange],
    block_scalars: dict[tuple[str | int, ...], BlockScalarRange],
) -> bool:
    if node is None:
        return value is None
    node = _unwrap(node)
    if node is None:
        return False

    if node.type == "block_mapping":
        if not isinstance(value, Mapping):
            return False
        pairs = [child for child in node.named_children if child.type == "block_mapping_pair"]
        mapping_items = list(value.items())
        if len(pairs) != len(mapping_items):
            return False
        for pair, (key, child_value) in zip(pairs, mapping_items):
            if not isinstance(key, str) or not _simple_key(pair, source, key):
                return False
            child = pair.child_by_field_name("value")
            if not _walk_value(
                child,
                child_value,
                path + (key,),
                source,
                scalars,
                block_scalars,
            ):
                return False
        return True

    if node.type == "block_sequence":
        if not _is_sequence(value):
            return False
        sequence_items = [
            child for child in node.named_children if child.type == "block_sequence_item"
        ]
        if len(sequence_items) != len(value):
            return False
        for index, item in enumerate(sequence_items):
            child_value = value[index]
            named = item.named_children
            child = named[0] if len(named) == 1 else None
            if not _walk_value(
                child,
                child_value,
                path + (index,),
                source,
                scalars,
                block_scalars,
            ):
                return False
        return True

    flow_node = _unwrap_flow(node)
    if flow_node is not None and flow_node.type in {"flow_mapping", "flow_sequence"}:
        return True

    block_scalar = _block_scalar_range(node, value, source)
    if block_scalar is not None:
        block_scalars[path] = block_scalar
        return True
    if flow_node is not None and flow_node.type == "block_scalar":
        return isinstance(value, str)

    scalar = _scalar_range(node, value, source)
    if scalar is not None:
        scalars[path] = scalar
        return True
    return _is_stable_unindexed_scalar(node, value, source)


def _simple_key(pair: Any, source: bytes, expected: str) -> bool:
    key = pair.child_by_field_name("key")
    if key is None or b"\n" in source[key.start_byte : key.end_byte]:
        return False
    node = _unwrap_flow(key)
    if node is None or node.type != "plain_scalar":
        return False
    named = node.named_children
    if len(named) != 1 or named[0].type != "string_scalar":
        return False
    return source[key.start_byte : key.end_byte].decode("utf-8") == expected


def _scalar_range(node: Any, value: Any, source: bytes) -> ScalarRange | None:
    outer = node
    node = _unwrap_flow(node)
    if node is None or b"\n" in source[outer.start_byte : outer.end_byte]:
        return None
    if node.type == "single_quote_scalar" and isinstance(value, str):
        return ScalarRange(outer.start_byte, outer.end_byte, "single")
    if node.type == "double_quote_scalar" and isinstance(value, str):
        return ScalarRange(outer.start_byte, outer.end_byte, "double")
    if node.type != "plain_scalar":
        return None
    named = node.named_children
    if len(named) != 1:
        return None
    token = source[outer.start_byte : outer.end_byte].decode("utf-8")
    scalar_type = named[0].type
    if scalar_type == "string_scalar" and isinstance(value, str):
        return ScalarRange(outer.start_byte, outer.end_byte, "plain")
    if scalar_type == "boolean_scalar" and isinstance(value, bool) and token in {"true", "false"}:
        return ScalarRange(outer.start_byte, outer.end_byte, "bool")
    if (
        scalar_type == "integer_scalar"
        and isinstance(value, int)
        and not isinstance(value, bool)
        and _INTEGER.fullmatch(token)
    ):
        return ScalarRange(outer.start_byte, outer.end_byte, "int")
    if scalar_type == "float_scalar" and isinstance(value, float) and _FLOAT.fullmatch(token):
        return ScalarRange(outer.start_byte, outer.end_byte, "float")
    return None


def _block_scalar_range(
    node: Any,
    value: Any,
    source: bytes,
) -> BlockScalarRange | None:
    node = _unwrap_flow(node)
    if node is None or node.type != "block_scalar" or not isinstance(value, str):
        return None
    segment = source[node.start_byte : node.end_byte].decode("utf-8")
    line_match = _LINE_ENDING.search(segment)
    if line_match is None:
        header = segment
        body = ""
        line_ending = "\n"
    else:
        header = segment[: line_match.start()]
        body = segment[line_match.end() :]
        line_ending = line_match.group()
    indicator = _block_indicator(header)
    if indicator is None:
        return None
    style, _ = indicator
    content_indent = _minimum_content_indent(body)
    end = _extend_block_end(node.end_byte, source, content_indent)
    segment = source[node.start_byte:end].decode("utf-8")
    return BlockScalarRange(
        start=node.start_byte,
        end=end,
        style=style,
        header=header,
        content_indent=content_indent,
        line_ending=line_ending,
        trailing_newlines=_trailing_newlines(value),
        at_end=end == len(source),
        terminal_suffix=segment[len(segment.rstrip("\r\n")) :],
    )


def _block_indicator(header: str) -> tuple[str, str] | None:
    token = header.split("#", 1)[0].rstrip()
    if not token or token[0] not in {"|", ">"}:
        return None
    modifiers = token[1:]
    digits = [character for character in modifiers if character.isdigit()]
    chomps = [character for character in modifiers if character in {"+", "-"}]
    if (
        any(character not in "+-123456789" for character in modifiers)
        or len(digits) > 1
        or len(chomps) > 1
    ):
        return None
    style = "literal" if token[0] == "|" else "folded"
    return style, chomps[0] if chomps else ""


def _minimum_content_indent(body: str) -> int | None:
    indents: list[int] = []
    for line in _LINE_ENDING.split(body):
        if not line.strip(" "):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0 and line.startswith("\t"):
            return None
        indents.append(indent)
    return min(indents) if indents else None


def _extend_block_end(end: int, source: bytes, content_indent: int | None) -> int:
    if content_indent is None:
        return end
    cursor = end
    while source[cursor : cursor + 1] in {b" ", b"\t"}:
        cursor += 1
    while cursor < len(source):
        if source.startswith(b"\r\n", cursor):
            content_start = cursor + 2
        elif source[cursor : cursor + 1] in {b"\n", b"\r"}:
            content_start = cursor + 1
        else:
            break
        line_end = content_start
        while line_end < len(source) and source[line_end : line_end + 1] not in {
            b"\n",
            b"\r",
        }:
            line_end += 1
        line = source[content_start:line_end]
        if not line or line.strip(b" ") or len(line) < content_indent:
            break
        cursor = line_end
    return cursor


def _trailing_newlines(value: str) -> int:
    return len(value) - len(value.rstrip("\n"))


def _unwrap_flow(node: Any) -> Any | None:
    while node.type == "flow_node":
        named = node.named_children
        if len(named) != 1:
            return None
        node = named[0]
    return node


def _is_stable_unindexed_scalar(node: Any, value: Any, source: bytes) -> bool:
    outer = node
    node = _unwrap_flow(node)
    if node is None:
        return False
    if node.type in {"single_quote_scalar", "double_quote_scalar"}:
        return (
            isinstance(value, str)
            and bool(_LINE_ENDING.search(source[outer.start_byte : outer.end_byte].decode("utf-8")))
        )
    if node.type != "plain_scalar":
        return False
    named = node.named_children
    if len(named) != 1:
        return False
    if named[0].type == "null_scalar":
        return value is None
    return (
        named[0].type == "string_scalar"
        and isinstance(value, str)
        and b"\n" in source[outer.start_byte : outer.end_byte]
    )


def _apply_scalar_styles(
    data: dict[str, Any],
    scalars: dict[tuple[str | int, ...], ScalarRange],
) -> None:
    for path, scalar in scalars.items():
        if scalar.style not in {"single", "double"}:
            continue
        parent: Any = data
        for part in path[:-1]:
            parent = parent[part]
        key = path[-1]
        value = parent[key]
        if scalar.style == "single":
            parent[key] = SingleQuotedScalarString(value)
        else:
            parent[key] = DoubleQuotedScalarString(value)


def _collect_changes(
    old: Any,
    new: Any,
    path: tuple[str | int, ...],
    scalars: dict[tuple[str | int, ...], ScalarRange],
    block_scalars: dict[tuple[str | int, ...], BlockScalarRange],
    changes: list[tuple[ScalarRange | BlockScalarRange, bytes]],
) -> bool:
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        if list(old.keys()) != list(new.keys()):
            return False
        return all(
            _collect_changes(
                old[key],
                new[key],
                path + (key,),
                scalars,
                block_scalars,
                changes,
            )
            for key in old
        )
    if _is_sequence(old) and _is_sequence(new):
        if len(old) != len(new):
            return False
        return all(
            _collect_changes(
                old_item,
                new_item,
                path + (index,),
                scalars,
                block_scalars,
                changes,
            )
            for index, (old_item, new_item) in enumerate(zip(old, new))
        )
    if semantic_equal(old, new):
        return True
    scalar = scalars.get(path)
    if scalar is not None:
        replacement = _render_replacement(scalar.style, old, new)
        if replacement is None:
            return False
        changes.append((scalar, replacement.encode("utf-8")))
        return True
    block_scalar = block_scalars.get(path)
    if block_scalar is None:
        return False
    replacement = _render_block_replacement(block_scalar, old, new)
    if replacement is None:
        return False
    changes.append((block_scalar, replacement.encode("utf-8")))
    return True


def _render_replacement(style: str, old: Any, new: Any) -> str | None:
    if style in {"plain", "single", "double"}:
        if not isinstance(old, str) or not isinstance(new, str) or "\n" in new or "\r" in new:
            return None
        if style == "single":
            return "'" + new.replace("'", "''") + "'"
        if style == "double":
            return json.dumps(new, ensure_ascii=False)
        return new
    if style == "bool":
        if not isinstance(old, bool) or not isinstance(new, bool):
            return None
        return "true" if new else "false"
    if style == "int":
        if (
            not isinstance(old, int)
            or isinstance(old, bool)
            or not isinstance(new, int)
            or isinstance(new, bool)
        ):
            return None
        return str(new)
    if style == "float":
        if not isinstance(old, float) or not isinstance(new, float) or not math.isfinite(new):
            return None
        return repr(new)
    return None


def _render_block_replacement(
    block: BlockScalarRange,
    old: Any,
    new: Any,
) -> str | None:
    if not isinstance(old, str) or not isinstance(new, str) or "\r" in new:
        return None
    if _trailing_newlines(new) != block.trailing_newlines:
        return None
    original_indicator = _block_indicator(block.header)
    if original_indicator is None:
        return None

    scalar_type = LiteralScalarString if block.style == "literal" else FoldedScalarString
    yaml = RuamelYAML()
    output = io.StringIO()
    yaml.dump(
        {"value": scalar_type(new), "__snapclass_next__": "sentinel"},
        output,
    )
    rendered = output.getvalue()
    prefix = "value: "
    marker = "\n__snapclass_next__:"
    marker_index = rendered.find(marker)
    if not rendered.startswith(prefix) or marker_index < 0:
        return None
    segment = rendered[len(prefix) : marker_index].rstrip("\n")
    lines = segment.split("\n")
    generated_indicator = _block_indicator(lines[0])
    if generated_indicator != original_indicator:
        eof_clip = (
            block.at_end
            and block.trailing_newlines == 0
            and original_indicator == (block.style, "")
            and generated_indicator == (block.style, "-")
        )
        if not eof_clip:
            return None
    content_lines = lines[1:]
    if not content_lines:
        return block.header + block.terminal_suffix
    generated_indent = _minimum_content_indent("\n".join(content_lines))
    if generated_indent is None or block.content_indent is None:
        return None

    reindented: list[str] = []
    for line in content_lines:
        if not line:
            reindented.append("")
            continue
        leading_spaces = len(line) - len(line.lstrip(" "))
        if leading_spaces < generated_indent:
            return None
        reindented.append(" " * block.content_indent + line[generated_indent:])
    return (
        block.header
        + block.line_ending
        + block.line_ending.join(reindented)
        + block.terminal_suffix
    )


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
