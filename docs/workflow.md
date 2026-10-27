# Workflow

How two people and their agents work in this repo without stepping on each other.
Agents: this is binding. Read it before your first git command in a session.

## People
- **Rohan** (`arorarohan`): the more experienced developer. Reviews and merges every PR.
- **Chris** (`BigManChris`): finance and research knowledge; newer to git.

Only Rohan merges into `main`. This is a team rule, not a GitHub setting. Agents never merge a
PR unless Rohan, in that session, tells them to.

## The loop
GitHub issues are the project tracker. Every piece of work is an issue, a branch, and a PR.

1. **Issue.** Find or open an issue for the task, assigned to whoever is doing it. Work only on
   issues assigned to your user. If an issue is assigned to the other person, ask before you
   touch it. `docs/roadmap.md` stays the big picture; issues are the units of work.
2. **Sync main, then branch.** Always start from an up-to-date `main`:
   ```
   git switch main
   git pull --ff-only
   git switch -c <type>/<issue#>-<short-slug>     # e.g. feat/12-kalshi-econ-series
   ```
   Types: `feat`, `fix`, `research`, `docs`, `chore`, `test`, `infra`.
3. **Commit small, push often.** Conventional messages (`feat(recorder): ...`) ending with
   `Refs #N`. Run tests and ruff before each commit, chained with `&&`:
   `uv run ruff check . && uv run ruff format --check . && uv run pytest && git commit ...`.
   Push the branch after each logical unit: `git push -u origin <branch>` the first time, then
   `git push`.
4. **Open a PR early**, as a draft if it's not finished (`gh pr create --draft`). The body
   starts with `Closes #N` (or `Refs #N` if the PR doesn't finish the issue) and uses the template.
   Trivial chores may skip the issue; nothing skips the PR.
5. **Keep up with main.** If `main` moves while you work, merge it into your branch. Don't rebase:
   ```
   git fetch origin
   git merge origin/main        # resolve conflicts, re-run tests, commit, push
   ```
6. **Ready for review.** CI is green, the PR body is filled in, then `gh pr ready`. Tell your user
   it is waiting for Rohan.
7. **Rohan merges** with a merge commit (no squash). Branches are not deleted after merge.
   Afterwards everyone runs step 2 again before the next task.

## Hard rules
- **Never commit on `main` locally, never push to `main`.** A Claude Code hook
  (`.claude/hooks/guard_main.py`) blocks this. If it fires, you're on the wrong branch: make a
  branch, don't try to get around the hook.
- **Local `main` only ever moves by `git pull --ff-only`.** If that fails, local `main` has stray
  commits. Stop, explain what happened to your user, then move them to a branch
  (`git branch rescue/<slug>`) before resetting `main` to `origin/main`. Ask before the reset.
- **No history rewriting on shared branches**: no `rebase`, `reset --hard`, `commit --amend` or
  force-push on anything already pushed, except `git push --force-with-lease` on your own branch
  when your user agrees. Plain `--force` is blocked.
- **No destructive commands without asking**: `git reset --hard`, `git clean`, `git checkout -- .`,
  `git stash drop`, `git branch -D`, deleting remote branches. Look at what would be lost first.
- **One task per branch.** Unrelated fixes go in their own branch and PR.
- Never commit market data, secrets or `.env` files. Never commit personal files
  (`CLAUDE.local.md`, `.local/`).

## Issues
- Agents may open issues (follow-ups, bugs, ideas they find) without asking, but must **tell
  their user** in the same reply, with the link. Inform, don't ask.
- Labels: one type (`research`, `infra`, `bug`, `documentation`, `chore`, `enhancement`) plus
  any `area:*` (`recorder`, `backtest`, `polymarket`, `kalshi`, `crypto`).
- Assign to `arorarohan` or `BigManChris`. Unassigned means not yet picked up.
- Close issues through PRs (`Closes #N`), not by hand, unless dropping them (then comment why).

## Research decisions
- **Approving** a result needs sign-off from **both** Rohan and Chris, as comments on the
  PR or issue. Approving means: declaring an edge real, promoting a hypothesis to a forward
  test or paper fund, or changing a pre-declared decision rule.
- **Killing** a hypothesis needs no permission if it is your own work. Write up why in
  `docs/findings/` as usual. Killing the other person's work needs their agreement first.
- Decision rules are written down before a test runs (existing rule), in the PR or findings doc.

## Shared files that conflict
`docs/roadmap.md`, `docs/gotchas.md`, `CLAUDE.md`, `pyproject.toml` and `uv.lock` are touched
by everyone. Keep edits to them small and on-topic, and merge `origin/main` before opening the
PR. For `uv.lock` conflicts, take main's version and re-run `uv lock`. Don't hand-merge it.

## Personal setup
Each person's agent instructions and learning notes are personal and gitignored:
`CLAUDE.local.md` at the repo root (Claude Code loads it automatically) and anything under
`.local/`. Don't put personal preferences in `CLAUDE.md`.
