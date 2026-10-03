"""Pure URL and hash comparison, independent of crawling and Flask."""

import pytest

from web_monitor.diff import compare_snapshots
from web_monitor.models import Snapshot, SnapshotDiff, SnapshotItem


def item(path, content_hash="same", title=None):
    return SnapshotItem("https://example.invalid/" + path, title, content_hash)


def test_diff_orders_each_category_and_retains_before_after():
    old = Snapshot((item("r2"), item("c2"), item("same"), item("r1"), item("c1")))
    new = Snapshot((item("a2"), item("c2", "new"), item("same"), item("a1"), item("c1", "new")))
    diff = compare_snapshots(old, new)
    assert diff.added == (item("a1"), item("a2"))
    assert diff.removed == (item("r1"), item("r2"))
    assert [change.old for change in diff.changed] == [item("c1"), item("c2")]
    assert [change.new for change in diff.changed] == [item("c1", "new"), item("c2", "new")]
    assert compare_snapshots(Snapshot(tuple(reversed(old.items))), Snapshot(tuple(reversed(new.items)))) == diff


def test_same_hash_is_unchanged_even_if_metadata_differs():
    assert compare_snapshots(Snapshot((item("a", title="Old"),)), Snapshot((item("a", title="New"),))) == SnapshotDiff()


def test_empty_snapshot_boundaries():
    snapshot = Snapshot((item("a"),))
    assert compare_snapshots(Snapshot(), Snapshot()) == SnapshotDiff()
    assert compare_snapshots(Snapshot(), snapshot).added == snapshot.items
    assert compare_snapshots(snapshot, Snapshot()).removed == snapshot.items


def test_duplicate_canonical_urls_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        Snapshot((item("a#one"), item("a#two")))
