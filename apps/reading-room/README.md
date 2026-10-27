# Reading Room

A reusable reading queue for entries such as “Introduction to gardening.” Each local record has a title, status, and note. No books, account links, or personal reading history are bundled.

`app.json` uses the shared record-board renderer. It grants only `local.records`; it cannot fetch articles or call a model. Follow [the manifest contract](../../system/contracts/MANIFESTS.md) for changes and keep actual reading records in the external private data root.
