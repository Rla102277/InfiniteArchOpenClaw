# Mac mini target — future release plan

**Status: design only. No production installer ships in v0.1.**

- Hardware sizing: determine memory, storage, local-vs-hosted model choice and concurrent agents.
- Separate service account with least privilege; dedicated private runtime folders and secrets.
- Restore project code, settings, approved skills and the knowledge vault from versioned backups.
- Run Hermes and approved background workers under `launchd`, with resource caps and crash restart.
- Use Tailscale (with explicit account approval) for private remote access; never expose the v0.1 HQ directly to public internet.
- Require health endpoint, metrics, structured logs, alerting, manual fallback and tested rollback before autonomous scheduling.
- Do not run unrestricted coding agents under the same account as sensitive production data.
- Verify test, deployment and disaster-recovery evidence on the actual target Mac before release.
