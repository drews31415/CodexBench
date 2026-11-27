import csv
from io import StringIO


def load_rows(text):
    rows = csv.DictReader(StringIO(text))
    return [{"id": row["id"], "score": int(row["score"] or "0") or None} for row in rows]
