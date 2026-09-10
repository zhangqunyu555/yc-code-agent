import unittest

from cache import is_expired
from pagination import page_count
from text_utils import normalize_space


class MorePublicTests(unittest.TestCase):
    def test_cache_before_and_after_expiration(self):
        self.assertFalse(is_expired(10, 5, 14))
        self.assertTrue(is_expired(10, 5, 16))

    def test_divisible_page_count(self):
        self.assertEqual(page_count(20, 10), 2)

    def test_already_normalized_text(self):
        self.assertEqual(normalize_space("hello world"), "hello world")


if __name__ == "__main__":
    unittest.main()
