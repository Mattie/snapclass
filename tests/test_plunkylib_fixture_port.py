from __future__ import annotations

import dataclasses
from dataclasses import field
from typing import ClassVar

from snapclass import Missing, Stash, snapclass
from snapclass.formatters import TypedTextFormatter


def test_plunkylib_schemas_fixture_chained_collection_relationships(tmp_path):
    root = Stash(tmp_path / "plunkylib")

    @snapclass("prompts/{self.name}.txt", stash=root, formatter=TypedTextFormatter)
    class Prompt:
        name: str
        text: str

    @snapclass("prompts/{self.chatprompt_name}.yml", stash=root)
    class ChatPrompt:
        chatprompt_name: str
        messages: list[dict[str, str]]

    @snapclass("promptvars/{self.name}.yml", stash=root)
    class PromptVars:
        name: str
        vars: dict[str, str]

    @snapclass("params/{self.name}.yml", stash=root, defaults=True)
    class CompletionParams:
        name: str
        engine: str = "text-davinci-002"
        stop: list[str] = field(default_factory=lambda: ["\n"])
        temperature: float = 0.0
        frequency_penalty: float = 0.0
        presence_penalty: float = 0.0
        logprobs: int | None = None

    @snapclass("petition/{self.name}.yml", stash=root)
    class Petition:
        name: str
        params_name: str
        prompt_name: str | None = None
        chatprompt_name: str | None = None
        promptvars_name: str | None = None
        prompt: ClassVar[Prompt]
        chatprompt: ClassVar[ChatPrompt]
        params: ClassVar[CompletionParams]
        promptvars: ClassVar[PromptVars]

        def load_all(self):
            if (
                self.prompt_name is None or self.prompt_name == ""
            ) and self.chatprompt_name is None:
                self.prompt_name = self.name
            if self.chatprompt_name is not None:
                self.chatprompt = ChatPrompt.snapshots.get(self.chatprompt_name)
                self.prompt = None
            else:
                self.prompt = Prompt.snapshots.get(self.prompt_name)
                self.chatprompt = None
            self.params = CompletionParams.snapshots.get(self.params_name)
            self.promptvars = (
                PromptVars.snapshots.get(self.promptvars_name)
                if self.promptvars_name is not None
                else None
            )

    @snapclass("completions/{self.name}.yml", stash=root)
    class Completion:
        name: str
        text: str
        petition_name: str
        parent_name: str | None = None
        petition: ClassVar[Petition]
        parent: ClassVar["Completion"]

        def load_all(self):
            self.petition = (
                Petition.snapshots.get(self.petition_name)
                if self.petition_name is not Missing and self.petition_name is not None
                else None
            )
            self.parent = (
                Completion.snapshots.get(self.parent_name)
                if self.parent_name is not Missing and self.parent_name is not None
                else None
            )

    Prompt("AnswerBot", "Answer carefully.\n")
    ChatPrompt("Chatty", [{"system": "{prompt.AnswerBot}"}, {"user": "Hello"}])
    PromptVars("CareVars", {"tone": "careful", "audience": "reader"})
    CompletionParams("Careful")
    Petition("AnswerBot", params_name="Careful", promptvars_name="CareVars")
    Petition("ChatPetition", params_name="Careful", chatprompt_name="Chatty")
    Completion("Parent", "Earlier answer", petition_name="AnswerBot")
    Completion("Child", "Follow-up answer", petition_name="ChatPetition", parent_name="Parent")

    expected_paths = [
        tmp_path / "plunkylib" / "prompts" / "AnswerBot.txt",
        tmp_path / "plunkylib" / "prompts" / "Chatty.yml",
        tmp_path / "plunkylib" / "promptvars" / "CareVars.yml",
        tmp_path / "plunkylib" / "params" / "Careful.yml",
        tmp_path / "plunkylib" / "petition" / "AnswerBot.yml",
        tmp_path / "plunkylib" / "petition" / "ChatPetition.yml",
        tmp_path / "plunkylib" / "completions" / "Parent.yml",
        tmp_path / "plunkylib" / "completions" / "Child.yml",
    ]
    missing_paths = [path for path in expected_paths if not path.exists()]
    assert missing_paths == []

    prompt_text = (tmp_path / "plunkylib" / "prompts" / "AnswerBot.txt").read_text(
        encoding="utf-8"
    )
    assert "text|str\nAnswer carefully.\n" in prompt_text
    params_text = (tmp_path / "plunkylib" / "params" / "Careful.yml").read_text(
        encoding="utf-8"
    )
    assert "engine: text-davinci-002" in params_text
    assert "stop:" in params_text
    assert "temperature: 0.0" in params_text
    assert "frequency_penalty: 0.0" in params_text
    assert "presence_penalty: 0.0" in params_text
    assert "logprobs:" in params_text
    promptvars_text = (
        tmp_path / "plunkylib" / "promptvars" / "CareVars.yml"
    ).read_text(encoding="utf-8")
    assert "tone: careful" in promptvars_text
    assert "audience: reader" in promptvars_text

    petition = Petition.snapshots.get("AnswerBot")
    petition.load_all()
    assert petition.prompt.text == "Answer carefully.\n"
    assert petition.chatprompt is None
    assert petition.params.engine == "text-davinci-002"
    assert petition.params.stop == ["\n"]
    assert petition.params.frequency_penalty == 0.0
    assert petition.params.presence_penalty == 0.0
    assert petition.params.logprobs is None
    assert petition.promptvars.vars == {"tone": "careful", "audience": "reader"}

    chat_petition = Petition.snapshots.get("ChatPetition")
    chat_petition.load_all()
    assert chat_petition.prompt is None
    assert chat_petition.chatprompt.messages == [
        {"system": "{prompt.AnswerBot}"},
        {"user": "Hello"},
    ]
    assert chat_petition.promptvars is None

    child = Completion.snapshots.get("Child")
    child.load_all()
    assert child.petition.chatprompt_name == "Chatty"
    assert child.parent.text == "Earlier answer"

    saved_child = (tmp_path / "plunkylib" / "completions" / "Child.yml").read_text(
        encoding="utf-8"
    )
    assert "petition:" not in saved_child
    assert "parent:" not in saved_child


def test_plunkylib_vector_search_fixture_chains_embedding_params(tmp_path):
    root = Stash(tmp_path / "plunkylib")

    @snapclass("prompts/{self.name}.txt", stash=root, formatter=TypedTextFormatter)
    class Prompt:
        name: str
        text: str

    @snapclass("params/{self.name}.yml", stash=root, defaults=True)
    class CompletionParams:
        name: str
        engine: str = "text-davinci-002"

    @snapclass("vectorsearchparams/{self.name}.yml", stash=root, defaults=True)
    class VectorSearchParams:
        name: str
        engine: str = "pinecone"
        embedding: str = "ExampleGPT3_Embedding"
        top_k: int = 1
        include_metadata: bool = False
        embedding_params: ClassVar[CompletionParams]

        def load_all(self):
            if self.embedding is not None:
                self.embedding_params = CompletionParams.snapshots.get(self.embedding)

    @snapclass("vectorsearch/{self.name}.yml", stash=root)
    class VectorSearch:
        name: str
        params_name: str
        prompt_name: str | None = None
        prompt: ClassVar[Prompt]
        params: ClassVar[VectorSearchParams]

        def load_all(self):
            if self.prompt_name is None or self.prompt_name == "":
                self.prompt_name = self.name
            self.prompt = Prompt.snapshots.get(self.prompt_name)
            self.params = VectorSearchParams.snapshots.get(self.params_name)
            self.params.load_all()

    @snapclass("namedlists/{self.list_name}.yml", stash=root)
    class NamedList:
        list_name: str
        items: list[str]

    Prompt("SearchPrompt", "Find matching passages.\n")
    CompletionParams("ExampleGPT3_Embedding", engine="text-embedding-ada-002")
    VectorSearchParams("SearchParams")
    VectorSearch("SearchPrompt", params_name="SearchParams")
    NamedList("Topics", ["alpha", "beta"])

    vector_params_text = (
        tmp_path / "plunkylib" / "vectorsearchparams" / "SearchParams.yml"
    ).read_text(encoding="utf-8")
    assert "engine: pinecone" in vector_params_text
    assert "embedding: ExampleGPT3_Embedding" in vector_params_text
    assert "top_k: 1" in vector_params_text
    assert "include_metadata: false" in vector_params_text
    assert (tmp_path / "plunkylib" / "vectorsearch" / "SearchPrompt.yml").exists()
    assert (tmp_path / "plunkylib" / "namedlists" / "Topics.yml").exists()

    search = VectorSearch.snapshots.get("SearchPrompt")
    search.load_all()
    assert search.prompt.text == "Find matching passages.\n"
    assert search.params.embedding_params.engine == "text-embedding-ada-002"
    assert search.params.top_k == 1
    assert search.params.include_metadata is False
    assert NamedList.snapshots.get("Topics").items == ["alpha", "beta"]


def test_plunkylib_prompt_collection_reads_external_text_edits(tmp_path):
    root = Stash(tmp_path / "plunkylib")

    @snapclass("prompts/{self.name}.txt", stash=root, formatter=TypedTextFormatter)
    class Prompt:
        name: str
        text: str

    Prompt("AnswerBot", "Original prompt.\n")
    prompt_path = tmp_path / "plunkylib" / "prompts" / "AnswerBot.txt"
    prompt_path.write_text(
        f"text|str\nEdited by a human.\n\n{TypedTextFormatter.divider}\n",
        encoding="utf-8",
    )

    assert Prompt.snapshots.get("AnswerBot").text == "Edited by a human.\n"


def test_plunkylib_cli_style_copy_uses_missing_and_dataclasses_replace(tmp_path):
    root = Stash(tmp_path / "plunkylib")

    @snapclass("prompts/{self.name}.txt", stash=root, formatter=TypedTextFormatter)
    class Prompt:
        name: str
        text: str

    Prompt("SourcePrompt", "Copy this prompt.\n")

    copied = dataclasses.replace(Prompt("SourcePrompt", Missing), name="CopiedPrompt")
    copied.snapshot.save()

    assert copied.name == "CopiedPrompt"
    assert copied.text == "Copy this prompt.\n"
    assert Prompt.snapshots.get("CopiedPrompt").text == "Copy this prompt.\n"
