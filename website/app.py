import sys
import os
import re
import json
import subprocess
import threading
import atexit
import time

from flask import Flask, render_template, request, jsonify
import requests

MCP_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MCP_SERVER_SCRIPT = os.path.join(MCP_SERVER_DIR, "mcp_tools.py")

app = Flask(__name__)

COPILOT_BRIDGE_URL = "http://127.0.0.1:5100/api/prompt"

MAX_AGENT_STEPS = 20  # Raised — acts as safety net, not a kill switch


# -- MCP Client (stdio JSON-RPC) ----------------------------------------

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


mcp_client = MCPClient()


# -- Copilot Bridge (LLM) ------------------------------------------------

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


# -- Helpers -------------------------------------------------------------

def extract_confluence_page_id(url: str) -> str | None:
    m = re.search(r"/pages/(\d+)", url)
    return m.group(1) if m else None


def extract_pr_info(url: str) -> dict | None:
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if m:
        return {"owner": m.group(1), "repo": m.group(2), "pr_number": int(m.group(3))}
    return None


def format_result(data) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)


# -- Routes --------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# -- Agent System Prompt -------------------------------------------------

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
   - Fetches the content of a Confluence page (read-only, does NOT post anything).
   - Args: {"page_id": "..."}
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
   - Gets raw Confluence page content.
   - Args: {"page_id": "..."}

7. update_confluence_page
   - Updates the content of a Confluence page.
   - Args: {"page_id": "...", "title": "...", "content": "...", "version": 1, "message": "..."}

WORKFLOW for "apply instructions from page A to page B":
  Step 1: get_page_content_by_sections_tool on page A to READ instructions
  Step 2: get_page_content_by_sections_tool on page B to READ target content
  Step 3: Analyze both. Decide what exact actions are needed.
  Step 4: Execute all required actions. If instructions say "comment on every occurrence of X", find ALL occurrences in the target page content and call post_confluence_inline_comment ONCE PER OCCURRENCE.
  Step 5: Verify ALL actions are done. Only then write FINAL_ANSWER.
"""


# -- Tool Registry --------------------------------------------------------

TOOL_REGISTRY = {
    "review_confluence_page_content": lambda args: mcp_client.call_tool("review_confluence_page_content", args),
    "get_page_content_by_sections_tool": lambda args: mcp_client.call_tool("get_page_content_by_sections_tool", args),
    "post_confluence_footer_comment": lambda args: mcp_client.call_tool("post_confluence_footer_comment", args),
    "post_confluence_inline_comment": lambda args: mcp_client.call_tool("post_confluence_inline_comment", args),
    "review_pull_request_tool": lambda args: mcp_client.call_tool("review_pull_request_tool", args),
    "get_confluence_page_content": lambda args: mcp_client.call_tool("get_confluence_page_content", args),
    "update_confluence_page": lambda args: mcp_client.call_tool("update_confluence_page", args),
}




def _extract_page_ids_from_link_context(link_context: str) -> list[str]:
    ids = re.findall(r'page_id\s*=\s*"(\d+)"', link_context)
    return ids


def _get_latest_page_content_observation(scratchpad: list, page_id: str) -> str:
    for entry in reversed(scratchpad):
        if entry.get("tool") not in ("get_page_content_by_sections_tool", "get_confluence_page_content"):
            continue
        inp = entry.get("input") or {}
        if str(inp.get("page_id", "")) != str(page_id):
            continue
        return str(entry.get("output", ""))
    return ""


def _infer_occurrence_term(instruction_text: str) -> str:
    patterns = [
        r"(?:word|term)\s*[\"']([^\"']+)[\"']",
        r"occurrence(?:s)?\s+of\s+[\"']([^\"']+)[\"']",
        r"every\s+occurrence\s+of\s+([A-Za-z0-9_-]+)",
        r"each\s+occurrence\s+of\s+([A-Za-z0-9_-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, instruction_text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _count_term_occurrences(text: str, term: str) -> int:
    if not text or not term:
        return 0
    term_escaped = re.escape(term)
    if re.match(r'^[A-Za-z0-9_]+$', term):
        pat = rf'\b{term_escaped}\b'
    else:
        pat = term_escaped
    return len(re.findall(pat, text, flags=re.IGNORECASE))


def _count_successful_inline_comments_for_term(scratchpad: list, page_id: str, term: str) -> int:
    count = 0
    for entry in scratchpad:
        if entry.get("tool") != "post_confluence_inline_comment":
            continue
        inp = entry.get("input") or {}
        if str(inp.get("page_id", "")) != str(page_id):
            continue
        sel = str(inp.get("text_selection", "")).strip().lower()
        if sel != term.strip().lower():
            continue

        raw_result = entry.get("raw_result")
        if isinstance(raw_result, dict) and raw_result.get("success") is True:
            out_text = str(entry.get("output", "")).lower()
            if "already exists" in out_text or "duplicate" in out_text:
                continue
            count += 1
    return count




def _count_successful_inline_comments_for_selection(scratchpad: list, page_id: str, text_selection: str) -> int:
    count = 0
    for entry in scratchpad:
        if entry.get("tool") != "post_confluence_inline_comment":
            continue
        inp = entry.get("input") or {}
        if str(inp.get("page_id", "")) != str(page_id):
            continue
        sel = str(inp.get("text_selection", "")).strip().lower()
        if sel != str(text_selection).strip().lower():
            continue
        raw_result = entry.get("raw_result")
        if isinstance(raw_result, dict) and raw_result.get("success") is True:
            count += 1
    return count

def _needs_occurrence_completion(user_msg: str, scratchpad: list, link_context: str) -> tuple[bool, str]:
    page_ids = _extract_page_ids_from_link_context(link_context)
    if len(page_ids) < 2:
        return False, ""

    instructions_page_id = page_ids[0]
    target_page_id = page_ids[1]

    instructions_text = _get_latest_page_content_observation(scratchpad, instructions_page_id)
    if not instructions_text:
        return False, ""

    occurrence_signal = bool(re.search(r'each\s+occurrence|every\s+occurrence|all\s+occurrences', instructions_text, re.IGNORECASE))
    if not occurrence_signal:
        return False, ""

    term = _infer_occurrence_term(instructions_text)
    if not term:
        return False, ""

    target_text = _get_latest_page_content_observation(scratchpad, target_page_id)
    if not target_text:
        return False, ""

    expected = _count_term_occurrences(target_text, term)
    if expected <= 0:
        return False, ""

    completed = _count_successful_inline_comments_for_term(scratchpad, target_page_id, term)
    if completed < expected:
        remaining = expected - completed
        msg = (
            f"VERIFICATION BLOCK: Incomplete multi-occurrence task. "
            f"Instructions require comments for every occurrence of '{term}'. "
            f"Expected {expected} based on target content, but only {completed} successful inline comment calls were made. "
            f"Continue with post_confluence_inline_comment for the remaining {remaining} occurrences before FINAL_ANSWER."
        )
        return True, msg

    return False, ""

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


def _is_grammar_only_request(user_msg: str) -> bool:
    text = (user_msg or "").lower()
    has_grammar = "grammar" in text
    has_review = "review" in text or "check" in text
    broad_terms = [
        "readability", "structure", "duplicate", "citation", "table", "all checks", "full review",
        "everything", "comprehensive", "long sentence", "long paragraph", "repeated word", "context noise"
    ]
    asks_broad = any(term in text for term in broad_terms)
    return has_grammar and has_review and not asks_broad


# -- Agent Loop (ReAct + Verify-Then-Continue) ----------------------------

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

    return (
        system_prompt + "\n\n"
        + link_context
        + history_context
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
            needs_more, verify_msg = _needs_occurrence_completion(user_msg, scratchpad, link_context)
            if needs_more:
                print("[AGENT] Completion gate blocked premature FINAL_ANSWER.", flush=True)
                scratchpad.append({"tool": "verifier", "input": {}, "output": verify_msg, "raw_result": {"success": False}})
                continue

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

        if tool_name == "review_confluence_page_content" and _is_grammar_only_request(user_msg):
            # Force grammar-only checklist unless user explicitly provided a checklist page.
            if not args.get("checklist_page_id"):
                args["checklist_page_id"] = "__GRAMMAR_ONLY__"

        print(f"[AGENT] Calling tool: {tool_name} | args: {json.dumps(args, default=str)[:300]}", flush=True)

        # --- Execute tool ---
        if tool_name == "post_confluence_inline_comment" and args.get("page_id") and args.get("text_selection"):
            # Prevent repeated comments on match_index=0 by auto-advancing match_index.
            page_id = str(args.get("page_id"))
            text_selection = str(args.get("text_selection"))
            already_done = _count_successful_inline_comments_for_selection(scratchpad, page_id, text_selection)

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
                done_now = _count_successful_inline_comments_for_selection(scratchpad, page_id, text_selection)
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


# -- Chat Route -----------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_msg = data.get("prompt", "").strip()
    history = data.get("history", [])
    if not user_msg:
        return jsonify({"error": "Empty prompt"}), 400

    # Extract detected links for badge display
    conf_urls = re.findall(r"https?://[^\s]*atlassian\.net/wiki[^\s]*", user_msg)
    page_ids: list[str] = []
    for url in conf_urls:
        pid = extract_confluence_page_id(url)
        if pid and pid not in page_ids:
            page_ids.append(pid)

    pr_urls = re.findall(r"https?://github\.com/[^\s]+/pull/\d+", user_msg)
    prs: list[dict] = []
    for url in pr_urls:
        info = extract_pr_info(url)
        if info:
            prs.append(info)

    detected = [f"confluence:{pid}" for pid in page_ids]
    detected += [f"pr:{p['owner']}/{p['repo']}#{p['pr_number']}" for p in prs]

    # Build link context
    link_context = ""
    if page_ids:
        link_context += "Detected Confluence page IDs from the user's message:\n"
        for i, pid in enumerate(page_ids):
            link_context += f"  Link {i+1}: page_id = \"{pid}\"\n"
        link_context += "\n"
    if prs:
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


atexit.register(lambda: mcp_client.shutdown())

if __name__ == "__main__":
    app.run(debug=True, port=5000)
