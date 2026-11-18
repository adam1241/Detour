"""Claude Code hooks for Detour (CLAUDE.md, spec §9.5).

pre  (PreToolUse on file edits): block edits to secrets files (.env*, .envrc) and to migrations
     already on main. Fails closed: if git can't tell whether a migration is on main, block.
post (PostToolUse on file edits): run unit tests after edits under agent/.

Exit code 2 blocks the tool call (pre) or feeds the failure back to Claude (post).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

MSYS_DRIVE = re.compile(r"^/([a-zA-Z])/")


def to_native(path: str) -> Path:
    """Absolute path, symlinks not resolved. Accepts Git Bash paths (/c/...) on Windows."""
    if os.name == "nt":
        path = MSYS_DRIVE.sub(lambda m: f"{m.group(1).upper()}:/", path)
    return Path(os.path.abspath(path))


PROJECT_DIR = to_native(os.environ.get("CLAUDE_PROJECT_DIR", str(Path.cwd())))
PROJECT_DIR_REAL = PROJECT_DIR.resolve()


def relative_to_project(path: Path, root: Path) -> PurePosixPath | None:
    try:
        return PurePosixPath(path.relative_to(root).as_posix())
    except ValueError:
        return None


def candidate_paths(file_path: str) -> list[PurePosixPath]:
    """Project-relative paths to classify: the path as written and, if different, its target.

    Classifying both means a symlink can't hide a protected file behind an innocent name,
    or an innocent-looking file behind a protected link.
    """
    lexical = to_native(file_path)
    found = [
        relative_to_project(lexical, PROJECT_DIR),
        relative_to_project(lexical.resolve(), PROJECT_DIR_REAL),
    ]
    unique: list[PurePosixPath] = []
    for rel in found:
        if rel is not None and rel not in unique:
            unique.append(rel)
    return unique


def is_env_file(rel: PurePosixPath) -> bool:
    name = rel.name
    if name == ".env.example":
        return False
    return name in (".env", ".envrc") or name.startswith(".env.")


def is_migration(rel: PurePosixPath) -> bool:
    return rel.parts[:2] == ("db", "migrations")


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def migration_on_main(rel: PurePosixPath) -> bool | None:
    """True/False if git could answer for a main ref, None if it couldn't (caller fails closed)."""
    answered = False
    for ref in ("origin/main", "main"):
        if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
            continue
        listing = git("ls-tree", "--name-only", ref, "--", str(rel))
        if listing.returncode != 0:
            return None
        answered = True
        if listing.stdout.strip():
            return True
    return False if answered else None


def pre(rels: list[PurePosixPath]) -> int:
    for rel in rels:
        if is_env_file(rel):
            print(f"Blocked: {rel} is a secrets file. Ask Adam to edit it.", file=sys.stderr)
            return 2
        if is_migration(rel):
            on_main = migration_on_main(rel)
            if on_main is None:
                print(
                    f"Blocked: couldn't check whether {rel} is already on main "
                    "(no main ref or git error). Run `git fetch origin` and retry.",
                    file=sys.stderr,
                )
                return 2
            if on_main:
                print(
                    f"Blocked: {rel} is an applied migration (it exists on main). "
                    "Add a new migration instead, or ask Adam for approval.",
                    file=sys.stderr,
                )
                return 2
    return 0


def post(rel: PurePosixPath) -> int:
    if rel.parts[:1] != ("agent",) or rel.suffix != ".py":
        return 0
    result = subprocess.run(
        ["uv", "run", "--no-sync", "pytest", "-q", "-m", "not db"],  # noqa: S607
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"Unit tests failed after editing {rel}:\n{result.stdout[-3000:]}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = json.load(sys.stdin)
    file_path = payload.get("tool_input", {}).get("file_path") or payload.get("tool_input", {}).get(
        "notebook_path"
    )
    if not file_path:
        return 0
    rels = candidate_paths(file_path)
    if not rels:
        return 0
    if mode == "pre":
        return pre(rels)
    if mode == "post":
        return post(rels[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
