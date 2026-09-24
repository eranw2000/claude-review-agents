# Claude Code review agents

Four Claude Code agents that check your work before it ships: one for code quality, one for writing, one for release-readiness across common stacks, and one that runs a pull request's tests and certifies whether it is ready to release. A companion hook runs a quick version of the writing check on every file Claude writes.

## The flow

![Review agents flow](docs/review-agents-flow.png)

Source: [docs/review-agents-flow.drawio](docs/review-agents-flow.drawio) (editable in draw.io).

## How these work

A Claude Code agent is a subagent with its own context window and a restricted set of tools. Claude can invoke one on its own when the description matches what you are doing, or you can ask for it by name. Each runs its review and reports back without editing your files, so the noise of a review stays out of your main conversation and you keep control of the changes.

- **`code-reviewer`**: senior code review for quality and maintainability. Reads the pending `git diff`, reports findings as Critical / Warning / Suggestion with file:line and a concrete fix. Looks at clarity, duplication, error handling, edge cases, over-large functions, test coverage, and consistency with the surrounding style. For changes to parsers, gates, and dispatchers it reproduces findings by executing the real path instead of reasoning about the code, and it checks new tests for vacuity (tests that cannot fail). Reviews only; does not edit.
- **`ai-signal-reviewer`**: checks human-facing prose (docs, READMEs, PR descriptions, commit messages, emails) for AI writing tells and formatting traps: em dashes, AI-typical vocabulary, significance inflation, rule-of-three, elegant variation, curly quotes, and Unicode box-drawing characters in tables. Flags each issue with its location and a short replacement rather than rewriting the piece. Runs on the `sonnet` model (the routine-work tier); change the `model:` line in the file if you want a different one.
- **`deploy-guard`**: pre-ship release-readiness review. Reads the pending diff, detects the stack, and reports Blocker / Warning / Note findings. Universal checks (secrets and PII leaks, config and prod hygiene, deploy-target safety) run on every project. Stack-specific profiles apply only when the diff touches that stack: Python/Django on Render (settings, migrations, POST-only views, i18n, persistent-disk and memory-accounting traps), Node/JS services, Docker, static frontends, LLM SDK usage, and Power Automate/M365. It states which stack(s) it detected at the top of its report.
- **`pr-validator`**: runs a pull request's test suite and writes a release verdict. Reads the PR (or the branch diff against its base), summarizes what changed, detects and runs the project's test runner (pytest, npm test, Django, go test, and so on), then reports a complete per-test pass/fail list. It also proves every test the diff adds actually ran, since a test can read as green while never executing (defined after a custom `__main__` runner, or missing from a hand-maintained call list), and returns the PR if any added test cannot be shown to have run. If every test passed it marks the PR **ready for release**; if anything failed, the suite could not run, or there are no tests, it returns the PR to the programmer with the failing tests named. It runs the tests but does not edit, fix, merge, or deploy. A Claude Code agent runs inside a session, so invoke it right after you open a PR (it does not fire on the GitHub PR event by itself).

## The hook

- **`ai-signal-check.py`**: a PostToolUse hook that runs a quick version of the `ai-signal-reviewer` check on every file Claude writes or edits, including files written from the shell with a heredoc, `>` or `tee`. It reads only the text just added, never the whole file. It flags em and en dashes, curly quotes and box-drawing characters in any prose or markup file, and the AI-typical vocabulary and constructions in prose files (`.md`, `.txt`, READMEs and similar). Code files get only a check for a stray closing tool-call tag line. It never blocks: it adds a warning Claude sees on its next turn. Files that only Claude reads are exempt: any file named `CLAUDE.md`, anything under `~/.claude/plans/`, and `TODO.md`, `MEMORY.md` and `memory/` files under `~/.claude/projects/`. `_bash_write_targets.py` and `_bash_command_parse.py` are its helpers for the shell case.

## Install

Copy the agent files into your Claude Code agents directory:

```bash
mkdir -p ~/.claude/agents
cp agents/*.md ~/.claude/agents/
```

They are available immediately. Ask for one by name ("have the code-reviewer look at this"), or let Claude pick them up on its own when you are about to ship.

### The hook (optional)

Copy the hook and its two helpers, then wire it in `~/.claude/settings.json` on the write tools and on Bash:

```bash
mkdir -p ~/.claude/hooks
cp hooks/ai-signal-check.py hooks/_bash_write_targets.py hooks/_bash_command_parse.py ~/.claude/hooks/
```

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          { "type": "command", "command": "python3 $HOME/.claude/hooks/ai-signal-check.py" }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 $HOME/.claude/hooks/ai-signal-check.py" }
        ]
      }
    ]
  }
}
```

If you already have PostToolUse entries with these matchers, add the command to their `hooks` list instead of adding a second entry. The helpers are the same files the `claude-release-workflow` pack ships, so installing both packs is safe.

## Notes

- None of the agents edit your files; they report so you decide what to change. Three are read-only reviewers; `pr-validator` also runs the test suite (it executes tests but changes nothing else).
- `code-reviewer` and `deploy-guard` split the work: `code-reviewer` stays on code quality, `deploy-guard` covers the shipping checks (secrets, framework correctness, deploy target). Running both before a release covers each side once.
- `pr-validator` is the test gate before release: run it right after you open a PR to get a pass/fail verdict on the suite. It answers "do the tests pass?", which the two reviewers do not check. To name dead or unregistered tests it can use the `test-integrity-check.py` hook from the companion `claude-release-workflow` pack, and works without it (falling back to running each added test directly).
- `code-reviewer` checks AI-agent code against the AI and agent security rules (`ai-agent-standards.md`) from the companion [secure-dev-guardrails](https://github.com/eranw2000/secure-dev-guardrails) pack (its installer puts the file in `/usr/local/share/secure-dev-guardrails/standards/`). Without that pack it still reviews; it just has no rule file to cite.
- `ai-signal-reviewer` enforces a plain-prose style (no em dashes, no marketing vocabulary, no box-drawing characters in tables). Edit the rule list in the file to match your own house style, and the word lists in `hooks/ai-signal-check.py` if you use the hook.

## License

MIT. See [LICENSE](LICENSE).

## Model routing

The agents in this pack pin a Claude Code model alias in their frontmatter, so each artifact runs on the tier its work needs:

- `model: inherit`: planning and judgment-heavy review. These run on your session model, so start a planning or review session on your strongest model (switch with `/model`).
- `model: opus`: execution and content work
- `model: sonnet`: routine or mechanical steps

If a pinned model is not available on your plan, or you prefer different routing, edit the `model:` line in the artifact's frontmatter, or delete it to inherit your session model.
