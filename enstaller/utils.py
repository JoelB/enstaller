from __future__ import print_function

from egginst._compat import urlparse
from egginst._compat import PY2, input, pathname2url, unquote, url2pathname

import json
import logging
import sys
import zlib

from os.path import abspath, expanduser, getmtime, getsize, isdir, isfile, join

from egginst.utils import compute_md5

from enstaller.vendor import requests
from enstaller.verlib import NormalizedVersion, IrrationalVersionError
from enstaller import plat


_GZIP_MAGIC = "1f8b"

PY_VER = '%i.%i' % sys.version_info[:2]


def abs_expanduser(path):
    return abspath(expanduser(path))


def canonical(s):
    """
    return the canonical representations of a project name
    DON'T USE THIS IN NEW CODE (ONLY (STILL) HERE FOR HISTORICAL REASONS)
    """
    # eventually (once Python 2.6 repo eggs are no longer supported), this
    # function should only return s.lower()
    s = s.lower()
    s = s.replace('-', '_')
    if s == 'tables':
        s = 'pytables'
    return s

def normalize_version_string(version_string):
    """
    Normalize the given version string to a string that can be converted to
    a NormalizedVersion.

    This function applies various special cases needed for EPD/Canopy and not
    handled in NormalizedVersion parser.

    Parameters
    ----------
    version_string: str
        The version to convert

    Returns
    -------
    normalized_version: str
        The normalized version string. Note that this is not guaranteed to be
        convertible to a NormalizedVersion
    """
    # This hack makes it possible to use 'rc' in the version, where
    # 'rc' must be followed by a single digit.
    version_string = version_string.replace('rc', '.dev99999')
    # This hack allows us to deal with single number versions (e.g.
    # pywin32's style '214').
    if not "." in version_string:
        version_string += ".0"

    if version_string.endswith(".dev"):
        version_string += "1"
    return version_string

def comparable_version(version):
    """
    Given a version string (e.g. '1.3.0.dev234'), return an object which
    allows correct comparison. For example:
        comparable_version('1.3.10') > comparable_version('1.3.8')  # True
    whereas:
        '1.3.10' > '1.3.8'  # False
    """
    try:
        ver = normalize_version_string(version)
        return NormalizedVersion(ver)
    except IrrationalVersionError:
        # If obtaining the RationalVersion object fails (for example for
        # the version '2009j'), simply return the string, such that
        # a string comparison can be made.
        return version


def info_file(path):
    return dict(size=getsize(path),
                mtime=getmtime(path),
                md5=compute_md5(path))


def cleanup_url(url):
    """
    Ensure a given repo string, i.e. a string specifying a repository,
    is valid and return a cleaned up version of the string.
    """
    if url.startswith(('http://', 'https://')):
        if not url.endswith('/'):
            url += '/'

    elif url.startswith('file://'):
        dir_path = url[7:]
        if dir_path.startswith('/'):
            # Unix filename
            if not url.endswith('/'):
                url += '/'
        else:
            # Windows filename
            if not url.endswith('\\'):
                url += '\\'

    elif isdir(abs_expanduser(url)):
        return cleanup_url('file://' + abs_expanduser(url))

    else:
        raise Exception("Invalid URL or non-existing file: %r" % url)

    return url


def fill_url(url):
    url = url.replace('{ARCH}', plat.arch)
    url = url.replace('{SUBDIR}', plat.subdir)
    url = url.replace('{PLATFORM}', plat.custom_plat)
    return cleanup_url(url)

def exit_if_sudo_on_venv(prefix):
    """ Exits the running process with a message to run as non-sudo user.

    All the following conditions should match:
        - if the platform is non-windows
        - if we are running inside a venv
        - and the script is run as root/sudo

    """

    if sys.platform == 'win32':
        return

    if not isfile(join(prefix, 'pyvenv.cfg')):
        return

    import os

    if os.getuid() != 0:
        return

    print('You are running enpkg as a root user inside a virtual environment. ' \
          'Please run it as a normal user')

    sys.exit(1)

def path_to_uri(path):
    """Convert the given path string to a valid URI.

    It produces URI that are recognized by the windows
    shell API on windows, e.g. 'C:\\foo.txt' will be
    'file:///C:/foo.txt'"""
    return urlparse.urljoin("file:", pathname2url(path))

def uri_to_path(uri):
    """Convert a valid file uri scheme string to a native
    path.

    The returned path should be recognized by the OS and
    the native path functions, but is not guaranteed to use
    the native path separator (e.g. it could be C:/foo.txt
    on windows instead of C:\\foo.txt)."""
    urlpart = urlparse.urlparse(uri)
    if urlpart.scheme == "file":
        unquoted = unquote(uri)
        path = unquoted[len("file://"):]
        if sys.platform == "win32" and path.startswith("/"):
            path = path[1:]
        return url2pathname(path)
    else:
        raise ValueError("Invalid file uri: {0}".format(uri))


def under_venv():
    return hasattr(sys, "real_prefix")


def real_prefix():
    if under_venv():
        return sys.real_prefix
    else:
        return sys.prefix


def prompt_yes_no(message, force_yes=False):
    """
    Prompt for a yes/no answer for the given message. Returns True if the
    answer is yes.

    Parameters
    ----------
    message : str
        The message to prompt the user with
    force_yes: boolean
        If True, then the message is only displayed, and the answer is assumed
        to be yes.

    """
    if force_yes:
        print(message)
        return True
    else:
        yn = input(message)
        return yn.lower() in set(['y', 'yes'])


def _bytes_to_hex(bdata):
    # Only use for tiny strings
    if PY2:
        return "".join("%02x" % (ord(c),) for c in bdata)
    else:
        return "".join("%02x" % c for c in bdata)


def decode_json_from_buffer(data):
    """
    Returns the decoded json dictionary contained in data. Optionally
    decompress the data if the buffer's data are detected as gzip-encoded.
    """
    if len(data) >= 2 and _bytes_to_hex(data[:2]) == _GZIP_MAGIC:
        # Some firewall/gateway has the "feature" of stripping Content-Encoding
        # from the response headers, without actually uncompressing the data,
        # in which case requests will give use a response object with
        # compressed data. We try to detect this case here, and decompress it
        # as requests would do if gzip format is detected.
        logging.debug("Detected compressed data with stripped header")
        try:
            data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
        except (IOError, zlib.error) as e:
            # ContentDecodingError is the exception raised by requests when
            # urllib3 fails to decompress.
            raise requests.exceptions.ContentDecodingError(
                "Detected gzip-compressed response, but failed to decode it.",
                 e)

    try:
        decoded_data = data.decode("utf8")
    except UnicodeDecodeError as e:
        raise ValueError("Invalid index data, try again ({0!r})".format(e))

    return json.loads(decoded_data)
