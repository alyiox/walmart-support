"""Set the release version everywhere it is written down.

``uv version`` moves ``pyproject.toml`` and ``uv.lock``; the plugin manifests
carry their own literal copy, and a host reads the manifest rather than the
package, so one left behind ships the old skill to everyone installed through
it. That is not hypothetical — the Cursor marketplace entry sat a release
behind because it was updated by hand, alongside four files that were not.

Run with:  uv run python scripts/bump_version.py 0.1.4
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Manifests holding a bare ``version``. Add a new one here and the CI drift
# check in .github/workflows/ci.yml needs the same entry.
MANIFESTS = (
    "plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
    ".codex-plugin/plugin.json",
)

# The Cursor marketplace nests one entry per plugin, matched by package name.
MARKETPLACE = ".cursor-plugin/marketplace.json"

# Final releases only: a resolver skips pre-releases unless asked, so an
# a/b/rc build is invisible to `uvx walmart-support`.
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def package_name() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["name"])


def set_manifest(path: Path, version: str) -> bool:
    data = read_json(path)
    if data.get("version") == version:
        return False
    data["version"] = version
    write_json(path, data)
    return True


def set_marketplace(path: Path, version: str, name: str) -> bool:
    data = read_json(path)
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise SystemExit(f"{path}: no plugins array to update")

    entries = [p for p in plugins if isinstance(p, dict) and p.get("name") == name]
    if not entries:
        raise SystemExit(f"{path}: no plugin entry named {name}")

    changed = [e for e in entries if e.get("version") != version]
    for entry in changed:
        entry["version"] = version
    if changed:
        write_json(path, data)
    return bool(changed)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not _VERSION.match(argv[0]):
        print("usage: bump_version.py <major.minor.patch>", file=sys.stderr)
        return 2
    version = argv[0]
    name = package_name()

    # uv owns pyproject.toml and uv.lock; editing them by hand desyncs the lock.
    subprocess.run(["uv", "version", version], cwd=ROOT, check=True)

    touched = [MARKETPLACE] if set_marketplace(ROOT / MARKETPLACE, version, name) else []
    touched += [rel for rel in MANIFESTS if set_manifest(ROOT / rel, version)]

    for rel in sorted(touched):
        print(f"updated {rel}")
    if touched:
        # pyproject.toml and uv.lock go in the same commit; a lock left behind
        # is the drift this script exists to prevent.
        print(f"\n{len(touched) + 2} files now at {version}; commit them together")
    else:
        print(f"\nevery manifest already reads {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
