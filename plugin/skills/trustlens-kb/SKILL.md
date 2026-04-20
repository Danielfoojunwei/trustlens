---
name: trustlens-kb
description: Build, version, edit, export, and revert the per-tenant knowledge base. Use whenever the user asks to "load my docs", "add to KB", "delete a doc", "export my KB", "revert the KB", or "what's in my KB".
---

# TrustLens KB management

## Discover

- `kb_list(tenant_id)` — show all docs (id + source_uri + first 120 chars).
- `kb_versions(tenant_id)` — show the version timeline; each row says
  who committed it, doc count, and a summary like "+3/~1 via bulk_upsert".

## Bulk load from any source

If the user points you at a JSON / JSONL / CSV / URL:

1. Parse into the canonical shape: `{doc_id, text, source_uri?, metadata?}`.
2. Validate that every `text` is non-empty and `doc_id` is unique within
   the batch. If duplicates exist, ask the user whether to keep the last
   or merge.
3. Call `kb_upsert(tenant_id, documents)`. It returns the new version
   number — surface it to the user.

For very large corpora (>10k docs), batch in chunks of 500 and report
progress between batches.

## Single-doc edit

The user often wants to fix one fact:

1. `kb_list(tenant_id)` → find the doc by id or text match.
2. Show the current text.
3. Confirm the edit (read the new text back).
4. `kb_upsert(tenant_id, [{doc_id, text, source_uri, metadata}])` — same
   `doc_id` overwrites in place. Confirm new version number.

## Delete (DESTRUCTIVE — confirm)

> "I'm about to delete N docs from tenant <X>'s KB. The change creates
> version V+1 which can be reverted later, but the underlying docs go
> away. Proceed?"

After explicit yes → `kb_delete(tenant_id, doc_ids)`.

## Revert

`kb_revert(tenant_id, version=N)` rolls back. **Always** show the user
what `kb_versions` looks like before reverting so they pick the right
target.

## Export

`kb_export(tenant_id, fmt="jsonl")` returns the full corpus. For very
large KBs, suggest the user download via `GET /v1/admin/kb/{tid}/export?fmt=jsonl`
directly (single round-trip, no MCP overhead).

## Quality checks the agent should run unprompted

Whenever the user finishes a load:
1. Run a **smoke verification** against a known-grounded claim from the
   loaded docs. If it doesn't VERIFY, KB scoring may need tuning.
2. Show `axes_summary()` — external axis should jump after a load.
