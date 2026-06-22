from __future__ import annotations

import os
from dataclasses import field
from pathlib import Path

import pytest

from snapclass import Stash, serializers, snapclass
from snapclass import formatters
from snapclass.formatters import FileFormatter


def test_bind_propagates_to_parent_stashes(tmp_path):
    root = Stash(tmp_path / ".lorebubble")
    world = root / "{world}"
    articles = world / "article"

    bound_articles = articles.bind(world="Storybook")
    assert bound_articles.path == tmp_path / ".lorebubble" / "Storybook" / "article"


def test_stash_placeholders_allow_spaces_and_safe_punctuation(tmp_path):
    worlds = Stash(tmp_path) / "{world}" / "articles"

    assert worlds.bind(world="Dusk Court, Part 1").path == (
        tmp_path / "Dusk Court, Part 1" / "articles"
    )


@pytest.mark.parametrize(
    "value",
    [
        "../escape",
        "nested/name",
        "nested\\name",
        ".",
        "..",
        "",
        "bad:name",
        "question?",
        "star*",
        "CON",
        "nul.txt",
    ],
)
def test_stash_placeholders_reject_unsafe_bound_values(tmp_path, value):
    worlds = Stash(tmp_path) / "{world}" / "articles"

    with pytest.raises(ValueError, match="placeholder"):
        worlds.bind(world=value).path


def test_stash_placeholders_are_sanitized_in_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREBUBBLE_WORLD_DIR", str(tmp_path / "{world}" / "article"))
    worlds = Stash("unused/{world}", env="LOREBUBBLE_WORLD_DIR")

    with pytest.raises(ValueError, match="placeholder"):
        worlds.bind(world="bad/name").path


def test_unbound_stash_placeholders_list_all_missing_names(tmp_path):
    articles = Stash(tmp_path) / "{world}" / "{section}" / "articles"

    with pytest.raises(ValueError) as error:
        articles.path

    message = str(error.value)
    assert "world" in message
    assert "section" in message


def test_collection_get_unbound_stash_placeholder_names_model_and_binding(tmp_path):
    worlds = Stash(tmp_path) / "{world}" / "articles"

    @snapclass("{self.slug}.yml", stash=worlds, manual=True)
    class Article:
        slug: str

    with pytest.raises(ValueError) as error:
        Article.snapshots.get("dusk-court")

    message = str(error.value)
    assert "Article" in message
    assert "world" in message


def test_collection_all_unbound_stash_placeholder_names_model_and_binding(tmp_path):
    worlds = Stash(tmp_path) / "{world}" / "articles"

    @snapclass("{self.slug}.yml", stash=worlds, manual=True)
    class Article:
        slug: str

    with pytest.raises(ValueError) as error:
        list(Article.snapshots.all())

    message = str(error.value)
    assert "Article" in message
    assert "world" in message


def test_stash_describe_reports_env_external_status_and_parent_chain(tmp_path, monkeypatch):
    root = Stash(tmp_path / "app", env="APP_ROOT")
    logs = root / Stash("logs", env="APP_LOGS")
    external = tmp_path / "external-logs"

    monkeypatch.setenv("APP_ROOT", str(tmp_path / "run"))
    monkeypatch.setenv("APP_LOGS", str(external))

    description = logs.describe()

    assert description["path"] == external
    assert description["source"] == "env:APP_LOGS"
    assert description["env"] == "APP_LOGS"
    assert description["is_external"] is True
    assert description["parent"]["path"] == tmp_path / "run"
    assert description["parent"]["source"] == "env:APP_ROOT"


def test_env_changes_before_first_resolution_are_honored(tmp_path, monkeypatch):
    root = Stash(tmp_path / "default", env="APP_ROOT")

    monkeypatch.setenv("APP_ROOT", str(tmp_path / "run"))

    assert root.path == tmp_path / "run"
    assert root.source == "env:APP_ROOT"


def test_resolved_stash_stays_stable_until_refreshed(tmp_path, monkeypatch):
    root = Stash(tmp_path / "default", env="APP_ROOT")

    monkeypatch.setenv("APP_ROOT", str(tmp_path / "first"))
    assert root.path == tmp_path / "first"

    monkeypatch.setenv("APP_ROOT", str(tmp_path / "second"))

    assert root.path == tmp_path / "first"
    assert root.source == "env:APP_ROOT"
    assert root.refresh().path == tmp_path / "second"


def test_refresh_preserves_bindings_and_refreshes_parent_env(tmp_path, monkeypatch):
    root = Stash(tmp_path / "default", env="APP_ROOT")
    articles = (root / "{world}" / "articles").bind(world="Storybook")

    monkeypatch.setenv("APP_ROOT", str(tmp_path / "first"))
    assert articles.path == tmp_path / "first" / "Storybook" / "articles"

    monkeypatch.setenv("APP_ROOT", str(tmp_path / "second"))

    refreshed = articles.refresh()
    assert refreshed.path == tmp_path / "second" / "Storybook" / "articles"


def test_stash_composition_preserves_nested_child_chains(tmp_path):
    root = Stash(tmp_path)
    child = Stash("worlds") / "{world}" / "articles"

    composed = root / child

    assert composed.bind(world="Storybook").path == (
        tmp_path / "worlds" / "Storybook" / "articles"
    )


def test_stash_composition_is_immutable_for_existing_child_chain(tmp_path):
    child = Stash("worlds") / "{world}" / "articles"

    composed = Stash(tmp_path) / child

    assert child.bind(world="Storybook").path == (
        Path.cwd() / "worlds" / "Storybook" / "articles"
    ).resolve()
    assert composed.bind(world="Storybook").path == (
        tmp_path / "worlds" / "Storybook" / "articles"
    )


def test_stash_composition_is_associative_for_relative_fragments(tmp_path):
    root = Stash(tmp_path)

    left_grouped = (root / Stash("worlds")) / (Stash("{world}") / "articles")
    right_grouped = root / (Stash("worlds") / Stash("{world}") / "articles")

    assert left_grouped.bind(world="Storybook").path == right_grouped.bind(
        world="Storybook"
    ).path


def test_include_defaults_alias_matches_defaults_option(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), include_defaults=True)
    class Params:
        name: str
        stop: list[str] = field(default_factory=lambda: ["\n"])

    Params("Example")
    text = (tmp_path / "Example.yml").read_text(encoding="utf-8")
    assert "stop:" in text


def test_collection_get_missing_required_placeholder_argument_is_clear(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Prompt:
        name: str
        text: str = ""

    with pytest.raises(TypeError, match="name"):
        Prompt.snapshots.get()


def test_path_placeholders_allow_spaces_and_safe_punctuation(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Article:
        name: str
        title: str = ""

    article = Article("Dusk Court, Part 1", "Dusk Court")
    article.snapshot.save()

    assert (tmp_path / "Dusk Court, Part 1.yml").exists()


@pytest.mark.parametrize(
    "value",
    [
        "../escape",
        "nested/name",
        "nested\\name",
        ".",
        "..",
        "",
        "bad:name",
        "question?",
        "star*",
        "CON",
        "nul.txt",
    ],
)
def test_path_placeholders_reject_unsafe_segments(tmp_path, value):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Article:
        name: str

    with pytest.raises(ValueError, match="placeholder"):
        Article(value).snapshot.path


def test_stashed_model_pattern_rejects_relative_traversal_before_write(tmp_path):
    root = Stash(tmp_path / "root")

    @snapclass("../outside/{self.name}.yml", stash=root, manual=True)
    class Article:
        name: str

    article = Article("escape")

    with pytest.raises(ValueError, match="traverse"):
        article.snapshot.save()

    assert not (tmp_path / "outside" / "escape.yml").exists()


def test_stashed_collection_scan_rejects_relative_traversal_pattern(tmp_path):
    root = Stash(tmp_path / "root")

    @snapclass("../outside/{self.name}.yml", stash=root, manual=True)
    class Article:
        name: str

    with pytest.raises(ValueError, match="traverse"):
        list(Article.snapshots(root).all())


def test_home_relative_stash_expands_and_overrides_parent(tmp_path):
    root = Stash(tmp_path / "app")
    home_logs = root / Stash("~/snapclass-logs")

    assert home_logs.path == (Path.home() / "snapclass-logs").resolve()
    assert home_logs.is_external is True


def test_home_relative_snapshot_pattern_expands_without_stash():
    @snapclass("~/snapclass-home-pattern/{self.name}.yml", manual=True)
    class Item:
        name: str

    item = Item("Alpha")

    assert item.snapshot.path == (
        Path.home() / "snapclass-home-pattern" / "Alpha.yml"
    ).resolve()


def test_multiple_app_roots_and_bound_worlds_stay_isolated(tmp_path, monkeypatch):
    chatsnack_root = Stash("chatsnack", env="CHATSNACK_BASE_DIR")
    plunkylib_root = Stash("plunkylib", env="PLUNKYLIB_BASE_DIR")
    lorebubble_world = Stash(tmp_path / ".lorebubble") / "{world}" / "article"

    monkeypatch.setenv("CHATSNACK_BASE_DIR", str(tmp_path / "chat-run"))
    monkeypatch.setenv("PLUNKYLIB_BASE_DIR", str(tmp_path / "plunky-run"))

    @snapclass("{self.name}.yml", stash=chatsnack_root, manual=True)
    class ChatPrompt:
        name: str
        text: str = ""

    @snapclass("{self.name}.yml", stash=plunkylib_root, manual=True)
    class PlunkyPrompt:
        name: str
        text: str = ""

    @snapclass("{self.slug}/article.yml", stash=lorebubble_world, manual=True)
    class Article:
        slug: str
        title: str = ""

    ChatPrompt("shared", "chat").save()
    PlunkyPrompt("shared", "plunky").save()
    Article.snapshots(lorebubble_world.bind(world="Storybook")).get_or_create(
        "shared",
        title="storybook",
    )
    Article.snapshots(lorebubble_world.bind(world="OtherWorld")).get_or_create(
        "shared",
        title="other",
    )

    assert (tmp_path / "chat-run" / "shared.yml").read_text(encoding="utf-8") == "text: chat\n"
    assert (tmp_path / "plunky-run" / "shared.yml").read_text(encoding="utf-8") == "text: plunky\n"
    assert (
        tmp_path / ".lorebubble" / "Storybook" / "article" / "shared" / "article.yml"
    ).read_text(encoding="utf-8") == "title: storybook\n"
    assert (
        tmp_path / ".lorebubble" / "OtherWorld" / "article" / "shared" / "article.yml"
    ).read_text(encoding="utf-8") == "title: other\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows drive and UNC path semantics")
def test_windows_drive_child_stash_overrides_parent_without_losing_drive():
    root = Stash(r"C:\snapclass\root")
    logs = root / Stash(r"D:\snapclass\logs")

    assert logs.path == Path(r"D:\snapclass\logs")
    assert logs.path.drive == "D:"
    assert logs.is_external is True


@pytest.mark.skipif(os.name != "nt", reason="Windows drive and UNC path semantics")
def test_windows_unc_stash_round_trips_network_root():
    root = Stash(r"C:\snapclass\root")
    network = root / Stash(r"\\server\share\snapclass")

    assert network.path == Path(r"\\server\share\snapclass")
    assert network.path.drive == r"\\server\share"
    assert network.is_external is True


def test_stash_local_formatters_isolate_same_extension_between_libraries(tmp_path):
    class UpperFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip().lower()}

        @classmethod
        def dumps(cls, data):
            return data["message"].upper() + "\n"

    class WrappedFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip("[]\n")}

        @classmethod
        def dumps(cls, data):
            return f"[{data['message']}]\n"

    first = Stash(tmp_path / "first", formatters={".yml": UpperFormatter})
    second = Stash(tmp_path / "second", formatters={".yml": WrappedFormatter})

    @snapclass("{self.name}.yml", stash=first, manual=True)
    class FirstPrompt:
        name: str
        message: str

    @snapclass("{self.name}.yml", stash=second, manual=True)
    class SecondPrompt:
        name: str
        message: str

    FirstPrompt("a", "hello").snapshot.save()
    SecondPrompt("a", "hello").snapshot.save()

    assert (tmp_path / "first" / "a.yml").read_text(encoding="utf-8") == "HELLO\n"
    assert (tmp_path / "second" / "a.yml").read_text(encoding="utf-8") == "[hello]\n"
    assert FirstPrompt.snapshots.get("a").message == "hello"
    assert SecondPrompt.snapshots.get("a").message == "hello"


def test_downstream_formatter_situations_coexist_without_global_registration(tmp_path):
    class ChatsnackYaml(formatters.Formatter):
        @classmethod
        def extensions(cls):
            return {"", ".yml", ".yaml"}

        @classmethod
        def deserialize(cls, file_object):
            data = formatters.YAML.deserialize(file_object)
            messages = []
            for message in data.get("messages", []):
                if isinstance(message, dict) and len(message) == 1:
                    role, content = next(iter(message.items()))
                    messages.append({"system" if role == "developer" else role: content})
                else:
                    messages.append(message)
            data["messages"] = messages
            return data

        @classmethod
        def serialize(cls, data):
            def without_none(value):
                if isinstance(value, dict):
                    return {
                        key: without_none(item)
                        for key, item in value.items()
                        if item is not None
                    }
                if isinstance(value, list):
                    return [without_none(item) for item in value]
                return value

            return formatters.YAML.serialize(without_none(data))

    class ChatsnackTxt(formatters.Formatter):
        @classmethod
        def extensions(cls):
            return {".txt"}

        @classmethod
        def serialize(cls, data):
            return "".join(value for value in data.values() if isinstance(value, str))

        @classmethod
        def deserialize(cls, file_object):
            with open(file_object.name, encoding="utf-8") as handle:
                return {"content": handle.read()}

    class PlunkylibTxt(formatters.Formatter):
        type_format = "{name}|{type}"
        divider = "#-=-=-=-=-DO-NOT-EDIT-THIS-LINE-PLEASE-=-=-=-=-#"
        types_by_name = {"str": str, "int": int, "float": float}

        @classmethod
        def extensions(cls):
            return {".txt"}

        @classmethod
        def serialize(cls, data):
            sections = []
            for key, value in data.items():
                if type(value) not in {str, int, float}:
                    raise ValueError(f"Unsupported type: {type(value)}")
                sections.append(
                    f"{cls.type_format.format(name=key, type=type(value).__name__)}\n"
                    f"{value}\n"
                    f"{cls.divider}\n"
                )
            return "".join(sections)

        @classmethod
        def deserialize(cls, file_object):
            output = {}
            current_key = ""
            current_type = None
            current_value = None
            for line in file_object.readlines():
                current_line = line.rstrip("\n")
                if current_line == cls.divider:
                    output[current_key] = current_type(current_value)
                    current_key = ""
                    current_type = None
                    current_value = None
                    continue
                if current_key == "":
                    current_key, type_name = current_line.split("|")
                    current_type = cls.types_by_name[type_name.strip()]
                    current_key = current_key.strip()
                    continue
                current_value = (
                    current_line
                    if current_value is None
                    else current_value + "\n" + current_line
                )
            return output

    chatsnack_root = Stash(
        tmp_path / "chatsnack",
        formatters={".yml": ChatsnackYaml, ".txt": ChatsnackTxt},
    )
    plunkylib_root = Stash(
        tmp_path / "plunkylib",
        formatters={".txt": PlunkylibTxt},
    )
    lorebubble_root = Stash(tmp_path / "lorebubble")

    @snapclass("{self.name}.yml", stash=chatsnack_root, manual=True, defaults=True)
    class Chat:
        name: str
        params: dict = field(default_factory=dict)
        messages: list[dict] = field(default_factory=list)

    @snapclass("{self.name}.txt", stash=chatsnack_root, manual=True)
    class RawPrompt:
        name: str
        content: str

    @snapclass("{self.name}.txt", stash=plunkylib_root, manual=True)
    class PromptVars:
        name: str
        prompt: str
        count: int
        temperature: float

    @snapclass("{self.name}.yml", stash=lorebubble_root, manual=True, defaults=True)
    class LoreArticle:
        name: str
        summary: str | None = None

    Chat(
        "Popsicle",
        params={"model": "gpt-5-chat-latest", "responses": {"state": None}},
        messages=[{"developer": "Follow house style."}, {"assistant": None}],
    ).snapshot.save()
    RawPrompt("System", "Answer carefully.\n").snapshot.save()
    PromptVars("Care", "Answer {question}", 2, 0.7).snapshot.save()
    LoreArticle("DuskCourt").snapshot.save()

    chat_yaml = (tmp_path / "chatsnack" / "Popsicle.yml").read_text(encoding="utf-8")
    raw_prompt = (tmp_path / "chatsnack" / "System.txt").read_text(encoding="utf-8")
    plunky_prompt = (tmp_path / "plunkylib" / "Care.txt").read_text(encoding="utf-8")
    lore_yaml = (tmp_path / "lorebubble" / "DuskCourt.yml").read_text(encoding="utf-8")

    assert "state:" not in chat_yaml
    assert "assistant:" not in chat_yaml
    assert "developer:" in chat_yaml
    assert raw_prompt == "Answer carefully.\n"
    assert "prompt|str\nAnswer {question}\n" in plunky_prompt
    assert "count|int\n2\n" in plunky_prompt
    assert "temperature|float\n0.7\n" in plunky_prompt
    assert "summary:" in lore_yaml

    (tmp_path / "chatsnack" / "Popsicle.yml").write_text(
        "messages:\n"
        "  - developer: Follow edited style.\n",
        encoding="utf-8",
    )
    (tmp_path / "plunkylib" / "Care.txt").write_text(
        "prompt|str\n"
        "Edited {question}\n"
        f"{PlunkylibTxt.divider}\n"
        "count|int\n"
        "3\n"
        f"{PlunkylibTxt.divider}\n"
        "temperature|float\n"
        "0.9\n"
        f"{PlunkylibTxt.divider}\n",
        encoding="utf-8",
    )

    assert Chat.snapshots.get("Popsicle").messages == [
        {"system": "Follow edited style."}
    ]
    loaded_vars = PromptVars.snapshots.get("Care")
    assert loaded_vars.prompt == "Edited {question}"
    assert loaded_vars.count == 3
    assert loaded_vars.temperature == 0.9


def test_child_stash_inherits_formatter_policy_and_child_extension_override_wins(tmp_path):
    class PipeFormatter(FileFormatter):
        extensions = {".pipe"}

        @classmethod
        def loads(cls, text: str):
            key, value = text.strip().split("|", 1)
            return {key: value}

        @classmethod
        def dumps(cls, data):
            return "".join(f"{key}|{value}\n" for key, value in data.items())

    class ParentYamlFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip().removeprefix("parent:")}

        @classmethod
        def dumps(cls, data):
            return f"parent:{data['message']}\n"

    class ChildYamlFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip().removeprefix("child:")}

        @classmethod
        def dumps(cls, data):
            return f"child:{data['message']}\n"

    root = Stash(
        tmp_path,
        formatters={".pipe": PipeFormatter, ".yml": ParentYamlFormatter},
    )
    child = root / Stash("child", formatters={".yml": ChildYamlFormatter})

    @snapclass("{self.name}.pipe", stash=child, manual=True)
    class Inherited:
        name: str
        text: str

    @snapclass("{self.name}.yml", stash=child, manual=True)
    class Overridden:
        name: str
        message: str

    Inherited("a", "one").snapshot.save()
    Overridden("b", "two").snapshot.save()

    assert (tmp_path / "child" / "a.pipe").read_text(encoding="utf-8") == "text|one\n"
    assert (tmp_path / "child" / "b.yml").read_text(encoding="utf-8") == "child:two\n"


def test_explicit_model_formatter_beats_stash_formatter_policy(tmp_path):
    class StashFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip().removeprefix("stash:")}

        @classmethod
        def dumps(cls, data):
            return f"stash:{data['message']}\n"

    class ExplicitFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip().removeprefix("explicit:")}

        @classmethod
        def dumps(cls, data):
            return f"explicit:{data['message']}\n"

    stash = Stash(tmp_path, formatters={".yml": StashFormatter})

    @snapclass(
        "{self.name}.yml",
        stash=stash,
        formatter=ExplicitFormatter,
        manual=True,
    )
    class Prompt:
        name: str
        message: str

    Prompt("a", "wins").snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "explicit:wins\n"
    assert Prompt.snapshots.get("a").message == "wins"


def test_stash_policy_helpers_return_augmented_copies():
    class OneFormatter(FileFormatter):
        extensions = {".one"}

        @classmethod
        def loads(cls, text: str):
            return {"value": text}

        @classmethod
        def dumps(cls, data):
            return data["value"]

    class TwoFormatter(OneFormatter):
        extensions = {".two"}

    class Token:
        pass

    class TokenSerializer(serializers.Serializer):
        @classmethod
        def to_preserialization_data(cls, value, **_kwargs):
            return "token"

        @classmethod
        def to_python_value(cls, value, **_kwargs):
            return Token()

    class OtherSerializer(TokenSerializer):
        pass

    base = Stash("root")
    updated = (
        base.with_formatter(".one", OneFormatter)
        .with_formatters({".two": TwoFormatter})
        .with_serializer(Token, TokenSerializer)
        .with_serializers({"OtherToken": OtherSerializer})
        .with_options(minimal_diffs=False, write_delay=0.125)
    )

    assert base.effective_formatters() == {}
    assert base.effective_serializers() == {}
    assert base.effective_minimal_diffs() is None
    assert base.effective_write_delay() is None
    assert updated.effective_formatters() == {
        ".one": OneFormatter,
        ".two": TwoFormatter,
    }
    assert updated.effective_serializers()[Token] is TokenSerializer
    assert updated.effective_serializers()["Token"] is TokenSerializer
    assert updated.effective_serializers()["OtherToken"] is OtherSerializer
    assert updated.effective_minimal_diffs() is False
    assert updated.effective_write_delay() == 0.125


def test_collection_bound_stash_uses_bound_formatter_policy(tmp_path):
    class ScalarFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip()}

        @classmethod
        def dumps(cls, data):
            return data["message"] + "\n"

    default_stash = Stash(tmp_path / "default")
    scalar_stash = Stash(tmp_path / "scalar", formatters={".yml": ScalarFormatter})

    @snapclass("{self.name}.yml", stash=default_stash, manual=True)
    class Prompt:
        name: str
        message: str

    Prompt("a", "default").snapshot.save()
    (tmp_path / "scalar" / "a.yml").parent.mkdir()
    (tmp_path / "scalar" / "a.yml").write_text("bound\n", encoding="utf-8")

    assert Prompt.snapshots(scalar_stash).get("a").message == "bound"


def test_collection_bound_stash_uses_bound_serializer_policy(tmp_path):
    class Token:
        def __init__(self, value: str) -> None:
            self.value = value

        def __eq__(self, other):
            return isinstance(other, Token) and self.value == other.value

    class BoundTokenSerializer(serializers.Serializer):
        @classmethod
        def to_preserialization_data(cls, value, **_kwargs):
            return "bound:" + value.value

        @classmethod
        def to_python_value(cls, value, **_kwargs):
            return Token(str(value).removeprefix("bound:"))

    default_stash = Stash(tmp_path / "default")
    bound_stash = Stash(
        tmp_path / "bound",
        serializers={Token: BoundTokenSerializer},
    )

    @snapclass("{self.name}.yml", stash=default_stash, manual=True)
    class Record:
        name: str
        token: Token

    (tmp_path / "bound").mkdir()
    (tmp_path / "bound" / "a.yml").write_text("token: bound:loaded\n", encoding="utf-8")

    record = Record.snapshots(bound_stash).get("a")
    record.token = Token("saved")
    record.snapshot.save()

    assert record.token == Token("saved")
    assert (tmp_path / "bound" / "a.yml").read_text(encoding="utf-8") == (
        "token: bound:saved\n"
    )
