def is_live(now, expires_at):
    """Return whether an entry may be served at the supplied time."""
    return expires_at is None or now < expires_at
