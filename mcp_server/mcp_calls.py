# mcp_calls.py
# Synchronous wrappers for Confluence and GitHub API calls.
# These are called directly by FastMCP tool definitions in mcp_tools.py.

import html
import logging
import re
from typing import Any, Dict, List

from mcp_server.confluence_reviewer import ConfluenceAPI, confluence_api, ReviewActions, SyntaxActions

logger = logging.getLogger(__name__)
from mcp_server.github_reviewer import github_api
from mcp_server.combined_review.combined_review import execute_combined_check

# Shared instances (created once, reused across all calls)
syntax_actions = SyntaxActions(confluence_api)
review_actions = ReviewActions(confluence_api, syntax_actions)

STRICT_HIGH_CONFIDENCE_MODE = True
STRICT_INLINE_COMMENT_LIMIT = 12

def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def set_runtime_auth(
    confluence_email: str = "",
    confluence_api_token: str = "",
    confluence_base_url: str = "",
    github_owner: str = "",
    github_token: str = "",
    github_base_url: str = "",
) -> Dict[str, Any]:
    """Apply per-request credentials to in-process API clients."""
    try:
        confluence_api.set_runtime_auth(
            email=confluence_email,
            api_token=confluence_api_token,
            base_url=confluence_base_url,
        )
        github_api.set_runtime_auth(
            owner=github_owner,
            github_token=github_token,
            base_url=github_base_url,
        )
        return {
            "success": True,
            "data": {
                "confluence_email": confluence_email,
                "confluence_api_token": _mask_secret(confluence_api_token),
                "confluence_base_url": confluence_base_url,
                "github_owner": github_owner,
                "github_token": _mask_secret(github_token),
                "github_base_url": github_base_url,
            },
        }
    except Exception as exc:
        return {"success": False, "error": f"Failed to apply runtime auth: {exc}"}

# Helpers
def _extract_confluence_page_ids(source: str) -> List[str]:
    """Return a list of Confluence page IDs extracted from *source*.

    *source* may contain plain numeric IDs, full Confluence page URLs, or a
    mix of both, separated by commas or newlines.
    """
    if not source:
        return []
    tokens = re.split(r"[,\n]+", source)
    ids: List[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        m = re.search(r"pages/(\d+)", token)
        if m:
            ids.append(m.group(1))
        elif token.isdigit():
            ids.append(token)
    return ids

# -----------------------
# Confluence calls
# -----------------------
def create_space(name: str, key: str, description: str) -> Dict[str, Any]:
    response, error = confluence_api.create_space(name, key, description)
    if response:
        return {"success": True, "data": response.json()}
    return {"success": False, "error": error or "Failed to create space"}

def create_page(title: str, space_key: str, content: str) -> Dict[str, Any]:
    response, error = confluence_api.create_page(title, space_key, content)
    if response:
        return {"success": True, "data": response.json()}
    return {"success": False, "error": error or "Failed to create page"}

def _get_current_page_version(page_id: str) -> int | None:
    """Fetch the current version number of a Confluence page."""
    try:
        response, error = confluence_api._request(
            "GET",
            f"/rest/api/content/{page_id}",
            params={"expand": "version"}
        )
        if response:
            return response.json().get("version", {}).get("number")
    except Exception:
        pass
    return None


def update_page(page_id: str, title: str, content: str, version: int, message: str) -> Dict[str, Any]:
    """Update a Confluence page, with auto-retry on 409 Conflict (version mismatch)."""
    max_retries = 3
    current_version = version
    error: str | None = None

    for attempt in range(max_retries):
        response, error = confluence_api.update_page(page_id, title, content, current_version, message)
        if response:
            return {"success": True, "data": response.json()}

        # Check if the error is a 409 Conflict (version mismatch)
        if error and "409" in str(error):
            latest_version = _get_current_page_version(page_id)
            if latest_version and latest_version != current_version:
                current_version = latest_version
                continue  # Retry with the latest version
            # If we can't get a new version, stop retrying
            break
        else:
            # Non-conflict error, don't retry
            break

    return {"success": False, "error": error or "Failed to update page"}

def get_page_content(page_id: str, __user_auth: dict | None = None) -> Dict[str, Any]:
    # Create fresh instance per request to avoid race conditions
    api = confluence_api if not __user_auth else ConfluenceAPI(
        email=__user_auth.get("confluence_email", ""),
        api_token=__user_auth.get("confluence_api_token", ""),
        base_url=__user_auth.get("confluence_base_url", ""),
    )
    text, error = api.get_page_storage(page_id)
    if text is not None:
        return {"success": True, "data": text}
    return {"success": False, "error": error or "Failed to get page content"}


def find_and_replace_in_page(page_id: str, find_text: str, replace_text: str, replace_all: bool = True) -> Dict[str, Any]:
    """Safely find and replace text in a Confluence page without losing any content.

    This fetches the full page, performs the replacement, and saves it back.
    It handles version conflicts automatically.
    """
    import re as _re

    if not page_id or not find_text:
        return {"success": False, "error": "page_id and find_text are required"}

    # Step 1: Fetch full page metadata (title, version, storage body)
    response, error = confluence_api._request(
        "GET",
        f"/rest/api/content/{page_id}",
        params={"expand": "body.storage,version"}
    )
    if error or response is None:
        return {"success": False, "error": error or "Failed to fetch page"}

    page_data = response.json()
    title = page_data.get("title", "")
    version = page_data.get("version", {}).get("number", 0)
    storage_body = page_data.get("body", {}).get("storage", {}).get("value", "")

    if not storage_body:
        return {"success": False, "error": "Page has no content body"}

    # Step 2: Count occurrences (search in visible text, but replace in storage format)
    # Try plain text replacement first
    if find_text in storage_body:
        if replace_all:
            new_body = storage_body.replace(find_text, replace_text)
            count = storage_body.count(find_text)
        else:
            new_body = storage_body.replace(find_text, replace_text, 1)
            count = 1
    else:
        # Try case-insensitive replacement
        pattern = _re.compile(_re.escape(find_text), _re.IGNORECASE)
        matches = pattern.findall(storage_body)
        if not matches:
            return {
                "success": False,
                "error": f"Text '{find_text}' not found on the page."
            }
        if replace_all:
            new_body = pattern.sub(replace_text, storage_body)
            count = len(matches)
        else:
            new_body = pattern.sub(replace_text, storage_body, count=1)
            count = 1

    if new_body == storage_body:
        return {"success": True, "data": {"message": "No changes needed — text already matches.", "replacements": 0}}

    # Step 3: Update the page with the full modified content
    result = update_page(page_id, title, new_body, version, f"Replaced '{find_text}' with '{replace_text}'")

    if result.get("success"):
        return {
            "success": True,
            "data": {
                "message": f"Successfully replaced '{find_text}' with '{replace_text}'.",
                "replacements": count,
                "page_id": page_id,
            }
        }
    return result

def get_page_content_by_sections(page_id: str, chunk_size: int = 2500, max_sections: int = 50, __user_auth: dict | None = None) -> Dict[str, Any]:
    # Create fresh syntax_actions instance with proper auth to avoid race conditions
    if __user_auth:
        api = ConfluenceAPI(
            email=__user_auth.get("confluence_email", ""),
            api_token=__user_auth.get("confluence_api_token", ""),
            base_url=__user_auth.get("confluence_base_url", ""),
        )
        local_syntax_actions = SyntaxActions(api)
        text, error = local_syntax_actions.get_page_content_by_sections(page_id, chunk_size, max_sections)
    else:
        text, error = syntax_actions.get_page_content_by_sections(page_id, chunk_size, max_sections)
    if text is not None:
        return {"success": True, "data": text}
    return {"success": False, "error": error or "Failed to get page content"}

def review_confluence_page(page_input: str = "", page_id: str = "", checklist_page_id: str = "", skip_inline: bool = True, skip_footer: bool = False, __user_auth: dict | None = None) -> Dict[str, Any]:
    source = page_input or page_id
    page_ids = _extract_confluence_page_ids(source)
    if not page_ids:
        return {
            "success": False,
            "error": "No valid Confluence page IDs found. Pass a page ID, a Confluence page URL, or multiple values separated by commas/newlines.",
        }

    if len(page_ids) == 1:
        result, error = review_actions.advanced_confluence_page_review(page_ids[0], checklist_page_id, skip_inline=skip_inline, skip_footer=skip_footer, user_auth=__user_auth)
        if result is not None:
            return {"success": True, "data": result}
        return {"success": False, "error": error or "Failed to review confluence page"}

    reviewed = []
    failed = []
    for pid in page_ids:
        result, error = review_actions.advanced_confluence_page_review(pid, checklist_page_id, skip_inline=skip_inline, skip_footer=skip_footer, user_auth=__user_auth)
        if result is not None:
            reviewed.append(result)
        else:
            failed.append({
                "page_id": pid,
                "error": error or "Failed to review confluence page",
            })

    if reviewed:
        return {
            "success": True,
            "data": {
                "mode": "batch",
                "requested": len(page_ids),
                "reviewed_count": len(reviewed),
                "failed_count": len(failed),
                "results": reviewed,
                "errors": failed,
            },
        }

    return {
        "success": False,
        "error": "Failed to review all provided pages",
        "data": {
            "mode": "batch",
            "requested": len(page_ids),
            "reviewed_count": 0,
            "failed_count": len(failed),
            "errors": failed,
        },
    }

def post_footer_comment(page_id: str, comment: str, __user_auth: dict | None = None) -> Dict[str, Any]:
    # Create fresh instance per request to avoid race conditions
    logger.info("[POST_FOOTER] Starting footer comment post for page_id=%s", page_id)
    logger.debug("[POST_FOOTER] Has auth: %s, comment length: %d", bool(__user_auth), len(comment) if comment else 0)
    
    if __user_auth:
        logger.debug("[POST_FOOTER] Auth details: email=%s, token=%s, base_url=%s",
                    __user_auth.get("confluence_email", "NONE"),
                    _mask_secret(__user_auth.get("confluence_api_token", "NONE")),
                    __user_auth.get("confluence_base_url", "NONE"))
    
    api = confluence_api if not __user_auth else ConfluenceAPI(
        email=__user_auth.get("confluence_email", ""),
        api_token=__user_auth.get("confluence_api_token", ""),
        base_url=__user_auth.get("confluence_base_url", ""),
    )
    
    logger.debug("[POST_FOOTER] Created API instance, calling post_footer_comment")
    response, error = api.post_footer_comment(page_id, comment)
    
    if response:
        logger.info("[POST_FOOTER] SUCCESS - Response status: %s", response.status_code)
        try:
            data = response.json()
            logger.debug("[POST_FOOTER] Response JSON: %s", str(data)[:200])
            return {"success": True, "data": data}
        except Exception as e:
            logger.error("[POST_FOOTER] Failed to parse response JSON: %s", str(e))
            return {"success": True, "data": response.text[:200]}
    else:
        logger.error("[POST_FOOTER] FAILED - Error: %s", error)
        return {"success": False, "error": error or "Failed to post footer comment"}

def post_inline_comment(page_id: str, comment: str, text_selection: str, match_index: int | None = None, __user_auth: dict | None = None) -> Dict[str, Any]:
    # Create fresh instance per request to avoid race conditions
    logger.info("[POST_INLINE] Starting inline comment post for page_id=%s, match_index=%s", page_id, match_index)
    logger.debug("[POST_INLINE] Has auth: %s, comment_len=%d, text_selection_len=%d", 
                bool(__user_auth), len(comment) if comment else 0, len(text_selection) if text_selection else 0)
    
    if __user_auth:
        logger.debug("[POST_INLINE] Auth details: email=%s, token=%s, base_url=%s",
                    __user_auth.get("confluence_email", "NONE"),
                    _mask_secret(__user_auth.get("confluence_api_token", "NONE")),
                    __user_auth.get("confluence_base_url", "NONE"))
    
    api = confluence_api if not __user_auth else ConfluenceAPI(
        email=__user_auth.get("confluence_email", ""),
        api_token=__user_auth.get("confluence_api_token", ""),
        base_url=__user_auth.get("confluence_base_url", ""),
    )
    
    logger.debug("[POST_INLINE] Created API instance, calling post_inline_comment")
    result, error = api.post_inline_comment(page_id, comment, text_selection, match_index=match_index)
    
    if result is not None:
        logger.info("[POST_INLINE] SUCCESS - Result: %s", str(result)[:200])
        return {"success": True, "data": result}
    else:
        logger.error("[POST_INLINE] FAILED - Error: %s", error)
        return {"success": False, "error": error or "Failed to post inline comment"}

# -----------------------
# GitHub calls
# -----------------------
def list_repositories():
    try:
        result = github_api.list_repositories()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def list_pull_requests(repo: str, state: str = "open"):
    try:
        result = github_api.list_pull_requests(repo, state)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def add_comment(repo: str, pr_number: int, comment_text: str):
    try:
        result = github_api.add_comment(repo, pr_number, comment_text)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_files_in_pr(repo: str, pr_number: int):
    try:
        result = github_api.get_files_in_pr(repo, pr_number)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_base_and_head_sha(repo: str, pr_number: int):
    try:
        result = github_api.get_base_and_head_sha(repo, pr_number)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_file_content_at_ref(repo: str, file_path: str, ref: str):
    try:
        result = github_api.get_file_content_at_ref(repo, file_path, ref)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def file_with_line_no_and_diff(repo: str, pr_number: int, file_path: str):
    try:
        result = github_api.file_with_line_no_and_diff(repo, pr_number, file_path)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def add_file_level_comment(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str):
    try:
        result = github_api.add_file_level_comment(repo, pr_number, head_sha, comment_body, selected_path)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def add_inline_comment(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str, start_line: int, end_line: int, side: str):
    try:
        result = github_api.add_inline_comment(repo, pr_number, head_sha, comment_body, selected_path, start_line, end_line, side)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def show_comments(repo: str, pr_number: int):
    try:
        result = github_api.show_comments(repo, pr_number)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def reply_comment(repo: str, pr_number: int, comment_id: int, reply_text: str):
    try:
        result = github_api.reply_comment(repo, pr_number, comment_id, reply_text)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

def review_pull_request(repo: str, pr_number: int, checklist: list, skip_inline: bool = False, skip_footer: bool = False, max_inline_comments: int = 6, group_similar_inline: bool = True):
    try:
        # High-confidence mode is enforced for all PR reviews.
        result = github_api.review_pull_request(
            repo,
            pr_number,
            checklist,
            skip_inline=False,
            skip_footer=False,
            max_inline_comments=STRICT_INLINE_COMMENT_LIMIT,
            group_similar_inline=True,
        )
        # Ensure summary is always included in response
        if result and isinstance(result, dict):
            return {"success": True, "data": result}
        return {"success": True, "data": {"summary": "Review completed", "result": result}}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============================================================================
# COMBINED REVIEW FUNCTIONS - Wrappers for combined PR and Confluence reviews
# ============================================================================

def check_doc_coverage(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Check documentation coverage for new or modified code.
    
    Cross-checks:
    - Does the PR documentation reference the PR changes?
    - Is all new code documented in the Confluence page?
    """
    try:
        # Pass both PR and Confluence content for cross-checking
        return execute_combined_check("doc_coverage", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_code_examples(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Check that code examples in documentation match PR changes.
    
    Cross-checks:
    - Do examples in Confluence match the actual changes in PR?
    """
    try:
        return execute_combined_check("code_examples", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_api_signatures(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Check that API/function signatures are up to date.
    
    Cross-checks:
    - Do API docs in Confluence match PR signature changes?
    - Are all new APIs documented?
    """
    try:
        return execute_combined_check("api_signatures", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_config_documented(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Check that new configuration or environment variables are documented.
    
    Cross-checks:
    - Are new config vars from PR documented in Confluence?
    """
    try:
        return execute_combined_check("config_documented", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_architecture_alignment(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Ensure PR changes align with documented architecture and design decisions.
    
    Cross-checks:
    - Do the PR changes follow the architecture documented in Confluence?
    - Are design decisions properly documented?
    """
    try:
        return execute_combined_check("architecture_alignment", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_instructions_match(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Verify step-by-step instructions match the implementation.
    
    Cross-checks:
    - Do the instructions in Confluence reflect the actual implementation in PR?
    """
    try:
        return execute_combined_check("instructions_match", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_error_handling(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Verify error handling and edge cases are documented.
    
    Cross-checks:
    - Are error cases introduced in PR documented in Confluence?
    - Are edge cases documented?
    """
    try:
        return execute_combined_check("error_handling", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_deprecated_removed(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Check that deprecated features and APIs are removed from documentation.
    
    Cross-checks:
    - If PR removes deprecated code, is it removed from Confluence docs?
    """
    try:
        return execute_combined_check("deprecated_removed", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_terminology_consistent(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Verify consistent terminology between code and documentation.
    
    Cross-checks:
    - Is terminology used in PR consistent with Confluence docs?
    - Are new terms documented?
    """
    try:
        return execute_combined_check("terminology_consistent", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_pr_references(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Verify links to PR and ticket references are present in documentation.
    
    Cross-checks:
    - Does Confluence documentation reference the PR?
    - Are related tickets/issues documented?
    """
    try:
        return execute_combined_check("pr_references", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}


def check_code_path_sections(pr_content, pr_sha, conf_content, conf_page_id, repo, **kwargs):
    """Check that documentation sections cover all new code paths and functions.
    
    Cross-checks:
    - Does Confluence cover all new code paths introduced in PR?
    - Are all new functions documented?
    """
    try:
        return execute_combined_check("code_path_sections", repo, pr_sha, pr_content, conf_content)
    except Exception as e:
        return {"compliant": False, "error": str(e)}
def cleanup_old_bot_comments(
    repo: str,
    pr_number: int,
    keep_latest: int = 1,
    include_inline: bool = False,
):
    try:
        result = github_api.cleanup_old_bot_comments(
            repo,
            pr_number,
            keep_latest,
            include_inline,
        )
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _strip_html_to_text(value: str) -> str:
    if not value:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", value)
    no_tags = html.unescape(no_tags)
    no_tags = re.sub(r"\s+", " ", no_tags).strip()
    return no_tags


def _truncate_text(value: str, limit: int) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def summarize_pr_and_confluence(
    repo: str = "",
    pr_number: int = 0,
    page_id: str = "",
    max_files: int = 6,
    max_chars_per_file: int = 2000,
    max_page_chars: int = 8000,
) -> Dict[str, Any]:
    """Summarize a GitHub PR, a Confluence page, or both in a single response."""
    repo = (repo or "").strip()
    page_id = str(page_id or "").strip()
    has_pr = bool(repo and int(pr_number or 0) > 0)
    has_page = bool(page_id)

    if repo and not int(pr_number or 0):
        return {"success": False, "error": "pr_number is required when repo is provided"}
    if int(pr_number or 0) and not repo:
        return {"success": False, "error": "repo is required when pr_number is provided"}
    if not has_pr and not has_page:
        return {"success": False, "error": "Provide either repo + pr_number, page_id, or both"}

    if max_files < 1:
        max_files = 1
    if max_chars_per_file < 200:
        max_chars_per_file = 200
    if max_page_chars < 500:
        max_page_chars = 500

    pr_data: Dict[str, Any] | None = None
    confluence_data: Dict[str, Any] | None = None
    summary_sections: List[str] = []

    if has_pr:
        files_result = get_files_in_pr(repo, int(pr_number))
        if not files_result.get("success"):
            return files_result

        files = files_result.get("data") or []
        additions = sum(int(f.get("additions") or 0) for f in files if isinstance(f, dict))
        deletions = sum(int(f.get("deletions") or 0) for f in files if isinstance(f, dict))

        ext_counts: Dict[str, int] = {}
        for item in files:
            if not isinstance(item, dict):
                continue
            name = str(item.get("filename") or "")
            ext = "(no-ext)"
            if "." in name.rsplit("/", 1)[-1]:
                ext = name.rsplit(".", 1)[-1].lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

        dominant_ext = "mixed"
        if ext_counts:
            dominant_ext = max(ext_counts.items(), key=lambda kv: kv[1])[0]

        head_sha = None
        sha_result = get_base_and_head_sha(repo, int(pr_number))
        if sha_result.get("success") and isinstance(sha_result.get("data"), dict):
            head_sha = sha_result["data"].get("head_sha")

        content_samples: List[Dict[str, Any]] = []
        if head_sha:
            for item in files:
                if len(content_samples) >= max_files:
                    break
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or "").strip()
                status = str(item.get("status") or "")
                if not filename or status == "removed":
                    continue
                fetched = get_file_content_at_ref(repo, filename, head_sha)
                if not fetched.get("success"):
                    continue
                raw_text = str(fetched.get("data") or "")
                sample = _truncate_text(raw_text, max_chars_per_file)
                content_samples.append({
                    "filename": filename,
                    "status": status or "modified",
                    "sample": sample,
                })

        top_files = sorted(
            [f for f in files if isinstance(f, dict)],
            key=lambda f: int(f.get("changes") or (int(f.get("additions") or 0) + int(f.get("deletions") or 0))),
            reverse=True,
        )[:5]
        top_file_lines = [
            f"- {f.get('filename')} (+{int(f.get('additions') or 0)} / -{int(f.get('deletions') or 0)})"
            for f in top_files
        ]
        sample_lines = [f"- {s['filename']}: {len(s['sample'])} chars sampled" for s in content_samples]

        pr_data = {
            "repo": repo,
            "pr_number": int(pr_number),
            "file_count": len(files),
            "additions": additions,
            "deletions": deletions,
            "dominant_extension": dominant_ext,
            "top_files": top_files,
            "content_samples": content_samples,
        }
        summary_sections.extend([
            "## PR Summary",
            f"- Repository: {repo}",
            f"- PR Number: {int(pr_number)}",
            f"- Files changed: {len(files)}",
            f"- Net diff: +{additions} / -{deletions}",
            f"- Dominant file type: {dominant_ext}",
            "- Top changed files:",
            *(top_file_lines or ["- (none)"]),
            *( [""] + sample_lines if sample_lines else ["", "- No file content samples were retrieved."] ),
            "",
        ])

    if has_page:
        page_result = get_page_content(page_id)
        if not page_result.get("success"):
            return page_result

        page_storage = str(page_result.get("data") or "")
        page_plain = _strip_html_to_text(page_storage)
        page_excerpt = _truncate_text(page_plain, max_page_chars)
        headings = re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", page_storage, flags=re.IGNORECASE | re.DOTALL)
        headings = [_strip_html_to_text(h) for h in headings if _strip_html_to_text(h)]

        confluence_data = {
            "page_id": page_id,
            "page_excerpt": page_excerpt,
            "headings": headings[:20],
        }
        summary_sections.extend([
            "## Confluence Page Summary",
            f"- Page ID: {page_id}",
            f"- Headings found: {len(headings)}",
            *( [f"- Heading: {h}" for h in headings[:8]] or ["- Heading: (none detected)"] ),
            "",
            "## Confluence Excerpt",
            page_excerpt or "(empty page content)",
            "",
        ])

    if has_pr and has_page:
        summary_sections.extend([
            "## Combined Highlights",
            "- PR and page were both retrieved successfully.",
            f"- PR files sampled: {len((pr_data or {}).get('content_samples', []))}.",
            f"- Page headings detected: {len((confluence_data or {}).get('headings', []))}.",
        ])

    return {
        "success": True,
        "data": {
            "mode": "combined" if has_pr and has_page else ("pr_only" if has_pr else "page_only"),
            "repo": repo or None,
            "pr_number": int(pr_number) if has_pr else None,
            "page_id": page_id or None,
            "pr": pr_data,
            "confluence": confluence_data,
            "summary": "\n".join(summary_sections).strip(),
        },
    }

# -----------------------
# Example usage
# -----------------------
if __name__ == "__main__":
    space_result = create_space("Test Space", "TEST", "Space created via sync call")
    print("Create Space:", space_result)

    page_result = create_page("Test Page", "TEST", "<p>This is a test page</p>")
    print("Create Page:", page_result)

    content_result = get_page_content("123456")
    print("Page Content:", content_result)
