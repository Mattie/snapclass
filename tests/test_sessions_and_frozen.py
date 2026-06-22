from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field
import json

import pytest

from snapclass import Stash, snapclass, frozen, hooks, sessions
from snapclass import Model
import snapclass.schemas as schemas


def test_hooks_enabled_false_disables_automatic_create_and_save(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""

    previous = sessions.HOOKS_ENABLED
    sessions.HOOKS_ENABLED = False
    try:
        item = Item("a", "one")
        assert not (tmp_path / "a.yml").exists()

        item.snapshot.save()
        item.value = "two"
        assert "two" not in (tmp_path / "a.yml").read_text(encoding="utf-8")
    finally:
        sessions.HOOKS_ENABLED = previous


def test_frozen_context_batches_automatic_saves_and_thaws_named_snapshots(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""

    item = Item("a", "one")

    with frozen(item):
        item.value = "two"
        assert "two" not in (tmp_path / "a.yml").read_text(encoding="utf-8")

    assert "two" in (tmp_path / "a.yml").read_text(encoding="utf-8")


def test_hooks_disabled_can_be_nested_and_restores_global_state():
    assert hooks.disabled is frozen

    previous = sessions.HOOKS_ENABLED
    sessions.HOOKS_ENABLED = True
    try:
        with hooks.disabled():
            assert sessions.HOOKS_ENABLED is False
            with hooks.disabled():
                assert sessions.HOOKS_ENABLED is False
            assert sessions.HOOKS_ENABLED is False
        assert sessions.HOOKS_ENABLED is True
    finally:
        sessions.HOOKS_ENABLED = previous


def test_hooks_disabled_saves_named_manual_snapshots_on_exit(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""

    item = Item("manual", "one")
    with hooks.disabled(item):
        item.value = "two"
        assert not (tmp_path / "manual.yml").exists()

    assert (tmp_path / "manual.yml").read_text(encoding="utf-8") == "value: two\n"


def test_automatic_sync_tracks_mutable_fields_inside_nested_dataclasses(tmp_path):
    @dataclass
    class Nested:
        items: list[int] = field(default_factory=list)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Sample:
        name: str
        nested: Nested = field(default_factory=Nested)

    sample = Sample("a")
    sample.nested.items.append(2)

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == (
        "nested:\n"
        "  items:\n"
        "    - 2\n"
    )

    sample.nested = Nested()
    sample.nested.items.append(3)

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == (
        "nested:\n"
        "  items:\n"
        "    - 3\n"
    )

    (tmp_path / "b.yml").write_text(
        "nested:\n"
        "  items:\n"
        "    - 1\n",
        encoding="utf-8",
    )
    loaded = Sample.snapshots.get("b")
    loaded.nested.items.append(4)

    assert (tmp_path / "b.yml").read_text(encoding="utf-8") == (
        "nested:\n"
        "  items:\n"
        "    - 1\n"
        "    - 4\n"
    )


def test_automatic_assignment_loads_current_file_before_saving(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Settings:
        name: str
        theme: str = "plain"
        mode: str = "draft"

    settings = Settings("app")
    (tmp_path / "app.yml").write_text("theme: readable\n", encoding="utf-8")

    settings.mode = "published"

    assert (tmp_path / "app.yml").read_text(encoding="utf-8") == (
        "theme: readable\n"
        "mode: published\n"
    )
    assert settings.theme == "readable"


def test_automatic_assignment_reloads_after_save_to_coerce_typed_fields(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Sample:
        name: str
        item: str = ""

    sample = Sample("typed")
    sample.item = 42  # type: ignore[assignment]

    assert sample.item == "42"
    assert (tmp_path / "typed.yml").read_text(encoding="utf-8") == "item: '42'\n"


def test_automatic_list_mutation_reloads_after_save_to_coerce_items(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Sample:
        name: str
        items: list[int] = field(default_factory=list)

    sample = Sample("typed-list")
    items = sample.items
    items.append(3.2)
    items.append(4.8)

    assert sample.items is items
    assert sample.items == [3, 4]
    assert (tmp_path / "typed-list.yml").read_text(encoding="utf-8") == (
        "items:\n"
        "  - 3\n"
        "  - 4\n"
    )


def test_automatic_saves_do_not_repeatedly_call_default_factories(tmp_path):
    calls = 0

    def default_value() -> str:
        nonlocal calls
        calls += 1
        return "draft"

    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Sample:
        name: str
        value: str = field(default_factory=default_value)

    sample = Sample("factory")
    sample.value = "review"
    sample.value = "published"

    assert calls == 2
    assert sample.value == "published"
    assert (tmp_path / "factory.yml").read_text(encoding="utf-8") == (
        "value: published\n"
    )

    other = Sample("other")
    other.value = "review"

    assert calls == 4
    assert other.value == "review"
    assert (tmp_path / "other.yml").read_text(encoding="utf-8") == "value: review\n"


def test_frozen_dataclass_models_save_load_and_remain_immutable(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, frozen=True)
    class Item:
        name: str
        value: str = ""

    item = Item("a", "one")
    item.snapshot.save()

    assert "value: one" in (tmp_path / "a.yml").read_text(encoding="utf-8")

    (tmp_path / "b.yml").write_text("value: two\n", encoding="utf-8")
    loaded = Item.snapshots.get("b")
    assert loaded.name == "b"
    assert loaded.value == "two"

    with pytest.raises(FrozenInstanceError):
        loaded.value = "three"


def test_minimal_diffs_writes_empty_lists_as_edit_friendly_yaml(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, defaults=True)
    class Item:
        name: str
        tags: list[str] = field(default_factory=list)

    Item("a").snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "tags:\n  -\n"
    assert Item.snapshots.get("a").tags == []


def test_minimal_diffs_can_be_disabled_for_semantic_empty_lists(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, defaults=True)
    class Item:
        name: str
        tags: list[str] = field(default_factory=list)

    previous = sessions.MINIMAL_DIFFS
    sessions.MINIMAL_DIFFS = False
    try:
        Item("a").snapshot.save()
    finally:
        sessions.MINIMAL_DIFFS = previous

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "tags: []\n"


def test_stash_minimal_diffs_is_scoped_to_that_stash(tmp_path):
    friendly_stash = Stash(tmp_path / "friendly", minimal_diffs=True)
    semantic_stash = Stash(tmp_path / "semantic", minimal_diffs=False)

    @snapclass("{self.name}.yml", stash=friendly_stash, manual=True, defaults=True)
    class FriendlyItem:
        name: str
        tags: list[str] = field(default_factory=list)

    @snapclass("{self.name}.yml", stash=semantic_stash, manual=True, defaults=True)
    class SemanticItem:
        name: str
        tags: list[str] = field(default_factory=list)

    previous = sessions.MINIMAL_DIFFS
    sessions.MINIMAL_DIFFS = False
    try:
        FriendlyItem("a").snapshot.save()
        SemanticItem("a").snapshot.save()
    finally:
        sessions.MINIMAL_DIFFS = previous

    assert (tmp_path / "friendly" / "a.yml").read_text(encoding="utf-8") == (
        "tags:\n"
        "  -\n"
    )
    assert (tmp_path / "semantic" / "a.yml").read_text(encoding="utf-8") == "tags: []\n"


def test_model_minimal_diffs_beats_stash_policy(tmp_path):
    stash = Stash(tmp_path, minimal_diffs=True)

    @snapclass(
        "{self.name}.yml",
        stash=stash,
        manual=True,
        defaults=True,
        minimal_diffs=False,
    )
    class Item:
        name: str
        tags: list[str] = field(default_factory=list)

    Item("a").snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "tags: []\n"


def test_minimal_diffs_applies_before_format_specific_rendering(tmp_path):
    @snapclass("{self.name}.json", stash=Stash(tmp_path), manual=True, defaults=True)
    class Item:
        name: str
        tags: list[str] = field(default_factory=list)

    Item("a").snapshot.save()

    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8")) == {
        "tags": [None]
    }


def test_write_delay_sleeps_after_snapshot_save(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(schemas.time, "sleep", sleeps.append)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""

    previous = sessions.WRITE_DELAY
    sessions.WRITE_DELAY = 0.125
    try:
        Item("a", "one").snapshot.save()
    finally:
        sessions.WRITE_DELAY = previous

    assert sleeps == [0.125]
    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "value: one\n"


def test_stash_write_delay_is_scoped_to_that_stash(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(schemas.time, "sleep", sleeps.append)

    slow_stash = Stash(tmp_path / "slow", write_delay=0.25)
    fast_stash = Stash(tmp_path / "fast", write_delay=0.0)

    @snapclass("{self.name}.yml", stash=slow_stash, manual=True)
    class SlowItem:
        name: str
        value: str = ""

    @snapclass("{self.name}.yml", stash=fast_stash, manual=True)
    class FastItem:
        name: str
        value: str = ""

    previous = sessions.WRITE_DELAY
    sessions.WRITE_DELAY = 0.5
    try:
        SlowItem("a", "one").snapshot.save()
        FastItem("a", "two").snapshot.save()
    finally:
        sessions.WRITE_DELAY = previous

    assert sleeps == [0.25]
    assert (tmp_path / "slow" / "a.yml").read_text(encoding="utf-8") == "value: one\n"
    assert (tmp_path / "fast" / "a.yml").read_text(encoding="utf-8") == "value: two\n"


def test_model_write_delay_beats_stash_policy(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(schemas.time, "sleep", sleeps.append)

    stash = Stash(tmp_path, write_delay=0.5)

    @snapclass("{self.name}.yml", stash=stash, manual=True, write_delay=0.125)
    class Item:
        name: str
        value: str = ""

    Item("a", "one").snapshot.save()

    assert sleeps == [0.125]


def test_default_write_delay_does_not_sleep(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(schemas.time, "sleep", sleeps.append)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""

    previous = sessions.WRITE_DELAY
    sessions.WRITE_DELAY = 0.0
    try:
        Item("a", "one").snapshot.save()
    finally:
        sessions.WRITE_DELAY = previous

    assert sleeps == []


def test_write_delay_applies_to_snapshot_text_setter(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(schemas.time, "sleep", sleeps.append)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str = ""

    item = Item("a", "one")

    previous = sessions.WRITE_DELAY
    sessions.WRITE_DELAY = 0.25
    try:
        item.snapshot.text = "value: direct\n"
    finally:
        sessions.WRITE_DELAY = previous

    assert sleeps == [0.25]
    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "value: direct\n"


def test_hidden_traceback_marks_patched_save_frames():
    @dataclass
    class Sample(Model):
        name: str

    previous = sessions.HIDDEN_TRACEBACK
    sessions.HIDDEN_TRACEBACK = True
    try:
        sample = Sample("a")
        with pytest.raises(RuntimeError) as exc_info:
            sample.save()
    finally:
        sessions.HIDDEN_TRACEBACK = previous

    hidden_values = [
        frame.f_locals["__tracebackhide__"]
        for frame in _traceback_frames(exc_info.value)
        if frame.f_code.co_name == "save" and "__tracebackhide__" in frame.f_locals
    ]
    assert len(hidden_values) == 2
    assert all(value is True for value in hidden_values)


def test_hidden_traceback_can_be_disabled_for_patched_save_frames():
    @dataclass
    class Sample(Model):
        name: str

    previous = sessions.HIDDEN_TRACEBACK
    sessions.HIDDEN_TRACEBACK = False
    try:
        sample = Sample("a")
        with pytest.raises(RuntimeError) as exc_info:
            sample.save()
    finally:
        sessions.HIDDEN_TRACEBACK = previous

    hidden_values = [
        frame.f_locals["__tracebackhide__"]
        for frame in _traceback_frames(exc_info.value)
        if frame.f_code.co_name == "save" and "__tracebackhide__" in frame.f_locals
    ]
    assert len(hidden_values) == 2
    assert all(value is False for value in hidden_values)


def test_hidden_traceback_marks_collection_get_frame(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str

    previous = sessions.HIDDEN_TRACEBACK
    sessions.HIDDEN_TRACEBACK = True
    try:
        with pytest.raises(FileNotFoundError) as exc_info:
            Item.snapshots.get("missing")
    finally:
        sessions.HIDDEN_TRACEBACK = previous

    hidden_values = [
        frame.f_locals["__tracebackhide__"]
        for frame in _traceback_frames(exc_info.value)
        if frame.f_code.co_name == "get" and "__tracebackhide__" in frame.f_locals
    ]
    assert hidden_values == [True]


def _traceback_frames(exc: BaseException):
    traceback = exc.__traceback__
    while traceback is not None:
        yield traceback.tb_frame
        traceback = traceback.tb_next
