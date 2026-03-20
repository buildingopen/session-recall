#!/usr/bin/env python3
"""session-recall: Search Claude Code session transcripts to recover context after compaction."""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def find_project_dirs():
    """Find all Claude Code project directories."""
    env_val = os.environ.get("CLAUDE_PROJECTS_DIR", "")
    if env_val:
        dirs = [Path(p).expanduser() for p in env_val.split(":") if p.strip()]
    else:
        dirs = [Path.home() / ".claude" / "projects"]
    return [d for d in dirs if d.is_dir()]


def find_all_sessions(project_dirs):
    """Find all .jsonl session files across project dirs, sorted by mtime desc."""
    sessions = []
    for d in project_dirs:
        for p in d.rglob("*.jsonl"):
            try:
                stat = p.stat()
                sessions.append((stat.st_mtime, stat.st_size, p))
            except OSError:
                continue
    sessions.sort(key=lambda x: x[0], reverse=True)
    return sessions


def find_current_session(project_dirs):
    """Find the most recently modified .jsonl file."""
    sessions = find_all_sessions(project_dirs)
    if not sessions:
        return None
    return sessions[0][2]


def session_id_from_path(path):
    """Extract session ID (filename without extension) from path."""
    return path.stem


def format_size(size_bytes):
    """Format file size in KB."""
    kb = max(1, size_bytes // 1024)
    return f"{kb}KB"


def has_human_text(content):
    """Check if a content field has actual human/assistant text (not just tool blocks)."""
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text", "").strip():
                    return True
            elif isinstance(block, str) and block.strip():
                return True
    return False


def extract_human_text(content):
    """Extract only human-readable text blocks (no tool_use/tool_result noise)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def extract_text_from_content(content):
    """Extract readable text from a message content field (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    inp_str = json.dumps(inp, ensure_ascii=False)
                    if len(inp_str) > 300:
                        inp_str = inp_str[:300] + "..."
                    parts.append(f"[tool_use] {name}: {inp_str}")
                elif btype == "tool_result":
                    tc = block.get("content", "")
                    text = extract_text_from_content(tc)
                    if len(text) > 300:
                        text = text[:300] + "..."
                    is_err = block.get("is_error", False)
                    prefix = "[tool_result ERROR]" if is_err else "[tool_result]"
                    parts.append(f"{prefix} {text}")
                elif btype == "thinking":
                    pass  # skip thinking blocks
        return "\n".join(parts)
    return str(content)


def extract_tool_blocks(content):
    """Extract tool_use and tool_result blocks from content."""
    blocks = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    inp_str = json.dumps(inp, ensure_ascii=False)
                    if len(inp_str) > 300:
                        inp_str = inp_str[:300] + "..."
                    blocks.append(("tool_use", f"{name}: {inp_str}"))
                elif btype == "tool_result":
                    tc = block.get("content", "")
                    text = extract_text_from_content(tc)
                    if len(text) > 300:
                        text = text[:300] + "..."
                    is_err = block.get("is_error", False)
                    label = "tool_result ERROR" if is_err else "tool_result"
                    blocks.append((label, text))
    return blocks


def truncate_around(text, pos, before=250, after=350):
    """Return a substring centered around pos with given context."""
    start = max(0, pos - before)
    end = min(len(text), pos + after)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end] + suffix


def print_session_header(session_path):
    """Print standard session header with pin status."""
    sid = session_id_from_path(session_path)
    size = format_size(session_path.stat().st_size)
    pinned = get_pinned_session()
    pin_info = " (pinned)" if pinned and pinned == session_path else ""
    print(f"Session: {sid}{pin_info}")
    print(f"Size: {size}")
    print("---")


def cmd_search(session_path, keywords, max_results=15, tools_only=False):
    """Search session for keyword matches."""
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    matches = []

    with open(session_path, "r", errors="replace") as f:
        for line_num, raw_line in enumerate(f):
            try:
                obj = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue

            entry_type = obj.get("type", "")
            if entry_type not in ("user", "assistant"):
                continue

            msg = obj.get("message", {})
            role = msg.get("role", entry_type)
            content = msg.get("content", "")

            if tools_only:
                tool_blocks = extract_tool_blocks(content)
                if not tool_blocks:
                    continue
                for label, text in tool_blocks:
                    full_text = f"[{label}] {text}"
                    if all(p.search(full_text) for p in patterns):
                        matches.append((line_num, label, full_text))
            else:
                text = extract_text_from_content(content)
                if not text:
                    continue
                if all(p.search(text) for p in patterns):
                    m = patterns[0].search(text)
                    pos = m.start() if m else 0
                    snippet = truncate_around(text, pos)
                    matches.append((line_num, role, snippet))

    if not matches:
        print(f"No matches for: {' '.join(keywords)}", file=sys.stderr)
        return 1

    show = matches[-max_results:]
    print_session_header(session_path)
    print(f"Found {len(matches)} matches (showing last {len(show)}):\n")

    for line_num, role, snippet in show:
        print(f"[{role}] (line {line_num})")
        print(snippet)
        print("---")

    return 0


def cmd_recent(session_path, count=20):
    """Show last N messages with actual human/assistant text (skips tool-only messages)."""
    entries = []

    with open(session_path, "r", errors="replace") as f:
        for raw_line in f:
            try:
                obj = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue

            entry_type = obj.get("type", "")
            if entry_type not in ("user", "assistant"):
                continue

            msg = obj.get("message", {})
            role = msg.get("role", entry_type)
            content = msg.get("content", "")

            if not has_human_text(content):
                continue

            text = extract_human_text(content)
            if not text or not text.strip():
                continue

            entries.append((role, text))
            if len(entries) > count * 3:
                entries = entries[-(count * 2):]

    show = entries[-count:]

    print_session_header(session_path)

    if not show:
        print("No human/assistant messages found.")
        return 1

    for role, text in show:
        display = text.strip()
        if len(display) > 400:
            display = display[:400] + "..."
        print(f"[{role}] {display}\n")

    return 0


def cmd_list(project_dirs, count=20):
    """List recent session files."""
    sessions = find_all_sessions(project_dirs)
    if not sessions:
        print("No session files found.", file=sys.stderr)
        return 1

    pinned = get_pinned_session()
    for mtime, fsize, path in sessions[:count]:
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        sz = format_size(fsize).rjust(8)
        marker = " *" if pinned and path == pinned else ""
        print(f"{ts}  {sz}  {path.name}{marker}")

    if pinned:
        print(f"\n* = pinned session")

    return 0


def cmd_decisions(session_path, max_results=15):
    """Find decision points in the session."""
    decision_keywords = [
        "decided", "chose", "going with", "approach:", "plan:",
        "will use", "decision:", "agreed", "settled on", "picked",
        "recommendation:", "let's go with", "the plan is"
    ]
    pattern = re.compile("|".join(re.escape(kw) for kw in decision_keywords), re.IGNORECASE)

    matches = []

    with open(session_path, "r", errors="replace") as f:
        for line_num, raw_line in enumerate(f):
            try:
                obj = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue

            entry_type = obj.get("type", "")
            if entry_type not in ("user", "assistant"):
                continue

            msg = obj.get("message", {})
            role = msg.get("role", entry_type)
            content = msg.get("content", "")
            text = extract_text_from_content(content)

            if not text:
                continue

            m = pattern.search(text)
            if m:
                snippet = truncate_around(text, m.start())
                matches.append((line_num, role, snippet))

    if not matches:
        print("No decision points found.", file=sys.stderr)
        return 1

    show = matches[-max_results:]
    print_session_header(session_path)
    print(f"Found {len(matches)} decision points (showing last {len(show)}):\n")

    for line_num, role, snippet in show:
        print(f"[{role}] (line {line_num})")
        print(snippet)
        print("---")

    return 0


# --- Session Report/Analysis ---

CORRECTION_PATTERNS = [
    re.compile(r"\bno[,.]?\s+(?:that|this|it|you|don't|we|i)", re.I),
    re.compile(r"\bthat'?s (?:not|wrong|incorrect)", re.I),
    re.compile(r"\bactually[,]\s", re.I),
    re.compile(r"\bi (?:said|asked|meant|wanted)\b", re.I),
    re.compile(r"\byou should(?:n'?t| not| have)\b", re.I),
    re.compile(r"\bwhy did you\b", re.I),
    re.compile(r"\bi already (?:told|said|asked)\b", re.I),
    re.compile(r"\bnot what i (?:asked|wanted|meant)\b", re.I),
    re.compile(r"\bincorrect\b", re.I),
    re.compile(r"\bdon'?t (?:do|use|add|change|remove|delete|create)\b", re.I),
    re.compile(r"\bstop (?:doing|adding|changing|deleting|creating)\b", re.I),
    re.compile(r"\bwrong (?:file|path|approach|way|method|port|url|name)\b", re.I),
    # Soft correction patterns: user steering, not just pushback
    re.compile(r"\blet'?s (?:use|try|go with|switch to)\b", re.I),
    re.compile(r"\bi(?:'d)? prefer\b", re.I),
    re.compile(r"\bcan we (?:do|use|try|go with)\b", re.I),
    re.compile(r"\bactually,? let'?s\b", re.I),
    re.compile(r"\bi'?d rather\b", re.I),
]

SCORE_PATTERN = re.compile(r'\b(\d{1,2})\s*/\s*10\b')


def parse_entries(session_path):
    """Parse all entries into structured dicts for analysis."""
    entries = []
    with open(session_path, "r", errors="replace") as f:
        for line_num, raw_line in enumerate(f):
            try:
                obj = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue

            entry_type = obj.get("type", "")
            if entry_type not in ("user", "assistant"):
                continue

            msg = obj.get("message", {})
            role = msg.get("role", entry_type)
            content = msg.get("content", "")

            entry = {
                "line": line_num,
                "type": entry_type,
                "role": role,
                "text": extract_human_text(content),
                "full_text": extract_text_from_content(content),
                "tool_uses": [],
                "tool_results": [],
            }

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "tool_use":
                        entry["tool_uses"].append({
                            "name": block.get("name", "?"),
                            "input": block.get("input", {}),
                        })
                    elif btype == "tool_result":
                        rc = block.get("content", "")
                        rc_text = extract_text_from_content(rc)
                        if len(rc_text) > 500:
                            rc_text = rc_text[:500]
                        entry["tool_results"].append({
                            "content": rc_text,
                            "is_error": block.get("is_error", False),
                        })

            entries.append(entry)
    return entries


def categorize_error(text):
    """Categorize an error message into a known type."""
    t = text.lower()
    if "not found" in t or "no such file" in t or "does not exist" in t:
        return "FILE_NOT_FOUND"
    if "permission denied" in t or "access denied" in t:
        return "PERMISSION_DENIED"
    if ("edit" in t or "old_string" in t) and ("unique" in t or "not found in" in t or "no match" in t):
        return "EDIT_FAILED"
    if "hook" in t and ("block" in t or "reject" in t):
        return "HOOK_BLOCKED"
    if "timeout" in t or "timed out" in t:
        return "TIMEOUT"
    if "connection refused" in t or "econnrefused" in t or "econnreset" in t:
        return "NETWORK_ERROR"
    if "syntax" in t or "parse error" in t or "unexpected token" in t or "syntaxerror" in t:
        return "SYNTAX_ERROR"
    if "too large" in t or "file_too_large" in t:
        return "FILE_TOO_LARGE"
    if "command failed" in t or "exit code" in t or "non-zero" in t or "exited with" in t:
        return "COMMAND_FAILED"
    if "import" in t and ("module" in t or "no module" in t or "cannot find" in t):
        return "IMPORT_ERROR"
    if "already exists" in t or "duplicate" in t:
        return "ALREADY_EXISTS"
    if "no space" in t or "disk full" in t or "quota" in t:
        return "DISK_FULL"
    if "killed" in t or "oom" in t or "out of memory" in t:
        return "OOM"
    return "OTHER"


def rpt_errors(entries):
    """Analyze errors from tool_result blocks."""
    errors = []
    for entry in entries:
        for tr in entry["tool_results"]:
            if tr["is_error"]:
                cat = categorize_error(tr["content"])
                errors.append({"line": entry["line"], "category": cat, "text": tr["content"][:200]})
    by_cat = Counter(e["category"] for e in errors)
    return {"total": len(errors), "by_category": dict(by_cat), "details": errors}


def rpt_retries(entries):
    """Find retry loops: same tool+similar input called multiple times in proximity."""
    calls = []
    for entry in entries:
        for tu in entry["tool_uses"]:
            inp_str = json.dumps(tu["input"], ensure_ascii=False)
            fingerprint = f"{tu['name']}:{inp_str[:150]}"
            calls.append({"name": tu["name"], "input": inp_str[:200], "fp": fingerprint, "line": entry["line"]})

    # Group by fingerprint
    groups = defaultdict(list)
    for c in calls:
        groups[c["fp"]].append(c)

    retries = []
    for fp, occs in groups.items():
        if len(occs) < 3:
            continue
        # Check proximity: at least 3 occurrences within 80 lines of each other
        cluster = [occs[0]]
        for occ in occs[1:]:
            if occ["line"] - cluster[-1]["line"] < 80:
                cluster.append(occ)
            else:
                if len(cluster) >= 3:
                    retries.append({
                        "tool": cluster[0]["name"],
                        "input_preview": cluster[0]["input"][:100],
                        "count": len(cluster),
                        "first_line": cluster[0]["line"],
                        "last_line": cluster[-1]["line"],
                    })
                cluster = [occ]
        if len(cluster) >= 3:
            retries.append({
                "tool": cluster[0]["name"],
                "input_preview": cluster[0]["input"][:100],
                "count": len(cluster),
                "first_line": cluster[0]["line"],
                "last_line": cluster[-1]["line"],
            })

    return sorted(retries, key=lambda x: x["count"], reverse=True)


def rpt_corrections(entries):
    """Find user messages that are corrections or pushback."""
    corrections = []
    for entry in entries:
        if entry["role"] != "user":
            continue
        text = entry["text"]
        if not text or len(text.strip()) < 3:
            continue
        # Skip non-human content (compaction summaries, skill prompts, system reminders)
        text_lower = text.lower()
        if "continued from a previous conversation" in text_lower:
            continue
        if "base directory for this skill" in text_lower:
            continue
        if "<system-reminder>" in text_lower or "system-reminder" in text_lower:
            continue
        # Skip tool-result-only messages
        if not has_human_text(entry.get("_raw_content", text)):
            continue
        for pat in CORRECTION_PATTERNS:
            if pat.search(text):
                corrections.append({"line": entry["line"], "text": text[:200].strip()})
                break
    return corrections


def rpt_scores(entries):
    """Find self-scoring (X/10) and check if user found issues after."""
    scores = []
    for i, entry in enumerate(entries):
        if entry["role"] != "assistant":
            continue
        text = entry["full_text"]
        if not text:
            continue
        for m in SCORE_PATTERN.finditer(text):
            score = int(m.group(1))
            if score > 10:
                continue
            # Check next few user messages for correction signals
            correction_after = False
            for j in range(i + 1, min(i + 6, len(entries))):
                if entries[j]["role"] == "user":
                    ut = entries[j]["text"].lower()
                    if any(w in ut for w in ["wrong", "bug", "broken", "not working", "incorrect", "fix", "fail"]):
                        correction_after = True
                        break
            scores.append({
                "line": entry["line"],
                "score": score,
                "correction_after": correction_after,
                "context": truncate_around(text, m.start(), before=50, after=100),
            })
    return scores


def rpt_tool_usage(entries):
    """Compute tool call counts and error rates per tool."""
    calls = Counter()
    errs = Counter()
    for entry in entries:
        for tu in entry["tool_uses"]:
            calls[tu["name"]] += 1
    # Match errors to their preceding tool_use
    for i, entry in enumerate(entries):
        if entry["type"] != "user":
            continue
        for tr in entry["tool_results"]:
            if tr["is_error"]:
                for j in range(i - 1, max(i - 5, -1), -1):
                    if entries[j]["type"] == "assistant" and entries[j]["tool_uses"]:
                        errs[entries[j]["tool_uses"][-1]["name"]] += 1
                        break
    return {"calls": dict(calls), "errors": dict(errs)}


def rpt_compactions(entries):
    """Find context compaction events in the session."""
    events = []
    for entry in entries:
        if entry["role"] != "user":
            continue
        text = entry["text"]
        if text and "continued from a previous conversation" in text.lower():
            events.append({"line": entry["line"]})
    return events


def generate_lessons(retries, errors, corrections, scores, compactions):
    """Generate actionable CLAUDE.md rules and MEMORY.md entries from analysis.

    Rules include specific context from the session (tool names, error text,
    line numbers) rather than generic advice.
    """
    rules = []  # CLAUDE.md-style rules
    memories = []  # MEMORY.md-style facts

    # From retries: include the actual command/input that was retried
    retry_tools = set()
    for r in retries:
        tool = r["tool"]
        if tool not in retry_tools:
            retry_tools.add(tool)
            preview = r.get("input_preview", "")[:80]
            line_info = f"lines {r['first_line']}-{r['last_line']}"
            rules.append(
                f"{tool} command retried {r['count']}x ({line_info}): `{preview}`. "
                f"After 2 failures with {tool}, switch approach instead of retrying."
            )

    # From errors: include specific error examples
    cats = errors.get("by_category", {})
    error_details = errors.get("details", [])
    if cats.get("FILE_NOT_FOUND", 0) >= 2:
        examples = [e["text"][:80] for e in error_details if e["category"] == "FILE_NOT_FOUND"][:2]
        rules.append("Use Glob to verify file paths before reading. FILE_NOT_FOUND errors ({} total): {}".format(
            cats["FILE_NOT_FOUND"], "; ".join(examples) if examples else "multiple paths missing"))
    if cats.get("EDIT_FAILED", 0) >= 2:
        rules.append("Re-read files immediately before editing. {} EDIT_FAILED errors in this session.".format(cats["EDIT_FAILED"]))
    if cats.get("HOOK_BLOCKED", 0) >= 1:
        examples = [e["text"][:80] for e in error_details if e["category"] == "HOOK_BLOCKED"][:1]
        rules.append("Hook blocks are deterministic. Blocked: {}. Never retry, use a different approach.".format(
            examples[0] if examples else "command blocked by hook"))
    if cats.get("FILE_TOO_LARGE", 0) >= 1:
        rules.append("Use offset/limit or Grep for large files. {} FILE_TOO_LARGE errors.".format(cats["FILE_TOO_LARGE"]))
    if cats.get("TIMEOUT", 0) >= 1:
        rules.append("{} TIMEOUT errors. Use timeout for long-running commands, investigate root cause.".format(cats["TIMEOUT"]))

    # From corrections: include the actual correction text
    if len(corrections) >= 3:
        correction_summaries = [c["text"][:60] for c in corrections[:3]]
        rules.append("Multiple user corrections ({}): {}. Read instructions more carefully before acting.".format(
            len(corrections), "; ".join(correction_summaries)))
    if len(corrections) >= 1:
        for c in corrections[:3]:
            text = c["text"][:150]
            memories.append(f"User correction (line {c['line']}): {text}")

    # From scores: include the actual score context
    inflated = [s for s in scores if s["correction_after"]]
    if inflated:
        score_details = ", ".join(
            f"{s['score']}/10 at line {s['line']}" for s in inflated[:3]
        )
        rules.append(f"List flaws BEFORE scoring. User found issues after self-scores of {score_details}. Adversarial review before claiming done.")

    # From compactions
    if len(compactions) >= 2:
        rules.append("Session had {} compactions. Use workplan files as external brain. Re-read the plan after each compaction.".format(
            len(compactions)))

    return rules, memories


# --- Deep analysis via Gemini ---

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
_GEMINI_KEY_FILE = Path.home() / ".config" / "session-recall" / "gemini-key"


def _get_gemini_key():
    """Resolve Gemini API key: env var > config file > error."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    if _GEMINI_KEY_FILE.exists():
        key = _GEMINI_KEY_FILE.read_text().strip()
        if key:
            return key
    return ""


def build_deep_context(entries, retries, errors, corrections, scores, compactions):
    """Build a structured context string for Gemini analysis."""
    parts = []

    # Conversation flow: extract human/assistant text messages (no tool noise)
    parts.append("=== CONVERSATION FLOW (human/assistant messages only) ===")
    msg_count = 0
    for entry in entries:
        text = entry["text"].strip()
        if not text:
            continue
        role = entry["role"]
        # Truncate long messages but keep corrections in full
        is_correction = any(c["line"] == entry["line"] for c in corrections)
        max_len = 500 if is_correction else 250
        display = text[:max_len] + "..." if len(text) > max_len else text
        parts.append(f"[{role}] (line {entry['line']}): {display}")
        msg_count += 1
        if msg_count > 200:
            parts.append(f"... ({len(entries)} total entries, truncated)")
            break

    # Retry loops
    if retries:
        parts.append("\n=== RETRY LOOPS ===")
        for r in retries:
            parts.append(f"- {r['tool']} x{r['count']} (lines {r['first_line']}-{r['last_line']}): {r['input_preview'][:150]}")

    # Errors
    if errors["total"] > 0:
        parts.append(f"\n=== ERRORS ({errors['total']}) ===")
        for cat, count in sorted(errors["by_category"].items(), key=lambda x: x[1], reverse=True):
            parts.append(f"- {cat}: {count}")
        parts.append("Details:")
        for e in errors["details"][:10]:
            parts.append(f"  Line {e['line']} [{e['category']}]: {e['text'][:150]}")

    # User corrections (FULL text, these are the gold signal)
    if corrections:
        parts.append("\n=== USER CORRECTIONS (critical - these are the most important signals) ===")
        for c in corrections:
            parts.append(f"- Line {c['line']}: {c['text']}")

    # Self-scoring
    inflated = [s for s in scores if s["correction_after"]]
    if inflated:
        parts.append("\n=== INFLATED SELF-SCORES ===")
        for s in inflated:
            parts.append(f"- {s['score']}/10 at line {s['line']}: {s['context'][:200]}")

    # Compactions
    if compactions:
        parts.append(f"\n=== COMPACTION EVENTS ({len(compactions)}) ===")
        parts.append(f"Lines: {', '.join(str(e['line']) for e in compactions)}")

    return "\n".join(parts)


def call_gemini(prompt, context):
    """Call Gemini API and return the text response."""
    import urllib.request
    import urllib.error

    api_key = _get_gemini_key()
    if not api_key:
        return (f"No Gemini API key found. Set GEMINI_API_KEY env var or write key to {_GEMINI_KEY_FILE}")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    full_prompt = f"{prompt}\n\n---\n\nSESSION DATA:\n{context}"

    payload = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4000},
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return "No response from Gemini."
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        return f"Gemini API error {e.code}: {body}"
    except Exception as e:
        return f"Gemini API error: {e}"


DEEP_PROMPT = """You are analyzing a Claude Code session transcript to extract project-specific lessons.

Your job is to find SPECIFIC, ACTIONABLE insights about THIS project and THIS session. NOT generic advice.

BAD (generic): "Don't retry commands too many times"
GOOD (specific): "When ElevenLabs TTS API returns 429, wait 30s before retrying. The agent wasted 20 calls without backoff."

BAD (generic): "Read instructions carefully"
GOOD (specific): "User wants video narration synced with visual transitions. Agent ignored timing requirements and focused only on content."

Analyze the session data below and produce:

1. **PROJECT CONTEXT** (1-2 sentences): What was this session about? What project/task?

2. **CLAUDE.MD RULES** (3-7 rules): Specific behavioral rules for THIS project's CLAUDE.md.
   - Each rule must reference a concrete event from the session
   - Rules must be imperative ("Always X", "Never Y", "When Z happens, do W")
   - Rules must be specific enough that a different agent reading them would avoid the same mistake

3. **MEMORY ENTRIES** (2-5 entries): Facts to remember for future sessions on this project.
   - User preferences discovered in this session
   - Technical decisions made
   - Pitfalls encountered and their solutions
   - Things the user corrected

4. **BIGGEST TIME WASTER**: The single largest waste of time in this session and how to prevent it.

Output in plain text, no markdown headers, just numbered sections. Be concise and direct."""


DEEP_PROMPT_JSON = """You are analyzing a Claude Code session transcript to extract project-specific lessons.

Your job is to find SPECIFIC, ACTIONABLE insights about THIS project and THIS session. NOT generic advice.

BAD (generic): "Don't retry commands too many times"
GOOD (specific): "When ElevenLabs TTS API returns 429, wait 30s before retrying. The agent wasted 20 calls without backoff."

Analyze the session data below and return ONLY valid JSON with this structure:
{
  "project_context": "1-2 sentence description of what this session was about",
  "rules": [
    "Imperative rule 1 referencing a concrete event",
    "Imperative rule 2..."
  ],
  "memories": [
    "Fact or preference to remember 1",
    "Fact or preference to remember 2..."
  ],
  "biggest_time_waster": "Description of the biggest time waste and how to prevent it"
}

Rules (3-7): Must be imperative ("Always X", "Never Y"), specific to THIS project.
Memories (2-5): User preferences, technical decisions, pitfalls, corrections.

Return ONLY the JSON object, no markdown fences, no commentary."""


# --- --apply: HITL review + append to CLAUDE.md / MEMORY.md ---


def _find_claude_md():
    """Find CLAUDE.md in current directory or parents."""
    cwd = Path.cwd()
    for d in [cwd] + list(cwd.parents):
        candidate = d / "CLAUDE.md"
        if candidate.exists():
            return candidate
        # Stop at home or root
        if d == Path.home() or d == Path("/"):
            break
    # Default: create in cwd
    return cwd / "CLAUDE.md"


def _find_memory_md():
    """Find the auto-memory MEMORY.md for the current project."""
    # Claude Code stores per-project memory in ~/.claude/projects/<hash>/memory/MEMORY.md
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        return None
    # Walk up cwd path trying each parent: /root/session-recall -> try -root-session-recall, then -root
    cwd = Path.cwd()
    candidates = [cwd] + list(cwd.parents)
    existing_dirs = {d.name: d for d in projects_dir.iterdir() if d.is_dir()}
    for path in candidates:
        encoded = str(path).replace("/", "-")
        if encoded in existing_dirs:
            mem_dir = existing_dirs[encoded] / "memory"
            mem_dir.mkdir(exist_ok=True)
            return mem_dir / "MEMORY.md"
        # Stop at root
        if path == Path("/"):
            break
    # Fallback: most recently modified project dir
    project_dirs = sorted(projects_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in project_dirs:
        if d.is_dir() and not d.name.startswith("."):
            mem_dir = d / "memory"
            if mem_dir.exists():
                return mem_dir / "MEMORY.md"
    return None


def _hitl_review(items, item_type):
    """Interactive review of items. Returns list of approved items."""
    if not items:
        return []
    approved = []
    print(f"\n{'=' * 50}")
    print(f"REVIEW {item_type.upper()} ({len(items)} items)")
    print(f"{'=' * 50}")
    print(f"  [y] approve  [n] skip  [e] edit  [a] approve all  [q] quit\n")

    for i, item in enumerate(items):
        print(f"  ({i + 1}/{len(items)}) {item}")
        while True:
            try:
                choice = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return approved
            if choice in ("y", "yes", ""):
                approved.append(item)
                break
            elif choice in ("n", "no", "s", "skip"):
                break
            elif choice in ("e", "edit"):
                try:
                    edited = input("  new text> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return approved
                if edited:
                    approved.append(edited)
                break
            elif choice in ("a", "all"):
                approved.append(item)
                approved.extend(items[i + 1:])
                print(f"  Approved all remaining ({len(items) - i} items)")
                return approved
            elif choice in ("q", "quit"):
                return approved
            else:
                print("  [y/n/e/a/q]?")
        print()
    return approved


def _append_to_file(filepath, section_header, items):
    """Append items under a section header in a file.

    Tracks code fence state (``` toggles) so that section headers
    appearing inside fenced code blocks are not matched.
    """
    filepath = Path(filepath)
    existing = filepath.read_text() if filepath.exists() else ""

    # Check if section already exists (outside code fences)
    lines = existing.split("\n")
    in_fence = False
    section_line = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and stripped == section_header:
            section_line = i
            break

    if section_line is not None:
        # Append to existing section (before next ## outside fences or end of file)
        insert_idx = None
        fence_state = False
        for j in range(section_line + 1, len(lines)):
            stripped = lines[j].strip()
            if stripped.startswith("```"):
                fence_state = not fence_state
                continue
            if not fence_state and lines[j].startswith("## ") and stripped != section_header:
                insert_idx = j
                break
        if insert_idx is None:
            insert_idx = len(lines)
        new_lines = [f"- {item}" for item in items]
        lines = lines[:insert_idx] + new_lines + lines[insert_idx:]
        filepath.write_text("\n".join(lines))
    else:
        # Add new section at end
        separator = "\n" if existing and not existing.endswith("\n") else ""
        section = separator + "\n" + section_header + "\n" + "\n".join(f"- {item}" for item in items) + "\n"
        filepath.write_text(existing + section)


def cmd_apply(session_path, deep=False):
    """Generate rules, HITL review, append to CLAUDE.md / MEMORY.md."""
    entries = parse_entries(session_path)
    if not entries:
        print("No messages found in session.", file=sys.stderr)
        return 1

    retries = rpt_retries(entries)
    errors = rpt_errors(entries)
    corrections = rpt_corrections(entries)
    scores = rpt_scores(entries)
    compactions = rpt_compactions(entries)

    # Collect template-based rules and memories
    rules, memories = generate_lessons(retries, errors, corrections, scores, compactions)

    # Deep analysis adds more
    if deep:
        print("Running Gemini deep analysis...")
        context = build_deep_context(entries, retries, errors, corrections, scores, compactions)
        raw = call_gemini(DEEP_PROMPT_JSON, context)
        try:
            # Strip markdown fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()
            data = json.loads(cleaned)
            deep_rules = data.get("rules", [])
            deep_memories = data.get("memories", [])
            context_str = data.get("project_context", "")
            time_waster = data.get("biggest_time_waster", "")
            if context_str:
                print(f"\nProject: {context_str}")
            if time_waster:
                print(f"Biggest time waster: {time_waster}")
            # Deduplicate: only add deep rules that aren't too similar to template rules
            for dr in deep_rules:
                if not any(dr[:40].lower() in r.lower() for r in rules):
                    rules.append(dr)
            for dm in deep_memories:
                if not any(dm[:40].lower() in m.lower() for m in memories):
                    memories.append(dm)
        except (json.JSONDecodeError, KeyError, AttributeError):
            print(f"Could not parse Gemini response as JSON. Raw output:\n{raw[:500]}", file=sys.stderr)

    if not rules and not memories:
        print("No rules or memories to apply.")
        return 0

    # HITL review
    approved_rules = _hitl_review(rules, "CLAUDE.MD rules")
    approved_memories = _hitl_review(memories, "MEMORY.MD entries")

    if not approved_rules and not approved_memories:
        print("Nothing approved. No changes made.")
        return 0

    # Apply
    if approved_rules:
        claude_md = _find_claude_md()
        _append_to_file(claude_md, "## Session Recall Rules", approved_rules)
        print(f"Appended {len(approved_rules)} rules to {claude_md}")

    if approved_memories:
        memory_md = _find_memory_md()
        if memory_md:
            _append_to_file(memory_md, "## Session Recall Insights", approved_memories)
            print(f"Appended {len(approved_memories)} entries to {memory_md}")
        else:
            # Fallback: print for manual copy
            print("Could not locate MEMORY.md. Approved entries:")
            for m in approved_memories:
                print(f"  - {m}")

    return 0


def cmd_report(session_path, deep=False):
    """Generate session analysis report with improvement suggestions."""
    entries = parse_entries(session_path)
    if not entries:
        print("No messages found in session.", file=sys.stderr)
        return 1

    # Basic stats
    user_msgs = sum(1 for e in entries if e["role"] == "user" and e["text"].strip())
    asst_msgs = sum(1 for e in entries if e["role"] == "assistant" and e["text"].strip())
    total_tools = sum(len(e["tool_uses"]) for e in entries)
    total_errors = sum(1 for e in entries for tr in e["tool_results"] if tr["is_error"])

    # Analysis passes
    retries = rpt_retries(entries)
    errors = rpt_errors(entries)
    corrections = rpt_corrections(entries)
    scores = rpt_scores(entries)
    tools = rpt_tool_usage(entries)
    compactions = rpt_compactions(entries)

    # --- Output ---
    print_session_header(session_path)
    print(f"Messages: {user_msgs} user, {asst_msgs} assistant | Tools: {total_tools} | Errors: {total_errors} | Compactions: {len(compactions)}")
    print()

    # Top issues (ranked)
    issues = []
    for r in retries:
        issues.append(("RETRY", f"{r['tool']} x{r['count']} (lines {r['first_line']}-{r['last_line']}): {r['input_preview'][:80]}"))
    inflated = [s for s in scores if s["correction_after"]]
    for s in inflated:
        issues.append(("INFLATED SCORE", f"{s['score']}/10 at line {s['line']}, user found issues after"))
    for c in corrections:
        issues.append(("CORRECTION", f"line {c['line']}: {c['text'][:100]}"))

    if issues:
        print("TOP ISSUES")
        for i, (tag, desc) in enumerate(issues[:10], 1):
            print(f"  {i}. [{tag}] {desc}")
        print()

    # Tool efficiency
    if tools["calls"]:
        print("TOOL EFFICIENCY")
        print(f"  {'Tool':<20} {'Calls':>6} {'Errors':>7} {'Success%':>9}")
        for tool in sorted(tools["calls"], key=tools["calls"].get, reverse=True)[:10]:
            c = tools["calls"][tool]
            e = tools["errors"].get(tool, 0)
            pct = f"{100 * (c - e) / c:.0f}%" if c > 0 else "-"
            print(f"  {tool:<20} {c:>6} {e:>7} {pct:>9}")
        print()

    # Error breakdown
    if errors["total"] > 0:
        print(f"ERRORS ({errors['total']})")
        for cat, count in sorted(errors["by_category"].items(), key=lambda x: x[1], reverse=True):
            print(f"  {cat}: {count}")
        print()

    # Corrections
    if corrections:
        print(f"USER CORRECTIONS ({len(corrections)})")
        for c in corrections[:5]:
            print(f"  Line {c['line']}: {c['text'][:120]}")
        if len(corrections) > 5:
            print(f"  ... and {len(corrections) - 5} more")
        print()

    # Self-scoring
    if scores:
        inflated_count = len(inflated)
        print(f"SELF-SCORING ({len(scores)} scores, {inflated_count} had issues after)")
        for s in scores[:5]:
            marker = " <-- user found issues" if s["correction_after"] else ""
            print(f"  {s['score']}/10 at line {s['line']}{marker}")
        print()

    # Compactions
    if compactions:
        print(f"COMPACTIONS ({len(compactions)})")
        for ev in compactions:
            print(f"  Line {ev['line']}")
        print()

    # Generated rules and memories
    rules, memories = generate_lessons(retries, errors, corrections, scores, compactions)

    if rules:
        print("SUGGESTED CLAUDE.MD RULES")
        for r in rules:
            print(f"  - {r}")
        print()

    if memories:
        print("SUGGESTED MEMORY ENTRIES")
        for m in memories:
            print(f"  - {m}")
        print()

    if not issues and not rules and not deep:
        print("No significant issues detected.")

    # Deep analysis via Gemini
    if deep:
        print("=" * 50)
        print("DEEP ANALYSIS (via Gemini)")
        print("=" * 50)
        print()
        context = build_deep_context(entries, retries, errors, corrections, scores, compactions)
        result = call_gemini(DEEP_PROMPT, context)
        print(result)
        print()

    return 0


# --- Cross-session analysis ---

CROSS_SESSION_PROMPT = """You are analyzing patterns across multiple Claude Code sessions for the same user.

Your job is to find RECURRING patterns, not one-off issues. Focus on what keeps happening.

BAD (one-off): "In session 3, the agent retried a curl command"
GOOD (recurring): "Across 7/10 sessions, the agent retries failing HTTP requests without adding backoff or checking the response code first. This wastes an average of 4 tool calls per session."

BAD (generic): "The agent should be more careful"
GOOD (specific): "The agent scores itself 9-10/10 in 80% of sessions, but the user finds issues in 15% of those. Calibration rule: never score above 8/10 without listing 3 potential flaws first."

Analyze the aggregated data below and produce:

1. **RECURRING PATTERNS** (3-7): Behaviors that appear across multiple sessions. For each, state frequency (X/N sessions) and impact.

2. **CLAUDE.MD RULES** (3-5 rules): Rules that would prevent the most common recurring issues.
   - Each rule must be backed by data from multiple sessions
   - Rules must be imperative and specific

3. **EFFICIENCY INSIGHTS**: Where is time consistently wasted? What tools have the worst success rates across sessions?

4. **SELF-SCORING CALIBRATION**: How accurate is the agent's self-assessment across sessions?

5. **TOP RECOMMENDATION**: The single highest-impact change to improve agent behavior.

Output in plain text, numbered sections. Be concise, data-driven, specific."""


def cmd_report_all(project_dirs, count=10, deep=False):
    """Analyze multiple recent sessions for recurring patterns."""
    sessions = find_all_sessions(project_dirs)
    if not sessions:
        print("No session files found.", file=sys.stderr)
        return 1

    # Skip agent sub-sessions (they're fragments, not full conversations)
    main_sessions = [(m, s, p) for m, s, p in sessions if not p.name.startswith("agent-")]
    if not main_sessions:
        main_sessions = sessions  # fallback if all are agent sessions

    to_analyze = main_sessions[:count]
    print(f"Analyzing {len(to_analyze)} sessions...\n")

    # Per-session analysis
    session_results = []
    for mtime, size, path in to_analyze:
        entries = parse_entries(path)
        if not entries:
            continue

        retries = rpt_retries(entries)
        errors = rpt_errors(entries)
        corrections = rpt_corrections(entries)
        scores = rpt_scores(entries)
        tools = rpt_tool_usage(entries)
        compactions = rpt_compactions(entries)

        user_msgs = sum(1 for e in entries if e["role"] == "user" and e["text"].strip())
        asst_msgs = sum(1 for e in entries if e["role"] == "assistant" and e["text"].strip())
        total_tools = sum(len(e["tool_uses"]) for e in entries)
        total_errors = sum(1 for e in entries for tr in e["tool_results"] if tr["is_error"])

        session_results.append({
            "name": path.name,
            "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
            "user_msgs": user_msgs,
            "asst_msgs": asst_msgs,
            "total_tools": total_tools,
            "total_errors": total_errors,
            "retries": retries,
            "errors": errors,
            "corrections": corrections,
            "scores": scores,
            "tools": tools,
            "compactions": compactions,
        })
        print(f"  {path.name[:40]}  {user_msgs} user msgs, {total_tools} tools, {total_errors} errors", file=sys.stderr)

    if not session_results:
        print("No analyzable sessions found.", file=sys.stderr)
        return 1

    n = len(session_results)
    print()

    # --- Aggregate stats ---
    print(f"CROSS-SESSION SUMMARY ({n} sessions)")
    print(f"  Total messages: {sum(r['user_msgs'] for r in session_results)} user, {sum(r['asst_msgs'] for r in session_results)} assistant")
    print(f"  Total tool calls: {sum(r['total_tools'] for r in session_results)}")
    print(f"  Total errors: {sum(r['total_errors'] for r in session_results)}")
    print()

    # Recurring retry patterns
    retry_freq = Counter()  # tool -> sessions with retries
    retry_counts = defaultdict(list)  # tool -> [count per session]
    for r in session_results:
        seen_tools = set()
        for rt in r["retries"]:
            tool = rt["tool"]
            if tool not in seen_tools:
                retry_freq[tool] += 1
                seen_tools.add(tool)
            retry_counts[tool].append(rt["count"])

    if retry_freq:
        print("RECURRING RETRY PATTERNS")
        for tool, freq in retry_freq.most_common(10):
            avg = sum(retry_counts[tool]) / len(retry_counts[tool])
            print(f"  {tool}: retried in {freq}/{n} sessions (avg {avg:.1f}x when it happens)")
        print()

    # Recurring error categories
    error_freq = Counter()  # category -> sessions with this error
    error_totals = Counter()  # category -> total count
    for r in session_results:
        for cat, cnt in r["errors"]["by_category"].items():
            error_freq[cat] += 1
            error_totals[cat] += cnt

    if error_freq:
        print("RECURRING ERROR TYPES")
        for cat, freq in error_freq.most_common(10):
            avg = error_totals[cat] / freq
            print(f"  {cat}: in {freq}/{n} sessions ({error_totals[cat]} total, avg {avg:.1f} per affected session)")
        print()

    # Correction frequency
    correction_counts = [len(r["corrections"]) for r in session_results]
    sessions_with_corrections = sum(1 for c in correction_counts if c > 0)
    total_corrections = sum(correction_counts)
    if total_corrections > 0:
        print(f"USER CORRECTIONS")
        print(f"  {total_corrections} total across {sessions_with_corrections}/{n} sessions (avg {total_corrections/n:.1f}/session)")
        # Show most common correction texts
        all_corrections = []
        for r in session_results:
            for c in r["corrections"]:
                all_corrections.append(c["text"][:150])
        if all_corrections:
            for c in all_corrections[:5]:
                print(f"  - {c}")
        print()

    # Self-scoring accuracy
    all_scores = []
    all_inflated = []
    for r in session_results:
        all_scores.extend(r["scores"])
        all_inflated.extend(s for s in r["scores"] if s["correction_after"])
    if all_scores:
        avg_score = sum(s["score"] for s in all_scores) / len(all_scores)
        inflated_pct = 100 * len(all_inflated) / len(all_scores) if all_scores else 0
        print(f"SELF-SCORING ACCURACY")
        print(f"  {len(all_scores)} scores across {n} sessions (avg {avg_score:.1f}/10)")
        print(f"  {len(all_inflated)}/{len(all_scores)} ({inflated_pct:.0f}%) had user issues after")
        # Score distribution
        dist = Counter(s["score"] for s in all_scores)
        print(f"  Distribution: {', '.join(f'{s}/10: {c}' for s, c in sorted(dist.items()))}")
        print()

    # Tool efficiency across sessions
    agg_calls = Counter()
    agg_errors = Counter()
    for r in session_results:
        for tool, cnt in r["tools"]["calls"].items():
            agg_calls[tool] += cnt
        for tool, cnt in r["tools"]["errors"].items():
            agg_errors[tool] += cnt
    if agg_calls:
        print("TOOL EFFICIENCY (aggregate)")
        print(f"  {'Tool':<25} {'Calls':>7} {'Errors':>7} {'Success%':>9}")
        for tool in sorted(agg_calls, key=agg_calls.get, reverse=True)[:10]:
            c = agg_calls[tool]
            e = agg_errors.get(tool, 0)
            pct = f"{100 * (c - e) / c:.0f}%" if c > 0 else "-"
            print(f"  {tool:<25} {c:>7} {e:>7} {pct:>9}")
        print()

    # Compaction rate
    comp_counts = [len(r["compactions"]) for r in session_results]
    total_comp = sum(comp_counts)
    if total_comp > 0:
        print(f"COMPACTION RATE")
        print(f"  {total_comp} compactions across {n} sessions (avg {total_comp/n:.1f}/session)")
        heavy = sum(1 for c in comp_counts if c >= 5)
        if heavy:
            print(f"  {heavy}/{n} sessions had 5+ compactions")
        print()

    # Deep analysis via Gemini
    if deep:
        print("=" * 50)
        print("DEEP CROSS-SESSION ANALYSIS (via Gemini)")
        print("=" * 50)
        print()

        # Build aggregated context for Gemini
        ctx_parts = ["Analyzed {} sessions.\n".format(n)]
        for i, r in enumerate(session_results):
            ctx_parts.append("Session {} ({}, {}):".format(i + 1, r["name"][:30], r["date"]))
            ctx_parts.append("  {} user msgs, {} tools, {} errors, {} compactions".format(
                r["user_msgs"], r["total_tools"], r["total_errors"], len(r["compactions"])))
            if r["retries"]:
                retry_strs = ["{} x{}".format(rt["tool"], rt["count"]) for rt in r["retries"][:5]]
                ctx_parts.append("  Retries: {}".format(", ".join(retry_strs)))
            if r["errors"]["by_category"]:
                err_strs = ["{}: {}".format(cat, cnt) for cat, cnt in r["errors"]["by_category"].items()]
                ctx_parts.append("  Errors: {}".format(", ".join(err_strs)))
            if r["corrections"]:
                ctx_parts.append("  Corrections ({}):".format(len(r["corrections"])))
                for c in r["corrections"][:3]:
                    ctx_parts.append("    - {}".format(c["text"][:200]))
            inflated = [s for s in r["scores"] if s["correction_after"]]
            if inflated:
                score_strs = ["{}/10".format(s["score"]) for s in inflated]
                ctx_parts.append("  Inflated scores: {}".format(", ".join(score_strs)))
            ctx_parts.append("")

        context = "\n".join(ctx_parts)
        result = call_gemini(CROSS_SESSION_PROMPT, context)
        print(result)
        print()

    return 0


# --- Pin system ---
# Pin file is auto-namespaced by the parent Claude process PID.
# Each Claude Code session gets its own pin file automatically.
# Fallback: SESSION_RECALL_NS env var, or "default" if no Claude parent found.


def _find_claude_pid():
    """Walk up PPID chain to find the Claude Code process. Returns PID or None."""
    try:
        pid = os.getppid()
        for _ in range(10):  # max 10 levels
            if pid <= 1:
                break
            comm_path = f"/proc/{pid}/comm"
            if os.path.exists(comm_path):
                with open(comm_path) as f:
                    comm = f.read().strip()
                if comm in ("claude", "node"):
                    return pid
            stat_path = f"/proc/{pid}/stat"
            if os.path.exists(stat_path):
                with open(stat_path) as f:
                    # PPID is field 4 (after comm which may contain spaces)
                    ppid = int(f.read().split(")")[1].split()[1])
                pid = ppid
            else:
                break
    except (OSError, IndexError, ValueError):
        pass
    return None


def _pin_path():
    """Get the pin file path, auto-namespaced by Claude process PID."""
    ns = os.environ.get("SESSION_RECALL_NS", "")
    if not ns:
        claude_pid = _find_claude_pid()
        ns = str(claude_pid) if claude_pid else "default"
    return Path(f"/tmp/.session-recall-pin-{ns}")


def get_pinned_session():
    """Read pinned session path. Warns and clears if stale."""
    pin_file = _pin_path()
    if not pin_file.exists():
        return None
    raw = pin_file.read_text().strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.exists():
        print(f"Warning: pinned session no longer exists: {p.name}", file=sys.stderr)
        print(f"Clearing stale pin. Use --pin-by to re-pin.", file=sys.stderr)
        pin_file.unlink()
        return None
    return p


def pin_session(session_path):
    """Pin a session so subsequent calls use the same file."""
    pin_file = _pin_path()
    pin_file.write_text(str(session_path))
    print(f"Pinned session: {session_id_from_path(session_path)}")
    print(f"Path: {session_path}")


def find_session_by_keyword(project_dirs, keyword):
    """Find the most recent session containing a keyword (searches newest first).

    Uses raw string check before regex for speed on large files.
    """
    sessions = find_all_sessions(project_dirs)
    kw_lower = keyword.lower()
    for _mtime, _size, path in sessions:
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    if kw_lower in line.lower():
                        return path
        except OSError:
            continue
    return None


def resolve_session(args, project_dirs):
    """Resolve session path from args, pin, or auto-detect."""
    if args.session:
        p = Path(args.session)
        if not p.exists():
            print(f"Session file not found: {args.session}", file=sys.stderr)
            return None
        return p
    # Check for pinned session (warns if stale)
    pinned = get_pinned_session()
    if pinned:
        return pinned
    # Fall back to most recent
    return find_current_session(project_dirs)


# --- MCP Server (stdin/stdout JSON-RPC) ---


def _mcp_capture(func, *args, **kwargs):
    """Capture stdout from a function call."""
    from io import StringIO
    old = sys.stdout
    sys.stdout = buf = StringIO()
    try:
        func(*args, **kwargs)
    finally:
        sys.stdout = old
    return buf.getvalue()


def _mcp_tool_result(text):
    return {"content": [{"type": "text", "text": text}]}


def _mcp_tool_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _mcp_resolve_session(project_dirs, session_keyword=None):
    if session_keyword:
        found = find_session_by_keyword(project_dirs, session_keyword)
        if found:
            return found
    return find_current_session(project_dirs)


MCP_TOOLS = [
    {
        "name": "recall_search",
        "description": (
            "Search past Claude Code session transcripts for keywords. "
            "Use after context compaction to recover lost details: "
            "decisions, error solutions, commands that worked, corrections."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"},
                             "description": "Keywords to search for (AND logic)."},
                "session_keyword": {"type": "string",
                                    "description": "Optional: find session containing this keyword first."},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "recall_recent",
        "description": (
            "Get last N human/assistant messages from current session. "
            "Skips tool noise. Useful for recovering conversation context after compaction."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 20},
                "session_keyword": {"type": "string"},
            },
        },
    },
    {
        "name": "recall_report",
        "description": (
            "Analyze session for patterns: retry loops, errors, corrections, "
            "inflated self-scores. Returns suggested CLAUDE.md rules and MEMORY.md entries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deep": {"type": "boolean", "default": False,
                          "description": "Use Gemini for project-specific insights."},
                "session_keyword": {"type": "string"},
            },
        },
    },
    {
        "name": "recall_apply",
        "description": (
            "Append an approved rule or memory to CLAUDE.md or MEMORY.md. "
            "User MUST explicitly approve each item before calling this."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Rule or memory text to append."},
                "target": {"type": "string", "enum": ["claude_md", "memory_md"]},
            },
            "required": ["text", "target"],
        },
    },
    {
        "name": "recall_decisions",
        "description": "Find decision points in the session (chose X, going with Y, decided Z).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_keyword": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "recall_list",
        "description": "List recent Claude Code session files with timestamps and sizes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 10},
            },
        },
    },
]


def _mcp_handle_tool(name, args):
    """Handle a single MCP tool call."""
    project_dirs = find_project_dirs()

    if name == "recall_search":
        keywords = args.get("keywords", [])
        if not keywords:
            return _mcp_tool_error("keywords is required")
        sp = _mcp_resolve_session(project_dirs, args.get("session_keyword"))
        if not sp:
            return _mcp_tool_error("No session found")
        out = _mcp_capture(cmd_search, sp, keywords, max_results=args.get("max_results", 10))
        return _mcp_tool_result(out or "No matches found.")

    elif name == "recall_recent":
        sp = _mcp_resolve_session(project_dirs, args.get("session_keyword"))
        if not sp:
            return _mcp_tool_error("No session found")
        out = _mcp_capture(cmd_recent, sp, args.get("count", 20))
        return _mcp_tool_result(out or "No messages found.")

    elif name == "recall_report":
        sp = _mcp_resolve_session(project_dirs, args.get("session_keyword"))
        if not sp:
            return _mcp_tool_error("No session found")
        entries = parse_entries(sp)
        if not entries:
            return _mcp_tool_error("No messages in session")
        retries = rpt_retries(entries)
        errors = rpt_errors(entries)
        corrections = rpt_corrections(entries)
        scores = rpt_scores(entries)
        compactions = rpt_compactions(entries)
        rules, memories = generate_lessons(retries, errors, corrections, scores, compactions)
        result = {
            "stats": {
                "user_messages": sum(1 for e in entries if e["role"] == "user" and e["text"].strip()),
                "tool_calls": sum(len(e["tool_uses"]) for e in entries),
                "errors": errors["total"],
                "compactions": len(compactions),
            },
            "retry_loops": [{"tool": r["tool"], "count": r["count"]} for r in retries[:5]],
            "error_categories": errors["by_category"],
            "corrections": [c["text"][:200] for c in corrections[:5]],
            "inflated_scores": [{"score": s["score"], "line": s["line"]}
                                for s in scores if s["correction_after"]],
            "suggested_rules": rules,
            "suggested_memories": memories,
        }
        if args.get("deep", False):
            context = build_deep_context(entries, retries, errors, corrections, scores, compactions)
            raw = call_gemini(DEEP_PROMPT_JSON, context)
            try:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1]
                if cleaned.endswith("```"):
                    cleaned = cleaned.rsplit("```", 1)[0]
                deep_data = json.loads(cleaned.strip())
                result["deep_analysis"] = deep_data
                for dr in deep_data.get("rules", []):
                    if dr not in result["suggested_rules"]:
                        result["suggested_rules"].append(dr)
                for dm in deep_data.get("memories", []):
                    if dm not in result["suggested_memories"]:
                        result["suggested_memories"].append(dm)
            except (json.JSONDecodeError, KeyError):
                result["deep_analysis_raw"] = raw[:2000]
        return _mcp_tool_result(json.dumps(result, indent=2))

    elif name == "recall_apply":
        text = args.get("text", "").strip()
        target = args.get("target", "")
        if not text:
            return _mcp_tool_error("text is required")
        if target == "claude_md":
            path = _find_claude_md()
            _append_to_file(path, "## Session Recall Rules", [text])
            return _mcp_tool_result("Appended rule to {}".format(path))
        elif target == "memory_md":
            path = _find_memory_md()
            if not path:
                return _mcp_tool_error("Could not locate MEMORY.md")
            _append_to_file(path, "## Session Recall Insights", [text])
            return _mcp_tool_result("Appended entry to {}".format(path))
        else:
            return _mcp_tool_error("target must be 'claude_md' or 'memory_md'")

    elif name == "recall_decisions":
        sp = _mcp_resolve_session(project_dirs, args.get("session_keyword"))
        if not sp:
            return _mcp_tool_error("No session found")
        out = _mcp_capture(cmd_decisions, sp, args.get("max_results", 10))
        return _mcp_tool_result(out or "No decision points found.")

    elif name == "recall_list":
        sessions = find_all_sessions(project_dirs)
        count = args.get("count", 10)
        lines = []
        for mtime, fsize, path in sessions[:count]:
            ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            lines.append("{} {:>8} {}".format(ts, format_size(fsize), path.name))
        return _mcp_tool_result("\n".join(lines) if lines else "No sessions found.")

    return _mcp_tool_error("Unknown tool: {}".format(name))


def mcp_serve():
    """Run MCP server over stdin/stdout."""
    sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        result = None
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "session-recall", "version": "1.2.0"},
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            result = {"tools": MCP_TOOLS}
        elif method == "tools/call":
            result = _mcp_handle_tool(params.get("name", ""), params.get("arguments", {}))
        elif method == "ping":
            result = {}
        else:
            if msg_id is not None:
                resp = {"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32601, "message": "Method not found"}}
                print(json.dumps(resp), flush=True)
            continue

        if msg_id is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}), flush=True)

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Search Claude Code session transcripts to recover context.",
        usage="session-recall [--pin-by KEYWORD | --recent N | --list | --decisions | KEYWORD...]",
    )
    parser.add_argument("keywords", nargs="*", help="Keywords to search for (AND logic)")
    parser.add_argument("--recent", type=int, metavar="N", help="Show last N human/assistant messages (skips tool noise)")
    parser.add_argument("--list", action="store_true", help="List recent session files (marks pinned)")
    parser.add_argument("--session", type=str, metavar="PATH", help="Search a specific session file")
    parser.add_argument("--max", type=int, default=15, metavar="N", help="Max results to show (default: 15)")
    parser.add_argument("--tools", action="store_true", help="Only show tool_use/tool_result entries")
    parser.add_argument("--decisions", action="store_true", help="Find decision points")
    parser.add_argument("--report", action="store_true", help="Analyze session: errors, retries, corrections, suggested rules")
    parser.add_argument("--deep", action="store_true", help="Add Gemini-powered deep analysis to --report (project-specific insights)")
    parser.add_argument("--apply", action="store_true", help="Review and append rules to CLAUDE.md / MEMORY.md (interactive)")
    parser.add_argument("--all", type=int, nargs="?", const=10, metavar="N", help="Cross-session analysis (last N sessions, default 10). Use with --report")
    parser.add_argument("--mcp", action="store_true", help="Run as MCP server (stdin/stdout JSON-RPC)")
    parser.add_argument("--check-compaction", action="store_true", help="Exit 0 if current session was compacted, exit 1 otherwise")
    parser.add_argument("--pin", action="store_true", help="Pin the resolved session for subsequent calls")
    parser.add_argument("--pin-by", type=str, metavar="KEYWORD", help="Find and pin the session containing KEYWORD")
    parser.add_argument("--unpin", action="store_true", help="Remove session pin")

    args = parser.parse_args()

    # --mcp mode: launch MCP server
    if args.mcp:
        return mcp_serve()

    project_dirs = find_project_dirs()

    # --check-compaction mode: fast check for hook usage
    if args.check_compaction:
        session_path = find_current_session(project_dirs)
        if not session_path:
            return 1
        try:
            with open(session_path, "r", errors="replace") as f:
                for raw_line in f:
                    if "continued from a previous conversation" in raw_line.lower():
                        return 0
        except OSError:
            pass
        return 1

    # --unpin mode
    if args.unpin:
        pin_file = _pin_path()
        if pin_file.exists():
            pin_file.unlink()
            print("Session unpinned.")
        else:
            print("No session pinned.")
        return 0

    # --pin-by mode: find session containing keyword and pin it
    if args.pin_by:
        found = find_session_by_keyword(project_dirs, args.pin_by)
        if not found:
            print(f"No session found containing: {args.pin_by}", file=sys.stderr)
            return 1
        pin_session(found)
        return 0

    # --list mode
    if args.list:
        return cmd_list(project_dirs)

    # Resolve session path
    session_path = resolve_session(args, project_dirs)
    if not session_path:
        print("No session files found.", file=sys.stderr)
        return 1

    # --pin mode: pin the resolved session
    if args.pin:
        pin_session(session_path)
        return 0

    # --recent mode
    if args.recent is not None:
        return cmd_recent(session_path, args.recent)

    # --decisions mode
    if args.decisions:
        return cmd_decisions(session_path, args.max)

    # --apply mode
    if args.apply:
        return cmd_apply(session_path, deep=args.deep)

    # --report mode (--all implies --report)
    if args.report or args.all is not None:
        if args.all is not None:
            return cmd_report_all(project_dirs, count=args.all, deep=args.deep)
        return cmd_report(session_path, deep=args.deep)

    # keyword search mode
    if not args.keywords:
        parser.print_help()
        return 1

    return cmd_search(session_path, args.keywords, max_results=args.max, tools_only=args.tools)


if __name__ == "__main__":
    sys.exit(main())
