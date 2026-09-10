"""Pagination calculations."""


def page_count(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size
