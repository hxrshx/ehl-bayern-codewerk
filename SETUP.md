# Team Setup: Entire CLI Checkpoint Tracking

This repo tracks AI coding session checkpoints with the [Entire CLI](https://entire.io),
which EHL sponsors may require for submission (`entire/checkpoints/v1` branch).

Checkpoint config (`.entire/settings.json`, `.claude/settings.json`, `.entire/.gitignore`)
is already committed to `main` so everyone shares the same setup. To avoid local file
conflicts, follow this order exactly:

## One-time setup (do this first, before anything else)

1. **Pull the repo first** — get the shared config before creating any local config of your own:
   ```bash
   git clone https://github.com/hxrshx/ehl-bayern-codewerk.git
   cd ehl-bayern-codewerk
   ```
   (If you already cloned earlier, just run `git pull`.)

2. **Install the Entire CLI:**
   ```bash
   brew tap entireio/tap
   brew trust entireio/tap
   brew install --cask entire
   ```

3. **Enable tracking for Claude Code** (reads the already-committed config, just installs
   your local git hooks — hooks live in `.git/hooks` and are never shared via git, so
   everyone must run this individually):
   ```bash
   entire enable --agent claude-code
   ```

4. Verify it worked:
   ```bash
   entire doctor
   entire status
   ```

## Daily workflow

```bash
git pull    # before starting work — pick up teammates' checkpoints
...work...
git push    # after finishing — share your checkpoints
```

## If `git pull` fails with "untracked working tree files would be overwritten"

This means you ran `entire enable` before pulling, so you have local copies of the
same config files that are now conflicting with the committed ones. Fix once:

```bash
rm -f .claude/settings.json .entire/.gitignore .entire/settings.json
git pull
```

After this one-time fix, future pulls work normally.

## Checking checkpoints

```bash
entire session list      # sessions tracked on this machine
entire checkpoint list   # checkpoints on the current branch
entire status            # overall health + active sessions
```
