import json

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
