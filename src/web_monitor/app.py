"""Flask/Jinja presentation and a platform-protected scheduler handler."""

from contextlib import ExitStack
from datetime import date
from pathlib import Path
import logging
import os
import re

from flask import Flask, abort, g, render_template, request
from flask.logging import default_handler
from werkzeug.exceptions import HTTPException

from web_monitor.activityinfo.client import ActivityInfoError
from web_monitor.web_services import configured_services
from web_monitor.scheduler_identity import InvalidInvocation, SchedulerInvocation


def create_app(config: dict | None = None, *, services=None) -> Flask:
    """Construct without credentials; inject WebServices or a context factory.

    Real dependencies are opened lazily per request and closed at teardown.
    /health and static assets never construct an external client.
    """
    app = Flask(__name__)
    app.config.from_mapping(ENV_FILE=Path.cwd() / '.env', SERVICES_FACTORY=None)
    app.config.from_mapping({name: os.environ.get(name, '') for name in (
        'GOOGLE_CLOUD_PROJECT', 'SCHEDULER_LOCATION', 'SCHEDULER_JOB_NAME',
    )})
    scheduler_logger = logging.getLogger('web_monitor.scheduling')
    scheduler_logger.setLevel(logging.INFO)
    if not scheduler_logger.handlers:
        scheduler_logger.addHandler(default_handler)
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

    @app.post('/tasks/monitor')
    def scheduled_monitoring():
        # These headers identify work, not its caller. app.yaml login: admin
        # provides the documented App Engine-target authorization boundary.
        try:
            invocation = SchedulerInvocation.from_headers(
                request.headers, project=app.config['GOOGLE_CLOUD_PROJECT'],
                location=app.config['SCHEDULER_LOCATION'],
                job_name=app.config['SCHEDULER_JOB_NAME'],
            )
        except InvalidInvocation as error:
            # Only fixed error categories and shape flags, never header values.
            job = request.headers.get('X-CloudScheduler-JobName', '')
            time_value = request.headers.get('X-CloudScheduler-ScheduleTime', '')
            app.logger.warning(
                'Scheduler identity rejected: category=%s job_present=%s short_job=%s time_present=%s',
                str(error), bool(job), '/' not in job if job else False, bool(time_value),
            )
            return {'error': 'Invalid scheduler invocation'}, 400
        scheduler = dependencies().scheduler
        if scheduler is None:
            abort(503)
        results = scheduler.run(invocation)
        return {'invocation': invocation.key, 'completed': len(results),
                'reused': sum(result.reused for result in results)}

    @app.get('/')
    def index():
        return render_template('index.html', sites=dependencies().sites.list_monitored_sites())

    @app.get('/reports')
    def reports():
        services = dependencies()
        overview = services.reporting.overview()
        metadata = None
        provider_error = False
        try:
            if services.report_provider is not None:
                metadata = services.report_provider.published_reports()
                metadata.validate()
        except Exception as error:
            app.logger.error('Report provider unavailable: category=%s', type(error).__name__)
            provider_error = True
            metadata = None
        return render_template('reports.html', overview=overview, published=metadata,
                               provider_error=provider_error)

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
        # App Engine targets retry HTTP 503 outside the configured retry count.
        # Use 500 for failed scheduled work so the conservative policy applies.
        code = 500 if request.path == '/tasks/monitor' else 503
        return render_template('error.html', title='History unavailable',
                               message='Monitoring data could not be loaded. Please try again later.'), code

    return app
