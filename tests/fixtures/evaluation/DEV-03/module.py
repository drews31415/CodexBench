DEFAULT_LIMIT = 10


def _load_limit(config):
    return int(config.get("limit", DEFAULT_LIMIT))


def can_accept(config, queued):
    return queued < _load_limit(config)
