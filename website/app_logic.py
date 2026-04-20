# app.py
# This is the main backend server file for the web application.
# It uses Flask (a Python web framework) to provide API endpoints and web pages.
# The backend also acts as a bridge between the web UI, a local AI agent, and external tools.
import sys
import os
import re
import json
import subprocess
import threading
import time
from flask import Flask
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

_USER_AUTH_LOCAL = threading.local()


def normalize_user_auth(raw: dict | None) -> dict:
    """Return a sanitized per-user auth payload used for runtime API credentials."""
    if not isinstance(raw, dict):
        return {}
    key_map = {
        "confluence_email": "confluence_email",
        "confluence_api_token": "confluence_api_token",
        "confluence_base_url": "confluence_base_url",
        "github_owner": "github_owner",
        "github_token": "github_token",
        "github_base_url": "github_base_url",
    }
    out: dict[str, str] = {}
    for key, out_key in key_map.items():
        val = raw.get(key)
        if isinstance(val, str):
            val = val.strip()
            if val:
                out[out_key] = val
    return out


def set_active_user_auth(raw: dict | None) -> None:
    """Bind user-provided credentials to the current thread/request context."""
    _USER_AUTH_LOCAL.value = normalize_user_auth(raw)


def get_active_user_auth() -> dict:
    """Get current thread/request credentials for tool execution."""
    value = getattr(_USER_AUTH_LOCAL, "value", {})
    return value if isinstance(value, dict) else {}


def clear_active_user_auth() -> None:
    """Clear request-scoped credentials after the request completes."""
    _USER_AUTH_LOCAL.value = {}
def _step_budget_for_request(user_msg: str) -> int:
    """Return a smaller step budget for lightweight requests to reduce latency."""
    text = (user_msg or "").lower()
    fast_keywords = ["spell", "spelling", "grammar", "typo", "inline comment"]
    if any(keyword in text for keyword in fast_keywords):
        return min(8, MAX_AGENT_STEPS)
    return MAX_AGENT_STEPS
# --- MCP Client Class ---
# This class manages communication with the FastMCP server (an external tool).
# It starts the server if needed, sends requests, and receives responses.
class MCPClient:
    """Communicates with the FastMCP server over stdio using JSON-RPC."""
    def __init__(self):
        """Initializes the MCP client with no active process or state."""
        self.process: subprocess.Popen[bytes] | None = None
        self.lock = threading.Lock()
        self._request_id = 0
        self._initialized = False
        self.stderr_lines: list[str] = []  # Shared buffer for MCP server stderr
        self._stderr_thread: threading.Thread | None = None
    def _ensure_started(self):
        """Starts the MCP server process if not running and performs protocol initialization."""
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
                stderr=subprocess.PIPE,
                cwd=MCP_SERVER_DIR,
                env=env,
            )
            assert self.process.stdin is not None
            assert self.process.stdout is not None
            self._initialized = False
            # Start a thread to continuously read stderr for [REVIEW] messages
            self.stderr_lines = []
            def _read_stderr():
                proc = self.process
                if proc and proc.stderr:
                    for raw_line in iter(proc.stderr.readline, b""):
                        text = raw_line.decode("utf-8", errors="replace").rstrip()
                        if text:
                            self.stderr_lines.append(text)
            self._stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            self._stderr_thread.start()
        if not self._initialized:
            self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "flask-bridge", "version": "1.0.0"},
            })
            self._send_notification("notifications/initialized", {})
            self._initialized = True
    def _next_id(self):
        """Returns the next unique auto-incremented request ID."""
        self._request_id += 1
        return self._request_id
    def _send_request(self, method, params, timeout=None):
        """Sends a JSON-RPC request to the MCP server and returns the response."""
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        if timeout is None:
            return self._send_and_receive(msg)
        return self._send_and_receive(msg, timeout=timeout)
    def _send_notification(self, method, params):
        """Sends a one-way JSON-RPC notification to the MCP server (no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        raw = json.dumps(msg) + "\n"
        proc = self.process
        if proc is None or proc.stdin is None:
            raise RuntimeError("MCP server not started")
        proc.stdin.write(raw.encode("utf-8"))
        proc.stdin.flush()
    def _send_and_receive(self, msg, timeout=120):
        """Writes a JSON-RPC message to the MCP process stdin and waits for the matching response."""
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
            """Reads lines from the MCP server stdout until a JSON-RPC response with a matching ID is found."""
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
            # Collect recent stderr lines from background reader
            recent_stderr = "\n".join(self.stderr_lines[-10:]) if self.stderr_lines else "(empty)"
            raise TimeoutError(
                f"MCP server did not respond within {timeout}s. stderr: {recent_stderr}"
            )
        if isinstance(result, Exception):
            raise result
        elapsed = time.time() - start
        print(f"[MCP] Response received in {elapsed:.1f}s", flush=True)
        return result
    def call_tool(self, tool_name, arguments, runtime_auth=None):
        """Calls a named tool on the MCP server and returns the parsed result."""
        with self.lock:
            try:
                print(f"[MCP] call_tool: {tool_name}", flush=True)
                self._ensure_started()
                if runtime_auth and tool_name != "set_runtime_auth_tool":
                    self._send_request(
                        "tools/call",
                        {"name": "set_runtime_auth_tool", "arguments": runtime_auth},
                        timeout=60,
                    )
                result = self._send_request("tools/call", {"name": tool_name, "arguments": arguments}, timeout=600)
                print(f"[MCP] call_tool result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}", flush=True)
                if "error" in result:
                    return {"success": False, "error": result["error"].get("message", str(result["error"]))}
                # Check if MCP flagged this as an error response
                mcp_result = result.get("result", {})
                if mcp_result.get("isError"):
                    err_text = ""
                    for item in mcp_result.get("content", []):
                        err_text += item.get("text", "")
                    return {"success": False, "error": err_text or "MCP tool returned an error"}
                tool_content = mcp_result.get("content", [])
                if tool_content:
                    text = tool_content[0].get("text", "")
                    # Detect FastMCP error wrapper (exception caught by framework, returned as text)
                    if text.startswith("Error calling tool "):
                        return {"success": False, "error": text}
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"success": True, "data": text}
                return {"success": True, "data": result.get("result")}
            except Exception as e:
                print(f"[MCP] call_tool error: {e}", flush=True)
                return {"success": False, "error": str(e)}
    def list_tools(self):
        """Retrieves the list of available tools from the MCP server."""
        with self.lock:
            self._ensure_started()
            result = self._send_request("tools/list", {})
            return result.get("result", {}).get("tools", [])
    def shutdown(self):
        """Gracefully closes the MCP server process."""
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
        resp = requests.post(COPILOT_BRIDGE_URL, json={"prompt": prompt}, timeout=90)
        data = resp.json()
        if "error" in data:
            return "", f"Bridge error: {data['error']}"
        model = data.get("model")
        family = data.get("family")
        profile = data.get("profile")
        latency_ms = data.get("latency_ms")
        if model or family or profile or latency_ms is not None:
            print(
                "[LLM] model={} family={} profile={} latency_ms={}".format(
                    model or "unknown",
                    family or "unknown",
                    profile or "unknown",
                    latency_ms if latency_ms is not None else "unknown",
                ),
                flush=True,
            )
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
    """Extracts a numeric Confluence page ID from a URL string."""
    m = re.search(r"/pages/(\d+)", url)
    return m.group(1) if m else None
def extract_pr_info(url: str) -> dict | None:
    """Parses a GitHub pull request URL and returns the owner, repo, and PR number."""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if m:
        return {"owner": m.group(1), "repo": m.group(2), "pr_number": int(m.group(3))}
    return None
def _extract_confluence_page_ids_from_text(text: str) -> list[str]:
    """Scans text for Atlassian Confluence URLs and returns a list of unique page IDs."""
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
    """Scans text for GitHub pull request URLs and returns a list of PR info dicts."""
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


_CHAT_LINK_METADATA_TTL_S = 45
_CHAT_LINK_METADATA_CACHE: dict[tuple, tuple[float, dict]] = {}
_CHAT_LINK_METADATA_CACHE_LOCK = threading.Lock()


def _build_chat_link_cache_key(user_msg: str, history: list, history_window: int = 8) -> tuple:
    recent_user_messages: list[str] = []
    for entry in (history or [])[-history_window:]:
        if isinstance(entry, dict) and entry.get("role") == "user":
            msg = str(entry.get("text", "")).strip()
            if msg:
                recent_user_messages.append(msg[:1000])
    return (str(user_msg or "").strip(), tuple(recent_user_messages))


def _copy_pr_entries(prs: list[dict]) -> list[dict]:
    copied: list[dict] = []
    for pr in prs or []:
        if isinstance(pr, dict):
            copied.append(dict(pr))
    return copied


def _get_cached_chat_link_metadata(user_msg: str, history: list) -> tuple[dict, bool]:
    """Return parsed link metadata with a short TTL cache for chat requests."""
    cache_key = _build_chat_link_cache_key(user_msg, history)
    now = time.time()
    with _CHAT_LINK_METADATA_CACHE_LOCK:
        entry = _CHAT_LINK_METADATA_CACHE.get(cache_key)
        if entry and (now - entry[0]) < _CHAT_LINK_METADATA_TTL_S:
            cached_payload = entry[1]
            return {
                "page_ids": list(cached_payload.get("page_ids", [])),
                "prs": _copy_pr_entries(cached_payload.get("prs", [])),
                "link_source": str(cached_payload.get("link_source", "current_message")),
            }, True

    page_ids = _extract_confluence_page_ids_from_text(user_msg)
    prs = _extract_prs_from_text(user_msg)
    link_source = "current_message"
    if not page_ids and not prs:
        hist_page_ids, hist_prs = _fallback_links_from_history(history)
        if hist_page_ids or hist_prs:
            page_ids = hist_page_ids
            prs = hist_prs
            link_source = "history_fallback"

    payload = {
        "page_ids": list(page_ids),
        "prs": _copy_pr_entries(prs),
        "link_source": link_source,
    }
    with _CHAT_LINK_METADATA_CACHE_LOCK:
        _CHAT_LINK_METADATA_CACHE[cache_key] = (now, payload)

    return {
        "page_ids": list(payload["page_ids"]),
        "prs": _copy_pr_entries(payload["prs"]),
        "link_source": payload["link_source"],
    }, False
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
def _recent_user_text(history: list, limit: int = 6) -> str:
    """Return recent user-authored messages combined into one string."""
    if not history:
        return ""
    texts: list[str] = []
    for entry in history[-limit:]:
        if entry.get("role") != "user":
            continue
        text = str(entry.get("text", "")).strip()
        if text:
            texts.append(text)
    return "\n".join(texts)
def _flatten_tool_text(value) -> str:
    """Convert nested tool output into plain text for lightweight checks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_flatten_tool_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = [_flatten_tool_text(item) for item in value.values()]
        return "\n".join(part for part in parts if part)
    return str(value)
def _is_simple_spelling_instruction_text(text: str) -> bool:
    """Check whether an instruction page only asks for spelling-style inline comments."""
    lowered = (text or "").lower()
    has_spelling = any(token in lowered for token in ["wrong spelling", "spelling", "misspell", "typo", "grammar"])
    has_inline = any(token in lowered for token in ["inline comment", "inline comments", "comment on"])
    has_complex_requirements = any(
        token in lowered
        for token in [
            "footer",
            "summary",
            "readability",
            "long sentence",
            "long paragraph",
            "statistics",
            "replace the text",
            "update the page",
            "find and replace",
        ]
    )
    return has_spelling and has_inline and not has_complex_requirements
def _build_fast_review_response(target_page_id: str, elapsed_ms: int) -> str:
    """Return a concise user-facing success message for the fast review path."""
    elapsed_seconds = elapsed_ms / 1000
    return (
        f"Used the fast spelling-review path for Confluence page {target_page_id}. "
        f"Completed in about {elapsed_seconds:.1f}s and posted the grammar/spelling findings directly to the page."
    )
def _try_fast_confluence_spelling_review(user_msg: str, history: list, page_ids: list[str]) -> str | None:
    """Bypass the agent loop for short spelling-only Confluence tasks."""
    if not page_ids:
        return None
    recent_text = _recent_user_text(history)
    combined = f"{recent_text}\n{user_msg}".lower()
    mentions_follow_apply = "follow" in combined and "apply" in combined
    mentions_spelling = any(token in combined for token in ["spell", "spelling", "misspell", "typo", "grammar"])
    if not mentions_spelling and not (mentions_follow_apply and len(page_ids) >= 2):
        return None
    instruction_page_id = page_ids[0] if len(page_ids) >= 2 else None
    target_page_id = page_ids[-1]
    if instruction_page_id:
        instructions_result = TOOL_REGISTRY["get_page_content_by_sections_tool"]({
            "page_id": instruction_page_id,
            "chunk_size": 2000,
            "max_sections": 4,
        })
        if not isinstance(instructions_result, dict) or not instructions_result.get("success"):
            return None
        instructions_text = _flatten_tool_text(instructions_result.get("data", ""))
        if not _is_simple_spelling_instruction_text(instructions_text):
            return None
    target_result = TOOL_REGISTRY["get_page_content_by_sections_tool"]({
        "page_id": target_page_id,
        "chunk_size": 2000,
        "max_sections": 4,
    })
    if not isinstance(target_result, dict) or not target_result.get("success"):
        return None
    target_text = _flatten_tool_text(target_result.get("data", ""))
    if len(target_text.strip()) > 4000:
        return None
    started_at = time.time()
    review_result = TOOL_REGISTRY["review_confluence_page_content"]({
        "page_id": target_page_id,
        "checklist_page_id": "__GRAMMAR_ONLY__",
    })
    elapsed_ms = int((time.time() - started_at) * 1000)
    if not isinstance(review_result, dict) or not review_result.get("success"):
        return None
    print(f"[FAST_PATH] Used direct grammar review for page {target_page_id} in {elapsed_ms} ms", flush=True)
    return _build_fast_review_response(target_page_id, elapsed_ms)
def _normalize_user_intent_text(text: str) -> str:
    """Normalize prompt text for intent detection by removing chat-log/timestamp noise."""
    if not text:
        return ""
    lines = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        # Skip common chat transcript timestamps like "04:51:33 PM".
        if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}\s*(AM|PM)", line, flags=re.IGNORECASE):
            continue
        lines.append(line)
    return "\n".join(lines).lower()


def _current_and_recent_user_text(user_msg: str, history: list) -> tuple[str, str]:
    """Return normalized current prompt and normalized recent user context."""
    current = _normalize_user_intent_text(user_msg)
    recent = _normalize_user_intent_text(_recent_user_text(history))
    return current, recent


def _is_pr_review_request(user_msg: str, history: list, prs: list[dict]) -> bool:
    """Return True when the latest user prompt asks to review detected PRs."""
    if not prs:
        return False
    current, recent = _current_and_recent_user_text(user_msg, history)

    review_terms = [
        "review",
        "code review",
        "review this pr",
        "review the pr",
        "review this pull request",
        "check this pr",
        "inspect this pr",
        "audit this pr",
    ]

    if any(term in current for term in review_terms):
        return True

    follow_up_terms = ["do it", "go ahead", "proceed", "continue", "run it", "apply it"]
    if any(term in current for term in follow_up_terms):
        return True

    return any(term in recent for term in review_terms)

def _build_universal_pr_review_checklist() -> list[dict]:
    """Build the default checklist for GitHub PR reviews from the web UI."""
    return [
        {
            "id": "universal_naming_conventions",
            "name": "Universal Naming Conventions",
            "description": (
                "Review naming in changed source files. Require PascalCase for classes, "
                "camelCase for JavaScript/TypeScript functions and methods, and snake_case "
                "for Python functions and methods."
            ),
            "enabled": True,
            "execution_order": 10,
        },
        {
            "id": "function_documentation",
            "name": "Function Documentation",
            "description": (
                "Review changed functions and methods to ensure each has an immediately "
                "associated comment or docstring that explains what it does."
            ),
            "enabled": True,
            "execution_order": 20,
        },
        {
            "id": "comment_accuracy",
            "name": "Comment Accuracy",
            "description": (
                "Review function comments and docstrings to ensure they still match the "
                "function name and apparent implementation behavior."
            ),
            "enabled": True,
            "execution_order": 30,
        },
        {
            "id": "cross_file_consistency",
            "name": "Cross-file Consistency",
            "description": "Review changed files for inconsistent terminology, metrics, or formatting.",
            "enabled": True,
            "execution_order": 40,
        },
        {
            "id": "python_flake8",
            "name": "Python Flake8 Compliance",
            "description": "Review changed Python files for flake8 violations.",
            "enabled": True,
            "execution_order": 50,
        },
    ]


_SECRET_LINE_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?token|secret|password|passwd|private[_-]?key)\b\s*[:=]\s*[\"\']?[A-Za-z0-9_\-=/+]{12,}"),
    re.compile(r"(?i)\bATATT[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}"),
]


def _looks_like_secret_assignment(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return False
    placeholders = ["YOUR_", "<TOKEN>", "example", "sample", "dummy"]
    lowered = text.lower()
    if any(token.lower() in lowered for token in placeholders):
        return False
    return any(pattern.search(text) for pattern in _SECRET_LINE_PATTERNS)


def _looks_like_gibberish_text(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return False
    if text.startswith("<") or text.startswith("{") or "{{" in text or "}}" in text:
        return False
    if " " in text:
        return False
    if not re.fullmatch(r"[A-Za-z]{8,}", text):
        return False
    vowel_count = sum(1 for ch in text.lower() if ch in "aeiou")
    return (vowel_count / len(text)) < 0.3


def _collect_chat_style_pr_findings(repo: str, pr_number: int) -> list[dict]:
    findings: list[dict] = []
    files_result = TOOL_REGISTRY["get_files_in_pr_tool"]({
        "repo": repo,
        "pr_number": pr_number,
    })
    if not isinstance(files_result, dict) or not files_result.get("success"):
        return findings

    for file_obj in files_result.get("data", []):
        file_path = file_obj.get("filename", "")
        if not file_path:
            continue
        diff_result = TOOL_REGISTRY["file_with_line_no_and_diff_tool"]({
            "repo": repo,
            "pr_number": pr_number,
            "file_path": file_path,
        })
        if not isinstance(diff_result, dict) or not diff_result.get("success"):
            continue
        for row in diff_result.get("data", []):
            if row.get("type") != "added":
                continue
            line_no = row.get("new_lineno")
            line_text = row.get("new_line", "")
            if _looks_like_secret_assignment(line_text):
                findings.append({
                    "severity": "critical",
                    "file": file_path,
                    "line": line_no,
                    "message": "Potential hardcoded credential or API token committed in source.",
                })
            if file_path.endswith(".html") and _looks_like_gibberish_text(line_text):
                findings.append({
                    "severity": "medium",
                    "file": file_path,
                    "line": line_no,
                    "message": "Suspicious placeholder/gibberish text appears in template output.",
                })

    deduped: list[dict] = []
    seen = set()
    for item in findings:
        key = (item.get("severity"), item.get("file"), item.get("line"), item.get("message"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    deduped.sort(key=lambda x: (severity_order.get(str(x.get("severity", "")).lower(), 99), str(x.get("file", "")), int(x.get("line") or 0)))
    return deduped


def _build_findings_comment_body(repo: str, pr_number: int, findings: list[dict]) -> str:
    """Build a concise PR comment body from fast findings."""
    lines = [
        "Automated quick PR review findings:",
        f"- Target: {repo}#{pr_number}",
    ]
    if not findings:
        lines.append("- No high-signal findings were detected in changed lines.")
        return "\n".join(lines)

    lines.append(f"- Findings: {len(findings)}")
    lines.append("- Ordered by severity:")
    for finding in findings[:25]:
        sev = str(finding.get("severity", "medium")).upper()
        file_path = finding.get("file", "unknown")
        line_no = finding.get("line")
        loc = f"{file_path}:{line_no}" if line_no else file_path
        lines.append(f"  - [{sev}] {loc} - {finding.get('message', '')}")
    if len(findings) > 25:
        lines.append(f"  - ... and {len(findings) - 25} more")
    return "\n".join(lines)
def _build_direct_pr_review_response(reviews: list[dict], elapsed_ms: int) -> str:
    """Format a concise user-facing summary for direct PR review execution."""
    elapsed_seconds = elapsed_ms / 1000
    lines = [
        (
            f"Completed GitHub PR review{'s' if len(reviews) != 1 else ''} in about "
            f"{elapsed_seconds:.1f}s using the universal coding-conventions checklist."
        )
    ]
    for review in reviews:
        lines.append("")
        lines.append(f"{review['repo']}#{review['pr_number']}")
        if review.get('error'):
            lines.append(f"- Review failed: {review['error']}")
            continue
        data = review.get('data', {})
        reviewed_items = data.get('reviewed_items') or []
        if reviewed_items:
            lines.append(f"- Reviewed: {', '.join(reviewed_items)}")
        summary = data.get('summary')
        if summary:
            lines.append(summary)
        else:
            lines.append("- Review completed and a footer summary was posted on the PR.")
        findings = review.get('findings') or []
        if findings:
            lines.append("- Findings (ordered by severity):")
            for finding in findings:
                sev = str(finding.get('severity', 'medium')).upper()
                file_path = finding.get('file', 'unknown')
                line_no = finding.get('line')
                loc = f"{file_path}:{line_no}" if line_no else file_path
                lines.append(f"  - [{sev}] {loc} - {finding.get('message', '')}")
    return "\n".join(lines)
def _wants_posted_pr_comments(user_msg: str, history: list) -> bool:
    """Return True when latest prompt asks to publish/post comments to the PR thread."""
    current, recent = _current_and_recent_user_text(user_msg, history)

    if any(token in current for token in ["don't post", "do not post", "no comments", "without comments"]):
        return False

    direct_triggers = [
        "post comments",
        "leave comments",
        "add comments",
        "comment on the pr",
        "write review comments",
        "publish review",
        "post the review",
        "post this review",
        "comment it",
        "post it on the comments",
        "post the review on the comments",
        "post the review in the comments",
    ]
    if any(token in current for token in direct_triggers):
        return True

    has_post_verb = re.search(r"\b(post|leave|add|write|publish|submit)\b", current) is not None
    has_comment_word = re.search(r"\b(comment|comments|feedback|review comment|review comments)\b", current) is not None
    has_pr_context = re.search(r"\b(pr|pull request)\b", current) is not None

    if has_post_verb and has_comment_word:
        return True
    if has_comment_word and has_pr_context and "review" in current:
        return True

    # Follow-up fallback when user says "do it" right after asking to post comments.
    follow_up_terms = ["do it", "go ahead", "proceed", "continue", "run it", "apply it"]
    if any(term in current for term in follow_up_terms):
        if any(token in recent for token in direct_triggers):
            return True

    return False


def _try_direct_pr_review(user_msg: str, history: list, prs: list[dict]) -> str | None:
    """Bypass the agent loop for explicit GitHub PR review requests."""
    if not _is_pr_review_request(user_msg, history, prs):
        return None

    should_post_comments = _wants_posted_pr_comments(user_msg, history)
    started_at = time.time()
    reviews: list[dict] = []

    for pr in prs:
        repo_name = f"{pr['owner']}/{pr['repo']}"
        findings = _collect_chat_style_pr_findings(repo_name, pr['pr_number'])

        if not should_post_comments:
            reviews.append({
                'repo': repo_name,
                'pr_number': pr['pr_number'],
                'data': {
                    'summary': 'Fast findings-only review completed. No PR comments were posted (comment-posting mode was not requested).',
                    'reviewed_items': ['changed files and line-level diff audit'],
                },
                'findings': findings,
            })
            continue

        comment_body = _build_findings_comment_body(repo_name, pr['pr_number'], findings)
        post_result = TOOL_REGISTRY['add_comment_tool']({
            'repo': repo_name,
            'pr_number': pr['pr_number'],
            'comment_text': comment_body,
        })
        if not isinstance(post_result, dict) or not post_result.get('success'):
            error = 'Failed to post PR summary comment'
            if isinstance(post_result, dict):
                error = str(post_result.get('error', error))
            reviews.append({
                'repo': repo_name,
                'pr_number': pr['pr_number'],
                'error': error,
                'findings': findings,
            })
            continue

        reviews.append({
            'repo': repo_name,
            'pr_number': pr['pr_number'],
            'data': {
                'summary': 'Fast findings review completed and posted as a PR comment.',
                'reviewed_items': ['changed files and line-level diff audit'],
            },
            'findings': findings,
        })

    elapsed_ms = int((time.time() - started_at) * 1000)
    if not reviews:
        return (
            "Could not run PR review because no pull request links were detected. "
            "Please include the full PR URL in the same message."
        )
    if all(review.get('error') for review in reviews):
        lines = [
            "GitHub PR review started, but the automated review tool failed for all detected PRs:",
        ]
        for review in reviews:
            lines.append(
                f"- {review['repo']}#{review['pr_number']}: {review.get('error', 'Unknown review error')}"
            )
        lines.append("Please retry in a moment. If it keeps failing, check MCP server logs for timeouts.")
        return "\n".join(lines)

    return _build_direct_pr_review_response(reviews, elapsed_ms)

def format_result(data) -> str:
    """Returns the data as a string, pretty-printing dicts/lists as formatted JSON."""
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)
# --- Flask Routes ---
# These define the web pages and API endpoints the backend provides.

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
   - For explicit PR review requests, use a checklist that covers universal coding conventions:
     class names in PascalCase, JavaScript/TypeScript function names in camelCase,
     required function comments/docstrings, and whether comments accurately describe the function.
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
PR REVIEW DIRECTIVE (when user asks to review a GitHub PR):
When the user asks to review a GitHub pull request:
1. IMMEDIATELY call review_pull_request_tool with the detected PR info and universal coding checklist.
2. The tool will:
   - Analyze all changed files and line-level diffs
   - Post inline comments on specific lines with findings
   - Post a footer summary with all findings organized by severity
3. Do NOT ask follow-up questions. Execute the review immediately.
4. Return a FINAL_ANSWER confirming the review was posted with both inline AND summary comments.
5. Example tool call:
   TOOL_CALL: review_pull_request_tool
   ARGS: {"repo": "owner/repo", "pr_number": 123, "checklist": [{"id": "...", "name": "...", "description": "...", "enabled": true, "execution_order": 10}]}
"""
# --- Tool Registry ---
# This dictionary maps tool names to functions that call them.
# It allows the agent to use different tools by name.
# --- Panel Checklist Mapping ---
# Maps panel checkbox labels (from index.html) to checklist item dicts used by review_pull_request.
_PANEL_TO_CHECKLIST_MAP = {
    # naming
    "PascalCase Class Names":           {"id": "universal_naming_conventions", "description": "Require PascalCase for class names."},
    "camelCase Function Names (JS/TS)": {"id": "universal_naming_conventions", "description": "Require camelCase for JS/TS functions and methods."},
    "snake_case Function Names (Python)": {"id": "universal_naming_conventions", "description": "Require snake_case for Python functions and methods."},
    # docs
    "Required Comments/Docstrings":     {"id": "function_documentation", "description": "Ensure functions have associated comments or docstrings."},
    "Comment Accuracy":                 {"id": "comment_accuracy", "description": "Ensure comments match implementation behavior."},
    # quality
    "Code Duplication / DRY Violations": {"id": "code_duplication", "description": "Flag duplicated code blocks."},
    "Unused Variables / Imports":       {"id": "unused_variables", "description": "Flag unused variables and imports."},
    "Complex / Long Functions":         {"id": "complex_functions", "description": "Flag overly complex or long functions."},
    # security
    "Hardcoded Secrets / Credentials":  {"id": "hardcoded_secrets", "description": "Flag hardcoded secrets or credentials."},
    "SQL Injection / XSS Risks":        {"id": "sql_injection_xss", "description": "Flag SQL injection and XSS risks."},
    "Input Validation":                 {"id": "input_validation", "description": "Check for missing input validation."},
    # errors
    "Proper Error Handling":            {"id": "error_handling", "description": "Check for proper error handling patterns."},
    "Edge Cases Covered":               {"id": "edge_cases", "description": "Check for unhandled edge cases."},
    # style
    "Flake8 / ESLint Compliance":       {"id": "python_flake8", "description": "Run flake8/ESLint compliance checks."},
    "Consistent Formatting / Indentation": {"id": "consistent_formatting", "description": "Check for consistent formatting and indentation."},
    # testing
    "Test Coverage for New Code":       {"id": "test_coverage", "description": "Check that new code has test coverage."},
    # perf
    "Performance / Efficiency Concerns": {"id": "performance", "description": "Flag performance and efficiency concerns."},
    # consistency
    "Cross-file Consistency":           {"id": "cross_file_consistency", "description": "Check for inconsistent terminology, metrics, or formatting across files."},
    # spelling
    "Spelling & Grammar":              {"id": "spelling_grammar", "description": "Check for spelling and grammar issues."},
    "Repeated Words":                   {"id": "repeated_words", "description": "Flag repeated or redundant words."},
}


def _build_checklist_from_panel(panel_items: list[str]) -> list[dict]:
    """Convert panel checkbox labels into the checklist format expected by review_pull_request.
    
    Deduplicates by ID (e.g. multiple naming items map to one 'universal_naming_conventions' entry)
    and preserves execution order.
    """
    seen_ids: set[str] = set()
    checklist: list[dict] = []
    order = 10

    for label in panel_items:
        mapping = _PANEL_TO_CHECKLIST_MAP.get(label)
        if not mapping:
            continue
        item_id = mapping["id"]
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        checklist.append({
            "id": item_id,
            "name": label,
            "description": mapping["description"],
            "enabled": True,
            "execution_order": order,
        })
        order += 10

    # If panel sent items but none matched, fall back to defaults
    if not checklist:
        return _build_universal_pr_review_checklist()
    return checklist


# --- Cached PR Review Checklist ---
# Built once and reused to avoid rebuilding on every request
_PR_CHECKLIST_CACHE_TTL_S = 300
_CACHED_PR_CHECKLIST = None
_CACHED_PR_CHECKLIST_AT = 0.0
_PR_CHECKLIST_CACHE_LOCK = threading.Lock()


def _get_cached_pr_checklist():
    global _CACHED_PR_CHECKLIST, _CACHED_PR_CHECKLIST_AT
    now = time.time()
    with _PR_CHECKLIST_CACHE_LOCK:
        if (
            _CACHED_PR_CHECKLIST is None
            or (now - _CACHED_PR_CHECKLIST_AT) >= _PR_CHECKLIST_CACHE_TTL_S
        ):
            _CACHED_PR_CHECKLIST = _build_universal_pr_review_checklist()
            _CACHED_PR_CHECKLIST_AT = now
        return _CACHED_PR_CHECKLIST


_MAX_CONCURRENT_REVIEWS = 3
_REVIEW_EXECUTION_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT_REVIEWS)
_REVIEW_INFLIGHT_LOCK = threading.Lock()
_REVIEW_INFLIGHT: dict[tuple, dict] = {}


def _make_review_coalesce_key(
    repo: str,
    pr_number: int,
    checklist: list[dict] | None = None,
    *,
    skip_inline: bool = False,
    skip_footer: bool = False,
    max_inline_comments: int | None = None,
    group_similar_inline: bool | None = None,
    github_base_url: str | None = None,
) -> tuple:
    checklist_ids = tuple(
        sorted(
            str(item.get("id", "")).strip()
            for item in (checklist or [])
            if isinstance(item, dict)
        )
    )
    normalized_repo = str(repo or "").strip().lower()
    normalized_base_url = str(github_base_url or "").strip().lower()
    normalized_max_inline = int(max_inline_comments) if max_inline_comments is not None else None
    normalized_group = bool(group_similar_inline) if group_similar_inline is not None else None
    return (
        normalized_repo,
        int(pr_number),
        checklist_ids,
        bool(skip_inline),
        bool(skip_footer),
        normalized_max_inline,
        normalized_group,
        normalized_base_url,
    )


def _run_review_with_coalescing(review_key: tuple, runner):
    """Run one review per key at a time, sharing in-flight results with duplicate callers."""
    waiter = False
    with _REVIEW_INFLIGHT_LOCK:
        inflight = _REVIEW_INFLIGHT.get(review_key)
        if inflight is None:
            inflight = {"event": threading.Event(), "result": None, "error": None}
            _REVIEW_INFLIGHT[review_key] = inflight
        else:
            waiter = True

    if waiter:
        wait_started = time.time()
        inflight["event"].wait()
        waited_s = max(0.0, time.time() - wait_started)
        if inflight.get("error") is not None:
            raise RuntimeError(str(inflight["error"]))
        return inflight.get("result"), True, waited_s

    _REVIEW_EXECUTION_SEMAPHORE.acquire()
    try:
        result = runner()
        inflight["result"] = result
    except Exception as exc:
        inflight["error"] = exc
    finally:
        _REVIEW_EXECUTION_SEMAPHORE.release()
        inflight["event"].set()
        with _REVIEW_INFLIGHT_LOCK:
            current = _REVIEW_INFLIGHT.get(review_key)
            if current is inflight:
                _REVIEW_INFLIGHT.pop(review_key, None)

    if inflight.get("error") is not None:
        raise RuntimeError(str(inflight["error"]))
    return inflight.get("result"), False, 0.0

# --- Lightweight PR Review Prompt ---
# Used when agent detects a PR review request to reduce Copilot latency
PR_REVIEW_AGENT_PROMPT = """You are MunnAI, an AI code reviewer.
A user has asked you to review a GitHub pull request.
Your task is SIMPLE:
1. Call review_pull_request_tool with the provided params
2. That's it.

When you receive the PR links, call the tool immediately without questions.
TOOL_CALL: review_pull_request_tool
ARGS: (provided below in context)
"""

def _call_tool_with_runtime_auth(tool_name: str, args: dict):
    payload = dict(args or {})
    override_auth = normalize_user_auth(payload.pop("__user_auth", None))
    runtime_auth = override_auth or get_active_user_auth()
    
    # Extract base URLs if provided
    github_base_url = payload.pop("__github_base_url", None)
    confluence_base_url = payload.pop("__confluence_base_url", None)
    
    # Add base URLs to runtime_auth for set_runtime_auth call
    if github_base_url:
        runtime_auth = runtime_auth or {}
        if not isinstance(runtime_auth, dict):
            runtime_auth = {}
        runtime_auth["github_base_url"] = github_base_url
    
    if confluence_base_url:
        runtime_auth = runtime_auth or {}
        if not isinstance(runtime_auth, dict):
            runtime_auth = {}
        runtime_auth["confluence_base_url"] = confluence_base_url
    
    return mcp_client.call_tool(tool_name, payload, runtime_auth=runtime_auth)


TOOL_REGISTRY = {
    "review_confluence_page_content": lambda args: _call_tool_with_runtime_auth("review_confluence_page_content", args),
    "get_page_content_by_sections_tool": lambda args: _call_tool_with_runtime_auth("get_page_content_by_sections_tool", args),
    "post_confluence_footer_comment": lambda args: _call_tool_with_runtime_auth("post_confluence_footer_comment", args),
    "post_confluence_inline_comment": lambda args: _call_tool_with_runtime_auth("post_confluence_inline_comment", args),
    "review_pull_request_tool": lambda args: _call_tool_with_runtime_auth("review_pull_request_tool", args),
    "add_comment_tool": lambda args: _call_tool_with_runtime_auth("add_comment_tool", args),
    "get_files_in_pr_tool": lambda args: _call_tool_with_runtime_auth("get_files_in_pr_tool", args),
    "file_with_line_no_and_diff_tool": lambda args: _call_tool_with_runtime_auth("file_with_line_no_and_diff_tool", args),
    "get_confluence_page_content": lambda args: _call_tool_with_runtime_auth("get_confluence_page_content", args),
    "update_confluence_page": lambda args: _call_tool_with_runtime_auth("update_confluence_page", args),
    "find_and_replace_in_confluence_page": lambda args: _call_tool_with_runtime_auth("find_and_replace_in_confluence_page", args),
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
def run_agent(user_msg: str, history: list, link_context: str, request_meta: dict | None = None) -> str:
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
    request_meta = request_meta or {}
    page_ids = list(request_meta.get("page_ids") or [])
    prs = list(request_meta.get("prs") or [])
    review_type = str(request_meta.get("review_type") or "").strip().lower()
    doc_type = str(request_meta.get("doc_type") or "").strip()
    checklist_input = request_meta.get("checklist") or []
    outputs = list(request_meta.get("outputs") or [])
    confluence_checklist_page_id = str(request_meta.get("confluence_checklist_page_id") or "").strip()

    if not page_ids:
        page_ids = re.findall(r'page_id = "(\d+)"', link_context)
    if not prs:
        pr_matches = re.findall(r"PR: ([^/]+)/([^#]+)#(\d+)", link_context)
        prs = [
            {"owner": owner, "repo": repo, "pr_number": int(pr_num)}
            for owner, repo, pr_num in pr_matches
        ]

    if checklist_input and isinstance(checklist_input, list) and checklist_input and isinstance(checklist_input[0], dict):
        pr_checklist = checklist_input
    elif checklist_input:
        pr_checklist = _build_checklist_from_panel([str(item) for item in checklist_input])
    else:
        pr_checklist = _get_cached_pr_checklist()

    wants_inline = any("inline" in str(item).lower() for item in outputs) if outputs else True
    wants_combined_review = bool(
        page_ids
        and prs
        and (
            review_type in {"document and code", "code and document", "combined", "both", "document+code", "code+document"}
            or "document and code" in user_msg.lower()
            or "code and document" in user_msg.lower()
            or "documentation and code" in user_msg.lower()
            or doc_type
            or checklist_input
            or outputs
        )
    )

    def _render_tool_summary(result: object, default_message: str) -> tuple[bool, str, list[str]]:
        if not isinstance(result, dict):
            return False, str(result) if result is not None else default_message, []
        if not result.get("success"):
            return False, result.get("error", default_message), []
        data = result.get("data", {})
        if not isinstance(data, dict):
            return True, str(data) if data else default_message, []
        summary = str(data.get("summary", "")).strip() or default_message
        reviewed = data.get("reviewed_items", []) or []
        reviewed = [str(item) for item in reviewed if str(item).strip()]
        return True, summary, reviewed

    # OPTIMIZATION: Detect combined document/code reviews early and run both tools directly.
    if wants_combined_review:
        print("[AGENT] Detected combined document/code review request - calling Confluence and PR tools directly", flush=True)
        pr = prs[0]
        page_id = str(page_ids[0])
        repo_full = f"{pr['owner']}/{pr['repo']}"
        skip_inline = not wants_inline

        confluence_result = TOOL_REGISTRY["review_confluence_page_content"]({
            "page_id": page_id,
            "checklist_page_id": confluence_checklist_page_id,
            "skip_inline": skip_inline,
            "skip_footer": False,
        })
        pr_result = TOOL_REGISTRY["review_pull_request_tool"]({
            "repo": repo_full,
            "pr_number": int(pr["pr_number"]),
            "checklist": pr_checklist,
            "skip_inline": skip_inline,
            "skip_footer": False,
        })

        confluence_ok, confluence_summary, confluence_reviewed = _render_tool_summary(
            confluence_result,
            f"Review comments and a footer summary have been posted to Confluence page {page_id}.",
        )
        pr_ok, pr_summary, pr_reviewed = _render_tool_summary(
            pr_result,
            f"Review comments and a footer summary have been posted to PR {repo_full}#{pr['pr_number']}.",
        )

        lines = [
            f"Completed combined document/code review for {repo_full}#{pr['pr_number']} and Confluence page {page_id}.",
        ]
        if review_type:
            lines.append(f"Review type: {review_type}")
        elif doc_type:
            lines.append(f"Document type: {doc_type}")
        if checklist_input:
            lines.append(f"Checklist items: {', '.join(str(item) for item in checklist_input)}")
        if outputs:
            lines.append(f"Expected outputs: {', '.join(str(item) for item in outputs)}")
        lines.append("")
        lines.append(f"Confluence page {page_id}:")
        lines.append(f"- Status: {'success' if confluence_ok else 'failed'}")
        if confluence_reviewed:
            lines.append(f"- Reviewed: {', '.join(confluence_reviewed)}")
        lines.append(f"- {confluence_summary}")
        lines.append("")
        lines.append(f"GitHub PR {repo_full}#{pr['pr_number']}:")
        lines.append(f"- Status: {'success' if pr_ok else 'failed'}")
        if pr_reviewed:
            lines.append(f"- Reviewed: {', '.join(pr_reviewed)}")
        lines.append(f"- {pr_summary}")
        lines.append("")
        lines.append("Footer summaries were posted to both the Confluence page and the PR.")
        return "\n".join(lines)

    # OPTIMIZATION: Detect PR reviews early and call tool directly (skip LLM)
    is_pr_review = "PR:" in link_context and any(term in user_msg.lower() for term in ["review", "check", "audit", "inspect"])
    if is_pr_review:
        print("[AGENT] Detected PR review request - calling review_pull_request_tool directly", flush=True)
        pr_matches = re.findall(r"PR: ([^/]+)/([^#]+)#(\d+)", link_context)
        if pr_matches:
            owner, repo, pr_num = pr_matches[0]
            repo_full = f"{owner}/{repo}"
            checklist = _get_cached_pr_checklist()
            review_key = _make_review_coalesce_key(
                repo_full,
                int(pr_num),
                checklist=checklist,
            )

            def _invoke_direct_review():
                return TOOL_REGISTRY["review_pull_request_tool"]({
                    "repo": repo_full,
                    "pr_number": int(pr_num),
                    "checklist": checklist,
                })

            started_at = time.time()
            result, reused_inflight, waited_s = _run_review_with_coalescing(review_key, _invoke_direct_review)
            elapsed_ms = int((time.time() - started_at) * 1000)
            elapsed_s = elapsed_ms / 1000
            if isinstance(result, dict) and result.get("success"):
                data = result.get("data", {})
                summary = data.get("summary", "") if isinstance(data, dict) else str(data)
                reviewed = data.get("reviewed_items", []) if isinstance(data, dict) else []
                lines = [
                    f"Completed PR review for {repo_full}#{pr_num} in {elapsed_s:.1f}s.",
                ]
                if reused_inflight:
                    lines.append(f"Reused an in-flight review result (waited {waited_s:.1f}s).")
                if reviewed:
                    lines.append(f"Reviewed: {', '.join(reviewed)}")
                if summary:
                    lines.append(summary)
                else:
                    lines.append("Review comments (inline + summary) have been posted to the PR.")
                return "\n".join(lines)
            else:
                error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
                return f"PR review for {repo_full}#{pr_num} failed: {error}"
    
    step_budget = _step_budget_for_request(user_msg)
    for step in range(step_budget):
        print(f"[AGENT] Step {step + 1}/{step_budget}", flush=True)
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
    print(f"[AGENT] Max steps ({step_budget}) reached. Returning deterministic execution summary.", flush=True)
    if scratchpad:
        return _build_deterministic_execution_summary(user_msg, scratchpad)
    return "No tool actions were executed before reaching the step limit."
# --- Chat API Endpoint ---
# This endpoint receives chat messages from the frontend, runs the agent, and returns the response.

def _clean_review_line(line: str) -> str | None:
    """Clean a raw [REVIEW] stderr line for user-friendly SSE display.

    Returns cleaned text or None to suppress the line entirely.
    """
    # Skip duplicate INFO:/DEBUG: prefixed lines (they repeat the [REVIEW] message)
    if line.lstrip().startswith(("INFO:", "DEBUG:")):
        return None
    # Extract the message after [REVIEW]
    idx = line.find("[REVIEW]")
    if idx < 0:
        return None
    msg = line[idx + len("[REVIEW]"):].strip()
    if not msg:
        return None
    # Suppress noisy per-comment lines
    if msg.startswith("Inline comment posted"):
        return None  # batched into a counter instead
    if msg.startswith("Inline anchor failed"):
        return None
    # Clean up common prefixes for readability
    if msg.startswith(">>>"):
        msg = msg.replace(">>>", "▶", 1)  # ▶
    if msg.startswith("<<<"):
        msg = msg.replace("<<<", "✅", 1)  # ✅
    return f"  {msg}"


# --- SSE Review Stream Endpoint ---
