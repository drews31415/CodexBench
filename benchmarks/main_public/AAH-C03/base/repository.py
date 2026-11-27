def add_record(store, row):
    key = row["id"]
    if key in store:
        return False
    store[key] = dict(row)
    return True
