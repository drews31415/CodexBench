def insert_job(queue, due_tick, priority, job_id):
    queue.append((due_tick, -priority, job_id))
    queue.sort()
