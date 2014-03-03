# Copyright by Enthought, Inc.
# Author: Ilan Schnell <ischnell@enthought.com>
from __future__ import print_function

import ast
import base64
import json
import re
import os
import sys
import textwrap
import platform
import urllib2
import warnings

from getpass import getpass
from os.path import isfile, join

from enstaller import __version__
from enstaller.errors import (
    AuthFailedError, EnstallerException, InvalidConfiguration, InvalidFormat)
from utils import PY_VER, abs_expanduser, fill_url


def __import_new_keyring():
    """
    Import keyring >= 1.1.
    """
    import keyring.backends.OS_X
    import keyring.backends.Gnome
    import keyring.backends.Windows
    import keyring.backends.kwallet

    keyring.core.init_backend()
    if keyring.get_keyring().priority < 0:
        keyring = None
    return keyring


def __import_old_keyring():
    import keyring
    import keyring.backend
    # don't use keyring backends that require console input or just do
    # more or less the same thing we're already doing
    keyring.backend._all_keyring = [keyring.backend.OSXKeychain(),
                                    keyring.backend.GnomeKeyring(),
                                    keyring.backend.KDEKWallet(),
                                    keyring.backend.Win32CryptoKeyring(),
                                    keyring.backend.Win32CryptoRegistry(),
                                    keyring.backend.WinVaultKeyring()]
    keyring.core.init_backend()
    if keyring.get_keyring().supported() < 0:
        keyring = None
    return keyring


try:
    import keyring
except ImportError, KeyError:
    # The KeyError happens when USERPROFILE env var is not defined on windows
    keyring = None
else:
    try:
        keyring = __import_new_keyring()
    except ImportError:
        try:
            keyring = __import_old_keyring()
        except ImportError:
            keyring = None

KEYRING_SERVICE_NAME = 'Enthought.com'

config_fn = ".enstaller4rc"
home_config_path = abs_expanduser("~/" + config_fn)
system_config_path = join(sys.prefix, config_fn)


def _canopyr_hack_location():
    return os.path.join(sys.prefix, "USE_CANOPYR_HACK")

def _canopyr_hack_path():
    with open(_canopyr_hack_location()) as fp:
        return fp.read().strip()

def use_canopy_order():
    if hasattr(sys, "real_prefix"):
        return True
    elif os.path.exists(_canopyr_hack_location()):
        return True
    else:
        return False

def configuration_search_order():
    paths = []
    use_canopyr_hack = os.path.exists(_canopyr_hack_location())

    if use_canopyr_hack:
        paths.append(sys.prefix)
        paths.append(_canopyr_hack_path())
        paths.append(abs_expanduser("~"))
    elif hasattr(sys, "real_prefix"):
        paths.append(sys.prefix)
        paths.append(abs_expanduser("~"))
        paths.append(sys.real_prefix)
    else:
        paths.append(abs_expanduser("~"))
        paths.append(sys.prefix)

    return [os.path.normpath(p) for p in paths]

def get_default_url():
    import plat
    return 'https://api.enthought.com/eggs/%s/' % plat.custom_plat


class PythonConfigurationParser(ast.NodeVisitor):
    def __init__(self):
        self._data = {}

    def parse(self, s):
        self._data.clear()

        root = ast.parse(s)
        self.visit(root)
        return self._data

    def generic_visit(self, node):
        if type(node) != ast.Module:
            raise InvalidFormat("Unexpected expression @ line {0}".
                                format(node.lineno))
        super(PythonConfigurationParser, self).generic_visit(node)

    def visit_Assign(self, node):
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            msg = "Invalid configuration syntax at line {0}".format(node.lineno)
            raise InvalidFormat(msg)
        else:
            for target in node.targets:
                self._data[target.id] = value


RC_TMPL = """\
# enstaller configuration file
# ============================
#
# This file contains the default package repositories and configuration
# used by enstaller %(version)s for the Python %(py_ver)s environment:
#
#   sys.prefix = %(sys_prefix)r
#
# This file was initially created by running the enpkg command.

%(auth_section)s

# `use_webservice` refers to using 'https://api.enthought.com/eggs/'.
# The default is True; that is, the webservice URL is used for fetching
# eggs.  Uncommenting changes this behavior to using the explicit
# IndexedRepos listed below.
#use_webservice = False

# When use_webservice is True, one can control the webservice entry point enpkg
# will talk to. If not specified, a default will be used. Mostly useful for
# testing
#webservice_entry_point = "https://acme.com/api/{PLATFORM}/"

# The enpkg command searches for eggs in the list `IndexedRepos` defined
# below.  When enpkg searches for an egg, it tries each repository in
# this list in order and selects the first one that matches, ignoring
# remaining repositories.  Therefore, the order of this list matters.
#
# For local repositories, the index file is optional.  Remember that on
# Windows systems backslashes in a directory path need to escaped, e.g.:
# r'file://C:\\repository\\' or 'file://C:\\\\repository\\\\'
IndexedRepos = [
#  'https://www.enthought.com/repo/ets/eggs/{SUBDIR}/',
  'https://www.enthought.com/repo/epd/GPL-eggs/{SUBDIR}/',
  'https://www.enthought.com/repo/epd/eggs/{SUBDIR}/',
# The Enthought PyPI build mirror:
  'http://www.enthought.com/repo/pypi/eggs/{SUBDIR}/',
]

# Install prefix (enpkg --prefix and --sys-prefix options overwrite
# this).  When this variable is not provided, it will default to the
# value of sys.prefix (within the current interpreter running enpkg).
#prefix = %(sys_prefix)r

# When running enpkg behind a firewall it might be necessary to use a
# proxy to access the repositories.  The URL for the proxy can be set
# here.  Note that the enpkg --proxy option will overwrite this setting.
%(proxy_line)s

# Uncomment the next line to disable application menu-item installation.
# This only affects the few packages that install menu items, such as
# IPython.
#noapp = True

# Uncomment the next line to turn off automatic prompts to update
# enstaller.
#autoupdate = False
"""


def _create_default_config():
    config = Configuration()
    path = config._default_filename()
    config.write(path)
    return path


def _decode_auth(s):
    parts = base64.decodestring(s).split(":")
    if len(parts) == 2:
        return tuple(parts)
    else:
        raise InvalidConfiguration("Invalid auth line")


def _encode_auth(username, password):
    s = "{0}:{1}".format(username, password)
    return base64.encodestring(s).rstrip()


class Configuration(object):
    @classmethod
    def _get_default_config(cls, create_if_not_exists=False):
        config_filename = get_path()
        if config_filename is None:
            if create_if_not_exists:
                path = _create_default_config()
                return cls.from_file(path)
            else:
                raise InvalidConfiguration("No configuration found.")
        else:
            return cls.from_file(config_filename)

    @classmethod
    def from_file(cls, filename, use_keyring=None):
        """
        Create a new Configuration instance from the given file.

        Parameters
        ----------
        filename: str or file-like object
            If a string, is understood as a filename to open. Understood as a
            file-like object otherwise.
        """
        accepted_keys_as_is = set([
            "proxy", "noapp", "use_webservice", "autoupdate",
            "prefix", "local", "IndexedRepos", "webservice_entry_point",
            "repository_cache"
        ])
        parser = PythonConfigurationParser()

        def _create(fp):
            ret = cls(use_keyring)
            for k, v in parser.parse(fp.read()).iteritems():
                if k in accepted_keys_as_is:
                    setattr(ret, k, v)
                elif k == "EPD_auth":
                    username, password = _decode_auth(v)
                    ret._username = username
                    ret._password = password
                elif k == "EPD_username":
                    ret._username = v
                    if keyring is None:
                        ret._password = None
                    else:
                        ret._password = \
                            keyring.get_password(KEYRING_SERVICE_NAME, v)
                else:
                    warnings.warn("Unsupported configuration setting {0}, "
                                  "ignored".format(k))
            return ret

        if isinstance(filename, basestring):
            with open(filename, "r") as fp:
                return _create(fp)
        else:
            return _create(filename)

    def __init__(self, use_keyring=None):
        self.proxy = None
        self.noapp = False
        self.use_webservice = True
        self.autoupdate = True

        self._prefix = sys.prefix
        self._local = join(sys.prefix, 'LOCAL-REPO')
        self._IndexedRepos = []
        self._webservice_entry_point = fill_url(get_default_url())

        self.repository_cache = self.local

        self._username = None
        self._password = None

        if use_keyring is None:
            self._use_keyring = keyring is not None
        elif use_keyring is True:
            if keyring is None:
                raise InvalidConfiguration("Requested using keyring, but "
                                           "no keyring available.")
            self._use_keyring = use_keyring
        elif use_keyring is False:
            self._use_keyring = use_keyring
        else:
            raise InvalidConfiguration("Invalid value for use_keyring: {0}".
                                       format(use_keyring))

    @property
    def use_keyring(self):
        return self._use_keyring

    def set_auth(self, username, password):
        if username is None or password is None:
            raise InvalidConfiguration(
                "invalid authentication arguments: "
                "{0}:{1}".format(username, password))
        else:
            self._username = username
            self._password = password

            if self.use_keyring:
                keyring.set_password(KEYRING_SERVICE_NAME, username, password)

    def reset_auth(self):
        if self.use_keyring:
            if self._username is None:
                raise ValueError("Cannot reset auth if not set up.")
            keyring.set_password(KEYRING_SERVICE_NAME, self.EPD_username, "")

        self._username = None
        self._password = None

    def get_auth(self):
        return (self._username, self._password)

    def _default_filename(self):
        if sys.platform != 'win32' and os.getuid() == 0:
            return system_config_path
        else:
            return home_config_path

    def write(self, filename=None):
        if filename is None:
            filename = self._default_filename()

        username, password = self.get_auth()
        if username and password:
            if self.use_keyring:
                authline = 'EPD_username = %r' % self.EPD_username
            else:
                authline = 'EPD_auth = %r' % self.EPD_auth
            auth_section = textwrap.dedent("""
            # EPD subscriber authentication is required to access the EPD
            # repository.  To change your credentials, use the 'enpkg --userpass'
            # command, which will ask you for your email address (or username) and
            # password.
            %s
            """ % authline)
        else:
            auth_section = ''

        if self.proxy:
            proxy_line = 'proxy = %r' % self.proxy
        else:
            proxy_line = ('#proxy = <proxy string>  '
                          '# e.g. "http://<user>:<passwd>@123.0.1.2:8080"')

        variables = {"py_ver": PY_VER, "sys_prefix": sys.prefix, "version": __version__,
                     "proxy_line": proxy_line, "auth_section": auth_section}
        with open(filename, "w") as fo:
            fo.write(RC_TMPL % variables)

    def _change_auth(self, filename=None):
        if filename is None:
            filename = self._default_filename()

        # XXX: should we really just write the file in this case instead of
        # erroring-out ?
        if not os.path.isfile(filename):
            self.write(filename)
            return
        else:
            pat = re.compile(r'^(EPD_auth|EPD_username)\s*=.*$', re.M)
            with open(filename, 'r') as fi:
                data = fi.read()

            if not self.is_auth_configured:
                if pat.search(data):
                    data = pat.sub("", data)
                with open(filename, 'w') as fo:
                    fo.write(data)
                return

            if self.use_keyring:
                authline = 'EPD_username = %r' % self.EPD_username
            else:
                authline = 'EPD_auth = %r' % self.EPD_auth

            if pat.search(data):
                data = pat.sub(authline, data)
            else:
                lines = data.splitlines()
                lines.append(authline)
                data = '\n'.join(lines) + '\n'

            with open(filename, 'w') as fo:
                fo.write(data)

    def _checked_change_auth(self, filename=None, remote=None):
        if not self.is_auth_configured:
            raise InvalidConfiguration("No auth configured: cannot "
                                       "change auth.")
        user = {}

        user = authenticate(self, remote)
        self._change_auth(filename)
        print(subscription_message(self, user))
        return user

    @property
    def is_auth_configured(self):
        """
        Returns True if authentication is set up for this configuration object.

        Note
        ----
        This only checks whether the auth is configured, not whether the
        authentication information is correct.
        """
        if self._username and self._password:
            return True
        else:
            return False

    @property
    def local(self):
        return self._local

    @local.setter
    def local(self, value):
        self._local = abs_expanduser(value)

    @property
    def prefix(self):
        return self._prefix

    @prefix.setter
    def prefix(self, value):
        self._prefix = abs_expanduser(value)

    @property
    def IndexedRepos(self):
        return self._IndexedRepos

    @IndexedRepos.setter
    def IndexedRepos(self, urls):
        self._IndexedRepos = [fill_url(url) for url in urls]

    @property
    def webservice_entry_point(self):
        return self._webservice_entry_point

    @webservice_entry_point.setter
    def webservice_entry_point(self, url):
        self._webservice_entry_point = fill_url(url)

    @property
    def EPD_username(self):
        return self._username

    @EPD_username.setter
    def EPD_username(self, value):
        self._username = value

    @property
    def EPD_auth(self):
        if not self.is_auth_configured:
            raise InvalidConfiguration("EPD_auth is not available when "
                                       "auth has not been configured.")
        return _encode_auth(self._username, self._password)

    @EPD_auth.setter
    def EPD_auth(self, value):
        try:
            username, password = _decode_auth(value)
        except Exception:
            raise InvalidConfiguration("Invalid EPD_auth value")
        else:
            self._username = username
            self._password = password


def get_auth():
    warnings.warn("get_auth deprecated, use Configuration.get_auth instead",
                  DeprecationWarning)
    if get_path() is None:
        raise InvalidConfiguration(
            "No enstaller configuration found, no "
            "authentication information available")
    return Configuration._get_default_config().get_auth()


def get_path():
    """
    Return the absolute path to the config file.
    """
    if isfile(home_config_path):
        return home_config_path
    if isfile(system_config_path):
        return system_config_path
    return None


def input_auth():
    """
    Prompt user for username and password.  Return (username, password)
    tuple or (None, None) if left blank.
    """
    print("""\
Please enter the email address (or username) and password for your
EPD or EPD Free subscription.
""")
    username = raw_input('Email (or username): ').strip()
    if not username:
        return None, None
    return username, getpass('Password: ')


def web_auth(auth,
             api_url='https://api.enthought.com/accounts/user/info/'):
    """
    Authenticate a user's credentials (an `auth` tuple of username,
    password) using the web API.  Return a dictionary containing user
    info.

    Function taken from Canopy and modified.
    """
    # Make basic local checks
    username, password = auth
    if username is None or password is None:
        raise AuthFailedError("Authentication error: User login is required.")

    # Authenticate with the web API
    auth = 'Basic ' + (':'.join(auth).encode('base64').strip())
    req = urllib2.Request(api_url, headers={'Authorization': auth})

    try:
        f = urllib2.urlopen(req)
    except urllib2.URLError as e:
        raise AuthFailedError("Authentication error: %s" % e.reason)

    try:
        res = f.read()
    except urllib2.HTTPError as e:
        raise AuthFailedError("Authentication error: %s" % e.reason)

    # See if web API refused to authenticate
    user = json.loads(res)
    if not(user['is_authenticated']):
        raise AuthFailedError('Authentication error: Invalid user login.')

    return user


def subscription_level(user):
    """
    Extract the level of EPD subscription from the dictionary (`user`)
    returned by the web API.
    """
    if 'has_subscription' in user:
        if user.get('is_authenticated', False) and user.get('has_subscription', False):
            return 'EPD Basic or above'
        elif user.get('is_authenticated', False) and not(user.get('has_subscription', False)):
            return 'EPD Free'
        else:
            return None
    else:  # don't know the subscription level
        if user.get('is_authenticated', False):
            return 'EPD'
        else:
            return None


def subscription_message(config, user):
    """
    Return a 'subscription level' message based on the `user`
    dictionary.

    `user` is a dictionary, probably retrieved from the web API, that
    may contain `is_authenticated`, and `has_subscription`.
    """
    message = ""

    if user.get('is_authenticated', False):
        username, password = config.get_auth()
        login = "You are logged in as %s" % username
        subscription = "Subscription level: %s" % subscription_level(user)
        name = user.get('first_name', '') + ' ' + user.get('last_name', '')
        name = name.strip()
        if name:
            name = ' (' + name + ')'
        message = login + name + '.\n' + subscription
    else:
        message = "You are not logged in.  To log in, type 'enpkg --userpass'."

    return message


def prepend_url(url):
    f = open(get_path(), 'r+')
    data = f.read()
    pat = re.compile(r'^IndexedRepos\s*=\s*\[\s*$', re.M)
    if not pat.search(data):
        sys.exit("Error: IndexedRepos section not found")
    data = pat.sub(r"IndexedRepos = [\n  '%s'," % url, data)
    f.seek(0)
    f.write(data)
    f.close()


def authenticate(configuration, remote=None):
    """
    Attempt to authenticate the user's credentials by the appropriate
    means.

    `remote` is enpkg.remote, required if not using the web API to
    authenticate

    If 'use_webservice' is set, authenticate with the web API and return
    a dictionary containing user info on success.

    Else, authenticate with remote.connect and return a dict containing
    is_authenticated=True on success.

    If authentication fails, raise an exception.
    """
    # FIXME: remove passing remote hack.

    if not configuration.is_auth_configured:
        raise EnstallerException("No valid auth information in "
                                 "configuration, cannot authenticate.")

    user = {}
    auth = configuration.get_auth()

    if configuration.use_webservice:
        # check credentials using web API
        try:
            user = web_auth(auth)
            assert user['is_authenticated']
        except Exception as e:
            raise AuthFailedError('Authentication failed: %s.' % e)
    else:
        # check credentials using remote.connect
        try:
            remote.connect(auth)
            user = dict(is_authenticated=True)
        except KeyError:
            raise AuthFailedError('Authentication failed:'
                                  ' Invalid user login.')
        except Exception as e:
            raise AuthFailedError('Authentication failed: %s.' % e)
    return user


def print_config(config, remote, prefix):
    print("Python version:", PY_VER)
    print("enstaller version:", __version__)
    print("sys.prefix:", sys.prefix)
    print("platform:", platform.platform())
    print("architecture:", platform.architecture()[0])
    print("use_webservice:", config.use_webservice)
    print("config file:", get_path())
    print("settings:")
    print("    prefix = %s" % prefix)
    print("    %s = %r" % ("local", config.local))
    print("    %s = %r" % ("noapp", config.noapp))
    print("    %s = %r" % ("proxy", config.proxy))
    print("    IndexedRepos:", '(not used)' if config.use_webservice else '')
    for repo in config.IndexedRepos:
        print('        %r' % repo)

    user = {}
    try:
        user = authenticate(config, remote)
    except Exception as e:
        print(e)
    print(subscription_message(config, user))
