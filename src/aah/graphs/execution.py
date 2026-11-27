"""Build the execution graph from normalized observations and approved check receipts."""

from collections import defaultdict
from collections.abc import Mapping

from aah.diagnosis.detector import case_signal_keys, comparable, trusted
from aah.domain import ContractError
from aah.evidence.ledger import EvidenceView

from .artifact import Builder, Node, identity


KINDS = {"COMMAND", "COMMAND_RESULT", "SEARCH", "FILE_ACCESS", "TEST", "FAILURE", "CHANGE", "INTERVENTION"}


def build(view: EvidenceView, run_id: str, snapshot_id: str, approved: tuple[str, ...]):
    graph = Builder()
    checks = {row.check_result_id: row for row in view.checks if row.check_id in approved}
    commands, tests, failures = {}, {}, {}
    case_nodes = {}
    def connect(source, target, relation, **basis):
        graph.edge(source, target, relation, (*graph.nodes[source].evidence_ids,
                                              *graph.nodes[target].evidence_ids), **basis)
    preceding = {}
    last_check = {}
    for row in checks.values():
        prior = last_check.get(row.check_id)
        if prior and comparable(prior, row):
            preceding[row.check_result_id] = prior.check_result_id
        last_check[row.check_id] = row
    temporal = defaultdict(list)
    ordered = defaultdict(lambda: defaultdict(list))
    seen = set()
    for record in view.records:
        if record.run_id != run_id:
            raise ContractError("foreign graph evidence")
        if record.evidence_id in seen or record.kind not in KINDS:
            continue
        seen.add(record.evidence_id)
        p, kind, turn = record.payload, record.kind, record.turn_id
        key = identity("exec", run_id, record.evidence_id)
        attrs = dict(p)
        attrs.update(evidence_id=record.evidence_id, turn_id=turn, sequence=record.sequence,
                     raw_refs=record.raw_refs, source_order=record.source_order)
        observed = record.observation_status == "observed"
        if kind in {"TEST", "FAILURE"}:
            rid = p["check_result_id"]
            check = checks.get(rid)
            if not (check and trusted(check) and check.snapshot_id == record.snapshot_id
                    and (check.turn_id == turn or check.turn_id is None and turn == "baseline") and observed):
                graph.warnings.add("UNVERIFIED_CHECK:" + record.evidence_id)
                continue
            if kind == "TEST":
                if check.check_id != p["check_id"] or check.status != p["status"]:
                    raise ContractError("graph check and evidence disagree")
                attrs["case_ids"] = tuple(c.test_id for c in check.test_cases)
            else:
                matches = [c for c in check.test_cases if c.status == "FAIL" and check.status == "FAIL"
                           and (c.fingerprint, c.frame, c.error_type, c.message) == (
                               p["fingerprint"], p["stable_frame"], p["error_type"], p["normalized_message"])]
                # Observer's stable ID also disambiguates equal fingerprints in one check.
                if len(matches) > 1:
                    matches = [c for c in matches if identity(turn, ["failure", rid, c.test_id], "FAILURE")
                               == record.evidence_id]
                if len(matches) != 1:
                    graph.warnings.add("UNRESOLVED_FAILURE:" + record.evidence_id)
                    continue
                attrs["test_id"] = matches[0].test_id
                attrs["check_id"] = check.check_id
                attrs["signal_keys"] = case_signal_keys(check, matches[0])
                frame = p["stable_frame"]
                attrs["stable_frame"] = ({"path": frame.get("path"), "line": frame.get("line")}
                                         if isinstance(frame, Mapping) else None)
                attrs.pop("normalized_message")
        if kind == "COMMAND_RESULT":
            attrs["result_ref"] = p["stdout_ref"] or p["stderr_ref"] or next(iter(record.raw_refs), None)
        graph.nodes[key] = Node(key, kind, "exec", record.snapshot_id, record.observation_status,
                                (record.evidence_id,), attrs)
        if not observed:
            continue
        if record.source_order is not None:
            ordered[turn][record.source_order].append(key)
        if kind == "COMMAND":
            commands[turn, p["command_id"]] = key
        elif kind == "COMMAND_RESULT" and (turn, p["command_id"]) in commands:
            connect(commands.pop((turn, p["command_id"])), key, "RETURNS", basis="command_id")
        elif kind == "TEST":
            tests[rid] = key
            for case in check.test_cases:
                if len(check.test_cases) == 1 or p["test_id"] == case.test_id:
                    case_nodes[rid, case.test_id] = key
                elif p["test_id"] is None:
                    case_key = identity("case", key, case.test_id)
                    graph.nodes[case_key] = Node(case_key, "TEST", "exec", record.snapshot_id, "observed",
                                                 (record.evidence_id,), {**attrs, "test_id": case.test_id,
                                                                         "status": case.status})
                    case_nodes[rid, case.test_id] = case_key
            # A new recovery explicitly ordered the approved checks after an intervention.
            prior_id = preceding.get(rid)
            if prior_id in tests and any(graph.nodes[n].kind == "INTERVENTION" for n in temporal[turn]):
                connect(key, tests[prior_id], "RETRIES", basis="approved_recovery_check")
        elif kind == "FAILURE":
            test_id = attrs["test_id"]
            parent = case_nodes.get((rid, test_id))
            if parent:
                connect(parent, key, "RETURNS", basis="approved_case", test_id=test_id)
            previous = failures.get((preceding.get(rid), test_id, p["fingerprint"]))
            if previous and p["fingerprint"]:
                connect(key, previous, "SAME_FAILURE_AS", basis="consecutive_comparable_check")
            failures[rid, test_id, p["fingerprint"]] = key
        if kind in ("TEST", "FAILURE"):
            for prior in temporal[turn]:
                connect(prior, key, "OBSERVED_AFTER", basis="post_turn_public_check")
        if kind == "CHANGE" or kind == "INTERVENTION" and p["delivery_status"] == "DELIVERED":
            temporal[turn].append(key)

    for groups in ordered.values():
        previous = None
        for _, group in sorted(groups.items()):
            current = group[0] if len(group) == 1 else None
            if current and previous:
                connect(previous, current, "NEXT", basis="source_order")
            previous = current
    return graph.finish("execution", run_id, snapshot_id, len(view.records))
