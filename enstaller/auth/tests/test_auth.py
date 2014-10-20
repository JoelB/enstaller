from egginst._compat import TestCase

import base64
import json
import os.path
import shutil
import tempfile

from mock import patch

import enstaller.config

from enstaller.auth import DUMMY_USER, UserInfo
from enstaller.auth.auth_managers import (BroodAuthenticator,
                                          LegacyCanopyAuthManager,
                                          OldRepoAuthManager)
from enstaller.config import Configuration, write_default_config
from enstaller.session import Session
from enstaller.errors import (AuthFailedError, EnstallerException,
                              InvalidConfiguration)
from enstaller.tests.common import (DummyAuthenticator, fake_keyring,
                                    R_JSON_NOAUTH_RESP, R_JSON_AUTH_RESP)
from enstaller.vendor import requests, responses


basic_user = UserInfo(True, first_name="Jane", last_name="Doe", has_subscription=True)
free_user = UserInfo(True, first_name="John", last_name="Smith", has_subscription=False)
anon_user = UserInfo(False)
old_auth_user = DUMMY_USER

def compute_creds(username, password):
    s = "{0}:{1}".format(username, password)
    return base64.b64encode(s.encode("ascii")).rstrip()

AUTH_API_URL = 'https://api.enthought.com/accounts/user/info/'

FAKE_USER = "john.doe"
FAKE_PASSWORD = "fake_password"
FAKE_CREDS = compute_creds(FAKE_USER, FAKE_PASSWORD)


@fake_keyring
class CheckedChangeAuthTestCase(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = os.path.join(self.d, "enstaller4rc")
        self.session = Session(DummyAuthenticator(), self.d)

    def tearDown(self):
        self.session.close()
        shutil.rmtree(self.d)

    @responses.activate
    def test_no_acct(self):
        # Given
        url = "https://acme.com"
        auth_url = url + "/accounts/user/info/"

        config = Configuration()
        config.update(store_url=url)

        def callback(request):
            if auth != ("valid_user", "valid_password"):
                return (403, {}, "")
            return (200, {}, json.dumps(R_JSON_AUTH_RESP))

        responses.add_callback(responses.GET, auth_url, callback)

        write_default_config(self.f)

        with Session.from_configuration(config) as session:
            config = Configuration()
            config.update(store_url=url)

            auth = ("invalid_user", "invalid_password")

            with self.assertRaises(AuthFailedError):
                usr = config._checked_change_auth(auth, session, self.f)

            config = Configuration()
            auth = ("valid_user", "valid_password")
            usr = config._checked_change_auth(auth, session, self.f)

            self.assertTrue(usr.is_authenticated)
            self.assertEqual(config.auth, ("valid_user", "valid_password"))

    def test_remote_success(self):
        write_default_config(self.f)

        config = Configuration()
        auth = ("usr", "password")
        session = Session(DummyAuthenticator(old_auth_user), self.d)

        with session:
            usr = config._checked_change_auth(auth, session, self.f)
            self.assertEqual(usr, UserInfo(True))

    def test_nones(self):
        config = Configuration()

        with self.assertRaises(InvalidConfiguration):
            config.set_auth(None, None)


class AuthManagerBase(TestCase):
    klass = None

    def setUp(self):
        self.prefix = tempfile.mkdtemp()

        self.config = Configuration()
        self.config.update(use_webservice=False,
                           indexed_repositories=["http://acme.com"])
        self.session = Session(self.klass.from_configuration(self.config),
                               self.prefix)

    def tearDown(self):
        shutil.rmtree(self.prefix)
        self.session.close()


class TestOldReposAuthManager(AuthManagerBase):
    klass = OldRepoAuthManager

    @responses.activate
    def test_from_configuration(self):
        # Given
        responses.add(responses.HEAD, self.config.indices[0][0], status=200,
                      body=json.dumps(R_JSON_AUTH_RESP))
        authenticator = OldRepoAuthManager.from_configuration(self.config)
        session = Session(authenticator, self.prefix)

        # When
        with session:
            session.authenticate((FAKE_USER, FAKE_PASSWORD))

        # Then
        self.assertEqual(session._raw.auth, (FAKE_USER, FAKE_PASSWORD))

    @responses.activate
    def test_simple(self):
        # Given
        responses.add(responses.HEAD, self.config.indices[0][0], status=200,
                      body=json.dumps(R_JSON_AUTH_RESP))

        # When
        # Then no exception
        self.session.authenticate((FAKE_USER, FAKE_PASSWORD))

    def test_connection_failure(self):
        with patch.object(self.session._raw, "head",
                          side_effect=requests.exceptions.ConnectionError):
            with self.assertRaises(AuthFailedError):
                self.session.authenticate((FAKE_USER, FAKE_PASSWORD))

    @responses.activate
    def test_auth_failure_404(self):
        # Given
        auth = ("nono", "le petit robot")
        responses.add(responses.HEAD, self.config.indices[0][0],
                      body="", status=404,
                      content_type='application/json')

        # When/Given
        with self.assertRaises(AuthFailedError):
            self.session.authenticate(auth)

    @responses.activate
    def test_auth_failure_50x(self):
        # Given
        auth = ("nono", "le petit robot")
        responses.add(responses.HEAD, self.config.indices[0][0],
                      status=503, content_type='application/json')

        # When/Given
        with self.assertRaises(AuthFailedError):
            self.session.authenticate(auth)

    @responses.activate
    def test_auth_failure_401(self):
        # Given
        auth = ("nono", "le petit robot")
        responses.add(responses.HEAD, self.config.indices[0][0],
                      body="", status=401,
                      content_type='application/json')

        # When/Given
        with self.assertRaises(AuthFailedError):
            self.session.authenticate(auth)


class TestLegacyCanopyAuthManager(AuthManagerBase):
    klass = LegacyCanopyAuthManager

    @responses.activate
    def test_from_configuration(self):
        # Given
        responses.add(responses.GET, self.config.api_url, status=200,
                      body=json.dumps(R_JSON_AUTH_RESP))
        authenticator = LegacyCanopyAuthManager.from_configuration(self.config)
        session = Session(authenticator, self.prefix)

        # When
        with session:
            # Then no exception
            session.authenticate((FAKE_USER, FAKE_PASSWORD))

    @responses.activate
    def test_simple(self):
        # Given
        responses.add(responses.GET, self.config.api_url, status=200,
                      body=json.dumps(R_JSON_AUTH_RESP))

        # When
        # Then no exception
        self.session.authenticate((FAKE_USER, FAKE_PASSWORD))

    def test_connection_failure(self):
        with patch.object(self.session._raw, "get",
                          side_effect=requests.exceptions.ConnectionError):
            with self.assertRaises(AuthFailedError):
                self.session.authenticate((FAKE_USER, FAKE_PASSWORD))

    @responses.activate
    def test_http_failure(self):
        # Given
        config = Configuration()
        responses.add(responses.GET, config.api_url, body="", status=404,
                      content_type='application/json')

        # When/Then
        with self.assertRaises(AuthFailedError):
            self.session.authenticate((FAKE_USER, FAKE_PASSWORD))

    @responses.activate
    def test_unauthenticated_user(self):
        # Given
        responses.add(responses.GET, self.config.api_url,
                      body=json.dumps(R_JSON_NOAUTH_RESP),
                      content_type='application/json')

        # When/Then
        with self.assertRaises(AuthFailedError):
            self.session.authenticate((FAKE_USER, FAKE_PASSWORD))


class TestBroodAuthManager(AuthManagerBase):
    klass = BroodAuthenticator

    def setUp(self):
        AuthManagerBase.setUp(self)
        self.token_url = self.config.store_url + "/api/v0/json/auth/tokens/auth"

    @responses.activate
    def test_from_configuration(self):
        # Given
        responses.add(responses.POST, self.token_url, status=200,
                      body=json.dumps({"token": "dummy token"}))
        authenticator = BroodAuthenticator.from_configuration(self.config)
        session = Session(authenticator, self.prefix)

        # When
        with session:
            session.authenticate((FAKE_USER, FAKE_PASSWORD))

            # Then
            self.assertEqual(session._raw.auth, ("dummy token", None))

    @responses.activate
    def test_simple(self):
        # Given
        responses.add(responses.POST, self.token_url, status=200,
                      body=json.dumps({"token": "dummy token"}))

        # When
        self.session.authenticate((FAKE_USER, FAKE_PASSWORD))

        # Then
        self.assertEqual(self.session._raw.auth, ("dummy token", None))

    def test_connection_failure(self):
        with patch.object(self.session._raw, "post",
                          side_effect=requests.exceptions.ConnectionError):
            with self.assertRaises(AuthFailedError):
                self.session.authenticate((FAKE_USER, FAKE_PASSWORD))

    @responses.activate
    def test_http_failure(self):
        # Given
        config = Configuration()
        responses.add(responses.POST, self.token_url, body="", status=403,
                      content_type='application/json')

        # When/Then
        with self.assertRaises(AuthFailedError):
            self.session.authenticate((FAKE_USER, FAKE_PASSWORD))


class TestAuthenticate(TestCase):
    def setUp(self):
        self.prefix = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.prefix)

    @fake_keyring
    @responses.activate
    def test_use_webservice_invalid_user(self):
        # Given
        config = Configuration()
        responses.add(responses.GET, config.api_url,
                      body=json.dumps(R_JSON_NOAUTH_RESP),
                      content_type='application/json')

        # When/Then
        session = Session(LegacyCanopyAuthManager(config.api_url), self.prefix)
        with session:
            with self.assertRaises(AuthFailedError):
                session.authenticate((FAKE_USER, FAKE_PASSWORD))


class SearchTestCase(TestCase):
    pass


class InstallTestCase(TestCase):
    pass