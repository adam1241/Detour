from pathlib import Path

import pytest

from db.migrate import MIGRATIONS_DIR, migration_files


def test_repo_migrations_are_numbered_and_ordered() -> None:
    names = [f.name for f in migration_files(MIGRATIONS_DIR)]
    assert names[0] == "0001_init.sql"
    assert names == sorted(names)


def test_migration_files_sorted_by_number(tmp_path: Path) -> None:
    for name in ("0002_b.sql", "0001_a.sql", "0010_c.sql"):
        (tmp_path / name).write_text("SELECT 1;")
    assert [f.name for f in migration_files(tmp_path)] == [
        "0001_a.sql",
        "0002_b.sql",
        "0010_c.sql",
    ]


@pytest.mark.parametrize("bad", ["1_init.sql", "0001-init.sql", "0001_Init.sql", "init.sql"])
def test_badly_named_migration_rejected(tmp_path: Path, bad: str) -> None:
    (tmp_path / bad).write_text("SELECT 1;")
    with pytest.raises(ValueError, match="migration file name"):
        migration_files(tmp_path)


def test_duplicate_migration_number_rejected(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="duplicate"):
        migration_files(tmp_path)
