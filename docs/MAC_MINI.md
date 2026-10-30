# Mac mini — manual local dashboard trial

**Production deployment remains unverified.** Bootstrap does not install services. This is an optional, manually installed user LaunchAgent for the local dashboard, not unattended coding agents. [Apple's launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) explains the property-list format and per-user agent directory.

## Prepare and verify

Use a dedicated Mac account and a separate deployment checkout of your fork, with least privilege. Keep development and service data separate. Back up both the checkout's `projects/` directory and its private `AIHQ_HOME`. Do not store secrets in the plist or Git.

In the deployment checkout, run bootstrap, the tests and the dashboard manually before installing a service:

```bash
./bootstrap.sh --yes
./hq validate
./hq test
./hq start --no-browser
```

Verify http://127.0.0.1:8731 responds, then stop it with Control-C. Resolve any fork/push prompt explicitly. Check `command -v python3` and that its version is at least 3.10. The example below uses `/opt/homebrew/bin/python3`; replace it with your actual absolute interpreter path. No Homebrew installation is implied.

Choose an approved service checkout outside macOS-protected Desktop/Documents folders, for example `/Users/YOU/Applications/InfiniteArchOpenClaw`. Replace **every** `/Users/YOU` path below with your service account's real absolute path. launchd does not expand `~`, shell variables or your interactive shell's startup configuration inside a plist. If macOS denies access, use an appropriately located checkout and normal system permissions; do not disable macOS security features.

## Create and review the agent manually

Create only these per-user directories:

```bash
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/PersonalAIHQ"
```

If `~/Library/LaunchAgents/com.infinitearch.hq.plist` already exists or the label is loaded, stop and back it up before making changes. Save the following text there using a local editor, after replacing the paths:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.infinitearch.hq</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/python3</string>
    <string>-m</string>
    <string>aihq.cli</string>
    <string>start</string>
    <string>--no-browser</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/YOU/Applications/InfiniteArchOpenClaw</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>AIHQ_HOME</key>
    <string>/Users/YOU/PersonalAIHQ</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>/Users/YOU/Library/Logs/PersonalAIHQ/dashboard.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/YOU/Library/Logs/PersonalAIHQ/dashboard.err.log</string>
</dict>
</plist>
```

KeepAlive is off for the trial so a failing process does not restart indefinitely. This agent runs in the logged-in user's session, starts at login after installation and stops at logout. It does not provide service before login. The dashboard remains bound to loopback. Its private database is `AIHQ_HOME/runtime/os.sqlite3`, outside the checkout. This example starts only the browser service; `./hq worker` is a separate process and `./hq worker --once` performs one local pass. No worker or model server is installed by this example.

Validate the plist; continue only if it reports OK:

```bash
plutil -lint "$HOME/Library/LaunchAgents/com.infinitearch.hq.plist"
```

Review the exact file and confirm its interpreter, checkout and log paths exist. Load it manually without sudo:

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.infinitearch.hq.plist"
launchctl print "gui/$(id -u)/com.infinitearch.hq"
```

Check http://127.0.0.1:8731 and the two log files. If startup fails, inspect the last exit status and logs before retrying; do not repeatedly load the same job. A loaded but stopped trial can be started manually with `launchctl kickstart "gui/$(id -u)/com.infinitearch.hq"`.

## Stop and roll back

Unload first; this stops the running dashboard:

```bash
launchctl bootout "gui/$(id -u)/com.infinitearch.hq"
```

If it reports that the job is not loaded, verify with `launchctl print` before continuing. Move the plist out of LaunchAgents to keep it for inspection and prevent the next-login launch; choose a fresh backup filename if this one exists:

```bash
mv "$HOME/Library/LaunchAgents/com.infinitearch.hq.plist" "$HOME/Library/Logs/PersonalAIHQ/com.infinitearch.hq.plist.disabled"
```

Verify the URL no longer responds and the job is absent from `launchctl print`. Keep project data, the private workspace and logs. For a software rollback, restore a previously verified deployment checkout and matching data backup while the service is stopped, rerun tests and the foreground trial, then review and reload the restored plist manually.

## Before an always-on release

Record actual target-Mac test results, restart/login behavior, backup restore evidence and owner acceptance in the project's handoff. Measure memory/storage with the chosen local model before scheduling workers. Add bounded log retention, health monitoring and a tested manual fallback. Keep any approved remote access private; this starter must not be exposed directly to the public internet.

Observed scope: a reviewable manual LaunchAgent example and rollback instructions. Unverified: service installation on a target Mac mini, login/restart behavior and production readiness. No service is installed or loaded by repository tests.
