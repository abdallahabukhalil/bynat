import json
import logging
import multiprocessing
import sqlite3

import pytest

from bynat.caching import CacheManager


def _run_legacy_migration(cache_dir, func_name, start_event, result_queue):
    try:
        start_event.wait(10)
        CacheManager(cache_dir)._migrate_legacy_json_cache(func_name)
        result_queue.put("ok")
    except Exception as error:  # pragma: no cover - reported to the parent
        result_queue.put(f"{type(error).__name__}: {error}")


def test_cache_round_trip_via_sqlite(tmp_path):
    manager = CacheManager(tmp_path / "cache")

    bucket = manager.get_column_cache("translate", "status")
    bucket["pending"] = "Pending"
    manager.save_function_cache("translate")

    reloaded = CacheManager(tmp_path / "cache")
    assert reloaded.load_function_cache("translate") == {
        "status": {"pending": "Pending"},
    }
    # The entry now lives in SQLite rather than a JSON cache file.
    assert not (tmp_path / "cache" / "translate_cache.json").exists()
    assert (tmp_path / "cache" / "cache.sqlite3").exists()


def test_save_persists_added_changed_and_removed_entries(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [
            ("status", "pending", '"Pending"'),
            ("status", "done", '"No"'),
            ("status", "stale", '"Old"'),
        ],
    )

    live = manager.load_function_cache("translate")
    live["status"]["done"] = "Yes"          # changed
    live["status"]["new"] = "Fresh"          # added
    del live["status"]["stale"]              # removed
    manager.save_function_cache("translate")

    with sqlite3.connect(manager._get_database_path()) as connection:
        rows = connection.execute(
            "SELECT bucket, key, value_json FROM cache_entries ORDER BY bucket, key"
        ).fetchall()

    # The changed and added entries are written and the removed key is deleted,
    # all within one save.
    assert rows == [
        ("status", "done", '"Yes"'),
        ("status", "new", '"Fresh"'),
        ("status", "pending", '"Pending"'),
    ]


def test_save_persists_entirely_removed_buckets(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [
            ("status", "pending", '"Pending"'),
            ("coordinates", "home", "[1.5, 2]"),
            ("coordinates", "nested", '{"ok": true}'),
        ],
    )

    live = manager.load_function_cache("translate")
    del live["coordinates"]                  # the whole bucket is gone
    manager.save_function_cache("translate")

    with sqlite3.connect(manager._get_database_path()) as connection:
        identities = connection.execute(
            "SELECT bucket, key FROM cache_entries ORDER BY bucket, key"
        ).fetchall()
        # Removal is per-identity, never a broad bucket delete, so a DELETE on
        # the bucket column alone would be detectable; here every coordinates
        # row is gone while unrelated rows survive.
        buckets = {row[0] for row in identities}

    assert identities == [("status", "pending")]
    assert "coordinates" not in buckets


def test_save_handles_deletion_only_transaction(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [("status", "pending", '"Pending"'), ("status", "done", '"Done"')],
    )

    live = manager.load_function_cache("translate")
    del live["status"]["done"]               # removal with no additions or changes
    manager.save_function_cache("translate")

    with sqlite3.connect(manager._get_database_path()) as connection:
        rows = connection.execute(
            "SELECT bucket, key, value_json FROM cache_entries ORDER BY key"
        ).fetchall()

    # A save carrying only a removal is not short-circuited by an early return.
    assert rows == [("status", "pending", '"Pending"')]


def test_failed_removal_transaction_rolls_back_and_retries(tmp_path, monkeypatch, caplog):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [("status", "pending", '"Pending"'), ("status", "done", '"Done"')],
    )
    live = manager.load_function_cache("translate")
    baseline_before = manager._baselines["translate"]

    del live["status"]["done"]

    real_connect = manager._connect_sqlite
    state = {"commits": 0}

    class CommitFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            return self.connection.execute(statement, parameters)

        def executemany(self, statement, parameters):
            return self.connection.executemany(statement, parameters)

        def commit(self):
            # The first commit belongs to _initialize_sqlite; the removal
            # transaction's commit is the second and fails.
            state["commits"] += 1
            if state["commits"] == 2:
                raise sqlite3.OperationalError("commit failure")
            return self.connection.commit()

        def rollback(self):
            return self.connection.rollback()

        def close(self):
            return self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        manager, "_connect_sqlite", lambda: CommitFailingConnection(real_connect())
    )
    manager.save_function_cache("translate")

    assert "commit failure" in caplog.text
    # The deletion is rolled back, so the row survives and the baseline is not
    # advanced, leaving the pending removal for a later retry.
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'status' AND key = 'done'"
        ).fetchone() == ('"Done"',)
    assert manager._baselines["translate"] is baseline_before

    monkeypatch.undo()
    manager.save_function_cache("translate")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'status' AND key = 'done'"
        ).fetchone() is None
    assert manager._baselines["translate"] == {"status": {"pending": "Pending"}}


def test_save_advances_baseline_after_removal(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [("status", "pending", '"Pending"'), ("status", "done", '"Done"')],
    )

    live = manager.load_function_cache("translate")
    del live["status"]["done"]
    manager.save_function_cache("translate")

    # After a successful removal the baseline advances and no longer lists it.
    assert manager._baselines["translate"] == {"status": {"pending": "Pending"}}

    # Re-adding the key is now a fresh addition against the advanced baseline and
    # is re-persisted rather than treated as a no-op.
    live["status"]["done"] = "Back"
    manager.save_function_cache("translate")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'status' AND key = 'done'"
        ).fetchone() == ('"Back"',)


def test_cache_rejects_empty_and_path_like_keys(tmp_path):
    manager = CacheManager(tmp_path)

    with pytest.raises(ValueError):
        manager.load_function_cache("   ")
    with pytest.raises(ValueError):
        manager.load_function_cache("nested/cache")
    with pytest.raises(TypeError):
        manager.get_column_cache("function", 123)


@pytest.mark.parametrize(
    "content, expected_log",
    [
        ("{not valid json", "is corrupted; moved it to"),
        ("", "is corrupted; moved it to"),
        # A valid-but-non-object top-level value is now reported by the accepted
        # legacy migration helper, which still quarantines and replaces it.
        ("[1, 2]", "has an invalid top-level JSON value; moved it to"),
        (b"\xff\xfe garbage", "is corrupted; moved it to"),
    ],
)
def test_corrupt_cache_is_quarantined_and_replaced_with_empty_dict(tmp_path, caplog, content, expected_log):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "broken_cache.json"
    if isinstance(content, bytes):
        cache_file.write_bytes(content)
    else:
        cache_file.write_text(content, encoding="utf-8")

    manager = CacheManager(cache_dir)
    assert manager.load_function_cache("broken") == {}
    assert expected_log in caplog.text

    quarantined = list(cache_dir.glob("broken_cache.json.corrupt.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == (
        content if isinstance(content, bytes) else content.encode("utf-8")
    )

    # Saving afterwards persists a fresh entry in SQLite and leaves the
    # quarantined copy alone.
    manager.get_column_cache("broken", "column")["key"] = "value"
    manager.save_function_cache("broken")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT bucket, key, value_json FROM cache_entries ORDER BY bucket, key"
        ).fetchall() == [("column", "key", '"value"')]
    assert quarantined[0].exists()


def test_save_unserializable_value_reports_error_without_raising(tmp_path, caplog):
    manager = CacheManager(tmp_path)
    manager.get_column_cache("function", "column")["bad"] = object()
    baseline_before = manager._baselines["function"]

    manager.save_function_cache("function")

    assert "Could not save cache for" in caplog.text

    # The failed serialization leaves the baseline unchanged and the pending
    # in-memory change in place, so a later serializable save can persist it.
    assert manager._baselines["function"] is baseline_before
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute("SELECT count(*) FROM cache_entries").fetchone() == (0,)

    manager.get_column_cache("function", "column")["bad"] = "now serializable"
    manager.save_function_cache("function")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'column' AND key = 'bad'"
        ).fetchone() == ('"now serializable"',)


def test_cache_directory_is_created_on_save_not_construction(tmp_path):
    cache_dir = tmp_path / "not-yet-created"
    CacheManager(cache_dir)
    assert not cache_dir.exists()

    manager = CacheManager(cache_dir)
    # In-memory changes exist without a prior load; saving them creates the
    # backend directory and SQLite database.
    manager.caches["function"] = {"column": {"key": "value"}}
    manager.save_function_cache("function")
    assert (cache_dir / "cache.sqlite3").exists()


def test_failed_db_save_keeps_baseline_and_rows_intact_for_retry(tmp_path, monkeypatch, caplog):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    manager.get_column_cache("function", "column")["good"] = "value"
    manager.save_function_cache("function")
    baseline_before = manager._baselines["function"]

    manager.get_column_cache("function", "column")["good"] = "updated"

    real_connect = manager._connect_sqlite

    class WriteFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            return self.connection.execute(statement, parameters)

        def executemany(self, statement, parameters):
            raise sqlite3.OperationalError("simulated write failure")

        def commit(self):
            return self.connection.commit()

        def rollback(self):
            return self.connection.rollback()

        def close(self):
            return self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        manager, "_connect_sqlite", lambda: WriteFailingConnection(real_connect())
    )
    manager.save_function_cache("function")

    assert "Could not save cache for" in caplog.text
    # The previously committed rows survive and the baseline is not advanced, so
    # the pending in-memory change is preserved for a later retry.
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'column' AND key = 'good'"
        ).fetchone() == ('"value"',)
    assert manager._baselines["function"] is baseline_before

    monkeypatch.undo()
    manager.save_function_cache("function")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'column' AND key = 'good'"
        ).fetchone() == ('"updated"',)
    assert manager._baselines["function"] is not baseline_before


def test_save_transaction_rolls_back_on_commit_failure(tmp_path, monkeypatch, caplog):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    manager.get_column_cache("function", "column")["good"] = "value"
    manager.save_function_cache("function")
    baseline_before = manager._baselines["function"]

    manager.get_column_cache("function", "column")["good"] = "updated"

    real_connect = manager._connect_sqlite
    state = {"commits": 0}

    class CommitFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            return self.connection.execute(statement, parameters)

        def executemany(self, statement, parameters):
            return self.connection.executemany(statement, parameters)

        def commit(self):
            # The first commit belongs to _initialize_sqlite; the write
            # transaction's commit is the second and fails.
            state["commits"] += 1
            if state["commits"] == 2:
                raise sqlite3.OperationalError("commit failure")
            return self.connection.commit()

        def rollback(self):
            return self.connection.rollback()

        def close(self):
            return self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        manager, "_connect_sqlite", lambda: CommitFailingConnection(real_connect())
    )
    manager.save_function_cache("function")

    assert "commit failure" in caplog.text
    # The rows inserted inside the failed transaction are rolled back, leaving
    # only the previously committed value behind.
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'column' AND key = 'good'"
        ).fetchone() == ('"value"',)
    assert manager._baselines["function"] is baseline_before

    monkeypatch.undo()
    manager.save_function_cache("function")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'column' AND key = 'good'"
        ).fetchone() == ('"updated"',)


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


def test_legacy_migration_imports_supported_values_and_maps_filename(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "translate_cache.json").write_text(
        json.dumps({
            "status": {"pending": "Pending", "missing": None},
            "coordinates": {"home": [1.5, 2], "nested": {"ok": True}},
        }),
        encoding="utf-8",
    )

    manager = CacheManager(cache_dir)
    manager._migrate_legacy_json_cache("translate")

    with sqlite3.connect(manager._get_database_path()) as connection:
        rows = connection.execute(
            "SELECT func_name, bucket, key, value_json FROM cache_entries ORDER BY bucket, key"
        ).fetchall()
        marker = connection.execute(
            "SELECT source_name FROM migration_metadata"
        ).fetchall()

    assert rows == [
        ("translate", "coordinates", "home", "[1.5, 2]"),
        ("translate", "coordinates", "nested", '{"ok": true}'),
        ("translate", "status", "missing", "null"),
        ("translate", "status", "pending", '"Pending"'),
    ]
    assert marker == [("translate_cache.json",)]


def test_legacy_migration_is_idempotent_and_existing_rows_win(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    manager = CacheManager(cache_dir)
    manager._initialize_sqlite()
    with sqlite3.connect(manager._get_database_path()) as connection:
        connection.execute(
            "INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?)",
            ("translate", "status", "pending", '"fresh"', "old"),
        )
        connection.commit()
    (cache_dir / "translate_cache.json").write_text(
        '{"status": {"pending": "stale", "new": 3}}', encoding="utf-8"
    )

    manager._migrate_legacy_json_cache("translate")
    manager._migrate_legacy_json_cache("translate")

    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT value_json FROM cache_entries WHERE bucket = 'status' AND key = 'pending'"
        ).fetchone() == ('"fresh"',)
        assert connection.execute(
            "SELECT count(*) FROM cache_entries"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM migration_metadata"
        ).fetchone() == (1,)


def test_missing_legacy_source_does_not_create_database_or_marker(tmp_path):
    manager = CacheManager(tmp_path / "cache")

    manager._migrate_legacy_json_cache("missing")

    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_legacy_migration_quarantines_corrupt_source_without_marker(tmp_path, content, caplog):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    source = cache_dir / "broken_cache.json"
    source.write_text(content, encoding="utf-8")

    CacheManager(cache_dir)._migrate_legacy_json_cache("broken")

    assert not source.exists()
    assert len(list(cache_dir.glob("broken_cache.json.corrupt.*"))) == 1
    assert "marker" not in caplog.text.lower()
    assert not (cache_dir / "cache.sqlite3").exists()


def test_legacy_migration_preserves_unsupported_source_for_retry(tmp_path, caplog):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    source = cache_dir / "translate_cache.json"
    source.write_text('{"status": ["bad"]}', encoding="utf-8")
    manager = CacheManager(cache_dir)

    manager._migrate_legacy_json_cache("translate")
    assert source.exists()
    assert "unsupported" in caplog.text
    assert not (cache_dir / "cache.sqlite3").exists()

    source.write_text('{"status": {"ok": null}}', encoding="utf-8")
    manager._migrate_legacy_json_cache("translate")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute("SELECT count(*) FROM cache_entries").fetchone() == (1,)


def test_legacy_migration_rolls_back_and_can_retry_after_partial_insert_failure(
    tmp_path, monkeypatch
):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "translate_cache.json").write_text(
        '{"status": {"a": 1, "b": 2}}', encoding="utf-8"
    )
    manager = CacheManager(cache_dir)
    real_connect = manager._connect_sqlite
    state = {"failed": False, "inserted_rows": 0}

    class PartiallyFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            return self.connection.execute(statement, parameters)

        def executemany(self, statement, parameters):
            if not state["failed"] and "INSERT OR IGNORE" in statement:
                rows = list(parameters)
                self.connection.execute(statement, rows[0])
                state["inserted_rows"] = 1
                state["failed"] = True
                raise sqlite3.OperationalError("insert failure")
            return self.connection.executemany(statement, parameters)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        manager, "_connect_sqlite", lambda: PartiallyFailingConnection(real_connect())
    )
    with pytest.raises(sqlite3.OperationalError, match="insert failure"):
        manager._migrate_legacy_json_cache("translate")

    assert state["inserted_rows"] == 1
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute("SELECT count(*) FROM cache_entries").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM migration_metadata").fetchone() == (0,)

    manager._migrate_legacy_json_cache("translate")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute(
            "SELECT bucket, key, value_json FROM cache_entries ORDER BY key"
        ).fetchall() == [("status", "a", "1"), ("status", "b", "2")]
        assert connection.execute(
            "SELECT source_name FROM migration_metadata"
        ).fetchall() == [("translate_cache.json",)]


def test_legacy_migration_rolls_back_and_can_retry_after_marker_insert_failure(
    tmp_path, monkeypatch
):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "translate_cache.json").write_text(
        '{"status": {"a": 1, "b": 2}}', encoding="utf-8"
    )
    manager = CacheManager(cache_dir)
    real_connect = manager._connect_sqlite
    state = {"failed": False, "entry_rows": 0, "marker_attempted": False}

    class MarkerFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.split()).upper()
            if not state["failed"] and normalized.startswith(
                "INSERT INTO MIGRATION_METADATA"
            ):
                state["failed"] = True
                state["marker_attempted"] = True
                raise sqlite3.OperationalError("marker insert failure")
            return self.connection.execute(statement, parameters)

        def executemany(self, statement, parameters):
            if "INSERT OR IGNORE" in statement:
                rows = list(parameters)
                state["entry_rows"] = len(rows)
                return self.connection.executemany(statement, rows)
            return self.connection.executemany(statement, parameters)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        manager, "_connect_sqlite", lambda: MarkerFailingConnection(real_connect())
    )
    with pytest.raises(sqlite3.OperationalError, match="marker insert failure"):
        manager._migrate_legacy_json_cache("translate")

    assert state["entry_rows"] == 2
    assert state["marker_attempted"]
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute("SELECT count(*) FROM cache_entries").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM migration_metadata").fetchone() == (0,)

    manager._migrate_legacy_json_cache("translate")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute("SELECT count(*) FROM cache_entries").fetchone() == (2,)
        assert connection.execute(
            "SELECT source_name FROM migration_metadata"
        ).fetchall() == [("translate_cache.json",)]


def test_legacy_migration_rolls_back_and_can_retry_after_commit_failure(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "translate_cache.json").write_text(
        '{"status": {"ok": true}}', encoding="utf-8"
    )
    manager = CacheManager(cache_dir)
    real_connect = manager._connect_sqlite
    state = {"commits": 0}

    class CommitFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def commit(self):
            state["commits"] += 1
            if state["commits"] == 2:
                raise sqlite3.OperationalError("commit failure")
            return self.connection.commit()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        manager, "_connect_sqlite", lambda: CommitFailingConnection(real_connect())
    )
    with pytest.raises(sqlite3.OperationalError, match="commit failure"):
        manager._migrate_legacy_json_cache("translate")

    monkeypatch.undo()
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute("SELECT count(*) FROM cache_entries").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM migration_metadata").fetchone() == (0,)
    manager._migrate_legacy_json_cache("translate")
    with sqlite3.connect(manager._get_database_path()) as connection:
        assert connection.execute("SELECT count(*) FROM migration_metadata").fetchone() == (1,)


@pytest.mark.parametrize("func_names", [["same"], ["left", "right"]])
def test_legacy_migration_is_safe_for_concurrent_sources(tmp_path, func_names):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    for func_name in func_names:
        (cache_dir / f"{func_name}_cache.json").write_text(
            json.dumps({"bucket": {f"key-{func_name}": func_name}}), encoding="utf-8"
        )

    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_run_legacy_migration,
            args=(str(cache_dir), func_name, start_event, result_queue),
        )
        for func_name in func_names * 2
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(20)
        assert not process.is_alive()
    assert [result_queue.get(timeout=2) for _ in processes] == ["ok"] * len(processes)

    with sqlite3.connect(cache_dir / "cache.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM cache_entries").fetchone() == (len(func_names),)
        assert connection.execute("SELECT count(*) FROM migration_metadata").fetchone() == (len(func_names),)


def test_rebuild_function_cache_from_sqlite(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    manager._initialize_sqlite()

    created_at = "2026-01-01T00:00:00+00:00"
    rows = [
        # Multiple buckets, explicit None, list and dict values, plus a scalar.
        ("translate", "status", "pending", '"Pending"', created_at),
        ("translate", "status", "missing", "null", created_at),
        ("translate", "coordinates", "home", "[1.5, 2]", created_at),
        ("translate", "coordinates", "nested", '{"ok": true}', created_at),
        ("translate", "meta", "count", "3", created_at),
        # A different function must not leak into the rebuild.
        ("other", "status", "pending", '"other"', created_at),
    ]

    with sqlite3.connect(manager._get_database_path()) as connection:
        connection.executemany(
            "INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()

    assert manager._rebuild_function_cache_from_sqlite("translate") == {
        "status": {"pending": "Pending", "missing": None},
        "coordinates": {"home": [1.5, 2], "nested": {"ok": True}},
        "meta": {"count": 3},
    }


def test_rebuild_function_cache_from_sqlite_is_empty_without_rows(tmp_path):
    manager = CacheManager(tmp_path / "cache")
    manager._initialize_sqlite()

    assert manager._rebuild_function_cache_from_sqlite("unused") == {}


def test_load_function_cache_migrates_legacy_json_through_public_path(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "translate_cache.json").write_text(
        json.dumps({"status": {"pending": "Pending", "missing": None}}),
        encoding="utf-8",
    )

    manager = CacheManager(cache_dir)

    # First public access migrates the legacy JSON and returns it as a dict.
    assert manager.load_function_cache("translate") == {
        "status": {"pending": "Pending", "missing": None}
    }

    # The backend was created and the read is now backed by SQLite rows.
    assert (cache_dir / "cache.sqlite3").exists()
    with sqlite3.connect(manager._get_database_path()) as connection:
        rows = connection.execute(
            "SELECT bucket, key, value_json FROM cache_entries ORDER BY bucket, key"
        ).fetchall()
        marker = connection.execute(
            "SELECT source_name FROM migration_metadata"
        ).fetchall()

    assert rows == [
        ("status", "missing", "null"),
        ("status", "pending", '"Pending"'),
    ]
    assert marker == [("translate_cache.json",)]


def test_load_function_cache_missing_function_returns_empty_and_builds_backend(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)

    assert not (cache_dir / "cache.sqlite3").exists()

    # A missing function returns an empty dict yet still creates the backend.
    assert manager.load_function_cache("ghost") == {}
    assert (cache_dir / "cache.sqlite3").exists()

    # A second missing function also returns empty without error.
    assert manager.load_function_cache("another-ghost") == {}


def test_load_function_cache_reads_sqlite_authoritatively(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    manager._initialize_sqlite()

    created_at = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(manager._get_database_path()) as connection:
        connection.execute(
            "INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?)",
            ("translate", "status", "pending", '"Pending"', created_at),
        )
        connection.commit()

    # No legacy JSON file exists; the public read is served from SQLite.
    assert manager.load_function_cache("translate") == {"status": {"pending": "Pending"}}


def test_load_function_cache_memoizes_and_preserves_mutations(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)

    first = manager.load_function_cache("translate")
    first["status"] = {"pending": "Pending"}

    second = manager.load_function_cache("translate")

    # Memoized: same object, mutations preserved across calls.
    assert second is first
    assert manager.load_function_cache("translate") == {"status": {"pending": "Pending"}}


def test_get_column_cache_returns_existing_bucket_identity(tmp_path):
    manager = CacheManager(tmp_path / "cache")
    func_cache = manager.load_function_cache("translate")

    first = manager.get_column_cache("translate", "status")
    first["pending"] = "Pending"

    # The second lookup returns the very same bucket object, and it is the one
    # stored inside the memoized function cache.
    second = manager.get_column_cache("translate", "status")
    assert second is first
    assert func_cache["status"] is second


def test_get_column_cache_creates_missing_bucket(tmp_path):
    manager = CacheManager(tmp_path / "cache")

    bucket = manager.get_column_cache("translate", "status")
    assert bucket == {}
    assert isinstance(bucket, dict)

    # The freshly created bucket lives inside the function cache so mutations
    # are visible through the memoized mapping.
    bucket["pending"] = "Pending"
    assert manager.load_function_cache("translate") == {"status": {"pending": "Pending"}}

    # A different bucket for the same function is an independent empty dict.
    other = manager.get_column_cache("translate", "coordinates")
    assert other == {}
    assert other is not bucket


def test_get_column_cache_preserves_explicit_none_value(tmp_path):
    manager = CacheManager(tmp_path / "cache")
    bucket = manager.get_column_cache("translate", "status")

    bucket["missing"] = None

    # Ordinary mutable-dict behaviors all observe the explicit None value.
    assert "missing" in bucket            # membership
    assert bucket["missing"] is None      # get
    assert dict(bucket) == {"missing": None}  # equality
    assert list(bucket) == ["missing"]    # iteration
    bucket["pending"] = "Pending"
    assert sorted(bucket) == ["missing", "pending"]

    # The None value survives through the memoized function cache.
    assert manager.get_column_cache("translate", "status") == {"missing": None, "pending": "Pending"}


def test_get_column_cache_missing_function_creates_empty_bucket(tmp_path):
    manager = CacheManager(tmp_path / "cache")

    # A function with no cached entries yields an empty bucket that can be
    # populated in place, memoized within the manager.
    bucket = manager.get_column_cache("ghost", "status")
    assert bucket == {}

    bucket["pending"] = "Pending"
    assert manager.get_column_cache("ghost", "status") is bucket
    assert manager.load_function_cache("ghost") == {"status": {"pending": "Pending"}}


def _seed_cache_entries(manager, func_name, rows):
    """Insert raw cache_entries rows for ``func_name`` and return the manager."""
    manager._initialize_sqlite()
    created_at = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(manager._get_database_path()) as connection:
        connection.executemany(
            "INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?)",
            [(func_name, bucket, key, value_json, created_at)
             for bucket, key, value_json in rows],
        )
        connection.commit()


def test_load_function_cache_records_baseline_matching_loaded_state(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)

    _seed_cache_entries(
        manager,
        "translate",
        [
            ("coordinates", "home", "[1.5, 2]"),
            ("status", "nested", '{"ok": true}'),
        ],
    )

    loaded = manager.load_function_cache("translate")

    # The baseline snapshot equals the state that was successfully loaded.
    assert manager._baselines["translate"] == {
        "coordinates": {"home": [1.5, 2]},
        "status": {"nested": {"ok": True}},
    }
    assert manager._baselines["translate"] == loaded


def test_baseline_is_deep_copy_and_survives_nested_mutation(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)

    _seed_cache_entries(
        manager,
        "translate",
        [
            ("coordinates", "home", "[1.5, 2]"),
            ("status", "nested", '{"ok": true}'),
        ],
    )

    loaded = manager.load_function_cache("translate")

    # Mutate nested lists and dicts in place, plus add new nested entries.
    loaded["coordinates"]["home"].append(99)
    loaded["coordinates"]["home"][0] = 0
    loaded["status"]["nested"]["ok"] = False
    loaded["status"]["nested"]["extra"] = "added"
    loaded["coordinates"]["new"] = {"deep": [1, 2]}

    # The baseline snapshot is unchanged despite the nested mutations.
    assert manager._baselines["translate"] == {
        "coordinates": {"home": [1.5, 2]},
        "status": {"nested": {"ok": True}},
    }


def test_separate_functions_have_separate_baselines(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)

    _seed_cache_entries(
        manager,
        "translate",
        [("status", "pending", '"Pending"')],
    )
    _seed_cache_entries(
        manager,
        "other",
        [("status", "pending", '"Other"')],
    )

    translate = manager.load_function_cache("translate")
    manager.load_function_cache("other")

    assert manager._baselines["translate"] == {"status": {"pending": "Pending"}}
    assert manager._baselines["other"] == {"status": {"pending": "Other"}}
    assert manager._baselines["translate"] is not manager._baselines["other"]
    assert manager._baselines["translate"] != manager._baselines["other"]

    # Mutating one function's live cache leaves the other function's baseline
    # untouched, proving the baselines are independent per function.
    translate["status"]["pending"] = "Done"
    assert manager._baselines["translate"] == {"status": {"pending": "Pending"}}
    assert manager._baselines["other"] == {"status": {"pending": "Other"}}


def test_save_persists_added_and_changed_entries(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [
            ("status", "pending", '"Pending"'),
            ("status", "done", '"No"'),
        ],
    )

    live = manager.load_function_cache("translate")
    live["status"]["done"] = "Yes"          # changed
    live["status"]["new"] = "Fresh"          # added
    manager.save_function_cache("translate")

    with sqlite3.connect(manager._get_database_path()) as connection:
        rows = connection.execute(
            "SELECT bucket, key, value_json FROM cache_entries ORDER BY bucket, key"
        ).fetchall()

    assert rows == [
        ("status", "done", '"Yes"'),
        ("status", "new", '"Fresh"'),
        ("status", "pending", '"Pending"'),
    ]


def test_save_detects_nested_mutation_and_writes_only_changed_entries(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    seed_at = "2026-01-01T00:00:00+00:00"
    manager._initialize_sqlite()
    with sqlite3.connect(manager._get_database_path()) as connection:
        connection.execute(
            "INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?)",
            ("translate", "coordinates", "home", "[1.5, 2]", seed_at),
        )
        connection.execute(
            "INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?)",
            ("translate", "status", "nested", '{"ok": true}', seed_at),
        )
        connection.commit()

    live = manager.load_function_cache("translate")
    # Mutate a nested dict field in place rather than reassigning the whole value.
    live["status"]["nested"]["ok"] = False
    manager.save_function_cache("translate")

    with sqlite3.connect(manager._get_database_path()) as connection:
        by_key = {
            row[1]: row
            for row in connection.execute(
                "SELECT bucket, key, value_json, created_at FROM cache_entries"
            ).fetchall()
        }

    # The untouched entry keeps its original value and timestamp.
    assert by_key["home"] == ("coordinates", "home", "[1.5, 2]", seed_at)
    # The nested mutation is detected, re-serialized, and rewritten with a fresh ts.
    assert by_key["nested"][2] == '{"ok": false}'
    assert by_key["nested"][3] != seed_at


def test_save_advances_baseline_after_success(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(manager, "translate", [("status", "pending", '"Pending"')])

    live = manager.load_function_cache("translate")
    assert manager._baselines["translate"] == {"status": {"pending": "Pending"}}

    live["status"]["pending"] = "Done"
    live["status"]["extra"] = "Added"
    manager.save_function_cache("translate")

    # After a successful save the baseline advances to a snapshot of the live cache.
    assert manager._baselines["translate"] == {"status": {"pending": "Done", "extra": "Added"}}

    # The advanced baseline is a deep copy: later live mutations do not move it.
    live["status"]["pending"] = "Later"
    assert manager._baselines["translate"] == {"status": {"pending": "Done", "extra": "Added"}}


def test_save_conflict_update_last_writer_wins_and_disjoint_merge(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(manager, "translate", [("bucket", "a", '"A0"'), ("bucket", "c", '"C0"')])

    left = CacheManager(cache_dir)
    right = CacheManager(cache_dir)
    left_live = left.load_function_cache("translate")
    right_live = right.load_function_cache("translate")

    # Disjoint keys written by two managers merge into one keyspace.
    left_live["bucket"]["a"] = "A1"
    left.save_function_cache("translate")
    right_live["bucket"]["c"] = "C1"
    right.save_function_cache("translate")

    reloaded = CacheManager(cache_dir).load_function_cache("translate")
    assert reloaded == {"bucket": {"a": "A1", "c": "C1"}}

    # The same key written by both managers resolves to the last committed writer.
    left_live["bucket"]["a"] = "A2"
    left.save_function_cache("translate")
    right_live["bucket"]["a"] = "A3"
    right.save_function_cache("translate")

    reloaded = CacheManager(cache_dir).load_function_cache("translate")
    assert reloaded["bucket"]["a"] == "A3"
    assert reloaded["bucket"]["c"] == "C1"


# ``delete_column_cache`` is validated through two sequential ``CacheManager``
# instances rather than real processes. The D-06 identity-scope rule asks which
# rows a bucket deletion removes, and that set is a deterministic function of the
# deleting manager's in-memory baseline snapshot: ``save_function_cache`` only
# ever issues per-identity ``DELETE`` rows, so a key that is absent from the
# baseline is never a deletion target regardless of when it was committed. Two
# managers sharing one SQLite file reproduce the exact shared-state interaction a
# race would (one manager commits rows the other never saw), while the set of
# rows deleted depends only on each manager's baseline, not on process
# scheduling. The genuine cross-process atomicity of those committed writes is
# already covered by the multiprocessing migration tests and SQLite's
# ``BEGIN IMMEDIATE``; re-proving it here would add nondeterminism without
# exercising any additional branch of the identity-scope rule.
def _rows(manager):
    """Return ``(bucket, key, value_json)`` rows for the shared cache database."""
    with sqlite3.connect(manager._get_database_path()) as connection:
        return connection.execute(
            "SELECT bucket, key, value_json FROM cache_entries ORDER BY bucket, key"
        ).fetchall()


def test_delete_column_cache_persists_existing_bucket_removal(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [
            ("status", "pending", '"Pending"'),
            ("status", "done", '"Done"'),
            ("coordinates", "home", "[1.5, 2]"),
        ],
    )

    manager.delete_column_cache("translate", "status")

    # Every row of the named bucket is removed row-by-row and the change commits,
    # so the deletion is durable on the next manager that reads the same database.
    assert _rows(manager) == [("coordinates", "home", "[1.5, 2]")]

    reloaded = CacheManager(cache_dir)
    assert "status" not in reloaded.load_function_cache("translate")


def test_delete_column_cache_stale_manager_preserves_concurrent_additions(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(manager, "translate", [("status", "known", '"Known"')])

    # The stale manager loads first, so its baseline only knows the pre-existing
    # key in the bucket.
    stale = CacheManager(cache_dir)
    stale.load_function_cache("translate")

    # A concurrent manager adds a fresh key to the same bucket and commits it
    # after the stale manager's baseline was captured.
    fresh = CacheManager(cache_dir)
    fresh_live = fresh.load_function_cache("translate")
    fresh_live["status"]["concurrent"] = "Concurrent"
    fresh.save_function_cache("translate")

    # The stale manager deletes the whole bucket. Because its baseline never saw
    # the concurrent key, only the known identity is deleted.
    stale.delete_column_cache("translate", "status")

    # The key added after the stale baseline survives; only the known row is gone.
    assert _rows(manager) == [("status", "concurrent", '"Concurrent"')]


def test_delete_column_cache_leaves_unrelated_buckets_intact(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [
            ("status", "pending", '"Pending"'),
            ("coordinates", "home", "[1.5, 2]"),
            ("meta", "count", "3"),
        ],
    )

    manager.delete_column_cache("translate", "status")

    # Only the named bucket's rows are removed; unrelated buckets are untouched.
    assert _rows(manager) == [
        ("coordinates", "home", "[1.5, 2]"),
        ("meta", "count", "3"),
    ]


def test_delete_column_cache_missing_bucket_is_a_no_op(tmp_path, caplog):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(manager, "translate", [("status", "pending", '"Pending"')])

    with caplog.at_level(logging.INFO, logger="bynat.caching.Caching"):
        manager.delete_column_cache("translate", "missing")

    # A bucket that is not present deletes nothing and persists nothing; the sole
    # existing row is unchanged.
    assert "No cache found for column 'missing'" in caplog.text
    assert _rows(manager) == [("status", "pending", '"Pending"')]


def test_delete_column_cache_failed_transaction_is_retryable(tmp_path, monkeypatch, caplog):
    cache_dir = tmp_path / "cache"
    manager = CacheManager(cache_dir)
    _seed_cache_entries(
        manager,
        "translate",
        [("status", "pending", '"Pending"'), ("status", "done", '"Done"')],
    )
    # Load first so the baseline is captured with the real connection, then hold
    # it to prove the failed deletion does not advance it.
    manager.load_function_cache("translate")
    baseline_before = manager._baselines["translate"]

    real_connect = manager._connect_sqlite
    state = {"commits": 0}

    class CommitFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            return self.connection.execute(statement, parameters)

        def executemany(self, statement, parameters):
            return self.connection.executemany(statement, parameters)

        def commit(self):
            # The first commit belongs to _initialize_sqlite; the deletion
            # transaction's commit is the second and fails.
            state["commits"] += 1
            if state["commits"] == 2:
                raise sqlite3.OperationalError("commit failure")
            return self.connection.commit()

        def rollback(self):
            return self.connection.rollback()

        def close(self):
            return self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        manager, "_connect_sqlite", lambda: CommitFailingConnection(real_connect())
    )
    manager.delete_column_cache("translate", "status")

    assert "commit failure" in caplog.text
    # The in-memory bucket stays deleted, but the transaction rolled back, so the
    # baseline is unchanged and the rows survive for a later retry.
    assert "status" not in manager.caches["translate"]
    assert manager._baselines["translate"] is baseline_before
    assert _rows(manager) == [
        ("status", "done", '"Done"'),
        ("status", "pending", '"Pending"'),
    ]

    monkeypatch.undo()
    manager.save_function_cache("translate")

    # The retry re-diffs against the same unchanged baseline and now succeeds.
    assert _rows(manager) == []
    assert manager._baselines["translate"] == {}
