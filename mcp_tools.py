# mcp_confluence_tools.py

import logging
from fastmcp import FastMCP
from mcp_calls import (
    create_space,
    create_page,
    update_page,
    find_and_replace_in_page,
    get_page_content,
    get_page_content_by_sections,
    review_confluence_page,
    post_footer_comment,
    post_inline_comment,
    list_repositories,
    list_pull_requests,
    add_comment,
    get_files_in_pr,
    get_base_and_head_sha,
    get_file_content_at_ref,
    file_with_line_no_and_diff,
    add_file_level_comment,
    add_inline_comment,
    show_comments,
    reply_comment,
    review_pull_request,
    cleanup_old_bot_comments,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

mcp = FastMCP("Confluence MCP Server")

@mcp.tool(
    description="""
This creates a new space in Atlassian Confluence.

Use this tool whenever the user asks to create a Confluence space.
This tool automatically generates a valid space key and performs the API call internally.

Args:
- name (str): The name of the space to create.

Returns:
- dict: {"success": True, "data": {...}} if space is created
        {"success": False, "error": "error message"} if creation fails
"""
)
def create_confluence_space(name: str):
    """
    Creates a Confluence space using the provided name.
    The space key is automatically generated from the name.
    """
    key = name.upper().replace(" ", "")
    description = f"Space '{name}' created via MCP tool"
    return create_space(name, key, description)

@mcp.tool(
    description="""
Create a new page in an existing Confluence space.

Args:
 - title (str): The title of the page.
 - space_key (str): The key of the confluence space where the page will be created.
 - content (str): The HTML or text content of the page.

Returns:
 - dict: {"success": True, "data": {...}} if page is created
         {"success": False, "error": "error message"} if creation falls
"""
)
def create_confluence_page(title: str, space_key: str, content: str):
    return create_page(title, space_key, content)

@mcp.tool(
    description="""
Update an existing Confluence page.

Args:
 - page_id (str): The ID of the page to update.
 - title (str): New title for the page.
 - content (str): New HTML or text content of the page.
 - version (int): The version number for the update (incremented from current).
 - message (str, optional): Change message or summary for the update.

Returns:
 - dict: {"success": True, "data": {...}} if update succeeds
        {"success": False, "error": "error message"} if update fails
"""
)
def update_confluence_page(page_id: str, title: str, content: str, version: int, message: str = "Updated page"):
    return update_page(page_id, title, content, version, message)

@mcp.tool(
    description="""
Safely find and replace text in a Confluence page WITHOUT losing any existing content.

This is the PREFERRED tool for text replacements. It automatically:
- Fetches the full page content
- Performs the find/replace
- Saves the complete page back (preserving all other content)
- Handles version conflicts

Use this instead of update_confluence_page when you need to replace specific text.

Args:
 - page_id (str): The ID of the page.
 - find_text (str): The exact text to find.
 - replace_text (str): The text to replace it with.
 - replace_all (bool, optional): If True (default), replaces ALL occurrences. If False, replaces only the first.

Returns:
 - dict: {"success": True, "data": {"message": "...", "replacements": N}} if successful
         {"success": False, "error": "error message"} if it fails
"""
)
def find_and_replace_in_confluence_page(page_id: str, find_text: str, replace_text: str, replace_all: bool = True):
    return find_and_replace_in_page(page_id, find_text, replace_text, replace_all)

@mcp.tool(
    description="""
Retrieve the content of a Confluence page by sections for easier reading of Co-pilot for reviews

Args:
 - page_id (str): The ID of the page to retrieve.

Returns:
 - dict: {"success": True, "data": "page content"} if successful
         {"success": False, "error": "error message"} if retrieval fails
"""
)
def get_page_content_by_sections_tool(page_id: str, chunk_size: int = 2500, max_sections: int = 50):
    return get_page_content_by_sections(page_id, chunk_size, max_sections)

@mcp.tool(
    description="""
Retrieve the content of a Confluence page.

Args:
 - page_id (str): The ID of the page to retrieve.

Returns:
 - dict: {"success": True, "data": "page content"} if successful
         {"success": False, "error": "error message"} if retrieval fails
"""
)
def get_confluence_page_content(page_id: str):
    return get_page_content(page_id)

@mcp.tool(
    description="""
Review one or more Confluence pages for grammar, spelling, readability, repeated words,
long sentences, long paragraphs, and page structure issues. Inline comments are posted
directly on the page and a footer summary is added.

Args:
 - page_id (str, optional): A single Confluence page ID to review.
 - page_input (str, optional): One or more Confluence page IDs or URLs, separated by
   commas or newlines. Takes precedence over page_id when both are supplied.
 - checklist_page_id (str, optional): The Confluence page ID that holds the review
   checklist (a JSON array in a code block). If omitted, falls back to
   config.REVIEW_CHECKLIST_PAGE_ID, then to the built-in default checklist.

Returns:
 - dict: {"success": True, "data": {...}} with per-page review results
         {"success": False, "error": "..."} if review fails
"""
)
def review_confluence_page_content(page_id: str = "", page_input: str = "", checklist_page_id: str = "", skip_inline: bool = False, skip_footer: bool = False):
    source = page_input or page_id
    return review_confluence_page(source, "", checklist_page_id, skip_inline=skip_inline, skip_footer=skip_footer)

@mcp.tool(
    description="""
Post a footer comment on a Confluence page.

Args:
 - page_id (str): The ID of the page to comment on.
 - comment (str): The text of the comment.

Returns:
 - dict: {"success": True, "data": {...}} if comment is posted
         {"success": False, "error": "error message"} if posting fails
"""
)
def post_confluence_footer_comment(page_id: str, comment: str):
    """
    Adds a footer comment to the specified Confluence page.
    """
    return post_footer_comment(page_id, comment)

@mcp.tool(
    description="""
Post an inline comment on specific text within a Confluence page.

Args:
 - page_id (str): The ID of the page to comment on.
 - comment (str): The text of the comment.
 - text_selection (str): The exact text to attach the comment to.
 - match_index (int, optional): 0-based occurrence index to target when text appears multiple times.

Returns:
 - dict: {"success": True, "data": {...}} if comment is posted
         {"success": False, "error": "error message"} if posting fails
"""
)
def post_confluence_inline_comment(page_id: str, comment: str, text_selection: str, match_index: int | None = None):
    return post_inline_comment(page_id, comment, text_selection, match_index=match_index)

@mcp.tool(description="""
Lists all repositories accessible to the authenticated user.

Returns:
 - dict: {"success": True, "data": [repo_list]} If successful
         {"success": False, "error": "error message"} If retrieval fails
""")
def list_repositories_tool():
    return list_repositories()

@mcp.tool(description="""
Lists all pull requests for a given repository

Args:
 - repo (str): Repository name in format "owner/repo".

Returns:
 - dict: {"success": True, "data": [pull_requests]} If successful
          {"success": False, "error": "error message"} If retrieval fails
""")
def list_pull_requests_tool(repo: str):
    return list_pull_requests(repo)

@mcp.tool(description= """
Add a general (conversation) comment to a specific pull request.

          Args:
          - repo (str): Repository name
          - pr_number (int): Pull request number.
          - comment_text (str): Text of the comment.

          Returns:
          - dict: {"success": True, "data": {...}} If comment is posted
                  {"success": False, "error": "error message"} if posting fails
          """)
def add_comment_tool(repo: str, pr_number: int, comment_text: str):
    return add_comment(repo, pr_number, comment_text)

@mcp.tool(description="""
Retrieve files changed in a pull request.

Each item in the returned list includes:
    - filename
    - additions
    - deletions
    - and other metadata.

          Args:
          - repo (str): Repository name
          - pr_number (int): Pull request number

          Returns:
          - dict: {"success": True, "data": [file_objects]} if successful
                  {"success": False, "error": "error message"} if retrieval fails
""")
def get_files_in_pr_tool(repo: str, pr_number: int):
    return get_files_in_pr(repo, pr_number)

@mcp.tool(description="""
Get base and head SHAs for a pull request.

          Args:
          - repo (str): Repository name
          - pr_number (int): Pull request number.

          Returns:
          - dict: {"success": True, "data": {...}} if success
                  {"success": False, "error": "error message"} If retrieval fails
""")
def get_base_and_head_sha_tool(repo: str, pr_number: int):
    return get_base_and_head_sha(repo, pr_number)

@mcp.tool(description="""
          Fetch the content of a file at a specific commit reference.

          Args:
          - repo (str): Repository name
          - file_path (str): Path of the file in the repo.
          - ref (str): Commit SHA, branch name, or tag.

          Returns:
          - dict: {"success": True, "data": "file content"} if successful
                  {"success": False, "error": "error message"} if retrieval fails
          """
          )
def get_file_content_at_ref_tool(repo: str, file_path: str, ref: str):
    return get_file_content_at_ref(repo, file_path, ref)

@mcp.tool(description=
          """
          Generate a structured line-by-line diff for a file in a Pull Request.

          This function compares the file at the PR's base commit (old version)
          and the head commit (new version) and returns a structured list that
          represents the differences line by line.

          Args:
          - repo (str): Repository name
          - pr_number (int): Pull request number
          - file_path (str): Path of the file to diff.

          Returns:
          - dict: {"success": True, "data": [line_diff_objects]} if successful
                  {"success": False, "error": "error message"} if retrieval falls
          """)
def file_with_line_no_and_diff_tool(repo: str, pr_number: int, file_path: str):
    return file_with_line_no_and_diff(repo, pr_number, file_path)

@mcp.tool(description=
          """
          Add a FILE-LEVEL review comment on a specific file in a PR.

          This corresponds to the GitHub UI action: "Add comment" on the
          file header in the "Files changed" tab

          Args:
          - repo (str): Repository name
          - pr_number (int): Pull request number
          - head_sha (str): The SHA of the commit to comment on
          - comment_body (str): The text of the comment
          - selected_path (str): Path of the file to comment on.

          Returns
          - dict: {"success": True, "data": {...}} If comment is posted
                  {"success": False, "error": "error message"} if posting fails
          """)
def add_file_level_comment_tool(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str):
    return add_file_level_comment(repo, pr_number, head_sha, comment_body, selected_path)

@mcp.tool(description=
          """
          Add an inline review comment on a specific file and
          line range in a pull request.

          Args:
          - repo (str): Repository name
          - pr_number (int): Pull request number.
          - head_sha (str): Commit SHA to comment on
          - comment_body (str): The text of the comment
          - selected_path (str): Path of the file to comment on
          - start_line (int) Starting line of the comment
          - end_line (int): Ending line of the comment
          - side (str): 'LEFT' for base, 'RIGHT' for head of the diff.

          Returns:
          - dict: {"success": True, "data": {...}} If comment is posted
                  {"success": False, "error": "error message"} if posting fails
          """)
def add_inline_comment_tool(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str, start_line: int, end_line: int, side: str):
    return add_inline_comment(repo, pr_number, head_sha, comment_body, selected_path, start_line, end_line, side)

@mcp.tool(description=
          """
          Retrieve both general and inline comments for a given pull request.

          Args:
          - repo (str): Repository Name
          - pr_number (int): Pull Request number

          Returns:
          - dict: {"success": True, "data": [comments]} if successful
                  {"success": False, "error": "error message"} if retrieval falls
          """)
def show_comments_tool(repo: str, pr_number: int):
    return show_comments(repo, pr_number)

@mcp.tool(description=
          """
        Reply to a comment on a PR.

        This method auto-detects whether the given comment_id refers to:
          - an inline PR review comment, or
          - a general issue comment on the PR.

        Args:
        - repo (str): Repository name
        - pr_number (int): Pull request number
        - comment_id (int): The ID of the comment to reply to.
        -reply_text (str): The text of the reply

        Returns:
        - dict: {"success": True, "data": {...}} If reply is posted
                {"success": False, "error": "error message"} if posting fails
          """)
def reply_comment_tool(repo: str, pr_number: int, comment_id: int, reply_text: str):
    return reply_comment(repo, pr_number, comment_id, reply_text)

@mcp.tool(description=
          """
        Review a Pull Request using a structured checklist.

        This function performs a comprehensive review of a GitHub pull request by:
        1. Fetching all modified files in the PR
        2. Analyzing each file based on a provided checklist
        3. Adding both general (conversation) comments and inline comments where applicable

        The review process is driven by a checklist containing review rules with the following structure:
        - id: Unique identifier for the check (e.g., "long_sentence", "grammar")
        - name: Human-readable name of the check
        - description: Detailed description of what the check validates
        - enabled: Boolean flag to enable/disable this check
        - execution_order: Integer determining the order checks are executed

        Inline comments are automatically added for specific issues (e.g., lines exceeding 120 characters
        in long_sentence checks). General comments summarizing each check are always added.

        Args:
        - repo (str): Repository name (e.g., "my-repo")
        - pr_number (int): Pull request number to review
        - checklist (list): List of review rule dictionaries. Each item should contain:
          {
            "id": "check_id",
            "name": "Check Name",
            "description": "What this check does",
            "enabled": true,
            "execution_order": 10
          }

        Returns:
        - dict: {
            "success": True,  # Indicates if review was initiated successfully
            "data": null      # Review results printed to console output
          }
          or
        - dict: {
            "success": False,
            "error": "error message"  # Reason review failed
          }

        Example:
          checklist = [
            {
              "id": "long_sentence",
              "name": "Long Line Detection",
              "description": "Check for lines exceeding 120 characters",
              "enabled": True,
              "execution_order": 10
            }
          ]
          result = review_pull_request_tool("my-repo", 42, checklist)
          """)
def review_pull_request_tool(repo: str, pr_number: int, checklist: list, skip_inline: bool = False, skip_footer: bool = False):
    """
    Executes a comprehensive pull request review based on the provided checklist.
    skip_inline: If True, skip posting inline comments on specific lines.
    skip_footer: If True, skip posting the footer summary comment.
    """
    return review_pull_request(repo, pr_number, checklist, skip_inline=skip_inline, skip_footer=skip_footer)

@mcp.tool(description=
          """
        Delete older automation comments from a PR and keep only the latest summary.

        Use this after repeated review runs when too many footer comments were posted.

        Args:
        - repo (str): Repository name
        - pr_number (int): Pull request number
        - keep_latest (int): Number of latest summary comments to keep (default 1)
        - include_inline (bool): Also remove bot-authored flake8 inline comments

        Returns:
        - dict: {"success": True, "data": {...}} on success
                {"success": False, "error": "error message"} on failure
          """)
def cleanup_old_bot_comments_tool(
    repo: str,
    pr_number: int,
    keep_latest: int = 1,
    include_inline: bool = False,
):
    return cleanup_old_bot_comments(
        repo,
        pr_number,
        keep_latest,
        include_inline,
    )

if __name__ == "__main__":
    logger.info("Running Confluence MCP server using stdio transport")
    mcp.run(transport="stdio")
