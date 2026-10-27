# Personal AI HQ — Fork, clone, bootstrap

1. Create a GitHub account and sign in.
2. Open the **InfiniteArchOpenClaw** source repository. If it's private, the owner must grant you access and enable private repository forking where GitHub policy permits. A private repository cannot be freely forked by everyone.
3. Click **Fork**. Create a fork under **your own account**, ideally private. If forking is disabled, ask the owner to enable it or provide a clean template repository; don't push to the owner's source.
4. Clone your fork on your Mac. Easiest option: GitHub Desktop's **Code → Open with GitHub Desktop**. Or run `gh repo clone YOUR_USERNAME/InfiniteArchOpenClaw` after `gh auth login`.
5. Open Terminal in the cloned folder and run `./bootstrap.sh`.
6. The installer checks and offers official tool installs, seeds `~/PersonalAIHQ`, tests HQ, and asks to make your **first push**. Type `PUSH` to approve. This writes only the generic `onboarding/FIRST_BOOTSTRAP.md` marker to **your own fork**, never the upstream source.
7. Run `./hq start` to view your local HQ, and `./hq chat` to start guided discovery. Your private runtime folders stay outside Git.

If you already have a local clone with uncommitted changes, `./scripts/first-push.sh` refuses to push until you review those changes. It is safe to rerun; it will not create a duplicate first commit.

**Important:** Cloning your fork rather than downloading a ZIP is mandatory for the automatic push because Git remotes must exist. Do not put credentials or personal knowledge into the repository.
