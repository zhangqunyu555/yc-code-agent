"""Small text normalization helpers."""


def normalize_space(text: str) -> str:
    return " ".join(text.split())
