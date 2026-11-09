"""Retention / hold boundary checks across the archive and Purview.

Two systems can each think they own disposal. These checks run on the source
manifest and say, per record and per integration path, which system must
enforce what - and flag the records where getting it wrong is irreversible.
"""

from __future__ import annotations

from datetime import date

# Which system governs each control, per integration path. "validate" means
# the behaviour must be proven in the target tenant before anyone relies on it.
BOUNDARY = {
    "reviewset_upload": {
        "where_records_live": "case copy in an eDiscovery Premium review set; original stays in HPCA",
        "litigation_hold": "HPCA (Purview holds cover Microsoft 365 locations, not uploaded review-set data)",
        "retention_and_disposal": "HPCA",
        "records_management_labels": "not applicable to review-set copies",
        "search_review_export": "Purview",
    },
    "mailbox_import": {
        "where_records_live": "custodian mailboxes (or a catch-all mailbox for unmapped items)",
        "litigation_hold": "Purview (and HPCA, until the archive copy is retired)",
        "retention_and_disposal": "Purview retention policies/labels - must be reconciled with HPCA classes",
        "records_management_labels": "Purview",
        "search_review_export": "Purview",
    },
    "graph_connector": {
        "where_records_live": "HPCA; Purview indexes connector content",
        "litigation_hold": "validate",
        "retention_and_disposal": "HPCA (validate whether any Purview retention applies)",
        "records_management_labels": "validate",
        "search_review_export": "Purview eDiscovery Premium, as a non-Microsoft 365 location (validate fidelity)",
    },
}


def check(manifest: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    findings = []
    for e in manifest["entries"]:
        a = e["archive"]
        expired = date.fromisoformat(a["retention_expires"]) < today
        if expired and a["legal_holds"]:
            findings.append({
                "archive_id": a["archive_id"], "severity": "critical",
                "finding": "HOLD_OVERRIDES_RETENTION",
                "detail": f"retention class {a['retention_class']} expired {a['retention_expires']} "
                          f"but holds {', '.join(a['legal_holds'])} are active: neither HPCA nor "
                          "Purview may dispose of any copy",
            })
        elif expired:
            findings.append({
                "archive_id": a["archive_id"], "severity": "high", "finding": "RETENTION_EXPIRED",
                "detail": "eligible for disposal in HPCA: confirm it should not be moved at all, "
                          "or that the destination will not extend its life unintentionally",
            })
        if a["legal_holds"]:
            findings.append({
                "archive_id": a["archive_id"], "severity": "info", "finding": "HOLD_MUST_FOLLOW",
                "detail": "on reviewset_upload the hold stays enforced in HPCA; on mailbox_import "
                          "the matching Purview hold must exist before HPCA releases its own",
            })
    return findings
