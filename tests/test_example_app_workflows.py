from __future__ import annotations

from dataclasses import field
from typing import ClassVar

from snapclass import Stash, snapclass, sidecar


def test_project_tracker_keeps_user_tasks_and_run_archives_separate(tmp_path):
    current = Stash(tmp_path / "tracker")
    archive = Stash(tmp_path / "archive" / "2026-03-01")

    @snapclass("{self.user}/projects/{self.project}/{self.slug}.yml", stash=current)
    class Task:
        user: str
        project: str
        slug: str
        title: str
        status: str = "open"
        meta: dict = field(default_factory=dict)
        history: list[dict] = field(default_factory=list)

    task = Task(
        "mattie",
        "snapclass",
        "add-tests",
        "Add behavioral tests",
        meta={"priority": "high", "area": "tests"},
    )
    task.history.append({"event": "created"})
    task.status = "done"
    Task("mattie", "snapclass", "review-plan", "Review plan", meta={"priority": "high"})

    archived = Task.snapshots(archive).get_or_create(
        "mattie",
        "snapclass",
        "add-tests",
        title="Archived task",
        status="done",
    )
    archived.history.append({"event": "archived"})

    current_path = (
        tmp_path
        / "tracker"
        / "mattie"
        / "projects"
        / "snapclass"
        / "add-tests.yml"
    )
    archive_path = (
        tmp_path
        / "archive"
        / "2026-03-01"
        / "mattie"
        / "projects"
        / "snapclass"
        / "add-tests.yml"
    )

    assert "status: done" in current_path.read_text(encoding="utf-8")
    assert "event: created" in current_path.read_text(encoding="utf-8")
    assert "event: archived" in archive_path.read_text(encoding="utf-8")
    assert sorted(task.slug for task in Task.snapshots.filter(meta__priority="high")) == [
        "add-tests",
        "review-plan",
    ]
    assert Task.snapshots(archive).get("mattie", "snapclass", "add-tests").title == (
        "Archived task"
    )


def test_notes_app_uses_yaml_metadata_with_text_sidecars(tmp_path):
    notes = Stash(tmp_path / "notes")

    @snapclass("{self.date}/{self.slug}.yml", stash=notes, manual=True)
    class Note:
        date: str
        slug: str
        title: str = ""
        body_file: str = ""
        tags: list[str] = field(default_factory=list)
        backlinks: list[str] = field(default_factory=list)

        body: str = sidecar.text(field="body_file", default="{self.slug}.md")

    note = Note(
        "2026-03-01",
        "daily",
        title="Daily Note",
        tags=["journal", "snapclass"],
        backlinks=["projects/snapclass"],
    )
    note.body = "# Daily Note\n\nKeep the tests practical.\n"

    metadata_path = tmp_path / "notes" / "2026-03-01" / "daily.yml"
    body_path = tmp_path / "notes" / "2026-03-01" / "daily.md"
    body_path.write_text("# Daily Note\n\nEdited by a person.\n", encoding="utf-8")

    loaded = Note.snapshots.get("2026-03-01", "daily")

    assert "body_file: daily.md" in metadata_path.read_text(encoding="utf-8")
    assert "- journal" in metadata_path.read_text(encoding="utf-8")
    assert loaded.body == "# Daily Note\n\nEdited by a person.\n"
    assert loaded.backlinks == ["projects/snapclass"]


def test_recipe_app_keeps_nested_yaml_and_derived_lists_readable(tmp_path):
    kitchen = Stash(tmp_path / "kitchen")

    @snapclass("recipes/{self.slug}.yml", stash=kitchen, manual=True, defaults=True)
    class Recipe:
        slug: str
        title: str
        ingredients: list[dict] = field(default_factory=list)
        steps: list[str] = field(default_factory=list)
        notes: dict = field(default_factory=dict)
        runtime_cache: ClassVar[dict]

    @snapclass("plans/{self.week}.yml", stash=kitchen, manual=True)
    class MealPlan:
        week: str
        recipes: list[str] = field(default_factory=list)

        def shopping_list(self) -> list[str]:
            items: list[str] = []
            for slug in self.recipes:
                recipe = Recipe.snapshots.get(slug)
                items.extend(item["name"] for item in recipe.ingredients)
            return sorted(items)

    recipe = Recipe(
        "lentil-soup",
        "Lentil Soup",
        ingredients=[
            {"name": "lentils", "amount": "1 cup"},
            {"name": "carrot", "amount": "2"},
        ],
        steps=["Rinse lentils", "Simmer until tender"],
        notes={"serves": 4},
    )
    recipe.runtime_cache = {"last_scaled": "twice"}
    recipe.snapshot.save()
    MealPlan("2026-W10", ["lentil-soup"]).snapshot.save()

    recipe_text = (tmp_path / "kitchen" / "recipes" / "lentil-soup.yml").read_text(
        encoding="utf-8"
    )
    plan = MealPlan.snapshots.get("2026-W10")

    assert "title: Lentil Soup" in recipe_text
    assert "name: lentils" in recipe_text
    assert "Simmer until tender" in recipe_text
    assert "runtime_cache" not in recipe_text
    assert plan.shopping_list() == ["carrot", "lentils"]
