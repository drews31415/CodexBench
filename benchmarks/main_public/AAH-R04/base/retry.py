from ordered import insert_job


def enqueue_retry(queue, job, now):
    if job["cancelled"]:
        return False
    insert_job(queue, now + 1, job["priority"], job["id"])
    return True
