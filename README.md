# archive-purview-integrity-lab

Evidence-integrity tooling for moving records from a third-party email archive into
Microsoft Purview eDiscovery, and for proving afterwards that nothing was lost,
altered, or attributed to the wrong custodian.

> **Status:** the offline pipeline (generate → manifest → stage → land → reconcile →
> retention/hold checks) is complete and covered by tests in CI. The landing step is a
> **simulation**, not Purview. The Graph eDiscovery call plan follows the published v1.0
> reference but has **not yet been run against a live tenant**. Every record is
> synthetic.

## The problem

When archived mail moves into Purview, three questions decide whether the result can
be defended:

1. **Did the content survive?** Pipelines re-serialize messages, so a byte hash of
   the original almost never matches what lands. A naive SHA-256 check then fails on
   every item, or someone turns it off. You need fingerprints that survive
   re-serialization but still catch real defects.
2. **Is every record attributed to the right custodian?** Leavers, shared mailboxes
   and messages archived for several people break naive custodian mapping. Dedupe
   during review or export can silently drop the second custodian's copy.
3. **Which system enforces holds and retention?** Depending on the integration path,
   the archive, Purview, or both must enforce them. A record whose retention has
   expired but is under legal hold must not be disposed by *either* system.

## What it does

| Step | Module | Output |
|---|---|---|
| Synthetic archive export: native `.eml` plus a metadata sidecar (custodian, retention class, holds) | `lab/generate.py` | `export/` |
| Source manifest with a three-level fingerprint per record; the manifest is hashed itself | `lab/manifest.py`, `lab/fingerprint.py` | `manifest.json` |
| Explicit custodian mapping: mapped / leaver / shared / unmapped, each with a reason | `lab/custodians.py` | |
| Staging for eDiscovery Premium's non-Microsoft 365 upload (one folder per custodian SMTP address) | `lab/stage_reviewset.py` | `staging/`, `staging-report.json` |
| Simulated landing with optional injected faults | `lab/simulate.py` | `landed/` |
| Reconciliation: each record gets a verdict, with differences listed field by field | `lab/reconcile.py` | `reconciliation.md/.json` |
| Retention/hold boundary checks per integration path | `lab/governance.py` | `governance.json` |
| Graph eDiscovery call plan: case → custodians → hold → search → review set → export | `lab/ediscovery_plan.py` | |

### Fingerprint levels

- **raw**: SHA-256 of the exact bytes. Proves nothing changed at all.
- **content**: SHA-256 of each attachment's *decoded* bytes (recursing into forwarded
  `message/rfc822` parts), of the normalized body text, and of a canonical header set
  (Message-ID, UTC date, sorted addresses, subject). This is what you reconcile on.
- **provenance**: whether the archive's `X-HPCA-*` headers survived. That tells you
  whether archive metadata can travel inside the item or has to go in a separate
  manifest or load file.

### Verdicts

`EXACT` · `CONTENT_PRESERVED` · `DEDUPED` (content present under another custodian;
this custodian's attribution is lost) · `DEGRADED` (field-level diff) · `MISSING` ·
`NOT_SENT` (held back at staging, with reason) · `UNEXPECTED` (landed but not in the
manifest). Any `DEGRADED`, `MISSING` or `UNEXPECTED` fails the run.

## Run it

```bash
python -m pip install pytest
python -m pytest -q                              # 15 tests
python -m lab.cli demo out/                      # clean run
python -m lab.cli demo out-faults/ --faults      # injected defects
python -m lab.cli plan "HPCA-PoC" 'subject:"contract"' dana.ortiz@contoso.example
```

No third-party dependencies: standard library only (Python 3.11+).

### Clean run (re-serialized and deduped on landing)

```
| Verdict           | Count |
| CONTENT_PRESERVED | 8 |
| DEDUPED           | 1 |
| NOT_SENT          | 2 |
Result: PASS with warnings (DEDUPED, NOT_SENT need a records decision)

HPCA-0007 jwalsh          NOT_SENT  account no longer active: decide inactive mailbox vs alternate custodian
HPCA-0008 accounts-shared NOT_SENT  shared mailbox: assign a named owner or treat as non-custodial source
HPCA-0010 sprice          DEDUPED   identical message landed as HPCA-0009; custodian attribution for 'sprice' is lost

HOLD_OVERRIDES_RETENTION HPCA-0011 (critical): retention class GENERAL-3Y expired 2022-05-02
but holds LH-2025-014 are active: neither HPCA nor Purview may dispose of any copy
```

### With injected faults

```
HPCA-0002 DEGRADED  attachment.missing: Übersicht_Q1.xlsx -> None; provenance header lost
HPCA-0006 DEGRADED  header.date_utc: 2024-03-08T09:15:00+00:00 -> 2024-03-08T10:15:00+00:00
Result: FAIL
```

## Integration paths and who enforces what

| | Review-set upload | Mailbox import | Graph connector |
|---|---|---|---|
| Where records live | Case copy in an eDiscovery Premium review set; original stays in the archive | Custodian mailboxes, plus a catch-all mailbox for unmapped items | Archive; Purview indexes connector content |
| Litigation hold | Archive | Purview (and the archive, until its copy is retired) | To validate |
| Retention and disposal | Archive | Purview, reconciled with archive classes | Archive (validate whether Purview applies) |
| Records Management labels | Not applicable | Purview | To validate |
| Search / review / export | Purview | Purview | Purview, as a non-Microsoft 365 location (validate fidelity) |
| Unmapped custodians | **No catch-all**: held back until resolved | Catch-all mailbox | Depends on connector ACLs |

Notes:

- Review-set upload requires the custodians to be in the case before upload, and an
  eDiscovery Premium licence for those custodians.
- Creating messages through the Graph mail API produces drafts. That isn't a
  defensible way to bulk-import archived mail. Microsoft's documented route for the
  mailbox path is a partner connector with a provisioned service principal (or PST
  import), and the lab treats it that way.
- Cells marked *to validate* are exactly that. They're the questions for the first
  week of a real engagement, not claims.

## What a real engagement adds

See [`docs/first-five-days.md`](docs/first-five-days.md): replace the generator with
the archive's real export API, replace the simulator with the actual Purview
upload/collection and export, and reconcile the export (and Purview's own upload
Items report) against the source manifest.

## Licence

MIT
