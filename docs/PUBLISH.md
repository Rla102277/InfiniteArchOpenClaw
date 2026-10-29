# Publish the starter to GitHub (optional)

Until you publish it, the starter is an ordinary downloadable folder. It **is not yet a live GitHub repository**. A GitHub URL must not be invented before publication.

## Create the private source repository

On your Mac, install and sign in to `gh` with `./setup.sh`, then run:

```bash
./scripts/publish-private.sh personal-ai-hq-starter
```

The script previews staged files and requires typing `PUBLISH`. It creates a **private** GitHub repo and pushes once. Review every proposed file before confirming, especially anything in `projects/`. The source repo's default branch should be protected and CI required after creation.

## Enable repeatable use by friends

In the GitHub repository's Settings, enable **Template repository**. Then anyone with access can click **Use this template**, choose **Private**, name the project, and clone their new repository. Never ask friends to Fork: a fork of a public repository is public, and `scripts/first-push.sh` refuses forks and public repositories. Provide `docs/ONBOARDING.md` as their first link. GitHub access is still subject to the template repository's permissions.

If they need the original starter without your projects, publish from a fresh copy instead of a working project directory.
