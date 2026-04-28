import json
import os
import subprocess
import sys
import threading
import time

import requests

MCP_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MCP_SERVER_MODULE = "mcp_server.mcp_tools"

COPILOT_BRIDGE_URL = "http://127.0.0.1:5100/api/prompt"
# Fallback URLs if primary endpoint fails
COPILOT_BRIDGE_FALLBACKS = [
    "http://127.0.0.1:5100/api/chat",
    "http://127.0.0.1:5100/copilot/chat",
    "http://127.0.0.1:5100/v1/chat/completions",
]
PREFERRED_LLM_MODEL = os.getenv("PREFERRED_LLM_MODEL", "claude")  # Default to Claude
# NOTE: Model selection may also require VS Code settings configuration
# The bridge may respect: VS Code > Settings > Copilot > Model Selection

_USER_AUTH_LOCAL = threading.local()

def normalize_user_auth(raw: dict | None) -> dict:
    """Return a sanitized per-user auth payload used for runtime API credentials."""
    if not isinstance(raw, dict):
        raw = {}
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

    # Static defaults so GitHub actions always have a valid API host even when UI omits base URLs.
    out.setdefault("github_base_url", "https://api.github.com")
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
        self._tool_names_cache: set[str] | None = None

    def _ensure_started(self):
        """Starts the MCP server process if not running and performs protocol initialization."""
        if self.process is None or self.process.poll() is not None:
            env = {
                **os.environ,
                "ENABLE_READABILITY_CHECK": "true",
                "READABILITY_SENTENCE_WORD_LIMIT": "40",
                "READABILITY_PARAGRAPH_WORD_LIMIT": "250",
            }
            env["PYTHONPATH"] = os.pathsep.join(
                [MCP_SERVER_DIR, env.get("PYTHONPATH", "")]
            ).strip(os.pathsep)
            self.process = subprocess.Popen(
                [sys.executable, "-m", MCP_SERVER_MODULE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=MCP_SERVER_DIR,
                env=env,
            )
            assert self.process.stdin is not None
            assert self.process.stdout is not None
            self._initialized = False
            self._tool_names_cache = None
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
    
    def _get_tool_names(self) -> set[str]:
        self._ensure_started()
        if self._tool_names_cache is not None:
            return self._tool_names_cache
        result = self._send_request("tools/list", {})
        tools = result.get("result", {}).get("tools", [])
        names = {
            str(tool.get("name", "")).strip()
            for tool in tools
            if isinstance(tool, dict) and str(tool.get("name", "")).strip()
        }
        self._tool_names_cache = names
        return names

    def _resolve_tool_name(self, tool_name: str) -> str:
        requested = str(tool_name or "").strip()
        if not requested:
            return requested
        names = self._get_tool_names()
        if requested in names:
            return requested
        prefixed = f"tool_{requested}"
        if prefixed in names:
            return prefixed
        return requested

    def call_tool(self, tool_name, arguments, runtime_auth=None):
        """Calls a named tool on the MCP server and returns the parsed result."""
        with self.lock:
            try:
                self._ensure_started()
                resolved_tool_name = self._resolve_tool_name(tool_name)
                print(f"[MCP] call_tool: {tool_name} -> {resolved_tool_name}", flush=True)
                if runtime_auth:
                    auth_tool_name = self._resolve_tool_name("set_runtime_auth")
                    if resolved_tool_name != auth_tool_name:
                        self._send_request(
                            "tools/call",
                            {"name": auth_tool_name, "arguments": runtime_auth},
                            timeout=60,
                        )
                result = self._send_request("tools/call", {"name": resolved_tool_name, "arguments": arguments}, timeout=600)
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
                print(f"[MCP] tool_content: {tool_content}", flush=True)
                if tool_content:
                    text = tool_content[0].get("text", "")
                    print(f"[MCP] text from tool_content[0]: {text[:100] if len(str(text)) > 100 else text}", flush=True)
                    # Detect FastMCP error wrapper (exception caught by framework, returned as text)
                    if text.startswith("Error calling tool "):
                        return {"success": False, "error": text}
                    try:
                        parsed = json.loads(text)
                        print(f"[MCP] parsed JSON result: success={parsed.get('success')}", flush=True)
                        return parsed
                    except json.JSONDecodeError as je:
                        print(f"[MCP] JSON decode error: {je}", flush=True)
                        return {"success": True, "data": text}
                else:
                    print(f"[MCP] WARNING: tool_content is empty! mcp_result={mcp_result}", flush=True)
                    return_val = {"success": True, "data": result.get("result")}
                    print(f"[MCP] returning: {return_val}", flush=True)
                    return return_val
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

mcp_client = MCPClient()

def call_llm(prompt: str) -> tuple[str, str | None]:
    """Send a prompt to the LLM (Copilot) and return (response, error)."""
    try:
        # Use default LLM without forcing a specific model
        payload = {"prompt": prompt}
        headers = {
            "Content-Type": "application/json",
        }
        
        # List of endpoints to try in order (without model parameter)
        endpoints_to_try = [
            COPILOT_BRIDGE_URL,
            *COPILOT_BRIDGE_FALLBACKS,
        ]
        
        resp = None
        last_error = None
        
        # Try each endpoint
        for endpoint_url in endpoints_to_try:
            try:
                resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=90)
                resp.raise_for_status()
                # Success! Break out of loop
                break
            except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError) as e:
                last_error = e
                continue
        
        # If we got no response or all requests failed
        if not resp or resp.status_code >= 400:
            error_msg = f"Bridge not responding. Tried endpoints: {', '.join(endpoints_to_try)}"
            if last_error:
                error_msg += f" (Last error: {str(last_error)})"
            return "", error_msg
        
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

def _call_tool_with_runtime_auth(tool_name: str, args: dict):
    payload = dict(args or {})

    # Keep __user_auth in payload for MCP tool functions to receive it
    raw_override_auth = payload.get("__user_auth", None)  # Use .get() instead of .pop()
    if isinstance(raw_override_auth, dict):
        # Only treat __user_auth as an override when caller provided explicit values.
        has_explicit_override = any(
            isinstance(value, str) and bool(value.strip())
            for k in (
                "confluence_email",
                "confluence_api_token",
                "confluence_base_url",
                "github_owner",
                "github_token",
                "github_base_url",
            )
            for value in [raw_override_auth.get(k)]
        )
        override_auth = normalize_user_auth(raw_override_auth) if has_explicit_override else {}
    else:
        override_auth = {}

    runtime_auth = override_auth or get_active_user_auth()

    # Extract base URLs if provided (still pop these as they're transport-only)
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
    # Confluence tools
    "review_confluence": lambda args: _call_tool_with_runtime_auth("review_confluence", args),
    "get_page_content": lambda args: _call_tool_with_runtime_auth("get_page_content", args),
    "get_page_content_by_sections": lambda args: _call_tool_with_runtime_auth("get_page_sections", args),
    "post_footer_comment": lambda args: _call_tool_with_runtime_auth("post_footer_comment", args),
    "post_inline_comment": lambda args: _call_tool_with_runtime_auth("post_inline_comment", args),
    "create_space": lambda args: _call_tool_with_runtime_auth("create_space", args),
    "create_page": lambda args: _call_tool_with_runtime_auth("create_page", args),
    "update_page": lambda args: _call_tool_with_runtime_auth("update_page", args),
    "find_and_replace": lambda args: _call_tool_with_runtime_auth("find_and_replace", args),
    
    # GitHub/PR tools
    "review_pull_request": lambda args: _call_tool_with_runtime_auth("review_pull_request", args),
    "add_pr_comment": lambda args: _call_tool_with_runtime_auth("add_pr_comment", args),
    "get_files_in_pr": lambda args: _call_tool_with_runtime_auth("get_files_in_pr", args),
    "file_with_line_no_and_diff": lambda args: _call_tool_with_runtime_auth("file_with_line_no_and_diff", args),
    "get_base_and_head_sha": lambda args: _call_tool_with_runtime_auth("get_base_and_head_sha", args),
    "get_file_content": lambda args: _call_tool_with_runtime_auth("get_file_content", args),
    "add_file_level_comment": lambda args: _call_tool_with_runtime_auth("add_file_level_comment", args),
    "add_inline_comment": lambda args: _call_tool_with_runtime_auth("add_inline_comment", args),
    "show_comments": lambda args: _call_tool_with_runtime_auth("show_comments", args),
    "reply_comment": lambda args: _call_tool_with_runtime_auth("reply_comment", args),
    "cleanup_old_bot_comments": lambda args: _call_tool_with_runtime_auth("cleanup_old_bot_comments", args),
    "list_repositories": lambda args: _call_tool_with_runtime_auth("list_repositories", args),
    "list_pull_requests": lambda args: _call_tool_with_runtime_auth("list_pull_requests", args),
    
    # Combined review
    "summarize_pr_confluence": lambda args: _call_tool_with_runtime_auth("summarize_pr_confluence", args),
    "check_doc_coverage": lambda args: _call_tool_with_runtime_auth("check_doc_coverage", args),
    "check_code_examples": lambda args: _call_tool_with_runtime_auth("check_code_examples", args),
    "check_api_signatures": lambda args: _call_tool_with_runtime_auth("check_api_signatures", args),
    "check_config_documented": lambda args: _call_tool_with_runtime_auth("check_config_documented", args),
    "check_architecture_alignment": lambda args: _call_tool_with_runtime_auth("check_architecture_alignment", args),
    "check_instructions_match": lambda args: _call_tool_with_runtime_auth("check_instructions_match", args),
    "check_error_handling": lambda args: _call_tool_with_runtime_auth("check_error_handling", args),
    "check_deprecated_removed": lambda args: _call_tool_with_runtime_auth("check_deprecated_removed", args),
    "check_terminology": lambda args: _call_tool_with_runtime_auth("check_terminology", args),
    "check_pr_references": lambda args: _call_tool_with_runtime_auth("check_pr_references", args),
    "check_code_path_sections": lambda args: _call_tool_with_runtime_auth("check_code_path_sections", args),
    
    "review_combined_pr_and_confluence": lambda args: _call_tool_with_runtime_auth("review_combined_pr_and_confluence", args),

    # Auth
    "set_runtime_auth": lambda args: _call_tool_with_runtime_auth("set_runtime_auth", args),
}
