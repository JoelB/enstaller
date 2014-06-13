import os
import os.path
import shutil
import sys
import tempfile

if sys.version_info < (2, 7):
    import unittest2 as unittest
else:
    import unittest

import mock

from egginst.tests.common import _EGGINST_COMMON_DATA

from enstaller.errors import EnstallerException
from enstaller.fetch import DownloadManager, URLFetcher
from enstaller.proxy.util import ProxyInfo
from enstaller.repository import Repository, RepositoryPackageMetadata
from enstaller.utils import compute_md5


class TestURLFetcher(unittest.TestCase):
    def setUp(self):
        self.prefix = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.prefix)

    def test_proxy_simple(self):
        # Given
        proxies = [ProxyInfo.from_string("http://acme.com")]

        # When
        fetcher = URLFetcher(self.prefix, proxies=proxies)

        # Then
        self.assertEqual(fetcher._proxies, {"http": "http://acme.com:3128"})

    def test_proxies(self):
        # Given
        proxies = [ProxyInfo.from_string("http://acme.com"),
                   ProxyInfo.from_string("https://acme.com:3129")]

        # When
        fetcher = URLFetcher(self.prefix, proxies=proxies)

        # Then
        self.assertEqual(fetcher._proxies, {"http": "http://acme.com:3128",
                                            "https": "https://acme.com:3129"})


class TestDownloadManager(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def _create_store_and_repository(self, eggs):
        repository = Repository()
        for egg in eggs:
            path = os.path.join(_EGGINST_COMMON_DATA, egg)
            package = RepositoryPackageMetadata.from_egg(path)
            repository.add_package(package)

        return repository

    def test_fetch_simple(self):
        # Given
        filename = "nose-1.3.0-1.egg"
        repository = self._create_store_and_repository([filename])

        downloader = DownloadManager(repository, self.tempdir)
        downloader.fetch(filename)

        # Then
        target = os.path.join(self.tempdir, filename)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(compute_md5(target),
                         repository.find_package("nose", "1.3.0-1").md5)

    def test_fetch_invalid_md5(self):
        # Given
        filename = "nose-1.3.0-1.egg"
        path = os.path.join(_EGGINST_COMMON_DATA, filename)

        repository = Repository()
        package = RepositoryPackageMetadata.from_egg(path)
        package.md5 = "a" * 32
        repository.add_package(package)

        downloader = DownloadManager(repository, self.tempdir)
        with self.assertRaises(EnstallerException):
            downloader.fetch(filename)

    def test_fetch_abort(self):
        # Given
        filename = "nose-1.3.0-1.egg"
        repository = self._create_store_and_repository([filename])

        downloader = DownloadManager(repository, self.tempdir)
        target = os.path.join(self.tempdir, filename)

        # When
        context = downloader.iter_fetch(filename)
        for i, chunk in enumerate(context):
            if i == 1:
                context.cancel()
                break

        # Then
        self.assertFalse(os.path.exists(target))

    def test_fetch_egg_refetch(self):
        # Given
        egg = "nose-1.3.0-1.egg"

        repository = self._create_store_and_repository([egg])

        # When
        downloader = DownloadManager(repository, self.tempdir)
        downloader.fetch(egg)

        # Then
        target = os.path.join(self.tempdir, egg)
        self.assertTrue(os.path.exists(target))

    def test_fetch_egg_refetch_invalid_md5(self):
        # Given
        egg = "nose-1.3.0-1.egg"
        path = os.path.join(_EGGINST_COMMON_DATA, egg)

        repository = self._create_store_and_repository([egg])

        def _corrupt_file(target):
            with open(target, "wb") as fo:
                fo.write("")

        # When
        downloader = DownloadManager(repository, self.tempdir)
        downloader.fetch(egg)

        # Then
        target = os.path.join(self.tempdir, egg)
        self.assertEqual(compute_md5(target), compute_md5(path))

        # When
        _corrupt_file(target)

        # Then
        self.assertNotEqual(compute_md5(target), compute_md5(path))

        # When
        downloader.fetch(egg, force=True)

        # Then
        self.assertEqual(compute_md5(target), compute_md5(path))

        # When/Then
        # Ensure we deal correctly with force=False when the egg is already
        # there.
        downloader.fetch(egg, force=False)

    def test_progress_manager(self):
        """
        Ensure that the progress manager __call__ is called inside the fetch
        loop.
        """
        # Given
        egg = "nose-1.3.0-1.egg"
        repository = self._create_store_and_repository([egg])

        with mock.patch("egginst.progress.ProgressManager") as m:
            # When
            downloader = DownloadManager(repository, self.tempdir)
            downloader.fetch(egg)

            # Then
            self.assertTrue(m.called)
