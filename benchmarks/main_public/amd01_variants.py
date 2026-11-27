"""AMD-01 objective-check variants, written from the public task specifications only.

The development session that wrote these files has no access to curator suites or reference
implementations. ``alternative`` variants are intended to satisfy the public specification by a
different route (BQ04/BQ06); ``partial`` variants are plausible incomplete fixes (BQ05). Each
partial states which specification sentence it violates. The curator side runs them against the
private suites; the development side only runs the public suites.
"""

from aah.domain import canonical_json_bytes, sha256_bytes


def _v(name, kind, intent, path, source):
    return {"name": name, "kind": kind, "intent": intent, "files": {path: source.lstrip("\n")}}


VARIANTS = {
    "AAH-S02": [
        _v("alt-sum-then-round", "alternative", "Sum exact Decimal products, round once with HALF_UP.", "line_total.py", '''
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def invoice_total(lines):
    total = sum((Decimal(price) * quantity for price, quantity in lines), Decimal("0"))
    return total.quantize(CENT, rounding=ROUND_HALF_UP)
'''),
        _v("alt-fraction-accumulator", "alternative", "Accumulate with Fraction, convert once, round HALF_UP.",
           "line_total.py", '''
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction


def invoice_total(lines):
    exact = Fraction(0)
    for price, quantity in lines:
        exact += Fraction(Decimal(price)) * quantity
    with localcontext() as context:
        context.prec = 100
        amount = Decimal(exact.numerator) / Decimal(exact.denominator)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
'''),
        _v("partial-half-even", "partial", "Rounds once but with banker's rounding; spec requires ROUND_HALF_UP.",
           "line_total.py", '''
from decimal import Decimal, ROUND_HALF_EVEN


def invoice_total(lines):
    total = sum((Decimal(price) * quantity for price, quantity in lines), Decimal("0"))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
'''),
        _v("partial-intermediate-mills", "partial", "Rounds each line to 3 places first; spec sums exactly.",
           "line_total.py", '''
from decimal import Decimal, ROUND_HALF_UP


def invoice_total(lines):
    total = Decimal("0")
    for price, quantity in lines:
        total += (Decimal(price) * quantity).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
'''),
        _v("partial-truncate", "partial", "Truncates instead of rounding HALF_UP.", "line_total.py", '''
from decimal import Decimal, ROUND_DOWN


def invoice_total(lines):
    total = sum((Decimal(price) * quantity for price, quantity in lines), Decimal("0"))
    return total.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
'''),
    ],
    "AAH-S03": [
        _v("alt-dictreader-empty-check", "alternative", "Exactly empty score is None, otherwise int().",
           "csv_rows.py", '''
import csv
from io import StringIO


def load_rows(text):
    rows = csv.DictReader(StringIO(text))
    return [{"id": row["id"], "score": None if row["score"] == "" else int(row["score"])} for row in rows]
'''),
        _v("alt-csv-reader-columns", "alternative", "Parse with csv.reader and header column positions.",
           "csv_rows.py", '''
import csv
from io import StringIO


def load_rows(text):
    reader = csv.reader(StringIO(text))
    header = next(reader, None)
    if header is None:
        return []
    id_at, score_at = header.index("id"), header.index("score")
    result = []
    for fields in reader:
        if not fields:
            continue
        raw = fields[score_at]
        result.append({"id": fields[id_at], "score": None if raw == "" else int(raw)})
    return result
'''),
        _v("partial-strip-blank", "partial", "Whitespace-only score becomes None; spec says only an exactly empty string.",
           "csv_rows.py", '''
import csv
from io import StringIO


def load_rows(text):
    rows = csv.DictReader(StringIO(text))
    return [{"id": row["id"], "score": None if not row["score"].strip() else int(row["score"])} for row in rows]
'''),
        _v("partial-float-coercion", "partial", "Accepts float-like text such as 3.0; spec requires ValueError for invalid integers.",
           "csv_rows.py", '''
import csv
from io import StringIO


def load_rows(text):
    rows = csv.DictReader(StringIO(text))
    return [{"id": row["id"], "score": None if row["score"] == "" else int(float(row["score"]))} for row in rows]
'''),
        _v("partial-strip-id", "partial", "Strips the id; spec keeps the original id string.", "csv_rows.py", '''
import csv
from io import StringIO


def load_rows(text):
    rows = csv.DictReader(StringIO(text))
    return [{"id": row["id"].strip(), "score": None if row["score"] == "" else int(row["score"])} for row in rows]
'''),
    ],
    "AAH-R03": [
        _v("alt-two-patterns", "alternative", "Two full-match ASCII patterns, then datetime.date.", "day_format.py", '''
import re
from datetime import date

DASHED = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
COMPACT = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})")


def parse_day(value):
    match = DASHED.fullmatch(value) or COMPACT.fullmatch(value)
    if match is None:
        raise ValueError("expected a calendar date")
    return date(*map(int, match.groups()))
'''),
        _v("alt-length-and-strptime", "alternative", "Shape check by length and ASCII digits, then strptime.",
           "day_format.py", '''
from datetime import datetime


def _ascii_digits(text):
    return text.isascii() and text.isdigit()


def parse_day(value):
    if len(value) == 10 and value[4] == "-" and value[7] == "-" and _ascii_digits(value[:4] + value[5:7] + value[8:]):
        return datetime.strptime(value, "%Y-%m-%d").date()
    if len(value) == 8 and _ascii_digits(value):
        return datetime.strptime(value, "%Y%m%d").date()
    raise ValueError("expected a calendar date")
'''),
        _v("partial-unicode-digits", "partial", "Uses \\d, so non-ASCII digits pass; spec allows ASCII digits only.",
           "day_format.py", '''
import re
from datetime import date


def parse_day(value):
    match = re.fullmatch(r"(\\d{4})-(\\d{2})-(\\d{2})", value) or re.fullmatch(r"(\\d{4})(\\d{2})(\\d{2})", value)
    if match is None:
        raise ValueError("expected a calendar date")
    return date(*map(int, match.groups()))
'''),
        _v("partial-unchecked-compact", "partial", "Compact branch trusts strptime; 7-digit input like 2024229 is accepted.",
           "day_format.py", '''
import re
from datetime import datetime


def parse_day(value):
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None:
        return datetime.strptime(value, "%Y-%m-%d").date()
    if value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    raise ValueError("expected a calendar date")
'''),
        _v("partial-dollar-anchor", "partial", "Uses re.match with $; a trailing newline after a compact date is accepted.",
           "day_format.py", '''
import re
from datetime import date, datetime


def parse_day(value):
    if re.match(r"[0-9]{8}$", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("expected a calendar date")
    return datetime.strptime(value, "%Y-%m-%d").date()
'''),
    ],
    "AAH-R04": [
        _v("alt-min-power", "alternative", "Delay min(2**(failures-1), 8) through insert_job.", "retry.py", '''
from ordered import insert_job


def enqueue_retry(queue, job, now):
    if job["cancelled"]:
        return False
    delay = min(2 ** (job["failures"] - 1), 8)
    insert_job(queue, now + delay, job["priority"], job["id"])
    return True
'''),
        _v("alt-branch-and-sort", "alternative", "Explicit cap branch, append and sort the full queue.", "retry.py", '''
def enqueue_retry(queue, job, now):
    if job["cancelled"]:
        return False
    failures = job["failures"]
    delay = 8 if failures >= 4 else 2 ** (failures - 1)
    queue.append((now + delay, -job["priority"], job["id"]))
    queue.sort()
    return True
'''),
        _v("partial-cap-by-failures", "partial", "Caps after 8 failures instead of at 8 ticks; failures 5-7 overshoot.",
           "retry.py", '''
from ordered import insert_job


def enqueue_retry(queue, job, now):
    if job["cancelled"]:
        return False
    failures = job["failures"]
    delay = 8 if failures >= 8 else 2 ** (failures - 1)
    insert_job(queue, now + delay, job["priority"], job["id"])
    return True
'''),
        _v("partial-float-tick", "partial", "Computes the delay with math.pow, so due ticks become floats.", "retry.py", '''
import math
from ordered import insert_job


def enqueue_retry(queue, job, now):
    if job["cancelled"]:
        return False
    delay = min(math.pow(2, job["failures"] - 1), 8)
    insert_job(queue, now + delay, job["priority"], job["id"])
    return True
'''),
        _v("partial-no-id-tiebreak", "partial", "Sorts by due tick and priority only, dropping the job_id order of the existing sort.",
           "retry.py", '''
def enqueue_retry(queue, job, now):
    if job["cancelled"]:
        return False
    delay = min(2 ** (job["failures"] - 1), 8)
    queue.append((now + delay, -job["priority"], job["id"]))
    queue.sort(key=lambda entry: entry[:2])
    return True
'''),
    ],
    "AAH-D02": [
        _v("alt-release-linked", "alternative", "Release the order's reservation_id through stock.release.", "orders.py", '''
from stock import release


def cancel(orders, stock, order_id):
    order = orders[order_id]
    if order["state"] == "cancelled":
        return False
    release(stock, order["reservation_id"])
    order["state"] = "cancelled"
    return True
'''),
        _v("alt-inline-release", "alternative", "Inline release of the linked reservation.", "orders.py", '''
def cancel(orders, stock, order_id):
    order = orders[order_id]
    if order["state"] == "cancelled":
        return False
    for sku, quantity in stock["reserved"].pop(order["reservation_id"], ()):
        stock["available"][sku] = stock["available"].get(sku, 0) + quantity
    order["state"] = "cancelled"
    return True
'''),
        _v("partial-release-both-ids", "partial", "Also releases a reservation keyed by the order id; spec says the ids can differ.",
           "orders.py", '''
from stock import release


def cancel(orders, stock, order_id):
    order = orders[order_id]
    if order["state"] == "cancelled":
        return False
    release(stock, order["reservation_id"])
    release(stock, order_id)
    order["state"] = "cancelled"
    return True
'''),
        _v("partial-release-before-state-check", "partial", "Releases before checking cancellation; spec adds no stock for cancelled orders.",
           "orders.py", '''
from stock import release


def cancel(orders, stock, order_id):
    order = orders[order_id]
    release(stock, order["reservation_id"])
    if order["state"] == "cancelled":
        return False
    order["state"] = "cancelled"
    return True
'''),
        _v("partial-fallback-to-order-id", "partial", "Falls back to the order id when the linked reservation is absent.",
           "orders.py", '''
from stock import release


def cancel(orders, stock, order_id):
    order = orders[order_id]
    if order["state"] == "cancelled":
        return False
    linked = order["reservation_id"]
    release(stock, linked if linked in stock["reserved"] else order_id)
    order["state"] = "cancelled"
    return True
'''),
    ],
    "AAH-D04": [
        _v("alt-ordered-queue", "alternative", "Use the stored due_tick with ordered_queue.", "restore.py", '''
from queue_order import ordered_queue


def restore_queue(records, current_tick):
    return ordered_queue((record["due_tick"], record["task_id"]) for record in records)
'''),
        _v("alt-sorted-tuples", "alternative", "Sort (due_tick, task_id) tuples directly.", "restore.py", '''
def restore_queue(records, current_tick):
    return sorted((record["due_tick"], record["task_id"]) for record in records)
'''),
        _v("partial-falsy-zero-tick", "partial", "Treats a stored due_tick of 0 as missing and replaces it with current_tick.",
           "restore.py", '''
from queue_order import ordered_queue


def restore_queue(records, current_tick):
    return ordered_queue((record.get("due_tick") or current_tick, record["task_id"]) for record in records)
'''),
        _v("partial-clamp-overdue", "partial", "Moves overdue jobs to current_tick; spec keeps past due ticks.", "restore.py", '''
from queue_order import ordered_queue


def restore_queue(records, current_tick):
    return ordered_queue((max(record["due_tick"], current_tick), record["task_id"]) for record in records)
'''),
        _v("partial-due-only-sort", "partial", "Sorts by due_tick only, keeping record order for ties.", "restore.py", '''
def restore_queue(records, current_tick):
    return sorted(((record["due_tick"], record["task_id"]) for record in records), key=lambda item: item[0])
'''),
    ],
    "AAH-C02": [
        _v("alt-aggregate-then-commit", "alternative", "Aggregate quantities per SKU, check all, then deduct.",
           "reserve.py", '''
from collections import Counter


def reserve_order(stock, order_id, lines):
    if order_id in stock["reservations"]:
        return False
    needed = Counter()
    for sku, quantity in lines:
        needed[sku] += quantity
    available = stock["available"]
    if any(sku not in available or available[sku] < quantity for sku, quantity in needed.items()):
        return False
    for sku, quantity in needed.items():
        available[sku] -= quantity
    stock["reservations"][order_id] = list(lines)
    return True
'''),
        _v("alt-trial-copy", "alternative", "Apply take() to a trial copy and write back only on success.", "reserve.py", '''
from inventory import take


def reserve_order(stock, order_id, lines):
    if order_id in stock["reservations"]:
        return False
    trial = dict(stock["available"])
    for sku, quantity in lines:
        if not take(trial, sku, quantity):
            return False
    for sku, quantity in trial.items():
        stock["available"][sku] = quantity
    stock["reservations"][order_id] = list(lines)
    return True
'''),
        _v("partial-rebind-on-rollback", "partial", "Restores a deep copy on failure, replacing the caller's inner dictionaries.",
           "reserve.py", '''
from copy import deepcopy
from inventory import take


def reserve_order(stock, order_id, lines):
    if order_id in stock["reservations"]:
        return False
    backup = deepcopy(stock)
    for sku, quantity in lines:
        if not take(stock["available"], sku, quantity):
            stock.clear()
            stock.update(backup)
            return False
    stock["reservations"][order_id] = list(lines)
    return True
'''),
        _v("partial-alias-lines", "partial", "Stores the caller's list object; later caller edits change the reservation.",
           "reserve.py", '''
from collections import Counter


def reserve_order(stock, order_id, lines):
    if order_id in stock["reservations"]:
        return False
    needed = Counter()
    for sku, quantity in lines:
        needed[sku] += quantity
    if any(stock["available"].get(sku, 0) < quantity for sku, quantity in needed.items()):
        return False
    for sku, quantity in needed.items():
        stock["available"][sku] -= quantity
    stock["reservations"][order_id] = lines
    return True
'''),
        _v("partial-per-line-precheck", "partial", "Checks each line separately, missing duplicate-SKU totals.", "reserve.py", '''
def reserve_order(stock, order_id, lines):
    if order_id in stock["reservations"]:
        return False
    available = stock["available"]
    if any(sku not in available or available[sku] < quantity for sku, quantity in lines):
        return False
    for sku, quantity in lines:
        available[sku] -= quantity
    stock["reservations"][order_id] = list(lines)
    return True
'''),
    ],
    "AAH-C03": [
        _v("alt-no-reservation", "alternative", "Validate first; only stored ids count as duplicates.", "ingest.py", '''
from records import valid
from repository import add_record


def ingest_batch(rows, store):
    report = {"accepted": [], "errors": []}
    for index, row in enumerate(rows, 1):
        if not valid(row):
            report["errors"].append({"row": index, "code": "INVALID"})
            continue
        if not add_record(store, row):
            report["errors"].append({"row": index, "code": "DUPLICATE"})
            continue
        report["accepted"].append(row["id"])
    return report
'''),
        _v("alt-self-contained", "alternative", "Self-contained validity and duplicate checks.", "ingest.py", '''
def ingest_batch(rows, store):
    accepted, errors = [], []
    for number, row in enumerate(rows, start=1):
        ident, label = row.get("id"), row.get("label")
        if not (isinstance(ident, str) and ident and isinstance(label, str) and label):
            errors.append({"row": number, "code": "INVALID"})
        elif ident in store:
            errors.append({"row": number, "code": "DUPLICATE"})
        else:
            store[ident] = dict(row)
            accepted.append(ident)
    return {"accepted": accepted, "errors": errors}
'''),
        _v("partial-duplicate-before-validity", "partial", "Checks duplicates before validity; spec validates each row first.",
           "ingest.py", '''
from records import valid
from repository import add_record


def ingest_batch(rows, store):
    report = {"accepted": [], "errors": []}
    for index, row in enumerate(rows, 1):
        if row.get("id") in store:
            report["errors"].append({"row": index, "code": "DUPLICATE"})
            continue
        if not valid(row):
            report["errors"].append({"row": index, "code": "INVALID"})
            continue
        add_record(store, row)
        report["accepted"].append(row["id"])
    return report
'''),
        _v("partial-truthy-label", "partial", "Accepts any truthy label; spec requires a non-empty string.", "ingest.py", '''
from repository import add_record


def ingest_batch(rows, store):
    report = {"accepted": [], "errors": []}
    for index, row in enumerate(rows, 1):
        if not (isinstance(row.get("id"), str) and row["id"] and row.get("label")):
            report["errors"].append({"row": index, "code": "INVALID"})
            continue
        if not add_record(store, row):
            report["errors"].append({"row": index, "code": "DUPLICATE"})
            continue
        report["accepted"].append(row["id"])
    return report
'''),
        _v("partial-strip-blank-id", "partial", "Rejects whitespace-only ids and labels; spec accepts any non-empty string.",
           "ingest.py", '''
from repository import add_record


def ingest_batch(rows, store):
    report = {"accepted": [], "errors": []}
    for index, row in enumerate(rows, 1):
        ident, label = row.get("id"), row.get("label")
        if not (isinstance(ident, str) and ident.strip() and isinstance(label, str) and label.strip()):
            report["errors"].append({"row": index, "code": "INVALID"})
            continue
        if not add_record(store, row):
            report["errors"].append({"row": index, "code": "DUPLICATE"})
            continue
        report["accepted"].append(row["id"])
    return report
'''),
    ],
}


def manifest() -> dict:
    """Content hashes of every variant file, so curator and review records bind to the same bytes."""
    return {task: {v["name"]: {"kind": v["kind"], "files": {p: sha256_bytes(s.encode("utf-8")) for p, s in v["files"].items()}}
                   for v in variants} for task, variants in sorted(VARIANTS.items())}


MANIFEST_SHA256 = sha256_bytes(canonical_json_bytes(manifest()))
