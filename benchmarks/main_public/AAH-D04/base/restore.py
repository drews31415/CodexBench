from queue_order import ordered_queue


def restore_queue(records, current_tick):
    return ordered_queue((current_tick, record["task_id"]) for record in records)
