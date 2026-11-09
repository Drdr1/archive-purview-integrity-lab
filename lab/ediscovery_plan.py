"""Microsoft Graph eDiscovery (Premium) call plan for the PoC case.

Produces the ordered v1.0 calls under /security/cases/ediscoveryCases:
create case -> add custodians -> hold policy -> search -> review set ->
add search to review set -> export. Long-running steps return an operation
that must be polled; the plan marks those.

Default is dry-run: it prints the plan so it can be reviewed before anything
touches a tenant. `run()` executes it with a caller-supplied transport.

Status: written against the published Graph v1.0 reference; NOT yet executed
against a live tenant. Permissions (application vs delegated
eDiscovery.ReadWrite.All) and licensing must be confirmed in the target tenant.
"""

from __future__ import annotations

import json
from typing import Callable

BASE = "https://graph.microsoft.com/v1.0/security/cases/ediscoveryCases"


def plan(case_name: str, custodian_upns: list[str], query: str, hold: bool = True) -> list[dict]:
    steps: list[dict] = [
        {"id": "case", "method": "POST", "url": BASE,
         "body": {"displayName": case_name,
                  "description": "PoC: archive records reconciled against source manifest"}},
    ]
    for i, upn in enumerate(custodian_upns):
        steps.append({"id": f"custodian{i}", "method": "POST", "url": BASE + "/{case}/custodians",
                      "body": {"email": upn}})
    if hold:
        steps.append({"id": "hold", "method": "POST", "url": BASE + "/{case}/legalHolds",
                      "body": {"displayName": f"{case_name} hold", "contentQuery": query},
                      "note": "Holds content in Microsoft 365 locations only; uploaded "
                              "review-set data is not covered by this hold."})
    steps += [
        {"id": "search", "method": "POST", "url": BASE + "/{case}/searches",
         "body": {"displayName": f"{case_name} search", "contentQuery": query}},
        {"id": "reviewset", "method": "POST", "url": BASE + "/{case}/reviewSets",
         "body": {"displayName": f"{case_name} review"}},
        {"id": "add", "method": "POST", "url": BASE + "/{case}/reviewSets/{reviewset}/addToReviewSet",
         "body": {"search": {"id": "{search}"}, "additionalDataOptions": "linkedFiles"},
         "long_running": True},
        {"id": "export", "method": "POST", "url": BASE + "/{case}/reviewSets/{reviewset}/export",
         "body": {"outputName": f"{case_name} export", "exportOptions": "originalFiles,fileInfo,tags",
                  "exportStructure": "directory"},
         "long_running": True},
    ]
    return steps


def _fill(value, ids: dict[str, str]):
    if isinstance(value, str):
        for k, v in ids.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


Transport = Callable[[str, str, dict], dict]


def run(steps: list[dict], transport: Transport) -> dict[str, str]:
    """Execute steps in order, substituting ids returned by earlier steps.

    transport(method, url, body) -> parsed JSON response (must include "id"
    for created objects). Raises on a missing id so a half-built case is visible.
    """
    ids: dict[str, str] = {}
    for step in steps:
        resp = transport(step["method"], _fill(step["url"], ids), _fill(step["body"], ids))
        if not step.get("long_running"):
            if "id" not in resp:
                raise RuntimeError(f"step {step['id']} returned no id: {resp}")
            ids[step["id"]] = resp["id"]
    return ids


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(plan("HPCA-PoC", ["dana.ortiz@contoso.example"], 'subject:"contract"'), indent=2))
