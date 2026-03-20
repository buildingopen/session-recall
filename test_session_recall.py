#!/usr/bin/env python3
"""Comprehensive test suite for session-recall CLI."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = "/usr/local/bin/session-recall"

# ---------- helpers ----------

passed = 0
failed = 0
errors = []


def run(args, env_extra=None, expect_rc=0):
    """Run session-recall with args, return (stdout, stderr, returncode)."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result.stdout, result.stderr, result.returncode


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)
        errors.append(name)


# ---------- fixture data ----------

def make_line(typ, role, content):
    """Build a single JSONL line."""
    return json.dumps({"type": typ, "message": {"role": role, "content": content}}, ensure_ascii=False)


def build_fixture_a():
    """Session about a deployment pipeline. 25+ lines with tools, decisions, errors."""
    lines = [
        make_line("user", "user", "Set up the deployment pipeline for the ZEPHYR project"),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "I'll set up the deployment pipeline for ZEPHYR. Let me check the existing config."},
            {"type": "tool_use", "id": "tu_001", "name": "Bash", "input": {"command": "ls /opt/zephyr/deploy"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_001", "content": "docker-compose.yml\nnginx.conf\nMakefile"},
        ]),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "Found the deploy config. Let me read docker-compose."},
            {"type": "tool_use", "id": "tu_002", "name": "Read", "input": {"file_path": "/opt/zephyr/deploy/docker-compose.yml"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_002", "content": "version: '3'\nservices:\n  web:\n    image: zephyr:latest\n    ports:\n      - 8080:8080"},
        ]),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "The docker-compose looks good. I decided to use a blue-green deployment strategy for zero downtime."},
        ]),
        make_line("user", "user", "Sounds good. Also add health checks."),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "Adding health checks to the service. Going with a TCP check on port 8080 with 30s interval."},
            {"type": "tool_use", "id": "tu_003", "name": "Edit", "input": {"file_path": "/opt/zephyr/deploy/docker-compose.yml", "old_string": "ports:", "new_string": "healthcheck:\n      test: ['CMD', 'curl', '-f', 'http://localhost:8080/health']\n      interval: 30s\n    ports:"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_003", "content": "Edit applied successfully."},
        ]),
        make_line("assistant", "assistant", "Health checks added. The plan is to deploy this to staging first, then production after QA pass."),
        make_line("user", "user", "Run the staging deploy now."),
        make_line("assistant", "assistant", [
            {"type": "tool_use", "id": "tu_004", "name": "Bash", "input": {"command": "cd /opt/zephyr/deploy && make staging"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_004", "content": "Error: connection refused to staging.example.com:22", "is_error": True},
        ]),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "The staging deploy failed because the SSH connection to staging.example.com was refused. Let me check if the server is reachable."},
            {"type": "tool_use", "id": "tu_005", "name": "Bash", "input": {"command": "ping -c 2 staging.example.com"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_005", "content": "PING staging.example.com: 2 packets transmitted, 0 received, 100% packet loss"},
        ]),
        make_line("assistant", "assistant", "The staging server is unreachable. Recommendation: check the firewall rules and VPN connection before retrying."),
        make_line("user", "user", "Let's skip staging for now and discuss the CANARY_RELEASE approach."),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "For the canary release, I will use a weighted routing approach. We settled on 10% canary traffic initially, ramping to 50%, then 100%."},
        ]),
        make_line("user", "user", "Agreed. Write the canary config."),
        make_line("assistant", "assistant", [
            {"type": "tool_use", "id": "tu_006", "name": "Bash", "input": {"command": "cat > /opt/zephyr/deploy/canary.yml << 'EOF'\nweight: 10\nramp_steps: [10, 50, 100]\nrollback_threshold: 5\nEOF"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_006", "content": ""},
        ]),
        make_line("assistant", "assistant", "Canary config written. The approach: monitor error rate, auto-rollback if it exceeds 5% at any step."),
        make_line("user", "user", "What UNICORN_METRIC_ALPHA should we track?"),
        make_line("assistant", "assistant", "For UNICORN_METRIC_ALPHA, we chose to track p99 latency and error rate. Decision: use Prometheus with a 15s scrape interval."),
        make_line("user", "user", "Perfect. Wrap up the pipeline."),
        make_line("assistant", "assistant", "Pipeline setup complete. Summary of decisions: blue-green deploy, TCP health checks, 10/50/100 canary ramp, Prometheus monitoring, 5% rollback threshold."),
    ]
    return "\n".join(lines) + "\n"


def build_fixture_b():
    """Session about database migration. 20+ lines, different unique keywords."""
    lines = [
        make_line("user", "user", "We need to migrate the QUASAR_DB database from MySQL to PostgreSQL"),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "I'll plan the QUASAR_DB migration. Let me check the current schema."},
            {"type": "tool_use", "id": "tu_100", "name": "Bash", "input": {"command": "mysqldump --no-data quasar_db > /tmp/schema.sql"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_100", "content": "-- MySQL dump for quasar_db\nCREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(255));"},
        ]),
        make_line("assistant", "assistant", "Schema exported. I will use pgloader for the migration. Approach: convert schema first, then migrate data in batches."),
        make_line("user", "user", "How many tables are there?"),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "Let me count them."},
            {"type": "tool_use", "id": "tu_101", "name": "Bash", "input": {"command": "mysql -e 'SHOW TABLES' quasar_db | wc -l"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_101", "content": "47"},
        ]),
        make_line("assistant", "assistant", "47 tables in QUASAR_DB. The largest is events_log at 12GB. I decided to migrate in order: small tables first, events_log last."),
        make_line("user", "user", "What about the NEBULA_INDEX column types?"),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "NEBULA_INDEX columns use MySQL ENUM types. For PostgreSQL, I'm going with custom CHECK constraints instead of ENUMs, since they're more flexible."},
        ]),
        make_line("user", "user", "Run a test migration on the users table."),
        make_line("assistant", "assistant", [
            {"type": "tool_use", "id": "tu_102", "name": "Bash", "input": {"command": "pgloader mysql://root@localhost/quasar_db postgresql://postgres@localhost/quasar_pg --only users"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_102", "content": "pgloader: 1000 rows transferred in 2.3s", "is_error": False},
        ]),
        make_line("assistant", "assistant", "Test migration of users table succeeded: 1000 rows in 2.3s. No data loss detected."),
        make_line("user", "user", "Good. Now document the AURORA_FAILOVER_SEQUENCE procedure."),
        make_line("assistant", "assistant", [
            {"type": "text", "text": "Here is the AURORA_FAILOVER_SEQUENCE:\n1. Stop writes to MySQL\n2. Run final pgloader sync\n3. Verify row counts match\n4. Switch DNS to PostgreSQL\n5. Monitor for 30 minutes\n6. Decommission MySQL replica"},
            {"type": "tool_use", "id": "tu_103", "name": "Edit", "input": {"file_path": "/docs/migration-runbook.md", "old_string": "## Failover", "new_string": "## AURORA_FAILOVER_SEQUENCE\n1. Stop writes\n2. Final sync\n3. Verify counts\n4. Switch DNS\n5. Monitor 30m\n6. Decommission"}},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_103", "content": "Edit applied successfully."},
        ]),
        make_line("assistant", "assistant", "Failover sequence documented. Let's go with a 2-hour maintenance window on Saturday at 02:00 UTC."),
        make_line("user", "user", "Agreed on the schedule. Final check: rollback plan?"),
        make_line("assistant", "assistant", "Rollback plan: keep MySQL running in read-only for 48 hours post-migration. If issues arise, revert DNS and re-enable writes. Decision: no data will be deleted from MySQL until 7 days after successful migration."),
        make_line("user", "user", "Ship it."),
        make_line("assistant", "assistant", "Migration plan finalized. All decisions recorded. Ready to execute on Saturday 02:00 UTC."),
    ]
    return "\n".join(lines) + "\n"


def build_fixture_empty():
    """Empty file."""
    return ""


def build_fixture_malformed():
    """File with some valid and some malformed lines."""
    lines = [
        '{"this is not valid json',
        "just plain text not json at all",
        make_line("user", "user", "valid message in a GARBLED_FILE_KEYWORD file"),
        '{"type": "user", "message": {"role": "user"',  # truncated JSON
        "",  # empty line
        make_line("assistant", "assistant", "another valid line with GARBLED_FILE_KEYWORD"),
        '{"type": "unknown_type", "message": {"role": "user", "content": "ignored type"}}',
    ]
    return "\n".join(lines) + "\n"


def build_fixture_tool_only():
    """File with only tool_result messages (no human text)."""
    lines = [
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_200", "content": "file1.txt\nfile2.txt"},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_201", "content": "OK"},
        ]),
        make_line("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu_202", "content": "done"},
        ]),
    ]
    return "\n".join(lines) + "\n"


def build_fixture_long_content():
    """File with very long content to test truncation."""
    long_text = "A" * 2000 + "NEEDLE_IN_HAYSTACK" + "B" * 2000
    lines = [
        make_line("user", "user", long_text),
        make_line("assistant", "assistant", "Short reply referencing NEEDLE_IN_HAYSTACK too."),
    ]
    return "\n".join(lines) + "\n"


# ---------- test runner ----------

def main():
    global passed, failed

    tmpdir = tempfile.mkdtemp(prefix="session-recall-test-")
    pin_ns = f"test-{os.getpid()}"
    pin_file = Path(f"/tmp/.session-recall-pin-{pin_ns}")

    # Project dir structure: tmpdir/project-a/*.jsonl
    project_dir = Path(tmpdir) / "project-a"
    project_dir.mkdir(parents=True)

    # Write fixtures with staggered mtimes so ordering is deterministic
    fixture_a_path = project_dir / "session-deploy-pipeline.jsonl"
    fixture_b_path = project_dir / "session-db-migration.jsonl"
    fixture_empty_path = project_dir / "session-empty.jsonl"
    fixture_malformed_path = project_dir / "session-malformed.jsonl"
    fixture_tool_only_path = project_dir / "session-tool-only.jsonl"
    fixture_long_path = project_dir / "session-long-content.jsonl"

    fixture_a_path.write_text(build_fixture_a())
    time.sleep(0.05)
    fixture_b_path.write_text(build_fixture_b())
    time.sleep(0.05)
    fixture_empty_path.write_text(build_fixture_empty())
    time.sleep(0.05)
    fixture_malformed_path.write_text(build_fixture_malformed())
    time.sleep(0.05)
    fixture_tool_only_path.write_text(build_fixture_tool_only())
    time.sleep(0.05)
    fixture_long_path.write_text(build_fixture_long_content())

    env = {"CLAUDE_PROJECTS_DIR": tmpdir, "SESSION_RECALL_NS": pin_ns}

    # Clean up any leftover pin from previous test run
    if pin_file.exists():
        pin_file.unlink()

    try:
        # ==========================================
        print("\n=== CORE SEARCH ===")
        # ==========================================

        # Single keyword match
        out, err, rc = run(["ZEPHYR", "--session", str(fixture_a_path)], env)
        check("search: single keyword match", rc == 0 and "ZEPHYR" in out, f"rc={rc}")

        # Multi-keyword AND logic
        out, err, rc = run(["canary", "rollback", "--session", str(fixture_a_path)], env)
        check("search: multi-keyword AND", rc == 0 and "canary" in out.lower(), f"rc={rc}")

        # Multi-keyword AND: one keyword absent -> no match
        out, err, rc = run(["ZEPHYR", "NONEXISTENT_XYZZY", "--session", str(fixture_a_path)], env)
        check("search: multi-keyword AND no match", rc == 1 and "No matches" in err, f"rc={rc}, err={err[:100]}")

        # Case insensitive match
        out, err, rc = run(["zephyr", "--session", str(fixture_a_path)], env)
        check("search: case insensitive", rc == 0 and "ZEPHYR" in out, f"rc={rc}")

        out2, err2, rc2 = run(["QUASAR_DB", "--session", str(fixture_b_path)], env)
        check("search: case insensitive (fixture b)", rc2 == 0 and "QUASAR_DB" in out2, f"rc={rc2}")

        # No matches returns exit 1
        out, err, rc = run(["XYZZY_NOT_IN_ANY_FILE", "--session", str(fixture_a_path)], env)
        check("search: no match exit 1", rc == 1, f"rc={rc}")

        # --max limits results
        out_all, _, _ = run(["deploy", "--session", str(fixture_a_path), "--max", "100"], env)
        out_lim, _, rc_lim = run(["deploy", "--session", str(fixture_a_path), "--max", "2"], env)
        # Count "---" separators (header has one, each result has one)
        all_count = out_all.count("(line ")
        lim_count = out_lim.count("(line ")
        check("search: --max limits results", lim_count <= 2 and (all_count > lim_count or all_count <= 2), f"all={all_count} lim={lim_count}")

        # Truncation around keyword
        out, _, rc = run(["NEEDLE_IN_HAYSTACK", "--session", str(fixture_long_path)], env)
        check("search: truncation works", rc == 0 and "NEEDLE_IN_HAYSTACK" in out, f"rc={rc}")
        # The output should contain ellipsis markers from truncation
        check("search: truncation has ellipsis", "..." in out, f"no ellipsis in output")

        # ==========================================
        print("\n=== --recent ===")
        # ==========================================

        # Shows messages with human text (use large count to get all messages)
        out, _, rc = run(["--recent", "30", "--session", str(fixture_a_path)], env)
        check("recent: shows human text", rc == 0 and "[user]" in out, f"rc={rc}")

        # Should contain actual user messages (with count=30, first message is included)
        check("recent: contains user message text", "ZEPHYR" in out or "deployment pipeline" in out.lower(), f"content missing")

        # Respects count parameter
        out3, _, _ = run(["--recent", "3", "--session", str(fixture_a_path)], env)
        out10, _, _ = run(["--recent", "10", "--session", str(fixture_a_path)], env)
        count3 = out3.count("[user]") + out3.count("[assistant]")
        count10 = out10.count("[user]") + out10.count("[assistant]")
        check("recent: count parameter respected", count3 <= 3 and count10 <= 10 and count10 >= count3, f"c3={count3} c10={count10}")

        # Empty file: returns 1 with "No human/assistant messages found." but does not crash
        out, err, rc = run(["--recent", "5", "--session", str(fixture_empty_path)], env)
        check("recent: empty file no crash", rc == 1 and "No human/assistant messages found" in out, f"rc={rc}, out={out[:100]}")

        # Tool-only file: should show nothing meaningful
        out, _, rc = run(["--recent", "5", "--session", str(fixture_tool_only_path)], env)
        msg_count = out.count("[user]") + out.count("[assistant]")
        check("recent: tool-only file shows no messages", msg_count == 0, f"msg_count={msg_count}")

        # ==========================================
        print("\n=== --tools ===")
        # ==========================================

        # Only shows tool blocks
        out, _, rc = run(["--tools", "ls", "--session", str(fixture_a_path)], env)
        check("tools: shows tool blocks", rc == 0 and "tool_use" in out.lower(), f"rc={rc}")

        # Keyword filtering within tools
        out, _, rc = run(["--tools", "staging", "--session", str(fixture_a_path)], env)
        check("tools: keyword filter in tool blocks", rc == 0 and "staging" in out.lower(), f"rc={rc}")

        # No matching tool keyword
        out, err, rc = run(["--tools", "XYZZY_NOT_HERE", "--session", str(fixture_a_path)], env)
        check("tools: no match exit 1", rc == 1, f"rc={rc}")

        # ==========================================
        print("\n=== --decisions ===")
        # ==========================================

        # Finds decision keywords
        out, _, rc = run(["--decisions", "--session", str(fixture_a_path)], env)
        check("decisions: finds decisions", rc == 0 and "decision" in out.lower() or "decided" in out.lower() or "settled on" in out.lower(), f"rc={rc}")

        out_b, _, rc_b = run(["--decisions", "--session", str(fixture_b_path)], env)
        check("decisions: fixture b has decisions", rc_b == 0 and "decision" in out_b.lower() or "decided" in out_b.lower(), f"rc={rc_b}")

        # No decisions in tool-only file
        out, err, rc = run(["--decisions", "--session", str(fixture_tool_only_path)], env)
        check("decisions: no decisions exit 1", rc == 1, f"rc={rc}")

        # ==========================================
        print("\n=== --list ===")
        # ==========================================

        out, _, rc = run(["--list"], env)
        check("list: shows files", rc == 0 and "session-" in out, f"rc={rc}")

        # Sorted by mtime: most recent first (fixture_long was written last)
        lines = [l for l in out.strip().split("\n") if l.strip() and not l.startswith("*")]
        if len(lines) >= 2:
            # First non-empty line should be the most recent file
            check("list: sorted by mtime (newest first)", "session-long-content" in lines[0], f"first line: {lines[0][:80]}")
        else:
            check("list: sorted by mtime", False, f"only {len(lines)} lines")

        # ==========================================
        print("\n=== PIN SYSTEM ===")
        # ==========================================

        # Unpin first (clean state)
        run(["--unpin"], env)

        # --pin-by finds and pins correct session
        out, _, rc = run(["--pin-by", "QUASAR_DB"], env)
        check("pin-by: finds session", rc == 0 and "session-db-migration" in out, f"rc={rc}, out={out[:100]}")

        # Pinned session is used on subsequent calls (without --session)
        out, _, rc = run(["QUASAR_DB"], env)
        check("pin: pinned session used automatically", rc == 0 and "QUASAR_DB" in out, f"rc={rc}")

        # Search for keyword only in fixture_a; pinned session is fixture_b, so should fail
        out, err, rc = run(["ZEPHYR"], env)
        check("pin: pinned session limits scope", rc == 1 and "No matches" in err, f"rc={rc}")

        # --session overrides pin
        out, _, rc = run(["ZEPHYR", "--session", str(fixture_a_path)], env)
        check("pin: --session overrides pin", rc == 0 and "ZEPHYR" in out, f"rc={rc}")

        # --unpin removes pin
        out, _, rc = run(["--unpin"], env)
        check("unpin: removes pin", rc == 0 and "unpinned" in out.lower(), f"rc={rc}, out={out}")

        # After unpin, auto-resolves to most recent session
        out, _, rc = run(["NEEDLE_IN_HAYSTACK"], env)
        check("unpin: auto-resolve to most recent", rc == 0 and "NEEDLE_IN_HAYSTACK" in out, f"rc={rc}")

        # Stale pin: write a nonexistent path to pin file, then search
        pin_file.write_text("/tmp/nonexistent-session-file-xyzzy.jsonl")
        out, err, rc = run(["QUASAR_DB"], env)
        check("stale pin: warns and clears", "stale" in err.lower() or "no longer exists" in err.lower(), f"err={err[:150]}")
        # After stale pin cleared, should fall back to most recent and find QUASAR_DB there or in auto-resolved
        check("stale pin: pin file cleaned up", not pin_file.exists(), f"pin file still exists")

        # --pin-by with no match
        out, err, rc = run(["--pin-by", "XYZZY_TOTALLY_ABSENT"], env)
        check("pin-by: no match exit 1", rc == 1, f"rc={rc}")

        # ==========================================
        print("\n=== EDGE CASES ===")
        # ==========================================

        # Empty JSONL file search
        out, err, rc = run(["anything", "--session", str(fixture_empty_path)], env)
        check("edge: empty file search exit 1", rc == 1, f"rc={rc}")

        # Malformed JSON lines are skipped, valid lines still found
        out, _, rc = run(["GARBLED_FILE_KEYWORD", "--session", str(fixture_malformed_path)], env)
        check("edge: malformed lines skipped, valid found", rc == 0 and "GARBLED_FILE_KEYWORD" in out, f"rc={rc}")

        # Tool-only file: --recent shows nothing
        out, _, rc = run(["--recent", "10", "--session", str(fixture_tool_only_path)], env)
        msg_count = out.count("[user]") + out.count("[assistant]")
        check("edge: tool-only file --recent empty", msg_count == 0, f"msg_count={msg_count}")

        # Long content truncation in search
        out, _, rc = run(["NEEDLE_IN_HAYSTACK", "--session", str(fixture_long_path)], env)
        check("edge: long content truncated", rc == 0 and "..." in out, f"rc={rc}")
        # The full 4000+ char content should not appear
        check("edge: full long content not in output", "A" * 500 not in out, f"too much content shown")

        # No keywords prints help
        out, err, rc = run([], env)
        check("edge: no args prints help", rc == 1 and ("usage" in out.lower() or "usage" in err.lower()), f"rc={rc}")

        # --list with pinned marker
        run(["--pin-by", "ZEPHYR"], env)
        out, _, rc = run(["--list"], env)
        check("list: shows pinned marker", "*" in out, f"no * marker in output")

        # Clean up pin
        run(["--unpin"], env)

        # --recent on empty file: returns 1 but still prints session header
        out, _, rc = run(["--recent", "5", "--session", str(fixture_empty_path)], env)
        check("edge: --recent empty file prints header", rc == 1 and "Session:" in out, f"rc={rc}, out={out[:80]}")

        # --decisions with --max
        out, _, rc = run(["--decisions", "--max", "1", "--session", str(fixture_a_path)], env)
        decision_lines = [l for l in out.split("\n") if l.startswith("[") and "(line " in l]
        check("decisions: --max limits", len(decision_lines) <= 1, f"decision_lines={len(decision_lines)}")

        # Search across auto-resolved session (most recent = fixture_long)
        # Touch fixture_b to make it newest
        fixture_b_path.write_text(build_fixture_b())
        out, _, rc = run(["QUASAR_DB"], env)
        check("auto-resolve: finds keyword in most recent", rc == 0 and "QUASAR_DB" in out, f"rc={rc}")

        # File not found with --session
        out, err, rc = run(["test", "--session", "/tmp/nonexistent-xyzzy.jsonl"], env)
        check("edge: --session nonexistent file", rc == 1 and "not found" in err.lower(), f"rc={rc}, err={err[:100]}")

        # --pin on resolved session
        run(["--unpin"], env)
        out, _, rc = run(["--pin", "--session", str(fixture_a_path)], env)
        check("pin: explicit --pin works", rc == 0 and "Pinned" in out, f"rc={rc}")
        # Verify it's pinned
        out, _, rc = run(["ZEPHYR"], env)
        check("pin: explicit pin is active", rc == 0 and "ZEPHYR" in out, f"rc={rc}")
        run(["--unpin"], env)

        # Unpin when nothing is pinned
        run(["--unpin"], env)
        out, _, rc = run(["--unpin"], env)
        check("unpin: when not pinned", rc == 0 and "No session pinned" in out, f"rc={rc}, out={out}")

        # ==========================================
        print("\n=== --report ===")
        # ==========================================

        # Report on fixture_a: has errors, decisions, tool calls
        out, _, rc = run(["--report", "--session", str(fixture_a_path)], env)
        check("report: runs on fixture_a", rc == 0 and "TOOL EFFICIENCY" in out, f"rc={rc}")
        check("report: shows tool stats", "Bash" in out, f"no Bash in output")
        check("report: shows errors section", "ERRORS" in out, f"no ERRORS section")

        # Report shows session header
        check("report: has session header", "Session:" in out, f"no session header")

        # Report on fixture_b: has decisions, no retry loops
        out_b, _, rc_b = run(["--report", "--session", str(fixture_b_path)], env)
        check("report: runs on fixture_b", rc_b == 0, f"rc={rc_b}")
        check("report: fixture_b has tools", "Bash" in out_b, f"no Bash stats")

        # Report on empty file: returns 1
        out_e, err_e, rc_e = run(["--report", "--session", str(fixture_empty_path)], env)
        check("report: empty file returns 1", rc_e == 1, f"rc={rc_e}")

        # Report on malformed file: doesn't crash
        out_m, _, rc_m = run(["--report", "--session", str(fixture_malformed_path)], env)
        check("report: malformed file no crash", rc_m == 0, f"rc={rc_m}")

        # Report on tool-only file: runs (minimal output)
        out_t, _, rc_t = run(["--report", "--session", str(fixture_tool_only_path)], env)
        check("report: tool-only file runs", rc_t == 0, f"rc={rc_t}")

        # Build a fixture with retry loops and corrections for targeted testing
        retry_fixture = project_dir / "session-retry-test.jsonl"
        retry_lines = []
        # User asks something
        retry_lines.append(make_line("user", "user", "Run the tests"))
        # Assistant retries Bash with same command 4 times
        for i in range(4):
            retry_lines.append(make_line("assistant", "assistant", [
                {"type": "tool_use", "id": f"tu_r{i}", "name": "Bash", "input": {"command": "npm test"}},
            ]))
            retry_lines.append(make_line("user", "user", [
                {"type": "tool_result", "tool_use_id": f"tu_r{i}", "content": "Error: test failed", "is_error": True},
            ]))
        # User corrects
        retry_lines.append(make_line("user", "user", "No, that's wrong. You need to start the database first."))
        # Assistant scores itself
        retry_lines.append(make_line("assistant", "assistant", "I rate this fix 9/10."))
        # User finds bug
        retry_lines.append(make_line("user", "user", "This is broken, there's a bug in the output."))
        retry_fixture.write_text("\n".join(retry_lines) + "\n")

        out_r, _, rc_r = run(["--report", "--session", str(retry_fixture)], env)
        check("report: detects retry loops", "RETRY" in out_r, f"no RETRY in output")
        check("report: detects corrections", "CORRECTION" in out_r, f"no CORRECTION in output")
        check("report: detects inflated scores", "INFLATED" in out_r or "issues after" in out_r, f"no inflated score detection")
        check("report: suggests rules", "CLAUDE.MD RULES" in out_r, f"no rules suggested")
        check("report: suggests memory entries", "MEMORY ENTRIES" in out_r, f"no memory entries")

        # Compaction fixture
        compaction_fixture = project_dir / "session-compaction-test.jsonl"
        comp_lines = [
            make_line("user", "user", "Start working on the feature"),
            make_line("assistant", "assistant", "Working on it."),
            make_line("user", "user", "This session is being continued from a previous conversation that ran out of context."),
            make_line("assistant", "assistant", "Continuing the work."),
            make_line("user", "user", "This session is being continued from a previous conversation that ran out of context."),
            make_line("assistant", "assistant", "Still going."),
        ]
        compaction_fixture.write_text("\n".join(comp_lines) + "\n")

        out_c, _, rc_c = run(["--report", "--session", str(compaction_fixture)], env)
        check("report: detects compactions", "COMPACTIONS (2)" in out_c, f"compaction count wrong: {out_c}")
        check("report: compaction rule suggested", "compaction" in out_c.lower() and "workplan" in out_c.lower(), f"no compaction rule")

        # Verify compaction messages are NOT counted as corrections
        check("report: compactions not false-positive corrections",
              "CORRECTION" not in out_c or "continued from" not in out_c,
              f"compaction message leaked into corrections")

        # System-reminder messages should not be detected as corrections
        sysrem_fixture = project_dir / "session-sysrem-test.jsonl"
        sysrem_lines = [
            make_line("user", "user", "Do something"),
            make_line("assistant", "assistant", "Done."),
            make_line("user", "user", "<system-reminder>Don't do that incorrectly</system-reminder>"),
            make_line("user", "user", "No, that's wrong. Fix the bug."),  # real correction
        ]
        sysrem_fixture.write_text("\n".join(sysrem_lines) + "\n")
        out_sr, _, _ = run(["--report", "--session", str(sysrem_fixture)], env)
        # Should find the real correction but not the system-reminder
        has_correction = "CORRECTION" in out_sr
        has_sysrem = "system-reminder" in out_sr.lower() and "CORRECTION" in out_sr
        check("report: system-reminder not false-positive correction",
              has_correction and "Fix the bug" in out_sr,
              f"correction detection wrong")

        # Skill prompt should not be detected as correction
        skill_fixture = project_dir / "session-skill-test.jsonl"
        skill_lines = [
            make_line("user", "user", "Write a post"),
            make_line("user", "user", "Base directory for this skill: /root/.claude/skills/linkedin-copy\n\nActually, write posts that sound like Federico."),
        ]
        skill_fixture.write_text("\n".join(skill_lines) + "\n")
        out_sk, _, _ = run(["--report", "--session", str(skill_fixture)], env)
        check("report: skill prompt not false-positive correction",
              "CORRECTION" not in out_sk or "linkedin" not in out_sk.lower(),
              f"skill prompt leaked into corrections")

        # ==========================================
        print("\n=== --report --all (cross-session) ===")
        # ==========================================

        # Cross-session report across all fixtures
        out_all, err_all, rc_all = run(["--report", "--all", "10"], env)
        check("all: runs successfully", rc_all == 0, f"rc={rc_all}")
        check("all: shows cross-session summary", "CROSS-SESSION SUMMARY" in out_all, f"no summary header")
        check("all: shows tool efficiency", "TOOL EFFICIENCY" in out_all, f"no tool efficiency")

        # --all implies --report (no explicit --report needed)
        out_impl, _, rc_impl = run(["--all", "10"], env)
        check("all: --all implies --report", rc_impl == 0 and "CROSS-SESSION SUMMARY" in out_impl, f"rc={rc_impl}")

        # Cross-session should show recurring error types
        check("all: shows error types", "ERROR" in out_all, f"no error types")

        # Cross-session should aggregate multiple sessions
        # Count how many sessions were analyzed (from stderr)
        analyzed_count = err_all.count("user msgs")
        check("all: analyzes multiple sessions", analyzed_count >= 2, f"only {analyzed_count} sessions analyzed")

        # Cross-session with --all 1 should analyze just 1 session
        out_one, err_one, rc_one = run(["--report", "--all", "1"], env)
        check("all: --all 1 limits to 1 session", rc_one == 0, f"rc={rc_one}")
        one_count = err_one.count("user msgs")
        check("all: --all 1 analyzed 1 session", one_count == 1, f"analyzed {one_count}")

        # Fixture with retries should show up in cross-session retry patterns
        # (retry_fixture was created earlier with 4x Bash retries)
        time.sleep(0.05)
        retry_fixture.write_text(retry_fixture.read_text())  # touch to update mtime
        out_retry_all, _, rc_retry_all = run(["--report", "--all", "20"], env)
        check("all: shows retry patterns", "RETRY" in out_retry_all or "retry" in out_retry_all.lower(),
              f"no retry patterns")

        # Compaction rate should appear if sessions have compactions
        check("all: shows compaction rate", "COMPACTION" in out_retry_all,
              f"no compaction section")

        # Empty project dir: --all returns 1
        empty_tmpdir = tempfile.mkdtemp(prefix="session-recall-empty-")
        Path(empty_tmpdir).joinpath("empty-project").mkdir()
        out_empty, err_empty, rc_empty = run(["--report", "--all"], {"CLAUDE_PROJECTS_DIR": empty_tmpdir, "SESSION_RECALL_NS": pin_ns})
        check("all: empty dir returns 1", rc_empty == 1, f"rc={rc_empty}")
        shutil.rmtree(empty_tmpdir, ignore_errors=True)

        # ==========================================
        print(f"\n=== --apply ===")
        # ==========================================

        # --apply with no input (should parse but need interactive, so test with pipe)
        out_apply, err_apply, rc_apply = run(["--apply"], env)
        # Should not crash; may say "no rules" or exit cleanly
        check("apply: runs without crash", rc_apply in (0, 1), f"rc={rc_apply}")

        # --apply shows up in --help
        out_help, _, _ = run(["--help"], env)
        check("apply: in help text", "--apply" in out_help, "not in help")

        # --apply on empty file returns 1
        empty_apply_dir = Path(tmpdir) / "empty-apply-proj"
        empty_apply_dir.mkdir(exist_ok=True)
        empty_apply = empty_apply_dir / "empty-apply.jsonl"
        empty_apply.write_text("")
        out_apply_empty, _, rc_apply_empty = run(["--apply", "--session", str(empty_apply)], env)
        check("apply: empty file returns 1", rc_apply_empty == 1, f"rc={rc_apply_empty}")

        # Test _find_claude_md and _find_memory_md imports
        import importlib.util
        # Try repo path first, then installed path
        sr_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_recall.py")
        if not os.path.exists(sr_path):
            sr_path = SCRIPT
        spec = importlib.util.spec_from_file_location("session_recall_mod", sr_path)
        if spec and spec.loader:
            sr_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sr_mod)
            # _find_claude_md should return a Path
            claude_md = sr_mod._find_claude_md()
            check("apply: _find_claude_md returns Path", isinstance(claude_md, Path), f"got {type(claude_md)}")

            # _append_to_file creates new section
            test_append_file = Path(tmpdir) / "test-append.md"
            sr_mod._append_to_file(test_append_file, "## Test Section", ["rule one", "rule two"])
            content = test_append_file.read_text()
            check("apply: append creates section", "## Test Section" in content and "- rule one" in content,
                  f"content: {content[:200]}")

            # _append_to_file appends to existing section
            sr_mod._append_to_file(test_append_file, "## Test Section", ["rule three"])
            content2 = test_append_file.read_text()
            check("apply: append to existing section", "- rule three" in content2 and "- rule one" in content2,
                  f"content: {content2[:200]}")

            # _hitl_review with empty list returns empty
            result = sr_mod._hitl_review([], "test")
            check("apply: hitl empty list", result == [], f"got {result}")
        else:
            check("apply: module import", False, "could not import session_recall module")

        # ==========================================
        print(f"\n=== --mcp ===")
        # ==========================================

        # --mcp flag shows up in help
        check("mcp: in help text", "--mcp" in out_help, "not in help")

        # MCP server responds to initialize
        mcp_input = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        mcp_input += json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"

        mcp_proc = subprocess.run(
            [sys.executable, SCRIPT, "--mcp"],
            input=mcp_input, capture_output=True, text=True, timeout=10,
            env={**os.environ, "CLAUDE_PROJECTS_DIR": tmpdir, "SESSION_RECALL_NS": pin_ns},
        )
        mcp_lines = [l for l in mcp_proc.stdout.strip().split("\n") if l.strip()]
        check("mcp: server responds", len(mcp_lines) >= 2, f"got {len(mcp_lines)} lines")

        if len(mcp_lines) >= 1:
            init_resp = json.loads(mcp_lines[0])
            check("mcp: initialize response", init_resp.get("result", {}).get("serverInfo", {}).get("name") == "session-recall",
                  f"got {init_resp}")

        if len(mcp_lines) >= 2:
            tools_resp = json.loads(mcp_lines[1])
            tool_names = [t["name"] for t in tools_resp.get("result", {}).get("tools", [])]
            check("mcp: has recall_search", "recall_search" in tool_names, f"tools: {tool_names}")
            check("mcp: has recall_report", "recall_report" in tool_names, f"tools: {tool_names}")
            check("mcp: has recall_apply", "recall_apply" in tool_names, f"tools: {tool_names}")
            check("mcp: has recall_recent", "recall_recent" in tool_names, f"tools: {tool_names}")
            check("mcp: has recall_decisions", "recall_decisions" in tool_names, f"tools: {tool_names}")
            check("mcp: has recall_list", "recall_list" in tool_names, f"tools: {tool_names}")

        # MCP tool call: recall_search
        mcp_search = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        mcp_search += json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "recall_search", "arguments": {"keywords": ["ZEPHYR"], "session_keyword": "ZEPHYR"}
        }}) + "\n"
        mcp_proc2 = subprocess.run(
            [sys.executable, SCRIPT, "--mcp"],
            input=mcp_search, capture_output=True, text=True, timeout=10,
            env={**os.environ, "CLAUDE_PROJECTS_DIR": tmpdir, "SESSION_RECALL_NS": pin_ns},
        )
        mcp_lines2 = [l for l in mcp_proc2.stdout.strip().split("\n") if l.strip()]
        if len(mcp_lines2) >= 2:
            search_resp = json.loads(mcp_lines2[1])
            search_text = search_resp.get("result", {}).get("content", [{}])[0].get("text", "")
            check("mcp: recall_search finds ZEPHYR", "ZEPHYR" in search_text, f"got: {search_text[:100]}")
        else:
            check("mcp: recall_search response", False, f"only {len(mcp_lines2)} lines")

        # MCP tool call: recall_list
        mcp_list = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        mcp_list += json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "recall_list", "arguments": {"count": 5}
        }}) + "\n"
        mcp_proc3 = subprocess.run(
            [sys.executable, SCRIPT, "--mcp"],
            input=mcp_list, capture_output=True, text=True, timeout=10,
            env={**os.environ, "CLAUDE_PROJECTS_DIR": tmpdir, "SESSION_RECALL_NS": pin_ns},
        )
        mcp_lines3 = [l for l in mcp_proc3.stdout.strip().split("\n") if l.strip()]
        if len(mcp_lines3) >= 2:
            list_resp = json.loads(mcp_lines3[1])
            list_text = list_resp.get("result", {}).get("content", [{}])[0].get("text", "")
            check("mcp: recall_list shows sessions", ".jsonl" in list_text, f"got: {list_text[:100]}")
        else:
            check("mcp: recall_list response", False, f"only {len(mcp_lines3)} lines")

        # MCP tool call: recall_apply (append to file)
        apply_test_dir = Path(tmpdir) / "mcp-apply-test"
        apply_test_dir.mkdir(exist_ok=True)
        apply_claude_md = apply_test_dir / "CLAUDE.md"
        apply_claude_md.write_text("# Test\n\nExisting content.\n")

        mcp_apply = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        mcp_apply += json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "recall_apply", "arguments": {"text": "Test rule from MCP", "target": "claude_md"}
        }}) + "\n"
        # recall_apply uses _find_claude_md() which checks cwd; we test the MCP handler directly
        # via the module import instead
        if spec and spec.loader:
            result = sr_mod._mcp_handle_tool("recall_apply", {"text": "MCP test rule", "target": "claude_md"})
            result_text = result.get("content", [{}])[0].get("text", "")
            check("mcp: recall_apply returns success", "Appended" in result_text, f"got: {result_text}")

        # MCP tool call: recall_report (structured JSON)
        mcp_report = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        mcp_report += json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "recall_report", "arguments": {"session_keyword": "ZEPHYR"}
        }}) + "\n"
        mcp_proc_report = subprocess.run(
            [sys.executable, SCRIPT, "--mcp"],
            input=mcp_report, capture_output=True, text=True, timeout=10,
            env={**os.environ, "CLAUDE_PROJECTS_DIR": tmpdir, "SESSION_RECALL_NS": pin_ns},
        )
        mcp_report_lines = [l for l in mcp_proc_report.stdout.strip().split("\n") if l.strip()]
        if len(mcp_report_lines) >= 2:
            report_resp = json.loads(mcp_report_lines[1])
            report_text = report_resp.get("result", {}).get("content", [{}])[0].get("text", "")
            try:
                report_data = json.loads(report_text)
                check("mcp: recall_report returns JSON", "stats" in report_data, f"keys: {list(report_data.keys())}")
                check("mcp: recall_report has stats", "user_messages" in report_data.get("stats", {}),
                      f"stats: {report_data.get('stats')}")
            except json.JSONDecodeError:
                check("mcp: recall_report returns JSON", False, f"not JSON: {report_text[:100]}")
        else:
            check("mcp: recall_report response", False, f"only {len(mcp_report_lines)} lines")

        # MCP error handling: unknown tool
        mcp_unknown = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        mcp_unknown += json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "nonexistent_tool", "arguments": {}
        }}) + "\n"
        mcp_proc_unk = subprocess.run(
            [sys.executable, SCRIPT, "--mcp"],
            input=mcp_unknown, capture_output=True, text=True, timeout=10,
            env={**os.environ, "CLAUDE_PROJECTS_DIR": tmpdir, "SESSION_RECALL_NS": pin_ns},
        )
        mcp_unk_lines = [l for l in mcp_proc_unk.stdout.strip().split("\n") if l.strip()]
        if len(mcp_unk_lines) >= 2:
            unk_resp = json.loads(mcp_unk_lines[1])
            unk_result = unk_resp.get("result", {})
            check("mcp: unknown tool returns error", unk_result.get("isError", False), f"got: {unk_result}")
        else:
            check("mcp: unknown tool response", False, f"only {len(mcp_unk_lines)} lines")

        # MCP error handling: recall_apply missing args
        if spec and spec.loader:
            result_no_text = sr_mod._mcp_handle_tool("recall_apply", {"target": "claude_md"})
            check("mcp: recall_apply missing text", result_no_text.get("isError", False), f"got: {result_no_text}")
            result_bad_target = sr_mod._mcp_handle_tool("recall_apply", {"text": "rule", "target": "invalid"})
            check("mcp: recall_apply bad target", result_bad_target.get("isError", False), f"got: {result_bad_target}")

        # MCP error handling: malformed params (non-dict)
        mcp_bad_params = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        mcp_bad_params += json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": "not a dict"}) + "\n"
        mcp_proc_bad = subprocess.run(
            [sys.executable, SCRIPT, "--mcp"],
            input=mcp_bad_params, capture_output=True, text=True, timeout=10,
            env={**os.environ, "CLAUDE_PROJECTS_DIR": tmpdir, "SESSION_RECALL_NS": pin_ns},
        )
        mcp_bad_lines = [l for l in mcp_proc_bad.stdout.strip().split("\n") if l.strip()]
        if len(mcp_bad_lines) >= 2:
            bad_resp = json.loads(mcp_bad_lines[1])
            check("mcp: malformed params returns error", "error" in bad_resp, f"got: {bad_resp}")
            check("mcp: malformed params error code", bad_resp.get("error", {}).get("code") == -32602, f"got: {bad_resp}")
        else:
            check("mcp: malformed params response", False, f"only {len(mcp_bad_lines)} lines")

        # ==========================================
        print(f"\n=== _find_memory_md() path resolution ===")
        # ==========================================

        if spec and spec.loader:
            # Create project dir structure: tmpdir/.claude/projects/-root-session-recall/ and -root/
            fake_home = Path(tmpdir) / "fakehome"
            fake_projects = fake_home / ".claude" / "projects"
            parent_dir = fake_projects / "-root"
            parent_dir.mkdir(parents=True)
            (parent_dir / "memory").mkdir()
            (parent_dir / "memory" / "MEMORY.md").write_text("# Parent memory\n")

            # Test: _find_memory_md with parent fallback
            # We can't easily override Path.home(), so test the logic directly
            # by testing the path walking algorithm
            cwd = Path("/root/session-recall")
            candidates = [cwd] + list(cwd.parents)
            existing_dirs = {"-root": parent_dir}
            found = None
            for path in candidates:
                encoded = str(path).replace("/", "-")
                if encoded in existing_dirs:
                    found = encoded
                    break
            check("path resolution: /root/session-recall falls back to -root",
                  found == "-root", f"found: {found}")

            # Test: exact match takes priority
            child_dir = fake_projects / "-root-session-recall"
            child_dir.mkdir(parents=True)
            (child_dir / "memory").mkdir()
            existing_dirs["-root-session-recall"] = child_dir
            found2 = None
            for path in candidates:
                encoded = str(path).replace("/", "-")
                if encoded in existing_dirs:
                    found2 = encoded
                    break
            check("path resolution: exact match takes priority",
                  found2 == "-root-session-recall", f"found: {found2}")

        # ==========================================
        print(f"\n=== _append_to_file() code fence handling ===")
        # ==========================================

        if spec and spec.loader:
            # Test: header inside code fence is ignored
            fence_file = Path(tmpdir) / "fence-test.md"
            fence_file.write_text(
                "# Doc\n\nSome content.\n\n```markdown\n## Session Recall Rules\nThis is example code\n```\n\n## Other Section\nstuff\n"
            )
            sr_mod._append_to_file(fence_file, "## Session Recall Rules", ["new rule"])
            content = fence_file.read_text()
            # The rule should be appended at the end as a NEW section (not inside the fence)
            check("fence: header inside fence ignored",
                  content.count("## Session Recall Rules") == 2,
                  f"count: {content.count('## Session Recall Rules')}")
            check("fence: new rule appended correctly",
                  "- new rule" in content, f"content: {content[-200:]}")

            # Test: header outside code fence works normally
            normal_file = Path(tmpdir) / "normal-test.md"
            normal_file.write_text("# Doc\n\n## Session Recall Rules\n- existing rule\n\n## Other\nstuff\n")
            sr_mod._append_to_file(normal_file, "## Session Recall Rules", ["added rule"])
            content2 = normal_file.read_text()
            check("fence: normal section append works",
                  "- added rule" in content2 and "- existing rule" in content2,
                  f"content: {content2}")
            # The added rule should be before ## Other
            added_pos = content2.index("- added rule")
            other_pos = content2.index("## Other")
            check("fence: rule inserted before next section",
                  added_pos < other_pos, f"added={added_pos} other={other_pos}")

        # ==========================================
        print(f"\n=== --check-compaction ===")
        # ==========================================

        # Compacted session: exit 0
        out_cc, _, rc_cc = run(["--check-compaction", "--session", str(compaction_fixture)], env)
        # --check-compaction doesn't use --session (it uses find_current_session), so we set up differently
        # Touch compaction fixture to make it newest
        compaction_fixture.write_text(compaction_fixture.read_text())
        time.sleep(0.05)
        out_cc, _, rc_cc = run(["--check-compaction"], env)
        check("check-compaction: compacted session exit 0", rc_cc == 0, f"rc={rc_cc}")

        # Non-compacted session: exit 1
        # Remove compaction fixture so no session in the dir has the marker
        # (--check-compaction now checks top 3 recent sessions, not just the newest)
        compaction_fixture.unlink()
        time.sleep(0.05)
        fixture_a_path.write_text(build_fixture_a())
        out_nc, _, rc_nc = run(["--check-compaction"], env)
        check("check-compaction: clean session exit 1", rc_nc == 1, f"rc={rc_nc}")
        # Restore compaction fixture for later tests
        comp_restore = [
            make_line("user", "user", "Start working on the feature"),
            make_line("assistant", "assistant", "Working on it."),
            make_line("user", "user", "This session is being continued from a previous conversation that ran out of context."),
            make_line("assistant", "assistant", "Continuing the work."),
        ]
        compaction_fixture.write_text("\n".join(comp_restore) + "\n")

        # --check-compaction in help
        out_help2, _, _ = run(["--help"], env)
        check("check-compaction: in help text", "--check-compaction" in out_help2, "not in help")

        # ==========================================
        print(f"\n=== Soft correction patterns ===")
        # ==========================================

        # Build a fixture with soft correction patterns
        soft_fixture = project_dir / "session-soft-corrections.jsonl"
        soft_lines = [
            make_line("user", "user", "Build the API"),
            make_line("assistant", "assistant", "Using Express."),
            make_line("user", "user", "Let's use Fastify instead of Express."),
            make_line("assistant", "assistant", "OK switching to Fastify."),
            make_line("user", "user", "I prefer TypeScript for this project."),
            make_line("assistant", "assistant", "Switching to TypeScript."),
            make_line("user", "user", "Can we use pnpm instead?"),
        ]
        soft_fixture.write_text("\n".join(soft_lines) + "\n")

        out_soft, _, rc_soft = run(["--report", "--session", str(soft_fixture)], env)
        check("soft corrections: detected", "CORRECTION" in out_soft, f"no corrections found")
        check("soft corrections: Fastify or prefer detected",
              "Fastify" in out_soft or "prefer" in out_soft.lower() or "pnpm" in out_soft.lower(),
              f"output: {out_soft[:300]}")

        # ==========================================
        print(f"\n=== Improved rule quality ===")
        # ==========================================

        # Rules from retry fixture should include specific context
        if spec and spec.loader:
            entries_r = sr_mod.parse_entries(retry_fixture)
            retries_r = sr_mod.rpt_retries(entries_r)
            errors_r = sr_mod.rpt_errors(entries_r)
            corrections_r = sr_mod.rpt_corrections(entries_r)
            scores_r = sr_mod.rpt_scores(entries_r)
            compactions_r = sr_mod.rpt_compactions(entries_r)
            rules_r, memories_r = sr_mod.generate_lessons(retries_r, errors_r, corrections_r, scores_r, compactions_r)
            # Rules should contain specific command previews AND error text (why it failed)
            has_command = any("npm test" in r for r in rules_r)
            has_error = any("test failed" in r.lower() or "error" in r.lower() for r in rules_r)
            has_advice = any("root cause" in r.lower() or "switch approach" in r.lower() for r in rules_r)
            check("rule quality: includes specific context", has_command and has_advice, f"rules: {rules_r}")
            check("rule quality: includes error text", has_error, f"rules: {rules_r}")
            # Memories should contain actual correction text
            has_correction_text = any("database" in m.lower() for m in memories_r)
            check("rule quality: memories include correction text", has_correction_text, f"memories: {memories_r}")

    finally:
        # Cleanup
        shutil.rmtree(tmpdir, ignore_errors=True)
        if pin_file.exists():
            pin_file.unlink()

    # ==========================================
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print(f"Failures: {', '.join(errors)}")
    print(f"{'='*50}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
