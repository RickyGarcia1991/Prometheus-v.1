import copy
import hashlib
import json

import pytest

from prometheus_assistant.reviewer_check_evidence import assess, measured_checks


def data(passed=False):
    source = b"value = 1\n"
    files = {"example.py": source}
    evidence = {hashlib.sha256(source).hexdigest(): {
        "path": "example.py", "checks": [{"input": True, "expected": 1,
                                              "actual": 1 if passed else 2,
                                              "passed": passed}]}}
    return evidence, files


def report(evidence, files):
    row = measured_checks(evidence, files)[0]
    return {"findings": [{"evidence_id": row["evidence_id"], "file": row["file"], "line": 1}]}


def test_valid_failure_is_bound_and_never_accepted():
    evidence, files = data()
    result = assess(json.dumps(report(evidence, files)), evidence, files)
    assert result["status"] == "checks_failed"
    assert result["reviewer_omissions"] == []
    assert result["production_accepted"] is False
    assert result["authorized"] is False
    assert result["reviewer_report"]["findings"][0]["line_verified"] is False
    assert result["may_present_as_checks_passed"] is False


@pytest.mark.parametrize("response", ['{"findings":[]}', '{"findings":[],"findings":[]}',
                                       'not JSON', '[1]', '"text"', '{"findings":[],"prose":"fine"}',
                                       '[' * 2000, 'x' * 65537, None],
                         ids=["empty", "duplicate", "text", "array", "string", "prose", "depth", "size", "none"])
def test_empty_or_invalid_report_cannot_hide_failure(response):
    evidence, files = data()
    result = assess(response, evidence, files)
    assert result["status"] == "checks_failed"
    assert len(result["failed_checks"]) == len(result["reviewer_omissions"]) == 1
    assert not result["may_present_as_checks_passed"]


@pytest.mark.parametrize("mutation", [lambda f: f.update(line=True), lambda f: f.update(line=0),
    lambda f: f.update(line=2), lambda f: f.update(file="other.py"),
    lambda f: f.update(evidence_id="unknown"), lambda f: f.update(message="invented claim")])
def test_bad_finding_rejects_whole_report(mutation):
    evidence, files = data()
    value = report(evidence, files)
    mutation(value["findings"][0])
    result = assess(json.dumps(value), evidence, files)
    assert result["reviewer_error"] == "invalid_reviewer_report"
    assert result["reviewer_report"] is None
    assert len(result["reviewer_omissions"]) == 1


def test_duplicate_finding_rejects_prior_coverage():
    evidence, files = data()
    value = report(evidence, files)
    value["findings"] *= 2
    result = assess(json.dumps(value), evidence, files)
    assert result["reviewer_report"] is None
    assert len(result["reviewer_omissions"]) == 1


def test_passing_reference_is_not_a_defect():
    evidence, files = data(True)
    result = assess(json.dumps(report(evidence, files)), evidence, files)
    assert result["status"] == "review_invalid"
    assert not result["may_present_as_checks_passed"]
    good = assess('{"findings":[]}', evidence, files)
    assert good["may_present_as_checks_passed"]
    assert good["production_accepted"] is False


def test_missing_evidence_is_not_a_pass():
    result = assess('{"findings":[]}', {}, {})
    assert result["status"] == "evidence_missing"
    assert not result["may_present_as_checks_passed"]


@pytest.mark.parametrize("change", [lambda e, f: f.update({"example.py": b"changed"}),
    lambda e, f: f.update({"extra.py": b"x"}),
    lambda e, f: next(iter(e.values()))["checks"][0].update(passed=0),
    lambda e, f: next(iter(e.values()))["checks"][0].update(passed=True),
    lambda e, f: next(iter(e.values()))["checks"][0].update(actual=float("nan")),
    lambda e, f: next(iter(e.values()))["checks"][0].update(input="x" * 4097),
    lambda e, f: next(iter(e.values())).update(path="../example.py"),
    lambda e, f: next(iter(e.values())).update(checks=[{}] * 257)])
def test_invalid_evidence_fails_closed(change):
    evidence, files = data()
    change(evidence, files)
    with pytest.raises(ValueError):
        assess('{"findings":[]}', evidence, files)


def test_boolean_and_integer_are_distinct_and_results_detached():
    evidence, files = data()
    check = next(iter(evidence.values()))["checks"][0]
    check.update(expected=True, actual=1, input=[1])
    snapshot = copy.deepcopy(evidence)
    result = assess('{"findings":[]}', evidence, files)
    assert result["status"] == "checks_failed"
    result["measured_checks"][0]["input"].append(2)
    assert evidence == snapshot


def test_changed_measurement_changes_identity():
    evidence, files = data()
    first = report(evidence, files)
    next(iter(evidence.values()))["checks"][0]["actual"] = 3
    result = assess(json.dumps(first), evidence, files)
    assert result["reviewer_error"]
    assert len(result["reviewer_omissions"]) == 1


def test_duplicate_nested_json_key_is_rejected():
    evidence, files = data()
    response = json.dumps(report(evidence, files)).replace('"line": 1', '"line": 1, "line": 1')
    assert assess(response, evidence, files)["reviewer_error"]


def test_optional_digest_and_historical_identifier_are_compatible():
    evidence, files = data()
    digest, entry = next(iter(evidence.items()))
    canonical = json.dumps([digest, entry["path"], 0, entry["checks"][0]],
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected_id = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    entry["source_sha256"] = digest
    assert measured_checks(evidence, files)[0]["evidence_id"] == expected_id
    entry["source_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        measured_checks(evidence, files)


def test_source_size_budget():
    source = b"x" * 1_048_577
    evidence = {hashlib.sha256(source).hexdigest(): {"path": "x.py", "checks": []}}
    with pytest.raises(ValueError):
        measured_checks(evidence, {"x.py": source})


def test_cyclic_value_is_bounded():
    evidence, files = data()
    value = []
    value.append(value)
    next(iter(evidence.values()))["checks"][0]["input"] = value
    with pytest.raises(ValueError):
        measured_checks(evidence, files)


def test_valid_prefix_is_discarded_if_later_finding_invalid():
    evidence, files = data()
    value = report(evidence, files)
    value["findings"].append({"evidence_id": "unknown", "file": "example.py", "line": 1})
    result = assess(json.dumps(value), evidence, files)
    assert result["reviewer_report"] is None
    assert len(result["reviewer_omissions"]) == 1
