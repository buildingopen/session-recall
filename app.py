"""
Floom wrapper for session-recall.

Accepts a pasted Claude Code session JSONL transcript and exposes three
core actions: keyword search, recent messages, and a session health report
(retries, errors, corrections, suggested CLAUDE.md rules).
"""

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from floom import app, save_artifact

# session_recall.py lives next to this file.
sys.path.insert(0, str(Path(__file__).parent))

import session_recall as sr  # noqa: E402


def _write_session(jsonl_text: str) -> Path:
    """Drop the pasted JSONL into a temp file and return its path."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="session-recall-"))
    path = tmp_dir / "session.jsonl"
    path.write_text(jsonl_text if jsonl_text.endswith("\n") else jsonl_text + "\n")
    return path


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            fn(*args, **kwargs)
        except SystemExit:
            pass
    return buf.getvalue()


@app.action
def search(jsonl_session: str, keywords: str, max_results: int = 15) -> dict:
    """
    Search a single session transcript for a list of keywords (AND logic).

    jsonl_session: full text of a .jsonl session file
    keywords: comma or space separated keyword list
    """
    if not jsonl_session.strip():
        return {"error": "jsonl_session is empty", "matches": ""}

    kw_list = [k.strip() for k in keywords.replace(",", " ").split() if k.strip()]
    if not kw_list:
        return {"error": "no keywords provided", "matches": ""}

    path = _write_session(jsonl_session)
    output = _capture(sr.cmd_search, path, kw_list, max_results=max_results)

    save_artifact("search-results.txt", output or "No matches.")
    return {
        "keywords": kw_list,
        "max_results": max_results,
        "matches": output.strip() or "No matches found.",
    }


@app.action
def recent(jsonl_session: str, count: int = 20) -> dict:
    """
    Return the last N human/assistant messages from the session (no tool noise).
    """
    if not jsonl_session.strip():
        return {"error": "jsonl_session is empty", "messages": ""}

    path = _write_session(jsonl_session)
    output = _capture(sr.cmd_recent, path, count)

    save_artifact("recent.txt", output or "No messages.")
    return {
        "count": count,
        "messages": output.strip() or "No messages found.",
    }


@app.action
def report(jsonl_session: str) -> dict:
    """
    Analyze a session for retry loops, errors, corrections, and inflated scores.
    Returns suggested CLAUDE.md rules and MEMORY.md entries.
    """
    if not jsonl_session.strip():
        return {"error": "jsonl_session is empty"}

    path = _write_session(jsonl_session)

    entries = sr.parse_entries(path)
    if not entries:
        return {"error": "no messages in session"}

    retries = sr.rpt_retries(entries)
    errors = sr.rpt_errors(entries)
    corrections = sr.rpt_corrections(entries)
    scores = sr.rpt_scores(entries)
    compactions = sr.rpt_compactions(entries)
    rules, memories = sr.generate_lessons(retries, errors, corrections, scores, compactions)

    result = {
        "stats": {
            "user_messages": sum(
                1 for e in entries if e["role"] == "user" and e["text"].strip()
            ),
            "tool_calls": sum(len(e["tool_uses"]) for e in entries),
            "errors": errors["total"],
            "compactions": len(compactions),
        },
        "retry_loops": [
            {"tool": r["tool"], "count": r["count"]} for r in retries[:5]
        ],
        "error_categories": errors["by_category"],
        "corrections": [c["text"][:200] for c in corrections[:5]],
        "inflated_scores": [
            {"score": s["score"], "line": s["line"]}
            for s in scores
            if s.get("correction_after")
        ],
        "suggested_rules": rules,
        "suggested_memories": memories,
    }

    save_artifact("report.json", json.dumps(result, indent=2))
    return result
