"""Collect shared candidates from approved checks and verified public snapshots."""

import ast
import json
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from aah.diagnosis.detector import Detection, trusted
from aah.diagnosis.flat import Diagnosis
from aah.domain import ContractError, RunRequest, SnapshotRef, canonical_json_bytes, sha256_bytes
from aah.workspace.patch import digest_ref, read_manifest, verified_bytes
from aah.workspace.snapshot import WorkspaceError, checked_path, public_path


KINDS = frozenset({"optional_guidance", "code_reference", "code_excerpt", "failure_evidence",
                   "change_evidence", "prior_attempt", "relation_evidence"})
PUBLIC_PROVENANCE = frozenset({"approved_check", "public_snapshot", "public_ledger", "approved_guidance"})


def quote(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Candidate:
    kind: str
    source: str
    text: str
    provenance: str
    snapshot_id: str | None = None
    revision: str | None = None
    scope: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    span: tuple[int, int] | None = None
    retired: bool = False
    path_refs: tuple[str, ...] = ()

    @cached_property
    def content_hash(self) -> str:
        return sha256_bytes(self.text.encode("utf-8"))

    @cached_property
    def candidate_id(self) -> str:
        return sha256_bytes(canonical_json_bytes([
            self.kind, self.source, self.scope, self.revision, self.snapshot_id,
            self.content_hash, self.provenance, self.evidence_ids, self.span, self.retired, self.path_refs,
        ]))

    def origin(self) -> dict:
        return {"candidate_id": self.candidate_id, "kind": self.kind, "source": self.source, "snapshot_id": self.snapshot_id,
                "revision": self.revision, "provenance": self.provenance, "scope": self.scope,
                "evidence_ids": self.evidence_ids, "span": self.span, "content_hash": self.content_hash,
                "path_refs": self.path_refs}

    def validity(self, snapshot_id: str) -> str:
        if self.retired:
            return "INVALID"
        if not self.revision or not self.snapshot_id:
            return "UNCERTAIN"
        return "CURRENT" if self.snapshot_id == snapshot_id else "HISTORICAL"


@dataclass(frozen=True)
class CandidatePool:
    core: tuple[Candidate, ...]
    optional: tuple[Candidate, ...]
    targets: tuple[str, ...] = ()
    failure_anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    candidate: Candidate
    origins: tuple[Candidate, ...]

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id


def rejection(candidate: Candidate, *, graph_recovery: bool) -> str | None:
    if (candidate.kind == "relation_evidence" or candidate.path_refs or candidate.provenance == "graph_public"
            ) and not graph_recovery:
        return "SECURITY_REJECTED"
    if (not public_path(candidate.source) or any(not public_path(path) for path in candidate.scope)):
        return "SECURITY_REJECTED"
    trusted_origins = PUBLIC_PROVENANCE | ({"graph_public"} if graph_recovery else set())
    if (candidate.kind not in KINDS or candidate.provenance not in trusted_origins
            or not candidate.text.strip()):
        return "UNSUPPORTED_PROVENANCE"
    return None


def _files(root: Path, snapshot: SnapshotRef, query: str, changed: set[str]):
    paths = [row for row in read_manifest(root, snapshot)["files"]
             if row["state"] == "present" and row["path"].endswith(".py") and public_path(row["path"])]
    # Match complete path or basename against the task, preserving priority for observed changes.
    def targeted(row):
        names = (row["path"], row["path"].rsplit("/", 1)[-1])
        return row["path"] in changed or any(re.search(r"(?<![\w./-])" + re.escape(name) + r"(?![\w./-])",
                                                      query, re.IGNORECASE | re.ASCII) for name in names)

    ordered = sorted(paths, key=lambda row: (not targeted(row), row["path"]))
    excerpt_count = 0
    for row in ordered[:64]:
        path, revision = row["path"], row["sha256"]
        yield Candidate("code_reference", path, path, "public_snapshot", snapshot.snapshot_id, revision, (path,))
        if excerpt_count >= 64 or not targeted(row):
            continue
        blob = checked_path(root / digest_ref("blobs", revision))
        if blob.stat().st_size > 65_536:
            continue
        try:
            text = verified_bytes(root, "blobs", revision).decode("utf-8")
            tree = ast.parse(text)
        except WorkspaceError:
            raise
        except (UnicodeError, SyntaxError, ValueError, RecursionError):
            continue
        lines = text.splitlines(keepends=True)
        for node in tree.body:
            if excerpt_count >= 64:
                break
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                first = min([node.lineno, *(line.lineno for line in node.decorator_list)])
                last = node.end_lineno
                yield Candidate("code_excerpt", path, "".join(lines[first - 1:last]), "public_snapshot",
                                snapshot.snapshot_id, revision, (path,), span=(first, last))
                excerpt_count += 1


def collect(request: RunRequest, snapshot: SnapshotRef, *, artifact_root: Path | None,
            detection: Detection | None, diagnosis: Diagnosis | None) -> CandidatePool:
    query = request.fixed.task + "\n" + request.fixed.mandatory_text
    approved = request.fixed.approved_check_ids
    core = []
    optional = [Candidate("optional_guidance", "public-checks/registry", check_id, "approved_check",
                          snapshot.snapshot_id, "registry-v1", evidence_ids=(check_id,)) for check_id in approved
                ] if detection is None else []
    changes = set()
    if detection is not None:
        if not detection.primary or diagnosis is None:
            raise ContractError("recovery requires observed anchors and a diagnosis")
        for check in diagnosis.current_checks:
            if (check.check_id not in approved or not trusted(check) or check.snapshot_id != snapshot.snapshot_id):
                raise ContractError("unapproved recovery evidence")
            failing = [case.test_id for case in check.test_cases if case.status == "FAIL"]
            body = quote({"check": check.check_id, "status": check.status, "failed": failing})
            core.append(Candidate("failure_evidence", "public-checks/receipt", body, "approved_check",
                                  check.snapshot_id, check.check_definition_hash,
                                  evidence_ids=(check.check_result_id,)))
        if detection.primary.kind == "T2":
            for item in diagnosis.searches:
                if (item.kind != "SEARCH" or item.observation_status != "observed"
                        or item.snapshot_id != snapshot.snapshot_id):
                    raise ContractError("unverified search anchor")
                body = quote({"scope": item.payload["scope"], "query": item.payload["query"]})
                core.append(Candidate("failure_evidence", "public-ledger/search", body, "public_ledger",
                                      item.snapshot_id, item.snapshot_id, evidence_ids=(item.evidence_id,)))
        if not core:
            raise ContractError("recovery anchors do not establish public evidence")
        for check in diagnosis.comparable_attempts:
            if check.check_id not in approved or not trusted(check):
                raise ContractError("unapproved prior check")
            for case in check.test_cases:
                if case.status == "FAIL" and case.message:
                    body = quote({"check": check.check_id, "test": case.test_id, "message": case.message})
                    optional.append(Candidate("prior_attempt", "public-checks/receipt", body, "approved_check",
                                              check.snapshot_id, check.check_definition_hash,
                                              evidence_ids=(check.check_result_id,)))
        for item in diagnosis.changes:
            if item.kind != "CHANGE" or item.snapshot_id != snapshot.snapshot_id or item.observation_status != "observed":
                raise ContractError("unverified change")
            path = item.payload["path"]
            if public_path(path):
                changes.add(path)
                optional.append(Candidate("change_evidence", "public-ledger/changes", path, "public_ledger",
                                          item.snapshot_id, item.snapshot_id, (path,), (item.evidence_id,)))
    targets = set(changes)
    if artifact_root is not None:
        additions = tuple(_files(artifact_root, snapshot, query, changes))
        targets.update(item.source for item in additions if item.kind == "code_excerpt")
        optional.extend(additions)
    return CandidatePool(tuple(core), tuple(optional), tuple(sorted(targets)), diagnosis.anchors if diagnosis else ())
