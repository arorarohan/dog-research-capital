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

## 7. Done
Tell Chris what was set up, where his personal file is, and the one rule to remember: **start
every task by syncing main and making a branch; never work on main.**
