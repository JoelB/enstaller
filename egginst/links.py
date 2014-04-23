from __future__ import print_function

import logging
import os
from os.path import dirname, isdir, join

from egginst.utils import rm_rf


logger = logging.getLogger(__name__)


def create_link(arcname, link, prefix, verbose):
    usr = 'EGG-INFO/usr/'
    assert arcname.startswith(usr), arcname
    dst = join(prefix, arcname[len(usr):])

    # Create the destination directory if it does not exist.  In most cases
    # it will exist, but you never know.
    if not isdir(dirname(dst)):
        os.makedirs(dirname(dst))

    rm_rf(dst, verbose)
    logger.info("Creating: %s (link to %s)", dst, link)
    os.symlink(link, dst)
    return dst
