import sys
# app.py
# Thin Flask entrypoint: routes only. Core logic lives in app_logic.py.
import atexit
import os
import queue
import requests
from datetime import datetime
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, Response, stream_with_context


def _configure_stdio_utf8() -> None:
    """Avoid Windows cp1252/charmap crashes when logs include emoji or other Unicode."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


_configure_stdio_utf8()

try:
    from . import app_logic as _logic
except ImportError:
    import app_logic as _logic

_extract_prs_from_text = _logic._extract_prs_from_text
_try_fast_confluence_spelling_review = _logic._try_fast_confluence_spelling_review
run_agent = _logic.run_agent
normalize_user_auth = _logic.normalize_user_auth


_CONFLUENCE_PANEL_CHECK_MAP = {
    "spelling & grammar": "grammar",
    "repeated words": "repeated_word",
    "long sentences (readability)": "long_sentence",
    "long paragraphs": "long_paragraph",
    "page structure & headings": "structure",
    "table of contents present": "structure",
    "broken / missing links": "citation",
    "outdated information": "statistics_validation",
    "consistent terminology": "consistency_checks",
    "consistent formatting": "table_validation",
    "proper use of code blocks": "structure",
    "missing sections / incomplete content": "structure",
}


def _resolve_confluence_checklist_page_id(checklist):
    selected_ids = []
    seen = set()
    for raw in (checklist or []):
        label = str(raw).strip().lower()
        if not label:
            continue
        check_id = _CONFLUENCE_PANEL_CHECK_MAP.get(label)
        if not check_id or check_id in seen:
            continue
        selected_ids.append(check_id)
        seen.add(check_id)

    if not selected_ids:
        return ""

    if selected_ids == ["grammar"]:
        return "__GRAMMAR_ONLY__"

    return "__CUSTOM_CHECKS__:" + ",".join(selected_ids)


set_active_user_auth = _logic.set_active_user_auth


def _normalize_confluence_base_url(raw_url: str | None) -> str:
    """Normalize user/link-provided Confluence base URL into a stable API root."""
    candidate = (raw_url or '').strip().rstrip('/')
    if not candidate:
        return ''
    try:
        parsed = urlparse(candidate)
    except Exception:
        return candidate
    if not parsed.scheme or not parsed.netloc:
        return candidate

    host = parsed.netloc
    host_l = host.lower()
    path_l = (parsed.path or '').lower()

    # Atlassian Cloud endpoints are always rooted under /wiki.
    if host_l.endswith('atlassian.net'):
        return f"{parsed.scheme}://{host}/wiki"

    # If we received a deep wiki URL, collapse it to the wiki root.
    if '/wiki' in path_l:
        return f"{parsed.scheme}://{host}/wiki"

    return f"{parsed.scheme}://{host}"


def _extract_base_urls_from_text(text: str) -> tuple[str | None, str | None]:
    github_base = None
    confluence_base = None
    if not text:
        return github_base, confluence_base

    for raw in re.findall(r'https?://[^\s"\'<>]+', text):
        candidate = raw.rstrip(').,;')
        try:
            parsed = urlparse(candidate)
        except Exception:
            continue
        if not parsed.scheme or not parsed.netloc:
            continue

        host = parsed.netloc.lower()
        path = (parsed.path or "").lower()

        if '/pull/' in path and not github_base:
            if host.endswith('github.com'):
                github_base = 'https://api.github.com'
            else:
                github_base = f"{parsed.scheme}://{parsed.netloc}/api/v3"

        if ('/wiki/' in path or '/pages/' in path or 'pageid=' in (parsed.query or '').lower()) and not confluence_base:
            confluence_base = _normalize_confluence_base_url(candidate)

    return github_base, confluence_base


def _augment_user_auth_with_detected_base_urls(user_auth: dict, user_msg: str = '', history: list | None = None) -> dict:
    auth = dict(user_auth or {})
    existing_conf_base = _normalize_confluence_base_url(auth.get('confluence_base_url'))
    if existing_conf_base:
        auth['confluence_base_url'] = existing_conf_base
    hist_text = ''
    for entry in (history or []):
        if isinstance(entry, dict):
            hist_text += '\n' + str(entry.get('text', '') or '')
    gh_base, conf_base = _extract_base_urls_from_text((user_msg or '') + hist_text)
    if gh_base and not auth.get('github_base_url'):
        auth['github_base_url'] = gh_base
    # Prefer a base URL detected from the current link to avoid stale saved values.
    if conf_base:
        auth['confluence_base_url'] = conf_base
    return auth


clear_active_user_auth = _logic.clear_active_user_auth
_build_checklist_from_panel = _logic._build_checklist_from_panel
_get_cached_pr_checklist = _logic._get_cached_pr_checklist
_get_cached_chat_link_metadata = _logic._get_cached_chat_link_metadata
_make_review_coalesce_key = _logic._make_review_coalesce_key
_run_review_with_coalescing = _logic._run_review_with_coalescing
_clean_review_line = _logic._clean_review_line
TOOL_REGISTRY = _logic.TOOL_REGISTRY
mcp_client = _logic.mcp_client
json = _logic.json
re = _logic.re
threading = _logic.threading
time = _logic.time

app = Flask(__name__)


def _try_fast_smalltalk_response(user_msg: str) -> str | None:
    """Return an immediate local response for simple greetings to avoid LLM latency."""
    if not user_msg:
        return None
    normalized = re.sub(r"[^a-z0-9\s?!.,]", "", user_msg.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized_no_punct = re.sub(r"[?!.,]+", "", normalized).strip()

    quick_greetings = {
        "hi", "hello", "hey", "yo", "hii", "hey there", "hello there", "hi there"
    }
    greeting_words = {"hi", "hello", "hey", "yo", "hii"}
    greeting_targets = {"there", "munnai", "assistant", "ai"}

    parts = normalized_no_punct.split()
    if normalized_no_punct in quick_greetings:
        return "Hi! I am ready. Share a PR link, Confluence page link, or tell me what you want reviewed."
    if parts and parts[0] in greeting_words and len(parts) <= 3:
        if len(parts) == 1 or parts[1] in greeting_targets:
            return "Hi! I am ready. Share a PR link, Confluence page link, or tell me what you want reviewed."

    if normalized_no_punct in {"thanks", "thank you", "ty", "tnx"}:
        return "You are welcome. I can help with PR reviews, Confluence checks, and account setup too."
    if normalized_no_punct in {"ok", "okay", "k", "cool", "nice"}:
        return "Great. Send the next task when you are ready."
    return None


_PR_FILES_CACHE_TTL_S = 45
_PR_FILES_CACHE: dict[tuple, tuple[float, list]] = {}
_PR_FILES_CACHE_LOCK = threading.Lock()
_STRICT_INLINE_COMMENT_LIMIT = 12


def _get_cached_pr_files(repo_full: str, pr_num: int, user_auth: dict, github_base_url: str | None):
    cache_key = (
        repo_full,
        int(pr_num),
        str(github_base_url or ""),
        str(user_auth.get("github_owner", "")),
    )
    now = time.time()
    with _PR_FILES_CACHE_LOCK:
        entry = _PR_FILES_CACHE.get(cache_key)
        if entry and (now - entry[0]) < _PR_FILES_CACHE_TTL_S:
            return entry[1], True

    files_temp = {"repo": repo_full, "pr_number": int(pr_num), "__user_auth": user_auth}
    if github_base_url:
        files_temp["__github_base_url"] = github_base_url
    files_result = TOOL_REGISTRY["get_files_in_pr_tool"](files_temp)
    file_list = []
    if isinstance(files_result, dict) and files_result.get("success"):
        file_list = files_result.get("data", []) or []

    with _PR_FILES_CACHE_LOCK:
        _PR_FILES_CACHE[cache_key] = (now, file_list)
    return file_list, False

@app.route("/")
def index():
    # Renders the main web page (index.html)
    """Renders and returns the main index.html page."""
    welcome_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template("index.html", welcome_timestamp=welcome_timestamp)


@app.route("/quick-user-guide")
def quick_user_guide():
    """Renders the quick user guide page."""
    return render_template("quick_user_guide.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """POST /api/chat - extracts links from the user prompt, runs the agent loop, and returns the response."""
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

@app.route("/api/chat-stream", methods=["POST"])
def chat_stream():
    """POST /api/chat-stream - streams agent progress as SSE, then delivers the final answer instantly."""
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
            finally:
                _al.clear_active_user_auth()
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


# --- PR Review Panel API ---
@app.route("/api/pr-checklist", methods=["GET"])
def get_pr_checklist():
    """GET /api/pr-checklist - returns default PR review checklist."""
    checklist = _get_cached_pr_checklist()
    return jsonify({
        "checklist": [
            {"name": item.get("name"), "id": item.get("id")}
            for item in checklist
        ]
    })

@app.route("/api/test-connections", methods=["POST"])
def test_connections():
    """POST /api/test-connections - validates GitHub and Confluence credentials."""
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


@app.route("/api/parse-pr", methods=["POST"])
def parse_pr():
    """POST /api/parse-pr - parses a GitHub PR URL or Confluence page link/ID."""
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


@app.route("/api/review-stream", methods=["POST"])
def review_stream():
    """POST /api/review-stream - streams PR review progress as Server-Sent Events."""
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
                    return TOOL_REGISTRY["review_pull_request_tool"](review_args)

                result, reused_inflight, waited_s = _run_review_with_coalescing(review_key, _invoke_review)
                result_queue.put(("ok", {
                    "result": result,
                    "reused_inflight": reused_inflight,
                    "waited_s": waited_s,
                }))
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

# --- Shutdown Handler ---
atexit.register(lambda: mcp_client.shutdown())

# --- Main Entry Point ---
if __name__ == "__main__":
    app_port = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=app_port, debug=False, use_reloader=False, threaded=True)
