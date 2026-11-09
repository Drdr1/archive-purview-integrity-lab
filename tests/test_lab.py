import json
from datetime import date
from pathlib import Path

import pytest

from lab import cli, ediscovery_plan, generate, governance, manifest, reconcile, simulate, stage_reviewset
from lab.fingerprint import fingerprint


@pytest.fixture()
def pipeline(tmp_path: Path):
    export = generate.write_export(tmp_path / "export")
    man = manifest.write_manifest(manifest.build_manifest(export), tmp_path / "manifest.json")
    idmap = tmp_path / "map.csv"
    idmap.write_text(cli.SAMPLE_MAP)
    rep = stage_reviewset.stage(man, export, idmap, tmp_path / "staging")
    return tmp_path, export, man, rep


def run_land(tmp_path, man, **kw):
    landed = tmp_path / "landed"
    simulate.land(tmp_path / "staging" / "upload-root", landed, **kw)
    return reconcile.reconcile(man, landed, tmp_path / "staging" / "staging-report.json")


def by_id(report):
    return {r["archive_id"]: r for r in report["records"]}


def test_generation_is_deterministic():
    a = [raw for _, raw in generate.build_records()]
    b = [raw for _, raw in generate.build_records()]
    assert a == b


def test_reserialization_changes_bytes_but_not_content(pipeline):
    tmp, _, man, _ = pipeline
    rep = run_land(tmp, man, reserialize=True)
    assert rep["passed"]
    assert "EXACT" not in rep["summary"]
    assert rep["summary"]["CONTENT_PRESERVED"] == 9


def test_untouched_landing_is_exact(pipeline):
    tmp, _, man, _ = pipeline
    rep = run_land(tmp, man, reserialize=False)
    assert rep["summary"]["EXACT"] == 9


def test_unmapped_custodians_are_held_back_with_reason(pipeline):
    tmp, _, man, rep = pipeline
    held = {h["archive_id"]: h["status"] for h in rep["held_back"]}
    assert held == {"HPCA-0007": "leaver", "HPCA-0008": "shared"}
    r = by_id(run_land(tmp, man))
    assert r["HPCA-0007"]["verdict"] == "NOT_SENT" and "inactive mailbox" in r["HPCA-0007"]["reason"]


def test_staging_uses_custodian_smtp_folders(pipeline):
    tmp, _, _, rep = pipeline
    folders = {p.name for p in (tmp / "staging" / "upload-root").iterdir()}
    assert folders == set(rep["custodians_required_in_case"])
    assert all("@" in f for f in folders)


def test_dropped_attachment_is_detected(pipeline):
    tmp, _, man, _ = pipeline
    rep = run_land(tmp, man, drop_attachment="HPCA-0002")
    r = by_id(rep)["HPCA-0002"]
    assert not rep["passed"]
    assert r["verdict"] == "DEGRADED"
    assert [d["field"] for d in r["differences"]] == ["attachment.missing"]


def test_shifted_date_is_detected(pipeline):
    tmp, _, man, _ = pipeline
    r = by_id(run_land(tmp, man, shift_date="HPCA-0006"))["HPCA-0006"]
    assert r["verdict"] == "DEGRADED"
    assert r["differences"][0]["field"] == "header.date_utc"


def test_dedupe_loses_custodian_attribution_not_content(pipeline):
    tmp, _, man, _ = pipeline
    rep = run_land(tmp, man, dedupe=True)
    r = by_id(rep)
    assert r["HPCA-0009"]["verdict"] == "CONTENT_PRESERVED"
    assert r["HPCA-0010"]["verdict"] == "DEDUPED"
    assert rep["passed"] and "DEDUPED" in rep["warnings"]


def test_matching_falls_back_to_message_id_when_provenance_header_is_stripped(pipeline):
    tmp, _, man, _ = pipeline
    rep = run_land(tmp, man, strip_x_headers=True)
    assert rep["passed"]
    assert all(r.get("provenance_header_survived") is False
               for r in rep["records"] if r["verdict"] == "CONTENT_PRESERVED")


def test_unexpected_items_fail_the_run(pipeline):
    tmp, export, man, _ = pipeline
    landed = tmp / "landed"
    simulate.land(tmp / "staging" / "upload-root", landed)
    stranger = generate.build_records(seed=99)[0][1].replace(b"hpca-0001", b"stranger")
    stranger = stranger.replace(b"X-HPCA-Archive-ID: HPCA-0001", b"X-HPCA-Archive-ID: OTHER-1")
    (landed / "stranger.eml").write_bytes(stranger)
    rep = reconcile.reconcile(man, landed, tmp / "staging" / "staging-report.json")
    assert rep["summary"].get("UNEXPECTED") == 1 and not rep["passed"]


def test_nested_and_non_ascii_attachments_are_fingerprinted():
    recs = {r.archive_id: raw for r, raw in generate.build_records()}
    paths2 = [a["path"] for a in fingerprint(recs["HPCA-0002"])["attachments"]]
    assert "Übersicht_Q1.xlsx" in paths2 and "ملخص_الربع_الأول.pdf" in paths2
    atts3 = fingerprint(recs["HPCA-0003"])["attachments"]
    assert len(atts3) == 2
    assert atts3[1]["path"].endswith("/invoice-4471.pdf")


def test_manifest_tampering_is_rejected(pipeline):
    _, _, man, _ = pipeline
    data = json.loads(man.read_text())
    data["entries"][-1]["archive"]["legal_holds"] = []  # quietly release a hold
    man.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        manifest.load_manifest(man)


def test_expired_retention_under_hold_is_critical(pipeline):
    _, _, man, _ = pipeline
    f = governance.check(manifest.load_manifest(man), today=date(2026, 11, 9))
    crit = [x for x in f if x["severity"] == "critical"]
    assert [x["archive_id"] for x in crit] == ["HPCA-0011"]


def test_ediscovery_plan_order_and_id_substitution():
    steps = ediscovery_plan.plan("PoC", ["a@x.example", "b@x.example"], 'subject:"contract"')
    assert [s["id"] for s in steps] == ["case", "custodian0", "custodian1", "hold", "search",
                                        "reviewset", "add", "export"]
    calls = []

    def fake(method, url, body):
        calls.append((method, url, body))
        return {"id": f"id{len(calls)}"}

    ids = ediscovery_plan.run(steps, fake)
    assert ids["case"] == "id1"
    add_url, add_body = calls[6][1], calls[6][2]
    assert add_url.endswith("/id1/reviewSets/id6/addToReviewSet")
    assert add_body["search"]["id"] == "id5"
    assert all("{" not in c[1] for c in calls)


def test_cli_demo_with_faults_fails_reconciliation(tmp_path):
    cli.demo(tmp_path / "out", faults=True)
    rep = json.loads((tmp_path / "out" / "reconciliation.json").read_text())
    assert not rep["passed"]
    assert (tmp_path / "out" / "governance.json").exists()
