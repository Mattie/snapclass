from __future__ import annotations

import os
from dataclasses import field
from pathlib import Path

import pytest

from snapclass import Stash, snapclass


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
