"""Flask/Jinja presentation over application services; no monitoring writes."""

from contextlib import ExitStack
from datetime import date
from pathlib import Path
import re

from flask import Flask, abort, g, render_template, request
from werkzeug.exceptions import HTTPException

from web_monitor.activityinfo.client import ActivityInfoError
from web_monitor.web_services import configured_services


def create_app(config: dict | None = None, *, services=None) -> Flask:
    """Construct without credentials; inject WebServices or a context factory.

    Real dependencies are opened lazily per request and closed at teardown.
    /health and static assets never construct an external client.
    """
    app = Flask(__name__)
    app.config.from_mapping(ENV_FILE=Path.cwd() / '.env', SERVICES_FACTORY=None)
    if config:
        app.config.update(config)

    def dependencies():
        if services is not None:
            return services
        if 'web_services' not in g:
            g.service_stack = ExitStack()
            factory = app.config['SERVICES_FACTORY']
            context = factory() if factory else configured_services(Path(app.config['ENV_FILE']))
            g.web_services = g.service_stack.enter_context(context)
        return g.web_services

    @app.teardown_appcontext
    def close_services(error):
        stack = g.pop('service_stack', None)
        if stack is not None:
            stack.close()

    def site_record(site_id):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,31}', site_id):
            abort(404)
        try:
            return dependencies().sites.read_monitored_site(site_id)
        except ActivityInfoError as error:
            if error.status == 404:
                abort(404)
            raise
        except KeyError:
            abort(404)

    def calendar_date(name):
        value = request.args.get(name, '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @app.get('/health')
    def health():
        return {'service': 'web-monitor', 'status': 'ok'}

    @app.get('/')
    def index():
        return render_template('index.html', sites=dependencies().sites.list_monitored_sites())

    @app.get('/sites/<site_id>')
    def overview(site_id):
        site = site_record(site_id)
        history = dependencies().history
        return render_template('overview.html', site=site,
                               crawls=history.recent_crawls(site_id),
                               latest=history.latest_change(site_id))

    @app.get('/sites/<site_id>/state')
    @app.get('/sites/<site_id>/changes-on')
    @app.get('/sites/<site_id>/compare')
    def query(site_id):
        site = site_record(site_id)
        mode = request.path.rsplit('/', 1)[-1]
        names = ('start', 'end') if mode == 'compare' else ('date',)
        values = {name: calendar_date(name) for name in names}
        error = None
        result = None
        if not all(values.values()):
            error = 'Choose a valid date for each required field (YYYY-MM-DD).'
        elif mode == 'compare' and values['end'] < values['start']:
            error = 'End date must be on or after start date.'
        else:
            history = dependencies().history
            if mode == 'state':
                result = history.state_on_date(site_id, values['date'])
            elif mode == 'changes-on':
                result = history.changes_on_date(site_id, values['date'])
            else:
                result = history.changes_between_dates(site_id, values['start'], values['end'])
        # Navigation without a query string opens a blank form; incomplete
        # submitted forms return a helpful validation error with HTTP 400.
        submitted = bool(request.query_string)
        return render_template('query.html', site=site, mode=mode, result=result,
                               error=error if submitted else None, values=values), (400 if error and submitted else 200)

    @app.get('/sites/<site_id>/latest-change')
    def latest_change(site_id):
        site = site_record(site_id)
        return render_template('latest.html', site=site,
                               latest=dependencies().history.latest_change(site_id))

    @app.errorhandler(404)
    def not_found(error):
        return render_template('error.html', title='Not found',
                               message='This Monitored Site or page could not be found.'), 404

    @app.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error, HTTPException):
            return render_template('error.html', title='Request unavailable',
                                   message='This request could not be completed.'), error.code
        # Deliberately exclude exception text, response bodies, URLs, and headers.
        status = error.status if isinstance(error, ActivityInfoError) else None
        app.logger.error('Web query failed: category=%s upstream_status=%s',
                         type(error).__name__, status)
        return render_template('error.html', title='History unavailable',
                               message='Monitoring data could not be loaded. Please try again later.'), 503

    return app
