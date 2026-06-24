from __future__ import annotations

from dataclasses import field
from io import StringIO

import pytest
from ruamel.yaml import YAML as RuamelYAML

from snapclass import SnapclassError, Stash, snapclass, hooks, sessions


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


def test_snapshots_get_initializes_init_false_fields_before_loaded(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""
        transient: list[str] = field(init=False)

        def __snapclass_ready__(self, *, snapshot):
            """Snapshot is attached and transient state can be initialized."""
            self.transient = ["ready"]

        def __snapclass_loaded__(self, *, snapshot, path):
            """File data has been applied and transient state can be reused."""
            self.transient.append(f"loaded:{path.name}")

    (tmp_path / "sample.yml").write_text("value: file\n", encoding="utf-8")

    item = Item.snapshots.get("sample")

    assert item.value == "file"
    assert item.transient == ["ready", "loaded:sample.yml"]


def test_snapshots_get_or_create_missing_file_runs_ready_without_loaded(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = "created"
        transient: list[str] = field(init=False)

        def __snapclass_ready__(self, *, snapshot):
            """Snapshot is attached and transient state can be initialized."""
            self.transient = ["ready"]

        def __snapclass_loaded__(self, *, snapshot, path):
            """File data has been applied and transient state can be reused."""
            self.transient.append("loaded")

    item = Item.snapshots.get_or_create("sample")

    assert item.value == "created"
    assert item.transient == ["ready"]
    assert (tmp_path / "sample.yml").exists()


def test_loaded_hook_runs_for_explicit_loads_and_text_setter(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""
        loaded_paths: list[str] = field(default_factory=list)

        def __snapclass_loaded__(self, *, snapshot, path):
            """File data has been applied and transient state can be reused."""
            self.loaded_paths.append(path.name)

    first = Item("first")
    (tmp_path / "first.yml").write_text("value: snapshot\n", encoding="utf-8")
    first.snapshot.load()

    second = Item("second")
    (tmp_path / "second.yml").write_text("value: object\n", encoding="utf-8")
    second.load()

    third = Item("third")
    third.snapshot.save()
    third.snapshot.text = "value: text\n"

    assert first.loaded_paths == ["first.yml"]
    assert second.loaded_paths == ["second.yml"]
    assert third.loaded_paths == ["third.yml"]
    assert first.value == "snapshot"
    assert second.value == "object"
    assert third.value == "text"


def test_lifecycle_hook_mutations_do_not_trigger_automatic_save_loops(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""
        events: list[str] = field(default_factory=list)

        def __snapclass_loaded__(self, *, snapshot, path):
            """File data has been applied and transient state can be reused."""
            self.value = "hooked"
            self.events.append("loaded")

    path = tmp_path / "sample.yml"
    path.write_text("value: file\nevents: []\n", encoding="utf-8")

    item = Item("sample")

    assert item.value == "hooked"
    assert item.events == ["loaded"]
    assert path.read_text(encoding="utf-8") == "value: file\nevents: []\n"


def test_ready_hook_does_not_mask_file_data_during_initial_load(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""
        ready_seen: str = field(init=False)

        def __snapclass_ready__(self, *, snapshot):
            """Snapshot is attached and file data is visible when present."""
            self.ready_seen = self.value
            if not self.value:
                self.value = "ready"

    (tmp_path / "sample.yml").write_text("value: file\n", encoding="utf-8")

    item = Item("sample")

    assert item.ready_seen == "file"
    assert item.value == "file"
    assert (tmp_path / "sample.yml").read_text(encoding="utf-8") == "value: file\n"


def test_lifecycle_hook_suppression_is_per_instance(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path / "other"))
    class Other:
        name: str
        value: str = ""

    other = Other("target")

    @snapclass("{self.name}.yml", stash=Stash(tmp_path / "items"))
    class Item:
        name: str
        value: str = ""

        def __snapclass_loaded__(self, *, snapshot, path):
            """File data has been applied and only this instance is suppressed."""
            assert sessions.HOOKS_ENABLED is True
            self.value = "hooked"
            other.value = "updated"

    item_path = tmp_path / "items" / "sample.yml"
    item_path.parent.mkdir()
    item_path.write_text("value: file\n", encoding="utf-8")

    item = Item("sample")

    assert item.value == "hooked"
    assert item_path.read_text(encoding="utf-8") == "value: file\n"
    assert (tmp_path / "other" / "target.yml").read_text(encoding="utf-8") == (
        "value: updated\n"
    )


def test_lifecycle_hook_failures_include_hook_name_and_path(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class ReadyItem:
        name: str

        def __snapclass_ready__(self, *, snapshot):
            """Snapshot is attached and transient state can be initialized."""
            raise RuntimeError("ready failed")

    with pytest.raises(SnapclassError) as ready_error:
        ReadyItem("ready")

    ready_message = str(ready_error.value)
    assert "__snapclass_ready__" in ready_message
    assert "ready.yml" in ready_message

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class LoadedItem:
        name: str

        def __snapclass_loaded__(self, *, snapshot, path):
            """File data has been applied and transient state can be reused."""
            raise RuntimeError("loaded failed")

    (tmp_path / "loaded.yml").write_text("", encoding="utf-8")

    with pytest.raises(SnapclassError) as loaded_error:
        LoadedItem.snapshots.get("loaded")

    loaded_message = str(loaded_error.value)
    assert "__snapclass_loaded__" in loaded_message
    assert "loaded.yml" in loaded_message
