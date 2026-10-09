"""Canonical security findings & dedupe (re-audit 14.4, donor: Strix).

Three agents finding the same bug must produce ONE canonical finding, not three — while still
recording who reported it. And the service must never decide authorisation; that is the PTE's job.
"""
from __future__ import annotations

from security.findings import Finding, SecurityFindingService


def _sqli(target="app.example.com", title="SQL injection in login", **kw):
    return Finding(target=target, title=title,
                   finding_class=kw.pop("finding_class", "sqli"),
                   severity=kw.pop("severity", "high"),
                   cwe=kw.pop("cwe", "CWE-89"), **kw)


# --------------------------------------------------------------------- basics
def test_a_finding_is_stored_with_its_identity():
    svc = SecurityFindingService()
    out = svc.add(_sqli(), reporter="agent-a")
    assert out["duplicate"] is False
    assert out["finding"].finding_id
    assert out["finding"].fingerprint
    assert svc.get(out["finding"].finding_id) is not None


def test_invalid_severity_falls_back_to_medium():
    svc = SecurityFindingService()
    out = svc.add(_sqli(severity="catastrophic"))
    assert out["finding"].severity == "medium"


# --------------------------------------------------------------------- dedupe
def test_identical_findings_are_merged_into_one():
    svc = SecurityFindingService()
    first = svc.add(_sqli(), reporter="agent-a")
    second = svc.add(_sqli(), reporter="agent-b")
    assert second["duplicate"] is True
    assert second["merged_into"] == first["finding"].finding_id
    assert len(svc.all()) == 1, "same root cause must not produce two findings"


def test_dedupe_records_every_reporter():
    svc = SecurityFindingService()
    svc.add(_sqli(), reporter="agent-a")
    svc.add(_sqli(), reporter="agent-b")
    svc.add(_sqli(), reporter="agent-c")
    finding = svc.all()[0]
    assert finding.reported_by == ["agent-a", "agent-b", "agent-c"]
    assert finding.duplicate_count == 2


def test_different_targets_are_separate_findings():
    svc = SecurityFindingService()
    svc.add(_sqli(target="a.com"))
    svc.add(_sqli(target="b.com"))
    assert len(svc.all()) == 2


def test_different_classes_are_separate_findings():
    svc = SecurityFindingService()
    svc.add(_sqli())
    svc.add(Finding(target="app.example.com", title="XSS in profile",
                    finding_class="xss", severity="medium"))
    assert len(svc.all()) == 2


def test_dedupe_is_robust_to_wording_and_case():
    svc = SecurityFindingService()
    svc.add(_sqli(title="SQL injection in login"))
    svc.add(_sqli(title="  SQL   Injection in LOGIN!  "))
    assert len(svc.all()) == 1, "punctuation/case must not split the same finding"


def test_a_more_severe_duplicate_escalates_the_canonical_finding():
    svc = SecurityFindingService()
    first = svc.add(_sqli(severity="low"))
    svc.add(_sqli(severity="critical"))
    assert svc.get(first["finding"].finding_id).severity == "critical"


def test_a_duplicate_fills_in_missing_fields():
    svc = SecurityFindingService()
    first = svc.add(_sqli(evidence=""))
    svc.add(_sqli(evidence="HTTP 500 on ' OR 1=1--", cve="CVE-2024-1234"))
    finding = svc.get(first["finding"].finding_id)
    assert "OR 1=1" in finding.evidence
    assert finding.cve == "CVE-2024-1234"


def test_affected_artifacts_are_unioned():
    svc = SecurityFindingService()
    first = svc.add(_sqli(affected_artifacts=["login.py"]))
    svc.add(_sqli(affected_artifacts=["login.py", "db.py"]))
    finding = svc.get(first["finding"].finding_id)
    assert sorted(finding.affected_artifacts) == ["db.py", "login.py"]


# -------------------------------------------------------------------- queries
def test_summary_counts_and_orders_by_severity():
    svc = SecurityFindingService()
    svc.add(_sqli(severity="low"))
    svc.add(_sqli(title="XSS here", finding_class="xss", severity="critical"))
    summary = svc.summary()
    assert summary["total"] == 2
    assert summary["by_severity"]["critical"] == 1
    assert svc.all()[0].severity == "critical", "most severe first"


def test_unresolved_excludes_closed_states():
    svc = SecurityFindingService()
    out = svc.add(_sqli())
    svc.update_status(out["finding"].finding_id, "resolved")
    svc.add(_sqli(title="XSS here", finding_class="xss"))
    assert len(svc.unresolved()) == 1


def test_by_target_filters():
    svc = SecurityFindingService()
    svc.add(_sqli(target="a.com"))
    svc.add(_sqli(target="b.com"))
    assert len(svc.by_target("a.com")) == 1


# ------------------------------------------------------------------ lifecycle
def test_retest_records_evidence_and_closes_when_passed():
    svc = SecurityFindingService()
    out = svc.add(_sqli())
    finding = svc.retest(out["finding"].finding_id, passed=True,
                         evidence="payload now rejected, 403")
    assert finding.status == "resolved"
    assert "403" in finding.retest_evidence


def test_a_failed_retest_reopens_the_finding():
    svc = SecurityFindingService()
    out = svc.add(_sqli())
    svc.retest(out["finding"].finding_id, passed=True, evidence="ok")
    finding = svc.retest(out["finding"].finding_id, passed=False,
                         evidence="still exploitable")
    assert finding.status == "retest-pending"
    assert "still exploitable" in finding.retest_evidence


def test_updating_an_unknown_finding_returns_none():
    assert SecurityFindingService().update_status("nope", "resolved") is None


# --------------------------------------------------------------------- SARIF
def test_sarif_export_has_valid_structure():
    svc = SecurityFindingService()
    svc.add(_sqli(severity="critical", affected_artifacts=["login.py"]))
    sarif = svc.to_sarif()
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].endswith("sarif-2.1.0.json")
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "GENIE"
    assert len(run["results"]) == 1
    result = run["results"][0]
    assert result["ruleId"] == "sqli"
    assert result["level"] == "error", "critical must map to error"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "login.py"


def test_sarif_maps_severities_to_levels():
    svc = SecurityFindingService()
    svc.add(_sqli(title="SQLi low", severity="low"))
    svc.add(_sqli(title="SQLi high", severity="high"))
    levels = {r["properties"]["severity"]: r["level"]
              for r in svc.to_sarif()["runs"][0]["results"]}
    assert levels["low"] == "note"
    assert levels["high"] == "error"


def test_sarif_carries_cwe_cve_and_cvss():
    svc = SecurityFindingService()
    svc.add(_sqli(cwe="CWE-89", cve="CVE-2024-1", cvss=9.8))
    props = svc.to_sarif()["runs"][0]["results"][0]["properties"]
    assert props["cwe"] == "CWE-89"
    assert props["cve"] == "CVE-2024-1"
    assert props["cvss"] == 9.8


def test_sarif_groups_findings_of_the_same_class_under_one_rule():
    """Same root-cause class = one SARIF rule with multiple results (correct semantics)."""
    svc = SecurityFindingService()
    svc.add(_sqli(title="SQLi in login"))
    svc.add(_sqli(title="SQLi in search"))
    sarif = svc.to_sarif()
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    results = sarif["runs"][0]["results"]
    assert len(rules) == 1, "one rule per finding class"
    assert rules[0]["id"] == "sqli"
    assert len(results) == 2, "but one result per actual finding"
    assert all(r["ruleId"] == "sqli" for r in results)


def test_sarif_of_an_empty_service_is_still_valid():
    sarif = SecurityFindingService().to_sarif()
    assert sarif["runs"][0]["results"] == []
