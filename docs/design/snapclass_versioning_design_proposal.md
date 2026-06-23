# Snapclass Object Versioning Requirements

## 1. Purpose

Add optional versioning for persisted snapclass objects. A version is a frozen checkpoint of the files that make up one object snapshot: the main persisted file plus any sidecar files. The feature should feel like normal snapclass code: ordinary dataclasses, readable files, explicit persistence boundaries, and no process-global policy.

This release should implement file-backed version history only. Git, remote sync, patch-chain storage, database-backed history, and automatic versioning on every save are out of scope.

## 2. Design Decision Summary

Versioning control belongs on the snapclass model, not on `Stash`.

`Stash` should continue to mean "where files live" and "what local file policy applies." A `Stash` may still be used as the destination for version history because it already supports path composition and environment-variable overrides. It should not gain `versioning=` or `versioned=` public options in this release.

Recommended public API:

```python
from snapclass import snapclass, Stash

notes = Stash("./notes", env="NOTES_DIR")
history = Stash("./history", env="HISTORY_DIR")


@snapclass("{self.slug}.yml", stash=notes, versions=history / "notes")
class Note:
    slug: str
    title: str
    body: str = ""
```

Supported values:

```python
@snapclass("{self.slug}.yml", versions=False)       # default, disabled
@snapclass("{self.slug}.yml", versions=True)        # enabled, local history
@snapclass("{self.slug}.yml", versions=Stash(...))  # enabled, explicit history root
```

The guiding rule:

> The snapclass controls whether objects are versioned. The object stash controls the live object files. The history stash controls where version files are written.

## 3. Goals

- Keep the default path simple for scripts, notebooks, and small projects.
- Preserve snapclass's dataclass-first feel.
- Store history in readable files that can be inspected and committed.
- Capture the full object snapshot: main file plus sidecars.
- Allow explicit history locations through `Stash`, including env-backed stashes.
- Avoid recursive or confusing behavior when the history location is itself a stash.
- Preserve existing save, load, formatter, serializer, conflict, migration, unknown-data, sidecar, and stash behavior.
- Keep the first implementation dependency-light and file-backed.

## 4. Non-Goals for This Release

Do not implement these in the first version:

- Git-backed history.
- Remote history sync.
- Stash-level versioning policy.
- Process-global versioning defaults.
- Patch-chain storage as the primary storage format.
- Automatic checkpointing on every save by default.
- A generalized public backend plugin API unless it naturally falls out of the implementation.
- Versioning of internal history metadata as snapclass objects.

## 5. Public API Requirements

### 5.1 Decorator Option

Add a `versions` keyword to `snapclass(...)`.

```python
@snapclass(pattern, *, stash=None, versions=False, ...)
class Model:
    ...
```

Accepted values:

| Value | Meaning |
| --- | --- |
| `False` or `None` | Versioning disabled. This is the default. |
| `True` | Versioning enabled. Store history under the model stash at `.snapclass/history`. |
| `Stash(...)` | Versioning enabled. Store history under that stash. |

Do not accept raw strings or paths for `versions` in the first release. Users should write `versions=Stash("./history")` or `versions=history / "notes"` so env override behavior remains explicit.

### 5.2 Other Model Construction Surfaces

The same setting should be available through all public model-construction paths:

```python
create_model(Note, pattern="{self.slug}.yml", stash=notes, versions=True)
sync(note, "{self.slug}.yml", stash=notes, versions=history / "notes")
```

For `Model` inheritance style, use `snapshot_versions` in `Meta`:

```python
from snapclass import Model, Stash

history = Stash("./history", env="NOTES_HISTORY_DIR")


class Note(Model):
    slug: str
    title: str

    class Meta:
        snapshot_pattern = "{self.slug}.yml"
        snapshot_stash = Stash("./notes", env="NOTES_DIR")
        snapshot_versions = history / "notes"
```

### 5.3 Snapshot API

Persisted instances should expose versioning through `.snapshot`.

```python
note.snapshot.checkpoint("first draft")
note.snapshot.versions.all()
note.snapshot.versions.latest()
note.snapshot.versions.get("draft")
note.snapshot.versions.diff("draft", "latest")
note.snapshot.versions.restore("draft")
```

Required method signatures:

```python
snapshot.checkpoint(
    message: str = "",
    *,
    tag: str | None = None,
    save: bool = True,
    replace_tag: bool = False,
) -> Version

snapshot.versions.enabled -> bool
snapshot.versions.all() -> list[Version]
snapshot.versions.latest() -> Version | None
snapshot.versions.get(id_or_tag: str) -> Version
snapshot.versions.diff(a: VersionRef, b: VersionRef = "current", *, context: int = 3) -> str
snapshot.versions.restore(id_or_tag: str, *, save_current: bool = True, reload: bool = True) -> Version | None
```

`VersionRef` should accept:

- a version id,
- a tag,
- a `Version` object,
- the string `"latest"`,
- the string `"current"` for the live on-disk object state.

`restore(...)` should return the safety checkpoint when `save_current=True`, otherwise `None`.

### 5.4 Disabled Versioning Behavior

`snapshot.versions` should exist even when versioning is disabled.

```python
note.snapshot.versions.enabled  # False
```

Calling version-mutating or version-reading methods when disabled should raise a clear `VersioningDisabledError`.

```python
note.snapshot.checkpoint("draft")  # raises VersioningDisabledError
```

## 6. Version Object Requirements

Expose a small immutable `Version` object.

Required fields:

```python
@dataclass(frozen=True)
class Version:
    id: str
    created: datetime
    message: str
    tag: str | None
    model: str
    object_relpath: str
    history_path: Path
    files: tuple[VersionFile, ...]
```

Required `VersionFile` fields:

```python
@dataclass(frozen=True)
class VersionFile:
    role: str              # "main" or "sidecar"
    relpath: str           # path relative to the file's owning stash
    archive_path: str      # path inside the version folder
    field: str | None = None
    exists: bool = True
    binary: bool = False
```

Nice-to-have after the MVP:

```python
version.text()       # text of the main file
version.data()       # deserialized data for the main file
version.load()       # construct an object from this version without restoring live files
```

The MVP may ship without `Version.load()` as long as `restore()` and `diff()` are present.

## 7. Storage Location Rules

### 7.1 Default Local History

For `versions=True`, store history under the model object's stash:

```text
<model-stash-root>/
  .snapclass/
    history/
      ...
```

Example:

```python
notes = Stash("./notes", env="NOTES_DIR")


@snapclass("{self.slug}.yml", stash=notes, versions=True)
class Note:
    slug: str
    title: str
```

Live object:

```text
notes/
  first.yml
```

History:

```text
notes/
  .snapclass/
    history/
      first.yml/
        index.yml
        versions/
          2026-06-23_18-42-11Z_first-draft/
            version.yml
            snapshot/
              model/
                first.yml
```

### 7.2 Explicit History Stash

For `versions=Stash(...)`, store history under that stash exactly.

```python
notes = Stash("./notes", env="NOTES_DIR")
history = Stash("./history", env="NOTES_HISTORY_DIR")


@snapclass("{self.slug}.yml", stash=notes, versions=history / "notes")
class Note:
    slug: str
    title: str
```

Live object:

```text
notes/
  first.yml
```

History:

```text
history/
  notes/
    first.yml/
      index.yml
      versions/
        2026-06-23_18-42-11Z_first-draft/
          version.yml
          snapshot/
            model/
              first.yml
```

### 7.3 Object History Key

Inside the history root, identify an object by the live main file path relative to the model stash.

```text
object relpath: first.yml
history key:    first.yml/
```

For nested paths:

```text
object relpath: articles/dusk-court/article.yml
history key:    articles/dusk-court/article.yml/
```

When users want a shared external history root, they should namespace with child stashes:

```python
history = Stash("./history", env="APP_HISTORY_DIR")

@snapclass("{self.slug}.yml", stash=notes, versions=history / "notes")
class Note: ...

@snapclass("{self.name}.yml", stash=prompts, versions=history / "prompts")
class Prompt: ...
```

Do not add a separate `namespace=` option in the first release. Child stashes already solve this.

## 8. Disk Layout Requirements

Use a readable chronological layout. Avoid object-store-style hash mazes.

Required shape:

```text
<history-root>/
  <object-relpath-as-directory>/
    index.yml
    versions/
      <version-id>/
        version.yml
        snapshot/
          model/
            <model-relpath>
          sidecars/
            <field-name>/
              <sidecar-relpath>
```

For objects with no sidecars, the `sidecars/` directory may be absent.

Example with a sidecar:

```python
from snapclass import snapclass, Stash, sidecar

articles = Stash("./articles", env="ARTICLES_DIR")
history = Stash("./history", env="ARTICLE_HISTORY_DIR")


@snapclass("{self.slug}/article.yml", stash=articles, versions=history / "articles")
class Article:
    slug: str
    title: str
    body: str = sidecar.text("{self.slug}.md")
```

Live files:

```text
articles/
  dusk-court/
    article.yml
    dusk-court.md
```

History:

```text
history/
  articles/
    dusk-court/
      article.yml/
        index.yml
        versions/
          2026-06-23_19-10-00Z_review-copy/
            version.yml
            snapshot/
              model/
                dusk-court/
                  article.yml
              sidecars/
                body/
                  dusk-court.md
```

### 8.1 `index.yml`

`index.yml` should make listing fast and human-readable.

Example:

```yaml
schema: snapclass.history.v1
object: first.yml
model: notes.Note
latest: 2026-06-23_18-50-02Z_better-title
tags:
  draft: 2026-06-23_18-42-11Z_first-draft
  approved: 2026-06-23_18-50-02Z_better-title
versions:
  - id: 2026-06-23_18-42-11Z_first-draft
    created: 2026-06-23T18:42:11Z
    message: first draft
    tag: draft
  - id: 2026-06-23_18-50-02Z_better-title
    created: 2026-06-23T18:50:02Z
    message: better title
    tag: approved
```

### 8.2 `version.yml`

`version.yml` should describe the version and map archived files back to live files.

Example:

```yaml
schema: snapclass.version.v1
id: 2026-06-23_18-42-11Z_first-draft
created: 2026-06-23T18:42:11Z
message: first draft
tag: draft
model: notes.Note
object: first.yml
files:
  - role: main
    relpath: first.yml
    archive_path: snapshot/model/first.yml
    exists: true
    binary: false
```

Sidecar example:

```yaml
schema: snapclass.version.v1
id: 2026-06-23_19-10-00Z_review-copy
created: 2026-06-23T19:10:00Z
message: review copy
tag: review
model: articles.Article
object: dusk-court/article.yml
files:
  - role: main
    relpath: dusk-court/article.yml
    archive_path: snapshot/model/dusk-court/article.yml
    exists: true
    binary: false
  - role: sidecar
    field: body
    relpath: dusk-court.md
    archive_path: snapshot/sidecars/body/dusk-court.md
    exists: true
    binary: false
```

Metadata should be written with an internal YAML writer and should not be affected by model formatters, model serializers, or stash-local formatters.

## 9. Version ID and Tag Requirements

### 9.1 Version IDs

Version IDs should be stable, sortable, and filesystem-safe.

Recommended format:

```text
YYYY-MM-DD_HH-MM-SSZ[_message-slug][-N]
```

Examples:

```text
2026-06-23_18-42-11Z_first-draft
2026-06-23_18-42-11Z_first-draft-2
2026-06-23_18-42-11Z
```

Rules:

- Use UTC timestamps.
- Use only path-safe characters.
- Slugify message text when included in the folder name.
- If the generated id already exists, append a small numeric suffix.
- The full unslugged message must live in `version.yml`.

### 9.2 Tags

Tags are optional friendly names for versions.

```python
note.snapshot.checkpoint("ready for review", tag="review")
note.snapshot.versions.restore("review")
```

Rules:

- Tags are scoped to one object history.
- Tags must be path-safe identifiers.
- Creating a checkpoint with an existing tag should raise `VersionTagExistsError` unless `replace_tag=True`.
- Tags must not collide with existing version ids.
- `get(id_or_tag)` should prefer exact version id matches and then tag matches.

## 10. Checkpoint Semantics

### 10.1 Default Behavior

```python
note.snapshot.checkpoint("first draft")
```

Default behavior is equivalent to:

```python
note.snapshot.checkpoint("first draft", save=True)
```

Required sequence:

1. Validate that versioning is enabled.
2. If `save=True`, save the live object using existing snapshot save behavior.
3. After saving, read exact bytes from disk for the main file and sidecar files.
4. Write a new version folder under history.
5. Update `index.yml` atomically.
6. Return the new `Version` object.

This matters because snapclass should preserve comments, quote style, block scalars, unknown keys, and human edits whenever the formatter supports preservation.

### 10.2 `save=False`

```python
note.snapshot.checkpoint("disk state", save=False)
```

This records the current on-disk files without saving in-memory changes.

If the main file does not exist, raise `VersionSourceMissingError`.

### 10.3 Sidecar File Presence

The checkpoint should record all files known to the snapshot file set:

- main model file,
- text sidecars,
- bytes sidecars.

For sidecars that are part of the model but do not currently exist, record an entry with `exists: false` and do not create an archived file. This lets restore reproduce the old state by deleting currently existing managed sidecars that were absent in the target version.

## 11. Restore Semantics

```python
note.snapshot.versions.restore("draft")
```

Default behavior is equivalent to:

```python
note.snapshot.versions.restore("draft", save_current=True, reload=True)
```

Required sequence:

1. Resolve the target version id or tag.
2. If `save_current=True`, create a safety checkpoint of the current live state with a generated message such as `before restore <target-id>`.
3. If the safety checkpoint fails, abort the restore.
4. Restore the main file and sidecar files from the target version.
5. Delete managed sidecar files recorded as `exists: false` in the target version.
6. If `reload=True`, reload the object from restored files.
7. Return the safety checkpoint if one was created.

Restore is an explicit overwrite operation. It should not silently skip changed files. The safety checkpoint is the default protection against accidental data loss.

## 12. Diff Semantics

Required MVP:

```python
note.snapshot.versions.diff("draft", "latest")
note.snapshot.versions.diff("latest", "current")
```

Return a unified text diff string.

Requirements:

- Use line-oriented diffs for text files.
- Include per-file headers when multiple files are present.
- Support diffing version-to-version and version-to-current.
- For binary files, show a concise binary-change summary instead of raw bytes.
- Preserve file labels in the output so the diff is readable.

Example output shape:

```diff
--- draft:model:first.yml
+++ current:model:first.yml
@@ -1,2 +1,2 @@
-title: Hello
+title: Hello again
```

Structured data diffs and JSON Patch-style diffs are future work.

## 13. Sidecar Requirements

Sidecars must be first-class in versioning.

The checkpoint file set must include sidecars created with:

```python
sidecar.text(...)
sidecar.bytes(...)
```

Requirements:

- Sidecar values remain ordinary `str` or `bytes` values in normal object use.
- Versioning should use the sidecar snapshot metadata to find files.
- Sidecar files should not appear as dataclass fields or YAML fields unless they already use pointer-field behavior.
- Pointer fields should be saved as part of the main model file, just like today.
- Missing sidecars should be represented in metadata rather than forcing empty files to exist.
- Sidecar restore should overwrite or remove only managed sidecar files for that object.

If a sidecar path cannot be safely resolved to a managed stash-relative path, checkpoint should raise a clear error rather than silently omit it.

## 14. Environment Variable and Refresh Behavior

A history location supplied as a `Stash` should behave like any other stash.

```python
history = Stash("./history", env="NOTES_HISTORY_DIR")

@snapclass("{self.slug}.yml", stash=notes, versions=history / "notes")
class Note: ...
```

Requirements:

- `NOTES_HISTORY_DIR` controls where history is written.
- `NOTES_DIR` controls where live object files are written.
- Changing an environment variable after stash creation should follow existing `Stash.refresh()` behavior.
- Version operations should use the current resolved paths from the relevant stashes.
- Internal history metadata should record relpaths, not machine-specific absolute paths.

## 15. Avoiding Recursive History Behavior

Internal history files are implementation files, not snapclass model instances.

Even if a user creates a snapclass whose live object stash points at the same directory used by another class as a history stash, internal version files must not recursively version themselves.

This design should hold because only classes with `versions=True` or `versions=Stash(...)` are versioned, and the file version store writes `index.yml`, `version.yml`, and archived snapshots directly through normal filesystem I/O.

Do not implement or expose:

```python
Stash(versioning=...)
Stash(versioned=True)
```

## 16. Collision Handling

Two models can accidentally point at the same history root and same object key.

Example:

```python
history = Stash("./history")

@snapclass("{self.slug}.yml", stash=notes, versions=history)
class Note: ...

@snapclass("{self.slug}.yml", stash=prompts, versions=history)
class Prompt: ...
```

Both could write:

```text
history/first.yml/
```

Requirement:

- If an existing `index.yml` has a different `model` value for the same object key, raise `VersionHistoryCollisionError`.
- The error should recommend using child stashes, such as `versions=history / "notes"` and `versions=history / "prompts"`.
- Do not silently add hash prefixes or hidden namespaces.

## 17. Atomicity and File Safety

Requirements:

- Write a version into a temporary directory under the object history directory.
- Complete all file copies and `version.yml` before making the version visible.
- Move the completed version directory into place atomically when the filesystem supports it.
- Update `index.yml` atomically by writing a temporary file and replacing it.
- Sanitize version ids and tags.
- Reject path traversal in generated history paths.
- Preserve file bytes exactly for archived files.
- Use `pathlib.Path` throughout.
- Keep behavior portable across Windows, macOS, and Linux.

If the project already has an internal file-locking helper, use it for per-object checkpoint and restore operations. If it does not, atomic writes are the minimum MVP requirement.

## 18. Error Classes

Add specific errors so callers can handle failures.

Recommended errors:

```python
VersioningError
VersioningDisabledError
VersionSourceMissingError
VersionNotFoundError
VersionTagExistsError
VersionHistoryCollisionError
VersionRestoreError
```

These should be exported from the public package only if that matches the existing snapclass error style. Otherwise they can live under the existing errors module and be documented.

## 19. Implementation Architecture

Recommended internal pieces:

```python
class VersionConfig:
    enabled: bool
    history_stash: Stash | None  # None means local .snapclass/history

class VersionCollection:
    def __init__(self, snapshot): ...
    @property
    def enabled(self) -> bool: ...
    def all(self) -> list[Version]: ...
    def latest(self) -> Version | None: ...
    def get(self, id_or_tag: str) -> Version: ...
    def diff(self, a, b="current", *, context=3) -> str: ...
    def restore(self, id_or_tag, *, save_current=True, reload=True): ...

class FileVersionStore:
    def checkpoint(self, snapshot, message, tag, save, replace_tag) -> Version: ...
    def list(self, snapshot) -> list[Version]: ...
    def read(self, snapshot, id_or_tag) -> Version: ...
    def diff(self, snapshot, a, b, context) -> str: ...
    def restore(self, snapshot, version, save_current, reload): ...
```

The public API should not require users to import `FileVersionStore` or `VersionConfig` in the first release.

## 20. Integration Points to Review

The developer should inspect and update these areas in the codebase:

- `snapclass(...)` decorator configuration plumbing.
- `Config` or equivalent internal model config object.
- `Model.Meta` parsing.
- `create_model(...)` and `sync(...)` parameter forwarding.
- `Snapshot` class for `checkpoint(...)` and `.versions`.
- Snapshot file path and stash resolution internals.
- Sidecar snapshot internals to enumerate managed sidecar files.
- Existing atomic write helpers, if any.
- Existing formatter/serializer behavior to ensure archived bytes are copied after save.
- Public docs and README examples.
- Test helpers around temporary stashes and file-byte assertions.

## 21. Acceptance Criteria

### 21.1 Basic Versioning

Given:

```python
notes = Stash(tmp_path / "notes")

@snapclass("{self.slug}.yml", stash=notes, versions=True)
class Note:
    slug: str
    title: str
```

When:

```python
note = Note("first", "Hello")
version = note.snapshot.checkpoint("first draft", tag="draft")
```

Then:

- `notes/first.yml` exists.
- `notes/.snapclass/history/first.yml/index.yml` exists.
- `version.id` is listed in `index.yml`.
- `note.snapshot.versions.get("draft").id == version.id`.

### 21.2 Default Disabled

A class without `versions=` should behave exactly as before. Calling `checkpoint()` should raise `VersioningDisabledError`.

### 21.3 Explicit History Stash with Env Override

Given:

```python
history = Stash(tmp_path / "history", env="NOTES_HISTORY_DIR")
```

When `NOTES_HISTORY_DIR` is set and the stash is refreshed according to existing stash behavior, version files should be written under the env-selected path.

### 21.4 No Stash-Level Versioning

The following should not be public API:

```python
Stash("./notes", versioning=True)
Stash("./notes", versioned=True)
```

### 21.5 Checkpoint Saves First

When an object has unsaved in-memory changes and `checkpoint(save=True)` is called, the archived main file should reflect the saved object state.

### 21.6 `save=False` Uses Disk State

When an object has unsaved in-memory changes and `checkpoint(save=False)` is called, the archived main file should reflect the current file bytes on disk.

### 21.7 Restore Creates Safety Checkpoint

When restoring an older version with default options, a safety checkpoint should be created first. Restoring should reload the object unless `reload=False`.

### 21.8 Diff Works

Changing a text field between checkpoints should produce a unified diff that includes the changed line and per-file labels.

### 21.9 Sidecars Are Captured

A class with `sidecar.text(...)` should checkpoint and restore both the main file and the sidecar file.

### 21.10 Missing Sidecars Are Reproduced

If a sidecar was absent in a target version, restoring that version should remove the current managed sidecar file for that object.

### 21.11 Collision Detection

Two different model classes writing the same object key under the same history root should raise `VersionHistoryCollisionError` with an actionable message.

### 21.12 Internal Metadata Is Not Snapclass-Versioned

`index.yml`, `version.yml`, and archived files should be written directly by the version store. They should not trigger snapclass save hooks or recursive versioning.

## 22. Documentation Requirements

Add a README section titled something like `Object history` or `Versioned snapshots`.

First example should be compact:

```python
from snapclass import snapclass, Stash

notes = Stash("./notes")


@snapclass("{self.slug}.yml", stash=notes, versions=True)
class Note:
    slug: str
    title: str
    body: str = ""


note = Note("first", "Hello", "Line one\n")
note.snapshot.checkpoint("first draft", tag="draft")

note.title = "Hello again"
note.snapshot.checkpoint("better title")

print(note.snapshot.versions.diff("draft", "latest"))
note.snapshot.versions.restore("draft")
```

Then show explicit history location:

```python
history = Stash("./history", env="NOTES_HISTORY_DIR")

@snapclass("{self.slug}.yml", stash=notes, versions=history / "notes")
class Note:
    slug: str
    title: str
```

Docs should explain:

- `versions=True` means local hidden history beside the model stash.
- `versions=Stash(...)` means explicit env-aware history location.
- Checkpoints are explicit.
- Sidecars are included.
- Restore creates a safety checkpoint by default.
- Stashes are used for locations, not for enabling versioning behavior.

## 23. Suggested Implementation Order

1. Add `versions` config plumbing to decorator, `create_model`, `sync`, and `Model.Meta`.
2. Add `Version`, `VersionFile`, and versioning error types.
3. Add `snapshot.versions` and `snapshot.checkpoint(...)` surfaces.
4. Implement history root resolution for `versions=True` and `versions=Stash(...)`.
5. Implement file set enumeration for main file only.
6. Implement checkpoint, index writing, and version listing.
7. Implement tags and tag lookup.
8. Implement restore for main file only.
9. Implement text diff for main file only.
10. Extend file set enumeration to sidecars.
11. Extend checkpoint, restore, and diff to sidecars.
12. Add collision detection.
13. Add atomic write behavior and targeted failure tests.
14. Update README and docs.
15. Add optional `note.checkpoint(...)` alias.
16. Consider `Version.load()` after the MVP is stable.

## 24. Open Questions

These can be decided during implementation:

- Should version ids include a message slug by default, or should folder names be timestamp-only?
- Should `replace_tag=True` move an existing tag silently, or should it also record a tag-change event in `index.yml`?
- Should `restore(..., reload=False)` be documented publicly or kept as an advanced/internal option?
- Should `Version.load()` be included in the first release or deferred?
- Should very large binary sidecars have a size guard or warning?
- Should `versions=True` write a small human pointer file when history is external? Current recommendation: no pointer file unless user feedback asks for it.

## 25. Final Recommendation

Implement the first release around this minimal public story:

```python
@snapclass("{self.slug}.yml", stash=notes, versions=True)
class Note:
    slug: str
    title: str
```

and this explicit-history story:

```python
history = Stash("./history", env="NOTES_HISTORY_DIR")

@snapclass("{self.slug}.yml", stash=notes, versions=history / "notes")
class Note:
    slug: str
    title: str
```

This keeps versioning clean: the model owns behavior, stashes own locations, and the stored history remains readable project files.
