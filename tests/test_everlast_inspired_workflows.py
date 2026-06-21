from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import field
from typing import ClassVar

from snapclass import Missing, Stash, snapclass
from snapclass.formatters import TextFormatter


def test_dynamic_bot_config_persists_registered_commands(tmp_path):
    base = Stash(tmp_path / "everlast")

    @snapclass("dynamic_config/{self.name}.yml", stash=base)
    class DynamicConfig:
        name: str
        comment: str = ""
        commands: list[str] = field(default_factory=list)
        react_commands: list[str] = field(default_factory=list)
        settings: dict = field(default_factory=dict)

    @snapclass("dynamic_commands/{self.command_name}.yml", stash=base)
    class DynamicCommand:
        command_name: str
        petition_name: str
        prefix_text: str = ""
        help_text: str = ""
        aliases: list[str] = field(default_factory=list)

    @snapclass("dynamic_commands/react__{self.command_name}.yml", stash=base)
    class DynamicReactCommand:
        command_name: str
        emoji: str
        petition_name: str
        prefix_text: str = ""
        help_text: str = ""

    config = DynamicConfig.snapshots.get_or_create(
        "DynamicCommands",
        comment="Commands generated at runtime.",
    )
    command = DynamicCommand(
        "rewrite",
        "ArticleRewrite",
        prefix_text="Rewrite this:",
        help_text="Rewrite an article.",
        aliases=["revise"],
    )
    react = DynamicReactCommand("sparkle", "sparkles", "ArticleRewrite")

    config.commands.append(command.command_name)
    config.react_commands.append(react.command_name)
    config.settings["prefix"] = "!"

    reloaded = DynamicConfig.snapshots.get("DynamicCommands")

    assert reloaded.commands == ["rewrite"]
    assert reloaded.react_commands == ["sparkle"]
    assert reloaded.settings == {"prefix": "!"}
    assert "aliases:" in (tmp_path / "everlast" / "dynamic_commands" / "rewrite.yml").read_text(
        encoding="utf-8"
    )
    assert (
        tmp_path / "everlast" / "dynamic_commands" / "react__sparkle.yml"
    ).exists()


def test_prompt_lab_chains_text_prompts_params_petitions_and_results(tmp_path):
    root = Stash(tmp_path / "prompt-lab")

    @snapclass("prompts/{self.name}.txt", stash=root, formatter=TextFormatter)
    class Prompt:
        name: str
        content: str

    @snapclass("promptvars/{self.name}.yml", stash=root)
    class PromptVars:
        name: str
        vars: dict[str, str]

    @snapclass("params/{self.name}.yml", stash=root, defaults=True)
    class CompletionParams:
        name: str
        engine: str = "gpt-5-chat-latest"
        temperature: float = 0.0
        stop: list[str] = field(default_factory=lambda: ["\n"])

    @snapclass("petition/{self.name}.yml", stash=root)
    class Petition:
        name: str
        prompt_name: str
        params_name: str
        promptvars_name: str | None = None
        prompt: ClassVar[Prompt | None]
        params: ClassVar[CompletionParams]
        promptvars: ClassVar[PromptVars | None]

        def load_all(self) -> None:
            self.prompt = Prompt.snapshots.get(self.prompt_name or self.name)
            self.params = CompletionParams.snapshots.get(self.params_name)
            self.promptvars = (
                PromptVars.snapshots.get(self.promptvars_name)
                if self.promptvars_name
                else None
            )

    @snapclass("completions/{self.name}.yml", stash=root)
    class Completion:
        name: str
        text: str
        petition_name: str
        parent_name: str | None = None
        petition: ClassVar[Petition | None]
        parent: ClassVar["Completion | None"]

        def load_all(self) -> None:
            self.petition = Petition.snapshots.get(self.petition_name)
            self.parent = (
                Completion.snapshots.get(self.parent_name)
                if self.parent_name
                else None
            )

    @snapclass("namedlists/{self.list_name}.yml", stash=root)
    class NamedList:
        list_name: str
        items: list[str]

    Prompt("ArticlePrompt", "Rewrite this article.\n")
    PromptVars("Tone", {"tone": "direct"})
    CompletionParams("Default")
    Petition("ArticleRewrite", "ArticlePrompt", "Default", "Tone")
    Completion("Parent", "Earlier answer", "ArticleRewrite")
    Completion("Child", "Follow-up answer", "ArticleRewrite", "Parent")
    NamedList("Topics", ["articles", "history"])

    copied = dataclasses.replace(Prompt("ArticlePrompt", Missing), name="CopiedPrompt")
    copied.snapshot.save()

    petition = Petition.snapshots.get("ArticleRewrite")
    petition.load_all()
    child = Completion.snapshots.get("Child")
    child.load_all()

    assert petition.prompt.content == "Rewrite this article.\n"
    assert petition.params.stop == ["\n"]
    assert petition.promptvars.vars == {"tone": "direct"}
    assert child.parent.text == "Earlier answer"
    assert Prompt.snapshots.get("CopiedPrompt").content == "Rewrite this article.\n"
    assert NamedList.snapshots.get("Topics").items == ["articles", "history"]


def test_chat_transcripts_expand_includes_and_save_generated_runs(tmp_path):
    package_prompts = Stash(tmp_path / "package-prompts")
    run_outputs = Stash(tmp_path / "runs" / "2026-03-01")

    @snapclass
    class EngineParams:
        model: str = "gpt-5-chat-latest"
        temperature: float | None = None

        def non_none(self) -> dict:
            return {
                "model": self.model,
                **({"temperature": self.temperature} if self.temperature is not None else {}),
            }

    @snapclass("{self.name}.txt", stash=package_prompts, manual=True, formatter=TextFormatter)
    class TextAsset:
        name: str
        content: str = ""

    @snapclass("{self.name}.yml", stash=package_prompts, manual=True)
    class ChatPrompt:
        name: str
        params: EngineParams | None = None
        messages: list[dict[str, str]] = field(default_factory=list)

        def expanded_messages(self) -> list[dict[str, str]]:
            expanded: list[dict[str, str]] = []
            for message in self.messages:
                for role, content in message.items():
                    if role == "include":
                        include = ChatPrompt.snapshots(self.snapshot.stash).get(content)
                        expanded.extend(include.expanded_messages())
                    elif role == "text":
                        asset = TextAsset.snapshots(self.snapshot.stash).get(content)
                        expanded.append({"system": asset.content})
                    else:
                        expanded.append({role: content})
            return expanded

    TextAsset("house-style", "Use concise project language.\n").snapshot.save()
    ChatPrompt(
        "shared",
        messages=[{"text": "house-style"}],
    ).snapshot.save()
    ChatPrompt(
        "rewrite",
        params=EngineParams(temperature=0.2),
        messages=[{"include": "shared"}, {"user": "Rewrite the article."}],
    ).snapshot.save()

    prompt = ChatPrompt.snapshots.get("rewrite")
    generated = ChatPrompt.snapshots(run_outputs).get_or_create(
        "rewrite-output",
        params=prompt.params,
        messages=prompt.expanded_messages()
        + [{"assistant": "Article rewritten."}],
    )
    generated.snapshot.save()

    output_path = tmp_path / "runs" / "2026-03-01" / "rewrite-output.yml"

    assert prompt.params.non_none() == {
        "model": "gpt-5-chat-latest",
        "temperature": 0.2,
    }
    assert prompt.expanded_messages() == [
        {"system": "Use concise project language.\n"},
        {"user": "Rewrite the article."},
    ]
    assert "assistant: Article rewritten." in output_path.read_text(encoding="utf-8")
    assert not (tmp_path / "package-prompts" / "rewrite-output.yml").exists()


def test_metadata_registry_keeps_users_images_and_thread_links(tmp_path):
    base = Stash(tmp_path / "registry")
    salt = "example.salt"

    @snapclass("users/{self.user_id}.yml", stash=base)
    class ExternalUser:
        user_id: int
        name: str
        discriminator: str | None = None
        user_hash: str | None = None
        last_server: str | None = None
        last_channel: str | None = None

        def load_hash(self) -> None:
            digest = hashlib.sha256(f"{self.user_id}:{salt}".encode("utf-8")).hexdigest()
            self.user_hash = "usr_" + digest

    @snapclass("imageinfo/{self.name}.yml", stash=base)
    class ImageInfo:
        name: str
        filepath: str
        data: dict = field(default_factory=dict)

    @snapclass("threads/{self.thread_id}.yml", stash=base)
    class ThreadInfo:
        thread_id: int
        chat_name: str
        message_ids: list[int] = field(default_factory=list)

    user = ExternalUser(
        42,
        "mattie",
        discriminator="0001",
        last_server="Workshop",
        last_channel="general",
    )
    user.load_hash()
    ImageInfo(
        "article-card",
        "images/article-card.png",
        data={"width": 1024, "height": 768},
    )
    thread = ThreadInfo(1001, "rewrite-output")
    thread.message_ids.extend([10, 11])

    loaded_user = ExternalUser.snapshots.get(42)
    loaded_thread = ThreadInfo.snapshots.get(1001)

    assert loaded_user.user_hash.startswith("usr_")
    assert loaded_user.last_channel == "general"
    assert ImageInfo.snapshots.get("article-card").data["width"] == 1024
    assert loaded_thread.message_ids == [10, 11]
    assert "chat_name: rewrite-output" in (
        tmp_path / "registry" / "threads" / "1001.yml"
    ).read_text(encoding="utf-8")
