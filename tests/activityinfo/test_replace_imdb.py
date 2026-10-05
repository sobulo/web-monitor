"""Exact preconditions and the one-time mutation boundary; no external services."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from web_monitor.activityinfo.bootstrap import DEMO_SITES
from web_monitor.activityinfo.persistence import CrawlRecord, SnapshotRecord, SnapshotItemRecord
from web_monitor.activityinfo.replace_imdb import (
    MaintenanceError, NEW_SITE, OLD_SITE, PRODUCTION_DATABASE_ID, production_config,
    replace_imdb, seed_record,
)
from web_monitor.models import SnapshotItem

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.ids = {name: name for name in ('monitored_site', 'crawl', 'snapshot', 'snapshot_item')}
        self.data = {name: {} for name in self.ids}
        self.client = SimpleNamespace(database_id=PRODUCTION_DATABASE_ID,
                                      query_rows=self.query_rows, update_records=Mock(side_effect=self.update))
        for definition in [d for d in DEMO_SITES if d != NEW_SITE] + [OLD_SITE]:
            record = seed_record(PRODUCTION_DATABASE_ID, definition)
            self.data['monitored_site'][record.record_id] = record
        self.old = seed_record(PRODUCTION_DATABASE_ID, OLD_SITE)
        for identity in ('crawl1', 'crawl2'):
            self.data['crawl'][identity] = CrawlRecord(identity, self.old.record_id, NOW, 'error')
        good = next(s for s in self.data['monitored_site'].values() if s.name == 'Python.org')
        self.data['snapshot']['state1'] = SnapshotRecord('state1', good.record_id, NOW, NOW, 1)
        self.data['snapshot_item']['item1'] = SnapshotItemRecord(
            'item1', 'state1', SnapshotItem('https://www.python.org/', 'Python', 'abc'))
        self.data['crawl']['goodcrawl'] = CrawlRecord('goodcrawl', good.record_id, NOW, 'initial', 'state1')
        self.create_monitored_site = Mock(side_effect=self.create)

    def query_rows(self, form, columns):
        return [{'record_id': key} for key in sorted(self.data[form])]

    def update(self, changes):
        for change in changes:
            assert change['deleted'] is True and change['fields'] == {}
            del self.data[change['formId']][change['recordId']]

    def create(self, record):
        assert record.record_id not in self.data['monitored_site']
        self.data['monitored_site'][record.record_id] = record

    def read_monitored_site(self, identity):
        return self.data['monitored_site'][identity]

    def read_crawl(self, identity):
        return self.data['crawl'][identity]

    def read_snapshot(self, identity):
        return self.data['snapshot'][identity]

    def read_snapshot_item(self, identity):
        return self.data['snapshot_item'][identity]


def test_replacement_preserves_all_good_records_and_refuses_second_run():
    store = Store()
    before = deepcopy(store.data)
    messages = []
    result = replace_imdb(store, emit=messages.append, guard=lambda: None)
    assert result['deleted_crawl_ids'] == ('crawl1', 'crawl2')
    assert result['deleted_site_id'] == store.old.record_id
    assert [r.name for r in store.data['monitored_site'].values()].count('Planet Python') == 1
    assert store.data['snapshot'] == before['snapshot']
    assert store.data['snapshot_item'] == before['snapshot_item']
    assert store.data['crawl'] == {'goodcrawl': before['crawl']['goodcrawl']}
    assert store.client.update_records.call_count == 2
    assert store.client.update_records.call_args_list[0].args[0] == [
        {'formId': 'crawl', 'recordId': identity, 'deleted': True, 'fields': {}}
        for identity in ('crawl1', 'crawl2')]
    assert store.client.update_records.call_args_list[1].args[0][0]['recordId'] == store.old.record_id
    store.create_monitored_site.assert_called_once_with(seed_record(PRODUCTION_DATABASE_ID, NEW_SITE))
    assert 'verified_before' in messages[0] and 'verified_after' in messages[-1]
    with pytest.raises(MaintenanceError):
        replace_imdb(store, guard=lambda: None)
    assert store.client.update_records.call_count == 2


@pytest.mark.parametrize('mismatch', [
    'name', 'url', 'identity', 'depth', 'one_crawl', 'three_crawls', 'successful',
    'snapshot_reference', 'snapshot', 'orphan_item', 'planet_history', 'other_site', 'database',
])
def test_every_mismatch_stops_before_any_mutation(mismatch):
    store = Store()
    old = store.old
    if mismatch == 'name':
        store.data['monitored_site'][old.record_id] = replace(old, name='IMDb')
    elif mismatch == 'url':
        store.data['monitored_site'][old.record_id] = replace(old, site=replace(old.site, root_url='https://www.imdb.com/'))
    elif mismatch == 'identity':
        del store.data['monitored_site'][old.record_id]
        store.data['monitored_site']['wrongid'] = replace(old, record_id='wrongid')
    elif mismatch == 'depth':
        store.data['monitored_site'][old.record_id] = replace(old, site=replace(old.site, crawl_depth=1))
    elif mismatch == 'one_crawl':
        del store.data['crawl']['crawl2']
    elif mismatch == 'three_crawls':
        store.data['crawl']['crawl3'] = replace(store.data['crawl']['crawl1'], record_id='crawl3')
    elif mismatch == 'successful':
        store.data['crawl']['crawl1'] = replace(store.data['crawl']['crawl1'], status='no_change')
    elif mismatch == 'snapshot_reference':
        store.data['crawl']['crawl1'] = replace(store.data['crawl']['crawl1'], previous_snapshot_id='state1')
    elif mismatch == 'snapshot':
        store.data['snapshot']['state1'] = replace(store.data['snapshot']['state1'], monitored_site_id=old.record_id)
    elif mismatch == 'orphan_item':
        store.data['snapshot_item']['item1'] = replace(store.data['snapshot_item']['item1'], snapshot_id='missing')
    elif mismatch == 'planet_history':
        store.data['crawl']['goodcrawl'] = replace(store.data['crawl']['goodcrawl'], monitored_site_id=seed_record(PRODUCTION_DATABASE_ID, NEW_SITE).record_id)
    elif mismatch == 'other_site':
        store.data['monitored_site']['other'] = replace(old, record_id='other')
    elif mismatch == 'database':
        store.client.database_id = 'wrongdatabase'
    before = deepcopy(store.data)
    with pytest.raises(MaintenanceError):
        replace_imdb(store, emit=lambda s: None, guard=lambda: None)
    assert store.data == before
    store.client.update_records.assert_not_called()
    store.create_monitored_site.assert_not_called()


def test_preflight_change_blocks_deletion():
    store = Store()
    def concurrent_change():
        store.data['crawl']['crawl3'] = replace(store.data['crawl']['crawl1'], record_id='crawl3')
    with pytest.raises(MaintenanceError, match='changed during preflight'):
        replace_imdb(store, emit=lambda s: None, guard=concurrent_change)
    store.client.update_records.assert_not_called()


def test_unacknowledged_deletion_stops_before_site_deletion():
    store = Store()
    store.client.update_records.side_effect = None
    with pytest.raises(MaintenanceError, match='Crawl deletion read-back'):
        replace_imdb(store, emit=lambda s: None, guard=lambda: None)
    assert store.client.update_records.call_count == 1
    assert store.old.record_id in store.data['monitored_site']
    store.create_monitored_site.assert_not_called()


def test_secret_access_uses_pinned_version_without_dotenv_or_payload_output(tmp_path, monkeypatch, capsys):
    app = tmp_path / 'app.yaml'
    app.write_text('env_variables:\n  ACTIVITYINFO_DATABASE_ID: "cfx7vfgmuttn7403"\n  ACTIVITYINFO_SECRET_VERSION: "1"\n')
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b'private-production-token'))
    monkeypatch.setattr('web_monitor.activityinfo.replace_imdb.subprocess.run', run)
    config = production_config(app, '/approved/gcloud')
    assert config.database_id == PRODUCTION_DATABASE_ID
    assert config.api_token == 'private-production-token'
    assert run.call_args.args[0] == ['/approved/gcloud', 'secrets', 'versions', 'access', '1',
                                     '--secret=activityinfo-api-token', '--quiet']
    assert 'private-production-token' not in repr(config) + capsys.readouterr().out
    run.return_value = SimpleNamespace(returncode=1, stdout=b'private-production-token')
    with pytest.raises(MaintenanceError, match='output suppressed'):
        production_config(app, '/approved/gcloud')


def test_bootstrap_demo_list_is_exact():
    assert DEMO_SITES == (
        ('Python.org', 'https://www.python.org/'),
        ('Python Insider', 'https://blog.python.org/'),
        ('Planet Python', 'https://planetpython.org/'),
        ('IANA Reserved Domains', 'https://www.iana.org/domains/reserved'),
    )
