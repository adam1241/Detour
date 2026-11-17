"""Claude Code hooks for Detour (CLAUDE.md, spec §9.5).

pre  (PreToolUse on file edits): block edits to .env files and to migrations already on main.
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
    """Accept Git Bash style paths (/c/Users/...) on Windows as well as native ones."""
    if os.name == "nt":
        path = MSYS_DRIVE.sub(lambda m: f"{m.group(1).upper()}:/", path)
    return Path(path).resolve()


PROJECT_DIR = to_native(os.environ.get("CLAUDE_PROJECT_DIR", str(Path.cwd())))


def rel_path(file_path: str) -> PurePosixPath | None:
    try:
        rel = to_native(file_path).relative_to(PROJECT_DIR)
    except ValueError:
        return None
    return PurePosixPath(rel.as_posix())


def is_env_file(rel: PurePosixPath) -> bool:
    name = rel.name
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


def exists_on_main(rel: PurePosixPath) -> bool:
    for ref in ("origin/main", "main"):
        result = subprocess.run(  # noqa: S603
            ["git", "cat-file", "-e", f"{ref}:{rel}"],  # noqa: S607
            cwd=PROJECT_DIR,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return True
    return False


def pre(rel: PurePosixPath) -> int:
    if is_env_file(rel):
        print(f"Blocked: {rel} is a secrets file. Ask Adam to edit it.", file=sys.stderr)
        return 2
    if rel.parts[:2] == ("db", "migrations") and exists_on_main(rel):
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
    rel = rel_path(file_path)
    if rel is None:
        return 0
    if mode == "pre":
        return pre(rel)
    if mode == "post":
        return post(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
