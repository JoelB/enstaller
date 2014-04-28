import collections
import operator
import os
import os.path
import sys

from egginst.eggmeta import info_from_z
from egginst.utils import ZipFile, compute_md5

from enstaller.errors import MissingPackage
from enstaller.eggcollect import info_from_metadir
from enstaller.utils import comparable_version


class PackageMetadata(object):
    """
    PackageMetadata encompasses the metadata required to resolve dependencies.
    They are not attached to a repository.
    """
    @classmethod
    def from_egg(cls, path):
        """
        Create an instance from an egg filename.
        """
        with ZipFile(path) as zp:
            metadata = info_from_z(zp)
        st = os.stat(path)
        metadata["size"] = st.st_size
        metadata["md5"] = compute_md5(path)
        metadata["packages"] = metadata.get("packages", [])
        return cls.from_json_dict(os.path.basename(path), metadata)

    @classmethod
    def from_json_dict(cls, key, json_dict):
        """
        Create an instance from a key (the egg filename) and metadata passed as
        a dictionary
        """
        return cls(key, json_dict["name"], json_dict["version"],
                   json_dict["build"], json_dict["packages"],
                   json_dict["python"], json_dict["size"], json_dict["md5"])

    def __init__(self, key, name, version, build, packages, python, size, md5):
        self.key = key

        self.name = name
        self.version = version
        self.build = build

        self.packages = packages
        self.python = python

        self.size = size
        self.md5 = md5

    def __repr__(self):
        return "PackageMetadata('{0}-{1}-{2}', key={3!r})".format(
            self.name, self.version, self.build, self.key)

    @property
    def full_version(self):
        """
        The full version as a string (e.g. '1.8.0-1' for the numpy-1.8.0-1.egg)
        """
        return "{0}-{1}".format(self.version, self.build)

    @property
    def comparable_version(self):
        """
        Returns an object that may be used to compare the version of two
        package metadata (only make sense for two packages which differ only in
        versions).
        """
        return comparable_version(self.version), self.build


class RepositoryPackageMetadata(object):
    """
    RepositoryPackageMetadata encompasses the full set of package metadata
    available from a repository.

    In particular, RepositoryPackageMetadata's instances know about which
    repository they are coming from through the store_location attribute.
    """
    @classmethod
    def from_json_dict(cls, key, json_dict):
        return cls(key, json_dict["name"], json_dict["version"],
                   json_dict["build"], json_dict["packages"],
                   json_dict["python"], json_dict["size"], json_dict["md5"],
                   json_dict.get("mtime", 0.0), json_dict.get("product", None),
                   json_dict.get("available", True),
                   json_dict["store_location"])

    @classmethod
    def from_installed_meta_dict(cls, json_dict):
        fake_size = -1
        fake_md5 = "a" * 32
        return cls(json_dict["key"], json_dict["name"], json_dict["version"],
                   json_dict["build"], json_dict["packages"],
                   json_dict["python"], fake_size, fake_md5,
                   json_dict.get("mtime", 0.0), json_dict.get("product", None),
                   json_dict.get("available", True),
                   json_dict.get("store_location", ""))

    def __init__(self, key, name, version, build, packages, python, size, md5,
                 mtime, product, available, store_location):
        self._package = PackageMetadata(key, name, version, build, packages, python, size, md5)

        self.mtime = mtime
        self.product = product
        self.available = available
        self.store_location = store_location

        self.type = "egg"

    @property
    def s3index_data(self):
        """
        Returns a dict that may be converted to json to re-create our legacy S3
        index content
        """
        keys = ("available", "build", "md5", "name", "packages", "product",
                "python", "mtime", "size", "type", "version")
        return dict((k, getattr(self, k)) for k in keys)

    def __repr__(self):
        template = "RepositoryPackageMetadata(" \
            "'{self.name}-{self.version}-{self.build}', key={self.key!r}, " \
            "available={self.available!r}, product={self.product!r}, " \
            "store_location={self.store_location!r}".format(self=self)
        return template.format(self.name, self.version, self.build, self.key,
                               self.available, self.product,
                               self.store_location)

    @property
    def comparable_version(self):
        return self._package.comparable_version

    # FIXME: would be nice to have some basic delegate functionalities instead
    @property
    def key(self):
        return self._package.key

    @property
    def name(self):
        return self._package.name

    @property
    def version(self):
        return self._package.version

    @property
    def build(self):
        return self._package.build

    @property
    def packages(self):
        return self._package.packages

    @property
    def python(self):
        return self._package.python

    @property
    def md5(self):
        return self._package.md5

    @property
    def size(self):
        return self._package.size

    @property
    def full_version(self):
        return self._package.full_version


def parse_version(version):
    """
    Parse a full version (e.g. '1.8.0-1' into upstream and build)

    Parameters
    ----------
    version: str

    Returns
    -------
    upstream_version: str
    build: int
    """
    parts = version.split("-")
    if len(parts) != 2:
        raise ValueError("Version not understood {0!r}".format(version))
    else:
        return parts[0], int(parts[1])

def egg_name_to_name_version(egg_name):
    """
    Convert a eggname (filename) to a (name, version) pair.

    Parameters
    ----------
    egg_name: str
        The egg filename

    Returns
    -------
    name: str
        The name
    version: str
        The *full* version (e.g. for 'numpy-1.8.0-1.egg', the full version is
        '1.8.0-1')
    """
    basename = os.path.splitext(os.path.basename(egg_name))[0]
    parts = basename.split("-", 1)
    if len(parts) != 2:
        raise ValueError("Invalid egg name: {0!r}".format(egg_name))
    else:
        return parts[0].lower(), parts[1]


def _valid_meta_dir_iterator(prefixes):
    for prefix in prefixes:
        egg_info_root = os.path.join(prefix, "EGG-INFO")
        if os.path.isdir(egg_info_root):
            for path in os.listdir(egg_info_root):
                meta_dir = os.path.join(egg_info_root, path)
                yield egg_info_root, meta_dir


class Repository(object):
    """
    A Repository is a set of package, and knows about which package it
    contains.
    """
    @classmethod
    def _from_prefixes(cls, prefixes=None):
        """
        Create a repository representing the *installed* packages.

        Parameters
        ----------
        prefixes: seq
            List of prefixes. [sys.prefix] by default
        """
        if prefixes is None: #  pragma: nocover
            prefixes = [sys.prefix]

        repository = cls()
        for egg_info_root, meta_dir in _valid_meta_dir_iterator(prefixes):
            info = info_from_metadir(meta_dir)
            if info is not None:
                info["store_location"] = egg_info_root

                package = \
                    RepositoryPackageMetadata.from_installed_meta_dict(info)
                repository.add_package(package)

        return repository

    @classmethod
    def _from_store(cls, store):
        assert store.is_connected, "This method expected an already connected store."

        _store_info = store.info()
        store_info = _store_info.get("root") if _store_info else ""

        repository = cls(store_info)

        for key in store.query_keys(type="egg"):
            raw_metadata = store.get_metadata(key)
            raw_metadata["store_location"] = store_info
            package = RepositoryPackageMetadata.from_json_dict(key,
                                                               raw_metadata)
            repository.add_package(package)
        return repository

    def __init__(self, store_info=""):
        self._name_to_packages = collections.defaultdict(list)
        self._packages = []

        self._store_info = ""

    def add_package(self, package_metadata):
        self._packages.append(package_metadata)
        self._name_to_packages[package_metadata.name].append(package_metadata)

    def has_package(self, package_metadata):
        """
        Returns True if the given package is available in this repository
        """
        candidates = self._name_to_packages.get(package_metadata.name, [])
        for candidate in candidates:
            if candidate.full_version == package_metadata.full_version:
                return True
        return False

    def find_package(self, name, version):
        """ Search for the first match of a package with the given name and
        version.

        Returns
        -------
        package: RepositoryPackageMetadata
            The corresponding metadata
        """
        candidates = self._name_to_packages.get(name, [])
        for candidate in candidates:
            if candidate.full_version == version:
                return candidate
        raise MissingPackage("Package '{0}-{1}' not found".format(name,
                                                                  version))


    def find_packages(self, name, version=None):
        """
        Returns a list of package metadata with the given name and version

        Parameters
        ----------
        name: str
            The package's name
        version: str or None
            If not None, the version to look for

        Returns
        -------
        packages: seq of RepositoryPackageMetadata
            The corresponding metadata (order is unspecified)
        """
        candidates = self._name_to_packages.get(name, [])
        if version is None:
            return [package for package in candidates]
        else:
            return [package for package in candidates if package.full_version == version]

    def iter_packages(self):
        """
        Iter over each package of the repository

        Returns
        -------
        packages: iterable of RepositoryPackageMetadata
            The corresponding metadata
        """
        for package in self._packages:
            yield package

    def iter_most_recent_packages(self):
        """
        Iter over each package of the repository, but only the most recent
        version of a given package

        Returns
        -------
        packages: iterable of RepositoryPackageMetadata
            The corresponding metadata
        """
        for name, packages in self._name_to_packages.items():
            sorted_by_version = sorted(packages,
                                       key=operator.attrgetter("comparable_version"))
            yield sorted_by_version[-1]
