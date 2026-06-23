---
name: snapclass-fluency
description: Design, implement, review, and document idiomatic snapclass code in this repository. Use when needing lightweight persistence with Python classes, or specifically working with snapclass models, readable YAML/JSON/TOML/text output, stashes, sidecars, Fresh defaults, serializers, formatters, collections, sync/create_model/Model APIs, persistence policy, README examples, or tests that should follow snapclass's dataclass-first file persistence style.
---

# Snapclass Fluency

Use this skill when you need to write, review, or explain code in the snapclass style.

`snapclass` is a small persistence layer built around Python dataclasses. The happy path should feel like normal dataclass code that happens to save itself into readable project files. The library should stay friendly to apps and libraries that share one Python process, which means persistence policy belongs close to the file tree that uses it.

## Core Taste

Write snapclass code with these priorities:

- Make dataclasses the center of the story.
- Treat the file tree as a readable, versionable API.
- Keep persisted YAML, JSON, TOML, and text pleasant for humans to inspect and edit.
- Put location and persistence policy on `Stash`, especially when multiple libraries may share one process.
- Prefer Python values over wrapper objects in model attributes.
- Keep examples compact, concrete, and a little charming.
- Prefer explicit local policy over ambient process-global behavior.
- Keep the default path easy enough for scripts and notebooks.

The project has a friendly voice, but the code should stay boring in the good way: obvious dataclasses, obvious paths, obvious saves.

## Import Style

Prefer teaching-oriented imports from the root package. Lead with `snapclass`, then add the supporting helpers the example needs:

```python
from snapclass import snapclass, Stash, Fresh, sidecar, field
```

In examples, import only what is used:

```python
from snapclass import snapclass
```

```python
from snapclass import snapclass, Stash, Fresh
```

```python
from snapclass import snapclass, Stash, sidecar
```

The project re-exports `field` from `dataclasses`, but prefer `Fresh` for common mutable defaults in user-facing examples.

## Choosing The API

Use `@snapclass(pattern, ...)` for a new persisted dataclass:

```python
from snapclass import snapclass


@snapclass("{self.slug}.yml")
class Note:
    slug: str
    title: str
    body: str = ""
```

Use bare `@snapclass` for a nested dataclass that should behave like `@dataclass` and does not need its own snapshot:

```python
from snapclass import snapclass


@snapclass
class Style:
    voice: str
    temperature: float
```

Use `@snapclass()` when you need dataclass kwargs without persistence:

```python
from snapclass import snapclass


@snapclass(frozen=True, slots=True)
class Point:
    x: int
    y: int
```

Use `sync(instance, pattern, ...)` to attach persistence to an existing dataclass instance.

Use `create_model(cls, pattern, ...)` when an existing dataclass class should gain snapclass behavior.

Use `Model` with nested `Meta` when class inheritance is the local style or when a base class reads better than a decorator.

Use `auto("settings.yml")` for quick dynamic settings objects, scripts, and lightweight one-off config.

Use `Missing` when constructing an object that should load a required field from disk:

```python
from snapclass import snapclass, Missing


@snapclass("{self.name}.yml")
class Prompt:
    name: str
    text: str


prompt = Prompt("Example", Missing)
```

## Model Patterns

Path patterns are part of the model contract. Keep them readable and stable:

```python
@snapclass("{self.slug}.yml")
class Note:
    slug: str
    title: str
```

```python
@snapclass("{self.world}/{self.slug}/article.yml")
class Article:
    world: str
    slug: str
    title: str
```

Use model fields in paths when they identify the object. Avoid putting volatile values in path patterns.

The default flow is:

```python
note = Note("first", "Hello")
note.snapshot.save()
same_note = Note.snapshots.get("first")
```

Instances also expose convenience aliases:

```python
note.save()
note.load()
```

Use `manual=True` when writes should happen only when explicitly saved:

```python
@snapclass("{self.name}.yml", manual=True)
class Draft:
    name: str
    text: str
```

Use `defaults=True` when default-valued fields should be written into the file. Without it, snapclass favors concise files and omits fields that can be reconstructed from dataclass defaults.

```python
@snapclass("{self.name}.yml", defaults=True)
class Run:
    name: str
    metrics: dict[str, float] = Fresh.Dict
```

Use `include_defaults=` only when matching older code or naming; `defaults=` is the friendlier public spelling.

## Persistence Output Shapes

When teaching or reviewing snapclass behavior, pair the model with the file it writes. Future agents should understand that the readable file is part of the public feel of this library.

Fields used in the path pattern are usually omitted from the file because the path already restores them. Default-valued fields are also omitted unless `defaults=True` or an explicit field policy writes them.

Default YAML output should stay concise:

```python
from snapclass import snapclass, Fresh


@snapclass("{self.slug}.yml")
class Note:
    slug: str
    title: str
    tags: list[str] = Fresh.List


Note("first", "Hello").snapshot.save()
```

`first.yml`:

```yaml
title: Hello
```

Use a separate `defaults=True` example when showing default-valued fields. Empty lists use the edit-friendly YAML shape when `minimal_diffs=True` is effective:

```python
from snapclass import snapclass, Fresh


@snapclass("{self.slug}.yml", defaults=True)
class Note:
    slug: str
    title: str
    tags: list[str] = Fresh.List


Note("first", "Hello").snapshot.save()
```

`first.yml`:

```yaml
title: Hello
tags:
  -
```

Nested dataclasses should serialize as nested mappings:

```python
from snapclass import snapclass


@snapclass
class Style:
    voice: str
    temperature: float


@snapclass("{self.slug}.yml")
class Article:
    slug: str
    title: str
    style: Style


Article("dusk-court", "Dusk Court", Style("warm", 0.4)).snapshot.save()
```

`dusk-court.yml`:

```yaml
title: Dusk Court
style:
  voice: warm
  temperature: 0.4
```

Lists, sets, and dictionaries should produce ordinary readable YAML. Sets and frozensets serialize in deterministic order:

```python
from snapclass import snapclass, Fresh


@snapclass("{self.name}.yml")
class Run:
    name: str
    metrics: dict[str, float] = Fresh.Dict
    tags: list[str] = Fresh.List


Run("baseline", {"accuracy": 0.98, "loss": 0.04}, ["eval"]).snapshot.save()
```

`baseline.yml`:

```yaml
metrics:
  accuracy: 0.98
  loss: 0.04
tags:
  - eval
```

Optional values load naturally from blank YAML keys and save as YAML nulls semantically:

```yaml
count:
label:
```

Use `YAMLFormatter.loads(...)` in tests when the exact null spelling is less important than the loaded data.

YAML preservation matters. Existing comments, quote style, block scalars, and folded scalars should survive saves where possible:

```yaml
# Header
body: |
  line one
  line two
label: 'quoted'
```

After updating `body` and `label`, snapclass should keep the block and quote style:

```yaml
# Header
body: |
  line three
  line four
label: 'updated'
```

Sidecars keep long text or bytes beside the metadata file. With a pointer field, the YAML records the sidecar filename while the sidecar value stays out of the metadata:

```python
from snapclass import snapclass, sidecar


@snapclass("{self.slug}/article.yml")
class Article:
    slug: str
    title: str
    body_file: str = ""
    body: str = sidecar.text(field="body_file", default="{self.slug}.md")


Article("dusk-court", "Dusk Court", body="# Dusk Court\n").snapshot.save()
```

`dusk-court/article.yml`:

```yaml
title: Dusk Court
body_file: dusk-court.md
```

`dusk-court/dusk-court.md`:

```md
# Dusk Court
```

Other built-in formats should still omit path fields and default values:

`.json`:

```json
{
  "value": 42
}
```

`.json5` can read comments and trailing commas:

```js
{
  // authored config
  value: 42,
}
```

`.toml`:

```toml
value = 42
```

Raw `.txt` uses the one serialized field as the whole file:

```text
hello
```

`TypedTextFormatter` is for plunkylib-style typed sections:

```text
prompt|str
Line one
Line two
#-=-=-=-=-DO-NOT-EDIT-THIS-LINE-PLEASE-=-=-=-=-#
count|int
3
#-=-=-=-=-DO-NOT-EDIT-THIS-LINE-PLEASE-=-=-=-=-#
enabled|bool
False
#-=-=-=-=-DO-NOT-EDIT-THIS-LINE-PLEASE-=-=-=-=-#
```

## Snapshot API

Persisted instances expose `.snapshot`; the class exposes `.snapshots`.

Use snapshot properties to inspect and manipulate the mapped file:

```python
note.snapshot.path
note.snapshot.relpath
note.snapshot.exists
note.snapshot.modified
note.snapshot.stash
note.snapshot.fields
note.snapshot.data
note.snapshot.text
```

Use `.snapshot.save()` and `.snapshot.load()` for explicit synchronization:

```python
note.snapshot.save()
note.snapshot.load()
note.save()
note.load()
```

`snapshot.data` is the serialized mapping before file formatting. `snapshot.text` is the formatted file text for the current pattern or formatter. Setting `snapshot.text` writes the file directly, reloads the object, and still honors conflict policy.

Patternless `Model` or `create_model(...)` objects can use `.snapshot.data` and `.snapshot.text` as projections, but saving requires a pattern.

`snapshot.modified` tracks external file changes. Automatic models reload before saving so human edits are folded in where possible; use `conflict="raise"` when stale writes should fail.

## Stashes

A `Stash` describes where persisted files live and which local policies apply there.

```python
from snapclass import snapclass, Stash


runs = Stash("./runs", env="RUNS_DIR")


@snapclass("{self.name}.yml", stash=runs)
class Run:
    name: str
```

Use child stashes for organized file trees:

```python
app = Stash("./myapp", env="MYAPP_DATA")
prompts = app / "prompts"
articles = app / "articles"
```

When composing child stashes, parent policy is inherited and child entries win:

```python
root = Stash(
    "./myapp",
    env="MYAPP_DATA",
    minimal_diffs=True,
    write_delay=0.0,
)

diagnostics = root / Stash(
    "diagnostics",
    minimal_diffs=False,
)
```

Use placeholders in stashes when a whole subtree is parameterized:

```python
root = Stash("./stories") / "{world}"
storybook = root.bind(world="storybook")
```

Stash placeholder values are path-safe. Traversal, absolute paths, separators, reserved names, and unsafe punctuation should be rejected.

Use `.describe()` when debugging a stash:

```python
info = runs.describe()
```

Use `.refresh()` when an environment override may have changed after the stash was created.

## Stash-Owned Policy

Persistence policy should be local to the stash when it affects serialized bytes or path-specific filesystem behavior.

Good:

```python
root = Stash(
    "./chats",
    env="CHAT_DIR",
    formatters={".yml": ChatYamlFormatter},
    serializers={Money: MoneySerializer, "LedgerMoney": MoneySerializer},
    minimal_diffs=True,
    write_delay=0.0,
)
```

Good immutable add-ons:

```python
root = root.with_formatter(".md", MarkdownFormatter)
root = root.with_formatters({".toml": AppTomlFormatter})
root = root.with_serializer(Token, TokenSerializer)
root = root.with_serializers({"Token": TokenSerializer})
root = root.with_options(minimal_diffs=False, write_delay=0.125)
```

Avoid public process-global formatter or serializer registration. `formatters.register()` and `serializers.register()` are not part of the public snapclass style. Defaults may exist internally as protected implementation details, while applications and libraries should set policy on stashes.

Lookup order should stay:

- Formatter: explicit model `formatter=` first, effective stash formatter second, protected built-in formatter third.
- Serializer: explicit `fields` first, annotation serializer class second, effective stash serializer third, protected built-in serializer fourth.
- `minimal_diffs`: model option first, effective stash option second, `sessions.MINIMAL_DIFFS` third.
- `write_delay`: model option first, effective stash option second, `sessions.WRITE_DELAY` third.

Keep `HOOKS_ENABLED` and `HIDDEN_TRACEBACK` process-level. They affect runtime and test mechanics rather than file-tree representation.

## Fresh Defaults

Use `Fresh` to avoid boilerplate for mutable defaults.

```python
from snapclass import snapclass, Fresh


@snapclass("{self.name}.yml")
class Run:
    name: str
    metrics: dict[str, float] = Fresh.Dict
    tags: list[str] = Fresh.List
```

Supported common factories:

```python
from collections import Counter, defaultdict, deque

items: list[str] = Fresh.List
counts: dict[str, int] = Fresh.Dict
seen: set[str] = Fresh.Set
queue: deque[str] = Fresh.Deque
counter: Counter[str] = Fresh.Counter
groups: defaultdict[str, list[str]] = Fresh.DefaultDict(list)
```

Use `Fresh.DefaultDict(list)` when a `defaultdict` should create a fresh list for missing keys:

```python
from collections import defaultdict
from snapclass import snapclass, Fresh


@snapclass("{self.name}.yml")
class Index:
    name: str
    groups: defaultdict[str, list[str]] = Fresh.DefaultDict(list)
```

Use `Fresh.copy(template)` when each instance should get a deep copy of an object:

```python
DEFAULT_STYLE = {"voice": "warm", "temperature": 0.4}


@snapclass("{self.slug}.yml")
class Prompt:
    slug: str
    style: dict[str, object] = Fresh.copy(DEFAULT_STYLE)
```

Use `Fresh(factory)` for a custom factory:

```python
def new_trace_id() -> str:
    return "trace-001"


@snapclass("{self.name}.yml")
class Run:
    name: str
    trace_id: str = Fresh(new_trace_id)
```

For optional fields, keep ordinary defaults when the value starts absent:

```python
subtitle: str | None = None
```

Use `Fresh` when the default value is a container or generated object that must be new per instance.

## Sidecars

Use sidecars for values that should live beside the metadata file rather than inside it. Current public helpers are `sidecar.text(...)` and `sidecar.bytes(...)`.

```python
from snapclass import snapclass, sidecar


@snapclass("{self.slug}/article.yml")
class Article:
    slug: str
    title: str
    body: str = sidecar.text("{self.slug}.md")
```

Sidecar attributes behave like their annotated value type:

```python
article = Article("dusk-court", "Dusk Court", body="# Dusk Court\n")

assert isinstance(article.body, str)
assert article.body.startswith("#")
assert article.body.snapshot.path.name == "dusk-court.md"
```

Assignments write the sidecar value:

```python
article.body = "# Updated\n"
```

Missing ordinary access returns an empty value:

```python
assert article.body == ""
```

Strict missing-file behavior stays on the sidecar snapshot:

```python
article.body.snapshot.read()
```

Use `sidecar.bytes(...)` for binary payloads:

```python
@snapclass("{self.name}/asset.yml")
class Asset:
    name: str
    payload: bytes = sidecar.bytes("{self.name}.bin")
```

Sidecars can use pointer fields when the serialized metadata should record the sidecar filename:

```python
@snapclass("{self.slug}/article.yml")
class Article:
    slug: str
    title: str
    body_file: str = ""
    body: str = sidecar.text(field="body_file", default="{self.slug}.md")
```

Sidecars can use their own stash. Without an explicit sidecar stash, the sidecar defaults to the parent model stash and lives next to the model metadata.

```python
app = Stash("./myapp", env="MYAPP_DATA")
assets = app / "assets"


@snapclass("{self.slug}/article.yml", stash=app / "articles")
class Article:
    slug: str
    body: str = sidecar.text("{self.slug}.md", stash=assets)
```

Relative sidecar stashes should compose under the parent stash:

```python
body: str = sidecar.text("{self.slug}.md", stash="body")
```

Sidecar fields should be excluded from dataclass fields, YAML, repr, and equality. They are value-style attributes with a `.snapshot` escape hatch for file operations.

`sidecar.markdown(...)` is not public. Markdown is a normal use of `sidecar.text(...)`.

## Nested Dataclasses

Use bare `@snapclass` instead of importing `dataclass` for nested values in snapclass examples:

```python
from snapclass import snapclass, Stash, sidecar


@snapclass
class Style:
    voice: str
    temperature: float


articles = Stash("./myapp", env="MYAPP_DATA") / "articles"


@snapclass("{self.slug}/article.yml", stash=articles)
class Article:
    slug: str
    title: str
    style: Style
    body: str = sidecar.text("{self.slug}.md")
```

This keeps one import family and reinforces that snapclass is dataclass-first.

## Formatters

Formatters convert whole files to and from dictionaries.

Built-in extensions:

- `""`, `.yml`, `.yaml`: YAML
- `.json`: JSON
- `.json5`: JSON5
- `.toml`: TOML
- `.txt`: text for one-field models

Use stash-local formatter policy:

```python
class PipeFormatter(formatters.FileFormatter):
    @classmethod
    def loads(cls, text: str) -> dict[str, object]:
        key, value = text.split("|", 1)
        return {key: value}

    @classmethod
    def dumps(cls, data: dict[str, object]) -> str:
        key, value = next(iter(data.items()))
        return f"{key}|{value}"


stash = Stash("./records", formatters={".pipe": PipeFormatter})
```

Use `formatter=` on a model when only that model needs a special formatter:

```python
@snapclass("{self.name}.pipe", formatter=PipeFormatter)
class Record:
    name: str
```

Keep direct one-off formatting available through `formatters.serialize(...)` and `formatters.deserialize(..., formatter=...)`.

## Serializers

Serializers convert individual values.

Use built-ins and inferred serializers for normal Python types:

- `bool`, `int`, `float`, `str`
- optional builtins like `int | None`
- `list[T]`, `set[T]`, `frozenset[T]`, `dict[K, V]`, and `Mapping[K, V]`
- nested dataclasses and lists of nested dataclasses
- enums by value
- dates, datetimes, and paths
- `TypedDict` as a plain dictionary with a warning
- custom serializer annotations
- generic custom serializers with specialized `SERIALIZERS`

Use explicit field serializers when one model field needs special treatment:

```python
@snapclass(
    "{self.name}.yml",
    fields={"messages": MessagesSerializer},
)
class Chat:
    name: str
    messages: list[Message]
```

Use stash-local serializers when a library or file tree has its own value policy:

```python
ledger = Stash(
    "./ledger",
    serializers={Money: MoneySerializer, "LedgerMoney": MoneySerializer},
)
```

Serializer lookup should avoid ambient defaults. Imported serializer classes should never become process-wide policy just because they exist.

For custom serializer annotations, inherit from `serializers.Serializer` and implement both directions:

```python
from snapclass import snapclass, serializers


class RoundedFloat(serializers.Float):
    @classmethod
    def to_preserialization_data(cls, value, **kwargs):
        return round(super().to_preserialization_data(value, **kwargs), 2)


@snapclass("{self.name}.yml")
class Result:
    name: str
    total: RoundedFloat = 0.0
```

For third-party or app-owned types, prefer stash-local serializers:

```python
ledger = Stash("./ledger", serializers={Money: MoneySerializer})
```

For generic serializers, use the generated `SERIALIZERS` list inside conversion methods so type arguments drive nested conversion.

## Unknown Data And Migrations

Default unknown behavior is forgiving:

```python
@snapclass("{self.name}.yml", unknown="ignore")
class Config:
    name: str
```

Use `unknown="reject"` when strict schemas matter.

Use `unknown="preserve"` when human-edited or forward-compatible files should keep extra keys on round-trip.

Use `extras_field=` when extra values should be visible on the object:

```python
@snapclass("{self.name}.yml", unknown="collect", extras_field="extras")
class Config:
    name: str
    extras: dict[str, object] = Fresh.Dict
```

Use `migrate=` to reshape loaded data before unknown policy and coercion:

```python
def migrate_config(data: dict[str, object]) -> dict[str, object]:
    if "title" in data and "name" not in data:
        data["name"] = data.pop("title")
    return data


@snapclass("{self.name}.yml", migrate=migrate_config)
class Config:
    name: str
```

Migration hooks may accept path-aware signatures where the implementation supports them.

## Collections

Each snapclass gets a collection at `Class.snapshots`.

```python
same_note = Note.snapshots.get("first")
maybe_note = Note.snapshots.get_or_none("first")
note = Note.snapshots.get_or_create("first", title="Hello")
all_notes = Note.snapshots.all()
```

`get(...)` loads an existing file or raises. `get_or_none(...)` returns `None` for a missing file. `get_or_create(...)` loads when present and writes a new file when absent.

Use collection stash binding when reading or writing from a different stash:

```python
test_notes = Note.snapshots(Stash("./tmp-notes"))
note = test_notes.get_or_create("first", title="Hello")
```

Use `.filter(...)` for small file-backed queries:

```python
runs = Run.snapshots.filter(params__model="gpt-5")
```

Filters can use nested lookup names with double underscores. `all(...)` and `filter(...)` accept `_exclude="prefix"` to skip matching path keys.

Path collection behavior should stay deterministic:

- Placeholder values come from path segments.
- Repeated placeholders must match.
- `*` supports recursive path segments.
- Results should sort predictably.
- Placeholder defaults may come from dataclass defaults where supported.

## Autosave And Hooks

By default, snapclass hooks can save changed instances automatically according to the active session behavior. Use explicit `.snapshot.save()` in examples because it teaches the persistence boundary clearly.

Use `manual=True` for models where mutation should stay in memory until saved.

Use `frozen(...)` or `hooks.disabled(...)` to temporarily suspend automatic saves:

```python
from snapclass import frozen


with frozen(note):
    note.title = "Draft"
    note.body = "Still editing"
```

`hooks.disabled` and `frozen` are aliases.

Use `auto(...)` for quick inferred settings files:

```python
from snapclass import auto


settings = auto("settings.yml")
settings.theme = "readable"
```

`auto(...)` infers fields from the existing file, exposes nested dict keys as attributes, and saves new assignments back to the same file.

Use `conflict="raise"` when stale object writes should fail instead of overwriting external changes:

```python
@snapclass("{self.name}.yml", conflict="raise")
class Config:
    name: str
```

## `Model`, `Config`, And `Meta`

Decorator style is preferred in docs, but `Model` exists for inheritance-oriented code.

```python
from snapclass import Model, Stash


class Prompt(Model):
    name: str
    text: str

    class Meta:
        snapshot_pattern = "{self.name}.yml"
        snapshot_stash = Stash("./prompts")
        snapshot_manual = True
        snapshot_defaults = False
        snapshot_infer = False
        snapshot_fields = None
        snapshot_formatter = None
        snapshot_minimal_diffs = None
        snapshot_write_delay = None
        snapshot_unknown = "ignore"
        snapshot_extras_field = None
        snapshot_migrate = None
        snapshot_conflict = "overwrite"
```

Use `Config` only when tests or internals need direct config objects.

Patternless models can expose snapshot data/text projections where supported, but they cannot save without a path pattern.

Use `create_model(...)` to patch an existing dataclass class:

```python
from dataclasses import dataclass
from snapclass import create_model, Stash


@dataclass
class Prompt:
    name: str
    text: str = ""


create_model(Prompt, pattern="{self.name}.yml", stash=Stash("./prompts"))
```

Use `sync(...)` to attach persistence to one existing dataclass instance:

```python
from dataclasses import dataclass
from snapclass import sync


@dataclass
class Prompt:
    name: str
    text: str = ""


prompt = Prompt("popsicle", "hello")
sync(prompt, "{self.name}.yml")
```

## Human-Editable Files

Prefer examples that produce files someone would like to commit:

```yaml
title: Dusk Court
style:
  voice: warm
  temperature: 0.4
```

Use `minimal_diffs=True` when preserving edit-friendly YAML style matters. Use `minimal_diffs=False` when semantic compact output matters, such as writing `[]` for empty lists.

Use `defaults=True` when explicit defaults help the human reader or downstream tool.

Use `unknown="preserve"` when snapclass should coexist with hand-authored keys.

Use sidecars for long text and bytes so metadata stays readable.

## Documentation Voice

README and docs should sound direct, warm, and practical.

Good phrases:

- "small persistence layer"
- "built around Python dataclasses"
- "readable YAML, JSON, TOML, or text"
- "the stuff that belongs in a repo or project folder"
- "little durable objects with names"
- "ORM is a Snap!"

Example tone:

```md
`snapclass` is a small persistence layer built around Python dataclasses.
Decorate a dataclass, give it a path pattern, and its instances can save and
load themselves as readable YAML, JSON, TOML, or text.
```

When comparing with `datafiles`, be respectful and specific. The project owes a lot to `datafiles`; the snapclass distinction is local persistence policy through stashes, sidecars, Fresh defaults, and a few extra convenience features.

Avoid example paths named `./datafiles`. Use `./runs`, `./myapp`, `./prompts`, `./articles`, or test temp paths.

Keep first examples very simple. Introduce `Stash`, `Fresh`, sidecars, serializers, and formatters only as soon as they help the example.

## Testing Style

Tests should be behavior-first and file-aware.

Use temp directories and inspect actual persisted files when bytes matter:

```python
def test_two_stashes_can_use_different_yaml_formatters(tmp_path):
    ...
```

Prefer targeted tests around the public story:

- Model save/load round-trips.
- Stash inheritance and child overrides.
- Independent stash policy for two libraries in one process.
- Explicit model `formatter=` and `fields=` beating stash policy.
- Sidecar constructor values, assignment, reload, `.snapshot`, pointer fields, stash inheritance, and unsafe paths.
- `Fresh` defaults creating independent containers.
- Serializer behavior for downstream-inspired cases.
- Collection stash binding.
- Unknown data, migration, defaults, minimal diffs, write delay, and conflict behavior.

For sidecars, assert both value behavior and snapshot behavior:

```python
assert isinstance(article.body, str)
assert article.body == "# Hello\n"
assert article.body.snapshot.exists()
```

For Fresh, assert separate instances do not share mutable state:

```python
first = Run("first")
second = Run("second")
first.metrics["accuracy"] = 0.98
assert second.metrics == {}
```

For stash policy, include tests that would fail if a process-global registry leaked across libraries.

Run focused tests first, then the full suite:

```powershell
python.exe -m pytest tests/test_fresh.py tests/test_sidecar.py
python.exe -m pytest
```

Follow the local repository guidance for ErrorHelp when a command fails.

## Implementation Habits

When adding features:

1. Start from the public story in a small test.
2. Prefer existing snapclass surfaces over new concepts.
3. Keep behavior scoped to a stash, model, collection, or sidecar when possible.
4. Preserve dataclass semantics unless persistence explicitly requires a hook.
5. Keep repr, equality, and dataclass fields unsurprising.
6. Update README examples only after the API feels stable.

Use `apply_patch` for manual file edits in this repo.

Keep code comments rare and useful. A small comment is good before tricky descriptor or dataclass setup code; comments that narrate assignments add noise.

## Sharp Edges To Remember

- Bare `@snapclass` and `@snapclass()` are dataclass-only forms and should not attach `.snapshot`.
- Sidecar values should act like `str` or `bytes`, with `.snapshot` for file details.
- Sidecars default to the model's stash; explicit sidecar stashes can override or compose under it.
- Sidecar fields should stay out of YAML and `dataclasses.fields(...)`.
- `Fresh.List`, `Fresh.Dict`, and friends must produce fresh field objects and fresh values.
- `defaults=True` controls whether default-valued fields are written.
- `field(default_factory=dict)` is the standard dataclass spell; `Fresh.Dict` is the snapclass-friendly shorthand.
- Process-global formatter and serializer registration is the problem snapclass is avoiding.
- `minimal_diffs` and `write_delay` belong on stashes because they affect file bytes or filesystem behavior.
- `HOOKS_ENABLED` and `HIDDEN_TRACEBACK` stay process-level.
- Imported formatter or serializer classes should not become ambient defaults.
- Environment-backed stashes may need `.refresh()` if the environment changes during a process.

## Quick Review Checklist

Before calling snapclass work done, ask:

- Does the code still read like normal dataclass code?
- Is persistence policy local to the model or stash that uses it?
- Would two independent libraries in one process avoid clobbering each other?
- Are serialized files readable and stable?
- Are mutable defaults fresh per instance?
- Do sidecars behave like values in ordinary use?
- Do tests cover the file bytes or paths that matter?
