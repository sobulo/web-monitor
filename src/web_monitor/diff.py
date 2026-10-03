"""Pure comparison of in-memory observations."""

from web_monitor.models import ChangedItem, Snapshot, SnapshotDiff


def compare_snapshots(old: Snapshot, new: Snapshot) -> SnapshotDiff:
    """Compare canonical URL membership and normalized content hashes."""
    old_items = {item.canonical_url: item for item in old.items}
    new_items = {item.canonical_url: item for item in new.items}
    return SnapshotDiff(
        added=tuple(new_items[url] for url in sorted(new_items.keys() - old_items.keys())),
        removed=tuple(old_items[url] for url in sorted(old_items.keys() - new_items.keys())),
        changed=tuple(
            ChangedItem(old_items[url], new_items[url])
            for url in sorted(old_items.keys() & new_items.keys())
            if old_items[url].content_hash != new_items[url].content_hash
        ),
    )
