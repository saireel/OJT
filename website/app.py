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
                stderr=subprocess.PIPE,
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


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_msg = data.get("prompt", "").strip()
    if not user_msg:
        return jsonify({"error": "Empty prompt"}), 400

    # Detect Confluence page links
    conf_urls = re.findall(r"https?://[^\s]*atlassian\.net/wiki[^\s]*", user_msg)
    page_ids: list[str] = []
    for url in conf_urls:
        pid = extract_confluence_page_id(url)
        if pid and pid not in page_ids:
            page_ids.append(pid)

    # Detect GitHub PR links
    pr_urls = re.findall(r"https?://github\.com/[^\s]+/pull/\d+", user_msg)
    prs: list[dict] = []
    seen_prs: set[tuple[str, str, int]] = set()
    for url in pr_urls:
        info = extract_pr_info(url)
        if info:
            key = (info["owner"], info["repo"], info["pr_number"])
            if key not in seen_prs:
                seen_prs.add(key)
                prs.append(info)

    # No links -- plain Copilot chat
    if not page_ids and not prs:
        response, err = send_to_copilot(user_msg)
        if err:
            return jsonify({"error": err}), 502
        return jsonify({"response": response})

    # Confluence pages detected
    if page_ids:
        return handle_confluence(user_msg, page_ids)

    # GitHub PRs detected
    if prs:
        return handle_pr(user_msg, prs)

    return jsonify({"error": "Could not process request"}), 400


# -- Confluence Handler --------------------------------------------------

def handle_confluence(user_msg: str, page_ids: list[str]):
    detected = [f"confluence:{pid}" for pid in page_ids]
    target_id = page_ids[-1]

    # Fetch the main page content
    page_result = mcp_client.call_tool("get_page_content_by_sections_tool", {"page_id": target_id})
    if not page_result.get("success"):
        return jsonify({"error": page_result.get("error", "Failed to fetch page content"), "detected": detected}), 502

    page_content = page_result.get("data", "")

    # If multiple pages, fetch their content as well
    reference_content = ""
    if len(page_ids) > 1:
        for pid in page_ids[:-1]:
            ref_result = mcp_client.call_tool("get_page_content_by_sections_tool", {"page_id": pid})
            if ref_result.get("success"):
                reference_content += f"\n--- Reference Page {pid} ---\n{format_result(ref_result.get('data', ''))}\n--- End ---\n"

    # Compose prompt for Copilot
    prompt = (
        f"USER PROMPT: {user_msg}\n\n"
        f"{reference_content}"
        f"--- Target Confluence Page Content ({target_id}) ---\n{format_result(page_content)}\n--- End ---\n\n"
        "Respond to the user prompt using the page content above."
    )

    response, err = send_to_copilot(prompt)
    if err:
        return jsonify({"error": err, "detected": detected}), 502

    return jsonify({"response": response, "detected": detected, "page_content": page_content})

# -- PR Handler ----------------------------------------------------------

def handle_pr(user_msg: str, prs: list[dict]):
    target = prs[0]
    label = f'{target["owner"]}/{target["repo"]}#{target["pr_number"]}'
    detected = [f"pr:{label}"]

    # Use MCP review tool -- it reviews AND posts comments automatically
    print(f"[DEBUG] Starting PR review for {label}", flush=True)
    result = mcp_client.call_tool("review_pull_request_tool", {
        "repo": target["repo"],
        "pr_number": target["pr_number"],
        "checklist": [],
    })
    print(f"[DEBUG] PR review result: {str(result)[:200]}", flush=True)

    if not result.get("success"):
        return jsonify({"error": result.get("error", "PR review failed"), "detected": detected}), 502

    review_data = result.get("data", {})
    summary = f"Review completed for {label}.\n\n{format_result(review_data)}"

    return jsonify({"response": summary, "detected": detected, "mcp_review": review_data})


atexit.register(lambda: mcp_client.shutdown())

if __name__ == "__main__":
    app.run(debug=True, port=5000)
