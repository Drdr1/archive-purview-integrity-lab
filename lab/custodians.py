"""Map archive-side accounts to Entra identities - explicitly, never by guessing.

Input CSV columns: archive_account, upn, kind
  kind = active | leaver | shared   (anything else is treated as unknown)

Every archive account ends in exactly one bucket, with a reason:
  mapped      active user with a UPN: can be an eDiscovery custodian
  leaver      UPN exists but the account is gone: needs an inactive-mailbox
              or alternate-custodian decision from the records team
  shared      no single human custodian: needs a named owner or a
              non-custodial data source decision
  unmapped    not in the mapping at all: blocked until someone decides
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Mapping:
    archive_account: str
    status: str
    upn: str | None
    reason: str


def load_map(path: Path) -> dict[str, tuple[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["archive_account"].strip(): (r.get("upn", "").strip(), r.get("kind", "").strip().lower())
                for r in csv.DictReader(fh)}


def resolve(account: str, table: dict[str, tuple[str, str]]) -> Mapping:
    if account not in table:
        return Mapping(account, "unmapped", None, "archive account not present in the identity map")
    upn, kind = table[account]
    if kind == "active" and upn:
        return Mapping(account, "mapped", upn, "active Entra user")
    if kind == "leaver":
        return Mapping(account, "leaver", upn or None,
                       "account no longer active: decide inactive mailbox vs alternate custodian")
    if kind == "shared":
        return Mapping(account, "shared", upn or None,
                       "shared mailbox: assign a named owner or treat as non-custodial source")
    return Mapping(account, "unmapped", upn or None, f"unrecognised kind '{kind}' or missing UPN")
