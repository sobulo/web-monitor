"""Cloud configuration is lazy, scoped, and entirely mocked offline."""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from google.cloud import secretmanager

from web_monitor.activityinfo.config import ActivityInfoConfig, ConfigurationError
from web_monitor.app import create_app


@pytest.fixture
def secrets(monkeypatch):
    for name in ('ACTIVITYINFO_API_TOKEN', 'GOOGLE_CLOUD_PROJECT', 'GAE_ENV', 'ACTIVITYINFO_SECRET_VERSION'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('ACTIVITYINFO_DATABASE_ID', 'testdatabase')
    client = MagicMock()
    client.__enter__.return_value = client
    client.access_secret_version.return_value = SimpleNamespace(payload=SimpleNamespace(data=b'private-test-token'))
    constructor = Mock(return_value=client)
    monkeypatch.setattr(secretmanager, 'SecretManagerServiceClient', constructor)
    return constructor, client


def cloud(monkeypatch):
    monkeypatch.setenv('GAE_ENV', 'standard')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'test-runtime-project')


def test_explicit_token_wins_even_in_cloud(secrets, monkeypatch):
    cloud(monkeypatch)
    monkeypatch.setenv('ACTIVITYINFO_API_TOKEN', 'explicit-token')
    assert ActivityInfoConfig.from_environment().api_token == 'explicit-token'
    secrets[0].assert_not_called()


@pytest.mark.parametrize('version', ['latest', '17'])
def test_runtime_project_secret_and_version_are_requested(secrets, monkeypatch, caplog, capsys, version):
    cloud(monkeypatch)
    monkeypatch.setenv('ACTIVITYINFO_SECRET_VERSION', version)
    result = ActivityInfoConfig.from_environment()
    assert result.api_token == 'private-test-token'
    secrets[1].access_secret_version.assert_called_once_with(
        request={'name': f'projects/test-runtime-project/secrets/activityinfo-api-token/versions/{version}'},
        timeout=10,
    )
    captured = capsys.readouterr()
    assert 'private-test-token' not in repr(result) + caplog.text + captured.out + captured.err


def test_local_missing_token_does_not_use_google_credentials(secrets, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'test-runtime-project')
    with pytest.raises(ConfigurationError):
        ActivityInfoConfig.from_environment()
    secrets[0].assert_not_called()


def test_explicit_empty_token_does_not_fall_back(secrets, monkeypatch):
    cloud(monkeypatch)
    monkeypatch.setenv('ACTIVITYINFO_API_TOKEN', '')
    with pytest.raises(ConfigurationError):
        ActivityInfoConfig.from_environment()
    secrets[0].assert_not_called()


def test_secret_error_is_sanitized(secrets, monkeypatch, caplog, capsys):
    cloud(monkeypatch)
    secrets[1].access_secret_version.side_effect = RuntimeError('secret-payload Authorization: private-test-token')
    with pytest.raises(ConfigurationError, match='could not be loaded') as error:
        ActivityInfoConfig.from_environment()
    assert error.value.__suppress_context__
    captured = capsys.readouterr()
    assert 'private-test-token' not in str(error.value) + caplog.text + captured.out + captured.err
    response = create_app().test_client().get('/')
    assert response.status_code == 503
    assert b'private-test-token' not in response.data
    assert 'private-test-token' not in caplog.text


def test_import_and_health_do_not_open_secret_client(secrets, monkeypatch):
    cloud(monkeypatch)
    import web_monitor.app
    importlib.reload(web_monitor.app)
    assert create_app().test_client().get('/health').status_code == 200
    secrets[0].assert_not_called()


def test_cloud_ignores_local_dotenv(secrets, monkeypatch, tmp_path):
    cloud(monkeypatch)
    env = tmp_path / '.env'
    env.write_text('ACTIVITYINFO_API_TOKEN=development-token\nACTIVITYINFO_DATABASE_ID=developmentdb\n')
    result = ActivityInfoConfig.from_environment(env_file=env)
    assert result.database_id == 'testdatabase' and result.api_token == 'private-test-token'


@pytest.mark.parametrize('name,value', [('GOOGLE_CLOUD_PROJECT', ''), ('ACTIVITYINFO_SECRET_VERSION', '../bad')])
def test_invalid_cloud_configuration_fails_before_api(secrets, monkeypatch, name, value):
    cloud(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError):
        ActivityInfoConfig.from_environment()
    secrets[0].assert_not_called()
