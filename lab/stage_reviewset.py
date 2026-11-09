"""Stage records for the eDiscovery (Premium) "upload non-Microsoft 365 data" path.

Microsoft's documented layout is one root folder with a sub-folder per
custodian, named with the custodian's SMTP address; custodians must already be
in the case. That has a consequence a records team needs to see early: there
is no catch-all on this path. Records whose archive account does not resolve to
an active custodian are held back here and listed with the reason, instead of
being silently dropped or dumped under the wrong person.

The upload itself is done with the AzCopy command that the Purview upload
wizard generates (it supplies the SAS destination); this module only prepares
the folder tree and the evidence of what was staged.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .custodians import load_map, resolve
from .manifest import load_manifest


def stage(manifest_path: Path, export_dir: Path, identity_map: Path, out_dir: Path) -> dict:
    manifest = load_manifest(manifest_path)
    table = load_map(identity_map)
    out_dir = Path(out_dir)
    root = out_dir / "upload-root"
    root.mkdir(parents=True, exist_ok=True)

    staged, held = [], []
    for entry in manifest["entries"]:
        meta = entry["archive"]
        m = resolve(meta["custodian"], table)
        src = Path(export_dir) / "items" / f"{meta['archive_id']}.eml"
        if m.status != "mapped":
            held.append({"archive_id": meta["archive_id"], "archive_account": meta["custodian"],
                         "status": m.status, "reason": m.reason, "legal_holds": meta["legal_holds"]})
            continue
        dest = root / m.upn / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        staged.append({"archive_id": meta["archive_id"], "custodian_upn": m.upn,
                       "path": str(dest.relative_to(out_dir)),
                       "raw_sha256": entry["fingerprint"]["raw_sha256"]})

    report = {
        "path": "eDiscovery Premium review-set upload (non-Microsoft 365 data)",
        "staged": staged,
        "held_back": held,
        "custodians_required_in_case": sorted({s["custodian_upn"] for s in staged}),
        "upload": "Run the AzCopy command shown by the Purview upload wizard against "
                  f"'{root}' so each custodian folder lands under its SMTP address.",
        "governance_note": "Items in a review set are case copies. Purview litigation holds and "
                           "retention labels do not govern them; retention and disposal stay with "
                           "the archive for this path.",
    }
    (out_dir / "staging-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report
