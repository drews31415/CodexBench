def count_by_status(records, statuses):
    requested = set(statuses)
    counts = {}
    for status in records:
        if status in requested:
            counts[status] = counts.get(status, 0) + 1
    return counts
