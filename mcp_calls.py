# mcp_calls.py
# Synchronous wrappers for Confluence and GitHub API calls.
# These are called directly by FastMCP tool definitions in mcp_tools.py.

import re
from typing import Any, Dict, List

from confluence_reviewer import confluence_api, ReviewActions, SyntaxActions
from github_reviewer import github_api

# Shared instances (created once, reused across all calls)
syntax_actions = SyntaxActions(confluence_api)
review_actions = ReviewActions(confluence_api, syntax_actions)

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

def get_page_content(page_id: str) -> Dict[str, Any]:
    text, error = confluence_api.get_page_storage(page_id)
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

def get_page_content_by_sections(page_id: str, chunk_size: int = 2500, max_sections: int = 50) -> Dict[str, Any]:
    text, error = syntax_actions.get_page_content_by_sections(page_id, chunk_size, max_sections)
    if text is not None:
        return {"success": True, "data": text}
    return {"success": False, "error": error or "Failed to get page content"}

def review_confluence_page(page_input: str = "", page_id: str = "", checklist_page_id: str = "", skip_inline: bool = False, skip_footer: bool = False) -> Dict[str, Any]:
    source = page_input or page_id
    page_ids = _extract_confluence_page_ids(source)
    if not page_ids:
        return {
            "success": False,
            "error": "No valid Confluence page IDs found. Pass a page ID, a Confluence page URL, or multiple values separated by commas/newlines.",
        }

    if len(page_ids) == 1:
        result, error = review_actions.advanced_confluence_page_review(page_ids[0], checklist_page_id, skip_inline=skip_inline, skip_footer=skip_footer)
        if result is not None:
            return {"success": True, "data": result}
        return {"success": False, "error": error or "Failed to review confluence page"}

    reviewed = []
    failed = []
    for pid in page_ids:
        result, error = review_actions.advanced_confluence_page_review(pid, checklist_page_id, skip_inline=skip_inline, skip_footer=skip_footer)
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

def post_footer_comment(page_id: str, comment: str) -> Dict[str, Any]:
    response, error = confluence_api.post_footer_comment(page_id, comment)
    if response:
        return {"success": True, "data": response.json()}
    return {"success": False, "error": error or "Failed to post footer comment"}

def post_inline_comment(page_id: str, comment: str, text_selection: str, match_index: int | None = None) -> Dict[str, Any]:
    result, error = confluence_api.post_inline_comment(page_id, comment, text_selection, match_index=match_index)
    if result is not None:
        return {"success": True, "data": result}
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

def review_pull_request(repo: str, pr_number: int, checklist: list, skip_inline: bool = False, skip_footer: bool = False):
    try:
        result = github_api.review_pull_request(repo, pr_number, checklist, skip_inline=skip_inline, skip_footer=skip_footer)
        # Ensure summary is always included in response
        if result and isinstance(result, dict):
            return {"success": True, "data": result}
        return {"success": True, "data": {"summary": "Review completed", "result": result}}
    except Exception as e:
        return {"success": False, "error": str(e)}

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
