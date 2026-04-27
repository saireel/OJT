from flask import render_template, request, jsonify, Response, stream_with_context
from typing import TYPE_CHECKING

# Lightweight stdlib imports available at module level for static analysis.
from datetime import datetime
import threading
import time
import json
import re
import queue


if TYPE_CHECKING:
    import requests
    from .mcp_runtime import TOOL_REGISTRY, clear_active_user_auth, get_active_user_auth, mcp_client, normalize_user_auth, set_active_user_auth
    from .review_logic import _build_checklist_from_panel, _extract_prs_from_text, _get_cached_chat_link_metadata, _get_cached_pr_checklist, _get_cached_pr_files, _make_review_coalesce_key, _run_review_with_coalescing, _try_fast_confluence_spelling_review, _extract_base_urls_from_text, _normalize_confluence_base_url, _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id, _try_fast_smalltalk_response
    from mcp_calls import STRICT_INLINE_COMMENT_LIMIT as _STRICT_INLINE_COMMENT_LIMIT
    from .agent_engine import _clean_review_line, run_agent

def register_routes(app):
    # Lazy imports to avoid heavy module loading at import time
    def _lazy_imports():
        # Import standard library modules used by route handlers
        global sys, threading, time, json, requests, re, queue
        import sys, threading, time, json, requests, re, queue
        global TOOL_REGISTRY, clear_active_user_auth, get_active_user_auth, mcp_client, normalize_user_auth, set_active_user_auth
        global _build_checklist_from_panel, _extract_prs_from_text, _get_cached_chat_link_metadata, _get_cached_pr_checklist, _get_cached_pr_files
        global _make_review_coalesce_key, _run_review_with_coalescing, _try_fast_confluence_spelling_review
        global _extract_base_urls_from_text, _normalize_confluence_base_url, _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id, _try_fast_smalltalk_response
        global _clean_review_line, run_agent, _STRICT_INLINE_COMMENT_LIMIT
        try:
            from .mcp_runtime import (
                TOOL_REGISTRY, clear_active_user_auth, get_active_user_auth, mcp_client, normalize_user_auth, set_active_user_auth
            )
            from .review_logic import (
                _build_checklist_from_panel, _extract_prs_from_text, _get_cached_chat_link_metadata,
                _get_cached_pr_checklist, _get_cached_pr_files, _make_review_coalesce_key, _run_review_with_coalescing,
                _try_fast_confluence_spelling_review, _extract_base_urls_from_text, _normalize_confluence_base_url,
                _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id, _try_fast_smalltalk_response,
            )
            from .agent_engine import _clean_review_line, run_agent
            from mcp_calls import STRICT_INLINE_COMMENT_LIMIT as _STRICT_INLINE_COMMENT_LIMIT
        except Exception:
            # fallback to absolute imports
            from mcp_runtime import (
                TOOL_REGISTRY, clear_active_user_auth, get_active_user_auth, mcp_client, normalize_user_auth, set_active_user_auth
            )
            from review_logic import (
                _build_checklist_from_panel, _extract_prs_from_text, _get_cached_chat_link_metadata,
                _get_cached_pr_checklist, _get_cached_pr_files, _make_review_coalesce_key, _run_review_with_coalescing,
                _try_fast_confluence_spelling_review, _extract_base_urls_from_text, _normalize_confluence_base_url,
                _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id, _try_fast_smalltalk_response,
            )
            from agent_engine import _clean_review_line, run_agent
            from mcp_calls import STRICT_INLINE_COMMENT_LIMIT as _STRICT_INLINE_COMMENT_LIMIT
    def index():
        _lazy_imports()
        # Renders the main web page (index.html)
        """Renders and returns the main index.html page."""
        welcome_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return render_template("index.html", welcome_timestamp=welcome_timestamp)
    def quick_user_guide():
        """Renders the quick user guide page."""
        return render_template("quick_user_guide.html")
    def chat():
        """POST /api/chat - extracts links from the user prompt, runs the agent loop, and returns the response."""
        _lazy_imports()
        data = request.get_json(silent=True) or {}
        user_msg = data.get("prompt", "").strip()
        history = data.get("history", [])
        user_auth = normalize_user_auth(data.get("user_auth", {}))
        user_auth = _augment_user_auth_with_detected_base_urls(user_auth, user_msg, history)
        review_type = (data.get("review_type") or "").strip()
        doc_type = (data.get("doc_type") or "").strip()
        checklist = data.get("checklist", [])
        outputs = data.get("outputs", [])
        confluence_checklist_page_id = _resolve_confluence_checklist_page_id(checklist) or (data.get("confluence_checklist_page_id") or "").strip()
        if not user_msg:
            return jsonify({"error": "Empty prompt"}), 400

        fast_smalltalk = _try_fast_smalltalk_response(user_msg)
        if fast_smalltalk:
            return jsonify({"response": fast_smalltalk, "mode": "fast_smalltalk"})
        link_meta, link_meta_from_cache = _get_cached_chat_link_metadata(user_msg, history)
        page_ids = link_meta.get("page_ids", [])
        prs = link_meta.get("prs", [])
        link_source = link_meta.get("link_source", "current_message")

        # Check if credentials are set before attempting to review links
        if page_ids and (not user_auth or not user_auth.get("confluence_email") or not user_auth.get("confluence_api_token")):
            return jsonify({"error": "Confluence credentials not configured. Please set them up in Account Settings."}), 401
        if prs and (not user_auth or not user_auth.get("github_owner") or not user_auth.get("github_token")):
            return jsonify({"error": "GitHub credentials not configured. Please set them up in Account Settings."}), 401
    
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
        if link_meta_from_cache:
            link_context += "Reused cached link metadata for this chat request.\n\n"
        if review_type:
            link_context += f"Review type: {review_type}\n"
        if doc_type:
            link_context += f"Document type: {doc_type}\n"
        if checklist:
            link_context += "Checklist items: " + ", ".join(str(item) for item in checklist) + "\n"
        if outputs:
            link_context += "Expected outputs: " + ", ".join(str(item) for item in outputs) + "\n"
        if confluence_checklist_page_id:
            link_context += f"Confluence checklist page: {confluence_checklist_page_id}\n"
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
                response = run_agent(
                    user_msg,
                    history,
                    link_context,
                    request_meta={
                        "page_ids": page_ids,
                        "prs": prs,
                        "review_type": review_type,
                        "doc_type": doc_type,
                        "checklist": checklist,
                        "outputs": outputs,
                        "confluence_checklist_page_id": confluence_checklist_page_id,
                        "link_source": link_source,
                    },
                )
            except Exception as e:
                print(f"[AGENT] Unhandled error: {e}", flush=True)
                return jsonify({"error": "Something went wrong. Please try again.", "detected": detected}), 500
            return jsonify({"response": response, "detected": detected if detected else None})
        finally:
            clear_active_user_auth()

    def chat_stream():
        """POST /api/chat-stream - streams agent progress as SSE, then delivers the final answer instantly."""
        _lazy_imports()
        data = request.get_json(silent=True) or {}
        user_msg = data.get("prompt", "").strip()
        history = data.get("history", [])
        user_auth = normalize_user_auth(data.get("user_auth", {}))
        user_auth = _augment_user_auth_with_detected_base_urls(user_auth, user_msg, history)
        review_type = (data.get("review_type") or "").strip()
        doc_type = (data.get("doc_type") or "").strip()
        checklist = data.get("checklist", [])
        outputs = data.get("outputs", [])
        confluence_checklist_page_id = _resolve_confluence_checklist_page_id(checklist) or (data.get("confluence_checklist_page_id") or "").strip()

        if not user_msg:
            def _err_gen():
                yield 'data: {"type":"error","message":"Empty prompt"}\n\n'
            return Response(stream_with_context(_err_gen()), mimetype="text/event-stream")

        def generate():
            q = queue.Queue()
            SENTINEL = object()

            def _fmt(event_type, message):
                return "data: " + json.dumps({"type": event_type, "message": message}) + "\n\n"

            def _run():
                try:
                    from . import app_logic as _al
                except ImportError:
                    import app_logic as _al
                _al.set_active_user_auth(user_auth)
                try:
                    link_meta, link_meta_from_cache = _get_cached_chat_link_metadata(user_msg, history)
                    page_ids = link_meta.get("page_ids", [])
                    prs = link_meta.get("prs", [])
                    link_source = link_meta.get("link_source", "current_message")
                    link_context = ""
                    if page_ids:
                        hdr = "Detected Confluence page IDs from recent conversation history (follow-up context):\n" if link_source == "history_fallback" else "Detected Confluence page IDs from the user's message:\n"
                        link_context += hdr
                        for idx, pid in enumerate(page_ids):
                            link_context += "  Link {}: page_id = \"{}\"\n".format(idx + 1, pid)
                        link_context += "\n"
                    if prs:
                        hdr = "Detected GitHub PRs from recent conversation history (follow-up context):\n" if link_source == "history_fallback" else "Detected GitHub PRs from the user's message:\n"
                        link_context += hdr
                        for pr in prs:
                            link_context += "  PR: {}/{}#{}\n".format(pr["owner"], pr["repo"], pr["pr_number"])
                        link_context += "\n"
                    if link_meta_from_cache:
                        link_context += "Reused cached link metadata for this chat request.\n\n"
                    if review_type:
                        link_context += "Review type: {}\n".format(review_type)
                    if doc_type:
                        link_context += "Document type: {}\n".format(doc_type)
                    if checklist:
                        link_context += "Checklist items: " + ", ".join(str(item) for item in checklist) + "\n"
                    if outputs:
                        link_context += "Expected outputs: " + ", ".join(str(item) for item in outputs) + "\n"
                    if confluence_checklist_page_id:
                        link_context += "Confluence checklist page: {}\n".format(confluence_checklist_page_id)
                    detected = ["confluence:{}".format(pid) for pid in page_ids]
                    detected += ["pr:{}/{}#{}".format(pr["owner"], pr["repo"], pr["pr_number"]) for pr in prs]

                    def _progress(msg):
                        q.put(("progress", msg))

                    response = _al.run_agent(
                        user_msg, history, link_context,
                        request_meta={
                            "page_ids": page_ids,
                            "prs": prs,
                            "review_type": review_type,
                            "doc_type": doc_type,
                            "checklist": checklist,
                            "outputs": outputs,
                            "confluence_checklist_page_id": confluence_checklist_page_id,
                            "link_source": link_source,
                        },
                        progress_callback=_progress,
                    )
                    q.put(("done", json.dumps({"response": response, "detected": detected or None})))
                except Exception as exc:
                    q.put(("error", str(exc)))
                    q.put(SENTINEL)

            threading.Thread(target=_run, daemon=True).start()

            while True:
                try:
                    item = q.get(timeout=30)
                except queue.Empty:
                    yield _fmt("heartbeat", "")
                    continue
                if item is SENTINEL:
                    break
                event_type, payload = item
                yield _fmt(event_type, payload)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )
    def get_pr_checklist():
        """GET /api/pr-checklist - returns default PR review checklist."""
        _lazy_imports()
        checklist = _get_cached_pr_checklist()
        return jsonify({
            "checklist": [
                {"name": item.get("name"), "id": item.get("id")}
                for item in checklist
            ]
        })
    def test_connections():
        """POST /api/test-connections - validates GitHub and Confluence credentials."""
        _lazy_imports()
        data = request.get_json(silent=True) or {}
        user_auth = normalize_user_auth(data.get("user_auth", {}))

        results = {
            "github": {"state": "unknown", "message": "Not tested"},
            "confluence": {"state": "unknown", "message": "Not tested"},
        }

        # GitHub test
        gh_owner = (user_auth.get("github_owner") or "").strip()
        gh_token = (user_auth.get("github_token") or "").strip()
        gh_base = (user_auth.get("github_base_url") or "https://api.github.com").strip().rstrip("/")
        if not gh_owner or not gh_token:
            results["github"] = {
                "state": "missing",
                "message": "GitHub owner/token is missing. PR parsing and review posting will fail.",
            }
        else:
            try:
                gh_resp = requests.get(
                    f"{gh_base}/user",
                    headers={
                        "Authorization": f"token {gh_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "MunnAI-Connection-Test",
                    },
                    timeout=15,
                )
                if gh_resp.ok:
                    login = ""
                    try:
                        login = (gh_resp.json() or {}).get("login") or ""
                    except Exception:
                        login = ""
                    msg = "GitHub connection OK"
                    if login:
                        msg += f" (authenticated as {login})"
                    results["github"] = {"state": "valid", "message": msg}
                else:
                    body = (gh_resp.text or "").strip().replace("\n", " ")[:220]
                    results["github"] = {
                        "state": "invalid",
                        "message": f"GitHub auth failed ({gh_resp.status_code}). {body or 'Check token scopes and owner.'}",
                    }
            except Exception as exc:
                results["github"] = {
                    "state": "invalid",
                    "message": f"GitHub connection error: {exc}",
                }

        # Confluence test
        conf_email = (user_auth.get("confluence_email") or "").strip()
        conf_token = (user_auth.get("confluence_api_token") or "").strip()
        conf_base = _normalize_confluence_base_url(user_auth.get("confluence_base_url"))
        if not conf_email or not conf_token:
            results["confluence"] = {
                "state": "missing",
                "message": "Confluence email/token is missing. Page fetch and comment actions will fail.",
            }
        elif not conf_base:
            results["confluence"] = {
                "state": "missing",
                "message": "Confluence base URL is missing. Set it (for example https://your-domain.atlassian.net/wiki).",
            }
        else:
            try:
                conf_resp = requests.get(
                    f"{conf_base}/rest/api/space",
                    params={"limit": 1},
                    auth=(conf_email, conf_token),
                    headers={"Accept": "application/json"},
                    timeout=15,
                )
                if conf_resp.ok:
                    results["confluence"] = {"state": "valid", "message": "Confluence connection OK"}
                else:
                    body = (conf_resp.text or "").strip().replace("\n", " ")[:220]
                    results["confluence"] = {
                        "state": "invalid",
                        "message": f"Confluence auth failed ({conf_resp.status_code}). {body or 'Check email/token/base URL.'}",
                    }
            except Exception as exc:
                results["confluence"] = {
                    "state": "invalid",
                    "message": f"Confluence connection error: {exc}",
                }

        return jsonify({"success": True, "results": results})
    def parse_pr():
        """POST /api/parse-pr - parses a GitHub PR URL or Confluence page link/ID."""
        _lazy_imports()
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()

        if not url:
            return jsonify({"error": "Please provide a link or identifier to parse."}), 400

        gh_match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url, re.IGNORECASE)
        if gh_match:
            owner, repo, pr_number = gh_match.groups()
            return jsonify({
                "success": True,
                "kind": "github_pr",
                "owner": owner,
                "repo": repo,
                "pr_number": int(pr_number),
                "url": f"https://github.com/{owner}/{repo}/pull/{pr_number}",
            })

        short_match = re.match(r"^([^\s/#]+)/([^\s/#]+)#(\d+)$", url)
        if short_match:
            owner, repo, pr_number = short_match.groups()
            return jsonify({
                "success": True,
                "kind": "github_pr",
                "owner": owner,
                "repo": repo,
                "pr_number": int(pr_number),
                "url": f"https://github.com/{owner}/{repo}/pull/{pr_number}",
            })

        conf_match = re.search(r"(?:/pages/|pageId=)(\d+)", url, re.IGNORECASE)
        if conf_match:
            page_id = conf_match.group(1)
            return jsonify({
                "success": True,
                "kind": "confluence_page",
                "page_id": page_id,
                "url": url,
            })

        if re.match(r"^\d+$", url):
            return jsonify({
                "success": True,
                "kind": "confluence_page",
                "page_id": url,
                "url": url,
            })

        return jsonify({
            "error": (
                "Invalid format. Supported examples: "
                "https://github.com/owner/repo/pull/123, owner/repo#123, "
                "Confluence URLs with /pages/<id> or ?pageId=<id>, or a numeric page ID."
            )
        }), 400
    def review_stream():
        """POST /api/review-stream - streams PR review progress as Server-Sent Events."""
        _lazy_imports()
        data = request.get_json(silent=True) or {}
        user_msg = data.get("prompt", "").strip()
        panel_checklist = data.get("checklist", [])
        user_auth = normalize_user_auth(data.get("user_auth", {}))
        github_base_url = data.get("github_base_url")  # Extracted from the link by frontend
        if not github_base_url:
            gh_detected, _ = _extract_base_urls_from_text(user_msg)
            github_base_url = gh_detected or user_auth.get("github_base_url")

        if not user_msg:
            return jsonify({"error": "Empty prompt"}), 400

        def generate():
            def send_event(event_type, message):
            
                return f"data: {json.dumps({'type': event_type, 'message': message})}\n\n"
        
            # Check credentials upfront
            if not user_auth or not user_auth.get("github_owner") or not user_auth.get("github_token"):
                yield send_event("error", "GitHub credentials not configured. Please set them up in Account Settings.")
                return

            yield send_event("progress", "Parsing PR link...")

            prs = _extract_prs_from_text(user_msg)
            if not prs:
                yield send_event("error", "No GitHub PR link found in your message.")
                return

            pr = prs[0]
            repo_full = f"{pr['owner']}/{pr['repo']}"
            pr_num = pr['pr_number']

            yield send_event("progress", f"Detected PR: {repo_full}#{pr_num}")

            # --- Step 1: Fetch changed files (cached to avoid duplicate API calls) ---
            yield send_event("progress", "Fetching changed files...")
            try:
                file_list, from_cache = _get_cached_pr_files(repo_full, pr_num, user_auth, github_base_url)
                if isinstance(file_list, list) and file_list:
                    cache_note = " (cached)" if from_cache else ""
                    yield send_event("progress", f"Found {len(file_list)} changed file(s){cache_note}")
                    for f_obj in file_list[:15]:
                        fname = f_obj.get("filename", "") if isinstance(f_obj, dict) else str(f_obj)
                        if fname:
                            add = int(f_obj.get("additions", 0) or 0) if isinstance(f_obj, dict) else 0
                            delete = int(f_obj.get("deletions", 0) or 0) if isinstance(f_obj, dict) else 0
                            yield send_event("progress", f"  • {fname} (+{add}/-{delete})")
                    if len(file_list) > 15:
                        yield send_event("progress", f"  ... and {len(file_list)-15} more file(s)")
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

            total_changed_lines = sum(
                int(f.get("additions", 0) or 0) + int(f.get("deletions", 0) or 0)
                for f in (file_list or [])
                if isinstance(f, dict)
            )
            # High-confidence mode is enforced: never defer checks for speed.
            large_pr = len(file_list or []) >= 25 or total_changed_lines >= 1800
            if checklist and large_pr:
                yield send_event(
                    "progress",
                    "High-confidence mode active: running full checklist on a large PR.",
                )

            # Track how many stderr lines we've already sent so we only send new ones
            _stderr_read_idx = len(mcp_client.stderr_lines)

            # High-confidence mode is enforced for PR reviews.
            _skip_inline = False
            _skip_footer = False
            _max_inline_comments = _STRICT_INLINE_COMMENT_LIMIT
            _group_similar_inline = True

            def _run_review():
                try:
                    review_args = {
                        "repo": repo_full,
                        "pr_number": int(pr_num),
                        "checklist": checklist,
                        "skip_inline": _skip_inline,
                        "skip_footer": _skip_footer,
                        "max_inline_comments": _max_inline_comments,
                        "group_similar_inline": _group_similar_inline,
                        "__user_auth": user_auth,
                    }
                    if github_base_url:
                        review_args["__github_base_url"] = github_base_url

                    review_key = _make_review_coalesce_key(
                        repo_full,
                        int(pr_num),
                        checklist=checklist,
                        skip_inline=_skip_inline,
                        skip_footer=_skip_footer,
                        max_inline_comments=_max_inline_comments,
                        group_similar_inline=_group_similar_inline,
                        github_base_url=github_base_url,
                    )

                    def _invoke_review():
                        return TOOL_REGISTRY["review_pull_request"](review_args)

                    print(f"[REVIEW] About to call _run_review_with_coalescing", file=sys.stderr, flush=True)
                    result, reused_inflight, waited_s = _run_review_with_coalescing(review_key, _invoke_review)
                    print(f"[REVIEW] _run_review_with_coalescing completed, result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}", file=sys.stderr, flush=True)
                    result_queue.put(("ok", {
                        "result": result,
                        "reused_inflight": reused_inflight,
                        "waited_s": waited_s,
                    }))
                except Exception as e:
                    print(f"[REVIEW] Exception in _run_review: {e}", file=sys.stderr, flush=True)
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

            reused_inflight = False
            waited_s = 0.0
            result = payload
            if isinstance(payload,  dict) and "result" in payload:
                result = payload.get("result")
                reused_inflight = bool(payload.get("reused_inflight", False))
                try:
                    waited_s = float(payload.get("waited_s", 0.0) or 0.0)
                except Exception:
                    waited_s = 0.0

            if isinstance(result, dict) and result.get("success"):
                data_r = result.get("data", {})
                summary = data_r.get("summary", "") if isinstance(data_r, dict) else str(data_r)
                reviewed = data_r.get("reviewed_items", []) if isinstance(data_r, dict) else []
                flake8_v = data_r.get("flake8_violations", 0) if isinstance(data_r, dict) else 0
                conv_v = data_r.get("convention_issues", 0) if isinstance(data_r, dict) else 0
                consist_v = data_r.get("consistency_issues", 0) if isinstance(data_r, dict) else 0
                inline_posted = data_r.get("inline_comments_posted", 0) if isinstance(data_r, dict) else 0
                inline_total = data_r.get("inline_candidates_total", 0) if isinstance(data_r, dict) else 0
                inline_selected = data_r.get("inline_candidates_selected", 0) if isinstance(data_r, dict) else 0

                yield send_event("progress", f"\u2705 Review complete in {elapsed_s:.1f}s")
                if reused_inflight:
                    yield send_event("progress", f"  Reused in-flight result for duplicate request (waited {waited_s:.1f}s)")
                if flake8_v > 0:
                    yield send_event("progress", f"  Flake8 violations: {flake8_v}")
                if conv_v > 0:
                    yield send_event("progress", f"  Convention issues: {conv_v}")
                if consist_v > 0:
                    yield send_event("progress", f"  Consistency issues: {consist_v}")
                if inline_posted > 0:
                    yield send_event("progress", f"  Inline comments posted: {inline_posted}")
                if inline_total > inline_selected:
                    yield send_event("progress", f"  Inline candidates reduced: {inline_total} -> {inline_selected} (grouped/capped)")

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
    def confluence_review_stream():
        """POST /api/confluence-review-stream - streams Confluence page review progress as SSE."""
        _lazy_imports()
        data = request.get_json(silent=True) or {}
        page_id = (data.get("page_id") or "").strip()
        page_input = (data.get("page_input") or "").strip()
        doc_type = (data.get("doc_type") or "").strip()
        checklist = data.get("checklist", [])
        outputs = data.get("outputs", [])
        user_auth = normalize_user_auth(data.get("user_auth", {}))
        confluence_checklist_page_id = _resolve_confluence_checklist_page_id(checklist)
        confluence_base_url = _normalize_confluence_base_url(data.get("confluence_base_url"))  # Extracted from link/frontend input
        if not confluence_base_url:
            _, conf_detected = _extract_base_urls_from_text(page_input)
            confluence_base_url = conf_detected or _normalize_confluence_base_url(user_auth.get("confluence_base_url"))

        if not page_id and not page_input:
            return jsonify({"error": "No page ID or URL provided"}), 400

        def generate():
            def send_event(event_type, message):
                return f"data: {json.dumps({'type': event_type, 'message': message})}\n\n"
                    # Check credentials upfront
        
            if not user_auth or not user_auth.get("confluence_email") or not user_auth.get("confluence_api_token"):
                yield send_event("error", "Confluence credentials not configured. Please set them up in Account Settings.")
                return

            yield send_event("progress", "Parsing Confluence page input...")

            # Use page_id if available, otherwise extract from page_input
            resolved_id = page_id
            if not resolved_id:
                m = re.search(r'pages/(\d+)', page_input)
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
                page_result = TOOL_REGISTRY["get_page_content"]({"page_id": resolved_id, "__user_auth": user_auth})
                if isinstance(page_result, dict) and page_result.get("success"):
                    page_data = page_result.get("data", {})
                    title = page_data.get("title") or f"Page {resolved_id}" if isinstance(page_data, dict) else f"Page {resolved_id}"
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
            yield send_event("progress", "Starting page review with the selected checklist...")
            yield send_event("progress", "This may take a moment — please wait...")

            result_queue = queue.Queue()

            # Track stderr position for reading [REVIEW] messages from MCP server
            _stderr_read_idx = len(mcp_client.stderr_lines)

            # High-confidence mode is enforced for Confluence reviews.
            _skip_inline = False
            _skip_footer = False

            def _run_confluence_review():
                try:
                    review_args = {"page_input": resolved_id, "checklist_page_id": confluence_checklist_page_id, "skip_inline": _skip_inline, "skip_footer": _skip_footer, "__user_auth": user_auth}
                    if confluence_base_url:
                        review_args["__confluence_base_url"] = confluence_base_url
                    result = TOOL_REGISTRY["review_confluence"](review_args)
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
            except queue.Empty:
                yield send_event("error", "Review thread completed but produced no result.")
                return

            if status == "error":
                yield send_event("error", f"Review failed: {payload}")
                return

            reused_inflight = False
            waited_s = 0.0
            result = payload
            if isinstance(payload, dict) and "result" in payload:
                result = payload.get("result")
                reused_inflight = bool(payload.get("reused_inflight", False))
                try:
                    waited_s = float(payload.get("waited_s", 0.0) or 0.0)
                except Exception:
                    waited_s = 0.0

            if isinstance(result, dict) and result.get("success"):
                data_r = result.get("data", {})
                if isinstance(data_r, dict):
                    summary = data_r.get("summary", "")
                    total_issues = int(data_r.get("issues_found", data_r.get("total_issues", 0)) or 0)
                    inline_posted = int(data_r.get("comments_posted", data_r.get("inline_comments_posted", 0)) or 0)
                    inline_failed = int(data_r.get("inline_failures_count", 0) or 0)
                    footer_posted = bool(data_r.get("footer_posted", data_r.get("footer_comment_posted", False)))
                    executed_checks = list(data_r.get("executed_checks", []) or [])
                    skipped_checks = list(data_r.get("skipped_checks", []) or [])
                else:
                    summary = str(data_r)
                    total_issues = 0
                    inline_posted = 0
                    inline_failed = 0
                    footer_posted = False
                    executed_checks = []
                    skipped_checks = []

                yield send_event("progress", f"\u2705 Review complete in {elapsed_s:.1f}s")
                if reused_inflight:
                    yield send_event("progress", f"  Reused in-flight result for duplicate request (waited {waited_s:.1f}s)")
                if total_issues > 0:
                    yield send_event("progress", f"  Issues found: {total_issues}")
                else:
                    yield send_event("progress", "  No issues were found for the selected checklist")
                yield send_event("progress", f"  Inline comments posted: {inline_posted}")
                if inline_failed > 0:
                    yield send_event("progress", f"  Inline comments failed: {inline_failed}")
                elif total_issues == 0:
                    yield send_event("progress", "  No inline comments were posted because no findings required them")
                if footer_posted:
                    yield send_event("progress", "  Footer summary posted to page")
                else:
                    yield send_event("progress", "  Footer summary was not posted")
                if executed_checks:
                    yield send_event("progress", "  Executed checks: " + ", ".join(str(item) for item in executed_checks))
                if skipped_checks:
                    skipped_text = ", ".join(f"{item.get('id', 'unknown')} ({item.get('reason', 'skipped')})" for item in skipped_checks[:5])
                    yield send_event("progress", "  Skipped checks: " + skipped_text)

                lines = [f"Completed Confluence page review for page {resolved_id} in {elapsed_s:.1f}s."]
                lines.append(f"Issues found: {total_issues}.")
                lines.append(f"Inline comments posted: {inline_posted}.")
                if inline_failed > 0:
                    lines.append(f"Inline comments failed: {inline_failed}.")
                elif total_issues == 0:
                    lines.append("No inline comments were needed because the selected checks found no issues.")
                lines.append("Footer summary posted to the page." if footer_posted else "Footer summary could not be posted to the page.")
                if executed_checks:
                    lines.append("Executed checks: " + ", ".join(str(item) for item in executed_checks) + ".")
                if skipped_checks:
                    skipped_text = ", ".join(f"{item.get('id', 'unknown')} ({item.get('reason', 'skipped')})" for item in skipped_checks[:5])
                    lines.append("Skipped checks: " + skipped_text + ".")
                if summary:
                    lines.append(summary)
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


    def combined_review_stream():
        """POST /api/combined-review-stream
        Sequential validation flow:
        1. Parse & extract Confluence page (like Confluence review does)
        2. Fetch Confluence page content with get_page_content
        3. Parse & extract PR info (like PR review does)
        4. Fetch PR files with get_files_in_pr
        5. Run combined review tool on validated sources
        """
        _lazy_imports()
        data = request.get_json(silent=True) or {}

        # Extract inputs
        conf_page_id = str(data.get("conf_page_id") or "").strip()
        pr_owner = str(data.get("pr_owner") or "").strip()
        pr_repo = str(data.get("pr_repo") or "").strip()
        pr_number = str(data.get("pr_number") or "").strip()
        pr_checklist = data.get("pr_checklist", [])
        conf_checklist = data.get("conf_checklist", [])
        user_auth = normalize_user_auth(data.get("user_auth", {}))
        confluence_base_url = str(data.get("confluence_base_url") or "").strip()
        github_base_url = str(data.get("github_base_url") or user_auth.get("github_base_url") or "").strip()
        
        # Validate inputs
        if not conf_page_id:
            return jsonify({"error": "Confluence page ID required"}), 400
        if not (pr_owner and pr_repo and pr_number):
            return jsonify({"error": "PR owner, repo, and number required"}), 400

        def generate():
            def send_event(event_type, message):
                return f"data: {json.dumps({'type': event_type, 'message': message})}\n\n"

            # Check both credentials
            if not user_auth or not user_auth.get("confluence_email") or not user_auth.get("confluence_api_token"):
                yield send_event("error", "Confluence credentials required")
                return
            if not user_auth or not user_auth.get("github_token"):
                yield send_event("error", "GitHub credentials required")
                return

            start_time = time.time()

            try:
                # ===== PART 1: CONFLUENCE PAGE ACCESS (like Confluence review) =====
                yield send_event("progress", "STEP 1: Accessing Confluence page...")
                
                # Fetch Confluence page content using get_page_content tool
                try:
                    conf_args = {"page_id": conf_page_id, "__user_auth": user_auth}
                    if confluence_base_url:
                        conf_args["__confluence_base_url"] = confluence_base_url
                    page_result = TOOL_REGISTRY["get_page_content"](conf_args)
                    
                    if isinstance(page_result, dict) and page_result.get("success"):
                        page_data = page_result.get("data", {})
                        conf_title = page_data.get("title") or f"Page {conf_page_id}" if isinstance(page_data, dict) else f"Page {conf_page_id}"
                        yield send_event("progress", f"✓ Confluence page accessed: {conf_title}")
                    else:
                        err = page_result.get("error", "Unknown") if isinstance(page_result, dict) else str(page_result)
                        yield send_event("error", f"Could not access Confluence page: {err}")
                        return
                except Exception as e:
                    yield send_event("error", f"Error accessing Confluence page: {e}")
                    return

                # ===== PART 2: GITHUB PR ACCESS (like PR review) =====
                yield send_event("progress", "STEP 2: Accessing GitHub PR...")
                
                # Fetch PR commit info + files + diffs using MCP tools
                try:
                    pr_args = {"repo": f"{pr_owner}/{pr_repo}", "pr_number": int(pr_number), "__user_auth": user_auth}
                    if github_base_url:
                        pr_args["__github_base_url"] = github_base_url

                    # 2a. Commit info
                    sha_result = TOOL_REGISTRY["get_base_and_head_sha"](pr_args)
                    if not isinstance(sha_result, dict) or not sha_result.get("success"):
                        err = sha_result.get("error", "Unknown") if isinstance(sha_result, dict) else str(sha_result)
                        yield send_event("error", f"Could not get PR commit info: {err}")
                        return

                    sha_payload = sha_result.get("data")
                    base_sha = ""
                    head_sha = ""
                    if isinstance(sha_payload, dict):
                        base_sha = str(sha_payload.get("base_sha") or "")
                        head_sha = str(sha_payload.get("head_sha") or "")
                    elif isinstance(sha_payload, (list, tuple)) and len(sha_payload) >= 2:
                        base_sha = str(sha_payload[0] or "")
                        head_sha = str(sha_payload[1] or "")

                    if not head_sha:
                        yield send_event("error", "Could not get PR commit info: missing head_sha")
                        return
                    yield send_event("progress", f"PR commit range: {base_sha[:7]}..{head_sha[:7]}")

                    # 2b. PR files
                    pr_result = TOOL_REGISTRY["get_files_in_pr"](pr_args)
                    if not isinstance(pr_result, dict) or not pr_result.get("success"):
                        err = pr_result.get("error", "Unknown") if isinstance(pr_result, dict) else str(pr_result)
                        yield send_event("error", f"Could not access PR files: {err}")
                        return

                    files_data = pr_result.get("data", [])
                    file_count = len(files_data) if isinstance(files_data, list) else 0
                    yield send_event("progress", f"PR accessed: {file_count} file(s) in scope")

                    # 2c. Per-file diff/content prefetch to follow PR review data path.
                    # Keep bounded to avoid long startup delays on very large PRs.
                    prefetched = 0
                    for item in files_data if isinstance(files_data, list) else []:
                        file_path = ""
                        if isinstance(item, dict):
                            file_path = str(item.get("filename") or item.get("path") or "")
                        elif isinstance(item, str):
                            file_path = item
                        if not file_path:
                            continue

                        TOOL_REGISTRY["file_with_line_no_and_diff"]({
                            "repo": f"{pr_owner}/{pr_repo}",
                            "pr_number": int(pr_number),
                            "file_path": file_path,
                            "__user_auth": user_auth,
                        })
                        TOOL_REGISTRY["get_file_content"]({
                            "repo": f"{pr_owner}/{pr_repo}",
                            "file_path": file_path,
                            "ref": head_sha,
                            "__user_auth": user_auth,
                        })
                        prefetched += 1
                        if prefetched >= 30:
                            break

                    if prefetched > 0:
                        yield send_event("progress", f"Prefetched diff/content for {prefetched} file(s)")
                except Exception as e:
                    yield send_event("error", f"Error accessing PR: {e}")
                    return

                # ===== PART 3: RUN COMBINED REVIEW =====
                yield send_event("progress", "STEP 3: Running combined review with validated sources...")
                yield send_event("progress", f"PR checks: {len(pr_checklist)} selected")
                yield send_event("progress", f"Confluence checks: {len(conf_checklist)} selected")
                
                result_queue = queue.Queue()
                _stderr_read_idx = len(mcp_client.stderr_lines)

                def _run_combined_review():
                    try:
                        review_args = {
                            "repo": f"{pr_owner}/{pr_repo}",
                            "pr_number": int(pr_number),
                            "conf_page_id": conf_page_id,
                            "pr_checklist": pr_checklist,
                            "conf_checklist": conf_checklist,
                            "skip_inline": bool(data.get("skip_inline", False)),
                            "skip_footer": bool(data.get("skip_footer", False)),
                            "__user_auth": user_auth,
                        }
                        if confluence_base_url:
                            review_args["__confluence_base_url"] = confluence_base_url
                        
                        result = TOOL_REGISTRY["review_combined_pr_and_confluence"](review_args)
                        result_queue.put(("ok", result))
                    except Exception as e:
                        result_queue.put(("error", str(e)))

                review_thread = threading.Thread(target=_run_combined_review, daemon=True)
                started_at = time.time()
                review_thread.start()

                # Monitor thread and stream progress
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
                    if not review_thread.is_alive():
                        break
                    # Heartbeat every ~10s if no log messages
                    elapsed = int(time.time() - started_at)
                    heartbeat_bucket = elapsed // 10
                    if not sent_any and heartbeat_bucket > _last_heartbeat:
                        _last_heartbeat = heartbeat_bucket
                        yield send_event("progress", f"  [Still analyzing... {elapsed}s elapsed]")

                # Drain remaining stderr
                for line in mcp_client.stderr_lines[_stderr_read_idx:]:
                    if "[REVIEW]" in line:
                        cleaned = _clean_review_line(line)
                        if cleaned is not None:
                            yield send_event("progress", cleaned)

                elapsed_s = time.time() - started_at

                try:
                    status, result = result_queue.get_nowait()
                except queue.Empty:
                    yield send_event("error", "Review thread completed but produced no result.")
                    return

                if status == "error":
                    yield send_event("error", f"Review failed: {result}")
                    return

                # Process results
                if result.get("success"):
                    # Emit detailed findings as [REVIEW] logs for real-time SSE display
                    detailed_findings = result.get("detailed_findings", [])
                    if detailed_findings:
                        print(f"[REVIEW] === Detailed findings ({len(detailed_findings)} non-compliant checks) ===", file=sys.stderr, flush=True)
                        for finding in detailed_findings:
                            check_name = finding.get("check", "Unknown")
                            why = finding.get("why", "Not specified")
                            action = finding.get("action", "Review manually")
                            evidence = finding.get("evidence", "")

                            # Emit finding as [REVIEW] log
                            msg = f"FINDING: {check_name} | Why flagged: {why} | What to do: {action}"
                            if evidence:
                                msg += f" | Evidence/metric meaning: {evidence}"
                            print(f"[REVIEW] {msg}", file=sys.stderr, flush=True)

                    passed_checks = result.get("passed_checks", [])
                    if isinstance(passed_checks, list) and passed_checks:
                        print(f"[REVIEW] === Checks with no issues found ({len(passed_checks)}) ===", file=sys.stderr, flush=True)
                        for chk in passed_checks:
                            print(f"[REVIEW] PASS: {chk} | No issues found in this check.", file=sys.stderr, flush=True)

                    yield send_event("progress", f"Combined review complete in {elapsed_s:.1f}s")

                    # Support both result shapes.
                    # Shape A: pr_results/confluence_results/combined_analysis
                    # Shape B: flat results map in result["results"]
                    pr_results = result.get("pr_results", {}) if isinstance(result.get("pr_results"), dict) else {}
                    conf_results = result.get("confluence_results", {}) if isinstance(result.get("confluence_results"), dict) else {}
                    combined_analysis = result.get("combined_analysis", {}) if isinstance(result.get("combined_analysis"), dict) else {}
                    flat_results = result.get("results", {}) if isinstance(result.get("results"), dict) else {}

                    if pr_results or conf_results:
                        pr_issues = pr_results.get("issues_found", 0) if isinstance(pr_results, dict) else 0
                        conf_issues = conf_results.get("issues_found", 0) if isinstance(conf_results, dict) else 0
                        yield send_event("progress", f"  PR issues found: {pr_issues}")
                        yield send_event("progress", f"  Confluence issues found: {conf_issues}")
                        if combined_analysis.get("summary"):
                            yield send_event("progress", f"  Cross-reference analysis: {combined_analysis['summary']}")
                    else:
                        total_checks = len(flat_results)
                        non_compliant = 0
                        for _name, _val in flat_results.items():
                            if isinstance(_val, dict) and _val.get("compliant") is False:
                                non_compliant += 1
                        yield send_event("progress", f"  Checks run: {total_checks}")
                        yield send_event("progress", f"  Non-compliant checks: {non_compliant}")

                    summary_text = str(result.get("summary") or "Combined review complete.")
                    posted_targets = result.get("footer_targets", []) if isinstance(result.get("footer_targets"), list) else []
                    comments_posted = int(result.get("comments_posted", 0) or 0)
                    if posted_targets:
                        yield send_event("progress", "  Footer posted to: {0}".format(", ".join(posted_targets)))
                    if comments_posted > 0:
                        yield send_event("progress", "  Inline/file-level comments posted: {0}".format(comments_posted))
                    posting_errors = result.get("posting_errors", []) if isinstance(result.get("posting_errors"), list) else []
                    if posting_errors:
                        yield send_event("progress", "  Posting warnings: {0}".format(", ".join(str(x) for x in posting_errors[:6])))
                    yield send_event("done", summary_text)
                    yield send_event("result", json.dumps(result))
                else:
                    yield send_event("error", f"Review result reported failure: {result.get('error', 'Unknown error')}")

            except Exception as e:
                yield send_event("error", f"Unexpected error: {e}")
                import traceback
                print(f"[ERROR] Combined review exception: {traceback.format_exc()}", file=sys.stderr, flush=True)

        return Response(stream_with_context(generate()), mimetype="text/event-stream")


    app.add_url_rule("/", "index", index, methods=["GET"])
    app.add_url_rule("/quick-user-guide", "quick_user_guide", quick_user_guide, methods=["GET"])
    app.add_url_rule("/api/chat", "chat", chat, methods=["POST"])
    app.add_url_rule("/api/chat-stream", "chat_stream", chat_stream, methods=["POST"])
    app.add_url_rule("/api/pr-checklist", "get_pr_checklist", get_pr_checklist, methods=["GET"])
    app.add_url_rule("/api/test-connections", "test_connections", test_connections, methods=["POST"])
    app.add_url_rule("/api/parse-pr", "parse_pr", parse_pr, methods=["POST"])
    app.add_url_rule("/api/review-stream", "review_stream", review_stream, methods=["POST"])
    app.add_url_rule("/api/confluence-review-stream", "confluence_review_stream", confluence_review_stream, methods=["POST"])
    app.add_url_rule("/api/combined-review-stream", "combined_review_stream", combined_review_stream, methods=["POST"])

