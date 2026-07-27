from __future__ import annotations


def normalize_command_text(value: str) -> str:
    """Normalize common email-editor wrappers without changing internal delimiters."""
    text = value.strip()
    while text.startswith("|"):
        text = text[1:].lstrip()
    while text.endswith("|"):
        text = text[:-1].rstrip()
    return text
