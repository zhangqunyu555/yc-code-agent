import unittest

from calculator import clamp


class ClampPublicTest(unittest.TestCase):
    def test_in_range_and_lower_bound(self):
        self.assertEqual(clamp(5, 0, 10), 5)
        self.assertEqual(clamp(-2, 0, 10), 0)


if __name__ == "__main__":
    unittest.main()
