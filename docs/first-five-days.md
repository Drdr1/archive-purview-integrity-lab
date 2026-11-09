# First five working days against a real archive and tenant

The lab's pipeline stays the same; the two simulated ends get replaced with real ones.

## Day 1: the archive side
- Read the archive's export API and MCP tools. What native format comes out (EML/MIME, MSG,
  vendor container)? Are attachments embedded or stubbed? Which metadata (custodian account,
  retention class, hold IDs, ingest date, folder) is available per item?
- Point `manifest` at a real nonproduction export. Every field the sidecar can't fill
  becomes a gap on the list.

## Day 2: identity and licensing
- Build the identity map from the archive's account list and Entra: active, leaver, shared,
  unmapped.
- Confirm licensing for the test custodians (eDiscovery Premium, Records Management) and which
  Graph permissions the tenant will grant (application vs delegated `eDiscovery.ReadWrite.All`).

## Day 3: push a handful of records through each viable path
- Review-set upload: stage, upload with the AzCopy command the Purview wizard generates, process.
- Mailbox path: only if a partner connector or PST import is in scope for the test tenant.
- Graph connector: if the existing Copilot connector is available, add it to a case as a
  non-Microsoft 365 location and search it.

## Day 4: reconcile and test holds
- Export from the review set and run `reconcile` against the source manifest. Also check
  Purview's upload Summary/Items CSV reports against the staging report.
- Apply a hold and test whether it covers each path's copy. Record what actually happens.

## Day 5: decide and scope
- Fill in the "to validate" cells of the boundary matrix with observed behaviour.
- Choose the PoC path and the representative record set; list licensing, permissions and
  production dependencies.

## Needed from the client
- Archive API docs and a nonproduction endpoint
- Sample records with edge cases (leavers, shared mailboxes, large/nested attachments)
- A test tenant with eDiscovery Premium licences for test custodians
- Permission to register an Entra app; current licence SKUs
- One archive developer reachable in writing
