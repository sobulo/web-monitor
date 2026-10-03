"""Historical semantics at the persistence boundary; no HTTP or credentials."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from web_monitor.activityinfo.persistence import CrawlRecord, SnapshotRecord, StoredSnapshot
from web_monitor.diff import compare_snapshots
from web_monitor.history import HistoryIntegrityError, HistoryService
from web_monitor.models import Snapshot, SnapshotDiff, SnapshotItem


def day(number):
    return date(2026, 9, number)


def moment(number):
    return datetime(2026, 9, number, 12, tzinfo=timezone.utc)


@pytest.fixture
def history():
    a = Snapshot((SnapshotItem('https://example.invalid/a', 'A', 'a'),))
    b = Snapshot((SnapshotItem('https://example.invalid/a', 'B', 'b'),
                  SnapshotItem('https://example.invalid/b', 'New', 'c')))
    c = a  # Reversion proves endpoint comparison is not accumulated changes.
    snapshots = {name: StoredSnapshot(SnapshotRecord(name, 'site', moment(14), moment(14), len(value.items)), value, ())
                 for name, value in zip('ABC', (a, b, c))}
    records = []
    for number, status, current, previous in (
        (14, 'initial', 'A', None), (15, 'no_change', 'A', 'A'),
        (16, 'no_change', 'A', 'A'), (17, 'changed', 'B', 'A'),
        (18, 'no_change', 'B', 'B'), (19, 'changed', 'C', 'B'),
        (20, 'error', None, 'C'),
    ):
        diff = compare_snapshots(snapshots[previous].snapshot, snapshots[current].snapshot) if status == 'changed' else SnapshotDiff()
        records.append(CrawlRecord(f'crawl{number}', 'site', moment(number), status, current, previous,
                                   len(diff.added), len(diff.removed), len(diff.changed),
                                   len(snapshots[current].snapshot.items) if current else 0))
    store = Mock()
    store.list_crawls.return_value = tuple(reversed(records))
    store.load_snapshot.side_effect = snapshots.__getitem__
    return HistoryService(store), store, records, snapshots


@pytest.mark.parametrize('number,reference', [(14,'A'),(15,'A'),(16,'A'),(17,'B'),(18,'B'),(19,'C'),(20,'C')])
def test_state(history, number, reference):
    service, store, _, snapshots = history
    result = service.state_on_date('site', day(number))
    assert result.crawl.snapshot_reference == reference
    assert result.snapshot == snapshots[reference].snapshot
    store.load_snapshot.assert_called_once_with(reference)


@pytest.mark.parametrize('number,refs', [(14,()),(16,()),(17,('A','B')),(19,('B','C')),(20,())])
def test_changes_on(history, number, refs):
    results = history[0].changes_on_date('site', day(number))
    assert len(results) == bool(refs)
    if refs:
        result = results[0]
        assert (result.crawl.previous_snapshot_reference, result.crawl.snapshot_reference) == refs
        assert result.diff == compare_snapshots(result.previous_snapshot, result.snapshot)
        assert (result.added_count, result.removed_count, result.changed_count) == (
            result.crawl.added_count, result.crawl.removed_count, result.crawl.changed_count)


@pytest.mark.parametrize('start,end,refs', [(14,16,('A','A')),(14,17,('A','B')),(17,19,('B','C')),(14,19,('A','C'))])
def test_endpoints(history, start, end, refs):
    service, store, _, snapshots = history
    result = service.changes_between_dates('site', day(start), day(end))
    assert result.diff == compare_snapshots(*(snapshots[ref].snapshot for ref in refs))
    assert (result.start.crawl.snapshot_reference, result.end.crawl.snapshot_reference) == refs
    assert not result.missing_endpoints
    store.list_crawls.assert_called_once_with('site')
    if refs in [('A','A'), ('A','C')]:
        assert result.counts == (0, 0, 0)


def test_latest_and_recent(history):
    service, store, records, _ = history
    latest = service.latest_change('site')
    assert latest.crawl.crawled_at == moment(19)
    assert latest.crawl.previous_snapshot_reference == 'B'
    assert latest.crawl.snapshot_reference == 'C'
    store.load_snapshot.reset_mock()
    recent = service.recent_crawls('site')
    assert [row.status for row in recent] == [row.status for row in reversed(records)]
    assert len(service.recent_crawls('site', 2)) == 2
    store.load_snapshot.assert_not_called()


@pytest.mark.parametrize('selection', [[], [6], [0]])
def test_empty_errors_initial(history, selection):
    service, store, records, _ = history
    store.list_crawls.return_value = tuple(records[index] for index in selection)
    assert service.latest_change('site') is None
    assert service.changes_on_date('site', day(14)) == ()
    assert service.state_on_date('site', day(20)).available == (selection == [0])
    assert not service.state_on_date('site', day(13)).available
    result = service.changes_between_dates('site', day(12), day(13))
    assert result.missing_endpoints == ('start', 'end') and result.diff is None
    result = service.changes_between_dates('site', day(13), day(20))
    assert result.missing_endpoints == (('start',) if selection == [0] else ('start', 'end'))


def test_multiple_changes(history):
    service, store, records, _ = history
    store.list_crawls.return_value = (records[3], replace(records[5], crawled_at=moment(17) + timedelta(hours=1)))
    assert [result.crawl.snapshot_reference for result in service.changes_on_date('site', day(17))] == ['B','C']


def test_counts_disagree(history):
    service, store, records, _ = history
    store.list_crawls.return_value = (replace(records[3], added_count=999),)
    with pytest.raises(HistoryIntegrityError, match='counts'):
        service.latest_change('site')


@pytest.mark.parametrize('limit', [0, -1, 1001, True, 1.5, '20', None])
def test_limits(history, limit):
    with pytest.raises(ValueError, match='limit'):
        history[0].recent_crawls('site', limit)
    history[1].list_crawls.assert_not_called()


def test_invalid_dates(history):
    with pytest.raises(ValueError, match='precede'):
        history[0].changes_between_dates('site', day(19), day(14))
    with pytest.raises(ValueError, match='calendar date'):
        history[0].state_on_date('site', moment(14))


def test_ties(history):
    service, store, records, _ = history
    first = records[0]
    duplicate = replace(first, record_id='zzz')
    store.list_crawls.return_value = (duplicate, first)
    assert service.state_on_date('site', day(14)).crawl.reference == 'zzz'
    assert [row.reference for row in service.recent_crawls('site')] == ['zzz', first.record_id]
    store.list_crawls.return_value = (first, replace(duplicate, snapshot_id='B'))
    with pytest.raises(HistoryIntegrityError, match='Ambiguous state'):
        service.state_on_date('site', day(14))
    store.list_crawls.return_value = (records[3], replace(records[5], crawled_at=moment(17)))
    with pytest.raises(HistoryIntegrityError, match='Ambiguous transition'):
        service.latest_change('site')
    with pytest.raises(HistoryIntegrityError, match='Ambiguous transition'):
        service.changes_on_date('site', day(17))


def test_utc_boundary_and_fractional_seconds(history):
    service, store, records, _ = history
    boundary = datetime(2026, 9, 18, tzinfo=timezone.utc)
    last = replace(records[3], crawled_at=boundary-timedelta(microseconds=1))
    next_day = replace(records[5], crawled_at=boundary.astimezone(timezone(timedelta(hours=2))))
    store.list_crawls.return_value = (next_day, last, records[0])
    assert service.state_on_date('site', day(17)).crawl.snapshot_reference == 'B'
    assert service.state_on_date('site', day(18)).crawl.snapshot_reference == 'C'
    assert len(service.changes_on_date('site', day(17))) == 1
    assert service.changes_on_date('site', day(18))[0].crawl.crawled_at == boundary


@pytest.mark.parametrize('change,message', [({'snapshot_id':None},'missing'),({'monitored_site_id':'other'},'another site'),({'crawled_at':datetime(2026,9,14)},'timezone')])
def test_bad_crawls(history, change, message):
    service, store, records, _ = history
    store.list_crawls.return_value = (replace(records[0], **change),)
    with pytest.raises(HistoryIntegrityError, match=message):
        service.state_on_date('site', day(20))


def test_wrong_snapshot_site(history):
    service, store, _, snapshots = history
    saved = snapshots['C']
    store.load_snapshot.side_effect = None
    store.load_snapshot.return_value = replace(saved, record=replace(saved.record, monitored_site_id='other'))
    with pytest.raises(HistoryIntegrityError, match='wrong state or site'):
        service.state_on_date('site', day(20))


@pytest.mark.parametrize('arguments,expected', [
    (['state','site','2026-09-16'], 'Snapshot A: 1 items'),
    (['state','site','2026-09-13'], 'No state available'),
    (['changes-on','site','2026-09-17'], 'Added 1, removed 0, changed 1'),
    (['changes-on','site','2026-09-16'], 'No changes'),
    (['changes-between','site','2026-09-14','2026-09-19'], 'Added 0, removed 0, changed 0'),
    (['changes-between','site','2026-09-13','2026-09-19'], 'No state available for: start'),
    (['latest-change','site'], 'B -> C'),
    (['recent','site','--limit','1'], 'error pages=0'),
])
def test_command(history, monkeypatch, capsys, arguments, expected):
    from web_monitor import history_command
    from web_monitor.activityinfo.config import ActivityInfoConfig
    monkeypatch.setattr(history_command.ActivityInfoConfig, 'from_environment', Mock(return_value=ActivityInfoConfig('database','private-test-secret')))
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(history_command, 'ActivityInfoClient', Mock(return_value=client))
    monkeypatch.setattr(history_command, 'ActivityInfoPersistence', Mock(return_value=history[1]))
    assert history_command.main(arguments) == 0
    output = capsys.readouterr()
    assert expected in output.out
    assert 'private-test-secret' not in output.out + output.err


def test_command_failure_redacts_remote_details(history, monkeypatch, capsys):
    from web_monitor import history_command
    from web_monitor.activityinfo.client import ActivityInfoError
    monkeypatch.setattr(history_command.ActivityInfoConfig, 'from_environment',
                        Mock(side_effect=ActivityInfoError('GET','/sensitive',403,'private-test-secret')))
    assert history_command.main(['latest-change','site']) == 2
    output = capsys.readouterr()
    assert '403' in output.err and 'private-test-secret' not in output.err
    assert not output.out
