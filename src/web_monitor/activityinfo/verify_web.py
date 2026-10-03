"""Explicit local HTTP verification backed by real development ActivityInfo."""

import argparse
from html import escape
from pathlib import Path
import sys
from threading import Thread

import requests
from werkzeug.serving import make_server, WSGIRequestHandler

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import ActivityInfoPersistence
from web_monitor.activityinfo.verify_history import verify as verify_history
from web_monitor.activityinfo.verify_lifecycle import require
from web_monitor.app import create_app


class QuietRequestHandler(WSGIRequestHandler):
    def log(self, type, message, *args):
        """Verification reports assertions rather than request URLs."""


def verify(env_file: Path):
    config = ActivityInfoConfig.from_environment(env_file=env_file)
    with ActivityInfoClient(config) as client:
        seeds = ActivityInfoPersistence(client).list_monitored_sites()
    require(len(seeds) == 4, 'Expected four seeded sites in development database')
    app = create_app({'ENV_FILE':env_file})
    server = make_server('127.0.0.1', 0, app, request_handler=QuietRequestHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = requests.Session()
    session.trust_env = False
    base = f'http://127.0.0.1:{server.server_port}'

    def page(path, expected):
        response = session.get(base + path, timeout=120)
        require(response.status_code == 200, 'Real web page failed')
        require(expected in response.text, 'Expected web content missing')
        require(config.api_token not in response.text, 'Secret exposed in web response')
        return response.text

    def inspect_history(site_id):
        root = '/sites/' + site_id
        page(root, 'Synthetic development failure')
        page(root + '/state?date=2026-09-16', 'Baseline')
        page(root + '/state?date=2026-09-13', 'No state available')
        page(root + '/changes-on?date=2026-09-17', 'Modified')
        page(root + '/changes-on?date=2026-09-16', 'No changes recorded on this date.')
        page(root + '/compare?start=2026-09-14&end=2026-09-17', 'Old hash')
        page(root + '/compare?start=2026-09-14&end=2026-09-19', 'No differences between these states.')
        page(root + '/latest-change', 'New hash')
        print('Real local HTTP overview and all historical query pages passed.', flush=True)

    try:
        homepage = page('/', 'Monitored Sites')
        for site in seeds:
            require(escape(site.name) in homepage, 'Seeded site missing from homepage')
        page('/sites/' + seeds[0].record_id, escape(seeds[0].name))
        page('/health', 'web-monitor')
        page('/static/style.css', ':focus-visible')
        print('Real homepage lists all four seeds; seeded overview, health, and CSS passed.', flush=True)
        verify_history(env_file, inspect_site=inspect_history)
    finally:
        session.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path.cwd() / '.env')
    args = parser.parse_args()
    try:
        verify(args.env_file)
        return 0
    except ActivityInfoError as error:
        print(f'STOP: ActivityInfo verification failed (HTTP {error.status or "unavailable"}).', file=sys.stderr)
    except (ValueError, requests.RequestException):
        print('STOP: web verification failed; inspect sanitized application logs.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
