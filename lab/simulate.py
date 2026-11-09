"""Simulate a landing pipeline so the reconciler can be tested without a tenant.

THIS IS NOT PURVIEW. It applies generic transformations that any ingest /
review / export pipeline may apply, plus optional injected faults, so that the
reconciliation logic is exercised against known answers:

  reserialize     regenerate every message (new line folding / MIME layout):
                  raw hashes change, content must still match
  strip_x_headers drop X-HPCA-* provenance headers
  dedupe          keep one copy per Message-ID (as review/export dedupe can)
  drop_attachment remove one attachment from a named record (a real defect)
  shift_date      move one record's Date header (a real defect)

The real behaviour of Purview for each of these is what the first week of the
engagement is for; the reconciler is what measures it.
"""

from __future__ import annotations

import hashlib
from email import policy
from email.generator import BytesGenerator
from email.parser import BytesParser
from email.utils import format_datetime, parsedate_to_datetime
from datetime import timedelta
from io import BytesIO
from pathlib import Path


def _reserialize(msg) -> bytes:
    buf = BytesIO()
    BytesGenerator(buf, policy=policy.SMTP.clone(max_line_length=60)).flatten(msg)
    return buf.getvalue()


def _drop_first_attachment(msg) -> None:
    for part in msg.walk():
        if part.is_multipart():
            payload = part.get_payload()
            for i, p in enumerate(payload):
                if p.get_content_disposition() == "attachment":
                    part.set_payload(payload[:i] + payload[i + 1:])
                    return


def land(staged_root: Path, landed_dir: Path, *, reserialize: bool = True, strip_x_headers: bool = False,
         dedupe: bool = False, drop_attachment: str | None = None, shift_date: str | None = None) -> list[Path]:
    landed_dir = Path(landed_dir)
    landed_dir.mkdir(parents=True, exist_ok=True)
    seen_ids: set[str] = set()
    written = []
    for src in sorted(Path(staged_root).rglob("*.eml")):
        raw = src.read_bytes()
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        archive_id = str(msg.get("X-HPCA-Archive-ID", src.stem))
        mid = str(msg.get("Message-ID", ""))
        if dedupe and mid in seen_ids:
            continue
        seen_ids.add(mid)
        changed = False
        if strip_x_headers:
            for k in [k for k in msg.keys() if k.lower().startswith("x-hpca-")]:
                del msg[k]
            changed = True
        if drop_attachment and archive_id == drop_attachment:
            _drop_first_attachment(msg)
            changed = True
        if shift_date and archive_id == shift_date:
            when = parsedate_to_datetime(str(msg["Date"])) + timedelta(hours=1)
            del msg["Date"]
            msg["Date"] = format_datetime(when)
            changed = True
        out = _reserialize(msg) if (reserialize or changed) else raw
        # Export tools rarely keep source file names; use an opaque name.
        name = hashlib.sha1(src.as_posix().encode()).hexdigest()[:12] + ".eml"
        dest = landed_dir / name
        dest.write_bytes(out)
        written.append(dest)
    return written
