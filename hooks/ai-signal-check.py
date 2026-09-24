#!/usr/bin/env python3
"""PostToolUse hook: warn on AI-writing signals in newly written/edited text.

Fires on Write / Edit / MultiEdit AND on Bash shell writes (heredocs,
`>`/`>>`, tee), via two settings.json matchers. Scans ONLY the text being
added (the tool_input's content / new_string, or a heredoc's body), never the
whole file, so it does not re-warn about pre-existing content elsewhere in a
file being edited. It is NON-BLOCKING: it emits a
hookSpecificOutput.additionalContext warning and exits 0, so the edit still
succeeds and Claude sees the warning on the next turn and can fix it.
Enforces the same writing rules as agents/ai-signal-reviewer.md: no AI
writing signals and no box-drawing characters.

INTERNAL-FILES EXEMPTION (the user's directive): the AI-signals rule
targets text that leaves us (READMEs, commit messages, PR bodies, emails,
docs). Internal bookkeeping between the user and Claude is exempt and exits 0
silently: anything under ~/.claude/plans/; the data-dir set under
~/.claude/projects/ (TODO.md, MEMORY.md, anything in a memory/ dir); and a
file whose basename is CLAUDE.md or CLAUDE_DECISIONS*.md ANYWHERE (harness
context for Claude even when committed; the harness-files protection keeps
those out of public/client repos). TODO.md and other docs inside code repos
and public packs are deliberately NOT exempt: they ship.

Checks:
  1. Typography (any file, near-zero false positives): em/en dashes, the
     horizontal bar, curly/smart quotes, box-drawing characters.
  2. Fuzzy word/phrase list (prose-ish files only, since these are noisy in
     code): the banned AI vocabulary and constructions from CLAUDE.md.
  3. Content-tag leak (any file): a standalone closing content/invoke tag
     line, which a batch of Write calls can leave behind.

Wired in ~/.claude/settings.json under hooks.PostToolUse, matchers
"^(Write|Edit|MultiEdit)$" and "^Bash$" (shared with test-integrity-check.py;
Bash write targets come from hooks/_bash_write_targets.py). The
hookSpecificOutput MUST carry hookEventName or Claude Code silently drops the
warning.

Special characters (dashes, curly quotes) are built from code points so this
source file contains none of them and can never flag itself.
"""

import json
import os
import re
import sys

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _bash_write_targets import (
        extract_write_targets,
        heredoc_bodies_by_target,
        strip_heredocs,
    )
except Exception:  # missing sibling module: the Bash branch degrades to no-op
    extract_write_targets = None
    heredoc_bodies_by_target = None
    strip_heredocs = None

try:
    # Quote-aware parse, so a pattern inside quotes stays one token and cannot be
    # mistaken for the command's own words. Same reason the module exists.
    from _bash_command_parse import command_segments
except Exception:  # degrade to warning about nothing, never about everything
    command_segments = None

# Code points, so this file holds no literal instances of what it forbids.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
H_BAR = chr(0x2015)
LSQUO, RSQUO, LDQUO, RDQUO = chr(0x2018), chr(0x2019), chr(0x201C), chr(0x201D)
# Box-drawing block U+2500..U+257F, built from code points so this source
# holds no literal box-drawing characters.
BOX_RANGE = "[" + chr(0x2500) + "-" + chr(0x257F) + "]"

# Banned AI vocabulary from CLAUDE.md, as (regex, label). Word-boundary,
# case-insensitive. Word families use a stem (e.g. enhanc\w* -> enhance,
# enhancing, enhanced, enhancement). "landscape" is deliberately omitted:
# only its abstract sense is barred and the literal sense is too common to
# flag without noise.
WORD_PATTERNS = [
    (r"\bcrucial\b", "crucial"),
    (r"\bpivotal\b", "pivotal"),
    (r"\bdelv\w*\b", "delve"),
    (r"\bfoster(?:s|ed|ing)?\b", "foster"),
    (r"\bgarner\w*\b", "garner"),
    (r"\bintricate\b", "intricate"),
    (r"\bmeticulous\w*\b", "meticulous"),
    (r"\bshowcas\w*\b", "showcase"),
    (r"\btapestry\b", "tapestry"),
    (r"\btestament\b", "testament"),
    (r"\bunderscor\w*\b", "underscore"),
    (r"\bvibrant\b", "vibrant"),
    (r"\bbolster\w*\b", "bolstered"),
    (r"\binterplay\b", "interplay"),
    (r"\benduring\b", "enduring"),
    (r"\benhanc\w*\b", "enhance/enhancing"),
    (r"\bboasts?\b", "boasts"),
    (r"\bnestled\b", "nestled"),
    (r"\bgroundbreaking\b", "groundbreaking"),
    (r"\brenowned\b", "renowned"),
    (r"\bexemplif\w*\b", "exemplifies"),
]

# Banned constructions / phrases (case-insensitive unless noted).
PHRASE_PATTERNS = [
    (r"\bserves as\b", "'serves as' (use is/are)"),
    (r"\bstands as\b", "'stands as' (use is/are)"),
    (r"\bdiverse array\b", "'diverse array'"),
    (r"\bvaluable insights\b", "'valuable insights'"),
    (r"\bsetting the stage\b", "'setting the stage'"),
    (r"\bindelible mark\b", "'indelible mark'"),
    (r"\bcommitment to\b", "'commitment to'"),
    (r"\bnot just\b[^.\n]*\bbut\b", "'not just X but Y' construction"),
    (r"\b(?:key|pivotal|vital|crucial)\s+role\b", "significance inflation ('key/vital role')"),
]

PROSE_EXT = re.compile(r"\.(?:md|markdown|mdx|txt|rst|drawio)$", re.I)
PROSE_NAME = re.compile(r"(?:README|CHANGELOG|CONTRIBUTING|COMMENTS|NOTICE)", re.I)
# Code files: their text is comments and string literals, i.e. internal engineering
# notes, so the style checks do not apply. Markup with user-facing text
# (.html/.css/.vue/.svelte) is NOT here;
# it stays checked. Only the tag-leak BUG check runs on code files.
CODE_EXT = re.compile(
    r"\.(?:py|pyi|js|mjs|cjs|jsx|ts|tsx|go|rs|java|kt|kts|c|h|cc|cpp|hpp|cs|rb|"
    r"php|swift|scala|lua|pl|pm|sql|sh|bash|zsh|fish|toml|ini|cfg)$", re.I)

# Built by concatenation so this source never contains a standalone tag line.
TAG_LEAK = re.compile(r"(?m)^\s*<" + r"/(?:content|invoke)>\s*$")

_CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")


def is_internal_file(path):
    """The internal-files exemption (see module docstring). Location-scoped
    plus repo CLAUDE.md; TODO.md inside code repos stays checked."""
    if not path:
        return False
    p = os.path.abspath(os.path.expanduser(path))
    base = os.path.basename(p)
    if base == "CLAUDE.md":
        return True
    if base.startswith("CLAUDE_DECISIONS") and base.endswith(".md"):
        return True
    if p.startswith(os.path.join(_CLAUDE_DIR, "plans") + os.sep):
        return True
    projects = os.path.join(_CLAUDE_DIR, "projects") + os.sep
    if p.startswith(projects):
        if base in ("TODO.md", "MEMORY.md"):
            return True
        rel_dirs = p[len(projects):].split(os.sep)[:-1]
        if "memory" in rel_dirs:
            return True
    return False


def collect_new_text(tool_input):
    parts = []
    if isinstance(tool_input.get("content"), str):        # Write
        parts.append(tool_input["content"])
    if isinstance(tool_input.get("new_string"), str):     # Edit
        parts.append(tool_input["new_string"])
    for e in tool_input.get("edits") or []:               # MultiEdit
        if isinstance(e, dict) and isinstance(e.get("new_string"), str):
            parts.append(e["new_string"])
    return "\n".join(parts)


def scan(text, file_path):
    findings = []

    # A leaked tool-call tag is a BUG (the batch-Write leak), not a style signal, so
    # it is checked on EVERY file, code included.
    if TAG_LEAK.search(text):
        findings.append(
            "leaked tool-call tag: a standalone closing content/invoke tag line "
            "(the batch-Write leak; delete the stray line, and check the tails "
            "of any sibling files written in the same batch)")

    # Code files hold comments and string literals, i.e. internal engineering notes,
    # so the style checks below do not apply. User-facing markup
    # (.html/.css/.vue/.svelte) is not CODE_EXT, so it
    # stays fully checked.
    if file_path and CODE_EXT.search(file_path):
        return findings

    # Tier 1: typography, prose and user-facing markup.
    if EM_DASH in text:
        findings.append("em dash (" + EM_DASH + "): use a comma, colon, period, or parentheses")
    if EN_DASH in text:
        findings.append("en dash (" + EN_DASH + "): use a plain hyphen or reword")
    if H_BAR in text:
        findings.append("horizontal bar (" + H_BAR + "): remove")
    smart = [c for c in (LSQUO, RSQUO, LDQUO, RDQUO) if c in text]
    if smart:
        findings.append("curly/smart quotes (" + "".join(smart) + "): use straight ' and \"")
    if re.search(BOX_RANGE, text):
        findings.append("box-drawing characters: use a markdown table or a bulleted list instead")

    # Tier 2: fuzzy word/phrase list, prose-ish files only.
    is_prose = (not file_path) or bool(PROSE_EXT.search(file_path)) or bool(PROSE_NAME.search(file_path))
    if is_prose:
        words = sorted({label for pat, label in WORD_PATTERNS if re.search(pat, text, re.I)})
        if words:
            findings.append("AI-typical words: " + ", ".join(words))
        phrases = [label for pat, label in PHRASE_PATTERNS if re.search(pat, text, re.I)]
        if phrases:
            findings.append("AI constructions: " + ", ".join(phrases))
        if re.search(r"(?m)^\s*Additionally\b", text):
            findings.append("sentence-start 'Additionally'")

    return findings


INLINE_WRITERS = ("echo", "printf")
ECHO_FLAG = re.compile(r"^-[neE]+$")
REDIRECTS = (">", ">>")


def inline_payload(command):
    """The text a non-heredoc shell write actually puts INTO a file.

    A command's TEXT is not its OUTPUT. `cat a.md > b.md`, `grep -nE PATTERN a.md >
    b.md` and `python gen.py > b.md` all write bytes that appear nowhere in the command,
    so scanning the command text would blame the new file for words it does
    not contain.

    Only echo, printf and a here-string carry their payload inline, which is exactly what
    the fallback was written for. Everything else contributes nothing, because there is
    nothing of it to read. Decide from a PARSE, never from a substring: the same lesson
    _bash_command_parse.py exists for, one guard later.

    Returns "" when the command cannot be parsed, so an unreadable command warns about
    nothing rather than about everything.
    """
    if command_segments is None:
        return ""
    try:
        segments = command_segments(command)
    except Exception:                                    # noqa: BLE001
        return ""
    out = []
    for tokens in segments:
        # a here-string writes its operand verbatim
        for i, tok in enumerate(tokens):
            if tok == "<<<" and i + 1 < len(tokens):
                out.append(tokens[i + 1])
        # trim the redirect and its target off the end of the segment
        args = []
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok in REDIRECTS:
                skip_next = True
                continue
            args.append(tok)
        if not args:
            continue
        name = os.path.basename(args[0])
        if name not in INLINE_WRITERS:
            continue
        for tok in args[1:]:
            if ECHO_FLAG.match(tok):
                continue
            out.append(tok)
    return "\n".join(out)


def scan_bash(data, tool_input):
    """The ^Bash$ branch: scan heredoc bodies (or the command text) only for
    write targets that pass the file gates and the internal-files exemption."""
    command = tool_input.get("command") or ""
    if (">" not in command and "tee" not in command) or extract_write_targets is None:
        return "", []
    targets = extract_write_targets(command, data.get("cwd"))
    live = [t for t in targets if not is_internal_file(t)]
    if not live:
        return "", []
    stripped, _bodies = strip_heredocs(command)
    # Attribute each heredoc body to the file ITS OWN line redirects to. One
    # Bash call routinely carries a second heredoc that is a verifier script,
    # and the document must not be blamed for the verifier's own text.
    attributed = {}
    if heredoc_bodies_by_target is not None:
        for target, body in heredoc_bodies_by_target(command, data.get("cwd")):
            if target and body.strip():
                attributed.setdefault(target, []).append(body)
    findings = []
    hits = []
    for t in live:
        parts = attributed.get(t)
        if not parts:
            # No heredoc wrote this file, so any payload is an echo/printf or a
            # here-string in the command itself. Take ONLY those operands, never
            # the command text: a grep pattern, a filename or a flag is not
            # content this file received. See inline_payload.
            parts = [inline_payload(stripped)]
        got = scan("\n".join(parts), t)
        if got:
            hits.append(t)
            for f in got:
                if f not in findings:
                    findings.append(f)
    return ", ".join(hits or live), findings


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    if (data.get("tool_name") or "") == "Bash":
        file_path, findings = scan_bash(data, tool_input)
        if not findings:
            sys.exit(0)
    else:
        file_path = tool_input.get("file_path") or ""
        if is_internal_file(file_path):
            sys.exit(0)  # internal bookkeeping between the user and Claude
        text = collect_new_text(tool_input)
        if not text.strip():
            sys.exit(0)
        findings = scan(text, file_path)
        if not findings:
            sys.exit(0)

    where = file_path or "this file"
    msg = (
        "AI-WRITING-SIGNAL CHECK on " + where + ": the text just written carries "
        "signs of AI-written text: "
        + "; ".join(findings) + ". Fix these before treating the text as final. "
        "This is a non-blocking heuristic warning; if a hit is a genuine false "
        "positive (a banned word inside code, a proper name, a real numeric range), "
        "note that and move on."
    )

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
