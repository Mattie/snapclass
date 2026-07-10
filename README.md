# snapclass

Human-readable file persistence for Python dataclasses, an adaptation of the cool [`datafiles` project](https://github.com/jacebrowning/datafiles).

`snapclass` is a small persistence layer built around Python dataclasses.
Decorate a dataclass, give it a path pattern, and its instances can save and
load themselves as readable YAML, JSON, TOML, TERSE, or text.

It is built for the stuff that belongs in a repo or project folder: prompts,
configs, fixtures, lightweight app state, and little durable objects with names.

You can use a `Stash` to pick a different location for the
serialized file (with env overrides) and have special format rules when you
need. You can also include a `sidecar` when you have a doc or binary you want to save next to it.

## File Type Support

snapclass picks a built-in formatter from the snapshot file extension:

- `""`, `.yml`, `.yaml`: YAML
- `.json`: JSON
- `.json5`: JSON5
- `.toml`: TOML
- `.terse`: TERSE
- `.txt`: raw text for one-field models

TERSE support follows the current draft of
[`RudsonCarvalho/terse-format`](https://github.com/RudsonCarvalho/terse-format).
TERSE itself is not finalized yet, so treat `.terse` files as useful for
experiments and token-efficient local workflows while the upstream format is
still settling.

### TERSE-Friendly Shapes

TERSE works especially well for arrays of small records with the same primitive
fields. In snapclass, a list of simple nested dataclasses serializes into a
schema array, so the field names appear once and the rows stay compact.

```python
from snapclass import Stash, snapclass


@snapclass
class ScoreRow:
    rank: int
    handle: str
    points: int
    qualified: bool


@snapclass(
    "{self.week}.terse",
    stash=Stash("./leaderboards"),
    manual=True,
    defaults=True,
)
class Leaderboard:
    week: str
    scores: list[ScoreRow]
    published: bool = False


board = Leaderboard(
    "week-32",
    scores=[
        ScoreRow(1, "mira", 982, True),
        ScoreRow(2, "jon-vale", 941, True),
        ScoreRow(3, "noor", 917, False),
    ],
    published=True,
)
board.snapshot.save()
```

`leaderboards/week-32.terse`:

```terse
scores:
  #[rank handle points qualified]
    1 mira 982 T
    2 jon-vale 941 T
    3 noor 917 F
published: T
```

See `examples/terse_schema_arrays.py` for a runnable version with leaderboard
and latency-report examples.

### Faster YAML

Install the optional native-assisted YAML path with:

```console
pip install "snapclass[yaml-fast]"
```

When the extra is available, built-in YAML snapshots automatically use a fast
loader and preserve common scalar edits by changing only the affected source
range. Literal and folded block strings use the same source-local path when
their style, indentation, and trailing-newline behavior can be retained. Flow
collections, multiline strings, comments, spacing, blank lines, line endings,
and quote style can remain untouched elsewhere in the document. These edits do
not reorder mapping keys.

Every patched document is parsed again and compared with the intended data
before it is written. New files, structural or type changes, incompatible block
string changes, anchors, aliases, tags, directives, and uncertain syntax use
the regular ruamel round-trip path automatically.

Custom model or stash formatters keep their normal precedence. JSON and TERSE
continue to serialize whole files because their formatter cost is already
small.



## ORM is a snap!

```python
from snapclass import snapclass

@snapclass("{self.name}.yml")
class Note:
    name: str
    title: str
    body: str = ""

mynote = Note(
    "first_note",
    "Today I used snapclass!",
    "It was my first day of snapclass. My note was saved to YAML for me!",
)
mynote.snapshot.save()

# Load it back later with the same name.
same_note = Note.snapshots.get("first_note")
```

```python
from snapclass import snapclass, Stash, Fresh

# Create a default location with an environment override.
runsloc = Stash("./runs", env="RUNS_DIR")

@snapclass("{self.name}.yml", stash=runsloc)
class RunData:
    name: str
    # shortcuts for common boilerplate factory code
    metrics: dict[str, float] = Fresh.Dict

RunData("baseline", {"accuracy": 0.98, "loss": 0.04}).snapshot.save()
```

```python
from snapclass import snapclass, Stash, sidecar

@snapclass
class Style:
    voice: str
    temperature: float

# Locations can be nested with stashes.
app = Stash("./myapp", env="MYAPP_DATA")
articles = app / "articles"

@snapclass("{self.slug}/article.yml", stash=articles)
class Article:
    slug: str
    title: str
    style: Style
    body: str = sidecar.text("{self.slug}.md")

article = Article(
    "dusk-court",
    "Dusk Court",
    Style("warm", 0.4),
    body="# Dusk Court\n\nBe brief, warm, and useful.\n",
)
loaded = Article.snapshots.get("dusk-court")
```

## Coordinating Shared Files

When two local processes may update the same file, wrap the short
read-modify-save section in `snapshot.locked(reload=True)`. The lock is
cooperative and local to the machine, using a `.lock` file beside the snapshot.
Snapshot filenames ending in `.lock` are reserved for these lock sidecars.

```python
from snapclass import snapclass, Stash, Fresh


@snapclass("{self.name}.yml", stash=Stash("./runs"), manual=True, require_lock=True)
class WorkflowState:
    name: str
    steps: list[str] = Fresh.List


state = WorkflowState("daily-run")

with state.snapshot.locked(reload=True):
    state.steps.append("started")
    state.save()
```

For async workflows, keep the locked block short. Do the slow work after the
save has released the file lock:

```python
with state.snapshot.locked(reload=True):
    state.steps.append("started")
    state.save()

await do_work()

with state.snapshot.locked(reload=True):
    state.steps.append("finished")
    state.save()
```

`require_lock=True` is optional, but useful for manual models where every save
should go through this pattern. It makes `state.save()` raise unless it is
called inside `state.snapshot.locked(...)`.

By default, snapclass writes snapshot files in place. This is friendlier to
active Windows app folders where another process may briefly have the file open
for reading. If a model or stash should preserve the old complete file until a
new complete file is ready, opt into same-directory temp-file replacement:

```python
safe_runs = Stash("./runs", write_strategy="atomic")
```

Use locks to coordinate cooperative writers. Use `write_strategy="atomic"` when
the file-integrity tradeoff matters more than compatibility with active readers.

## FAQ

### Why use `snapclass` over `datafiles`?

`datafiles` is great and you should absolutely use it for your app or script! I love it so much and use it in project after project. After years of use, I've run into a few limitations-- like issues when multiple modules needed different `datafiles` behavior in one process (because much of the behavior control is global). I designed `snapclass` to isolate some of that via stashes and added some extra features I liked along the way.

## License

snapclass is licensed under the MIT License. See [LICENSE](LICENSE).

Much of snapclass's core behavior, along with portions of its implementation
and test suite, is adapted from the wonderful [`datafiles`](https://github.com/jacebrowning/datafiles) project by [Jace Browning](https://github.com/jacebrowning). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
