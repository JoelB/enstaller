from egginst._compat import TestCase

from ..enpkg import EnpkgVersion
from ..pep386_workaround import PEP386WorkaroundVersion


class TestEnpkgVersionParsing(TestCase):
    def test_from_string_valid(self):
        # Given
        s = "1.3.0-1"

        # When
        version = EnpkgVersion.from_string(s)

        # Then
        self.assertEqual(version.upstream,
                         PEP386WorkaroundVersion.from_string("1.3.0"))
        self.assertEqual(version.build, 1)

        # Given
        s = "1.2.0-3"

        # When
        version = EnpkgVersion.from_string(s)

        # Then
        self.assertEqual(version.upstream,
                         PEP386WorkaroundVersion.from_string("1.2.0"))
        self.assertEqual(version.build, 3)

        # Given
        s = "1.2-3"

        # When
        version = EnpkgVersion.from_string(s)

        # Then
        self.assertEqual(version.upstream,
                         PEP386WorkaroundVersion.from_string("1.2.0"))
        self.assertEqual(version.build, 3)

    def test_from_string_invalid(self):
        # Given
        s = "1.3.0"

        # When/Then
        with self.assertRaises(ValueError):
            EnpkgVersion.from_string(s)

        # Given
        s = "1.3.0-a"

        # When/Then
        with self.assertRaises(ValueError):
            EnpkgVersion.from_string(s)

    def test_cannot_compare(self):
        # Given
        left = EnpkgVersion.from_string("1.3.0-1")
        right = PEP386WorkaroundVersion.from_string("1.3.0")

        # When/Then
        with self.assertRaises(TypeError):
            left == right

        # When/Then
        with self.assertRaises(TypeError):
            left < right

        # When/Then
        with self.assertRaises(TypeError):
            left > right

        # When/Then
        with self.assertRaises(TypeError):
            left <= right

        # When/Then
        with self.assertRaises(TypeError):
            left >= right


class TestEnpkgVersionComparison(TestCase):
    def test_equal(self):
        # Given
        left = EnpkgVersion.from_string("1.2.0-1")
        right = EnpkgVersion.from_string("1.2.0-1")

        # When/Then
        self.assertEqual(left, right)

        # Given
        left = EnpkgVersion.from_string("1.2.0-1")
        right = EnpkgVersion.from_string("1.2-1")

        # When/Then
        self.assertEqual(left, right)

        # Given
        left = EnpkgVersion.from_string("1.2.0-1")
        right = EnpkgVersion.from_string("1.2.0-2")

        # When/Then
        self.assertNotEqual(left, right)

        # Given
        left = EnpkgVersion.from_string("1.1.0-1")
        right = EnpkgVersion.from_string("1.2.0-1")

        # When/Then
        self.assertNotEqual(left, right)

    def test_less_than(self):
        # Given
        left = EnpkgVersion.from_string("1.2.0-1")
        right = EnpkgVersion.from_string("1.2.0-1")

        # When/Then
        self.assertTrue(left <= right)
        self.assertTrue(left >= right)
        self.assertFalse(left < right)
        self.assertFalse(left > right)

        # Given
        left = EnpkgVersion.from_string("1.2.0-1")
        right = EnpkgVersion.from_string("1.2.0-2")

        # When/Then
        self.assertTrue(left <= right)
        self.assertTrue(left < right)
        self.assertFalse(left >= right)
        self.assertFalse(left > right)

        # Given
        left = EnpkgVersion.from_string("1.2.0-2")
        right = EnpkgVersion.from_string("1.2.1-1")

        # When/Then
        self.assertTrue(left <= right)
        self.assertTrue(left < right)
        self.assertFalse(left >= right)
        self.assertFalse(left > right)


class TestEnpkgVersionMisc(TestCase):
    def test_string(self):
        # Given
        r_version_string = "1.3.0-1"
        v = EnpkgVersion.from_string(r_version_string)

        # When
        version_string = str(v)

        # Then
        self.assertEqual(version_string, r_version_string)