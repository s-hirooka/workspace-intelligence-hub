from app.services.indexer import classify_changes


def test_hash_unchanged_is_not_reembedded():
    added, updated, unchanged, deleted = classify_changes({"a": "same"}, {"a": "same"})
    assert unchanged == {"a"}
    assert not (added or updated or deleted)


def test_new_file():
    assert classify_changes({"new": "h"}, {})[0] == {"new"}


def test_updated_file():
    assert classify_changes({"a": "new"}, {"a": "old"})[1] == {"a"}


def test_deleted_file():
    assert classify_changes({}, {"gone": "old"}, set())[3] == {"gone"}


def test_unreadable_candidate_is_not_deleted():
    assert classify_changes({}, {"kept": "old"}, {"kept"})[3] == set()
