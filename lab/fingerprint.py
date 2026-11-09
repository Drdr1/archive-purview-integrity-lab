"""Evidence fingerprints for an email item at three levels.

1. raw      - SHA-256 of the exact bytes. Proves nothing changed at all, but
              breaks on any re-serialization (header folding, MIME boundaries,
              transfer encoding), which most processing pipelines do.
2. content  - SHA-256 of each attachment's *decoded* bytes, of the normalized
              text body, and of a canonical header set. These survive
              re-serialization and are what you reconcile on after landing.
3. presence - which provenance headers (X-HPCA-*) are still present, so you
              know whether archive metadata can ride inside the item or must
              travel in a separate manifest / load file.
"""

from __future__ import annotations

import hashlib
import json
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from datetime import timezone


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _addrs(msg: EmailMessage, name: str) -> list[str]:
    vals = msg.get_all(name, [])
    return sorted(a.lower() for _, a in getaddresses([str(v) for v in vals]) if a)


def header_set(msg: EmailMessage) -> dict:
    date = msg.get("Date")
    try:
        date_utc = parsedate_to_datetime(str(date)).astimezone(timezone.utc).isoformat() if date else None
    except (TypeError, ValueError):
        date_utc = str(date)
    return {
        "message_id": (str(msg.get("Message-ID", "")).strip() or None),
        "date_utc": date_utc,
        "from": _addrs(msg, "From"),
        "to": _addrs(msg, "To"),
        "cc": _addrs(msg, "Cc"),
        "subject": " ".join(str(msg.get("Subject", "")).split()),
    }


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()


def _attachments(msg: EmailMessage, prefix: str = "") -> list[dict]:
    """Decoded-content hashes for every attachment, recursing into nested messages.

    Uses an explicit walk so a forwarded message's attachments are reported once,
    under the forwarded message's path, instead of also appearing at top level.
    """
    found: list[dict] = []

    def visit(part: EmailMessage) -> None:
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            inner = part.get_payload()[0]
            label = f"{prefix}[forwarded:{header_set(inner)['message_id']}]"
            found.append({"path": label, "content_type": ctype,
                          "sha256": sha256(_canon(header_set(inner)) + _body_text(inner).encode()),
                          "size": None, "nested": True})
            found.extend(_attachments(inner, prefix=label + "/"))
            return
        if part.is_multipart():
            for sub in part.iter_parts():
                visit(sub)
            return
        disp = part.get_content_disposition()
        fname = part.get_filename()
        if disp in ("attachment", "inline") and (fname or part.get("Content-ID")):
            data = part.get_payload(decode=True) or b""
            found.append({"path": prefix + (fname or str(part.get("Content-ID"))),
                          "content_type": ctype, "sha256": sha256(data), "size": len(data),
                          "nested": False})

    if msg.is_multipart():
        for sub in msg.iter_parts():
            visit(sub)
    return found


def fingerprint(raw: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    headers = header_set(msg)
    atts = _attachments(msg)
    provenance = {k: str(v) for k, v in msg.items() if k.lower().startswith("x-hpca-")}
    return {
        "raw_sha256": sha256(raw),
        "raw_size": len(raw),
        "headers": headers,
        "header_sha256": sha256(_canon(headers)),
        "body_sha256": sha256(_body_text(msg).encode()),
        "attachments": sorted(atts, key=lambda a: a["path"]),
        "provenance_headers": provenance,
    }
