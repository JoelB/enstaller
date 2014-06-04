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
from enstaller.fetch import DownloadManager
from enstaller.repository import Repository, RepositoryPackageMetadata
from enstaller.utils import compute_md5

from enstaller.tests.common import mock_url_fetcher


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
        path = os.path.join(_EGGINST_COMMON_DATA, filename)
        repository = self._create_store_and_repository([filename])

        # When
        downloader = DownloadManager(repository, self.tempdir)
        with mock_url_fetcher(downloader, open(path, "rb")):
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

        repository = self._create_store_and_repository([filename])

        mocked_metadata = mock.Mock()
        mocked_metadata.md5 = "a" * 32
        mocked_metadata.size = 1024
        mocked_metadata.key = filename

        with mock.patch.object(repository, "find_package", return_value=mocked_metadata):
            downloader = DownloadManager(repository, self.tempdir)
            with mock_url_fetcher(downloader, open(path)):
                # When/Then
                with self.assertRaises(EnstallerException):
                    downloader.fetch(filename)

    def test_fetch_abort(self):
        # Given
        filename = "nose-1.3.0-1.egg"
        path = os.path.join(_EGGINST_COMMON_DATA, filename)
        repository = self._create_store_and_repository([filename])

        target = os.path.join(self.tempdir, filename)

        # When
        downloader = DownloadManager(repository, self.tempdir)
        with mock_url_fetcher(downloader, open(path)):
            context = downloader.iter_fetch(filename)
            for i, chunk in enumerate(context):
                if i == 1:
                    context.cancel()
                    break

            # Then
            self.assertFalse(os.path.exists(target))

    def test_fetch_egg_simple(self):
        # Given
        egg = "nose-1.3.0-1.egg"
        path = os.path.join(_EGGINST_COMMON_DATA, egg)

        repository = self._create_store_and_repository([egg])

        # When
        downloader = DownloadManager(repository, self.tempdir)
        with mock_url_fetcher(downloader, open(path, "rb")):
            downloader.fetch(egg)

        # Then
        target = os.path.join(self.tempdir, egg)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(compute_md5(target),
                         compute_md5(os.path.join(_EGGINST_COMMON_DATA, egg)))

    def test_fetch_egg_refetch(self):
        # Given
        egg = "nose-1.3.0-1.egg"
        path = os.path.join(_EGGINST_COMMON_DATA, egg)

        repository = self._create_store_and_repository([egg])

        # When
        downloader = DownloadManager(repository, self.tempdir)
        with mock_url_fetcher(downloader, open(path, "rb")):
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
        with mock_url_fetcher(downloader, open(path, "rb")):
            downloader.fetch(egg)

        # Then
        target = os.path.join(self.tempdir, egg)
        self.assertEqual(compute_md5(target), compute_md5(path))

        # When
        _corrupt_file(target)

        # Then
        self.assertNotEqual(compute_md5(target), compute_md5(path))

        # When
        with mock_url_fetcher(downloader, open(path, "rb")):
            downloader.fetch(egg, force=True)

        # Then
        self.assertEqual(compute_md5(target), compute_md5(path))

        # When/Then
        # Ensure we deal correctly with force=False when the egg is already
        # there.
        with mock_url_fetcher(downloader, open(path, "rb")):
            downloader.fetch(egg, force=False)

    def test_progress_manager(self):
        """
        Ensure that the progress manager __call__ is called inside the fetch
        loop.
        """
        # Given
        egg = "nose-1.3.0-1.egg"
        path = os.path.join(_EGGINST_COMMON_DATA, egg)
        repository = self._create_store_and_repository([egg])

        with mock.patch("egginst.console.ProgressManager") as m:
            # When
            downloader = DownloadManager(repository, self.tempdir)
            with mock_url_fetcher(downloader, open(path, "rb")):
                downloader.fetch(egg)

            # Then
            self.assertTrue(m.called)
