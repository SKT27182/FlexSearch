"""User field helpers."""


def default_name_from_email(email: str) -> str:
    """Derive a display name from an email address."""
    local = email.split("@", 1)[0].strip()
    return local if local else "User"
