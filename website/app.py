import sys
import os
import re
import json
import subprocess
import threading
import atexit

from flask import Flask, render_template, request, jsonify
import requests

MCP_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MCP_SERVER_SCRIPT = os.path.join(MCP_SERVER_DIR, "mcp_tools.py")

app = Flask(__name__)

COPILOT_BRIDGE_URL = "http://127.0.0.1:5100/api/prompt"


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
                stderr=None,  # Inherit stderr so MCP/review logs appear in Flask terminal
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

        import time
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
                    # Skip notifications (no "id" field) — we only want our response
                    if "id" not in parsed:
                        print(f"[MCP] notification: {parsed.get('method', '?')}", flush=True)
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


# -- Copilot Bridge ------------------------------------------------------

def send_to_copilot(prompt: str) -> tuple[str, str | None]:
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




# -- Tool Descriptions for Copilot ------------------------------------

AVAILABLE_TOOLS = """
You have access to the following tools. When the user's request requires using a tool, respond with EXACTLY this format:
TOOL_CALL: <tool_name>
ARGS: <json_arguments>

Available tools:

1. review_confluence_page_content
   - Reviews a Confluence page for grammar, structure, readability, etc. and posts inline comments + footer summary directly on the page.
   - Args: {"page_id": "...", "checklist_page_id": "(optional) page ID with custom review instructions"}
   - Use when: user wants to review a Confluence page, post comments, check quality, apply a checklist, etc.

2. get_page_content_by_sections_tool
   - Fetches the content of a Confluence page (read-only, does NOT post anything).
   - Args: {"page_id": "..."}
   - Use when: user wants to read, summarize, or ask questions about a Confluence page.

3. post_confluence_footer_comment
   - Posts a single footer comment on a Confluence page.
   - Args: {"page_id": "...", "comment": "..."}
   - Use when: user wants to post a specific comment on a page footer.

4. post_confluence_inline_comment
   - Posts an inline comment on specific text within a Confluence page.
   - Args: {"page_id": "...", "comment": "...", "text_selection": "exact text to attach comment to"}
   - Use when: user wants to comment on a specific part of a page.

5. review_pull_request_tool
   - Reviews a GitHub pull request and posts review comments.
   - Args: {"repo": "repo_name", "pr_number": 123, "checklist": []}
   - Use when: user wants to review a GitHub PR.

6. get_confluence_page_content
   - Gets raw Confluence page content.
   - Args: {"page_id": "..."}
   - Use when: user needs the full raw content of a page.

7. update_confluence_page
   - Updates the content of a Confluence page.
   - Args: {"page_id": "...", "title": "...", "content": "...", "version": 1, "message": "..."}
   - Use when: user wants to edit/update a Confluence page.

IMPORTANT PATTERNS:
- If the user provides TWO Confluence links and says to "follow instructions" or "apply instructions" from one to the other, use review_confluence_page_content with the instruction page as checklist_page_id and the other as page_id.
- If the user provides ONE Confluence link and asks to review/check it, use review_confluence_page_content with just page_id.
- If the user provides a link and asks to read/summarize/explain it, use get_page_content_by_sections_tool.
- Always use a tool when the user provides a link. Do NOT ask for clarification if you can infer the intent.

If the user's request does NOT require any tool (e.g. general questions, greetings), just respond normally without TOOL_CALL.
"""


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_msg = data.get("prompt", "").strip()
    history = data.get("history", [])
    if not user_msg:
        return jsonify({"error": "Empty prompt"}), 400

    # Extract any detected links for badge display
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

    # Build prompt with tool descriptions and conversation history
    history_context = ""
    if history:
        recent = history[-20:]
        parts = []
        for entry in recent:
            role = "User" if entry.get("role") == "user" else "Assistant"
            parts.append(f"{role}: {entry.get('text', '')}")
        history_context = "Conversation history:\n" + "\n".join(parts) + "\n\n"

    # Build context about detected links so Copilot knows the page IDs
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

    prompt = (
        AVAILABLE_TOOLS + "\n\n"
        + link_context
        + history_context
        + f"User: {user_msg}\n\n"
        + "If this requires a tool, respond with TOOL_CALL and ARGS. Otherwise respond normally."
    )

    response, err = send_to_copilot(prompt)
    if err:
        return jsonify({"error": err, "detected": detected}), 502

    # Check if Copilot wants to call a tool
    tool_result = _parse_and_execute_tool_call(response)
    if tool_result is not None:
        return jsonify({"response": tool_result, "detected": detected})

    # No tool call — return Copilot's direct response
    return jsonify({"response": response, "detected": detected if detected else None})


def _format_confluence_review(page_id: str, data: dict) -> str:
    """Format review results into a user-friendly summary."""
    issues_found = data.get("issues_found", 0)
    severity = data.get("severity_breakdown", {})
    errors = severity.get("errors", 0)
    warnings = severity.get("warnings", 0)
    info = severity.get("info", 0)
    readability = data.get("readability") or {}
    flesch = readability.get("flesch_ease", "N/A")
    grade = readability.get("fk_grade", "N/A")
    comments_posted = data.get("comments_posted", 0)
    footer_posted = data.get("footer_posted", False)
    executed = data.get("executed_checks", [])
    skipped = data.get("skipped_checks", [])
    doc_type = data.get("document_type", "unknown")

    lines = []
    lines.append(f"Review completed for Confluence page {page_id}.")
    lines.append("")
    lines.append(f"Document Type: {doc_type.title()}")
    lines.append("")
    lines.append(f"Issues Found: {issues_found}")
    lines.append(f"  - Errors: {errors}")
    lines.append(f"  - Warnings: {warnings}")
    lines.append(f"  - Info: {info}")
    lines.append("")
    lines.append(f"Readability: Flesch Ease {flesch}, Grade Level {grade}")
    lines.append("")
    lines.append(f"Comments Posted: {comments_posted} inline")
    footer_status = "Posted" if footer_posted else "Not posted (may retry on next run)"
    lines.append(f"Footer Summary: {footer_status}")
    lines.append("")
    checks_str = ", ".join(executed) if executed else "None"
    lines.append(f"Checks Executed: {checks_str}")
    if skipped:
        skipped_parts = []
        for s in skipped:
            if isinstance(s, dict):
                skipped_parts.append(f"{s.get('id', '?')} ({s.get('reason', '?')})")
        if skipped_parts:
            lines.append(f"Checks Skipped: {', '.join(skipped_parts)}")

    issues = data.get("issues", [])
    if issues:
        lines.append("")
        lines.append("Top Issues:")
        for issue in issues[:5]:
            sev = issue.get("severity", "info").upper()
            msg = issue.get("message", "")
            lines.append(f"  [{sev}] {msg}")
        if len(issues) > 5:
            lines.append(f"  ... and {len(issues) - 5} more issues (see inline comments on page)")

    return "\n".join(lines)


def _parse_and_execute_tool_call(response: str) -> str | None:
    """Parse Copilot's response for a TOOL_CALL and execute it if found."""
    if "TOOL_CALL:" not in response:
        return None

    try:
        # Extract tool name
        tool_match = re.search(r"TOOL_CALL:\s*(.+?)\s*(?:\n|$)", response)
        if not tool_match:
            return None
        tool_name = tool_match.group(1).strip()

        # Extract args
        args_match = re.search(r"ARGS:\s*({.+?})\s*(?:\n|$)", response, re.DOTALL)
        if not args_match:
            args = {}
        else:
            args = json.loads(args_match.group(1))

        print(f"[TOOL_CALL] Tool: {tool_name}, Args: {args}", flush=True)

        # Execute the tool
        result = mcp_client.call_tool(tool_name, args)
        print(f"[TOOL_CALL] Result: {str(result)[:300]}", flush=True)

        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            print(f"[TOOL_CALL] Failed: {error_msg}", flush=True)
            return "Sorry, something went wrong while processing your request. Please try again."

        data = result.get("data", {})

        # Format based on tool type
        if tool_name == "review_confluence_page_content":
            return _format_confluence_review(args.get("page_id", ""), data)
        elif tool_name == "review_pull_request_tool":
            pr_summary = format_result(data)
            followup = f"Here are the PR review results:\n\n{pr_summary}\n\nSummarize these results in a clear, human-friendly way. List key findings, issues, and suggestions. Do NOT output raw JSON."
            followup_response, followup_err = send_to_copilot(followup)
            if followup_err:
                return "PR review completed but I couldn't generate a summary. Please check the PR page for inline comments."
            return followup_response
        elif tool_name in ("get_page_content_by_sections_tool", "get_confluence_page_content"):
            # Send fetched content back to Copilot for summarization/answering
            page_text = format_result(data)
            followup = f"Here is the Confluence page content:\n\n{page_text}\n\nNow answer the user\'s original request about this content."
            followup_response, followup_err = send_to_copilot(followup)
            if followup_err:
                return "I fetched the page content but couldn't process it. Please try again."
            return followup_response
        else:
            raw = format_result(data)
            followup = f"Here is the tool result:\n\n{raw}\n\nPresent this information to the user in a clear, readable way. Do NOT output raw JSON or code blocks."
            followup_response, followup_err = send_to_copilot(followup)
            if followup_err:
                return "The action was completed successfully."
            return followup_response

    except Exception as e:
        print(f"[TOOL_CALL] Error: {e}", flush=True)
        return "Sorry, something went wrong while processing your request. Please try again."


atexit.register(lambda: mcp_client.shutdown())

if __name__ == "__main__":
    app.run(debug=True, port=5000)
