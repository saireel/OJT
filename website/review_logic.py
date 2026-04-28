import json
import re
import threading
import time
from urllib.parse import urlparse

try:
    from .mcp_runtime import TOOL_REGISTRY
except ImportError:
    from mcp_runtime import TOOL_REGISTRY

MAX_AGENT_STEPS = 20  # Raised — acts as safety net, not a kill switch

_CHAT_LINK_METADATA_TTL_S = 45

_CHAT_LINK_METADATA_CACHE: dict[tuple, tuple[float, dict]] = {}

_CHAT_LINK_METADATA_CACHE_LOCK = threading.Lock()

_SECRET_LINE_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?token|secret|password|passwd|private[_-]?key)\b\s*[:=]\s*[\"\']?[A-Za-z0-9_\-=/+]{12,}"),
    re.compile(r"(?i)\bATATT[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}"),
]

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
# Combined Review Checklist - for simultaneous GitHub PR + Confluence page reviews
# Uses dedicated handlers from combined_review module
_COMBINED_REVIEW_CHECKLIST = {
    "Documentation Coverage for New / Changed Code": {
        "id": "doc_coverage",
        "description": "Verify that new or modified code has appropriate documentation."
    },
    "Code Examples Match PR Changes": {
        "id": "code_examples",
        "description": "Ensure any code examples in docs match the actual changes in the PR."
    },
    "API / Function Signatures Up to Date": {
        "id": "api_signatures",
        "description": "Verify that API documentation and function signatures are current with the PR changes."
    },
    "Config / Env Variables Documented": {
        "id": "config_documented",
        "description": "Check that any new configuration or environment variables are documented."
    },
    "Architecture / Design Alignment with PR": {
        "id": "architecture_alignment",
        "description": "Ensure PR changes align with documented architecture and design decisions."
    },
    "Step-by-Step Instructions Match Implementation": {
        "id": "instructions_match",
        "description": "Verify that step-by-step instructions in docs match the actual implementation."
    },
    "Error Handling & Edge Cases Documented": {
        "id": "error_handling",
        "description": "Check that error handling and edge cases are properly documented."
    },
    "Deprecated Features / APIs Removed from Docs": {
        "id": "deprecated_removed",
        "description": "Verify that deprecated features and APIs are removed from documentation."
    },
    "Consistent Terminology Between Code and Docs": {
        "id": "terminology_consistent",
        "description": "Ensure consistent terminology is used between code and documentation."
    },
    "Links to PR / Ticket References Present": {
        "id": "pr_references",
        "description": "Verify that documentation links to related PR numbers and ticket references."
    },
    "Missing Sections for Covered Code Paths": {
        "id": "code_path_sections",
        "description": "Check that documentation sections cover all new code paths and functions."
    },
}


_PR_CHECKLIST_CACHE_TTL_S = 300

_CACHED_PR_CHECKLIST = None

_CACHED_PR_CHECKLIST_AT = 0.0

_PR_CHECKLIST_CACHE_LOCK = threading.Lock()

_PR_FILES_CACHE_TTL_S = 60  # Cache PR files for 60 seconds

_PR_FILES_CACHE: dict = {}

_PR_FILES_CACHE_LOCK = threading.Lock()

_MAX_CONCURRENT_REVIEWS = 3

_REVIEW_EXECUTION_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT_REVIEWS)

_REVIEW_INFLIGHT_LOCK = threading.Lock()

_REVIEW_INFLIGHT: dict[tuple, dict] = {}

def _step_budget_for_request(user_msg: str) -> int:
    """Return a smaller step budget for lightweight requests to reduce latency."""
    text = (user_msg or "").lower()
    fast_keywords = ["spell", "spelling", "grammar", "typo", "inline comment"]
    if any(keyword in text for keyword in fast_keywords):
        return min(10, MAX_AGENT_STEPS)
    return MAX_AGENT_STEPS

def _extract_confluence_page_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    if not text:
        return ids
    for url in re.findall(r"https?://[^\s]*atlassian\.net/wiki[^\s]*", text):
        m = re.search(r"/pages/(\d+)", url)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids

def _extract_prs_from_text(text: str) -> list[dict]:
    found: list[dict] = []
    if not text:
        return found
    for url in re.findall(r"https?://github\.com/[^\s]+/pull/\d+", text):
        m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
        if m:
            info = {"owner": m.group(1), "repo": m.group(2), "pr_number": int(m.group(3))}
            if info not in found:
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
    # PHASE 4 FIX: Disable fast path to use agent loop for consistent "Progress:" formatting
    return None
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
        instructions_result = TOOL_REGISTRY["get_page_content_by_sections"]({
            "page_id": instruction_page_id,
            "chunk_size": 2000,
            "max_sections": 4,
        })
        if not isinstance(instructions_result, dict) or not instructions_result.get("success"):
            return None
        instructions_text = _flatten_tool_text(instructions_result.get("data", ""))
        if not _is_simple_spelling_instruction_text(instructions_text):
            return None
    target_result = TOOL_REGISTRY["get_page_content_by_sections"]({
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
    review_result = TOOL_REGISTRY["review_confluence"]({
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
        }
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
    files_result = TOOL_REGISTRY["get_files_in_pr"]({
        "repo": repo,
        "pr_number": pr_number,
    })
    if not isinstance(files_result, dict) or not files_result.get("success"):
        return findings

    for file_obj in files_result.get("data", []):
        file_path = file_obj.get("filename", "")
        if not file_path:
            continue
        diff_result = TOOL_REGISTRY["file_with_line_no_and_diff"]({
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
        post_result = TOOL_REGISTRY['add_pr_comment']({
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


def _extract_checklist_from_prompt(user_msg: str) -> list[str]:
    """Extract checklist items from the user's prompt text.
    
    Looks for patterns like:
    - "Checklist items to review:"
    
    And extracts the bulleted items that follow.
    """
    if not user_msg:
        return []
    
    extracted = []
    # Look for "Checklist items to review:" section
    checklist_match = re.search(
        r'checklist\s+(?:items\s+)?to\s+review\s*:\s*((?:[-*]\s+[^\n]+(?:\n|$))+)',
        user_msg,
        re.IGNORECASE
    )
    
    if checklist_match:
        checklist_section = checklist_match.group(1)
        # Extract bullet points (lines starting with - or *)
        items = re.findall(r'[-*]\s+([^\n]+)', checklist_section)
        extracted.extend([item.strip() for item in items if item.strip()])
    
    return extracted

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




# Maps Confluence panel checklist labels to check IDs (normalized to lowercase)
_CONFLUENCE_PANEL_CHECK_MAP = {
    "missing sections / incomplete content": "empty_section",
    "complete test case": "complete_test_case",
    "check c0 and c1 coverage": "check_coverage",
    "consistent formatting / indentation": "consistent_formatting",
    "cross-file consistency": "cross_file_consistency",
    "spelling & grammar": "spelling_grammar",
    "long sentences (readability)": "long_sentence",
    "long paragraphs": "long_paragraph",
    "page structure & headings": "structure",
    "table of contents present": "table_validation",
    "broken / missing links": "citation",
    "consistent terminology": "consistency_checks",
    "consistent formatting": "formatting",



}
# Migrated from website/app.py.bak
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


# Migrated from website/app.py.bak
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


# Migrated from website/app.py.bak
def _resolve_confluence_checklist_page_id(checklist):
    selected_ids = []
    seen = set()
    for raw in (checklist or []):
        label = str(raw).strip().lower()
        if not label:
            continue
        check_id = _CONFLUENCE_PANEL_CHECK_MAP.get(label)
        if not check_id:
            # Keep unknown user-selected labels visible in footer by passing a stable synthetic id.
            check_id = re.sub(r"[^a-z0-9]+", "_", label).strip("_")
        if not check_id or check_id in seen:
            continue
        selected_ids.append(check_id)
        seen.add(check_id)

    if not selected_ids:
        return ""

    if selected_ids == ["grammar"]:
        return "__GRAMMAR_ONLY__"

    return "__CUSTOM_CHECKS__:" + ",".join(selected_ids)


# Migrated from website/app.py.bak
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


# Migrated from website/app.py.bak
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


# Migrated from website/app.py.bak
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
    files_result = TOOL_REGISTRY["get_files_in_pr"](files_temp)
    file_list = []
    if isinstance(files_result, dict) and files_result.get("success"):
        file_list = files_result.get("data", []) or []

    with _PR_FILES_CACHE_LOCK:
        _PR_FILES_CACHE[cache_key] = (now, file_list)
    return file_list, False
