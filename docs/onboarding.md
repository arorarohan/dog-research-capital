# Onboarding (for Chris's Claude Code agent)

**Chris:** open Claude Code in the folder where you keep projects and say:
> Follow the onboarding guide at
> https://raw.githubusercontent.com/arorarohan/dog-research-capital/main/docs/onboarding.md

**Agent:** you are setting up a new contributor who is new to git. Treat this as a lesson as
well as a setup. Before each git or GitHub command, say in one or two plain sentences what it
does and why. Afterwards, say what changed. Use analogies, not jargon. Check each step before
you move on. Skip steps that are already done, and say so. Anything interactive (logins,
installers that need a password) is Chris's to run: tell him to type `! <command>` in the
prompt.

## 1. Platform
Find out the OS. On Windows, recommend WSL2 (Ubuntu) and do everything inside it, since that's
what Rohan uses and the recorder targets Linux. If Chris prefers native Windows, carry on, but
note that the repo's hook runs through `bash` (Git Bash).

## 2. Tools
Check for each one and install it if it's missing:
- `git` (2.40+)
- `gh` (GitHub CLI)
- `uv` (https://docs.astral.sh/uv/; installs Python for us)

## 3. Identity and GitHub login
- `git config --global user.name "Chris ..."` and `user.email` (the email on his GitHub account;
  a GitHub noreply address is fine). Explain that this name goes on every commit.
- `git config --global init.defaultBranch main`
- Chris runs `! gh auth login` (GitHub.com, HTTPS, log in with browser), then
  `gh auth setup-git` so git pushes use that login.
- Accept the repo invite: open https://github.com/arorarohan/dog-research-capital/invitations,
  or run `gh api user/repository_invitations` to find it and accept it by its id with
  `gh api -X PATCH user/repository_invitations/<id>`.

## 4. Clone and install
```
gh repo clone arorarohan/dog-research-capital
cd dog-research-capital
git config pull.ff only          # pulls never invent merge commits
uv sync
uv run pytest
```
Explain the result: a clone is a full copy of the history, `origin` is the GitHub copy, and
`main` is the shared trunk nobody commits to directly. Tests should pass. If any fail, stop and
tell Rohan with the output.

## 5. Personal instructions
Ask Chris how he'd like his agent to work with him (how much to explain, what he already knows
about markets and research, what he wants to learn). Write the answers to `CLAUDE.local.md` at
the repo root. It is gitignored and Claude Code loads it automatically. Always include:
```
# Chris's local instructions (gitignored, not shared)
- Chris is learning git. Before each git/gh command, explain in plain words what it does and
  why; after it, what changed. Point out mistakes that would have been easy to make.
```
If he wants a learning library like Rohan's, put it under `.local/` (also gitignored).

## 6. Read the rules, then a practice run
Read `CLAUDE.md` and `docs/workflow.md` with Chris and summarise the loop: issue, branch,
commits, PR, Rohan merges, sync. Then do the whole loop once for real on a tiny change:
1. Open an issue "Onboarding: add Chris to README" assigned to `BigManChris`.
2. Sync main and create branch `docs/<issue#>-onboarding-chris`.
3. Add a line to the README listing Chris as a contributor, commit with `Refs #<issue#>`, push.
4. Open a PR with `Closes #<issue#>` and ask Rohan to review.
5. Show Chris the PR on GitHub. Explain that once Rohan merges it, the next task starts again
   from step 2 of the loop.

## 7. Market data (read-only copy of the Drive archive)
Recordings live on Rohan's Google Drive in a folder `drc` (layout and policy: `docs/sync.md`).
Chris reads it with his own rclone login; he never writes to it.

**Rohan does first (once), and tells Chris when done:**
1. Share the Drive folder `drc` with Chris's Google account as **Viewer**, and send him the
   folder link (`https://drive.google.com/drive/folders/<FOLDER_ID>`).
2. Google Cloud console, project `drc-rclone`, OAuth consent screen > *Test users*: add Chris's
   Google account (the app is in Testing; only listed users can log in).
3. Send Chris the OAuth client secret privately (not in chat with an agent, not in the repo).
   The client ID is public and is in `docs/sync.md`.

**Chris (his agent explains each step):**
1. Install rclone as in `docs/sync.md` "Setup on a new machine", step 1 (download, check the
   SHA256, put it on PATH).
2. Save the client secret yourself, in a normal terminal (not through the agent):
   `mkdir -p ~/.config/rclone && nano ~/.config/rclone/client_secret.txt` (paste, save), then
   `chmod 600 ~/.config/rclone/client_secret.txt`.
3. Create a remote called `drc` whose root is the shared folder (`root_folder_id`; a folder
   shared with you is under "Shared with me", not your own Drive, so `gdrive:drc` would not find
   it) and that can only read (`drive.readonly`). Chris runs, with the IDs filled in:
   ```
   ! rclone config create drc drive scope=drive.readonly root_folder_id=<FOLDER_ID> client_id=<CLIENT_ID> client_secret="$(cat ~/.config/rclone/client_secret.txt)"
   ```
   It prints a `http://127.0.0.1:53682/auth?...` link: open it in the browser, pick your Google
   account, *Advanced > Go to drc-rclone*, Allow. Then `date +%s > ~/.config/rclone/drc-authorized-at`.
   (The read-only scope is not yet tried with our Testing app; if Google refuses it, use
   `scope=drive` and rely on the Viewer role.)
4. Check: `rclone lsf drc:` lists `rohan-laptop/` and `vm-london/`.
5. In the repo: `export DRC_REMOTE=drc:` (add it to `~/.bashrc`). Do **not** set `DRC_HOST`:
   Chris has no recorder, and without it `drc-sync push` refuses to run.
6. Prove it end to end with a restore drill on a recent hour (raw is kept 15 days):
   `uv run drc-sync --host vm-london restore-drill --venue kalshi --date <YYYY-MM-DD> --hour 17`
   should end with `PASS`.
7. Pull what you need into `data/remote/vm-london/` (Parquet, kept forever; all of it was 7.1 GiB
   on 2026-11-06 and grows ~0.9 GB a day, so pull slices):
   ```
   uv run drc-sync pull --host vm-london --what norm --root raw_forward --venue kalshi
   uv run drc-sync pull --host vm-london --what norm --root raw_forward --venue polymarket --date 2026-11-03
   ```

**Weekly:** the login dies 7 days after each authorization while the app is in Testing
(`drc-sync` warns from day 5, then says `AUTH EXPIRED`). Renew with `! rclone config reconnect drc:`
and `date +%s > ~/.config/rclone/drc-authorized-at`.

**Never upload to the shared archive** (no `drc-sync push`, no `rclone copy` into `drc:`): the
archive is Rohan's VM and laptop output only, and the read-only scope plus Viewer role block it
anyway. The quota catch: a file Chris uploads into Rohan's folder is owned by Chris and counts
against **Chris's** Drive storage (15 GB free), not Rohan's 400 GiB; only Chris can free it. A
day of VM raw is ~5 GB, so it would fill within days and fail halfway. Downloads use no storage.
If Chris ever records data of his own, talk to Rohan first about where it goes.

## 8. Done
Tell Chris what was set up, where his personal file is, and the one rule to remember: **start
every task by syncing main and making a branch; never work on main.**
