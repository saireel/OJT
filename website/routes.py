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
    from .review_logic import _build_checklist_from_panel, _extract_checklist_from_prompt, _extract_prs_from_text, _get_cached_chat_link_metadata, _get_cached_pr_checklist, _get_cached_pr_files, _make_review_coalesce_key, _run_review_with_coalescing, _try_fast_confluence_spelling_review, _extract_base_urls_from_text, _normalize_confluence_base_url, _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id, _try_fast_smalltalk_response
    from mcp_server.mcp_calls import STRICT_INLINE_COMMENT_LIMIT as _STRICT_INLINE_COMMENT_LIMIT
    from .agent_engine import  run_agent


def clean_summary_for_display(text):
    """Clean raw markdown summary for better UI display."""
    import re
    if not text or not isinstance(text, str):
        return ""
    # Remove markdown pipes/table formatting
    text = text.replace('|', '')
    # Remove extra dashes (markdown table separators)  
    text = re.sub(r'-{3,}', '', text)
    # Clean up extra whitespace
    text = re.sub(r'\n\s*\n', '\n', text)
    text = re.sub(r' +', ' ', text)
    # Limit length
    if len(text) > 600:
        text = text[:600].rsplit(' ', 1)[0] + "..."
    return text.strip()

def register_routes(app):
    # Lazy imports to avoid heavy module loading at import time
    def _lazy_imports():
        # Import standard library modules used by route handlers
        global sys, threading, time, json, requests, re, queue
        import sys, threading, time, json, requests, re, queue
        global TOOL_REGISTRY, clear_active_user_auth, get_active_user_auth, mcp_client, normalize_user_auth, set_active_user_auth
        global _build_checklist_from_panel, _extract_checklist_from_prompt, _extract_prs_from_text, _get_cached_chat_link_metadata, _get_cached_pr_checklist, _get_cached_pr_files
        global _make_review_coalesce_key, _run_review_with_coalescing, _try_fast_confluence_spelling_review
        global _extract_base_urls_from_text, _normalize_confluence_base_url, _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id, _try_fast_smalltalk_response
        global run_agent, _STRICT_INLINE_COMMENT_LIMIT
        try:
            from .mcp_runtime import (
                TOOL_REGISTRY, clear_active_user_auth, get_active_user_auth, mcp_client, normalize_user_auth, set_active_user_auth
            )
            from .review_logic import (
                _build_checklist_from_panel, _extract_checklist_from_prompt, _extract_prs_from_text, _get_cached_chat_link_metadata,
                _get_cached_pr_checklist, _get_cached_pr_files, _make_review_coalesce_key, _run_review_with_coalescing,
                _try_fast_confluence_spelling_review, _extract_base_urls_from_text, _normalize_confluence_base_url,
                _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id, _try_fast_smalltalk_response,
            )
            from .agent_engine import  run_agent
            from mcp_server.mcp_calls import STRICT_INLINE_COMMENT_LIMIT as _STRICT_INLINE_COMMENT_LIMIT
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
            from agent_engine import  run_agent
            from mcp_server.mcp_calls import STRICT_INLINE_COMMENT_LIMIT as _STRICT_INLINE_COMMENT_LIMIT
    
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
        # Extract checklist items from the user's prompt if provided
        prompt_checklist = _extract_checklist_from_prompt(user_msg)
        if prompt_checklist:
            checklist = prompt_checklist  # Use prompt-extracted checklist over UI selection
        outputs = data.get("outputs", [])
        confluence_checklist_page_id = _resolve_confluence_checklist_page_id(checklist) or (data.get("confluence_checklist_page_id") or "").strip()
        if not user_msg:
            return jsonify({"error": "Empty prompt"}), 400

        # Skip fast reviews if user has specified a checklist
        prompt_checklist = _extract_checklist_from_prompt(user_msg)
        fast_smalltalk = _try_fast_smalltalk_response(user_msg) if not prompt_checklist else None
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
        # Skip fast path if user has specified a checklist
        set_active_user_auth(user_auth)
        try:
            try:
                fast_response = _try_fast_confluence_spelling_review(user_msg, history, page_ids) if not prompt_checklist else None
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
        # Extract checklist items from the user's prompt if provided
        prompt_checklist = _extract_checklist_from_prompt(user_msg)
        if prompt_checklist:
            checklist = prompt_checklist  # Use prompt-extracted checklist over UI selection
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
                payload = {"type": event_type}
                if isinstance(message, dict):
                    payload.update(message)
                    payload.setdefault("message", "")
                else:
                    payload["message"] = message
                return "data: " + json.dumps(payload) + "\n\n"

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
                finally:
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
    
    


    app.add_url_rule("/", "index", index, methods=["GET"])
    app.add_url_rule("/quick-user-guide", "quick_user_guide", quick_user_guide, methods=["GET"])
    app.add_url_rule("/api/chat", "chat", chat, methods=["POST"])
    app.add_url_rule("/api/chat-stream", "chat_stream", chat_stream, methods=["POST"])
    app.add_url_rule("/api/pr-checklist", "get_pr_checklist", get_pr_checklist, methods=["GET"])
    app.add_url_rule("/api/test-connections", "test_connections", test_connections, methods=["POST"])
    app.add_url_rule("/api/parse-pr", "parse_pr", parse_pr, methods=["POST"])

