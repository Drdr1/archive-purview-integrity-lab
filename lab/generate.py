"""Generate a synthetic, deterministic archive export.

The export imitates what an enterprise email archive typically hands over:
one native .eml per record plus a JSON sidecar with archive-side metadata
(custodian account, retention class, legal holds). Every record is fake.

The record set deliberately includes the cases that break naive pipelines:
non-ASCII attachment names, nested forwarded messages, inline images,
calendar invites, a large attachment, a leaver, a shared mailbox, a message
archived under two custodians, and a record whose retention has expired
while it is still under legal hold.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path

DOMAIN = "contoso-archive.example"


@dataclass
class Record:
    archive_id: str
    custodian: str  # archive-side account, not an Entra UPN
    folder: str
    retention_class: str
    retention_expires: str
    legal_holds: list[str] = field(default_factory=list)
    note: str = ""


def _blob(rng: random.Random, size: int, header: bytes = b"") -> bytes:
    return header + bytes(rng.getrandbits(8) for _ in range(max(0, size - len(header))))


def _base(rng: random.Random, sender: str, to: list[str], subject: str, when: datetime,
          archive_id: str, cc: list[str] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Message-ID"] = f"<{archive_id.lower()}.{when:%Y%m%d%H%M}@{DOMAIN}>"
    msg["Date"] = format_datetime(when)
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    # Archive-side provenance header. The reconciler reports whether it survives.
    msg["X-HPCA-Archive-ID"] = archive_id
    return msg


def _freeze(msg: EmailMessage, tag: str) -> bytes:
    """Fix MIME boundaries so the same seed always yields the same bytes."""
    for i, part in enumerate(msg.walk()):
        if part.is_multipart():
            part.set_boundary(f"=_hpca_{tag.lower()}_{i}")
    return bytes(msg)


def build_records(seed: int = 7) -> list[tuple[Record, bytes]]:
    rng = random.Random(seed)
    t0 = datetime(2024, 3, 4, 9, 15, tzinfo=timezone.utc)
    out: list[tuple[Record, bytes]] = []

    def add(rec: Record, msg: EmailMessage) -> None:
        out.append((rec, _freeze(msg, rec.archive_id)))

    # R001 plain email with a PDF attachment
    m = _base(rng, f"dana.ortiz@{DOMAIN}", [f"lee.chen@{DOMAIN}"], "Q1 supplier contract - signed copy",
              t0, "HPCA-0001")
    m.set_content("Signed copy attached. Please file under supplier agreements.\n")
    m.add_attachment(_blob(rng, 48_000, b"%PDF-1.7\n"), maintype="application", subtype="pdf",
                     filename="supplier-contract-signed.pdf")
    add(Record("HPCA-0001", "dortiz", "Inbox", "CONTRACTS-7Y", "2031-03-04"), m)

    # R002 non-ASCII attachment names (German + Arabic), RFC 2231 encoded
    m = _base(rng, f"lee.chen@{DOMAIN}", [f"dana.ortiz@{DOMAIN}"], "Übersicht / ملخص الربع الأول",
              t0 + timedelta(hours=3), "HPCA-0002")
    m.set_content("Bilingual summary attached.\n")
    m.add_attachment(_blob(rng, 9_000, b"PK\x03\x04"), maintype="application",
                     subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     filename="Übersicht_Q1.xlsx")
    m.add_attachment(_blob(rng, 7_000, b"%PDF-1.7\n"), maintype="application", subtype="pdf",
                     filename="ملخص_الربع_الأول.pdf")
    add(Record("HPCA-0002", "lchen", "Sent Items", "FINANCE-10Y", "2034-03-04"), m)

    # R003 forwarded message carried as message/rfc822 with its own attachment
    inner = _base(rng, f"vendor@supplier.example", [f"dana.ortiz@{DOMAIN}"], "Invoice 4471",
                  t0 - timedelta(days=2), "HPCA-0003-INNER")
    del inner["X-HPCA-Archive-ID"]
    inner.set_content("Invoice attached.\n")
    inner.add_attachment(_blob(rng, 15_000, b"%PDF-1.4\n"), maintype="application", subtype="pdf",
                         filename="invoice-4471.pdf")
    m = _base(rng, f"dana.ortiz@{DOMAIN}", [f"ap@{DOMAIN}"], "FW: Invoice 4471",
              t0 + timedelta(days=1), "HPCA-0003")
    m.set_content("Forwarding for payment approval.\n")
    m.add_attachment(inner)
    add(Record("HPCA-0003", "dortiz", "Sent Items", "FINANCE-10Y", "2034-03-05"), m)

    # R004 HTML body with an inline (cid) image
    m = _base(rng, f"marketing@{DOMAIN}", [f"lee.chen@{DOMAIN}"], "Brand refresh preview",
              t0 + timedelta(days=2), "HPCA-0004")
    m.set_content("Plain-text fallback.\n")
    cid = f"<preview-0004@{DOMAIN}>"
    m.add_alternative(f'<p>Preview:</p><img src="cid:{cid[1:-1]}">', subtype="html")
    m.get_payload()[1].add_related(_blob(rng, 6_000, b"\x89PNG\r\n\x1a\n"), maintype="image",
                                   subtype="png", cid=cid, filename="preview.png")
    add(Record("HPCA-0004", "lchen", "Inbox", "GENERAL-3Y", "2027-03-06"), m)

    # R005 calendar invite
    m = _base(rng, f"dana.ortiz@{DOMAIN}", [f"lee.chen@{DOMAIN}", f"sam.price@{DOMAIN}"],
              "Contract review meeting", t0 + timedelta(days=3), "HPCA-0005")
    m.set_content("Invite attached.\n")
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:hpca-0005@" + DOMAIN +
           "\r\nDTSTART:20240310T100000Z\r\nSUMMARY:Contract review\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    m.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invite.ics")
    add(Record("HPCA-0005", "dortiz", "Calendar", "GENERAL-3Y", "2027-03-07"), m)

    # R006 large attachment (2 MB)
    m = _base(rng, f"sam.price@{DOMAIN}", [f"dana.ortiz@{DOMAIN}"], "Site survey scans",
              t0 + timedelta(days=4), "HPCA-0006")
    m.set_content("Scans attached.\n")
    m.add_attachment(_blob(rng, 2_000_000, b"\xff\xd8\xff\xe0"), maintype="image", subtype="jpeg",
                     filename="site-survey.jpg")
    add(Record("HPCA-0006", "sprice", "Inbox", "PROJECTS-7Y", "2031-03-08"), m)

    # R007 leaver custodian - no active Entra account
    m = _base(rng, f"j.walsh@{DOMAIN}", [f"dana.ortiz@{DOMAIN}"], "Handover notes",
              t0 - timedelta(days=200), "HPCA-0007")
    m.set_content("Handover notes before I leave.\n")
    add(Record("HPCA-0007", "jwalsh", "Sent Items", "HR-7Y", "2030-08-17",
               note="leaver: account disabled in source directory"), m)

    # R008 shared mailbox
    m = _base(rng, f"supplier@supplier.example", [f"accounts@{DOMAIN}"], "Remittance advice",
              t0 + timedelta(days=5), "HPCA-0008")
    m.set_content("Remittance advice attached.\n")
    m.add_attachment(b"date,amount\n2024-03-09,4471.00\n", maintype="text", subtype="csv",
                     filename="remittance.csv")
    add(Record("HPCA-0008", "accounts-shared", "Inbox", "FINANCE-10Y", "2034-03-09",
               note="shared mailbox: no single human custodian"), m)

    # R009/R010 same message archived for two custodians (duplicate Message-ID)
    m = _base(rng, f"lee.chen@{DOMAIN}", [f"dana.ortiz@{DOMAIN}"], "Pricing exception approval",
              t0 + timedelta(days=6), "HPCA-0009", cc=[f"sam.price@{DOMAIN}"])
    m.set_content("Approved for this quarter only.\n")
    raw = _freeze(m, "HPCA-0009")
    out.append((Record("HPCA-0009", "lchen", "Sent Items", "CONTRACTS-7Y", "2031-03-10",
                       legal_holds=["LH-2025-014"]), raw))
    out.append((Record("HPCA-0010", "sprice", "Inbox", "CONTRACTS-7Y", "2031-03-10",
                       legal_holds=["LH-2025-014"],
                       note="same message as HPCA-0009, archived for a second custodian"),
                raw.replace(b"X-HPCA-Archive-ID: HPCA-0009", b"X-HPCA-Archive-ID: HPCA-0010")))

    # R011 retention expired but under legal hold: hold must win
    m = _base(rng, f"dana.ortiz@{DOMAIN}", [f"legal@{DOMAIN}"], "Dispute background",
              datetime(2019, 5, 2, 14, 0, tzinfo=timezone.utc), "HPCA-0011")
    m.set_content("Background for the dispute file.\n")
    add(Record("HPCA-0011", "dortiz", "Legal", "GENERAL-3Y", "2022-05-02",
               legal_holds=["LH-2025-014"],
               note="retention expired; must not be disposed while hold LH-2025-014 is active"), m)

    return out


def write_export(out_dir: Path, seed: int = 7) -> Path:
    out_dir = Path(out_dir)
    (out_dir / "items").mkdir(parents=True, exist_ok=True)
    index = []
    for rec, raw in build_records(seed):
        (out_dir / "items" / f"{rec.archive_id}.eml").write_bytes(raw)
        meta = rec.__dict__.copy()
        (out_dir / "items" / f"{rec.archive_id}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        index.append(meta)
    (out_dir / "export-index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    return out_dir
