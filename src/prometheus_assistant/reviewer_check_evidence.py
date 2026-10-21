"""Pure, bounded reviewer/evidence validation; not a trust or acceptance boundary.

Callers must obtain measured data from a trusted producer. Hashes bind bytes,
not the truth of a supplied measurement. No model, approval or runtime wiring is
performed here. Locations are range checked, not proven to cause the failure.
"""
import hashlib
import json
import math
import re

MAX_FILES = 32
MAX_CHECKS = 256
MAX_SOURCE_BYTES = 1_048_576
MAX_REPORT_BYTES = 65_536
MAX_VALUE_NODES = 8192


def _json_value(value, budget, depth=0):
    budget[0] -= 1
    if budget[0] < 0 or depth > 12:
        raise ValueError("Evidence value exceeds bounds")
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int and value.bit_length() <= 256:
        return
    if kind is float and math.isfinite(value):
        return
    if kind is str and len(value) <= 4096:
        value.encode("utf-8")
        return
    if kind is list and len(value) <= 256:
        for item in value:
            _json_value(item, budget, depth + 1)
        return
    if kind is dict and len(value) <= 128:
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise ValueError("Invalid evidence object key")
            key.encode("utf-8")
            _json_value(item, budget, depth + 1)
        return
    raise ValueError("Invalid or oversized JSON evidence value")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def measured_checks(evidence, files):
    """Validate digest-keyed evidence and return detached, labeled check rows.

    Each entry is {path, checks}; each check is {input, expected, actual, passed}.
    Paths are relative POSIX identifiers, never read from disk. Files are bytes.
    Equality uses canonical JSON, intentionally distinguishing bools and numbers.
    """
    if type(evidence) is not dict or type(files) is not dict:
        raise ValueError("Evidence and files must be dictionaries")
    if len(evidence) > MAX_FILES or len(files) != len(evidence):
        raise ValueError("Invalid file count")
    rows, paths, total = [], set(), 0
    budget = [MAX_VALUE_NODES]
    for digest, entry in evidence.items():
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid source digest")
        if type(entry) is not dict or set(entry) not in ({"path", "checks"}, {"path", "checks", "source_sha256"}):
            raise ValueError("Invalid evidence entry")
        if "source_sha256" in entry and entry["source_sha256"] != digest:
            raise ValueError("Conflicting source digest")
        path = entry["path"]
        if (type(path) is not str or not 1 <= len(path) <= 512
                or any(c in path for c in "\\:\x00")
                or any(ord(c) < 32 for c in path)
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or path in paths):
            raise ValueError("Invalid or duplicate source path")
        path.encode("utf-8")
        paths.add(path)
        source = files.get(path)
        if type(source) is not bytes:
            raise ValueError("Source must be bytes")
        total += len(source)
        if total > MAX_SOURCE_BYTES:
            raise ValueError("Sources exceed byte limit")
        if hashlib.sha256(source).hexdigest() != digest:
            raise ValueError("Evidence source binding mismatch")
        line_count = len(source.decode("utf-8").splitlines())
        checks = entry["checks"]
        if type(checks) is not list or len(checks) + len(rows) > MAX_CHECKS:
            raise ValueError("Invalid check count")
        for index, check in enumerate(checks):
            if type(check) is not dict or set(check) != {"input", "expected", "actual", "passed"}:
                raise ValueError("Invalid measured check fields")
            _json_value(check, budget)
            if type(check["passed"]) is not bool or check["passed"] != (
                    _canonical(check["expected"]) == _canonical(check["actual"])):
                raise ValueError("Inconsistent measured check")
            eid = hashlib.sha256(_canonical([digest, path, index, check]).encode("utf-8")).hexdigest()[:16]
            if any(row["evidence_id"] == eid for row in rows):
                raise ValueError("Evidence identifier collision")
            rows.append(dict(json.loads(_canonical(check)), evidence_id=eid,
                             file=path, source_sha256=digest, line_count=line_count,
                             origin="caller_supplied_measured_check"))
    return rows


def assess(response, evidence, files):
    """Keep measured failures visible even when reviewer output is unusable.

    Invalid evidence raises ValueError; an invalid report returns a fixed error
    without echoing arbitrary reviewer prose. Nothing here grants acceptance.
    """
    checks = measured_checks(evidence, files)
    by_id = {row["evidence_id"]: row for row in checks}
    findings, covered, error = [], set(), None
    try:
        if type(response) is not str or len(response) > MAX_REPORT_BYTES or len(response.encode("utf-8")) > MAX_REPORT_BYTES:
            raise ValueError("Report exceeds bounds")
        report = json.loads(response, object_pairs_hook=_pairs)
        if type(report) is not dict or set(report) != {"findings"} or type(report["findings"]) is not list or len(report["findings"]) > 32:
            raise ValueError("Invalid report schema")
        for finding in report["findings"]:
            if type(finding) is not dict or set(finding) != {"evidence_id", "file", "line"}:
                raise ValueError("Unsupported finding fields")
            eid, path, line = finding["evidence_id"], finding["file"], finding["line"]
            if type(eid) is not str or eid not in by_id or eid in covered:
                raise ValueError("Invalid evidence reference")
            row = by_id[eid]
            if type(path) is not str or path != row["file"] or type(line) is not int or not 1 <= line <= row["line_count"] or row["passed"]:
                raise ValueError("Invalid finding location or passing reference")
            covered.add(eid)
            findings.append({"evidence_id": eid, "file": path, "line": line,
                             "line_origin": "reviewer_supplied", "line_verified": False})
    except (ValueError, TypeError, RecursionError, UnicodeError):
        error, findings, covered = "invalid_reviewer_report", [], set()
    failures = [row for row in checks if not row["passed"]]
    return dict(status="checks_failed" if failures else "review_invalid" if error else "checks_passed" if checks else "evidence_missing",
                measured_checks=checks, failed_checks=failures,
                reviewer_report=None if error else {"findings": findings},
                reviewer_error=error,
                reviewer_omissions=[row["evidence_id"] for row in failures if row["evidence_id"] not in covered],
                may_present_as_checks_passed=bool(checks) and not failures and error is None,
                authorized=False, production_accepted=False)
