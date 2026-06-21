from __future__ import annotations

import concurrent.futures
import datetime as dt
import os
from pathlib import Path
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pytest

from snapclass import SnapclassError, Missing, Stash, serializers, snapclass
from snapclass.formatters import YAMLFormatter


def test_snapshot_modified_tracks_external_file_changes(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""

    item = Item("a", "one")
    assert item.snapshot.modified is False

    time.sleep(0.01)
    (tmp_path / "a.yml").write_text("value: two\n", encoding="utf-8")
    assert item.snapshot.modified is True
    assert item.value == "two"
    assert item.snapshot.modified is False


def test_conflict_raise_refuses_to_overwrite_existing_unloaded_file(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        conflict="raise",
    )
    class Item:
        name: str
        value: str = ""

    (tmp_path / "a.yml").write_text("value: human edit\n", encoding="utf-8")
    item = Item("a", "local")

    with pytest.raises(SnapclassError, match="unloaded data"):
        item.snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "value: human edit\n"


def test_conflict_raise_refuses_to_overwrite_externally_modified_file(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        conflict="raise",
    )
    class Item:
        name: str
        value: str = ""

    item = Item("a", "one")
    item.snapshot.save()
    (tmp_path / "a.yml").write_text("value: human edit\n", encoding="utf-8")
    item.value = "local"

    with pytest.raises(SnapclassError, match="externally modified"):
        item.snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "value: human edit\n"


def test_conflict_raise_applies_to_text_setter(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        conflict="raise",
    )
    class Item:
        name: str
        value: str = ""

    item = Item("a", "one")
    item.snapshot.save()
    (tmp_path / "a.yml").write_text("value: human edit\n", encoding="utf-8")

    with pytest.raises(SnapclassError, match="externally modified"):
        item.snapshot.text = "value: local\n"

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "value: human edit\n"


def test_conflict_raise_serializes_concurrent_stale_instance_saves(tmp_path, monkeypatch):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        conflict="raise",
    )
    class Item:
        name: str
        value: str = ""

    Item("a", "old").snapshot.save()
    first = Item.snapshots.get("a")
    second = Item.snapshots.get("a")
    first.value = "first"
    second.value = "second"

    path = tmp_path / "a.yml"
    first_replace_started = threading.Event()
    release_first_replace = threading.Event()
    original_replace = Path.replace
    replace_guard = threading.Lock()
    delayed = False

    def delayed_first_replace(self: Path, target: Path) -> Path:
        nonlocal delayed
        if target == path:
            with replace_guard:
                should_delay = not delayed
                delayed = True
            if should_delay:
                first_replace_started.set()
                assert release_first_replace.wait(timeout=5)
        return original_replace(self, target)

    def save_item(item: Item) -> str:
        item.snapshot.save()
        return item.value

    monkeypatch.setattr(Path, "replace", delayed_first_replace)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(save_item, first)
        assert first_replace_started.wait(timeout=5)
        second_future = pool.submit(save_item, second)
        release_first_replace.set()

        saved = first_future.result(timeout=5)
        with pytest.raises(SnapclassError, match="externally modified"):
            second_future.result(timeout=5)

    assert saved == "first"
    assert path.read_text(encoding="utf-8") == "value: first\n"


def test_invalid_conflict_policy_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="conflict"):

        @snapclass("{self.name}.yml", stash=Stash(tmp_path), conflict="merge")
        class Item:
            name: str
            value: str = ""


def test_initial_load_preserves_explicit_non_default_constructor_value(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""

    (tmp_path / "a.yml").write_text("value: file\n", encoding="utf-8")

    item = Item("a", "constructor")
    assert item.value == "constructor"


def test_initial_load_fills_default_constructor_values_from_existing_file(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""
        count: int = 0

    (tmp_path / "a.yml").write_text("value: file\ncount: 3\n", encoding="utf-8")

    item = Item("a")

    assert item.value == "file"
    assert item.count == 3


def test_infer_mode_text_setter_adds_dynamic_scalar_and_autosaves_coerced_updates(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), infer=True)
    class Sample:
        name: str

    sample = Sample("dynamic")
    sample.snapshot.text = "count: 1"

    assert sample.count == 1

    sample.count = 4.2

    assert sample.count == 4
    assert (tmp_path / "dynamic.yml").read_text(encoding="utf-8") == "count: 4\n"


def test_infer_mode_text_setter_tracks_dynamic_lists_and_dicts(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), infer=True)
    class Sample:
        name: str

    sample = Sample("containers")
    sample.snapshot.text = "same_items: [1, 2]\ndata: {'a': 1}\n"

    assert sample.same_items == [1, 2]
    assert sample.data == {"a": 1}

    sample.same_items.append(3.2)
    sample.data["b"] = 2.3

    assert sample.same_items == [1, 2, 3]
    assert sample.data == {"a": 1, "b": 2.3}
    text = (tmp_path / "containers.yml").read_text(encoding="utf-8")
    assert "same_items:\n  - 1\n  - 2\n  - 3\n" in text
    assert YAMLFormatter.loads(text) == {
        "same_items": [1, 2, 3],
        "data": {"a": 1, "b": 2.3},
    }


def test_boolean_serializer_accepts_human_authored_words():
    true_values = ["1", "enabled", "T", "true", "Y", "yes", "on", "anything else"]
    false_values = ["0", "disabled", "F", "false", "N", "no", "off"]

    assert [serializers.Boolean.to_python_value(value) for value in true_values] == [
        True
    ] * len(true_values)
    assert [serializers.Boolean.to_python_value(value) for value in false_values] == [
        False
    ] * len(false_values)


def test_integer_serializer_truncates_decimal_strings():
    assert serializers.Integer.to_python_value("2.3") == 2
    assert serializers.List.of_type(serializers.Integer).to_python_value("1, 2.3") == [
        1,
        2,
    ]


def test_builtin_serializer_quirks_apply_to_snapclass_loads(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class FeatureFlag:
        name: str
        enabled: bool = False
        retries: int = 0

    (tmp_path / "human.yml").write_text(
        "enabled: enabled\nretries: '2.3'\n",
        encoding="utf-8",
    )

    loaded = FeatureFlag.snapshots.get("human")

    assert loaded.enabled is True
    assert loaded.retries == 2


def test_initial_load_replaces_missing_constructor_values_from_existing_file(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Prompt:
        name: str
        text: str

    (tmp_path / "Example.yml").write_text("text: hello\n", encoding="utf-8")

    prompt = Prompt("Example", Missing)  # type: ignore[arg-type]

    assert prompt.text == "hello"
    assert prompt.text is not Missing


def test_initial_load_merges_file_defaults_with_explicit_nested_constructor_value(tmp_path):
    @dataclass
    class Nested:
        title: str
        sschemas: float

    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        nested: Nested
        sschemas: float = 0.0

    (tmp_path / "a.yml").write_text("sschemas: 7\n", encoding="utf-8")

    item = Item("a", Nested("constructor", 8.0))

    assert item.sschemas == 7.0
    assert item.nested == Nested("constructor", 8.0)


def test_external_reload_overwrites_current_value(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path))
    class Item:
        name: str
        value: str = ""

    item = Item("a", "one")
    time.sleep(0.01)
    (tmp_path / "a.yml").write_text("value: external\n", encoding="utf-8")

    assert item.value == "external"


def test_fields_serializer_applies_on_save_and_load(tmp_path):
    class RoundedFloat:
        @classmethod
        def to_preserialization_data(cls, value):
            return round(float(value), 2)

        @classmethod
        def to_python_value(cls, value):
            return float(value)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, fields={"total": RoundedFloat})
    class Result:
        name: str
        total: float

    Result("a", 1.2345).snapshot.save()
    assert "total: 1.23" in (tmp_path / "a.yml").read_text(encoding="utf-8")
    assert Result.snapshots.get("a").total == 1.23


def test_fields_serializer_receives_target_object_on_save_and_load(tmp_path):
    class RelativePathSerializer:
        @classmethod
        def to_preserialization_data(cls, value, target_object):
            prefix = target_object.root + "/"
            if str(value).startswith(prefix):
                return str(value)[len(prefix):]
            return value

        @classmethod
        def to_python_value(cls, value, target_object):
            return f"{target_object.root}/{value}"

    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        fields={"root": serializers.String, "asset": RelativePathSerializer},
    )
    class AssetRef:
        name: str
        root: str
        asset: str

    AssetRef("a", "project", "project/images/icon.png").snapshot.save()

    assert "asset: images/icon.png" in (tmp_path / "a.yml").read_text(encoding="utf-8")
    assert AssetRef.snapshots.get("a").asset == "project/images/icon.png"


def test_annotation_serializer_applies_on_save_and_load(tmp_path):
    class RoundedFloat(serializers.Float):
        @classmethod
        def to_preserialization_data(cls, value, **kwargs):
            return round(super().to_preserialization_data(value, **kwargs), 2)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Result:
        name: str
        total: RoundedFloat = 0.0

    Result("a", 1.2345).snapshot.save()

    assert "total: 1.23" in (tmp_path / "a.yml").read_text(encoding="utf-8")
    assert Result.snapshots.get("a").total == 1.23


def test_number_serializer_preserves_integer_shape_and_float_values(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        value: serializers.Number = 0

    Sample("integer", 4).snapshot.save()
    Sample("float", 1.25).snapshot.save()

    assert (tmp_path / "integer.yml").read_text(encoding="utf-8") == "value: 4\n"
    assert (tmp_path / "float.yml").read_text(encoding="utf-8") == "value: 1.25\n"
    assert Sample.snapshots.get("integer").value == 4.0
    assert Sample.snapshots.get("float").value == 1.25


def test_text_serializer_writes_block_scalars_and_normalizes_loaded_text(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        body: serializers.Text = ""

    Sample("single", "Hello, world!").snapshot.save()
    assert (tmp_path / "single.yml").read_text(encoding="utf-8") == (
        "body: Hello, world!\n"
    )

    Sample("multi", "\nLine 1\nLine 2\n\n").snapshot.save()
    assert (tmp_path / "multi.yml").read_text(encoding="utf-8") == (
        "body: |\n"
        "  Line 1\n"
        "  Line 2\n"
    )

    (tmp_path / "loaded.yml").write_text(
        "body: |\n"
        "  Line 3\n"
        "  Line 4\n",
        encoding="utf-8",
    )

    assert Sample.snapshots.get("loaded").body == "Line 3\nLine 4\n"


def test_map_type_exposes_container_and_optional_serializer_surface():
    list_serializer = serializers.map_type(list[str])
    dict_serializer = serializers.map_type(dict[str, int])
    optional_serializer = serializers.map_type(Optional[str])

    assert list_serializer.__name__ == "StringList"
    assert list_serializer.SERIALIZER is serializers.String
    assert list_serializer.to_python_value("a, b") == ["a", " b"]
    assert list_serializer.to_preserialization_data({"b", "a"}) == ["a", "b"]

    assert dict_serializer.__name__ == "StringIntegerDict"
    assert dict_serializer.to_python_value({"a": 1}) == {"a": 1}

    assert optional_serializer.__name__ == "OptionalString"
    assert optional_serializer.DEFAULT is None
    assert optional_serializer.to_python_value(None) is None
    assert optional_serializer.to_preserialization_data("x") == "x"


def test_map_type_exposes_optional_builtin_serializers():
    optional_bool = serializers.map_type(Optional[bool])
    optional_int = serializers.map_type(Optional[int])
    optional_float = serializers.map_type(Optional[float])
    optional_str = serializers.map_type(Optional[str])

    assert optional_bool.__name__ == "OptionalBoolean"
    assert optional_bool.DEFAULT is None
    assert optional_bool.to_python_value(None) is None
    assert optional_bool.to_python_value("yes") is True
    assert optional_bool.to_preserialization_data(None) is None
    assert optional_bool.to_preserialization_data(0) is False

    assert optional_int.__name__ == "OptionalInteger"
    assert optional_int.DEFAULT is None
    assert optional_int.to_python_value(None) is None
    assert optional_int.to_python_value("7") == 7
    assert optional_int.to_preserialization_data(None) is None
    assert optional_int.to_preserialization_data("8") == 8

    assert optional_float.__name__ == "OptionalFloat"
    assert optional_float.DEFAULT is None
    assert optional_float.to_python_value(None) is None
    assert optional_float.to_python_value("1.25") == 1.25
    assert optional_float.to_preserialization_data(None) is None
    assert optional_float.to_preserialization_data("2.5") == 2.5

    assert optional_str.__name__ == "OptionalString"
    assert optional_str.DEFAULT is None
    assert optional_str.to_python_value(None) is None
    assert optional_str.to_python_value(3) == "3"
    assert optional_str.to_preserialization_data(None) is None
    assert optional_str.to_preserialization_data(4) == "4"


def test_map_type_exposes_dataclass_and_enum_serializer_surface():
    @dataclass
    class Nested:
        count: int
        flag: bool = False

    class Color(Enum):
        red = "red"
        blue = "blue"

    nested_serializer = serializers.map_type(Nested)
    enum_serializer = serializers.map_type(Color)

    assert nested_serializer.__name__ == "NestedSerializer"
    assert nested_serializer.SERIALIZERS == {
        "count": serializers.Integer,
        "flag": serializers.Boolean,
    }
    assert nested_serializer.to_python_value({"count": "3"}) == Nested(count=3, flag=False)
    assert nested_serializer.to_preserialization_data(Nested(4, True)) == {
        "count": 4,
        "flag": True,
    }

    assert enum_serializer.__name__ == "ColorSerializer"
    assert enum_serializer.to_python_value("red") is Color.red
    assert enum_serializer.to_preserialization_data(Color.blue) == "blue"


def test_explicit_container_serializer_round_trips_through_snapshot_fields(tmp_path):
    string_list = serializers.List.of_type(serializers.String)

    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        fields={"tags": string_list},
        manual=True,
    )
    class Sample:
        name: str
        tags: list[str]

    Sample("a", {"beta", "alpha"}).snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == (
        "tags:\n"
        "  - alpha\n"
        "  - beta\n"
    )
    assert Sample.snapshots.get("a").tags == ["alpha", "beta"]


def test_registered_serializer_applies_for_external_type(tmp_path):
    class Money:
        def __init__(self, currency: str, amount: float) -> None:
            self.currency = currency
            self.amount = amount

        def __eq__(self, other):
            return (
                isinstance(other, Money)
                and self.currency == other.currency
                and self.amount == other.amount
            )

    class MoneySerializer(serializers.Serializer):
        @classmethod
        def to_preserialization_data(cls, value, **_kwargs):
            return f"{value.currency} {value.amount:.2f}"

        @classmethod
        def to_python_value(cls, value, **_kwargs):
            currency, amount = str(value).split()
            return Money(currency, float(amount))

    serializers.register(Money, MoneySerializer)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Invoice:
        name: str
        total: Money

    Invoice("a", Money("USD", 12.5)).snapshot.save()

    assert "total: USD 12.50" in (tmp_path / "a.yml").read_text(encoding="utf-8")
    assert Invoice.snapshots.get("a").total == Money("USD", 12.5)


def test_serializer_subclass_annotation_round_trips_and_loads_from_text(tmp_path):
    class MyDateTime(serializers.Serializer, dt.datetime):
        @classmethod
        def to_preserialization_data(cls, value, **_kwargs):
            return value.isoformat()

        @classmethod
        def to_python_value(cls, value, **_kwargs):
            return cls.fromisoformat(str(value))

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Timestamp:
        name: str
        when: MyDateTime

    timestamp = Timestamp("sample", MyDateTime(2026, 3, 1, 12, 30))
    timestamp.snapshot.save()

    assert timestamp.snapshot.text == "when: '2026-03-01T12:30:00'\n"

    loaded = Timestamp.snapshots.get("sample")
    assert loaded.when == MyDateTime(2026, 3, 1, 12, 30)

    loaded.snapshot.text = "when: '2026-03-02T08:15:00'\n"
    assert loaded.when == MyDateTime(2026, 3, 2, 8, 15)


def test_registered_serializer_with_default_round_trips_and_loads_from_text(tmp_path):
    class LedgerStamp:
        def __init__(self, value: dt.datetime) -> None:
            self.value = value

        def __eq__(self, other):
            return isinstance(other, LedgerStamp) and self.value == other.value

    class LedgerStampSerializer(serializers.Serializer):
        @classmethod
        def to_preserialization_data(cls, value, **_kwargs):
            return "ledger:" + value.value.isoformat()

        @classmethod
        def to_python_value(cls, value, **_kwargs):
            return LedgerStamp(dt.datetime.fromisoformat(str(value).removeprefix("ledger:")))

    serializers.register(LedgerStamp, LedgerStampSerializer)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Timestamp:
        name: str
        when: LedgerStamp | None = None

    Timestamp("sample", LedgerStamp(dt.datetime(2026, 3, 1, 12, 30))).snapshot.save()

    assert (tmp_path / "sample.yml").read_text(encoding="utf-8") == (
        "when: ledger:2026-03-01T12:30:00\n"
    )
    assert Timestamp.snapshots.get("sample").when == LedgerStamp(
        dt.datetime(2026, 3, 1, 12, 30)
    )

    loaded = Timestamp("sample")
    loaded.snapshot.text = "when: ledger:2026-03-02T08:15:00\n"
    assert loaded.when == LedgerStamp(dt.datetime(2026, 3, 2, 8, 15))


def test_registered_serializer_applies_for_unresolved_string_annotation(tmp_path):
    class Money:
        def __init__(self, currency: str, amount: float) -> None:
            self.currency = currency
            self.amount = amount

        def __eq__(self, other):
            return (
                isinstance(other, Money)
                and self.currency == other.currency
                and self.amount == other.amount
            )

    class MoneySerializer(serializers.Serializer):
        @classmethod
        def to_preserialization_data(cls, value, **_kwargs):
            return f"{value.currency} {value.amount:.2f}"

        @classmethod
        def to_python_value(cls, value, **_kwargs):
            currency, amount = str(value).split()
            return Money(currency, float(amount))

    serializers.register("LedgerMoney", MoneySerializer)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Invoice:
        name: str
        count: int
        total: "LedgerMoney"

    Invoice("a", 2, Money("USD", 12.5)).snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == (
        "count: 2\n"
        "total: USD 12.50\n"
    )
    loaded = Invoice.snapshots.get("a")
    assert loaded.count == 2
    assert loaded.total == Money("USD", 12.5)


def test_registering_type_also_registers_class_name():
    class SnapclassRegistryNameTestToken:
        pass

    class TokenSerializer(serializers.Serializer):
        pass

    serializers.register(SnapclassRegistryNameTestToken, TokenSerializer)

    assert serializers.serializer_for_hint("SnapclassRegistryNameTestToken") is TokenSerializer
