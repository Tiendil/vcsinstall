from urllib.parse import unquote


def is_url(value: str) -> bool:
    """Distinguish URL notation from native paths and scp-style addresses."""
    return "://" in value


def decode_url(value: str) -> str:
    """Decode percent escapes once while preserving literal plus signs."""
    return unquote(value)
