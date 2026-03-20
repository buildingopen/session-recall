# session-recall

Claude Code forgets everything after context compaction. This gets it back.

```bash
npx session-recall "the thing you lost"
```

## The problem

When Claude Code compacts context, you get a vague summary. Details vanish: decisions, error solutions, specific commands that worked, corrections you gave. You're left grepping through JSON soup in `~/.claude/projects/`.

session-recall parses those JSONL transcripts properly. It extracts human-readable messages, tool calls, errors, and patterns, so you find what you need in seconds.

## What you get

**Search** any past session by keyword:

```
$ session-recall "deploy" "vercel"

Session: 953afe03-f9fd-4ef1-8235-eb84b34093c5
Size: 7564KB
---
Found 12 matches (showing last 5):

[assistant] (line 4820)
...deployed to Vercel with --token flag. Production URL: https://app.example.com
Build time: 34s, no errors. The CORS issue was caused by missing...
---
```

**Analyze** your sessions for patterns with `--report`:

```
$ session-recall --report

Messages: 26 user, 181 assistant | Tools: 299 | Errors: 8 | Compactions: 2

TOP ISSUES
  1. [RETRY] Bash x3 (lines 1837-1929)
  2. [INFLATED SCORE] 10/10 at line 1346, user found issues after
  3. [CORRECTION] line 1384: "the summary is not enough..."

TOOL EFFICIENCY
  Tool                  Calls  Errors  Success%
  Bash                    169       8       95%
  Read                     43       0      100%
  Edit                     39       0      100%

SUGGESTED CLAUDE.MD RULES
  - After 2 failures with Bash, switch approach. Do not retry a third time.
  - List flaws BEFORE scoring. User found issues after self-scores of 10/10.
  - Session had multiple compactions. Use workplan files as external brain.
```

**Cross-session analysis** with `--all` finds recurring problems:

```
$ session-recall --all 10

CROSS-SESSION SUMMARY (10 sessions)
  Total tool calls: 4351
  Total errors: 165

RECURRING RETRY PATTERNS
  Bash: retried in 7/10 sessions (avg 4.2x when it happens)

SELF-SCORING ACCURACY
  185 scores across 10 sessions (avg 7.7/10)
  20/185 (11%) had user issues after

RECURRING ERROR TYPES
  COMMAND_FAILED: in 8/10 sessions
  FILE_NOT_FOUND: in 6/10 sessions
```

**Deep analysis** via Gemini (`--deep`) gives project-specific rules instead of generic advice:

```
$ session-recall --report --deep

DEEP ANALYSIS (via Gemini)

1. PROJECT CONTEXT: Building a video generation pipeline with Remotion.
   Agent repeatedly failed on TTS API rate limits.

2. CLAUDE.MD RULES:
   - When ElevenLabs returns 429, wait 30s before retry. Agent wasted 20 calls.
   - Always check ffmpeg output file exists before proceeding to next render step.
   - User wants narration synced with visual transitions, not just content-correct.

3. BIGGEST TIME WASTER: 47 minutes retrying a Bash command that was blocked
   by a pre-commit hook. Switch approach after first hook rejection.
```

## All commands

```bash
# Search
session-recall "keyword"              # Find keyword in current session
session-recall "error" "deploy"       # AND search (both must match)
session-recall --recent 10            # Last 10 messages (no tool noise)
session-recall --decisions            # Find decision points
session-recall --tools "Edit"         # Search tool calls only
session-recall --list                 # List all sessions

# Pin a session (auto-namespaced per Claude process)
session-recall --pin-by "project-x"   # Pin session containing keyword
session-recall --unpin                # Remove pin

# Analyze
session-recall --report               # Errors, retries, corrections, rules
session-recall --report --deep        # + Gemini project-specific insights
session-recall --all                  # Cross-session patterns (last 10)
session-recall --all 20 --deep        # Cross-session + Gemini
```

## Setup

```bash
npx session-recall --help
```

For deep analysis, add a Gemini key:

```bash
mkdir -p ~/.config/session-recall
echo "your-gemini-key" > ~/.config/session-recall/gemini-key
chmod 600 ~/.config/session-recall/gemini-key
```

Or set `GEMINI_API_KEY` as an env var.

## Part of Building Open

Open-source tools for Claude Code power users:

- [**session-recall**](https://github.com/buildingopen/session-recall) - Recover context after compaction
- [**claude-wrapped**](https://github.com/buildingopen/claude-wrapped) - Your Claude Code year in review
- [**bouncer**](https://github.com/buildingopen/bouncer) - AI quality audit for any work
- [**blast-radius**](https://github.com/buildingopen/blast-radius) - Impact analysis before code changes

## License

MIT
