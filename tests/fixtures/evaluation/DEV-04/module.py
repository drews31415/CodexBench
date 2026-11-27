def count_by_status(records, statuses):
    counts = dict.fromkeys(statuses, 0)
    for status in records:
        if status in counts:
            counts[status] += 1
    return counts
