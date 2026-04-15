# app.py
# Thin Flask entrypoint: routes only. Core logic lives in app_logic.py.
import atexit
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

try:
    from . import app_logic as _logic
except ImportError:
    import app_logic as _logic

_extract_confluence_page_ids_from_text = _logic._extract_confluence_page_ids_from_text
_extract_prs_from_text = _logic._extract_prs_from_text
_fallback_links_from_history = _logic._fallback_links_from_history
_try_fast_confluence_spelling_review = _logic._try_fast_confluence_spelling_review
run_agent = _logic.run_agent
normalize_user_auth = _logic.normalize_user_auth
set_active_user_auth = _logic.set_active_user_auth
clear_active_user_auth = _logic.clear_active_user_auth
FEEDBACK_FILE = _logic.FEEDBACK_FILE
_build_universal_pr_review_checklist = _logic._build_universal_pr_review_checklist
_build_checklist_from_panel = _logic._build_checklist_from_panel
_get_cached_pr_checklist = _logic._get_cached_pr_checklist
_clean_review_line = _logic._clean_review_line
TOOL_REGISTRY = _logic.TOOL_REGISTRY
mcp_client = _logic.mcp_client
json = _logic.json
re = _logic.re
threading = _logic.threading
time = _logic.time

app = Flask(__name__)

@app.route("/")
def index():
    # Renders the main web page (index.html)
    """Renders and returns the main index.html page."""
    return render_template("index.html")


@app.route("/quick-user-guide")
def quick_user_guide():
    """Renders the quick user guide page."""
    return render_template("quick_user_guide.html")
# --- Agent System Prompt ---
# This is a long string that defines the rules and workflow for the AI agent.
# It tells the agent how to process user requests, use tools, and when to stop.

@app.route("/api/chat", methods=["POST"])
def chat():
    """POST /api/chat - extracts links from the user prompt, runs the agent loop, and returns the response."""
    data = request.get_json(silent=True) or {}
    user_msg = data.get("prompt", "").strip()
    history = data.get("history", [])
    user_auth = normalize_user_auth(data.get("user_auth", {}))
    if not user_msg:
        return jsonify({"error": "Empty prompt"}), 400
    # Extract detected links from the current prompt first
    page_ids = _extract_confluence_page_ids_from_text(user_msg)
    prs = _extract_prs_from_text(user_msg)
    link_source = "current_message"
    
    # Check if credentials are set before attempting to review links
    if page_ids and (not user_auth or not user_auth.get("confluence_email") or not user_auth.get("confluence_api_token")):
        return jsonify({"error": "Confluence credentials not configured. Please set them up in Account Settings."}), 401
    if prs and (not user_auth or not user_auth.get("github_owner") or not user_auth.get("github_token")):
        return jsonify({"error": "GitHub credentials not configured. Please set them up in Account Settings."}), 401
    
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
    # Try direct fast path for lightweight Confluence spelling tasks.
    set_active_user_auth(user_auth)
    try:
        try:
            fast_response = _try_fast_confluence_spelling_review(user_msg, history, page_ids)
            if fast_response:
                return jsonify({"response": fast_response, "detected": detected if detected else None, "mode": "fast_path"})
        except Exception as e:
            print(f"[FAST_PATH] Failed and falling back to agent: {e}", flush=True)
        # Run the agent loop
        try:
            response = run_agent(user_msg, history, link_context)
        except Exception as e:
            print(f"[AGENT] Unhandled error: {e}", flush=True)
            return jsonify({"error": "Something went wrong. Please try again.", "detected": detected}), 500
        return jsonify({"response": response, "detected": detected if detected else None})
    finally:
        clear_active_user_auth()
# --- Feedback API Endpoint ---
# Lets users report false positives (e.g. a term wrongly flagged as context noise).
# The feedback is stored in a JSON file and loaded into the AI's context on future requests.
@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    """POST /api/feedback - records user feedback about a flagged term to the feedback log file."""
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

# --- PR Review Panel API ---
@app.route("/api/pr-checklist", methods=["GET"])
def get_pr_checklist():
    """GET /api/pr-checklist - returns default PR review checklist."""
    checklist = _build_universal_pr_review_checklist()
    return jsonify({
        "checklist": [
            {"name": item.get("name"), "id": item.get("id")}
            for item in checklist
        ]
    })

@app.route("/api/parse-pr", methods=["POST"])
def parse_pr():
    """POST /api/parse-pr - parses a GitHub PR URL."""
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    
    match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not match:
        return jsonify({"error": "Invalid PR URL format"}), 400
    
    owner, repo, pr_number = match.groups()
    return jsonify({
        "success": True,
        "owner": owner,
        "repo": repo,
        "pr_number": int(pr_number),
        "url": url
    })



@app.route("/api/review-stream", methods=["POST"])
def review_stream():
    """POST /api/review-stream - streams PR review progress as Server-Sent Events."""
    data = request.get_json(silent=True) or {}
    user_msg = data.get("prompt", "").strip()
    panel_checklist = data.get("checklist", [])  # List of checked item names from panel
    panel_outputs = data.get("outputs", [])      # List of expected output types from panel
    user_auth = normalize_user_auth(data.get("user_auth", {}))
    github_base_url = data.get("github_base_url")  # Extracted from the link by frontend

    if not user_msg:
        return jsonify({"error": "Empty prompt"}), 400

    def generate():
        import queue

        def send_event(event_type, message):
            return f"data: {json.dumps({'type': event_type, 'message': message})}\n\n"

        yield send_event("progress", "Parsing PR link...")

        prs = _extract_prs_from_text(user_msg)
        if not prs:
            yield send_event("error", "No GitHub PR link found in your message.")
            return

        pr = prs[0]
        repo_full = f"{pr['owner']}/{pr['repo']}"
        pr_num = pr['pr_number']

        yield send_event("progress", f"Detected PR: {repo_full}#{pr_num}")

        # --- Step 1: Fetch changed files (fast, gives us file list for progress) ---
        yield send_event("progress", "Fetching changed files...")
        try:
            files_temp = {"repo": repo_full, "pr_number": pr_num, "__user_auth": user_auth}
            if github_base_url:
                files_temp["__github_base_url"] = github_base_url
            files_result = TOOL_REGISTRY["get_files_in_pr_tool"](files_temp)
            file_list = []
            if isinstance(files_result, dict) and files_result.get("success"):
                file_list = files_result.get("data", [])
                yield send_event("progress", f"Found {len(file_list)} changed file(s)")
                for f_obj in file_list[:15]:
                    fname = f_obj.get("filename", "") if isinstance(f_obj, dict) else str(f_obj)
                    if fname:
                        yield send_event("progress", f"  \u2022 {fname}")
            else:
                yield send_event("progress", "Could not list files, continuing...")
        except Exception as e:
            yield send_event("progress", f"File listing error: {e}")
            file_list = []

        # --- Step 2: Run the full review tool in a background thread ---
        yield send_event("progress", "Running checklist analysis (flake8, conventions, consistency)...")
        yield send_event("progress", "This step analyzes all files and posts comments — please wait...")

        result_queue = queue.Queue()
        if panel_checklist:
            checklist = _build_checklist_from_panel(panel_checklist)
        else:
            checklist = _get_cached_pr_checklist()

        # Track how many stderr lines we've already sent so we only send new ones
        _stderr_read_idx = len(mcp_client.stderr_lines)

        # Determine which outputs to skip based on panel selections
        _want_inline = any("inline" in o.lower() for o in panel_outputs) if panel_outputs else True
        _want_footer = any("summary" in o.lower() or "general comment" in o.lower() for o in panel_outputs) if panel_outputs else True
        _skip_inline = not _want_inline
        _skip_footer = not _want_footer

        def _run_review():
            try:
                review_args = {
                    "repo": repo_full,
                    "pr_number": int(pr_num),
                    "checklist": checklist,
                    "skip_inline": _skip_inline,
                    "skip_footer": _skip_footer,
                    "__user_auth": user_auth,
                }
                if github_base_url:
                    review_args["__github_base_url"] = github_base_url
                result = TOOL_REGISTRY["review_pull_request_tool"](review_args)
                result_queue.put(("ok", result))
            except Exception as e:
                result_queue.put(("error", str(e)))

        review_thread = threading.Thread(target=_run_review, daemon=True)
        started_at = time.time()
        review_thread.start()

        _inline_count = 0
        _last_heartbeat = -1
        while review_thread.is_alive():
            review_thread.join(timeout=1)
            # Read new [REVIEW] messages from MCP server stderr
            new_lines = mcp_client.stderr_lines[_stderr_read_idx:]
            _stderr_read_idx = len(mcp_client.stderr_lines)
            sent_any = False
            for line in new_lines:
                if "[REVIEW]" in line:
                    cleaned = _clean_review_line(line)
                    if cleaned is not None:
                        yield send_event("progress", cleaned)
                        sent_any = True
                    elif "Inline comment posted" in line:
                        _inline_count += 1
            if not review_thread.is_alive():
                break
            # Heartbeat every ~10s if no log messages
            elapsed = int(time.time() - started_at)
            heartbeat_bucket = elapsed // 10
            if not sent_any and heartbeat_bucket > _last_heartbeat:
                _last_heartbeat = heartbeat_bucket
                yield send_event("progress", f"  \u23f3 Still working... ({elapsed}s elapsed)")

        # Drain any remaining stderr lines after thread finishes
        for line in mcp_client.stderr_lines[_stderr_read_idx:]:
            if "[REVIEW]" in line:
                cleaned = _clean_review_line(line)
                if cleaned is not None:
                    yield send_event("progress", cleaned)
                elif "Inline comment posted" in line:
                    _inline_count += 1
        if _inline_count > 0:
            yield send_event("progress", f"  Posted {_inline_count} inline comment(s)")

        elapsed_s = time.time() - started_at

        # Get the result
        try:
            status, payload = result_queue.get_nowait()
        except queue.Empty:
            yield send_event("error", "Review thread completed but produced no result.")
            return

        if status == "error":
            yield send_event("error", f"Review tool crashed: {payload}")
            return

        result = payload
        if isinstance(result, dict) and result.get("success"):
            data_r = result.get("data", {})
            summary = data_r.get("summary", "") if isinstance(data_r, dict) else str(data_r)
            reviewed = data_r.get("reviewed_items", []) if isinstance(data_r, dict) else []
            flake8_v = data_r.get("flake8_violations", 0) if isinstance(data_r, dict) else 0
            conv_v = data_r.get("convention_issues", 0) if isinstance(data_r, dict) else 0
            consist_v = data_r.get("consistency_issues", 0) if isinstance(data_r, dict) else 0
            inline_posted = data_r.get("inline_comments_posted", 0) if isinstance(data_r, dict) else 0

            yield send_event("progress", f"\u2705 Review complete in {elapsed_s:.1f}s")
            if flake8_v > 0:
                yield send_event("progress", f"  Flake8 violations: {flake8_v}")
            if conv_v > 0:
                yield send_event("progress", f"  Convention issues: {conv_v}")
            if consist_v > 0:
                yield send_event("progress", f"  Consistency issues: {consist_v}")
            if inline_posted > 0:
                yield send_event("progress", f"  Inline comments posted: {inline_posted}")

            lines = [f"Completed PR review for {repo_full}#{pr_num} in {elapsed_s:.1f}s."]
            if reviewed:
                lines.append(f"Reviewed: {', '.join(reviewed)}")
            if summary:
                lines.append(summary)
            else:
                lines.append("Review comments (inline + summary) have been posted to the PR.")
            yield send_event("done", "\n".join(lines))
        else:
            error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
            yield send_event("error", f"Review failed: {error}")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        direct_passthrough=False,
    )



# --- SSE Confluence Review Stream Endpoint ---
@app.route("/api/confluence-review-stream", methods=["POST"])
def confluence_review_stream():
    """POST /api/confluence-review-stream - streams Confluence page review progress as SSE."""
    data = request.get_json(silent=True) or {}
    page_id = (data.get("page_id") or "").strip()
    page_input = (data.get("page_input") or "").strip()
    doc_type = (data.get("doc_type") or "").strip()
    checklist = data.get("checklist", [])
    outputs = data.get("outputs", [])
    user_auth = normalize_user_auth(data.get("user_auth", {}))
    confluence_base_url = data.get("confluence_base_url")  # Extracted from the link by frontend

    if not page_id and not page_input:
        return jsonify({"error": "No page ID or URL provided"}), 400

    def generate():
        import queue as _q

        def send_event(event_type, message):
            return f"data: {json.dumps({'type': event_type, 'message': message})}\n\n"

        yield send_event("progress", "Parsing Confluence page input...")

        # Use page_id if available, otherwise extract from page_input
        resolved_id = page_id
        if not resolved_id:
            import re as _re
            m = _re.search(r'pages/(\d+)', page_input)
            if m:
                resolved_id = m.group(1)
            elif page_input.isdigit():
                resolved_id = page_input

        if not resolved_id:
            yield send_event("error", "Could not extract a page ID from the input.")
            return

        yield send_event("progress", f"Page ID: {resolved_id}")

        # --- Step 1: Fetch page content to confirm it exists ---
        yield send_event("progress", "Fetching page content...")
        try:
            page_result = TOOL_REGISTRY["get_confluence_page_content"]({"page_id": resolved_id, "__user_auth": user_auth})
            if isinstance(page_result, dict) and page_result.get("success"):
                page_data = page_result.get("data", {})
                title = page_data.get("title", "Unknown") if isinstance(page_data, dict) else "Unknown"
                yield send_event("progress", f"Page found: {title}")
            else:
                err = page_result.get("error", "Unknown") if isinstance(page_result, dict) else str(page_result)
                yield send_event("error", f"Could not fetch page: {err}")
                return
        except Exception as e:
            yield send_event("error", f"Error fetching page: {e}")
            return

        if doc_type:
            yield send_event("progress", f"Document type: {doc_type}")

        if checklist:
            yield send_event("progress", f"Checklist items: {len(checklist)} selected")

        # --- Step 2: Run the review in a background thread ---
        yield send_event("progress", "Starting page review (spelling, grammar, readability, structure)...")
        yield send_event("progress", "This may take a moment — please wait...")

        result_queue = _q.Queue()

        # Track stderr position for reading [REVIEW] messages from MCP server
        _stderr_read_idx = len(mcp_client.stderr_lines)

        # Determine which output types to skip based on user's selections
        want_inline = any("inline" in o.lower() for o in outputs)
        want_footer = any("footer" in o.lower() or "summary" in o.lower() for o in outputs)
        _skip_inline = not want_inline
        _skip_footer = not want_footer

        def _run_confluence_review():
            try:
                review_args = {"page_input": resolved_id, "skip_inline": _skip_inline, "skip_footer": _skip_footer, "__user_auth": user_auth}
                if confluence_base_url:
                    review_args["__confluence_base_url"] = confluence_base_url
                result = TOOL_REGISTRY["review_confluence_page_content"](review_args)
                result_queue.put(("ok", result))
            except Exception as e:
                result_queue.put(("error", str(e)))

        review_thread = threading.Thread(target=_run_confluence_review, daemon=True)
        started_at = time.time()
        review_thread.start()

        _inline_count = 0
        _last_heartbeat = -1
        while review_thread.is_alive():
            review_thread.join(timeout=1)
            # Read new [REVIEW] messages from MCP server stderr
            new_lines = mcp_client.stderr_lines[_stderr_read_idx:]
            _stderr_read_idx = len(mcp_client.stderr_lines)
            sent_any = False
            for line in new_lines:
                if "[REVIEW]" in line:
                    cleaned = _clean_review_line(line)
                    if cleaned is not None:
                        yield send_event("progress", cleaned)
                        sent_any = True
                    elif "Inline comment posted" in line:
                        _inline_count += 1
            if _inline_count > 0 and _inline_count % 10 == 0:
                yield send_event("progress", f"  Posted {_inline_count} inline comment(s) so far...")
                sent_any = True
            if not review_thread.is_alive():
                break
            # Heartbeat every ~10s if no log messages
            elapsed = int(time.time() - started_at)
            heartbeat_bucket = elapsed // 10
            if not sent_any and heartbeat_bucket > _last_heartbeat:
                _last_heartbeat = heartbeat_bucket
                yield send_event("progress", f"  \u23f3 Still working... ({elapsed}s elapsed)")

        # Drain any remaining stderr lines after thread finishes
        for line in mcp_client.stderr_lines[_stderr_read_idx:]:
            if "[REVIEW]" in line:
                cleaned = _clean_review_line(line)
                if cleaned is not None:
                    yield send_event("progress", cleaned)
                elif "Inline comment posted" in line:
                    _inline_count += 1
        if _inline_count > 0:
            yield send_event("progress", f"  Posted {_inline_count} inline comment(s)")

        elapsed_s = time.time() - started_at

        try:
            status, payload = result_queue.get_nowait()
        except _q.Empty:
            yield send_event("error", "Review thread completed but produced no result.")
            return

        if status == "error":
            yield send_event("error", f"Review failed: {payload}")
            return

        result = payload
        if isinstance(result, dict) and result.get("success"):
            data_r = result.get("data", {})
            if isinstance(data_r, dict):
                summary = data_r.get("summary", "")
                total_issues = data_r.get("total_issues", 0)
                inline_posted = data_r.get("inline_comments_posted", 0)
                footer_posted = data_r.get("footer_comment_posted", False)
            else:
                summary = str(data_r)
                total_issues = 0
                inline_posted = 0
                footer_posted = False

            yield send_event("progress", f"\u2705 Review complete in {elapsed_s:.1f}s")
            if total_issues > 0:
                yield send_event("progress", f"  Issues found: {total_issues}")
            if inline_posted > 0:
                yield send_event("progress", f"  Inline comments posted: {inline_posted}")
            if footer_posted:
                yield send_event("progress", "  Footer summary posted to page")

            lines = [f"Completed Confluence page review for page {resolved_id} in {elapsed_s:.1f}s."]
            if summary:
                lines.append(summary)
            else:
                lines.append("Review comments have been posted to the Confluence page.")
            yield send_event("done", "\n".join(lines))
        else:
            error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
            yield send_event("error", f"Review failed: {error}")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        direct_passthrough=False,
    )

# --- Shutdown Handler ---
atexit.register(lambda: mcp_client.shutdown())

# --- Main Entry Point ---
if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
