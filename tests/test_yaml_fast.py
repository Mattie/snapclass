from __future__ import annotations

import copy
from dataclasses import dataclass, field

import pytest
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

from snapclass import Stash, snapclass
from snapclass import _yaml_fast, schemas
from snapclass.formatters import (
    FileFormatter,
    JSONFormatter,
    TERSEFormatter,
    YAMLFormatter,
)


requires_fast_yaml = pytest.mark.skipif(
    not _yaml_fast.available(),
    reason="install snapclass[yaml-fast] to exercise the accelerator",
)


@pytest.mark.parametrize(
    ("extension", "formatter"),
    [
        (".yml", YAMLFormatter),
        (".terse", TERSEFormatter),
        (".json", JSONFormatter),
    ],
)
def test_unchanged_save_reuses_text_and_still_writes(
    tmp_path,
    monkeypatch,
    extension,
    formatter,
):
    @snapclass("{self.name}" + extension, stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        value: str = ""

    sample = Sample("sample", "kept")
    sample.snapshot.save()
    expected = sample.snapshot.path.read_text(encoding="utf-8")

    writes = []
    real_write = schemas._write_text

    def track_write(*args, **kwargs):
        writes.append(args[0])
        return real_write(*args, **kwargs)

    def fail_dump(data):
        raise AssertionError("unchanged save should reuse the loaded text")

    monkeypatch.setattr(schemas, "_write_text", track_write)
    monkeypatch.setattr(formatter, "dumps", fail_dump)

    sample.snapshot.save()

    assert writes == [sample.snapshot.path]
    assert sample.snapshot.path.read_text(encoding="utf-8") == expected


@requires_fast_yaml
def test_fast_yaml_patches_supported_scalars_without_reformatting(tmp_path, monkeypatch):
    @dataclass
    class Item:
        name: str = ""
        enabled: bool = False

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        title: str = ""
        single: str = ""
        double: str = ""
        count: int = 0
        weight: float = 0.0
        items: list[Item] = field(default_factory=list)

    path = tmp_path / "sample.yml"
    path.write_text(
        "# Header\n"
        "title: plain       # Inline\n"
        "single: 'one'\n"
        'double: "two"\n'
        "count: 12\n"
        "weight: 1.25\n"
        "items:\n"
        "  - name: first\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    sample = Sample.snapshots.get("sample")
    assert sample.snapshot._yaml_state is not None

    def fail_dump(data):
        raise AssertionError("supported scalar edit should not use full YAML dump")

    monkeypatch.setattr(YAMLFormatter, "dumps", fail_dump)
    sample.title = "changed"
    sample.single = "it's"
    sample.double = "quoted"
    sample.count = 14
    sample.weight = 2.5
    sample.items[0].name = "second"
    sample.items[0].enabled = False
    sample.snapshot.save()

    assert path.read_text(encoding="utf-8") == (
        "# Header\n"
        "title: changed       # Inline\n"
        "single: 'it''s'\n"
        'double: "quoted"\n'
        "count: 14\n"
        "weight: 2.5\n"
        "items:\n"
        "  - name: second\n"
        "    enabled: false\n"
    )
    assert sample.snapshot._yaml_state is not None


@requires_fast_yaml
def test_fast_yaml_uses_utf8_byte_offsets(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        label: str = ""
        count: int = 0

    path = tmp_path / "sample.yml"
    path.write_text("label: caf\u00e9\ncount: 1 # kept\n", encoding="utf-8")

    sample = Sample.snapshots.get("sample")
    sample.count = 2
    sample.snapshot.save()

    assert path.read_text(encoding="utf-8") == "label: caf\u00e9\ncount: 2 # kept\n"


@requires_fast_yaml
def test_fast_yaml_preserves_crlf_line_endings(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        title: str = ""
        count: int = 0

    path = tmp_path / "sample.yml"
    path.write_bytes(b"title: one\r\ncount: 1 # kept\r\n")

    sample = Sample.snapshots.get("sample")
    sample.count = 2
    sample.snapshot.save()

    assert path.read_bytes() == b"title: one\r\ncount: 2 # kept\r\n"


@requires_fast_yaml
def test_fast_yaml_ignores_untouched_wrapped_plain_strings():
    text = (
        "summary: Representative nested payload used to compare readable file \n"
        "  formats without changing this wrapped value.\n"
        "count: 1 # kept\n"
    )
    loaded = _yaml_fast.load(text)

    assert loaded is not None
    loaded_data, state = loaded
    data = copy.deepcopy(loaded_data)
    data["count"] = 2

    patched = _yaml_fast.patch(state, data)

    assert patched is not None
    assert patched[0] == text.replace("count: 1", "count: 2")

    data["summary"] = "changed"
    assert _yaml_fast.patch(patched[1], data) is None


@requires_fast_yaml
@pytest.mark.parametrize(
    "text",
    [
        "settings: {theme: dark, sizes: [1, 2]}\ncount: 1 # kept\n",
        'message: "first line\\\n  second line"\ncount: 1 # kept\n',
        "message: >-\n  first line\n  second line\ncount: 1 # kept\n",
    ],
)
def test_fast_yaml_opaque_regions_allow_scalar_edits_elsewhere(text):
    loaded = _yaml_fast.load(text)

    assert loaded is not None
    loaded_data, state = loaded
    data = copy.deepcopy(loaded_data)
    data["count"] = 2

    patched = _yaml_fast.patch(state, data)

    assert patched is not None
    assert patched[0] == text.replace("count: 1", "count: 2")


@requires_fast_yaml
@pytest.mark.parametrize(
    ("text", "path", "replacement", "expected"),
    [
        (
            "root:\n"
            "  body: |- # keep this comment\n"
            "    first\n"
            "    second\n"
            "  count: 1\n",
            ("root", "body"),
            "changed\ntext",
            "root:\n"
            "  body: |- # keep this comment\n"
            "    changed\n"
            "    text\n"
            "  count: 1\n",
        ),
        (
            "messages:\n"
            "  - system: |2\n"
            "      first\n"
            "      second\n"
            "    name: kept\n",
            ("messages", 0, "system"),
            "changed\ntext\n",
            "messages:\n"
            "  - system: |2\n"
            "      changed\n"
            "      text\n"
            "    name: kept\n",
        ),
        (
            "messages:\n"
            "  - user: |-\n"
            "      first\n"
            "          \n"
            "  - assistant: kept\n",
            ("messages", 0, "user"),
            "changed",
            "messages:\n"
            "  - user: |-\n"
            "      changed\n"
            "  - assistant: kept\n",
        ),
        (
            "body: |+\n"
            "  first\n"
            "  second\n"
            "\n",
            ("body",),
            "changed\ntext\n\n",
            "body: |+\n"
            "  changed\n"
            "  text\n"
            "\n",
        ),
        (
            "body: |\n"
            "  first\n",
            ("body",),
            "changed\n",
            "body: |\n"
            "  changed\n",
        ),
        (
            "body: |-\n"
            "  first\n",
            ("body",),
            "changed",
            "body: |-\n"
            "  changed\n",
        ),
        (
            "body: |-\n"
            "  first      \n"
            "count: 1\n",
            ("body",),
            "changed",
            "body: |-\n"
            "  changed\n"
            "count: 1\n",
        ),
        (
            "body: >- # folded\n"
            "  first line\n"
            "  second line\n"
            "count: 1\n",
            ("body",),
            "changed line\nsecond paragraph",
            "body: >- # folded\n"
            "  changed line\n"
            "\n"
            "  second paragraph\n"
            "count: 1\n",
        ),
    ],
)
def test_fast_yaml_patches_block_scalars_in_place(text, path, replacement, expected):
    loaded = _yaml_fast.load(text)

    assert loaded is not None
    loaded_data, state = loaded
    data = copy.deepcopy(loaded_data)
    parent = data
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = replacement

    patched = _yaml_fast.patch(state, data)

    assert patched is not None
    assert patched[0] == expected
    assert _yaml_fast.semantic_equal(patched[1].data, data)


@requires_fast_yaml
def test_fast_yaml_patches_block_scalar_with_crlf():
    text = "body: |-\r\n  first\r\n  second\r\ncount: 1\r\n"
    loaded = _yaml_fast.load(text)

    assert loaded is not None
    loaded_data, state = loaded
    data = copy.deepcopy(loaded_data)
    data["body"] = "changed\ntext"

    patched = _yaml_fast.patch(state, data)

    assert patched is not None
    assert patched[0] == "body: |-\r\n  changed\r\n  text\r\ncount: 1\r\n"


@requires_fast_yaml
def test_fast_yaml_preserves_missing_terminal_newline():
    final_block = _yaml_fast.load("body: |\n  first")
    assert final_block is not None
    final_data = copy.deepcopy(final_block[0])
    final_data["body"] = "changed\ntext"
    final_patch = _yaml_fast.patch(final_block[1], final_data)
    assert final_patch is not None
    assert final_patch[0] == "body: |\n  changed\n  text"


@requires_fast_yaml
def test_snapshot_uses_fast_block_scalar_patch(tmp_path, monkeypatch):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        body: str = ""
        count: int = 0

    path = tmp_path / "sample.yml"
    path.write_text(
        "body: |- # prompt\n  first\n  second\ncount: 1 # unchanged\n",
        encoding="utf-8",
    )
    sample = Sample.snapshots.get("sample")

    def fail_dump(data):
        raise AssertionError("block scalar edit should not use a full YAML dump")

    monkeypatch.setattr(YAMLFormatter, "dumps", fail_dump)
    sample.body = "changed\ntext"
    sample.snapshot.save()

    assert path.read_text(encoding="utf-8") == (
        "body: |- # prompt\n  changed\n  text\ncount: 1 # unchanged\n"
    )
    assert sample.snapshot._yaml_state is not None


@requires_fast_yaml
def test_fast_yaml_falls_back_when_opaque_or_block_structure_changes():
    flow_loaded = _yaml_fast.load("values: [one, two]\ncount: 1\n")
    assert flow_loaded is not None
    flow_data = copy.deepcopy(flow_loaded[0])
    flow_data["values"].append("three")
    assert _yaml_fast.patch(flow_loaded[1], flow_data) is None

    block_loaded = _yaml_fast.load("body: |\n  first\ncount: 1\n")
    assert block_loaded is not None
    block_data = copy.deepcopy(block_loaded[0])
    block_data["body"] = "changed"
    assert _yaml_fast.patch(block_loaded[1], block_data) is None


@requires_fast_yaml
def test_fast_yaml_structural_edit_falls_back_and_preserves_comments(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        tags: list[str] = field(default_factory=list)

    path = tmp_path / "sample.yml"
    path.write_text("# Header\ntags:\n  - one # kept\n", encoding="utf-8")

    sample = Sample.snapshots.get("sample")
    assert sample.snapshot._yaml_state is not None
    sample.tags.append("two")
    sample.snapshot.save()

    assert path.read_text(encoding="utf-8") == "# Header\ntags:\n  - one\n  - two\n"
    assert Sample.snapshots.get("sample").tags == ["one", "two"]
    assert sample.snapshot._yaml_state is None


@requires_fast_yaml
def test_fast_yaml_type_change_falls_back(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        value: str | int = ""

    path = tmp_path / "sample.yml"
    path.write_text("# Header\nvalue: 'one'\n", encoding="utf-8")

    sample = Sample.snapshots.get("sample")
    assert sample.snapshot._yaml_state is not None
    sample.value = 2
    sample.snapshot.save()

    assert path.read_text(encoding="utf-8") == "# Header\nvalue: 2\n"
    assert sample.snapshot._yaml_state is None


@requires_fast_yaml
def test_fast_yaml_save_to_another_path_does_not_patch_source(tmp_path, monkeypatch):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        value: str = ""

    source = tmp_path / "sample.yml"
    source.write_text("# Header\nvalue: one\n", encoding="utf-8")
    sample = Sample.snapshots.get("sample")
    sample.value = "two"

    def fail_patch(*args, **kwargs):
        raise AssertionError("saving to another path must not source-patch")

    monkeypatch.setattr(_yaml_fast, "patch", fail_patch)
    destination = tmp_path / "copy.yml"
    sample.snapshot.save(destination)

    assert source.read_text(encoding="utf-8") == "# Header\nvalue: one\n"
    assert destination.read_text(encoding="utf-8") == "value: two\n"
    assert sample.snapshot._yaml_state is None


@requires_fast_yaml
def test_fast_yaml_semantics_match_round_trip_loader():
    text = (
        "title: plain\n"
        "enabled: true\n"
        "count: 12\n"
        "weight: 1.25\n"
        "items:\n"
        "  - name: first\n"
    )

    loaded = _yaml_fast.load(text)

    assert loaded is not None
    assert _yaml_fast.semantic_equal(loaded[0], YAMLFormatter.loads(text))


@requires_fast_yaml
def test_fast_yaml_retains_quoted_scalar_types_used_during_coercion():
    loaded = _yaml_fast.load("single: '1'\ndouble: \"2\"\n")

    assert loaded is not None
    assert type(loaded[0]["single"]) is SingleQuotedScalarString
    assert type(loaded[0]["double"]) is DoubleQuotedScalarString


@requires_fast_yaml
@pytest.mark.parametrize(
    "text",
    [
        "base: &base one\nvalue: *base\n",
        "%YAML 1.2\n---\nvalue: one\n",
        "value: !example one\n",
        "value: one",
    ],
)
def test_fast_yaml_declines_unsupported_document_shapes(text):
    assert _yaml_fast.load(text) is None


def test_custom_yaml_formatter_does_not_enter_fast_yaml_path(tmp_path, monkeypatch):
    class CustomFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text):
            return {"value": text.removeprefix("custom:")}

        @classmethod
        def dumps(cls, data):
            return "custom:" + data["value"]

    stash = Stash(tmp_path, formatters={".yml": CustomFormatter})

    @snapclass("{self.name}.yml", stash=stash, manual=True)
    class Sample:
        name: str
        value: str = ""

    path = tmp_path / "sample.yml"
    path.write_text("custom:kept", encoding="utf-8")

    def unexpected_fast_load(text):
        raise AssertionError("custom formatter should keep precedence")

    monkeypatch.setattr(_yaml_fast, "load", unexpected_fast_load)

    sample = Sample.snapshots.get("sample")

    assert sample.value == "kept"
    assert sample.snapshot._yaml_state is None
