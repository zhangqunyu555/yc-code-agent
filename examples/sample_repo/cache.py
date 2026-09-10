"""Cache expiration helpers."""


def is_expired(created_at: int, ttl: int, now: int) -> bool:
    return now >= created_at + ttl
