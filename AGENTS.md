# Agent Instructions

Rules AI agents must follow when working in this repository.

---

## Commit messages

Use **Conventional Commits**.

### Header

* Format: `<type>(optional scope): summary`
* Use lowercase types (`feat`, `fix`, `ci`, `chore`, `docs`)
* Use scopes when relevant
* Write summaries in lowercase, imperative mood

### Body

* Leave a blank line after the header
* Explain **why**, not what
* Use imperative, present tense
* Wrap lines at ~72 characters

The body is optional for trivial changes.

---

## Release tags

* Use the bare version as the tag name — **no `v` prefix** (e.g. `0.1.1`, not `v0.1.1`)
* Prefer final versions over pre-releases: a resolver skips pre-releases unless
  asked, so `uvx walmart-support` cannot see an `a`/`b`/`rc` build
* Tags must be annotated (`git tag -a`) with a structured release-notes message

---

## Commits

When generating commits via a shell:

* Do **not** pass generated messages directly to `git commit -m`
* Write the commit message to a file or standard input
* Use `git commit -F <file>` or `git commit -F -`
* Disable shell expansion when writing commit messages

This avoids issues with backticks, quotes, and other shell-expanded
characters in generated commit messages.

---

## Code style

Follow existing project conventions.

* Match formatting, naming, and file structure already in use
* Do not reformat unrelated code
* Prefer small, focused changes
* Avoid introducing new patterns without clear benefit

### Language-specific rules

* Respect `.editorconfig` when present
* Do not disable lint rules without justification
* Prefer explicit, readable code over clever abstractions
* Ensure all changes pass `ruff check .`, `ruff format --check .`, `pyright`, and `pytest`

---

## `uv` Workflow Rules

* Use `uv` exclusively for dependency management instead of `pip`
* Always prefix tool and script invocations with `uv run` so they execute inside the managed environment
* Do not manually create, activate, or delete `.venv` directories
* Use `uv run python scripts/bump_version.py <new-version>` to bump the project version — do
  **not** edit `pyproject.toml` directly, and do not call `uv version` on its own: the plugin
  manifests carry their own copy of the version and a host reads those, not the package
* Always commit both `pyproject.toml` and `uv.lock` together after a version bump

---

## Attribution

Every AI-assisted commit, tag, PR, comment, reply, or message an agent writes
for someone must carry an `Assisted-by` trailer:

```
Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]
```

| Field             | Description                                               |
|-------------------|-----------------------------------------------------------|
| `AGENT_NAME`      | AI tool or framework (e.g. `Claude`, `Cursor`, `Copilot`) |
| `MODEL_VERSION`   | Specific model (e.g. `claude-opus-4-6`)                   |
| `[TOOL1] [TOOL2]` | Optional specialized analysis tools; omit everyday tools  |

* Place it at the **end**, after a blank line: a git trailer in commits, the last line of the body everywhere else.
* Skip it only for text the user dictates verbatim.
* Use only `Assisted-by` — no `Co-Authored-By`, no `Made with …`, no hand-written `Sent using …`, no other footers.

Example:

```
Assisted-by: Claude:claude-opus-4-6 coccinelle sparse
```

---

## Documentation

Each fact has one home; repeating one across files is how they drift.

* `README.md` — the front door: what it is, install, the commands, configuration
* `docs/portal-internals.md` — how the portals behave on the wire: endpoints, actions, parameter
  lists, and the traps found by getting them wrong
* `skills/walmart-support/` — what an operator should do: gates, per-case request costs, per-portal
  playbooks. This is the only one an agent loads on its own, so a rule that must change behaviour
  belongs here rather than in the README
* module docstrings — why the code is shaped the way it is

A new portal finding goes in `docs/portal-internals.md`. Link to it from the README rather than
restating it.

---

## CLI conventions

The command surface is the product. Keep it predictable.

* Group commands by the noun they act on (`cases`, `categories`, `auth`); the
  group name carries the entity, the subcommand carries the verb
* Every command MUST accept `--json` and emit machine-readable output under it,
  with the human rendering as the default
* Take prose (case bodies, replies) through a `--*-file` flag rather than an
  argument, so long text never passes through shell quoting
* A command that changes state at Walmart's end MUST NOT act on defaults alone:
  `cases create` prints its payload unless `--submit` is given
* Exit codes: `0` success, `1` portal or network failure, `2` usage, config, or
  refused input
* A change to a flag's behaviour or cost updates `skills/walmart-support/SKILL.md`
  in the same commit: the skill documents the gates and per-case request costs
  that the help text does not
