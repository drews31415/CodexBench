def valid(row):
    return all(isinstance(row.get(key), str) and row[key] for key in ("id", "label"))
