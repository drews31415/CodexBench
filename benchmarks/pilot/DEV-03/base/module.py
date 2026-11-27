DEFAULT_LIMIT = 10


def _load_limit(config):
    limit = config.get("limit", DEFAULT_LIMIT)
    return limit if isinstance(limit, int) else DEFAULT_LIMIT


def can_accept(config, queued):
    return queued < _load_limit(config)
