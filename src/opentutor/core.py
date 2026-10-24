"""Example module. Replace with real code; keep the pattern: typed, validated, tested."""


def greet(name: str) -> str:
    """Return a greeting for ``name``.

    Raises:
        ValueError: if ``name`` is empty or only whitespace.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("name must not be empty")
    return f"Hello, {cleaned}!"
