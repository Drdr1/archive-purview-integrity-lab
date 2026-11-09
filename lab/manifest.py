"""Source-side evidence manifest.

Built from the archive export *before* anything leaves the archive. Each entry
joins the archive metadata (custodian, retention class, holds) with the
three-level fingerprint of the native item. The manifest itself is hashed so a
later change to it is detectable (chain of custody for the manifest, not just
the items).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .fingerprint import _canon, fingerprint, sha256


def build_manifest(export_dir: Path) -> dict:
    export_dir = Path(export_dir)
    entries = []
    for eml in sorted((export_dir / "items").glob("*.eml")):
        meta = json.loads(eml.with_suffix(".json").read_text())
        entries.append({"archive": meta, "fingerprint": fingerprint(eml.read_bytes())})
    body = {
        "tool": f"archive-purview-integrity-lab {__version__}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(export_dir),
        "count": len(entries),
        "entries": entries,
    }
    body["entries_sha256"] = sha256(_canon(entries))
    return body


def write_manifest(manifest: dict, path: Path) -> Path:
    Path(path).write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return Path(path)


def load_manifest(path: Path) -> dict:
    manifest = json.loads(Path(path).read_text())
    expected = manifest.get("entries_sha256")
    actual = sha256(_canon(manifest["entries"]))
    if expected != actual:
        raise ValueError(f"manifest entries were modified after generation: {expected} != {actual}")
    return manifest
