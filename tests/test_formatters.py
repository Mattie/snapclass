from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json

import pytest

from snapclass import Stash, snapclass, formatters
from snapclass.formatters import TypedTextFormatter, YAMLFormatter


def test_json_format_round_trips(tmp_path):
    @snapclass("{self.name}.json", stash=Stash(tmp_path), manual=True)
    class Config:
        name: str
        value: int

    Config("sample", 42).snapshot.save()
    data = json.loads((tmp_path / "sample.json").read_text(encoding="utf-8"))
    assert data == {"value": 42}
    assert Config.snapshots.get("sample").value == 42


def test_json5_format_round_trips_with_comments(tmp_path):
    @snapclass("{self.name}.json5", stash=Stash(tmp_path), manual=True)
    class Config:
        name: str
        value: int = 0
        label: str = ""

    (tmp_path / "sample.json5").write_text(
        "{\n  // authored config\n  value: 42,\n  label: 'hello',\n}\n",
        encoding="utf-8",
    )

    loaded = Config.snapshots.get("sample")
    assert loaded.value == 42
    assert loaded.label == "hello"

    loaded.label = "updated"
    loaded.snapshot.save()
    assert Config.snapshots.get("sample").label == "updated"


def test_toml_format_round_trips(tmp_path):
    @snapclass("{self.name}.toml", stash=Stash(tmp_path), manual=True)
    class Config:
        name: str
        value: int

    Config("sample", 42).snapshot.save()
    text = (tmp_path / "sample.toml").read_text(encoding="utf-8")
    assert "value = 42" in text
    assert Config.snapshots.get("sample").value == 42


def test_toml_enum_values_round_trip(tmp_path):
    class FileOutputType(Enum):
        IN_MESSAGE = 1
        FILESYSTEM = 2

    @snapclass("{self.name}.toml", stash=Stash(tmp_path), manual=True)
    class Config:
        name: str
        path_type: FileOutputType = FileOutputType.IN_MESSAGE

    config = Config("sample")
    config.path_type = FileOutputType.FILESYSTEM
    config.snapshot.save()

    path = tmp_path / "sample.toml"
    assert path.read_text(encoding="utf-8") == "path_type = 2\n"

    path.write_text("path_type = 1\n", encoding="utf-8")
    assert Config.snapshots.get("sample").path_type is FileOutputType.IN_MESSAGE


def test_no_extension_files_default_to_yaml(tmp_path):
    @snapclass("{self.name}", stash=Stash(tmp_path), manual=True)
    class Config:
        name: str
        value: int = 0

    (tmp_path / "sample").write_text("value: '42'\n", encoding="utf-8")

    loaded = Config.snapshots.get("sample")
    assert loaded.value == 42

    loaded.value = 43
    loaded.snapshot.save()
    assert YAMLFormatter.loads((tmp_path / "sample").read_text(encoding="utf-8")) == {"value": 43}


def test_yaml_formatter_uses_readable_sequence_indentation():
    assert formatters.serialize({"key": "value", "items": [1, "a", None]}, ".yaml") == (
        "key: value\n"
        "items:\n"
        "  - 1\n"
        "  - a\n"
        "  -\n"
    )
    assert formatters.serialize([{"one": 1, "two": 2}], ".yaml") == (
        "- one: 1\n"
        "  two: 2\n"
    )


def test_yaml_non_mapping_file_loads_as_empty_data_for_defaults(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Config:
        name: str
        value: int = 7
        label: str = "default"

    path = tmp_path / "sample.yml"
    original = "- one\n- two\n"
    path.write_text(original, encoding="utf-8")

    loaded = Config.snapshots.get("sample")

    assert loaded.value == 7
    assert loaded.label == "default"
    assert path.read_text(encoding="utf-8") == original


def test_registered_builtin_yaml_formatter_non_mapping_file_loads_as_empty_data(tmp_path):
    formatters.register(".yml", formatters.YAML)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Config:
        name: str
        value: int = 7

    (tmp_path / "sample.yml").write_text("- one\n- two\n", encoding="utf-8")

    assert Config.snapshots.get("sample").value == 7


def test_json_and_json5_non_mapping_files_load_as_empty_data_for_defaults(tmp_path):
    @snapclass("{self.name}.json", stash=Stash(tmp_path), manual=True)
    class JSONConfig:
        name: str
        value: int = 7

    @snapclass("{self.name}.json5", stash=Stash(tmp_path), manual=True)
    class JSON5Config:
        name: str
        value: int = 9

    (tmp_path / "sample.json").write_text("[1, 2]\n", encoding="utf-8")
    (tmp_path / "sample.json5").write_text("[1, 2,]\n", encoding="utf-8")

    assert JSONConfig.snapshots.get("sample").value == 7
    assert JSON5Config.snapshots.get("sample").value == 9


def test_formatters_deserialize_non_mapping_structured_files_as_empty_data(tmp_path):
    yaml_path = tmp_path / "sample.yaml"
    json_path = tmp_path / "sample.json"
    json5_path = tmp_path / "sample.json5"

    yaml_path.write_text("- one\n- two\n", encoding="utf-8")
    json_path.write_text("[1, 2]\n", encoding="utf-8")
    json5_path.write_text("[1, 2,]\n", encoding="utf-8")

    assert formatters.deserialize(yaml_path, ".yaml") == {}
    assert formatters.deserialize(json_path, ".json") == {}
    assert formatters.deserialize(json5_path, ".json5") == {}


def test_formatters_deserialize_custom_formatter_non_mapping_as_empty_data(tmp_path):
    class ListFormatter(formatters.Formatter):
        @classmethod
        def extensions(cls) -> set[str]:
            return {".listish"}

        @classmethod
        def deserialize(cls, file_object):
            return [line.strip() for line in file_object]

        @classmethod
        def serialize(cls, data):
            return "\n".join(data)

    path = tmp_path / "sample.listish"
    path.write_text("one\ntwo\n", encoding="utf-8")

    assert formatters.deserialize(path, ".listish", formatter=ListFormatter) == {}


def test_yaml_save_preserves_nested_dataclass_comments(tmp_path):
    @dataclass
    class Nested:
        name: str = ""
        sschemas: float = 0.0

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        sschemas: float = 0.0
        nested: Nested = field(default_factory=Nested)

    path = tmp_path / "sample.yml"
    path.write_text(
        "# Header\n"
        "sschemas: 1        # Line\n"
        "\n"
        "nested:\n"
        "  # Nested header\n"
        "  name: n\n"
        "  sschemas: 2      # Nested line\n",
        encoding="utf-8",
    )

    sample = Sample.snapshots.get("sample")
    sample.sschemas = 3.0
    sample.nested.sschemas = 4.0
    sample.snapshot.save()

    text = path.read_text(encoding="utf-8")
    assert "# Header" in text
    assert "sschemas: 3.0      # Line" in text
    assert "  # Nested header" in text
    assert "  sschemas: 4.0    # Nested line" in text


def test_yaml_save_preserves_quote_style_for_existing_scalars(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        s1: str = ""
        s2: str = ""
        s3: str = ""

    path = tmp_path / "sample.yml"
    path.write_text("s1: a\ns2: 'b'\ns3: \"c\"\n", encoding="utf-8")

    sample = Sample.snapshots.get("sample")
    sample.s1 = "d"
    sample.s2 = "e"
    sample.s3 = "f"
    sample.snapshot.save()

    assert path.read_text(encoding="utf-8") == "s1: d\ns2: 'e'\ns3: \"f\"\n"


def test_yaml_save_preserves_block_and_folded_scalar_styles(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Prompt:
        name: str
        body: str = ""
        summary: str = ""

    path = tmp_path / "sample.yml"
    path.write_text(
        "body: |\n"
        "  line one\n"
        "  line two\n"
        "summary: >\n"
        "  folded one\n"
        "  folded two\n",
        encoding="utf-8",
    )

    prompt = Prompt.snapshots.get("sample")
    prompt.body = "line three\nline four\n"
    prompt.summary = "folded three folded four\n"
    prompt.snapshot.save()

    assert path.read_text(encoding="utf-8") == (
        "body: |\n"
        "  line three\n"
        "  line four\n"
        "summary: >\n"
        "  folded three folded four\n"
    )


def test_yaml_preservation_template_is_scoped_to_loaded_path(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        value: str = ""

    source = tmp_path / "source.yml"
    source.write_text("# Source comment\nvalue: old\n", encoding="utf-8")

    sample = Sample.snapshots.get("source")
    sample.value = "new"
    sample.snapshot.save(tmp_path / "copy.yml")

    assert (tmp_path / "copy.yml").read_text(encoding="utf-8") == "value: new\n"
    assert source.read_text(encoding="utf-8") == "# Source comment\nvalue: old\n"


def test_typed_text_formatter_reads_plunkylib_style_legacy_file(tmp_path):
    @snapclass("{self.name}.txt", stash=Stash(tmp_path), manual=True, formatter=TypedTextFormatter)
    class PromptVars:
        name: str
        prompt: str
        count: int
        temperature: float
        enabled: bool

    divider = TypedTextFormatter.divider
    (tmp_path / "Legacy.txt").write_text(
        "prompt|str\n"
        "Line one\n"
        "Line two\n"
        f"{divider}\n"
        "count|int\n"
        "3\n"
        f"{divider}\n"
        "temperature|float\n"
        "0.7\n"
        f"{divider}\n"
        "enabled|bool\n"
        "False\n"
        f"{divider}\n",
        encoding="utf-8",
    )

    loaded = PromptVars.snapshots.get("Legacy")
    assert loaded.prompt == "Line one\nLine two"
    assert loaded.count == 3
    assert loaded.temperature == 0.7
    assert loaded.enabled is False


def test_typed_text_formatter_writes_plunkylib_style_sections(tmp_path):
    @snapclass("{self.name}.txt", stash=Stash(tmp_path), manual=True, formatter=TypedTextFormatter)
    class PromptVars:
        name: str
        prompt: str
        count: int
        enabled: bool

    PromptVars("Saved", "Hello", 2, True).snapshot.save()

    text = (tmp_path / "Saved.txt").read_text(encoding="utf-8")
    assert "prompt|str\nHello\n" in text
    assert "count|int\n2\n" in text
    assert "enabled|bool\nTrue\n" in text
    assert text.count(TypedTextFormatter.divider) == 3


def test_typed_text_formatter_handles_none_and_rejects_invalid_bool(tmp_path):
    encoded = TypedTextFormatter.dumps({"value": None})
    assert "value|NoneType\n" in encoded
    assert TypedTextFormatter.loads(encoded) == {"value": None}

    with pytest.raises(ValueError, match="Cannot parse bool"):
        TypedTextFormatter.loads(
            "enabled|bool\n"
            "maybe\n"
            f"{TypedTextFormatter.divider}\n"
        )


def test_default_txt_formatter_remains_raw_text(tmp_path):
    @snapclass("{self.name}.txt", stash=Stash(tmp_path), manual=True)
    class Text:
        name: str
        content: str

    Text("Raw", "hello\n").snapshot.save()

    assert (tmp_path / "Raw.txt").read_text(encoding="utf-8") == "hello\n"
