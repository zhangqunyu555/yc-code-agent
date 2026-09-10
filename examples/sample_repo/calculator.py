def clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)
