# Claude Code review agents

Three Claude Code agents that review your work before it ships: one for code quality, one for writing, one for release-readiness on Django-on-Render projects.

## How these work

A Claude Code agent is a subagent with its own context window and a restricted set of tools. Claude can invoke one on its own when the description matches what you are doing, or you can ask for it by name. Each runs its review and reports back without editing your files, so the noise of a review stays out of your main conversation and you keep control of the changes.

- **`code-reviewer`** — senior code review for quality and maintainability. Reads the pending `git diff`, reports findings as Critical / Warning / Suggestion with file:line and a concrete fix. Looks at clarity, duplication, error handling, edge cases, over-large functions, test coverage, and consistency with the surrounding style. Reviews only; does not edit.
- **`ai-signal-reviewer`** — checks human-facing prose (docs, READMEs, PR descriptions, commit messages, emails) for AI writing tells and formatting traps: em dashes, AI-typical vocabulary, significance inflation, rule-of-three, elegant variation, curly quotes, and Unicode box-drawing characters in tables. Flags each issue with its location and a short replacement rather than rewriting the piece. Runs on the `haiku` model to keep it cheap; change the `model:` line in the file if you want a different one.
- **`deploy-guard`** — pre-ship review for **Django-on-Render** projects. Reads the pending diff and reports Blocker / Warning / Note findings for secrets and PII leaks, Django settings (DEBUG, SECRET_KEY, ALLOWED_HOSTS, migrations, POST-only views, i18n), Render persistent-disk and memory-accounting traps, and a couple of LLM-SDK gotchas. This one is scoped to the Django-on-Render stack; the checks outside that stack will not apply if you use something else.

## Install

Copy the agent files into your Claude Code agents directory:

```bash
mkdir -p ~/.claude/agents
cp agents/*.md ~/.claude/agents/
```

They are available immediately. Ask for one by name ("have the code-reviewer look at this"), or let Claude pick them up on its own when you are about to ship.

## Notes

- The agents are review-only by design. None of them edit your files; they report findings so you decide what to change.
- `code-reviewer` and `deploy-guard` split the work: `code-reviewer` stays on code quality, `deploy-guard` covers the shipping checks (secrets, framework correctness, deploy target). Running both before a release covers each side once.
- `ai-signal-reviewer` enforces a plain-prose style (no em dashes, no marketing vocabulary, no box-drawing characters in tables). Edit the rule list in the file to match your own house style.

## License

MIT. See [LICENSE](LICENSE).
