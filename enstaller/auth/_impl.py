from __future__ import absolute_import

import json

from egginst._compat import urlparse

from enstaller.errors import AuthFailedError, EnstallerException

from .auth_managers import LegacyCanopyAuthManager, OldRepoAuthManager
from .user_info import UserInfo


def authenticate(auth, session, configuration):
    """
    Attempt to authenticate the user's credentials by the appropriate
    means.

    Parameters
    ----------
    auth : tuple
        Authentication pair.
    session : Session
        The connection handler used for actual network connections.
    configuration : Configuration_like
        A Configuration instance.

    Returns
    -------
    user_info : UserInfo

    If the 'use_webservice' mode is enabled in the configuration, authenticate
    with the web API and return the corresponding information.

    Else, authenticate with the configured repositories in
    config.indexed_repositories

    If authentication fails, raise an exception.
    """
    if configuration.use_webservice:
        user = _web_auth(auth, configuration.api_url, session)
        if not user.is_authenticated:
            raise AuthFailedError('Authentication failed: could not authenticate')
    else:
        authenticator = OldRepoAuthManager(configuration.indices)
        authenticator.authenticate(session, auth)
        user = authenticator.user_info

    return user


def subscription_message(config, user):
    """
    Return a 'subscription level' message based on the `user`
    information.

    Parameters
    ----------
    config : Configuration
    user : UserInfo

    Returns
    -------
    message : str
        The subscription message.
    """
    message = ""

    if user.is_authenticated:
        username, password = config.auth
        login = "You are logged in as %s" % username
        subscription = "Subscription level: %s" % user.subscription_level
        name = user.first_name + ' ' + user.last_name
        name = name.strip()
        if name:
            name = ' (' + name + ')'
        message = login + name + '.\n' + subscription
    else:
        message = "You are not logged in.  To log in, type 'enpkg --userpass'."

    return message


def _web_auth(auth, api_url, session):
    """
    Authenticate a user's credentials (an `auth` tuple of username, password)
    using the web API.
    """
    # Make basic local checks
    username, password = auth
    if username is None or password is None:
        raise AuthFailedError("Authentication error: User login is required.")

    authenticator = LegacyCanopyAuthManager(api_url)
    authenticator.authenticate(session, auth)
    return authenticator.user_info
