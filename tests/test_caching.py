import json
import sqlite3

import pytest

from bynat.caching import CacheManager


def test_cache_round_trip_and_column_deletion(tmp_path):
    manager = CacheManager(tmp_path / "cache")

    bucket = manager.get_column_cache("translate", "status")
    bucket["pending"] = "Pending"
    manager.save_function_cache("translate")

    reloaded = CacheManager(tmp_path / "cache")
    assert reloaded.load_function_cache("translate") == {
        "status": {"pending": "Pending"}
    }

    reloaded.delete_column_cache("translate", "status")
    assert reloaded.load_function_cache("translate") == {}
    assert json.loads((tmp_path / "cache" / "translate_cache.json").read_text()) == {}


def test_cache_rejects_empty_and_path_like_keys(tmp_path):
    manager = CacheManager(tmp_path)

    with pytest.raises(ValueError):
        manager.load_function_cache("   ")
    with pytest.raises(ValueError):
        manager.load_function_cache("nested/cache")
    with pytest.raises(TypeError):
        manager.get_column_cache("function", 123)


@pytest.mark.parametrize("content", ["{not valid json", "", "[1, 2]", b"\xff\xfe garbage"])
def test_corrupt_cache_is_quarantined_and_replaced_with_empty_dict(tmp_path, caplog, content):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "broken_cache.json"
    if isinstance(content, bytes):
        cache_file.write_bytes(content)
    else:
        cache_file.write_text(content, encoding="utf-8")

    manager = CacheManager(cache_dir)
    assert manager.load_function_cache("broken") == {}
    assert "is corrupted; moved it to" in caplog.text

    quarantined = list(cache_dir.glob("broken_cache.json.corrupt.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == (
        content if isinstance(content, bytes) else content.encode("utf-8")
    )

    # Saving afterwards writes a fresh file and leaves the quarantined copy alone.
    manager.get_column_cache("broken", "column")["key"] = "value"
    manager.save_function_cache("broken")
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"column": {"key": "value"}}
    assert quarantined[0].exists()


def test_save_unserializable_value_reports_error_without_raising(tmp_path, caplog):
    manager = CacheManager(tmp_path)
    manager.get_column_cache("function", "column")["bad"] = object()

    manager.save_function_cache("function")

    assert "Could not save cache file" in caplog.text


def test_cache_directory_is_created_on_save_not_construction(tmp_path):
    cache_dir = tmp_path / "not-yet-created"
    CacheManager(cache_dir)
    assert not cache_dir.exists()

    manager = CacheManager(cache_dir)
    manager.save_function_cache("function")
    assert (cache_dir / "function_cache.json").exists()


def test_failed_save_keeps_previous_cache_file_intact(tmp_path, caplog):
    manager = CacheManager(tmp_path)
    manager.get_column_cache("function", "column")["good"] = "value"
    manager.save_function_cache("function")
    cache_file = tmp_path / "function_cache.json"
    previous = cache_file.read_text(encoding="utf-8")

    manager.get_column_cache("function", "column")["bad"] = object()
    manager.save_function_cache("function")

    assert "Could not save cache file" in caplog.text
    assert cache_file.read_text(encoding="utf-8") == previous
    assert [path.name for path in tmp_path.iterdir()] == ["function_cache.json"]


def test_interrupted_replace_leaves_no_partial_or_temp_files(tmp_path, monkeypatch, caplog):
    import os

    manager = CacheManager(tmp_path)
    manager.get_column_cache("function", "column")["key"] = "value"

    def failing_replace(src, dst):
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", failing_replace)
    manager.save_function_cache("function")

    assert "simulated interruption" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_cache_dir_defaults_to_environment_variable_then_legacy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BYNAT_CACHE_DIR", raising=False)
    assert CacheManager().cache_dir_path == str(tmp_path / "Settings" / "Cache Data" / "Automated")

    monkeypatch.setenv("BYNAT_CACHE_DIR", str(tmp_path / "from-env"))
    assert CacheManager().cache_dir_path == str(tmp_path / "from-env")
    assert CacheManager(tmp_path / "explicit").cache_dir_path == str(tmp_path / "explicit")


def test_sqlite_initialization_is_lazy_until_explicitly_requested(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)

    assert not cache_dir.exists()

    manager._initialize_sqlite()

    assert (cache_dir / "cache.sqlite3").exists()


def test_sqlite_schema_has_required_tables_columns_keys_and_index(tmp_path):
    manager = CacheManager(tmp_path / "cache")
    manager._initialize_sqlite()

    with sqlite3.connect(manager._get_database_path()) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"cache_entries", "migration_metadata"}.issubset(tables)

        cache_columns = connection.execute(
            "PRAGMA table_info(cache_entries)"
        ).fetchall()
        assert cache_columns == [
            (0, "func_name", "TEXT", 1, None, 1),
            (1, "bucket", "TEXT", 1, None, 2),
            (2, "key", "TEXT", 1, None, 3),
            (3, "value_json", "TEXT", 1, None, 0),
            (4, "created_at", "TEXT", 1, None, 0),
        ]

        metadata_columns = connection.execute(
            "PRAGMA table_info(migration_metadata)"
        ).fetchall()
        assert metadata_columns == [
            (0, "source_name", "TEXT", 0, None, 1),
            (1, "migrated_at", "TEXT", 1, None, 0),
        ]

        indexes = connection.execute(
            "PRAGMA index_list(cache_entries)"
        ).fetchall()
        index_names = {row[1] for row in indexes}
        assert "idx_cache_entries_func_bucket" in index_names
        assert connection.execute(
            "PRAGMA index_info(idx_cache_entries_func_bucket)"
        ).fetchall() == [(0, 0, "func_name"), (1, 1, "bucket")]


def test_sqlite_connection_configures_timeout_wal_and_full_synchronous(tmp_path):
    manager = CacheManager(tmp_path / "cache")
    (tmp_path / "cache").mkdir()

    connection = manager._connect_sqlite()
    try:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        connection.close()


def test_sqlite_connection_closes_when_pragma_configuration_fails(tmp_path, monkeypatch):
    import importlib

    caching_module = importlib.import_module("bynat.caching.Caching")

    class FailingConnection:
        def __init__(self):
            self.execute_calls = []
            self.close_called = False

        def execute(self, statement):
            self.execute_calls.append(statement)
            if len(self.execute_calls) == 2:
                raise sqlite3.OperationalError("PRAGMA failure")
            return None

        def close(self):
            self.close_called = True

    connection = FailingConnection()
    monkeypatch.setattr(caching_module.sqlite3, "connect", lambda *args, **kwargs: connection)

    with pytest.raises(sqlite3.OperationalError, match="PRAGMA failure"):
        CacheManager(tmp_path / "cache")._connect_sqlite()

    assert connection.close_called


def test_sqlite_initialization_rolls_back_and_closes_on_failure(tmp_path, monkeypatch):
    manager = CacheManager(tmp_path / "cache")

    class FailingConnection:
        def __init__(self):
            self.rollback_called = False
            self.close_called = False

        def execute(self, statement):
            if statement == "BEGIN IMMEDIATE":
                return None
            raise sqlite3.OperationalError("schema failure")

        def rollback(self):
            self.rollback_called = True

        def close(self):
            self.close_called = True

    connection = FailingConnection()
    monkeypatch.setattr(manager, "_connect_sqlite", lambda: connection)

    with pytest.raises(sqlite3.OperationalError, match="schema failure"):
        manager._initialize_sqlite()

    assert connection.rollback_called
    assert connection.close_called


def test_sqlite_initialization_rolls_back_real_ddl_on_later_schema_failure(tmp_path, monkeypatch):
    manager = CacheManager(tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    real_connection = sqlite3.connect(manager._get_database_path())

    class FailingSchemaConnection:
        def __init__(self, connection):
            self.connection = connection
            self.first_ddl_completed = False
            self.rollback_called = False
            self.close_called = False

        def execute(self, statement):
            normalized = " ".join(statement.split()).upper()
            if normalized.startswith("CREATE TABLE"):
                result = self.connection.execute(statement)
                self.first_ddl_completed = True
                return result
            if normalized.startswith("CREATE INDEX"):
                raise sqlite3.OperationalError("later schema failure")
            return self.connection.execute(statement)

        def rollback(self):
            self.rollback_called = True
            self.connection.rollback()

        def close(self):
            self.close_called = True
            self.connection.close()

        def commit(self):
            return self.connection.commit()

    connection = FailingSchemaConnection(real_connection)
    monkeypatch.setattr(manager, "_connect_sqlite", lambda: connection)

    with pytest.raises(sqlite3.OperationalError, match="later schema failure"):
        manager._initialize_sqlite()

    assert connection.first_ddl_completed
    assert connection.rollback_called
    assert connection.close_called

    with sqlite3.connect(manager._get_database_path()) as inspection:
        schema_objects = inspection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'index') "
            "AND name IN ('cache_entries', 'migration_metadata', 'idx_cache_entries_func_bucket')"
        ).fetchall()
    assert schema_objects == []


def test_sqlite_initialization_rolls_back_and_closes_when_commit_fails(tmp_path, monkeypatch):
    manager = CacheManager(tmp_path / "cache")

    class CommitFailingConnection:
        def __init__(self):
            self.rollback_called = False
            self.close_called = False

        def execute(self, statement):
            return None

        def commit(self):
            raise sqlite3.OperationalError("commit failure")

        def rollback(self):
            self.rollback_called = True

        def close(self):
            self.close_called = True

    connection = CommitFailingConnection()
    monkeypatch.setattr(manager, "_connect_sqlite", lambda: connection)

    with pytest.raises(sqlite3.OperationalError, match="commit failure"):
        manager._initialize_sqlite()

    assert connection.rollback_called
    assert connection.close_called


def test_repeated_sqlite_initialization_is_safe(tmp_path):
    manager = CacheManager(tmp_path / "cache")

    manager._initialize_sqlite()
    manager._initialize_sqlite()

    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name IN "
            "('cache_entries', 'migration_metadata', 'idx_cache_entries_func_bucket')"
        ).fetchone()[0] == 3
