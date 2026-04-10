# app.py
# This is the main backend server file for the web application.
# It uses Flask (a Python web framework) to provide API endpoints and web pages.
# The backend also acts as a bridge between the web UI, a local AI agent, and external tools.

import sys
import os
import re
import hashlib
import json
import subprocess
import threading
import atexit
import time

from flask import Flask, render_template, request, jsonify
import requests

# --- Configuration and Setup ---

# Define paths to important files and directories
MCP_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MCP_SERVER_SCRIPT = os.path.join(MCP_SERVER_DIR, "mcp_tools.py")


# Create the Flask app
app = Flask(__name__)

# URL for the Copilot Bridge (AI agent)
COPILOT_BRIDGE_URL = "http://127.0.0.1:5100/api/prompt"

# Path to the feedback file where users can report false positives (e.g. terms wrongly flagged as noise)
FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), "feedback_log.json")

# Maximum steps the AI agent can take in a single task

MAX_AGENT_STEPS = 20  # Raised — acts as safety net, not a kill switch



# --- MCP Client Class ---
# This class manages communication with the FastMCP server (an external tool).
# It starts the server if needed, sends requests, and receives responses.
class MCPClient:
    """Communicates with the FastMCP server over stdio using JSON-RPC."""

    def __init__(self):
        self.process: subprocess.Popen[bytes] | None = None
        self.lock = threading.Lock()
        self._request_id = 0
        self._initialized = False

    def _ensure_started(self):
        if self.process is None or self.process.poll() is not None:
            env = {
                **os.environ,
                "ENABLE_READABILITY_CHECK": "true",
                "READABILITY_SENTENCE_WORD_LIMIT": "40",
                "READABILITY_PARAGRAPH_WORD_LIMIT": "250",
            }
            self.process = subprocess.Popen(
                [sys.executable, MCP_SERVER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                cwd=MCP_SERVER_DIR,
                env=env,
            )
            assert self.process.stdin is not None
            assert self.process.stdout is not None
            self._initialized = False

        if not self._initialized:
            self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "flask-bridge", "version": "1.0.0"},
            })
            self._send_notification("notifications/initialized", {})
            self._initialized = True

    def _next_id(self):
        self._request_id += 1
        return self._request_id

    def _send_request(self, method, params, timeout=None):
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        if timeout is None:
            return self._send_and_receive(msg)
        return self._send_and_receive(msg, timeout=timeout)

    def _send_notification(self, method, params):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        raw = json.dumps(msg) + "\n"
        proc = self.process
        if proc is None or proc.stdin is None:
            raise RuntimeError("MCP server not started")
        proc.stdin.write(raw.encode("utf-8"))
        proc.stdin.flush()

    def _send_and_receive(self, msg, timeout=120):
        proc = self.process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError("MCP server not started")
        raw = json.dumps(msg) + "\n"
        proc.stdin.write(raw.encode("utf-8"))
        proc.stdin.flush()

        msg_id = msg.get("id")
        print(f"[MCP] Sent: {msg.get('method', '?')} (id={msg_id})", flush=True)

        from queue import Queue, Empty

        result_queue: Queue = Queue()

        def _reader():
            try:
                stdout = proc.stdout
                if stdout is None:
                    result_queue.put(RuntimeError("MCP server stdout is None"))
                    return
                while True:
                    line = stdout.readline()
                    if not line:
                        result_queue.put(ConnectionError("MCP server closed unexpectedly"))
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "id" not in parsed:
                        print(f"[MCP] notification: {parsed.get('method', '?')}",  flush=True)
                        continue
                    result_queue.put(parsed)
                    return
            except Exception as e:
                result_queue.put(e)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        start = time.time()
        try:
            result = result_queue.get(timeout=timeout)
        except Empty:
            stderr_output = ""
            if proc.stderr:
                try:
                    import selectors
                    sel = selectors.DefaultSelector()
                    sel.register(proc.stderr, selectors.EVENT_READ)
                    if sel.select(timeout=0):
                        stderr_output = proc.stderr.read(4096).decode("utf-8", errors="replace")
                    sel.close()
                except Exception:
                    pass
            raise TimeoutError(
                f"MCP server did not respond within {timeout}s. stderr: {stderr_output or '(empty)'}"
            )

        if isinstance(result, Exception):
            raise result

        elapsed = time.time() - start
        print(f"[MCP] Response received in {elapsed:.1f}s", flush=True)
        return result

    def call_tool(self, tool_name, arguments):
        with self.lock:
            try:
                print(f"[MCP] call_tool: {tool_name}", flush=True)
                self._ensure_started()
                result = self._send_request("tools/call", {"name": tool_name, "arguments": arguments}, timeout=300)
                print(f"[MCP] call_tool result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}", flush=True)
                if "error" in result:
                    return {"success": False, "error": result["error"].get("message", str(result["error"]))}
                tool_content = result.get("result", {}).get("content", [])
                if tool_content:
                    text = tool_content[0].get("text", "")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"success": True, "data": text}
                return {"success": True, "data": result.get("result")}
            except Exception as e:
                print(f"[MCP] call_tool error: {e}", flush=True)
                return {"success": False, "error": str(e)}

    def list_tools(self):
        with self.lock:
            self._ensure_started()
            result = self._send_request("tools/list", {})
            return result.get("result", {}).get("tools", [])

    def shutdown(self):
        proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

# Create a single instance of the MCP client to use throughout the app
mcp_client = MCPClient()


# --- Copilot Bridge (LLM) ---
# This function sends prompts to the Copilot Bridge (AI agent) and returns the response.
def call_llm(prompt: str) -> tuple[str, str | None]:
    """Send a prompt to the LLM (Copilot) and return (response, error)."""
    try:
        resp = requests.post(COPILOT_BRIDGE_URL, json={"prompt": prompt}, timeout=180)
        data = resp.json()
        if "error" in data:
            return "", f"Bridge error: {data['error']}"
        return data.get("response", ""), None
    except requests.ConnectionError:
        return "", "Cannot reach Copilot Bridge. Make sure VS Code is running with the bridge extension active."
    except requests.Timeout:
        return "", "Request timed out."
    except Exception as e:
        return "", str(e)


# --- Helper Functions ---
# These functions help extract information from user input, such as Confluence page IDs or GitHub PR info.

def extract_confluence_page_id(url: str) -> str | None:
    m = re.search(r"/pages/(\d+)", url)
    return m.group(1) if m else None


def extract_pr_info(url: str) -> dict | None:
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if m:
        return {"owner": m.group(1), "repo": m.group(2), "pr_number": int(m.group(3))}
    return None


def _extract_confluence_page_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    if not text:
        return ids
    conf_urls = re.findall(r"https?://[^\s]*atlassian\.net/wiki[^\s]*", text)
    for url in conf_urls:
        pid = extract_confluence_page_id(url)
        if pid and pid not in ids:
            ids.append(pid)
    return ids


def _extract_prs_from_text(text: str) -> list[dict]:
    found: list[dict] = []
    if not text:
        return found
    pr_urls = re.findall(r"https?://github\.com/[^\s]+/pull/\d+", text)
    for url in pr_urls:
        info = extract_pr_info(url)
        if info and info not in found:
            found.append(info)
    return found


def _fallback_links_from_history(history: list) -> tuple[list[str], list[dict]]:
    """Find most recent user message containing links and reuse those IDs for follow-up prompts."""
    if not history:
        return [], []

    for entry in reversed(history):
        if entry.get("role") != "user":
            continue
        text = str(entry.get("text", ""))
        page_ids = _extract_confluence_page_ids_from_text(text)
        prs = _extract_prs_from_text(text)
        if page_ids or prs:
            return page_ids, prs
    return [], []


def _load_recent_feedback(limit: int = 20) -> str:
    """Load recent user feedback so the AI can learn from past false positives."""
    if not os.path.exists(FEEDBACK_FILE):
        return ""
    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        if not entries:
            return ""
        recent = entries[-limit:]
        feedback_lines = []
        for entry in recent:
            term = entry.get("term", "")
            sentence = entry.get("sentence", "")
            feedback = entry.get("feedback", "")
            feedback_lines.append(f'  - Term: "{term}" | Context: "{sentence}" | User said: {feedback}')
        return (
            "\nUSER FEEDBACK ON PAST FLAGS (learn from these — do NOT repeat these mistakes):\n"
            + "\n".join(feedback_lines) + "\n"
        )
    except Exception:
        return ""


def format_result(data) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)


# --- Flask Routes ---
# These define the web pages and API endpoints the backend provides.
@app.route("/")
def index():
    # Renders the main web page (index.html)
    return render_template("index.html")


# --- Agent System Prompt ---
# This is a long string that defines the rules and workflow for the AI agent.
# It tells the agent how to process user requests, use tools, and when to stop.

AGENT_SYSTEM_PROMPT = """You are an autonomous AI agent called MunnAI.

YOUR CORE RULES:
1. Always read and consider the user's ENTIRE prompt and conversation history first.
2. Think step by step before acting.
3. When given an instructions page and a target page, ALWAYS fetch the instructions page first to read what it says before doing anything to the target page. Never assume what it says.
4. After EVERY tool call, you MUST explicitly verify: "Have I completed ALL required actions? What is left undone?"
5. If a task requires applying an action to MULTIPLE items (e.g. every occurrence of a word, every section, every row), you MUST continue calling tools until ALL items are covered. Never stop after one.
6. Track completed vs remaining work in every THOUGHT.
7. Only produce FINAL_ANSWER when the ENTIRE task is fully complete — not partially.
8. If the user's intent is ambiguous, ask a clarifying question using FINAL_ANSWER.
9. Do NOT auto-review pages unless the instructions or user explicitly ask for it.
10. When an instructions page defines specific output requirements, derive a task contract from it and satisfy that exact contract. Do not substitute a generic template. If a built-in review tool does not fully satisfy the contract, use additional tools to complete the missing required output.

RESPONSE FORMAT — you must ALWAYS use one of these two formats:

Format 1 — when you need to use a tool:
THOUGHT: <your reasoning, what you have done so far, what still needs to be done>
TOOL_CALL: <tool_name>
ARGS: <json_arguments>

Format 2 — when the task is fully complete OR you need to ask for clarification:
THOUGHT: <your reasoning confirming task is complete>
FINAL_ANSWER: <your response to the user>

VERIFICATION RULE (CRITICAL):
After every tool call observation, ask yourself:
- "Have I completed ALL required actions, not just some?"
- "Are there more items/occurrences/steps I missed?"
- If NO: keep using tools
- If YES: only then write FINAL_ANSWER

Available tools:

1. review_confluence_page_content
   - Reviews a Confluence page for grammar, structure, readability, etc. and posts inline comments + footer summary.
   - Args: {"page_id": "...", "checklist_page_id": "(optional) page ID with custom review instructions"}
   - Use ONLY when the user explicitly asks to review a page OR when instructions say to review.

2. get_page_content_by_sections_tool
   - Fetches the content of a Confluence page split into sections (read-only, does NOT post anything).
   - Args: {"page_id": "..."}
   - Returns the FULL page content split into manageable sections.
   - Use to READ a page before deciding what to do.

3. post_confluence_footer_comment
   - Posts a footer comment on a Confluence page.
   - Args: {"page_id": "...", "comment": "..."}

4. post_confluence_inline_comment
   - Posts an inline comment on specific text in a Confluence page.
   - Args: {"page_id": "...", "comment": "...", "text_selection": "exact text to attach comment to"}
   - CRITICAL: This posts ONE comment per call. To comment on N occurrences, call this tool N times, once per occurrence with the exact text of each occurrence.
   - NOTE: This only posts on ONE text selection per call. If you need to comment on multiple occurrences, you MUST call this tool once per occurrence.

5. review_pull_request_tool
   - Reviews a GitHub pull request and posts review comments.
   - Args: {"repo": "repo_name", "pr_number": 123, "checklist": []}

6. get_confluence_page_content
   - Gets the FULL raw HTML storage content of a Confluence page.
   - Args: {"page_id": "..."}
   - Use this when you need the complete, untruncated page content (e.g., for extracting ALL key points).

7. update_confluence_page
   - Updates the ENTIRE content of a Confluence page (full replacement).
   - Args: {"page_id": "...", "title": "...", "content": "...", "version": 1, "message": "..."}
   - WARNING: This replaces the ENTIRE page body. If you pass incomplete content, the rest of the page will be LOST.
   - Only use this for full page rewrites. For text replacements, use find_and_replace_in_confluence_page instead.

8. find_and_replace_in_confluence_page
   - SAFELY replaces specific text in a Confluence page without losing any other content.
   - Args: {"page_id": "...", "find_text": "text to find", "replace_text": "replacement text", "replace_all": true}
   - This is the PREFERRED tool for replacing words or phrases. It automatically fetches the full page,
     performs the replacement, and saves it back — preserving all other content.
   - Use this instead of update_confluence_page when you need to replace specific text.

IMPORTANT: When extracting key points, summarizing, or analyzing a FULL page, check the "sections_returned" field
in the response. If the content seems incomplete or you need to ensure you have EVERYTHING, also call
get_confluence_page_content to get the full raw content.

WORKFLOW for "apply instructions from page A to page B":
  Step 1: get_page_content_by_sections_tool on page A to READ instructions
  Step 2: get_page_content_by_sections_tool on page B to READ target content
  Step 3: Analyze both. Decide what exact actions are needed.
  Step 4: Execute all required actions. If instructions say "comment on every occurrence of X", find ALL occurrences in the target page content and call post_confluence_inline_comment ONCE PER OCCURRENCE.
  Step 5: Verify ALL actions are done. Only then write FINAL_ANSWER.

AI-DRIVEN VERIFICATION (important):
You are responsible for verifying your own work. The system does NOT enforce hard-coded checks.
After every tool call, you must reason through the following yourself:

1. OCCURRENCE TRACKING: If the task requires commenting on every occurrence of a term,
   YOU must count the occurrences in the target text and track how many you have commented on.
   Do not stop until all occurrences are covered.

2. INSTRUCTION COMPLIANCE: If you are applying instructions from one page to another,
   YOU must read the instructions, derive what actions are required (review, inline comments,
   footer summary, page edits, etc.), and verify each requirement is met before finishing.

3. GRAMMAR-ONLY vs FULL REVIEW: If the user asks for a grammar-only review, decide on your own
   whether to limit the review scope. You do not need hard-coded flags — use your judgment
   based on the user's request.

4. FOOTER STRUCTURE: If the instructions require a footer summary with specific sections
   (e.g. severity breakdown, readability metrics, overall assessment), YOU must ensure
   your footer comment includes all required sections. Reason about what is needed from
   the instruction text — do not rely on a fixed template.

5. READABILITY ANALYSIS: If the instructions ask for readability metrics (Flesch Reading Ease,
   Flesch-Kincaid Grade Level, etc.), compute or estimate them yourself and include them
   in your output.

6. COMPLETION CHECK: Before writing FINAL_ANSWER, always ask yourself:
   - "Have I completed ALL required actions?"
   - "Are there remaining occurrences, items, or steps I missed?"
   - "Does my output satisfy every requirement from the instructions?"
   Only write FINAL_ANSWER when the answer to all three is YES.

CONTEXT-AWARE FLAGGING (critical — avoid false positives):
When reviewing text and considering whether a term is "context noise," "suspicious," or "out of place":

1. NEVER flag a term based on the word alone. Always read the FULL sentence or paragraph
   it appears in. A word like "metaphysical" is perfectly valid in philosophy, history,
   or social science contexts.

2. Before flagging any term, ask yourself:
   - "Is this term commonly used in the subject area of this document?"
   - "Does the sentence make sense with this term?"
   - "Would removing or replacing it improve clarity, or would it lose meaning?"
   If the term fits the context, do NOT flag it.

3. If user feedback is provided below, treat it as ground truth. If a user previously
   said a term is NOT noise in a given context, do not flag similar usage again.

4. When you DO flag a term, always explain WHY it seems out of place in that specific
   context — not just that the word is uncommon.
"""


# --- Tool Registry ---
# This dictionary maps tool names to functions that call them.
# It allows the agent to use different tools by name.

TOOL_REGISTRY = {
    "review_confluence_page_content": lambda args: mcp_client.call_tool("review_confluence_page_content", args),
    "get_page_content_by_sections_tool": lambda args: mcp_client.call_tool("get_page_content_by_sections_tool", args),
    "post_confluence_footer_comment": lambda args: mcp_client.call_tool("post_confluence_footer_comment", args),
    "post_confluence_inline_comment": lambda args: mcp_client.call_tool("post_confluence_inline_comment", args),
    "review_pull_request_tool": lambda args: mcp_client.call_tool("review_pull_request_tool", args),
    "get_confluence_page_content": lambda args: mcp_client.call_tool("get_confluence_page_content", args),
    "update_confluence_page": lambda args: mcp_client.call_tool("update_confluence_page", args),
    "find_and_replace_in_confluence_page": lambda args: mcp_client.call_tool("find_and_replace_in_confluence_page", args),
}




def _build_deterministic_execution_summary(user_msg: str, scratchpad: list) -> str:
    """Build a non-LLM summary based only on actual tool outputs."""
    total_calls = 0
    success_calls = 0
    failed_calls = 0
    inline_success = 0
    inline_failed = 0
    last_errors: list[str] = []

    for entry in scratchpad:
        tool = entry.get("tool")
        if tool == "verifier":
            continue
        total_calls += 1

        raw = entry.get("raw_result")
        if isinstance(raw, dict) and raw.get("success") is True:
            success_calls += 1
            if tool == "post_confluence_inline_comment":
                inline_success += 1
        else:
            failed_calls += 1
            if tool == "post_confluence_inline_comment":
                inline_failed += 1
            if isinstance(raw, dict):
                err = raw.get("error")
                if err:
                    last_errors.append(str(err))
            out = str(entry.get("output", ""))
            if "TOOL ERROR:" in out:
                last_errors.append(out)

    lines = []
    lines.append("Execution summary (from actual tool results):")
    lines.append(f"- Request: {user_msg}")
    lines.append(f"- Tool calls attempted: {total_calls}")
    lines.append(f"- Successful tool calls: {success_calls}")
    lines.append(f"- Failed tool calls: {failed_calls}")
    lines.append(f"- Inline comments successfully posted: {inline_success}")
    lines.append(f"- Inline comment failures: {inline_failed}")

    if inline_success == 0:
        lines.append("- Status: No inline comments were posted successfully.")
    else:
        lines.append("- Status: Some inline comments were posted; verify page for full coverage.")

    if last_errors:
        lines.append("- Recent errors:")
        for err in last_errors[-3:]:
            lines.append(f"  * {err}")

    lines.append("- Note: This summary is deterministic and does not rely on LLM-generated claims.")
    return "\n".join(lines)


def _build_agent_prompt(
    system_prompt: str,
    link_context: str,
    history_context: str,
    user_msg: str,
    scratchpad: list,
    step: int,
) -> str:
    """Build the full prompt for the LLM at each agent step."""

    scratchpad_text = ""
    if scratchpad:
        scratchpad_text = "\n\n--- AGENT SCRATCHPAD (tools called so far) ---\n"
        for i, entry in enumerate(scratchpad):
            scratchpad_text += f"\n[Step {i+1}] Tool: {entry['tool']}\n"
            scratchpad_text += f"Input: {json.dumps(entry['input'], default=str)}\n"
            output_str = str(entry["output"])
            if len(output_str) > 4000:
                output_str = output_str[:4000] + "\n... (truncated for brevity)"
            scratchpad_text += f"Observation: {output_str}\n"
        scratchpad_text += "--- END SCRATCHPAD ---\n"

    verification_reminder = ""
    if scratchpad:
        verification_reminder = (
            "\n\nVERIFICATION REQUIRED: Review your scratchpad above. "
            "Have you completed ALL required actions? Are there any remaining occurrences, items, or steps? "
            "If the task is NOT fully done, continue with more tool calls. "
            "Only write FINAL_ANSWER when everything is complete."
        )

    # Load user feedback so the AI can learn from past false positives
    feedback_context = _load_recent_feedback()

    return (
        system_prompt + "\n\n"
        + link_context
        + history_context
        + feedback_context
        + scratchpad_text
        + f"\nUser request: {user_msg}\n"
        + verification_reminder
        + f"\n\n[Agent step {step + 1}] What do you do next?"
    )


def run_agent(user_msg: str, history: list, link_context: str) -> str:
    """
    ReAct + Verify-Then-Continue agent loop.
    LLM -> Tool -> Observation -> Verify -> LLM -> ... -> FINAL_ANSWER
    """
    scratchpad: list = []

    history_context = ""
    if history:
        recent = history[-20:]
        parts = []
        for entry in recent:
            role = "User" if entry.get("role") == "user" else "Assistant"
            parts.append(f"{role}: {entry.get('text', '')}")
        history_context = "Conversation history:\n" + "\n".join(parts) + "\n\n"

    for step in range(MAX_AGENT_STEPS):
        print(f"[AGENT] Step {step + 1}/{MAX_AGENT_STEPS}", flush=True)

        prompt = _build_agent_prompt(
            AGENT_SYSTEM_PROMPT,
            link_context,
            history_context,
            user_msg,
            scratchpad,
            step,
        )

        response, err = call_llm(prompt)
        print(f"[AGENT] LLM response (step {step + 1}): {response[:600]}", flush=True)

        if err:
            return f"Sorry, I encountered an error: {err}"

        if not response.strip():
            return "Sorry, I received an empty response. Please try again."

        # --- Check for FINAL_ANSWER ---
        final_match = re.search(r"FINAL_ANSWER:\s*(.+)", response, re.DOTALL)
        if final_match:
            final_answer = final_match.group(1).strip()
            print(f"[AGENT] Final answer at step {step + 1}", flush=True)
            return final_answer

        # --- Check for TOOL_CALL ---
        tool_match = re.search(r"TOOL_CALL:\s*(.+?)\s*(?:\n|$)", response)
        if not tool_match:
            # No TOOL_CALL and no FINAL_ANSWER — strip THOUGHT prefix and return
            cleaned = re.sub(r"^THOUGHT:.*?\n", "", response, count=1, flags=re.DOTALL).strip()
            return cleaned if cleaned else response.strip()

        tool_name = tool_match.group(1).strip()

        # --- Parse ARGS ---
        args_match = re.search(r"ARGS:\s*(\{.+?\})\s*(?:\n|$)", response, re.DOTALL)
        args: dict = {}
        if args_match:
            try:
                args = json.loads(args_match.group(1))
            except json.JSONDecodeError as e:
                observation = f"ERROR: Could not parse ARGS as JSON: {e}. Raw: {args_match.group(1)}"
                scratchpad.append({"tool": tool_name, "input": args_match.group(1), "output": observation})
                continue

        # --- Validate tool ---
        if tool_name not in TOOL_REGISTRY:
            observation = f"ERROR: Unknown tool '{tool_name}'. Available: {', '.join(TOOL_REGISTRY.keys())}"
            scratchpad.append({"tool": tool_name, "input": args, "output": observation})
            continue

        print(f"[AGENT] Calling tool: {tool_name} | args: {json.dumps(args, default=str)[:300]}", flush=True)

        # --- Execute tool ---
        if tool_name == "post_confluence_inline_comment" and args.get("page_id") and args.get("text_selection"):
            # Prevent repeated comments on match_index=0 by auto-advancing match_index.
            page_id = str(args.get("page_id"))
            text_selection = str(args.get("text_selection"))
            # Count how many successful inline comments were already posted for this selection
            already_done = sum(
                1 for entry in scratchpad
                if entry.get("tool") == "post_confluence_inline_comment"
                and str(entry.get("input", {}).get("page_id", "")) == page_id
                and str(entry.get("input", {}).get("text_selection", "")).strip().lower() == text_selection.strip().lower()
                and isinstance(entry.get("raw_result"), dict) and entry.get("raw_result", {}).get("success") is True
            )

            first_args = dict(args)
            if "match_index" not in first_args:
                first_args["match_index"] = already_done

            batch_limit = 30
            batch_results = []

            first_result = TOOL_REGISTRY[tool_name](first_args)
            print(f"[AGENT] Tool result: {str(first_result)[:400]}", flush=True)
            batch_results.append((first_args, first_result))

            total_occurrences = None
            if isinstance(first_result, dict) and first_result.get("success"):
                data = first_result.get("data", {})
                if isinstance(data, dict):
                    total_occurrences = data.get("occurrences_found")

            if isinstance(total_occurrences, int):
                start_idx = int(first_args.get("match_index", 0))
                end_exclusive = min(total_occurrences, start_idx + batch_limit)
                for idx in range(start_idx + 1, end_exclusive):
                    next_args = dict(args)
                    next_args["match_index"] = idx
                    next_result = TOOL_REGISTRY[tool_name](next_args)
                    print(f"[AGENT] Tool result (idx={idx}): {str(next_result)[:220]}", flush=True)
                    batch_results.append((next_args, next_result))

            for call_args, call_result in batch_results:
                if isinstance(call_result, dict):
                    if call_result.get("success"):
                        observation = format_result(call_result.get("data", {}))
                    else:
                        observation = f"TOOL ERROR: {call_result.get('error', 'Unknown error')}"
                else:
                    observation = format_result(call_result)

                scratchpad.append({
                    "tool": tool_name,
                    "input": call_args,
                    "output": observation,
                    "raw_result": call_result,
                })

            if isinstance(total_occurrences, int):
                # Count how many successful inline comments have been posted so far
                done_now = sum(
                    1 for entry in scratchpad
                    if entry.get("tool") == "post_confluence_inline_comment"
                    and str(entry.get("input", {}).get("page_id", "")) == page_id
                    and str(entry.get("input", {}).get("text_selection", "")).strip().lower() == text_selection.strip().lower()
                    and isinstance(entry.get("raw_result"), dict) and entry.get("raw_result", {}).get("success") is True
                )
                remaining = max(total_occurrences - done_now, 0)
                scratchpad.append({
                    "tool": "verifier",
                    "input": {"page_id": page_id, "text_selection": text_selection},
                    "output": f"Batch progress: total_occurrences={total_occurrences}, completed={done_now}, remaining={remaining}. Continue if remaining > 0.",
                    "raw_result": {"success": True},
                })

            continue

        result = TOOL_REGISTRY[tool_name](args)
        print(f"[AGENT] Tool result: {str(result)[:400]}", flush=True)

        # --- Format observation ---
        if isinstance(result, dict):
            if result.get("success"):
                observation = format_result(result.get("data", {}))
            else:
                observation = f"TOOL ERROR: {result.get('error', 'Unknown error')}"
        else:
            observation = format_result(result)

        scratchpad.append({
            "tool": tool_name,
            "input": args,
            "output": observation,
            "raw_result": result,
        })
    # --- MAX_STEPS reached: return deterministic execution report ---
    print(f"[AGENT] Max steps ({MAX_AGENT_STEPS}) reached. Returning deterministic execution summary.", flush=True)

    if scratchpad:
        return _build_deterministic_execution_summary(user_msg, scratchpad)

    return "No tool actions were executed before reaching the step limit."


# --- Chat API Endpoint ---
# This endpoint receives chat messages from the frontend, runs the agent, and returns the response.

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_msg = data.get("prompt", "").strip()
    history = data.get("history", [])
    if not user_msg:
        return jsonify({"error": "Empty prompt"}), 400

    # Extract detected links from the current prompt first
    page_ids = _extract_confluence_page_ids_from_text(user_msg)
    prs = _extract_prs_from_text(user_msg)
    link_source = "current_message"

    # If follow-up prompt has no links, reuse the most recent linked user message.
    if not page_ids and not prs:
        hist_page_ids, hist_prs = _fallback_links_from_history(history)
        if hist_page_ids or hist_prs:
            page_ids = hist_page_ids
            prs = hist_prs
            link_source = "history_fallback"

    detected = [f"confluence:{pid}" for pid in page_ids]
    detected += [f"pr:{p['owner']}/{p['repo']}#{p['pr_number']}" for p in prs]

    # Build link context
    link_context = ""
    if page_ids:
        if link_source == "history_fallback":
            link_context += "Detected Confluence page IDs from recent conversation history (follow-up context):\n"
        else:
            link_context += "Detected Confluence page IDs from the user's message:\n"
        for i, pid in enumerate(page_ids):
            link_context += f"  Link {i+1}: page_id = \"{pid}\"\n"
        link_context += "\n"
    if prs:
        if link_source == "history_fallback":
            link_context += "Detected GitHub PRs from recent conversation history (follow-up context):\n"
        else:
            link_context += "Detected GitHub PRs from the user's message:\n"
        for p in prs:
            owner, repo, pr_num = p["owner"], p["repo"], p["pr_number"]
            link_context += f"  PR: {owner}/{repo}#{pr_num}\n"
        link_context += "\n"

    # Run the agent loop
    try:
        response = run_agent(user_msg, history, link_context)
    except Exception as e:
        print(f"[AGENT] Unhandled error: {e}", flush=True)
        return jsonify({"error": "Something went wrong. Please try again.", "detected": detected}), 500

    return jsonify({"response": response, "detected": detected if detected else None})

# --- Feedback API Endpoint ---
# Lets users report false positives (e.g. a term wrongly flagged as context noise).
# The feedback is stored in a JSON file and loaded into the AI's context on future requests.

@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    data = request.get_json(silent=True) or {}
    term = data.get("term", "").strip()
    sentence = data.get("sentence", "").strip()
    feedback = data.get("feedback", "").strip()  # e.g. "not_noise", "valid_term", "false_positive"

    if not term or not feedback:
        return jsonify({"error": "Both 'term' and 'feedback' are required."}), 400

    entry = {
        "term": term,
        "sentence": sentence,
        "feedback": feedback,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        return jsonify({"error": f"Could not save feedback: {e}"}), 500

    return jsonify({"status": "ok", "message": f"Feedback recorded for term '{term}'."})


# --- Shutdown Handler ---
# Ensures the MCP client is properly shut down when the server stops.

atexit.register(lambda: mcp_client.shutdown())

# --- Main Entry Point ---
# Starts the Flask server if this file is run directly.

if __name__ == "__main__":
    app.run(debug=True, port=5000)
