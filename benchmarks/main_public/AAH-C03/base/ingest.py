from records import valid
from repository import add_record


def ingest_batch(rows, store):
    report = {"accepted": [], "errors": []}
    reserved_ids = set()
    for index, row in enumerate(rows, 1):
        if not valid(row):
            row_id = row.get("id")
            if isinstance(row_id, str) and row_id:
                reserved_ids.add(row_id)
            report["errors"].append({"row": index, "code": "INVALID"})
            continue
        if row["id"] in reserved_ids or not add_record(store, row):
            report["errors"].append({"row": index, "code": "DUPLICATE"})
            continue
        report["accepted"].append(row["id"])
    return report
