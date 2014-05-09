from __future__ import print_function

import contextlib
import logging
import os
import threading
import sys
import tempfile

from uuid import uuid4
from os.path import isfile, join

from egginst.main import EggInst
from egginst.progress import progress_manager_factory

from enstaller.errors import EnpkgError
from enstaller.eggcollect import meta_dir_from_prefix
from enstaller.repository import (InstalledPackageMetadata, Repository,
                                  egg_name_to_name_version)

from enstaller.history import History
from enstaller.solver import Solver

from enstaller.config import Configuration


logger = logging.getLogger(__name__)


class _ExecuteContext(object):
    def __init__(self, prefix, actions):
        self._actions = actions
        self._prefix = prefix

    @property
    def n_actions(self):
        return len(self._actions)

    def iter_actions(self):
        with History(self._prefix):
            for action in self._actions:
                yield action


class Enpkg(object):
    """ This is main interface for using enpkg, it is used by the CLI.
    Arguments for object creation:

    Parameters
    ----------
    repository: Repository
        This is the remote repository which enpkg will use to resolve
        dependencies.
    prefixes: list of path -- default: [sys.prefix]
        Each path, is an install "prefix" (such as, e.g. /usr/local) in which
        things get installed. Eggs are installed or removed from the first
        prefix in the list.
    evt_mgr: encore event manager instance -- default: None
        Various progress events (e.g. for download, install, ...) are being
        emitted to the event manager.  By default, a simple progress bar is
        displayed on the console (which does not use the event manager at all).
    """
    def __init__(self, remote_repository, download_manager,
                 prefixes=[sys.prefix], evt_mgr=None, config=None):
        if config is None:
            config = Configuration._get_default_config()
        self.local_dir = config.repository_cache

        self.prefixes = prefixes
        self.top_prefix = prefixes[0]

        self.evt_mgr = evt_mgr

        self._remote_repository = remote_repository

        self._installed_repository = Repository._from_prefixes(self.prefixes)
        self._top_installed_repository = Repository._from_prefixes([self.top_prefix])

        self._execution_aborted = threading.Event()

        self._downloader = download_manager

        self._solver = Solver(self._remote_repository,
                              self._top_installed_repository)

    def _install_egg(self, path, extra_info=None):
        """
        Install the given egg.

        Parameters
        ----------
        path: str
            The path to the egg to install
        """
        name, _ = egg_name_to_name_version(path)

        installer = EggInst(path, prefix=self.prefixes[0], evt_mgr=self.evt_mgr)
        installer.super_id = getattr(self, 'super_id', None)
        installer.install(extra_info)

        meta_dir = meta_dir_from_prefix(self.top_prefix, name)
        package = InstalledPackageMetadata.from_meta_dir(meta_dir)

        self._top_installed_repository.add_package(package)
        self._installed_repository.add_package(package)

    def _remove_egg(self, egg):
        """
        Remove the given egg.

        Parameters
        ----------
        path: str
            The egg basename (e.g. 'numpy-1.8.0-1.egg')
        """
        remover = EggInst(egg, prefix=self.top_prefix)
        remover.super_id = getattr(self, 'super_id', None)
        remover.remove()

        # FIXME: we recalculate the full repository because we don't have a
        # feature to remove a package yet
        self._top_installed_repository = \
            Repository._from_prefixes([self.prefixes[0]])

    def _execute_opcode(self, opcode, egg):
        logger.info('\t' + str((opcode, egg)))
        if opcode.startswith('fetch_'):
            self._fetch(egg, force=int(opcode[-1]))
        elif opcode == 'remove':
            self._remove_egg(egg)
        elif opcode == 'install':
            name, version = egg_name_to_name_version(egg)
            package = self._remote_repository.find_package(name, version)
            extra_info = package.s3index_data
            self._install_egg(os.path.join(self.local_dir, egg), extra_info)
        else:
            raise Exception("unknown opcode: %r" % opcode)

    @contextlib.contextmanager
    def _enpkg_progress_manager(self, execution_context):
        self.super_id = None

        progress = progress_manager_factory("super", "",
                                            execution_context.n_actions,
                                            self.evt_mgr, self, self.super_id)

        try:
            yield progress
        finally:
            self.super_id = uuid4()

    def get_execute_context(self, actions):
        return _ExecuteContext(self.prefixes[0], actions)

    def execute(self, actions):
        """
        Execute actions, which is an iterable over tuples(action, egg_name),
        where action is one of 'fetch', 'remote', or 'install' and egg_name
        is the filename of the egg.
        This method is only meant to be called with actions created by the
        *_actions methods below.
        """
        logger.info("Enpkg.execute: %d", len(actions))

        context = self.get_execute_context(actions)

        with self._enpkg_progress_manager(context) as progress:
            for n, (opcode, egg) in enumerate(context.iter_actions()):
                if self._execution_aborted.is_set():
                    self._execution_aborted.clear()
                    break
                self._execute_opcode(opcode, egg)
                progress(step=n)

    def abort_execution(self):
        self._execution_aborted.set()

    def revert_actions(self, arg):
        """
        Calculate the actions necessary to revert to a given state, the
        argument may be one of:
          * complete set of eggs, i.e. a set of egg file names
          * revision number (negative numbers allowed)
        """
        h = History(self.prefixes[0])
        h.update()
        if isinstance(arg, set):
            state = arg
        else:
            try:
                rev = int(arg)
            except (TypeError, ValueError):
                raise EnpkgError("Invalid argument: integer expected, "
                                 "got: {0!r}".format(arg))
            try:
                state = h.get_state(rev)
            except IndexError:
                raise EnpkgError("Error: no such revision: %r" % arg)

        curr = h.get_state()
        if state == curr:
            return []

        res = []
        for egg in curr - state:
            if egg.startswith('enstaller'):
                continue
            res.append(('remove', egg))

        for egg in state - curr:
            if egg.startswith('enstaller'):
                continue
            if not isfile(join(self.local_dir, egg)):
                if self._remote_repository._has_package_key(egg):
                    res.append(('fetch_0', egg))
                else:
                    raise EnpkgError("cannot revert -- missing %r" % egg)
            res.append(('install', egg))
        return res

    def get_history(self):
        """
        return a history (h) object with this Enpkg instance prefix.
        """
        # FIXME: only used by canopy
        return History(self.prefixes[0])

    def _fetch(self, egg, force=False):
        self._downloader.super_id = getattr(self, 'super_id', None)
        self._downloader.fetch_egg(egg, force, self._execution_aborted)
