from __future__ import annotations

import importlib.resources
from dataclasses import FrozenInstanceError, field, is_dataclass
from typing import IO

import pytest

from snapclass import Stash, auto, snapclass, formatters, plugins


def test_snapclass_decorator_supports_stash_bound_models(tmp_path):
    root = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str = ""

    Prompt("Popsicle", "hello").snapshot.save()

    assert Prompt.snapshots.get("Popsicle").text == "hello"
    assert (tmp_path / "prompts" / "Popsicle.yml").exists()


def test_snapclass_init_false_preserves_custom_initializer_and_collections(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True, init=False)
    class Chat:
        name: str = field(default_factory=lambda: "Generated")
        messages: list[dict] = field(default_factory=list)

        def __init__(self, *args, **kwargs):
            if len(args) == 2:
                self.name = args[0]
                self.messages = [{"system": args[1]}]
            else:
                self.name = kwargs.get("name", self.__dataclass_fields__["name"].default_factory())
                self.messages = kwargs.get("messages", [])

    chat = Chat("Popsicle", "Follow house style.")
    chat.snapshot.save()

    loaded = Chat.snapshots.get("Popsicle")

    assert chat.messages == [{"system": "Follow house style."}]
    assert loaded.messages == [{"system": "Follow house style."}]
    assert loaded.snapshot.path == tmp_path / "Popsicle.yml"


def test_bare_snapclass_decorator_is_dataclass_replacement_for_nested_values(tmp_path):
    @snapclass
    class Params:
        model: str = "gpt-5-chat-latest"
        temperature: float = 0.0

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Chat:
        name: str
        params: Params

    Chat("Popsicle", Params(temperature=0.2)).snapshot.save()

    loaded = Chat.snapshots.get("Popsicle")
    assert is_dataclass(Params)
    assert loaded.params == Params("gpt-5-chat-latest", 0.2)
    assert (tmp_path / "Popsicle.yml").read_text(encoding="utf-8") == (
        "params:\n"
        "  model: gpt-5-chat-latest\n"
        "  temperature: 0.2\n"
    )


def test_snapclass_without_pattern_accepts_dataclass_kwargs():
    @snapclass(frozen=True)
    class Params:
        model: str = "gpt-5-chat-latest"

    params = Params()

    assert is_dataclass(Params)
    assert params.model == "gpt-5-chat-latest"
    assert not hasattr(params, "snapshot")
    with pytest.raises(FrozenInstanceError):
        params.model = "other"


def test_package_declares_pep561_typing_marker():
    assert (importlib.resources.files("snapclass") / "py.typed").is_file()


def test_plugins_export_mypy_entrypoint():
    assert plugins.DECORATOR_SUFFIXES == (".snapclass",)
    assert plugins.mypy("1.18.1").__name__ in {"SnapclassPlugin", "MypyUnavailablePlugin"}


def test_global_register_apis_are_not_public():
    assert not hasattr(formatters, "register")


def test_stash_formatter_feeds_snapshot_load_and_save(tmp_path):
    class PipeFormatter(formatters.Formatter):
        @classmethod
        def extensions(cls) -> set[str]:
            return {".pipe"}

        @classmethod
        def deserialize(cls, file_object: IO[str]) -> dict:
            data = {}
            for line in file_object:
                key, value = line.rstrip("\n").split("|", 1)
                data[key] = value
            return data

        @classmethod
        def serialize(cls, data: dict) -> str:
            return "".join(f"{key}|{value}\n" for key, value in data.items())

    stash = Stash(tmp_path, formatters={".pipe": PipeFormatter})

    @snapclass("{self.name}.pipe", stash=stash, manual=True)
    class Prompt:
        name: str
        text: str = ""

    Prompt("Scratch", "first").snapshot.save()
    assert (tmp_path / "Scratch.pipe").read_text(encoding="utf-8") == "text|first\n"

    (tmp_path / "Popsicle.pipe").write_text("text|loaded\n", encoding="utf-8")
    assert Prompt.snapshots.get("Popsicle").text == "loaded"


def test_stash_formatter_snapshot_load_passes_real_named_file_object(tmp_path):
    class ReopeningFormatter(formatters.Formatter):
        @classmethod
        def extensions(cls) -> set[str]:
            return {".reopen"}

        @classmethod
        def deserialize(cls, file_object: IO[str]) -> dict:
            with open(file_object.name, "r", encoding="utf-8") as reopened:
                key, value = reopened.read().strip().split("|", 1)
            return {key: value}

        @classmethod
        def serialize(cls, data: dict) -> str:
            return "".join(f"{key}|{value}\n" for key, value in data.items())

    stash = Stash(tmp_path, formatters={".reopen": ReopeningFormatter})

    @snapclass("{self.name}.reopen", stash=stash, manual=True)
    class Prompt:
        name: str
        text: str = ""

    (tmp_path / "Popsicle.reopen").write_text("text|loaded by name\n", encoding="utf-8")

    assert Prompt.snapshots.get("Popsicle").text == "loaded by name"


def test_formatters_format_alias_supports_stale_docs_base_class(tmp_path):
    class PipeFormat(formatters.Format):
        @classmethod
        def extensions(cls) -> set[str]:
            return {".legacy"}

        @classmethod
        def deserialize(cls, file_object: IO[str]) -> dict:
            key, value = file_object.read().split("=", 1)
            return {key: value}

        @classmethod
        def serialize(cls, data: dict) -> str:
            return f"text={data['text']}"

    stash = Stash(tmp_path, formatters={".legacy": PipeFormat})

    @snapclass("{self.name}.legacy", stash=stash, manual=True)
    class Prompt:
        name: str
        text: str = ""

    Prompt("Scratch", "first").snapshot.save()
    (tmp_path / "Popsicle.legacy").write_text("text=loaded", encoding="utf-8")

    assert formatters.Format is formatters.Formatter
    assert (tmp_path / "Scratch.legacy").read_text(encoding="utf-8") == "text=first"
    assert Prompt.snapshots.get("Popsicle").text == "loaded"


def test_stash_formatters_can_map_formatter_class_for_all_extensions(tmp_path):
    class MultiFormatter(formatters.Formatter):
        @classmethod
        def extensions(cls) -> set[str]:
            return {".multi", ".multi2"}

        @classmethod
        def deserialize(cls, file_object: IO[str]) -> dict:
            key, value = file_object.read().strip().split("=", 1)
            return {key: value}

        @classmethod
        def serialize(cls, data: dict) -> str:
            key, value = next(iter(data.items()))
            return f"{key}={value}\n"

    stash = Stash(
        tmp_path,
        formatters={extension: MultiFormatter for extension in MultiFormatter.extensions()},
    )

    @snapclass("{self.name}.multi", stash=stash, manual=True)
    class First:
        name: str
        text: str = ""

    @snapclass("{self.name}.multi2", stash=stash, manual=True)
    class Second:
        name: str
        text: str = ""

    First("a", "one").snapshot.save()
    (tmp_path / "b.multi2").write_text("text=two\n", encoding="utf-8")

    assert (tmp_path / "a.multi").read_text(encoding="utf-8") == "text=one\n"
    assert Second.snapshots.get("b").text == "two"


def test_formatters_serialize_matches_readable_json_shape():
    assert formatters.serialize({"value": 1}, ".json") == '{\n  "value": 1\n}'


def test_formatters_json5_accepts_comments(tmp_path):
    path = tmp_path / "config.json5"
    path.write_text("{// comment\nvalue: 1, label: 'ok'}", encoding="utf-8")

    assert formatters.deserialize(path, ".json5") == {"value": 1, "label": "ok"}
    path.write_text(formatters.serialize({"value": 1}, ".json5"), encoding="utf-8")
    assert formatters.deserialize(path, ".json5") == {"value": 1}


def test_app_owned_stash_formatter_can_keep_compact_yaml_shape(tmp_path):
    class CompactTurnsFormatter(formatters.YAML):
        @classmethod
        def deserialize(cls, file_object: IO[str]) -> dict:
            data = super().deserialize(file_object)
            messages = data.get("messages")
            if isinstance(messages, list):
                data["messages"] = [
                    {"system" if "developer" in item else next(iter(item)): next(iter(item.values()))}
                    for item in messages
                ]
            return data

        @classmethod
        def serialize(cls, data: dict) -> str:
            data = dict(data)
            messages = data.get("messages")
            if isinstance(messages, list):
                data["messages"] = [
                    {role: content.get("text") if isinstance(content, dict) else content}
                    for item in messages
                    for role, content in item.items()
                ]
            return super().serialize(data)

    stash = Stash(tmp_path, formatters={".yml": CompactTurnsFormatter})

    @snapclass("{self.name}.yml", stash=stash, manual=True)
    class Chat:
        name: str
        messages: list[dict]

    (tmp_path / "Popsicle.yml").write_text(
        "messages:\n  - developer: Follow house style.\n", encoding="utf-8"
    )
    chat = Chat.snapshots.get("Popsicle")
    assert chat.messages == [{"system": "Follow house style."}]

    chat.messages = [{"assistant": {"text": "Done."}}]
    chat.snapshot.save()
    saved = (tmp_path / "Popsicle.yml").read_text(encoding="utf-8")
    assert "assistant: Done." in saved
    assert "text:" not in saved


def test_auto_loads_inferred_fields_and_saves_new_assignments(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "settings.yml").write_text("theme: readable\ncount: 2\n", encoding="utf-8")

    settings = auto("settings.yml")

    assert settings.theme == "readable"
    assert settings.count == 2

    settings.mode = "draft"
    saved = (tmp_path / "settings.yml").read_text(encoding="utf-8")
    assert "mode: draft" in saved


def test_auto_nested_dict_keys_are_accessible_as_attributes_and_save(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "settings.yml").write_text(
        "database:\n  host: localhost\n  ports:\n    primary: 5432\n",
        encoding="utf-8",
    )

    settings = auto("settings.yml")

    assert settings.database.host == "localhost"
    assert settings.database.ports.primary == 5432

    settings.database.host = "db.local"
    settings.database.ports.replica = 5433
    saved = (tmp_path / "settings.yml").read_text(encoding="utf-8")
    assert "host: db.local" in saved
    assert "replica: 5433" in saved


def test_auto_infers_list_item_serializers_from_existing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "settings.yml").write_text(
        "homogeneous_list:\n"
        "  - 1\n"
        "  - 2\n"
        "heterogeneous_list:\n"
        "  - 1\n"
        "  - 'abc'\n"
        "empty_list: []\n",
        encoding="utf-8",
    )

    settings = auto("settings.yml")
    settings.homogeneous_list.append(3.4)
    settings.heterogeneous_list.append(5.6)
    settings.empty_list.append(7.8)

    assert settings.homogeneous_list == [1, 2, 3]
    assert settings.heterogeneous_list == [1, "abc", 5.6]
    assert settings.empty_list == [7.8]
    assert (tmp_path / "settings.yml").read_text(encoding="utf-8") == (
        "homogeneous_list:\n"
        "  - 1\n"
        "  - 2\n"
        "  - 3\n"
        "heterogeneous_list:\n"
        "  - 1\n"
        "  - 'abc'\n"
        "  - 5.6\n"
        "empty_list:\n"
        "  - 7.8\n"
    )
