"""Command line entry point.

  python -m lab.cli demo OUT_DIR       full offline run (generate -> manifest -> stage -> land -> reconcile)
  python -m lab.cli manifest EXPORT_DIR MANIFEST.json
  python -m lab.cli stage MANIFEST.json EXPORT_DIR IDENTITY_MAP.csv OUT_DIR
  python -m lab.cli reconcile MANIFEST.json LANDED_DIR [STAGING_REPORT.json]
  python -m lab.cli plan CASE_NAME QUERY UPN [UPN ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ediscovery_plan, generate, governance, manifest, reconcile, simulate, stage_reviewset

SAMPLE_MAP = """archive_account,upn,kind
dortiz,dana.ortiz@contoso.example,active
lchen,lee.chen@contoso.example,active
sprice,sam.price@contoso.example,active
jwalsh,j.walsh@contoso.example,leaver
accounts-shared,accounts@contoso.example,shared
"""


def demo(out: Path, faults: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    export = generate.write_export(out / "export")
    man_path = manifest.write_manifest(manifest.build_manifest(export), out / "manifest.json")
    (out / "identity-map.csv").write_text(SAMPLE_MAP)
    rep = stage_reviewset.stage(man_path, export, out / "identity-map.csv", out / "staging")
    simulate.land(out / "staging" / "upload-root", out / "landed", reserialize=True, dedupe=True,
                  strip_x_headers=faults,
                  drop_attachment="HPCA-0002" if faults else None,
                  shift_date="HPCA-0006" if faults else None)
    result = reconcile.reconcile(man_path, out / "landed", out / "staging" / "staging-report.json")
    (out / "reconciliation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    md = reconcile.to_markdown(result)
    gov = governance.check(manifest.load_manifest(man_path))
    (out / "governance.json").write_text(json.dumps({"boundary": governance.BOUNDARY, "findings": gov},
                                                    indent=2, ensure_ascii=False))
    md += "\n## Retention / hold findings\n\n"
    md += "\n".join(f"- **{g['finding']}** {g['archive_id']} ({g['severity']}): {g['detail']}"
                     for g in gov if g["severity"] != "info") + "\n"
    (out / "reconciliation.md").write_text(md)
    print(f"staged {len(rep['staged'])}, held back {len(rep['held_back'])}")
    print(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo"); d.add_argument("out", type=Path)
    d.add_argument("--faults", action="store_true", help="inject defects to show detection")
    m = sub.add_parser("manifest"); m.add_argument("export", type=Path); m.add_argument("out", type=Path)
    s = sub.add_parser("stage"); s.add_argument("manifest", type=Path); s.add_argument("export", type=Path)
    s.add_argument("identity_map", type=Path); s.add_argument("out", type=Path)
    r = sub.add_parser("reconcile"); r.add_argument("manifest", type=Path); r.add_argument("landed", type=Path)
    r.add_argument("staging_report", type=Path, nargs="?")
    p = sub.add_parser("plan"); p.add_argument("case"); p.add_argument("query"); p.add_argument("upns", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        return demo(a.out, a.faults)
    if a.cmd == "manifest":
        manifest.write_manifest(manifest.build_manifest(a.export), a.out)
        return 0
    if a.cmd == "stage":
        print(json.dumps(stage_reviewset.stage(a.manifest, a.export, a.identity_map, a.out), indent=2))
        return 0
    if a.cmd == "reconcile":
        res = reconcile.reconcile(a.manifest, a.landed, a.staging_report)
        print(reconcile.to_markdown(res))
        return 0 if res["passed"] else 1
    if a.cmd == "plan":
        print(json.dumps(ediscovery_plan.plan(a.case, a.upns, a.query), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
