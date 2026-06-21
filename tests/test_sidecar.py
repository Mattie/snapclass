from __future__ import annotations

import os
from pathlib import Path

import pytest

from snapclass import SnapclassError, Stash, snapclass, sidecar


def test_markdown_sidecar_reads_and_writes_next_to_metadata(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        body = sidecar.markdown("{self.slug}.md")

    article = Article("dusk-court", "Dusk Court")
    article.snapshot.save()
    article.body.write("# Dusk Court\n")

    assert article.body.path == tmp_path / "world" / "article" / "dusk-court" / "dusk-court.md"
    assert article.body.read() == "# Dusk Court\n"


def test_markdown_sidecar_updates_relative_pointer_field(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    article = Article("dusk-court", "Dusk Court")
    article.snapshot.save()
    article.body.write("# Dusk Court\n")

    metadata = tmp_path / "world" / "article" / "dusk-court" / "article.yml"
    body = tmp_path / "world" / "article" / "dusk-court" / "dusk-court.md"

    assert body.read_text(encoding="utf-8") == "# Dusk Court\n"
    assert "content_file: dusk-court.md" in metadata.read_text(encoding="utf-8")

    loaded = Article.snapshots.get("dusk-court")
    assert loaded.content_file == "dusk-court.md"
    assert loaded.body.path == body
    assert loaded.body.read() == "# Dusk Court\n"


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
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    article = Article("dusk-court", "Dusk Court")
    article.snapshot.save()
    article.body.write("# Original\n")

    metadata = tmp_path / "world" / "article" / "dusk-court" / "article.yml"
    body = tmp_path / "world" / "article" / "dusk-court" / "dusk-court.md"
    metadata.write_text(
        "title: Human edit\ncontent_file: dusk-court.md\n",
        encoding="utf-8",
    )

    with pytest.raises(SnapclassError, match="externally modified"):
        article.body.write("# Local edit\n")

    assert body.read_text(encoding="utf-8") == "# Original\n"
    assert "Human edit" in metadata.read_text(encoding="utf-8")


def test_markdown_sidecar_pointer_stays_relative_after_metadata_move(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    article = Article("ember", "Ember")
    moved_metadata = tmp_path / "world" / "article" / "archive" / "ember.yml"
    article.snapshot.save(moved_metadata)
    article.body.write("Moved body\n")

    assert article.content_file == "ember.md"
    assert (tmp_path / "world" / "article" / "archive" / "ember.md").read_text(
        encoding="utf-8"
    ) == "Moved body\n"
    assert "content_file: ember.md" in moved_metadata.read_text(encoding="utf-8")


def test_markdown_sidecar_moves_with_metadata_path_changes(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    article = Article("ember", "Ember")
    article.snapshot.save()
    article.body.write("Original body\n")

    old_body = tmp_path / "world" / "article" / "ember" / "ember.md"
    article.slug = "ember-renamed"
    article.snapshot.save()

    new_metadata = tmp_path / "world" / "article" / "ember-renamed" / "article.yml"
    new_body = tmp_path / "world" / "article" / "ember-renamed" / "ember.md"

    assert not old_body.exists()
    assert new_body.read_text(encoding="utf-8") == "Original body\n"
    assert "content_file: ember.md" in new_metadata.read_text(encoding="utf-8")
    assert article.body.read() == "Original body\n"


def test_markdown_sidecar_move_conflict_raises_without_overwriting(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    article = Article("ember")
    article.snapshot.save()
    article.body.write("Original body\n")

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


def test_markdown_sidecar_relpath_falls_back_when_os_relpath_fails(tmp_path, monkeypatch):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="content/{self.slug}.md")

    article = Article("ember")
    article.snapshot.save()

    def fail_relpath(*_args, **_kwargs):
        raise ValueError("path is on mount 'Z:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", fail_relpath)
    article.body.write("Body\n")

    metadata = tmp_path / "world" / "article" / "ember" / "article.yml"
    assert article.content_file == "content/ember.md"
    assert "content_file: content/ember.md" in metadata.read_text(encoding="utf-8")


def test_markdown_sidecar_uses_existing_relative_pointer(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    article_dir = tmp_path / "world" / "article" / "ember"
    article_dir.mkdir(parents=True)
    (article_dir / "article.yml").write_text("content_file: content/body.md\n", encoding="utf-8")
    (article_dir / "content").mkdir()
    (article_dir / "content" / "body.md").write_text("Existing body\n", encoding="utf-8")

    loaded = Article.snapshots.get("ember")
    assert loaded.body.path == article_dir / "content" / "body.md"
    assert loaded.body.read() == "Existing body\n"


def test_markdown_sidecar_reports_stale_pointer_with_metadata_context(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    article_dir = tmp_path / "world" / "article" / "ember"
    article_dir.mkdir(parents=True)
    (article_dir / "article.yml").write_text("content_file: missing.md\n", encoding="utf-8")

    loaded = Article.snapshots.get("ember")

    assert loaded.body.stale is True
    with pytest.raises(sidecar.SidecarMissingError) as exc_info:
        loaded.body.read()

    assert exc_info.value.path == article_dir / "missing.md"
    assert exc_info.value.metadata_path == article_dir / "article.yml"
    assert exc_info.value.relative_path == Path("missing.md")
    assert exc_info.value.field == "content_file"
    assert "content_file" in str(exc_info.value)
    assert "article.yml" in str(exc_info.value)
    assert loaded.body.read(default="") == ""


def test_sidecar_rejects_absolute_or_traversing_paths(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.slug}.yml", stash=root, manual=True)
    class Article:
        slug: str
        body = sidecar.markdown("../escape.md")

    with pytest.raises(ValueError, match="traverse"):
        Article("a").body.path

    @snapclass("{self.slug}.yml", stash=root, manual=True)
    class AbsoluteArticle:
        slug: str
        body = sidecar.markdown(str(tmp_path / "outside.md"))

    with pytest.raises(ValueError, match="relative"):
        AbsoluteArticle("a").body.path


def test_sidecar_rejects_unsafe_pointer_field_values(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.slug}.yml", stash=root, manual=True)
    class Article:
        slug: str
        content_file: str = ""
        body = sidecar.markdown(field="content_file", default="{self.slug}.md")

    (tmp_path / "traversal.yml").write_text("content_file: ../escape.md\n", encoding="utf-8")
    with pytest.raises(ValueError, match="traverse"):
        Article.snapshots.get("traversal").body.path

    absolute = tmp_path / "outside.md"
    (tmp_path / "absolute.yml").write_text(f"content_file: {absolute}\n", encoding="utf-8")
    absolute_article = Article.snapshots.get("absolute")
    with pytest.raises(ValueError, match="relative"):
        absolute_article.body.path

    absolute_article.slug = "absolute-renamed"
    with pytest.raises(ValueError, match="relative"):
        absolute_article.snapshot.save()
