# session-recall

Search and analyze Claude Code session transcripts to recover context lost during compaction.

## Install

```bash
npx session-recall --help
```

Or install globally:

```bash
npm install -g session-recall
```

Requires Python 3.8+.

## Usage

### Search transcripts

```bash
session-recall "keyword"              # Search current session for keyword
session-recall "error" "deploy"       # AND search (both must match)
session-recall --recent 10            # Last 10 human/assistant messages
session-recall --decisions            # Find decision points
session-recall --tools "Edit"         # Search tool calls only
session-recall --list                 # List all sessions
```

### Pin a session

```bash
session-recall --pin-by "project-x"   # Pin session containing "project-x"
session-recall "keyword"              # Now searches the pinned session
session-recall --unpin                # Remove pin
```

Pins are auto-namespaced per Claude Code process, so different terminal sessions don't interfere.

### Analyze sessions

```bash
session-recall --report               # Single session: errors, retries, corrections
session-recall --report --deep        # + Gemini-powered project-specific insights
session-recall --all                  # Cross-session analysis (last 10 sessions)
session-recall --all 20 --deep        # Cross-session with Gemini deep analysis
```

The report shows:
- Retry loops (same tool called 3+ times in proximity)
- Error categorization (FILE_NOT_FOUND, EDIT_FAILED, HOOK_BLOCKED, etc.)
- User corrections and pushback
- Self-scoring accuracy (X/10 claims vs reality)
- Compaction events
- Suggested CLAUDE.md rules and MEMORY.md entries

### Deep analysis (Gemini)

`--deep` sends structured session data to Gemini for project-specific insights. Set up:

```bash
# Option 1: environment variable
export GEMINI_API_KEY="your-key"

# Option 2: config file
mkdir -p ~/.config/session-recall
echo "your-key" > ~/.config/session-recall/gemini-key
chmod 600 ~/.config/session-recall/gemini-key
```

## How it works

Claude Code stores session transcripts as JSONL files in `~/.claude/projects/`. Each line is a JSON object with `type` (user/assistant), role, and content (text, tool_use, tool_result blocks).

session-recall parses these files with JSONL-aware logic, extracting human-readable text, tool calls, errors, and patterns. This is faster and more precise than raw `grep` on JSON.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Colon-separated list of project directories |
| `GEMINI_API_KEY` | - | Gemini API key for `--deep` analysis |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model to use |
| `SESSION_RECALL_NS` | auto (Claude PID) | Pin namespace override |

## Tests

```bash
python3 test_session_recall.py
```

75 tests covering search, recent, decisions, tools, pin system, report analysis, and cross-session aggregation.

## License

MIT
