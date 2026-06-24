from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path

import pytest

from snapclass import SnapclassError, Stash, snapclass, sidecar


def test_sidecar_public_surface_is_typed_text_and_bytes():
    assert hasattr(sidecar, "text")
    assert hasattr(sidecar, "bytes")
    assert not hasattr(sidecar, "markdown")


def test_text_sidecar_values_read_and_write_next_to_metadata(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        body: str = sidecar.text("{self.slug}.md")

    article = Article("dusk-court", "Dusk Court", body="# Dusk Court\n")
    article.snapshot.save()

    assert [field.name for field in fields(Article)] == ["slug", "title"]
    assert isinstance(article.body, str)
    assert article.body == "# Dusk Court\n"
    assert article.body.snapshot.path == (
        tmp_path / "world" / "article" / "dusk-court" / "dusk-court.md"
    )
    assert "body" not in article.snapshot.text
    assert article.body.snapshot.read() == "# Dusk Court\n"
    assert article.body.snapshot.stash == articles


def test_sidecar_constructor_values_are_visible_before_ready_hook_runs(tmp_path):
    articles = Stash(tmp_path / "world") / "article"
    observed: list[str] = []

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        body: str = sidecar.text("{self.slug}.md")

        def __snapclass_ready__(self, *, snapshot):
            """Snapshot is attached and sidecar constructor values are visible."""
            observed.append(self.body)

    Article("dusk-court", body="# Dusk Court\n")

    assert observed == ["# Dusk Court\n"]


def test_sidecar_assignment_in_loaded_hook_does_not_rewrite_files(tmp_path):
    articles = Stash(tmp_path / "world") / "article"
    hook_calls: list[str] = []

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

        def __snapclass_loaded__(self, *, snapshot, path):
            """File data has been applied and sidecar hook writes stay in memory."""
            if not hook_calls:
                self.body = "Hook body\n"
            hook_calls.append("loaded")

    metadata = tmp_path / "world" / "article" / "dusk-court" / "article.yml"
    body = tmp_path / "world" / "article" / "dusk-court" / "dusk-court.md"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("content_file: dusk-court.md\n", encoding="utf-8")
    body.write_text("File body\n", encoding="utf-8")

    article = Article.snapshots.get("dusk-court")

    assert article.body == "Hook body\n"
    assert body.read_text(encoding="utf-8") == "File body\n"
    assert metadata.read_text(encoding="utf-8") == "content_file: dusk-court.md\n"

    article.snapshot.load()

    assert article.body == "File body\n"
    assert hook_calls == ["loaded", "loaded"]


def test_text_sidecar_can_use_explicit_stash(tmp_path):
    app = Stash(tmp_path / "world")
    articles = app / "article"
    assets = app / "assets"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body: str = sidecar.text(
            field="content_file",
            default="{self.slug}.md",
            stash=assets,
        )

    article = Article("dusk-court", body="# Dusk Court\n")
    article.snapshot.save()

    metadata = tmp_path / "world" / "article" / "dusk-court" / "article.yml"
    body = tmp_path / "world" / "assets" / "dusk-court.md"

    assert article.body.snapshot.stash == assets
    assert article.body.snapshot.path == body
    assert article.content_file == "dusk-court.md"
    assert "content_file: dusk-court.md" in metadata.read_text(encoding="utf-8")
    assert body.read_text(encoding="utf-8") == "# Dusk Court\n"

    loaded = Article.snapshots.get("dusk-court")
    assert loaded.body == "# Dusk Court\n"


def test_explicit_sidecar_stash_inherits_parent_bindings(tmp_path):
    root = Stash(tmp_path / ".lorebubble")
    world = root / "{world}"
    articles = world / "article"
    assets = world / "assets"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        body: str = sidecar.text("{self.slug}.md", stash=assets)

    storybook_articles = articles.bind(world="Storybook")
    article = Article.snapshots(storybook_articles).get_or_create("dusk-court")
    article.body = "# Dusk Court\n"

    assert article.body.snapshot.path == (
        tmp_path / ".lorebubble" / "Storybook" / "assets" / "dusk-court.md"
    )
    assert article.body == "# Dusk Court\n"

    loaded = Article.snapshots(storybook_articles).get("dusk-court")
    assert loaded.body == "# Dusk Court\n"


def test_relative_sidecar_stash_is_child_of_parent_stash(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        body: str = sidecar.text("{self.slug}.md", stash="assets")

    article = Article("dusk-court", body="# Dusk Court\n")

    body = tmp_path / "world" / "article" / "assets" / "dusk-court.md"

    assert article.body.snapshot.path == body
    assert body.read_text(encoding="utf-8") == "# Dusk Court\n"


def test_text_sidecar_assignment_updates_file(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        body: str = sidecar.text("{self.slug}.md")

    article = Article("dusk-court")

    assert article.body == ""
    assert article.body.snapshot.exists() is False

    article.body = "# Dusk Court\n"

    assert article.body == "# Dusk Court\n"
    assert article.body.snapshot.read() == "# Dusk Court\n"


def test_text_sidecar_updates_relative_pointer_field(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    article = Article("dusk-court", "Dusk Court", body="# Dusk Court\n")
    article.snapshot.save()

    metadata = tmp_path / "world" / "article" / "dusk-court" / "article.yml"
    body = tmp_path / "world" / "article" / "dusk-court" / "dusk-court.md"

    assert body.read_text(encoding="utf-8") == "# Dusk Court\n"
    assert "content_file: dusk-court.md" in metadata.read_text(encoding="utf-8")

    loaded = Article.snapshots.get("dusk-court")
    assert loaded.content_file == "dusk-court.md"
    assert loaded.body.snapshot.path == body
    assert loaded.body == "# Dusk Court\n"


def test_bytes_sidecar_round_trips_as_bytes(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        payload: bytes = sidecar.bytes("{self.slug}.bin")

    article = Article("dusk-court", payload=b"\x00abc")

    assert isinstance(article.payload, bytes)
    assert article.payload == b"\x00abc"
    assert article.payload.snapshot.read() == b"\x00abc"

    article.payload = b"updated"
    article.snapshot.save()

    loaded = Article.snapshots.get("dusk-court")
    assert isinstance(loaded.payload, bytes)
    assert loaded.payload == b"updated"
    assert loaded.payload.snapshot.path == (
        tmp_path / "world" / "article" / "dusk-court" / "dusk-court.bin"
    )


def test_sidecar_write_checks_metadata_conflict_before_content_write(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass(
        "{self.slug}/article.yml",
        stash=articles,
        manual=True,
        conflict="raise",
    )
    class Article:
        slug: str
        title: str = ""
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    article = Article("dusk-court", "Dusk Court", body="# Original\n")
    article.snapshot.save()

    metadata = tmp_path / "world" / "article" / "dusk-court" / "article.yml"
    body = tmp_path / "world" / "article" / "dusk-court" / "dusk-court.md"
    metadata.write_text(
        "title: Human edit\ncontent_file: dusk-court.md\n",
        encoding="utf-8",
    )

    with pytest.raises(SnapclassError, match="externally modified"):
        article.body = "# Local edit\n"

    assert body.read_text(encoding="utf-8") == "# Original\n"
    assert "Human edit" in metadata.read_text(encoding="utf-8")


def test_text_sidecar_pointer_stays_relative_after_metadata_move(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    article = Article("ember", "Ember")
    moved_metadata = tmp_path / "world" / "article" / "archive" / "ember.yml"
    article.snapshot.save(moved_metadata)
    article.body = "Moved body\n"

    assert article.content_file == "ember.md"
    assert (tmp_path / "world" / "article" / "archive" / "ember.md").read_text(
        encoding="utf-8"
    ) == "Moved body\n"
    assert "content_file: ember.md" in moved_metadata.read_text(encoding="utf-8")


def test_text_sidecar_moves_with_metadata_path_changes(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    article = Article("ember", "Ember")
    article.snapshot.save()
    article.body = "Original body\n"

    old_body = tmp_path / "world" / "article" / "ember" / "ember.md"
    article.slug = "ember-renamed"
    article.snapshot.save()

    new_metadata = tmp_path / "world" / "article" / "ember-renamed" / "article.yml"
    new_body = tmp_path / "world" / "article" / "ember-renamed" / "ember.md"

    assert not old_body.exists()
    assert new_body.read_text(encoding="utf-8") == "Original body\n"
    assert "content_file: ember.md" in new_metadata.read_text(encoding="utf-8")
    assert article.body == "Original body\n"


def test_text_sidecar_move_conflict_raises_without_overwriting(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    article = Article("ember")
    article.snapshot.save()
    article.body = "Original body\n"

    conflict = tmp_path / "world" / "article" / "ember-renamed" / "ember.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("Existing body\n", encoding="utf-8")

    article.slug = "ember-renamed"
    with pytest.raises(FileExistsError, match="destination already exists"):
        article.snapshot.save()

    assert (tmp_path / "world" / "article" / "ember" / "ember.md").read_text(
        encoding="utf-8"
    ) == "Original body\n"
    assert conflict.read_text(encoding="utf-8") == "Existing body\n"


def test_text_sidecar_relpath_falls_back_when_os_relpath_fails(
    tmp_path,
    monkeypatch,
):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="content/{self.slug}.md")

    article = Article("ember")
    article.snapshot.save()

    def fail_relpath(*_args, **_kwargs):
        raise ValueError("path is on mount 'Z:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", fail_relpath)
    article.body = "Body\n"

    metadata = tmp_path / "world" / "article" / "ember" / "article.yml"
    assert article.content_file == "content/ember.md"
    assert "content_file: content/ember.md" in metadata.read_text(encoding="utf-8")


def test_text_sidecar_uses_existing_relative_pointer(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    article_dir = tmp_path / "world" / "article" / "ember"
    article_dir.mkdir(parents=True)
    (article_dir / "article.yml").write_text(
        "content_file: content/body.md\n",
        encoding="utf-8",
    )
    (article_dir / "content").mkdir()
    (article_dir / "content" / "body.md").write_text(
        "Existing body\n",
        encoding="utf-8",
    )

    loaded = Article.snapshots.get("ember")
    assert loaded.body.snapshot.path == article_dir / "content" / "body.md"
    assert loaded.body == "Existing body\n"


def test_text_sidecar_reports_stale_pointer_with_metadata_context(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    article_dir = tmp_path / "world" / "article" / "ember"
    article_dir.mkdir(parents=True)
    (article_dir / "article.yml").write_text(
        "content_file: missing.md\n",
        encoding="utf-8",
    )

    loaded = Article.snapshots.get("ember")

    assert loaded.body == ""
    assert loaded.body.snapshot.stale is True
    with pytest.raises(sidecar.SidecarMissingError) as exc_info:
        loaded.body.snapshot.read()

    assert exc_info.value.path == article_dir / "missing.md"
    assert exc_info.value.metadata_path == article_dir / "article.yml"
    assert exc_info.value.relative_path == Path("missing.md")
    assert exc_info.value.field == "content_file"
    assert "content_file" in str(exc_info.value)
    assert "article.yml" in str(exc_info.value)
    assert loaded.body.snapshot.read(default="") == ""


def test_sidecar_rejects_absolute_or_traversing_paths(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.slug}.yml", stash=root, manual=True)
    class Article:
        slug: str
        body: str = sidecar.text("../escape.md")

    with pytest.raises(ValueError, match="traverse"):
        Article("a").body

    @snapclass("{self.slug}.yml", stash=root, manual=True)
    class AbsoluteArticle:
        slug: str
        body: str = sidecar.text(str(tmp_path / "outside.md"))

    with pytest.raises(ValueError, match="relative"):
        AbsoluteArticle("a").body


def test_sidecar_rejects_unsafe_pointer_field_values(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.slug}.yml", stash=root, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    (tmp_path / "traversal.yml").write_text(
        "content_file: ../escape.md\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="traverse"):
        Article.snapshots.get("traversal").body

    absolute = tmp_path / "outside.md"
    (tmp_path / "absolute.yml").write_text(
        f"content_file: {absolute}\n",
        encoding="utf-8",
    )
    absolute_article = Article.snapshots.get("absolute")
    with pytest.raises(ValueError, match="relative"):
        absolute_article.body

    absolute_article.slug = "absolute-renamed"
    with pytest.raises(ValueError, match="relative"):
        absolute_article.snapshot.save()
