# Project Board

A reusable board for tasks such as “Outline a small project.” Each local record has a title, status, and note. No personal records are shipped.

`app.json` is the complete declaration. The shared record-board renderer supplies behavior; this app has no executable code, credentials, or network permission. Creating or changing the declaration follows [the builder contract](../../system/contracts/BUILDER.md). Records belong in the external private data root, not this directory.
