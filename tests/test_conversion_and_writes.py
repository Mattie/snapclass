from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import datetime as dt
from enum import Enum
from pathlib import Path
from typing import Generic, TypedDict, TypeVar

import pytest

from snapclass import SnapclassError, Stash, serializers, snapclass
from snapclass.formatters import FileFormatter, YAMLFormatter


def test_nested_dataclass_and_optional_values_coerce_on_load(tmp_path):
    @dataclass
    class ChatParams:
        model: str
        max_tokens: int | None = None

    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Chat:
        name: str
        params: ChatParams

    (tmp_path / "Popsicle.yml").write_text(
        "params:\n  model: gpt-5-chat-latest\n  max_tokens: 42\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")
    assert chat.params == ChatParams("gpt-5-chat-latest", 42)
    assert isinstance(chat.params.max_tokens, int)


def test_list_of_nested_dataclasses_coerces_on_load(tmp_path):
    @dataclass
    class Era:
        name: str
        year: int

    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Timeline:
        name: str
        eras: list[Era] = field(default_factory=list)

    (tmp_path / "main.yml").write_text(
        "eras:\n  - name: Founding\n    year: 100\n",
        encoding="utf-8",
    )

    timeline = Timeline.snapshots.get("main")
    assert timeline.eras == [Era("Founding", 100)]
    assert isinstance(timeline.eras[0].year, int)


def test_enum_datetime_date_and_path_round_trip(tmp_path):
    class Status(Enum):
        DRAFT = "draft"
        DONE = "done"

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, defaults=True)
    class Snapshot:
        name: str
        status: Status
        timestamp: dt.datetime
        day: dt.date
        path: Path

    timestamp = dt.datetime(2026, 3, 1, 12, 30, 5, tzinfo=dt.timezone.utc)
    Snapshot(
        "event",
        Status.DONE,
        timestamp,
        dt.date(2026, 3, 1),
        Path("content/body.md"),
    ).snapshot.save()

    text = (tmp_path / "event.yml").read_text(encoding="utf-8")
    assert "status: done" in text
    assert "timestamp: '2026-03-01T12:30:05+00:00'" in text
    assert "day: '2026-03-01'" in text
    assert "path: content\\body.md" in text or "path: content/body.md" in text

    loaded = Snapshot.snapshots.get("event")
    assert loaded.status is Status.DONE
    assert loaded.timestamp == timestamp
    assert loaded.day == dt.date(2026, 3, 1)
    assert loaded.path == Path("content/body.md")


def test_optional_enum_values_load_and_save_null(tmp_path):
    class Status(Enum):
        DRAFT = "draft"
        DONE = "done"

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, defaults=True)
    class Snapshot:
        name: str
        status: Status | None = None

    (tmp_path / "draft.yml").write_text("status: draft\n", encoding="utf-8")
    (tmp_path / "blank.yml").write_text("status:\n", encoding="utf-8")

    assert Snapshot.snapshots.get("draft").status is Status.DRAFT
    assert Snapshot.snapshots.get("blank").status is None

    Snapshot("saved", None).snapshot.save()
    saved = YAMLFormatter.loads((tmp_path / "saved.yml").read_text(encoding="utf-8"))
    assert saved == {"status": None}


def test_nested_workflow_event_datetime_coerces_on_load(tmp_path):
    @dataclass
    class Event:
        type: str
        timestamp: dt.datetime

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Workflow:
        name: str
        events: list[Event] = field(default_factory=list)

    (tmp_path / "wf.yml").write_text(
        "events:\n"
        "  - type: step_started\n"
        "    timestamp: '2026-03-01T12:30:05+00:00'\n",
        encoding="utf-8",
    )

    workflow = Workflow.snapshots.get("wf")
    assert workflow.events == [
        Event("step_started", dt.datetime(2026, 3, 1, 12, 30, 5, tzinfo=dt.timezone.utc))
    ]


def test_optional_nested_dataclass_can_load_null(tmp_path):
    @dataclass
    class Params:
        model: str = "gpt-5-chat-latest"

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Chat:
        name: str
        params: Params | None = field(default_factory=Params)

    (tmp_path / "none.yml").write_text("params:\n", encoding="utf-8")
    (tmp_path / "set.yml").write_text("params:\n  model: compact\n", encoding="utf-8")

    assert Chat.snapshots.get("none").params is None
    assert Chat.snapshots.get("set").params == Params("compact")


def test_deeply_nested_dataclasses_load_recursively(tmp_path):
    @dataclass
    class Leaf:
        value: int = 0

    @dataclass
    class Level3:
        leaf: Leaf = field(default_factory=Leaf)

    @dataclass
    class Level2:
        level3: Level3 = field(default_factory=Level3)

    @dataclass
    class Level1:
        level2: Level2 = field(default_factory=Level2)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Document:
        name: str
        level1: Level1 = field(default_factory=Level1)

    (tmp_path / "deep.yml").write_text(
        "level1:\n"
        "  level2:\n"
        "    level3:\n"
        "      leaf:\n"
        "        value: '9'\n",
        encoding="utf-8",
    )

    loaded = Document.snapshots.get("deep")

    assert loaded.level1.level2.level3.leaf == Leaf(9)
    assert isinstance(loaded.level1.level2.level3.leaf.value, int)


def test_sets_serialize_deterministically_and_coerce_on_load(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Article:
        name: str
        tags: set[str] = field(default_factory=set)
        frozen_tags: frozenset[str] = frozenset()

    Article("alpha", {"zeta", "alpha", "middle"}, frozenset({"b", "a"})).snapshot.save()

    text = (tmp_path / "alpha.yml").read_text(encoding="utf-8")
    data = YAMLFormatter.loads(text)
    assert data["tags"] == ["alpha", "middle", "zeta"]
    assert data["frozen_tags"] == ["a", "b"]

    loaded = Article.snapshots.get("alpha")
    assert loaded.tags == {"alpha", "middle", "zeta"}
    assert loaded.frozen_tags == frozenset({"a", "b"})
    assert isinstance(loaded.tags, set)
    assert isinstance(loaded.frozen_tags, frozenset)


def test_mapping_annotations_coerce_keys_and_values_on_load(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sschemass:
        name: str
        values: Mapping[int, float]

    (tmp_path / "round.yml").write_text(
        "values:\n  '1': '2.5'\n  '3': 4\n",
        encoding="utf-8",
    )

    loaded = Sschemass.snapshots.get("round")
    assert loaded.values == {1: 2.5, 3: 4.0}
    assert all(isinstance(key, int) for key in loaded.values)
    assert all(isinstance(value, float) for value in loaded.values.values())


def test_generic_custom_serializer_annotation_round_trips(tmp_path):
    first_type = TypeVar("first_type")
    second_type = TypeVar("second_type")

    class Pair(Generic[first_type, second_type], serializers.Serializer):
        first: first_type
        second: second_type

        def __init__(self, first: first_type, second: second_type) -> None:
            self.first = first
            self.second = second

        @classmethod
        def to_python_value(cls, deserialized_data, *, target_object=None):
            values = [
                serializer.to_python_value(value)
                for serializer, value in zip(cls.SERIALIZERS, deserialized_data)
            ]
            return cls(*values)

        @classmethod
        def to_preserialization_data(cls, python_value, *, default_to_skip=None):
            values = [python_value.first, python_value.second]
            return [
                serializer.to_preserialization_data(value)
                for serializer, value in zip(cls.SERIALIZERS, values)
            ]

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Record:
        name: str
        contents: list[Pair[str, serializers.Number]]

    Record("sample", [Pair[str, serializers.Number]("pi", 3.14)]).snapshot.save()

    data = YAMLFormatter.loads((tmp_path / "sample.yml").read_text(encoding="utf-8"))
    assert data == {"contents": [["pi", 3.14]]}

    loaded = Record.snapshots.get("sample")
    assert loaded.contents[0].first == "pi"
    assert loaded.contents[0].second == 3.14

    loaded.snapshot.text = "contents:\n  - [degrees, 360]\n"
    assert loaded.contents[0].first == "degrees"
    assert loaded.contents[0].second == 360


def test_typeddict_annotations_load_as_plain_dicts_with_warning(tmp_path):
    class Metadata(TypedDict):
        rating: int

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Article:
        name: str
        metadata: Metadata

    (tmp_path / "alpha.yml").write_text(
        "metadata:\n  rating: '5'\n  extra: kept\n",
        encoding="utf-8",
    )

    with pytest.warns(RuntimeWarning, match="TypedDict annotation Metadata"):
        loaded = Article.snapshots.get("alpha")

    assert loaded.metadata == {"rating": "5", "extra": "kept"}
    assert isinstance(loaded.metadata, dict)


def test_init_false_fields_are_excluded_from_inferred_attributes(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, infer=True)
    class Item:
        name: str
        value: str = ""
        runtime: str = field(default="", init=False)

    (tmp_path / "sample.yml").write_text(
        "value: loaded\n"
        "runtime: transient\n"
        "extra: kept\n",
        encoding="utf-8",
    )

    item = Item.snapshots.get("sample")
    item.snapshot.save()

    text = (tmp_path / "sample.yml").read_text(encoding="utf-8")
    assert item.value == "loaded"
    assert item.extra == "kept"
    assert "extra: kept" in text
    assert "runtime:" not in text


def test_non_optional_none_coerces_to_builtin_defaults(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Params:
        name: str
        enabled: bool
        count: int
        ratio: float
        label: str
        optional_count: int | None = None

    (tmp_path / "defaults.yml").write_text(
        "enabled:\n"
        "count:\n"
        "ratio:\n"
        "label:\n"
        "optional_count:\n",
        encoding="utf-8",
    )

    loaded = Params.snapshots.get("defaults")
    assert loaded.enabled is False
    assert loaded.count == 0
    assert loaded.ratio == 0.0
    assert loaded.label == ""
    assert loaded.optional_count is None


def test_optional_builtin_values_load_save_and_preserve_nulls(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, defaults=True)
    class Params:
        name: str
        enabled: bool | None = None
        count: int | None = None
        ratio: float | None = None
        label: str | None = None

    (tmp_path / "coerced.yml").write_text(
        "enabled: yes\n"
        "count: '7'\n"
        "ratio: '1.25'\n"
        "label: 99\n",
        encoding="utf-8",
    )
    (tmp_path / "blank.yml").write_text(
        "enabled:\n"
        "count:\n"
        "ratio:\n"
        "label:\n",
        encoding="utf-8",
    )

    coerced = Params.snapshots.get("coerced")
    assert coerced.enabled is True
    assert coerced.count == 7
    assert coerced.ratio == 1.25
    assert coerced.label == "99"

    blank = Params.snapshots.get("blank")
    assert blank.enabled is None
    assert blank.count is None
    assert blank.ratio is None
    assert blank.label is None

    Params("saved", None, None, None, None).snapshot.save()
    assert YAMLFormatter.loads((tmp_path / "saved.yml").read_text(encoding="utf-8")) == {
        "enabled": None,
        "count": None,
        "ratio": None,
        "label": None,
    }


def test_unions_coerce_in_annotation_order_and_preserve_exact_matches(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Params:
        name: str
        numeric: int | float
        float_first: float | int
        float_from_string: float | int
        string_first: str | int
        int_first: int | str

    (tmp_path / "unions.yml").write_text(
        "numeric: '3'\n"
        "float_first: 4\n"
        "float_from_string: '4'\n"
        "string_first: '5'\n"
        "int_first: '6'\n",
        encoding="utf-8",
    )

    loaded = Params.snapshots.get("unions")

    assert loaded.numeric == 3
    assert type(loaded.numeric) is int
    assert loaded.float_first == 4
    assert type(loaded.float_first) is int
    assert loaded.float_from_string == 4.0
    assert type(loaded.float_from_string) is float
    assert loaded.string_first == "5"
    assert loaded.int_first == 6
    assert type(loaded.int_first) is int


def test_lists_load_from_scalar_and_comma_separated_values(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Params:
        name: str
        stop: list[str] = field(default_factory=list)
        penalties: list[float] = field(default_factory=list)

    (tmp_path / "single.yml").write_text(
        "stop: END\npenalties: 0.5, 1.25\n",
        encoding="utf-8",
    )
    single = Params.snapshots.get("single")
    assert single.stop == ["END"]
    assert single.penalties == [0.5, 1.25]

    (tmp_path / "many.yml").write_text(
        "stop: one, two, three\npenalties: 1\n",
        encoding="utf-8",
    )
    many = Params.snapshots.get("many")
    assert many.stop == ["one", "two", "three"]
    assert many.penalties == [1.0]

    (tmp_path / "empty.yml").write_text(
        "stop:\npenalties:\n",
        encoding="utf-8",
    )
    empty = Params.snapshots.get("empty")
    assert empty.stop == []
    assert empty.penalties == []


def test_schema_mismatch_reports_file_field_expected_type_and_value(tmp_path):
    @dataclass
    class Era:
        name: str
        year: int

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Timeline:
        name: str
        eras: list[Era] = field(default_factory=list)

    path = tmp_path / "main.yml"
    path.write_text(
        "eras:\n  - name: Founding\n    year: someday\n",
        encoding="utf-8",
    )

    with pytest.raises(SnapclassError) as exc_info:
        Timeline.snapshots.get("main")

    message = str(exc_info.value)
    assert str(path) in message
    assert "eras[0].year" in message
    assert "int" in message
    assert "str" in message
    assert "someday" in message


def test_schema_mismatch_reports_custom_serializer_failures(tmp_path):
    class Money:
        def __init__(self, cents: int) -> None:
            self.cents = cents

    class MoneySerializer(serializers.Serializer):
        @classmethod
        def to_python_value(cls, value, **_kwargs):
            if not str(value).startswith("$"):
                raise ValueError("money values must start with $")
            return Money(int(float(str(value)[1:]) * 100))

        @classmethod
        def to_preserialization_data(cls, value, **_kwargs):
            return f"${value.cents / 100:.2f}"

    serializers.register(Money, MoneySerializer)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Invoice:
        name: str
        total: Money

    path = tmp_path / "bad.yml"
    path.write_text("total: twelve\n", encoding="utf-8")

    with pytest.raises(SnapclassError) as exc_info:
        Invoice.snapshots.get("bad")

    message = str(exc_info.value)
    assert str(path) in message
    assert "total" in message
    assert "MoneySerializer" in message
    assert "twelve" in message
    assert "money values must start with $" in message


def test_failed_save_preserves_existing_file(tmp_path):
    class ExplodingFileFormatter(FileFormatter):
        extensions = {".boom"}

        @classmethod
        def loads(cls, text: str):
            return {"value": text.strip()}

        @classmethod
        def dumps(cls, data):
            if data["value"] == "explode":
                raise RuntimeError("boom")
            return data["value"] + "\n"

    @snapclass("{self.name}.boom", stash=Stash(tmp_path), manual=True, formatter=ExplodingFileFormatter)
    class Item:
        name: str
        value: str

    item = Item("a", "old")
    item.snapshot.save()
    item.value = "explode"

    with pytest.raises(RuntimeError, match="boom"):
        item.snapshot.save()

    assert (tmp_path / "a.boom").read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_replace_failure_preserves_existing_file_and_cleans_temp(tmp_path, monkeypatch):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str
        value: str

    item = Item("a", "old")
    item.snapshot.save()

    original_replace = Path.replace

    def fail_atomic_replace(self: Path, target: Path) -> Path:
        if self.name.startswith(".a.yml.") and self.suffix == ".tmp":
            raise OSError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_atomic_replace)
    item.value = "new"

    with pytest.raises(OSError, match="replace failed"):
        item.snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "value: old\n"
    assert not list(tmp_path.glob("*.tmp"))
