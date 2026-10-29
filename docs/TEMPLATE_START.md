# Use this template, clone, bootstrap and first push

**Create your own private repository from the template; don't fork it and don't download the ZIP.** A fork of a public repository is always public, so your project notes would be visible to anyone. A ZIP has no Git history and cannot perform the first-push flow.

1. Sign into GitHub. If the template repository is private, ask the owner to add you as a collaborator first.
2. Go to https://github.com/Rla102277/InfiniteArchOpenClaw and click **Use this template → Create a new repository**. Choose YOUR account as the owner, give it a name, select **Private**, and click **Create repository**. Do not change the template repository itself.
3. Check the new page: the URL shows YOUR username and your repository name, and the label next to the name says **Private**.
4. Install GitHub Desktop from https://desktop.github.com and sign in.
5. On YOUR new repository, click **Code → Open with GitHub Desktop** and clone into Documents.
6. In GitHub Desktop, choose **Repository → Open in Terminal**. Then run `./bootstrap.sh`.
7. If macOS prompts for Apple Command Line Tools, complete that installation and rerun. If Homebrew is missing, open https://brew.sh when asked and follow official instructions, then rerun. If Hermes is missing, install Hermes Desktop from https://hermes-agent.nousresearch.com/docs/getting-started/installation, reopen Terminal and rerun.
8. The script seeds `~/PersonalAIHQ`, verifies the local app, offers model/GitHub/Codex sign-in and skill trust, and when all required tools are present calls the guarded first-push script.
9. Type `PUSH` when you authorize the first commit to YOUR repository. Refresh it on GitHub and look for `onboarding/FIRST_BOOTSTRAP.md`. If a network issue interrupts the push, rerun `./scripts/first-push.sh`.
10. Run `./hq start`, open http://127.0.0.1:8731, create a project. Open another Terminal window in the same repo and run `./hq chat` for the conversational engineering process.

The first-push script checks that you are signed in as the repository's owner, that the repository is **private** and **not a fork**, that it is not the template itself, that the remote is GitHub, that the working tree is clean, and that you typed PUSH. It stops with a plain explanation if any check fails. Personal notes and secrets remain in local private storage unless you intentionally export them.

**Important limits:** The local dashboard works, but its chat is not yet integrated directly with Hermes/Codex execution. Mac mini deployment is future work. First-run installations require real user interaction and may incur paid AI-provider costs. Treat installed scripts and third-party skills as executable software and review the trust prompt.
