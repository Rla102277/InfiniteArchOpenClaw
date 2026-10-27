# Declarative app builder

The generic builder turns a short interview into a previewed declaration using the fixed [manifest schema](MANIFESTS.md). The supported result is a record board with fields `title`, `status`, `note` and permission `local.records`. It does not generate or execute arbitrary application code.

## Interview and preview

Ask about the problem, users, desired outcome, and an observable acceptance check. Gather a generic name and valid unique ID. The dashboard asks one question per step; conversational coordinators use Grill Me for discovery and Wayfinder to bound the implementation plan. Avoid collecting personal sample records. Offer only `project-board`, `reading-room`, or `daily-planner`. Do not imply calendar integration, web scraping, messaging, model calls, or custom fields.

Construct the exact JSON, validate it, and show the destination `apps/<id>/app.json`, complete content, permission list, generated specification, plan, and rollback. Include the companion `SPEC.md` and `HANDOFF.md` in the reviewed output scope. The preview performs no canonical write. Reject invalid paths, existing IDs, unsupported templates, additional manifest keys, and requested executable content. Persisting a draft in private local storage is distinct from saving a canonical declaration and must follow its own local write boundary.

## Approval and write

Only explicit owner approval for the reviewed preview permits saving the declaration bundle. The local action flow uses `builder.preview`, followed by `builder.create` with the same interview/template/name/ID and explicit confirmation. Regenerate and validate the declaration from that reviewed input, and reject an existing target. Record a local audit result. Repeating creation with the same ID fails safely rather than overwriting the app. See [TASKS_AND_APPROVALS.md](TASKS_AND_APPROVALS.md); builder confirmation is separate from the external-agent approval table.

The builder's canonical write scope is `apps/<id>/app.json`, `apps/<id>/SPEC.md`, and `apps/<id>/HANDOFF.md`: declarations and Markdown only. It cannot write Python, JavaScript, shell scripts, dependencies, hooks, skills, CI configuration, credentials, or provider jobs. The shared renderer reads the declaration; no generated executable is loaded. A complete bundle is staged outside the catalog and atomically renamed into place; a write failure before publication does not leave a partial app. Private catalog reconciliation happens afterward and is retryable. A saved declaration is not automatically committed, pushed, published, installed, or deployed. Cryptographically bound preview tokens and approval expiry are not implemented.

## Acceptance and rollback

Demonstrate interview → preview with zero canonical writes → explicit approval → one validated declaration bundle → rendering and private local record persistence. Verify missing confirmation, invalid paths, unsupported manifests, and ID collisions cannot write. Review changed input as a new preview before confirming. Evidence must come from the runtime owner's checks; adding these Markdown requirements does not prove implementation.

Rollback preserves local records: disable use of the new declaration and restore the previously reviewed declaration through another approved change. Do not delete the private database or user content. An arbitrary-code generator is future work requiring its own sandbox, dependency review, execution permissions, and release gate.
