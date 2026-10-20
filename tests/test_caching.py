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


def test_corrupt_cache_is_reported_and_replaced_with_empty_dict(tmp_path, capsys):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "broken_cache.json"
    cache_file.write_text("{not valid json", encoding="utf-8")

    manager = CacheManager(cache_dir)
    assert manager.load_function_cache("broken") == {}
    assert "corrupted or unreadable" in capsys.readouterr().out


def test_save_unserializable_value_reports_error_without_raising(tmp_path, capsys):
    manager = CacheManager(tmp_path)
    manager.get_column_cache("function", "column")["bad"] = object()

    manager.save_function_cache("function")

    assert "Could not save cache file" in capsys.readouterr().out


def test_cache_directory_is_created_on_save_not_construction(tmp_path):
    cache_dir = tmp_path / "not-yet-created"
    CacheManager(cache_dir)
    assert not cache_dir.exists()

    manager = CacheManager(cache_dir)
    manager.save_function_cache("function")
    assert (cache_dir / "function_cache.json").exists()


def test_failed_save_keeps_previous_cache_file_intact(tmp_path, capsys):
    manager = CacheManager(tmp_path)
    manager.get_column_cache("function", "column")["good"] = "value"
    manager.save_function_cache("function")
    cache_file = tmp_path / "function_cache.json"
    previous = cache_file.read_text(encoding="utf-8")

    manager.get_column_cache("function", "column")["bad"] = object()
    manager.save_function_cache("function")

    assert "Could not save cache file" in capsys.readouterr().out
    assert cache_file.read_text(encoding="utf-8") == previous
    assert [path.name for path in tmp_path.iterdir()] == ["function_cache.json"]


def test_interrupted_replace_leaves_no_partial_or_temp_files(tmp_path, monkeypatch, capsys):
    import os

    manager = CacheManager(tmp_path)
    manager.get_column_cache("function", "column")["key"] = "value"

    def failing_replace(src, dst):
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", failing_replace)
    manager.save_function_cache("function")

    assert "simulated interruption" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
