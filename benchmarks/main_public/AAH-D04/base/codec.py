def dump_queue(queue):
    return [{"task_id": task_id, "due_tick": due} for due, task_id in queue]
