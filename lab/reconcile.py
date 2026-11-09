"""Reconcile what landed against the source manifest.

Verdict per record:
  EXACT              byte-identical to the source
  CONTENT_PRESERVED  bytes differ (re-serialized) but canonical headers, body
                     text and every attachment's decoded content match
  DEDUPED            not landed as its own item, but an identical message
                     landed for another custodian: content is there, the
                     custodian attribution for this copy is not
  DEGRADED           landed, but a header field, the body or an attachment
                     differs - listed field by field
  MISSING            nothing matching landed
  NOT_SENT           held back at staging (unmapped / leaver / shared), with reason
And per landed item that matches nothing: UNEXPECTED.

Matching uses the X-HPCA-Archive-ID provenance header when it survived,
otherwise falls back to Message-ID.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .fingerprint import fingerprint
from .manifest import load_manifest

BAD = {"DEGRADED", "MISSING", "UNEXPECTED"}
WARN = {"DEDUPED", "NOT_SENT"}


def _att_map(fp: dict) -> dict[str, str]:
    return {a["path"]: a["sha256"] for a in fp["attachments"]}


def compare(src: dict, dst: dict) -> dict:
    if src["raw_sha256"] == dst["raw_sha256"]:
        return {"verdict": "EXACT", "differences": []}
    diffs = []
    for k, v in src["headers"].items():
        if dst["headers"].get(k) != v:
            diffs.append({"field": f"header.{k}", "source": v, "landed": dst["headers"].get(k)})
    if src["body_sha256"] != dst["body_sha256"]:
        diffs.append({"field": "body_text", "source": src["body_sha256"], "landed": dst["body_sha256"]})
    sa, da = _att_map(src), _att_map(dst)
    for path, h in sa.items():
        if path not in da:
            diffs.append({"field": "attachment.missing", "source": path, "landed": None})
        elif da[path] != h:
            diffs.append({"field": "attachment.changed", "source": path, "landed": da[path]})
    for path in da.keys() - sa.keys():
        diffs.append({"field": "attachment.added", "source": None, "landed": path})
    return {"verdict": "DEGRADED" if diffs else "CONTENT_PRESERVED", "differences": diffs}


def reconcile(manifest_path: Path, landed_dir: Path, staging_report: Path | None = None) -> dict:
    manifest = load_manifest(manifest_path)
    entries = {e["archive"]["archive_id"]: e for e in manifest["entries"]}

    not_sent = {}
    if staging_report:
        rep = json.loads(Path(staging_report).read_text())
        not_sent = {h["archive_id"]: h for h in rep["held_back"]}
    expected = {aid: e for aid, e in entries.items() if aid not in not_sent}

    landed = [(p, fingerprint(p.read_bytes())) for p in sorted(Path(landed_dir).rglob("*.eml"))]
    matched: dict[str, tuple[Path, dict]] = {}
    leftovers = []
    for path, fp in landed:
        aid = fp["provenance_headers"].get("X-HPCA-Archive-ID")
        if aid in expected and aid not in matched:
            matched[aid] = (path, fp)
        else:
            leftovers.append((path, fp))

    unexpected = []
    for path, fp in leftovers:
        mid = fp["headers"]["message_id"]
        candidates = [aid for aid, e in expected.items()
                      if aid not in matched and e["fingerprint"]["headers"]["message_id"] == mid]
        if candidates:
            matched[candidates[0]] = (path, fp)
        else:
            unexpected.append({"landed_file": path.name, "message_id": mid})

    results = []
    for aid, e in sorted(entries.items()):
        meta, src = e["archive"], e["fingerprint"]
        row = {"archive_id": aid, "custodian": meta["custodian"], "legal_holds": meta["legal_holds"]}
        if aid in not_sent:
            row.update(verdict="NOT_SENT", differences=[], reason=not_sent[aid]["reason"])
        elif aid in matched:
            path, dst = matched[aid]
            row.update(compare(src, dst))
            row["landed_file"] = path.name
            row["provenance_header_survived"] = "X-HPCA-Archive-ID" in dst["provenance_headers"]
        else:
            mid = src["headers"]["message_id"]
            twin = next((o for o, e2 in entries.items() if o != aid and o in matched
                         and e2["fingerprint"]["headers"]["message_id"] == mid), None)
            if twin:
                row.update(verdict="DEDUPED", differences=[],
                           reason=f"identical message landed as {twin}; custodian attribution for "
                                  f"'{meta['custodian']}' is lost")
            else:
                row.update(verdict="MISSING", differences=[])
        results.append(row)

    counts = Counter(r["verdict"] for r in results)
    if unexpected:
        counts["UNEXPECTED"] = len(unexpected)
    return {
        "manifest_entries_sha256": manifest["entries_sha256"],
        "landed_dir": str(landed_dir),
        "summary": dict(sorted(counts.items())),
        "passed": not (set(counts) & BAD),
        "warnings": sorted(set(counts) & WARN),
        "records": results,
        "unexpected": unexpected,
    }


def to_markdown(report: dict) -> str:
    lines = ["# Reconciliation report", "",
             f"Manifest entries SHA-256: `{report['manifest_entries_sha256']}`", "",
             "| Verdict | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in report["summary"].items()]
    result = "PASS" if report["passed"] else "FAIL"
    if report["passed"] and report.get("warnings"):
        result += " with warnings (" + ", ".join(report["warnings"]) + " need a records decision)"
    lines += ["", f"**Result: {result}**", "",
              "| Archive ID | Custodian | Holds | Verdict | Detail |", "|---|---|---|---|---|"]
    for r in report["records"]:
        detail = r.get("reason") or "; ".join(
            f"{d['field']}: {d['source']} -> {d['landed']}" for d in r["differences"]) or ""
        if r.get("provenance_header_survived") is False:
            detail = (detail + "; " if detail else "") + "provenance header lost"
        lines.append(f"| {r['archive_id']} | {r['custodian']} | {', '.join(r['legal_holds']) or '-'} "
                     f"| {r['verdict']} | {detail} |")
    if report["unexpected"]:
        lines += ["", "## Unexpected landed items", ""]
        lines += [f"- {u['landed_file']} ({u['message_id']})" for u in report["unexpected"]]
    return "\n".join(lines) + "\n"
