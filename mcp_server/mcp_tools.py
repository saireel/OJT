import logging
from typing import Optional, Any, Dict, List, Tuple, cast
from fastmcp import FastMCP
from mcp_server.mcp_calls import (
    create_space,
    create_page,
    update_page,
    find_and_replace_in_page,
    get_page_content,
    get_page_content_by_sections,
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
    set_runtime_auth,
    check_doc_coverage,
    check_code_examples,
    check_api_signatures,
    check_config_documented,
    check_architecture_alignment,
    check_instructions_match,
    check_error_handling,
    check_deprecated_removed,
    check_terminology_consistent,
    check_pr_references,
    check_code_path_sections,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

mcp = FastMCP("Confluence MCP Server")


@mcp.tool(description="Create a new Confluence space")
def tool_create_space(name: str, key: str, description: str) -> Dict[str, Any]:
    return create_space(name, key, description)


@mcp.tool(description="Create a new Confluence page")
def tool_create_page(title: str, space_key: str, content: str) -> Dict[str, Any]:
    return create_page(title, space_key, content)


@mcp.tool(description="Update an existing Confluence page")
def tool_update_page(page_id: str, title: str, content: str, version: int, message: str) -> Dict[str, Any]:
    return update_page(page_id, title, content, version, message)


@mcp.tool(description="Find and replace text in a Confluence page")
def tool_find_and_replace(page_id: str, find_text: str, replace_text: str, replace_all: bool = True) -> Dict[str, Any]:
    return find_and_replace_in_page(page_id, find_text, replace_text, replace_all)


@mcp.tool(description="Get content of a Confluence page")
def tool_get_page_content(page_id: str) -> Dict[str, Any]:
    return get_page_content(page_id)


@mcp.tool(description="Get content of a Confluence page by sections")
def tool_get_page_sections(page_id: str, chunk_size: int = 2500, max_sections: int = 50) -> Dict[str, Any]:
    return get_page_content_by_sections(page_id, chunk_size, max_sections)


@mcp.tool(description="Post a footer comment on a Confluence page")
def tool_post_footer_comment(page_id: str, comment: str) -> Dict[str, Any]:
    return post_footer_comment(page_id, comment)


@mcp.tool(description="Post an inline comment on a Confluence page")
def tool_post_inline_comment(page_id: str, comment: str, text_selection: str, match_index: Optional[int] = None) -> Dict[str, Any]:
    return post_inline_comment(page_id, comment, text_selection, match_index)


@mcp.tool(description="List GitHub repositories")
def tool_list_repositories() -> Any:
    return list_repositories()


@mcp.tool(description="List pull requests in a GitHub repository")
def tool_list_pull_requests(repo: str, state: str = "open") -> Any:
    return list_pull_requests(repo, state)


@mcp.tool(description="Add a comment to a GitHub pull request")
def tool_add_pr_comment(repo: str, pr_number: int, comment_text: str) -> Any:
    return add_comment(repo, pr_number, comment_text)


@mcp.tool(description="Get files in a GitHub pull request")
def tool_get_files_in_pr(repo: str, pr_number: int) -> Any:
    return get_files_in_pr(repo, pr_number)


@mcp.tool(description="Get base and head commit SHAs for a GitHub pull request")
def tool_get_base_and_head_sha(repo: str, pr_number: int) -> Any:
    return get_base_and_head_sha(repo, pr_number)


@mcp.tool(description="Get file content at a specific ref in GitHub")
def tool_get_file_content(repo: str, file_path: str, ref: str) -> Any:
    return get_file_content_at_ref(repo, file_path, ref)


@mcp.tool(description="Get file content with line numbers and diff in GitHub")
def tool_file_with_line_no_and_diff(repo: str, pr_number: int, file_path: str) -> Any:
    return file_with_line_no_and_diff(repo, pr_number, file_path)


@mcp.tool(description="Add a file-level comment to a GitHub pull request")
def tool_add_file_level_comment(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str) -> Any:
    return add_file_level_comment(repo, pr_number, head_sha, comment_body, selected_path)


@mcp.tool(description="Add an inline comment to a GitHub pull request")
def tool_add_inline_comment(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str, start_line: int, end_line: int, side: str) -> Any:
    return add_inline_comment(repo, pr_number, head_sha, comment_body, selected_path, start_line, end_line, side)


@mcp.tool(description="Show comments on a GitHub pull request")
def tool_show_comments(repo: str, pr_number: int) -> Any:
    return show_comments(repo, pr_number)


@mcp.tool(description="Reply to a comment on a GitHub pull request")
def tool_reply_comment(repo: str, pr_number: int, comment_id: int, reply_text: str) -> Any:
    return reply_comment(repo, pr_number, comment_id, reply_text)


@mcp.tool(description="Cleanup old bot comments on a GitHub pull request")
def tool_cleanup_old_bot_comments(repo: str, pr_number: int) -> Any:
    return cleanup_old_bot_comments(repo, pr_number)

@mcp.tool(description="Review a GitHub pull request")
def tool_review_pull_request(
    repo: str,
    pr_number: int,
    checklist: list,
    skip_inline: bool = False,
    skip_footer: bool = False,
    max_inline_comments: int = 6,
    group_similar_inline: bool = True,
) -> Any:
    return review_pull_request(
        repo,
        pr_number,
        checklist,
        skip_inline,
        skip_footer,
        max_inline_comments,
        group_similar_inline,
    )


@mcp.tool(description="Set runtime authentication credentials")
def tool_set_runtime_auth(
    confluence_email: str = "",
    confluence_api_token: str = "",
    confluence_base_url: str = "",
    github_owner: str = "",
    github_token: str = "",
    github_base_url: str = "",
) -> Any:
    return set_runtime_auth(
        confluence_email=confluence_email,
        confluence_api_token=confluence_api_token,
        confluence_base_url=confluence_base_url,
        github_owner=github_owner,
        github_token=github_token,
        github_base_url=github_base_url,
    )

@mcp.tool(description="Check documentation coverage for PR changes")
def tool_check_doc_coverage(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_doc_coverage(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if code examples match PR changes")
def tool_check_code_examples(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_code_examples(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if API/function signatures are up to date")
def tool_check_api_signatures(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_api_signatures(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if config/env variables are documented")
def tool_check_config_documented(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_config_documented(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check architecture/design alignment with PR")
def tool_check_architecture_alignment(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_architecture_alignment(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if step-by-step instructions match implementation")
def tool_check_instructions_match(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_instructions_match(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if error handling & edge cases are documented")
def tool_check_error_handling(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_error_handling(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if deprecated features/APIs are removed from docs")
def tool_check_deprecated_removed(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_deprecated_removed(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check for consistent terminology between code and docs")
def tool_check_terminology(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_terminology_consistent(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if PR/ticket references are present")
def tool_check_pr_references(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_pr_references(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check for missing sections in covered code paths")
def tool_check_code_path_sections(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_code_path_sections(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


if __name__ == "__main__":
    mcp.run()
