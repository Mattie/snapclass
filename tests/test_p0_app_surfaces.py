from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from snapclass import Missing, Stash, serializers, snapclass
from snapclass.formatters import FileFormatter, YAMLFormatter
from snapclass.schemas import SnapclassError


def test_stash_env_relative_and_absolute_overrides(tmp_path, monkeypatch):
    root = Stash(tmp_path / "app", env="APP_ROOT")
    logs = root / Stash("logs", env="APP_LOGS")

    monkeypatch.setenv("APP_ROOT", str(tmp_path / "run"))
    monkeypatch.setenv("APP_LOGS", "logs/dev")
    assert logs.path == tmp_path / "run" / "logs" / "dev"
    assert logs.is_external is False

    external = tmp_path / "external-logs"
    monkeypatch.setenv("APP_LOGS", str(external))
    logs = logs.refresh()
    assert logs.path == external
    assert logs.is_external is True
    assert logs.describe()["source"] == "env:APP_LOGS"


def test_stash_rejects_relative_traversal(tmp_path):
    root = Stash(tmp_path / "app")
    with pytest.raises(ValueError, match="traverse"):
        (root / "../outside").path


def test_manual_chat_style_collection_and_explicit_path(tmp_path):
    prompts = Stash(tmp_path / "prompts")
    runs = Stash(tmp_path / "runs")

    @snapclass("{self.name}.yml", stash=prompts, manual=True)
    class Chat:
        name: str
        messages: list[dict] = field(default_factory=list)

    chat = Chat("Popsicle")
    chat.messages.append({"system": "hello"})
    assert not (tmp_path / "prompts" / "Popsicle.yml").exists()

    chat.snapshot.save()
    loaded = Chat.snapshots.get("Popsicle")
    assert loaded.messages == [{"system": "hello"}]

    run_chat = Chat.snapshots(runs).get_or_create("Scratch")
    run_chat.messages.append({"user": "run"})
    run_chat.snapshot.save()
    assert (tmp_path / "runs" / "Scratch.yml").exists()
    assert not (tmp_path / "prompts" / "Scratch.yml").exists()

    explicit = tmp_path / "elsewhere" / "OneOff.yml"
    chat.snapshot.path = explicit
    chat.snapshot.save()
    assert explicit.exists()

    restored = Chat("OneOff")
    restored.snapshot.path = explicit
    restored.snapshot.load()
    assert restored.messages == [{"system": "hello"}]


def test_snapshot_save_and_load_accept_explicit_paths(tmp_path):
    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=prompts, manual=True)
    class Chat:
        name: str
        messages: list[dict] = field(default_factory=list)

    explicit = tmp_path / "sessions" / "Scratch.yml"
    Chat("Scratch", [{"system": "saved elsewhere"}]).snapshot.save(explicit)

    assert explicit.exists()
    assert not (tmp_path / "prompts" / "Scratch.yml").exists()

    loaded = Chat("Scratch")
    loaded.snapshot.load(explicit)
    assert loaded.messages == [{"system": "saved elsewhere"}]
    assert loaded.snapshot.path == explicit


def test_object_save_and_load_delegate_to_snapshot_with_explicit_paths(tmp_path):
    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=prompts, manual=True)
    class Chat:
        name: str
        messages: list[dict] = field(default_factory=list)
        temperature: float = 0.0

    explicit = tmp_path / "sessions" / "Scratch.yml"
    chat = Chat("Scratch", [{"system": "saved through object"}])

    assert chat.save(explicit, include_default_values=True) is chat
    assert explicit.exists()
    assert "temperature: 0.0" in explicit.read_text(encoding="utf-8")

    loaded = Chat("Scratch")
    assert loaded.load(explicit) is loaded
    assert loaded.messages == [{"system": "saved through object"}]
    assert loaded.snapshot.path == explicit


def test_snapclass_does_not_replace_app_defined_save_or_load_methods(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Chat:
        name: str
        message: str = ""

        def save(self, path=None):
            return f"app-save:{path}"

        def load(self, path=None):
            return f"app-load:{path}"

    chat = Chat("Popsicle", "hello")
    assert chat.save("elsewhere") == "app-save:elsewhere"
    assert chat.load("elsewhere") == "app-load:elsewhere"

    chat.snapshot.save()
    assert (tmp_path / "Popsicle.yml").exists()


def test_snapclass_does_not_replace_inherited_app_file_methods(tmp_path):
    class SnapshotMixin:
        def save(self, path=None):
            return f"mixin-save:{path}"

        def load(self, path=None):
            return f"mixin-load:{path}"

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Chat(SnapshotMixin):
        name: str
        message: str = ""

    chat = Chat("Popsicle", "hello")
    assert chat.save("elsewhere") == "mixin-save:elsewhere"
    assert chat.load("elsewhere") == "mixin-load:elsewhere"
    chat.snapshot.save()
    assert (tmp_path / "Popsicle.yml").exists()


def test_snapshots_and_stash_share_collection_behavior(tmp_path):
    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=prompts, manual=True)
    class Chat:
        name: str
        params: dict = field(default_factory=dict)

    Chat("Popsicle", {"model": "gpt-5-chat-latest"}).snapshot.save()

    assert Chat.snapshots.get("Popsicle").params["model"] == "gpt-5-chat-latest"
    assert Chat.snapshots(prompts).get("Popsicle").params["model"] == "gpt-5-chat-latest"
    assert Chat.snapshots.get("Popsicle").params["model"] == "gpt-5-chat-latest"
    assert Chat.snapshots(prompts).get("Popsicle").params["model"] == "gpt-5-chat-latest"
    assert [c.name for c in Chat.snapshots.filter(params__model="gpt-5-chat-latest")] == [
        "Popsicle"
    ]


def test_text_formatter_for_chatsnack_style_text_assets(tmp_path):
    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.txt", stash=prompts, manual=True)
    class Text:
        name: str
        content: str | None = None

    text = Text("SnackExplosion", "first line\nsecond line\n")
    text.snapshot.save()

    assert (tmp_path / "prompts" / "SnackExplosion.txt").read_text(encoding="utf-8") == (
        "first line\nsecond line\n"
    )
    assert Text.snapshots.get("SnackExplosion").content == "first line\nsecond line\n"


def test_chatsnack_style_prompt_includes_resolve_from_effective_stash(tmp_path):
    prompts = Stash(tmp_path / "prompts")
    runs = Stash(tmp_path / "runs")

    @snapclass("{self.name}.txt", stash=prompts, manual=True)
    class Text:
        name: str
        content: str | None = None

    @snapclass("{self.name}.yml", stash=prompts, manual=True)
    class Chat:
        name: str
        include_texts: list[str] = field(default_factory=list)

        def resolved_texts(self) -> list[str]:
            stash = self.snapshot.stash
            return [
                text.content or ""
                for name in self.include_texts
                if (text := Text.snapshots(stash).get_or_none(name)) is not None
            ]

    Text("Voice", "prompt voice\n").snapshot.save()
    Chat("Popsicle", ["Voice", "Missing"]).snapshot.save()

    Text.snapshots(runs).get_or_create("Voice", content="run voice\n")
    Chat.snapshots(runs).get_or_create("Popsicle", include_texts=["Voice"])

    default_chat = Chat.snapshots.get("Popsicle")
    compat_chat = Chat.snapshots(prompts).get("Popsicle")
    bound_chat = Chat.snapshots(prompts).get("Popsicle")
    run_chat = Chat.snapshots(runs).get("Popsicle")

    assert default_chat.snapshot.stash == prompts
    assert compat_chat.snapshot.stash == prompts
    assert bound_chat.snapshot.stash == prompts
    assert run_chat.snapshot.stash == runs
    assert default_chat.resolved_texts() == ["prompt voice\n"]
    assert compat_chat.resolved_texts() == ["prompt voice\n"]
    assert bound_chat.resolved_texts() == ["prompt voice\n"]
    assert run_chat.resolved_texts() == ["run voice\n"]


def test_chatsnack_style_instance_snapshots_resolve_prompt_includes(tmp_path):
    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=prompts, manual=True)
    class Chat:
        name: str
        messages: list[dict[str, str]] = field(default_factory=list)

        def get_messages(self) -> list[dict[str, str]]:
            expanded: list[dict[str, str]] = []
            for message in self.messages:
                for role, content in message.items():
                    if role == "include":
                        include = self.snapshots.get_or_none(content)
                        if include is None:
                            raise ValueError(
                                f"Could not find 'include' prompt with name: {content}"
                            )
                        expanded.extend(include.get_messages())
                    else:
                        expanded.append({role: content})
            return expanded

    Chat("Shared", [{"system": "Use the project style."}]).snapshot.save()
    Chat("Popsicle", [{"include": "Shared"}, {"user": "What is a popsicle?"}]).snapshot.save()

    loaded = Chat.snapshots.get("Popsicle")

    assert loaded.get_messages() == [
        {"system": "Use the project style."},
        {"user": "What is a popsicle?"},
    ]


def test_snapshot_text_setter_preserves_raw_text_asset_whitespace(tmp_path):
    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.txt", stash=prompts, manual=True)
    class Text:
        name: str
        content: str | None = None

    raw = "  leading space\ntrailing blank lines\n\n"
    text = Text("WhitespacePrompt")
    text.snapshot.text = raw

    path = tmp_path / "prompts" / "WhitespacePrompt.txt"
    assert path.read_text(encoding="utf-8") == raw
    assert Text.snapshots.get("WhitespacePrompt").content == raw
    assert text.snapshot.modified is False


def test_snapshot_text_setter_writes_exact_yaml_text(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Chat:
        name: str
        message: str = ""

    chat = Chat("Popsicle")
    authored = "message: hello"
    chat.snapshot.text = authored

    assert (tmp_path / "Popsicle.yml").read_text(encoding="utf-8") == authored
    assert Chat.snapshots.get("Popsicle").message == "hello"


def test_model_scoped_yaml_formatter_does_not_replace_global_yaml(tmp_path):
    class UpperMessageFileFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            return {"message": text.strip().lower()}

        @classmethod
        def dumps(cls, data):
            return data["message"].upper() + "\n"

    @snapclass("{self.name}.yml", stash=Stash(tmp_path / "custom"), manual=True, formatter=UpperMessageFileFormatter)
    class Custom:
        name: str
        message: str

    @snapclass("{self.name}.yml", stash=Stash(tmp_path / "normal"), manual=True)
    class Normal:
        name: str
        message: str

    Custom("a", "hello").snapshot.save()
    Normal("a", "hello").snapshot.save()

    assert (tmp_path / "custom" / "a.yml").read_text(encoding="utf-8") == "HELLO\n"
    assert "message: hello" in (tmp_path / "normal" / "a.yml").read_text(encoding="utf-8")


def test_chatsnack_scalar_turn_formatter_round_trips_readable_yaml(tmp_path):
    class ScalarTurnsFileFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            data = YAMLFormatter.loads(text)
            messages = []
            for item in data.get("messages", []):
                if isinstance(item, dict) and len(item) == 1:
                    role, content = next(iter(item.items()))
                    if role == "developer":
                        role = "system"
                    messages.append({"role": role, "content": content})
                else:
                    messages.append(item)
            data["messages"] = messages
            return data

        @classmethod
        def dumps(cls, data):
            data = dict(data)
            messages = []
            for message in data.get("messages", []):
                if set(message) == {"role", "content"}:
                    messages.append({message["role"]: message["content"]})
                else:
                    messages.append(message)
            data["messages"] = messages
            return YAMLFormatter.dumps(data)

    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=prompts, manual=True, formatter=ScalarTurnsFileFormatter)
    class Chat:
        name: str
        messages: list[dict] = field(default_factory=list)

    (tmp_path / "prompts").mkdir()
    prompt_path = tmp_path / "prompts" / "Popsicle.yml"
    authored = (
        "messages:\n"
        "  - system: Keep it short.\n"
        "  - developer: Follow the house style guide.\n"
        "  - user: What is a popsicle?\n"
    )
    prompt_path.write_text(authored, encoding="utf-8")

    chat = Chat.snapshots.get("Popsicle")
    assert prompt_path.read_text(encoding="utf-8") == authored
    assert chat.messages == [
        {"role": "system", "content": "Keep it short."},
        {"role": "system", "content": "Follow the house style guide."},
        {"role": "user", "content": "What is a popsicle?"},
    ]

    first_save = chat.snapshot.text
    chat.snapshot.save()
    expected_first_save = (
        "messages:\n"
        "  - system: Keep it short.\n"
        "  - system: Follow the house style guide.\n"
        "  - user: What is a popsicle?\n"
    )
    assert first_save == expected_first_save
    assert prompt_path.read_text(encoding="utf-8") == expected_first_save

    chat.messages.append({"role": "assistant", "content": "Frozen juice on a stick."})
    author_text = chat.snapshot.text
    chat.snapshot.save()

    saved = prompt_path.read_text(encoding="utf-8")
    assert author_text == saved
    assert "role:" not in saved
    assert "content:" not in saved
    assert "developer:" not in saved
    assert "- system: Follow the house style guide." in saved
    assert "- assistant: Frozen juice on a stick." in saved


def test_chatsnack_explicit_paths_load_project_prompt_and_save_run_output(tmp_path):
    class ScalarTurnsFileFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            data = YAMLFormatter.loads(text)
            data["messages"] = [
                {"role": role, "content": content}
                for item in data.get("messages", [])
                for role, content in item.items()
            ]
            return data

        @classmethod
        def dumps(cls, data):
            data = dict(data)
            data["messages"] = [
                {message["role"]: message["content"]}
                for message in data.get("messages", [])
            ]
            return YAMLFormatter.dumps(data)

    prompts = Stash(tmp_path / "package-prompts")

    @snapclass("{self.name}.yml", stash=prompts, manual=True, formatter=ScalarTurnsFileFormatter)
    class Chat:
        name: str
        messages: list[dict] = field(default_factory=list)

    project_prompt = tmp_path / "project" / "prompts" / "ArticleRewrite.yml"
    project_prompt.parent.mkdir(parents=True)
    authored = (
        "messages:\n"
        "  - system: Rewrite lore articles for consistency.\n"
        "  - user: Tighten the Dusk Court entry.\n"
    )
    project_prompt.write_text(authored, encoding="utf-8")
    run_output = tmp_path / "runs" / "2026-03-01" / "ArticleRewrite-output.yml"

    prompt = Chat("ArticleRewrite")
    prompt.snapshot.load(project_prompt)
    generated = Chat(
        "ArticleRewrite-output",
        messages=prompt.messages
        + [{"role": "assistant", "content": "Dusk Court revised."}],
    )
    generated.snapshot.save(run_output)

    assert prompt.snapshot.path == project_prompt
    assert prompt.messages == [
        {"role": "system", "content": "Rewrite lore articles for consistency."},
        {"role": "user", "content": "Tighten the Dusk Court entry."},
    ]
    assert project_prompt.read_text(encoding="utf-8") == authored
    assert generated.snapshot.path == run_output
    assert run_output.read_text(encoding="utf-8") == (
        "messages:\n"
        "  - system: Rewrite lore articles for consistency.\n"
        "  - user: Tighten the Dusk Court entry.\n"
        "  - assistant: Dusk Court revised.\n"
    )
    assert not (tmp_path / "package-prompts" / "ArticleRewrite-output.yml").exists()


def test_chatsnack_style_chat_params_nested_dataclass_round_trips(tmp_path):
    prompts = Stash(tmp_path / "prompts")

    @dataclass
    class ChatParams:
        model: str = "gpt-5-chat-latest"
        temperature: float = 0.2
        max_tokens: int | None = None

    @snapclass("{self.name}.yml", stash=prompts, manual=True)
    class Chat:
        name: str
        params: ChatParams | None = None
        messages: list[dict[str, str]] = field(default_factory=list)

    Chat(
        "Popsicle",
        params=ChatParams(max_tokens=128),
        messages=[{"system": "Keep it short."}],
    ).snapshot.save()

    saved = (tmp_path / "prompts" / "Popsicle.yml").read_text(encoding="utf-8")
    loaded = Chat.snapshots.get("Popsicle")

    assert "model: gpt-5-chat-latest" in saved
    assert "temperature: 0.2" in saved
    assert "max_tokens: 128" in saved
    assert loaded.params == ChatParams(max_tokens=128)
    assert loaded.messages == [{"system": "Keep it short."}]


def test_chatsnack_compact_tool_formatter_round_trips_without_internal_json(tmp_path):
    class CompactChatFileFormatter(FileFormatter):
        extensions = {".yml"}

        @classmethod
        def loads(cls, text: str):
            data = YAMLFormatter.loads(text)
            tools = []
            for item in data.get("tools", []):
                name, spec = next(iter(item.items()))
                properties = {
                    key: {"type": value}
                    for key, value in spec.get("params", {}).items()
                }
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": spec.get("description", ""),
                            "parameters": {
                                "type": "object",
                                "properties": properties,
                            },
                        },
                    }
                )
            data["tools"] = tools
            return data

        @classmethod
        def dumps(cls, data):
            data = dict(data)
            compact_tools = []
            for tool in data.get("tools", []):
                function = tool.get("function", {})
                properties = function.get("parameters", {}).get("properties", {})
                compact_tools.append(
                    {
                        function.get("name", ""): {
                            "description": function.get("description", ""),
                            "params": {
                                key: value.get("type", "string")
                                for key, value in properties.items()
                            },
                        }
                    }
                )
            data["tools"] = compact_tools
            return YAMLFormatter.dumps(data)

    prompts = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=prompts, manual=True, formatter=CompactChatFileFormatter)
    class Chat:
        name: str
        tools: list[dict] = field(default_factory=list)

    @snapclass("{self.name}.yml", stash=Stash(tmp_path / "normal"), manual=True)
    class NormalYaml:
        name: str
        tools: list[dict] = field(default_factory=list)

    (tmp_path / "prompts" / "Research.yml").parent.mkdir(parents=True)
    (tmp_path / "prompts" / "Research.yml").write_text(
        "tools:\n"
        "  - search:\n"
        "      description: Search docs\n"
        "      params:\n"
        "        query: string\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Research")
    assert chat.tools[0]["function"]["name"] == "search"
    assert chat.tools[0]["function"]["parameters"]["properties"]["query"] == {
        "type": "string"
    }

    chat.tools[0]["_json"] = {"adapter": "internal"}
    chat.tools[0]["function"]["description"] = "Search project docs"
    chat.snapshot.save()

    saved = (tmp_path / "prompts" / "Research.yml").read_text(encoding="utf-8")
    assert "search:" in saved
    assert "description: Search project docs" in saved
    assert "_json" not in saved
    assert "function:" not in saved

    NormalYaml("Plain", [{"function": {"name": "ordinary"}}]).snapshot.save()
    normal = (tmp_path / "normal" / "Plain.yml").read_text(encoding="utf-8")
    assert "function:" in normal


def test_chatsnack_runtime_fields_can_be_excluded_from_authored_yaml(tmp_path):
    class MessagesSerializer(serializers.Serializer):
        @classmethod
        def to_python_value(cls, deserialized_data, **_kwargs):
            return [dict(message) for message in (deserialized_data or [])]

        @classmethod
        def to_preserialization_data(cls, python_value, **_kwargs):
            return [dict(message) for message in (python_value or [])]

    prompts = Stash(tmp_path / "prompts")

    @snapclass(
        "{self.name}.yml",
        stash=prompts,
        manual=True,
        fields={"params": serializers.Dictionary, "messages": MessagesSerializer},
    )
    class Chat:
        name: str
        params: dict = field(default_factory=dict)
        messages: list[dict] = field(default_factory=list)
        provider_response_id: str | None = None
        upload_cache: dict[str, str] = field(default_factory=dict)
        diagnostics: list[str] = field(default_factory=list)

    chat = Chat(
        "Popsicle",
        params={"model": "gpt-5-chat-latest"},
        messages=[{"role": "system", "content": "stay concise"}],
        provider_response_id="resp_123",
        upload_cache={"file.txt": "file_123"},
        diagnostics=["token count unavailable"],
    )

    chat.snapshot.save()
    saved = (tmp_path / "prompts" / "Popsicle.yml").read_text(encoding="utf-8")

    assert "model: gpt-5-chat-latest" in saved
    assert "stay concise" in saved
    assert "provider_response_id" not in saved
    assert "upload_cache" not in saved
    assert "diagnostics" not in saved
    assert chat.provider_response_id == "resp_123"
    assert chat.upload_cache == {"file.txt": "file_123"}
    assert chat.diagnostics == ["token count unavailable"]

    loaded = Chat.snapshots.get("Popsicle")
    assert loaded.params == {"model": "gpt-5-chat-latest"}
    assert loaded.messages == [{"role": "system", "content": "stay concise"}]
    assert loaded.provider_response_id is None
    assert loaded.upload_cache == {}
    assert loaded.diagnostics == []


def test_automatic_plunkylib_style_create_and_mutation(tmp_path):
    root = Stash(tmp_path / "plunkylib")

    @snapclass("completions/{self.name}.yml", stash=root)
    class Completion:
        name: str
        text: str
        petition_name: str
        content_filter_rating: int | None = None

    completion = Completion("Example-1", "answer", "Petition")
    path = tmp_path / "plunkylib" / "completions" / "Example-1.yml"
    assert path.exists()
    assert "answer" in path.read_text(encoding="utf-8")

    completion.content_filter_rating = 2
    assert "content_filter_rating: 2" in path.read_text(encoding="utf-8")


def test_defaults_inclusive_params_with_default_factory(tmp_path):
    root = Stash(tmp_path / "plunkylib")

    @snapclass("params/{self.name}.yml", stash=root, defaults=True)
    class CompletionParams:
        name: str
        engine: str = "text-davinci-002"
        stop: list[str] = field(default_factory=lambda: ["\n"])
        temperature: float = 0.0
        logprobs: int | None = None

    params = CompletionParams("ExampleGPT3")
    text = (tmp_path / "plunkylib" / "params" / "ExampleGPT3.yml").read_text(
        encoding="utf-8"
    )

    assert "engine: text-davinci-002" in text
    assert "temperature: 0.0" in text
    assert "stop:" in text
    assert "logprobs:" in text
    assert CompletionParams.snapshots.get("ExampleGPT3").stop == ["\n"]
    assert params.snapshot.exists


def test_automatic_list_mutation_for_lorebubble_style_history(tmp_path):
    root = Stash(tmp_path / "world")

    @snapclass("article/{self.title}.yml", stash=root)
    class Article:
        title: str
        version_history: list[dict] = field(default_factory=list)

    article = Article("Alpha")
    article.version_history.append({"summary": "created"})

    saved = (tmp_path / "world" / "article" / "Alpha.yml").read_text(encoding="utf-8")
    assert "summary: created" in saved


def test_automatic_common_list_and_dict_mutations(tmp_path):
    root = Stash(tmp_path / "world")

    @snapclass("article/{self.title}.yml", stash=root)
    class Article:
        title: str
        version_history: list[str] = field(default_factory=list)
        metadata: dict[str, str] = field(default_factory=dict)

    article = Article("Alpha")
    path = tmp_path / "world" / "article" / "Alpha.yml"

    article.version_history.extend(["created", "revised"])
    assert "revised" in path.read_text(encoding="utf-8")

    article.version_history[0] = "drafted"
    assert "drafted" in path.read_text(encoding="utf-8")

    article.version_history.pop()
    text = path.read_text(encoding="utf-8")
    assert "drafted" in text
    assert "revised" not in text

    article.metadata.update({"editor": "mattie", "state": "draft"})
    assert "editor: mattie" in path.read_text(encoding="utf-8")

    article.metadata.pop("state")
    text = path.read_text(encoding="utf-8")
    assert "editor: mattie" in text
    assert "state:" not in text

    article.version_history = ["replacement"]
    article.version_history.append("tracked-after-replace")
    assert "tracked-after-replace" in path.read_text(encoding="utf-8")

    loaded = Article.snapshots.get("Alpha")
    assert loaded.version_history == ["replacement", "tracked-after-replace"]
    assert loaded.metadata == {"editor": "mattie"}


def test_nested_message_dict_mutations_are_tracked_after_load(tmp_path):
    root = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=root)
    class ChatPrompt:
        name: str
        messages: list[dict[str, str]] = field(default_factory=list)

    prompt = ChatPrompt("Nested", [{"role": "system", "content": "draft"}])
    path = tmp_path / "prompts" / "Nested.yml"

    prompt.messages[0]["content"] = "saved from nested dict"
    assert "saved from nested dict" in path.read_text(encoding="utf-8")

    loaded = ChatPrompt.snapshots.get("Nested")
    loaded.messages[0]["content"] = "saved after reload"
    loaded.messages.append({"role": "user", "content": "hello"})
    loaded.messages[1]["content"] = "hello again"

    saved = path.read_text(encoding="utf-8")
    assert "saved after reload" in saved
    assert "hello again" in saved
    assert ChatPrompt.snapshots.get("Nested").messages == [
        {"role": "system", "content": "saved after reload"},
        {"role": "user", "content": "hello again"},
    ]


def test_tracked_containers_rebind_when_assigned_between_snapshots(tmp_path):
    root = Stash(tmp_path / "prompts")

    @snapclass("{self.name}.yml", stash=root)
    class ChatPrompt:
        name: str
        messages: list[dict[str, str]] = field(default_factory=list)

    source = ChatPrompt("Source", [{"role": "system", "content": "source"}])
    target = ChatPrompt("Target")

    target.messages = source.messages
    target.messages[0]["content"] = "target changed"

    source_text = (tmp_path / "prompts" / "Source.yml").read_text(encoding="utf-8")
    target_text = (tmp_path / "prompts" / "Target.yml").read_text(encoding="utf-8")

    assert "target changed" not in source_text
    assert "target changed" in target_text
    assert ChatPrompt.snapshots.get("Target").messages == [
        {"role": "system", "content": "target changed"}
    ]


def test_lorebubble_sidecar_can_be_derived_from_snapshot_path(tmp_path):
    root = Stash(tmp_path / "world")

    @snapclass("article/{self.title}.yml", stash=root, manual=True)
    class Article:
        title: str
        content_file: str | None = None

        def markdown_path(self) -> Path:
            if self.content_file:
                return self.snapshot.path.parent / self.content_file
            return self.snapshot.path.with_suffix(".md")

    article = Article("Alpha", "Alpha.md")
    article.snapshot.save()
    assert article.markdown_path() == tmp_path / "world" / "article" / "Alpha.md"


def test_windows_relpath_fallback_when_mounts_differ(tmp_path, monkeypatch):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Item:
        name: str

    item = Item("A")

    def fail_relpath(*_args, **_kwargs):
        raise ValueError("path is on mount 'Z:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", fail_relpath)
    assert item.snapshot.relpath == item.snapshot.path


def test_corrupt_yaml_is_preserved_and_reported(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Item:
        name: str
        value: str = ""

    path = tmp_path / "Broken.yml"
    path.write_text("value: [unterminated\n", encoding="utf-8")

    with pytest.raises(SnapclassError):
        Item.snapshots.get("Broken")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "value: [unterminated\n"


def test_missing_sentinel_loads_required_value_from_file(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str

    (tmp_path / "Example.yml").write_text("text: hello\n", encoding="utf-8")
    prompt = Prompt.snapshots.get("Example")
    assert prompt.text == "hello"
    assert prompt.name == "Example"
    assert prompt.text is not Missing
