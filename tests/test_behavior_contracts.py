from __future__ import annotations

from dataclasses import field
from io import StringIO

import pytest
from ruamel.yaml import YAML as RuamelYAML

from snapclass import Stash, snapclass, hooks


def test_snapclass_types_behave_like_plain_yaml_values():
    from snapclass import types

    yaml = RuamelYAML()
    yaml.register_class(types.List)
    yaml.register_class(types.Dict)

    marker = object()
    data = types.Dict({"name": "Popsicle", "tags": types.List(["cold", "sweet"])})
    data.label = "Prompt"
    data.snapshot = marker

    stream = StringIO()
    yaml.dump({"item": data}, stream)
    text = stream.getvalue()

    assert data.label == "Prompt"
    assert data["label"] == "Prompt"
    assert data.snapshot is marker
    assert "snapshot" not in data
    assert "name: Popsicle" in text
    assert "  - cold" in text
    assert "!<" not in text
    with pytest.raises(AttributeError):
        data.missing


def test_snapclass_missing_sentinel_loads_required_fields_from_files(tmp_path):
    from snapclass import Missing

    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Prompt:
        name: str
        text: str

    (tmp_path / "Example.yml").write_text("text: loaded\n", encoding="utf-8")

    prompt = Prompt("Example", Missing)  # type: ignore[arg-type]

    assert prompt.text == "loaded"
    assert prompt.text is not Missing


def test_hooks_wrappers_are_idempotent_and_plain_snapshots_are_noops():
    calls: list[str] = []

    def read_value(self):
        calls.append("read")
        return "ok"

    def write_value(self, value):
        calls.append(value)
        return value.upper()

    plain = object()
    read_once = hooks.load_before(type(plain), read_value)
    read_twice = hooks.load_before(type(plain), read_once)
    write_once = hooks.save_after(type(plain), write_value)
    write_twice = hooks.save_after(type(plain), write_once)

    assert read_once is read_twice
    assert write_once is write_twice
    assert getattr(read_once, hooks.FLAG) is True
    assert getattr(write_once, hooks.FLAG) is True
    assert read_twice(plain) == "ok"
    assert write_twice(plain, "done") == "DONE"
    assert calls == ["read", "done"]


def test_hooks_reload_external_edits_before_wrapped_reads(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""

    item = Item("sample", "first")
    path = tmp_path / "sample.yml"
    path.write_text("value: edited\n", encoding="utf-8")

    read_value = hooks.load_before(Item, lambda self: self.value)

    assert read_value(item) == "edited"
    assert item.value == "edited"


def test_hooks_save_wrapped_mutations_for_automatic_snapshots(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""

    item = Item("sample", "first")

    def replace_value(self, value):
        object.__setattr__(self, "value", value)
        return self.value

    wrapped = hooks.save_after(Item, replace_value)

    assert wrapped(item, "wrapped") == "wrapped"
    assert (tmp_path / "sample.yml").read_text(encoding="utf-8") == "value: wrapped\n"


def test_hooks_skip_automatic_saves_for_manual_snapshots(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""

    item = Item("manual", "first")

    def replace_value(self, value):
        self.value = value

    wrapped = hooks.save_after(Item, replace_value)
    wrapped(item, "held")

    assert item.value == "held"
    assert not (tmp_path / "manual.yml").exists()


def test_hooks_disabled_batches_and_flushes_named_snapshots(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""
        events: list[str] = field(default_factory=list)

    item = Item("manual", "first")

    with hooks.disabled(item):
        item.value = "batched"
        with hooks.disabled():
            item.events.append("nested")
        assert not (tmp_path / "manual.yml").exists()

    assert (tmp_path / "manual.yml").read_text(encoding="utf-8") == (
        "value: batched\n"
        "events:\n"
        "  - nested\n"
    )
