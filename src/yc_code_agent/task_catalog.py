"""Twenty deterministic, self-authored Python repair tasks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    prompt: str
    source: str
    hidden_test: str
    old: str
    new: str

    @property
    def allowed_paths(self) -> set[str]:
        return {"solution.py"}

    @property
    def entrypoint(self) -> str:
        return self.source.split("def ", 1)[1].split("(", 1)[0]

    @property
    def public_test(self) -> str:
        return f"""import unittest
import solution

class PublicSmokeTest(unittest.TestCase):
    def test_entrypoint_exists(self):
        self.assertTrue(callable(solution.{self.entrypoint}))
"""


def _task(number: int, prompt: str, source: str, test_body: str, old: str, new: str) -> Task:
    tests = "import unittest\nfrom solution import *\n\nclass HiddenTest(unittest.TestCase):\n" + test_body + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    return Task(f"yc-{number:02d}", prompt, source, tests, old, new)


TASKS = [
    _task(1, "修复 clamp，使结果始终位于 lower 和 upper 之间。", "def clamp(value, lower, upper):\n    return max(upper, min(lower, value))\n", "    def test_bounds(self):\n        self.assertEqual(clamp(5, 0, 10), 5)\n        self.assertEqual(clamp(-2, 0, 10), 0)\n        self.assertEqual(clamp(20, 0, 10), 10)", "return max(upper, min(lower, value))", "return max(lower, min(upper, value))"),
    _task(2, "修复 is_palindrome，使其忽略大小写和非字母数字字符。", "def is_palindrome(text):\n    return text == text[::-1]\n", "    def test_normalization(self):\n        self.assertTrue(is_palindrome('A man, a plan, a canal: Panama!'))\n        self.assertFalse(is_palindrome('agent'))", "return text == text[::-1]", "clean = ''.join(char.lower() for char in text if char.isalnum())\n    return clean == clean[::-1]"),
    _task(3, "修复 chunks，保留最后一个不足 size 的分块。", "def chunks(items, size):\n    if size <= 0:\n        raise ValueError('size must be positive')\n    return [items[i:i + size] for i in range(0, len(items) - size, size)]\n", "    def test_tail(self):\n        self.assertEqual(chunks([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])\n        self.assertEqual(chunks([], 2), [])", "range(0, len(items) - size, size)", "range(0, len(items), size)"),
    _task(4, "修复 unique，使其去重同时保留首次出现顺序。", "def unique(items):\n    return list(set(items))\n", "    def test_order(self):\n        self.assertEqual(unique([3, 1, 3, 2, 1]), [3, 1, 2])", "return list(set(items))", "return list(dict.fromkeys(items))"),
    _task(5, "修复 mean：空序列返回 0，非空序列返回平均值。", "def mean(values):\n    return sum(values) / len(values)\n", "    def test_values(self):\n        self.assertEqual(mean([]), 0)\n        self.assertEqual(mean([2, 4, 6]), 4)", "return sum(values) / len(values)", "return sum(values) / len(values) if values else 0"),
    _task(6, "修复 flatten_once，只展开一层嵌套列表。", "def flatten_once(groups):\n    result = []\n    for group in groups:\n        result.append(group)\n    return result\n", "    def test_flatten(self):\n        self.assertEqual(flatten_once([[1, 2], [], [3]]), [1, 2, 3])", "result.append(group)", "result.extend(group)"),
    _task(7, "修复 merge_defaults，使 overrides 中的值优先于 defaults。", "def merge_defaults(defaults, overrides):\n    return {**overrides, **defaults}\n", "    def test_precedence(self):\n        self.assertEqual(merge_defaults({'a': 1, 'b': 2}, {'b': 9}), {'a': 1, 'b': 9})", "return {**overrides, **defaults}", "return {**defaults, **overrides}"),
    _task(8, "修复 fibonacci，使 fibonacci(0)=0、fibonacci(1)=1。", "def fibonacci(n):\n    if n < 0:\n        raise ValueError('n must be non-negative')\n    a, b = 0, 1\n    for _ in range(n + 1):\n        a, b = b, a + b\n    return a\n", "    def test_sequence(self):\n        self.assertEqual([fibonacci(i) for i in range(7)], [0, 1, 1, 2, 3, 5, 8])", "range(n + 1)", "range(n)"),
    _task(9, "修复 slugify：转小写、去掉首尾空白，并把连续空白变成一个连字符。", "def slugify(text):\n    return text.lower().replace(' ', '-')\n", "    def test_spaces(self):\n        self.assertEqual(slugify('  Hello   Code Agent  '), 'hello-code-agent')", "return text.lower().replace(' ', '-')", "return '-'.join(text.lower().split())"),
    _task(10, "修复 count_words，使任意连续空白都只作为分隔符。", "def count_words(text):\n    return len(text.split(' '))\n", "    def test_whitespace(self):\n        self.assertEqual(count_words('one  two\\nthree'), 3)\n        self.assertEqual(count_words('   '), 0)", "return len(text.split(' '))", "return len(text.split())"),
    _task(11, "修复 safe_divide：仅当分母为 0 时返回 None。", "def safe_divide(numerator, denominator):\n    if numerator == 0:\n        return None\n    return numerator / denominator\n", "    def test_zero(self):\n        self.assertEqual(safe_divide(0, 5), 0)\n        self.assertIsNone(safe_divide(5, 0))", "if numerator == 0:", "if denominator == 0:"),
    _task(12, "修复 rotate_right，使空列表和 steps 大于列表长度时也正确。", "def rotate_right(items, steps):\n    return items[-steps:] + items[:-steps]\n", "    def test_rotation(self):\n        self.assertEqual(rotate_right([1, 2, 3], 4), [3, 1, 2])\n        self.assertEqual(rotate_right([], 3), [])", "return items[-steps:] + items[:-steps]", "if not items:\n        return []\n    steps %= len(items)\n    return items[-steps:] + items[:-steps]"),
    _task(13, "修复 unique_casefold：忽略大小写去重并保留首次出现的原字符串。", "def unique_casefold(items):\n    return list(dict.fromkeys(items))\n", "    def test_case(self):\n        self.assertEqual(unique_casefold(['API', 'api', 'Agent', 'AGENT']), ['API', 'Agent'])", "return list(dict.fromkeys(items))", "seen = set()\n    result = []\n    for item in items:\n        key = item.casefold()\n        if key not in seen:\n            seen.add(key)\n            result.append(item)\n    return result"),
    _task(14, "修复 parse_bool，识别常用真假字符串并拒绝未知值。", "def parse_bool(value):\n    return bool(value)\n", "    def test_values(self):\n        for value in ('true', 'YES', '1', 'on'):\n            self.assertTrue(parse_bool(value))\n        for value in ('false', 'No', '0', 'off'):\n            self.assertFalse(parse_bool(value))\n        with self.assertRaises(ValueError):\n            parse_bool('maybe')", "return bool(value)", "normalized = value.strip().casefold()\n    if normalized in {'true', 'yes', '1', 'on'}:\n        return True\n    if normalized in {'false', 'no', '0', 'off'}:\n        return False\n    raise ValueError(f'invalid boolean: {value}')"),
    _task(15, "修复 median，使偶数长度序列返回中间两个数的平均值。", "def median(values):\n    if not values:\n        raise ValueError('empty sequence')\n    ordered = sorted(values)\n    return ordered[len(ordered) // 2]\n", "    def test_even_and_odd(self):\n        self.assertEqual(median([9, 1, 5]), 5)\n        self.assertEqual(median([1, 2, 8, 10]), 5)", "return ordered[len(ordered) // 2]", "middle = len(ordered) // 2\n    if len(ordered) % 2:\n        return ordered[middle]\n    return (ordered[middle - 1] + ordered[middle]) / 2"),
    _task(16, "修复 binary_search 的右边界，避免查找缺失值时越界。", "def binary_search(items, target):\n    low, high = 0, len(items)\n    while low <= high:\n        middle = (low + high) // 2\n        if items[middle] == target:\n            return middle\n        if items[middle] < target:\n            low = middle + 1\n        else:\n            high = middle - 1\n    return -1\n", "    def test_search(self):\n        self.assertEqual(binary_search([1, 3, 5], 5), 2)\n        self.assertEqual(binary_search([1, 3, 5], 9), -1)\n        self.assertEqual(binary_search([], 1), -1)", "low, high = 0, len(items)", "low, high = 0, len(items) - 1"),
    _task(17, "修复 transpose，使其正确交换矩阵的行列。", "def transpose(matrix):\n    return [list(row) for row in zip(matrix)]\n", "    def test_matrix(self):\n        self.assertEqual(transpose([[1, 2, 3], [4, 5, 6]]), [[1, 4], [2, 5], [3, 6]])\n        self.assertEqual(transpose([]), [])", "zip(matrix)", "zip(*matrix)"),
    _task(18, "修复 is_prime 的除数范围。", "def is_prime(n):\n    if n < 2:\n        return False\n    for divisor in range(1, int(n ** 0.5) + 1):\n        if n % divisor == 0:\n            return False\n    return True\n", "    def test_primes(self):\n        self.assertTrue(is_prime(2))\n        self.assertTrue(is_prime(29))\n        self.assertFalse(is_prime(1))\n        self.assertFalse(is_prime(21))", "range(1, int(n ** 0.5) + 1)", "range(2, int(n ** 0.5) + 1)"),
    _task(19, "修复 gcd，使返回值始终非负。", "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a\n", "    def test_sign(self):\n        self.assertEqual(gcd(54, 24), 6)\n        self.assertEqual(gcd(-54, 24), 6)\n        self.assertEqual(gcd(0, -5), 5)", "return a", "return abs(a)"),
    _task(20, "修复 format_bytes，使 1024 字节正确显示为 1.0 KB。", "def format_bytes(size):\n    units = ['B', 'KB', 'MB', 'GB']\n    value = float(size)\n    unit = 0\n    while value > 1024 and unit < len(units) - 1:\n        value /= 1024\n        unit += 1\n    return f'{value:.1f} {units[unit]}'\n", "    def test_boundaries(self):\n        self.assertEqual(format_bytes(0), '0.0 B')\n        self.assertEqual(format_bytes(1024), '1.0 KB')\n        self.assertEqual(format_bytes(1024 ** 2), '1.0 MB')", "while value > 1024", "while value >= 1024"),
]
