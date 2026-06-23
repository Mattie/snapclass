from __future__ import annotations

from dataclasses import dataclass, field

from snapclass import Stash, snapclass, sidecar, sync


def test_lorebubble_world_bound_assets_sidecars_and_logs(tmp_path):
    root = Stash(tmp_path / ".lorebubble")
    world = root / "{world}"
    articles = world / "article"
    types = world / "types"
    users = world / "users"
    ontologies = world / "ontology"
    timelines = world / "timeline"
    recordings = world / "recordings"

    @snapclass("lore.yml", stash=world, defaults=True)
    class LoreSchemas:
        name: str
        description: str = ""
        active_timeline: str = "main"
        tags: list[str] = field(default_factory=list)

    @snapclass("{self.slug}/article.yml", stash=articles)
    class Article:
        slug: str
        title: str = ""
        content_file: str = ""
        tags: list[str] = field(default_factory=list)
        relationships: list[dict[str, str]] = field(default_factory=list)
        version_history: list[dict[str, str]] = field(default_factory=list)

        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    @snapclass("{self.name}.yml", stash=types, defaults=True)
    class Type:
        name: str
        description: str = ""
        fields: list[dict[str, str]] = field(default_factory=list)

    @snapclass("{self.name}.yml", stash=users, defaults=True)
    class User:
        name: str
        role: str = "writer"
        visible_worlds: list[str] = field(default_factory=list)

    @snapclass("{self.name}.yml", stash=ontologies, defaults=True)
    class RelationshipOntology:
        name: str
        relationships: list[dict[str, str]] = field(default_factory=list)

    @snapclass("{self.name}_v{self.revision}.yml", stash=timelines, defaults=True)
    class Timeline:
        name: str
        revision: int = 1
        summary: str = ""
        eras: list[dict[str, str]] = field(default_factory=list)

    @snapclass("{self.name}.yml", stash=recordings, defaults=True)
    class CommandRecordingLog:
        name: str
        status: str = "recording"
        commands: list[dict[str, str]] = field(default_factory=list)

    storybook = world.bind(world="Storybook")
    storybook_articles = articles.bind(world="Storybook")
    storybook_types = types.bind(world="Storybook")
    storybook_users = users.bind(world="Storybook")
    storybook_ontologies = ontologies.bind(world="Storybook")
    storybook_timelines = timelines.bind(world="Storybook")
    storybook_recordings = recordings.bind(world="Storybook")
    other_articles = articles.bind(world="OtherWorld")

    schemas = LoreSchemas.snapshots(storybook).get_or_create(
        "Storybook",
        description="A world fixture",
        tags=["fairytale"],
    )
    article = Article.snapshots(storybook_articles).get_or_create(
        "dusk-court",
        title="Dusk Court",
        tags=["place"],
    )
    article.body = "# Dusk Court\n\nMoonlit court notes.\n"
    article.relationships.append({"kind": "neighbor", "target": "lantern-gate"})
    article.version_history.append({"summary": "created"})
    place_type = Type.snapshots(storybook_types).get_or_create(
        "place",
        description="A location in the world",
    )
    place_type.fields.append({"name": "summary", "kind": "text"})
    User.snapshots(storybook_users).get_or_create(
        "mattie",
        visible_worlds=["Storybook"],
    )
    ontology = RelationshipOntology.snapshots(storybook_ontologies).get_or_create("main")
    ontology.relationships.append({"name": "neighbor", "inverse": "neighbor"})

    timeline = Timeline.snapshots(storybook_timelines).get_or_create("main", 1)
    timeline.eras.append({"name": "First Light"})

    log = CommandRecordingLog.snapshots(storybook_recordings).get_or_create("commands")
    log.commands.append({"command": "article.create", "target": "dusk-court"})

    other_article = Article.snapshots(other_articles).get_or_create(
        "dusk-court",
        title="Other Dusk Court",
    )
    other_article.body = "# Other World\n"

    storybook_path = tmp_path / ".lorebubble" / "Storybook"
    other_world_path = tmp_path / ".lorebubble" / "OtherWorld"
    metadata = storybook_path / "article" / "dusk-court" / "article.yml"
    body = storybook_path / "article" / "dusk-court" / "dusk-court.md"

    assert schemas.snapshot.path == storybook_path / "lore.yml"
    assert "active_timeline: main" in schemas.snapshot.text
    assert body.read_text(encoding="utf-8") == "# Dusk Court\n\nMoonlit court notes.\n"
    metadata_text = metadata.read_text(encoding="utf-8")
    assert "content_file: dusk-court.md" in metadata_text
    assert "target: lantern-gate" in metadata_text
    assert "summary: created" in metadata_text
    type_text = (storybook_path / "types" / "place.yml").read_text(encoding="utf-8")
    assert "description: A location in the world" in type_text
    assert "kind: text" in type_text
    user_text = (storybook_path / "users" / "mattie.yml").read_text(encoding="utf-8")
    assert "role: writer" in user_text
    assert "- Storybook" in user_text
    ontology_text = (storybook_path / "ontology" / "main.yml").read_text(
        encoding="utf-8"
    )
    assert "name: neighbor" in ontology_text
    assert "inverse: neighbor" in ontology_text
    timeline_text = (storybook_path / "timeline" / "main_v1.yml").read_text(
        encoding="utf-8"
    )
    assert "summary: ''" in timeline_text
    assert "First Light" in timeline_text
    recording_text = (storybook_path / "recordings" / "commands.yml").read_text(
        encoding="utf-8"
    )
    assert "status: recording" in recording_text
    assert "article.create" in recording_text

    loaded_article = Article.snapshots(storybook_articles).get("dusk-court")
    assert loaded_article.body.startswith("# Dusk Court")
    assert loaded_article.relationships == [
        {"kind": "neighbor", "target": "lantern-gate"}
    ]
    assert loaded_article.version_history == [{"summary": "created"}]
    assert Type.snapshots(storybook_types).get("place").fields == [
        {"name": "summary", "kind": "text"}
    ]
    loaded_user = User.snapshots(storybook_users).get("mattie")
    loaded_ontology = RelationshipOntology.snapshots(storybook_ontologies).get("main")
    loaded_other_article = Article.snapshots(other_articles).get("dusk-court")
    assert loaded_user.visible_worlds == ["Storybook"]
    assert loaded_ontology.relationships == [
        {"name": "neighbor", "inverse": "neighbor"}
    ]
    assert loaded_other_article.body == "# Other World\n"
    assert (other_world_path / "article" / "dusk-court" / "article.yml").exists()

    body.unlink()
    stale_article = Article.snapshots(storybook_articles).get("dusk-court")
    assert stale_article.body.snapshot.stale is True


def test_lorebubble_world_stashes_are_isolated_by_base_root_and_world(tmp_path):
    root_a = Stash(tmp_path / "user-a" / ".lorebubble")
    root_b = Stash(tmp_path / "user-b" / ".lorebubble")
    articles_a = root_a / "{world}" / "article"
    articles_b = root_b / "{world}" / "article"

    @snapclass("{self.slug}/article.yml", stash=articles_a)
    class Article:
        slug: str
        title: str = ""
        content_file: str = ""

        body: str = sidecar.text(field="content_file", default="{self.slug}.md")

    a_storybook = articles_a.bind(world="Storybook")
    a_other = articles_a.bind(world="OtherWorld")
    b_storybook = articles_b.bind(world="Storybook")

    article_a = Article.snapshots(a_storybook).get_or_create(
        "dusk-court",
        title="A Storybook",
    )
    article_a.body = "# A Storybook\n"
    article_other = Article.snapshots(a_other).get_or_create(
        "dusk-court",
        title="A Other World",
    )
    article_other.body = "# A Other World\n"
    article_b = Article.snapshots(b_storybook).get_or_create(
        "dusk-court",
        title="B Storybook",
    )
    article_b.body = "# B Storybook\n"

    a_storybook_article = Article.snapshots(a_storybook).get("dusk-court")
    a_other_article = Article.snapshots(a_other).get("dusk-court")
    b_storybook_article = Article.snapshots(b_storybook).get("dusk-court")

    assert a_storybook_article.title == "A Storybook"
    assert a_storybook_article.body == "# A Storybook\n"
    assert a_other_article.title == "A Other World"
    assert a_other_article.body == "# A Other World\n"
    assert b_storybook_article.title == "B Storybook"
    assert b_storybook_article.body == "# B Storybook\n"
    assert a_storybook_article.snapshot.path == (
        tmp_path
        / "user-a"
        / ".lorebubble"
        / "Storybook"
        / "article"
        / "dusk-court"
        / "article.yml"
    )
    assert a_other_article.snapshot.path == (
        tmp_path
        / "user-a"
        / ".lorebubble"
        / "OtherWorld"
        / "article"
        / "dusk-court"
        / "article.yml"
    )
    assert b_storybook_article.snapshot.path == (
        tmp_path
        / "user-b"
        / ".lorebubble"
        / "Storybook"
        / "article"
        / "dusk-court"
        / "article.yml"
    )


def test_lorebubble_workflow_snapshot_syncs_under_bound_world(tmp_path):
    world = Stash(tmp_path / ".lorebubble") / "{world}"
    snapshots = world / "workflow"

    @dataclass
    class WorkflowSnapshot:
        name: str
        status: str = "started"
        events: list[dict[str, str]] = field(default_factory=list)

    snapshot = sync(
        WorkflowSnapshot("index-rebuild"),
        "{self.name}.yml",
        stash=snapshots.bind(world="Storybook"),
        defaults=True,
    )
    snapshot.events.append({"stage": "articles"})
    snapshot.status = "complete"

    path = tmp_path / ".lorebubble" / "Storybook" / "workflow" / "index-rebuild.yml"
    saved = path.read_text(encoding="utf-8")

    assert "status: complete" in saved
    assert "stage: articles" in saved


def test_lorebubble_loads_package_prompt_and_saves_generated_output_under_world(tmp_path):
    package_prompts = tmp_path / "package" / "prompts"
    package_prompts.mkdir(parents=True)
    package_prompt = package_prompts / "article-rewrite.yml"
    package_prompt.write_text(
        "messages:\n"
        "  - system: Rewrite lore articles for consistency.\n",
        encoding="utf-8",
    )

    world = Stash(tmp_path / ".lorebubble") / "{world}"
    runs = world / "runs"

    @snapclass("{self.name}.yml", stash=Stash(tmp_path / "default-prompts"), manual=True)
    class Chat:
        name: str
        messages: list[dict[str, str]] = field(default_factory=list)

    prompt = Chat("article-rewrite")
    prompt.snapshot.load(package_prompt)

    generated = Chat.snapshots(runs.bind(world="Storybook")).get_or_create(
        "article-rewrite-output",
        messages=prompt.messages + [{"assistant": "Rewritten article text."}],
    )
    generated.snapshot.save()

    generated_path = (
        tmp_path
        / ".lorebubble"
        / "Storybook"
        / "runs"
        / "article-rewrite-output.yml"
    )

    assert prompt.snapshot.path == package_prompt
    assert prompt.messages == [{"system": "Rewrite lore articles for consistency."}]
    assert package_prompt.read_text(encoding="utf-8") == (
        "messages:\n"
        "  - system: Rewrite lore articles for consistency.\n"
    )
    assert generated.snapshot.path == generated_path
    assert "Rewritten article text." in generated_path.read_text(encoding="utf-8")
    assert not (tmp_path / "default-prompts" / "article-rewrite-output.yml").exists()
