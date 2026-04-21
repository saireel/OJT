"""Core GitHub API actions (HTTP, listing, file retrieval, and commenting)."""

import ast
import base64
import difflib
import re

import requests
import config
import time
from requests.exceptions import HTTPError

class GitHubActions:
    def __init__(self, base_url: str, owner: str, github_token: str):
            """
            Initialize the GitHubAPI client.
            :param base_url: Base URL for the GitHub API
            (e.g. "https://api.github.com")
            :param owner:    Default GitHub owner (user or organization name).
            :param github_token: Personal access token (PAT) or GitHub App token
                                 used for authentication.
            """
            # Ensure we don't end with a trailing slash to avoid double slashes
            # in URLs.
            self._base_url = base_url.rstrip("/")
            # GitHub repository owner (user or org). Many endpoints require this.
            self._owner = owner
            self._authenticated_login = None
            # Store the raw token in case we need it later (even though we use
            # config.GITHUB_TOKEN below).
            self._github_token = github_token
            # Default headers for all GitHub API requests:
            # - Authorization: Bearer <token> for authenticated calls.
            # - Accept: "application/vnd.github+json" is GitHub's recommended
            # media type.
            self._headers = {
                "Authorization": f"Bearer {self._github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            self._last_pr_lookup_error = None
            # In-memory caches to avoid redundant GitHub API calls during a review
            self._sha_cache = {}       # (repo, pr_number) -> (base_sha, head_sha)
            self._pr_title_cache = {}   # (repo, pr_number) -> title
            self._pr_files_cache = {}   # (repo, pr_number) -> (timestamp, [file_objects])
            self._file_content_cache = {}  # (repo, file_path, ref) -> content_str

    def _repo_path(self, repo: str) -> str:
            """Return an API-ready repo path that supports both 'repo' and 'owner/repo'."""
            normalized = str(repo or "").strip().strip("/")
            if not normalized:
                return f"{self._owner}"
            if "/" in normalized:
                owner, name = normalized.split("/", 1)
                return f"{owner.strip()}/{name.strip()}"
            return f"{self._owner}/{normalized}"

    def set_runtime_auth(self, owner: str | None = None, github_token: str | None = None, base_url: str | None = None):
            """Update active GitHub credentials for this runtime process."""
            if isinstance(owner, str) and owner.strip():
                self._owner = owner.strip()
            if isinstance(github_token, str) and github_token.strip():
                self._github_token = github_token.strip()
            if isinstance(base_url, str) and base_url.strip():
                self._base_url = base_url.strip().rstrip("/")
            if self._github_token:
                self._headers["Authorization"] = f"Bearer {self._github_token}"
            # Credentials or API base changed: invalidate request caches.
            self._sha_cache.clear()
            self._pr_title_cache.clear()
            self._pr_files_cache.clear()
            self._file_content_cache.clear()

    def _request(self, method: str, url: str,
                     data: dict | None = None,
                     params: dict | None = None):
            """
            Internal low-level HTTP request helper.
            - Wraps requests.request to:
              * set common headers
              * send optional JSON body and query params
              * raise a detailed exception on HTTP error.
            :param method: HTTP method ("GET", "POST", etc.).
            :param url:    Full URL for the GitHub endpoint.
            :param data:   Optional JSON body (for POST/PATCH).
            :param params: Optional query parameters (for GETs).
            :return:       requests.Response object (if status is OK).
            """
            # Perform the HTTP request with provided method, URL, headers,
            # JSON body (data), and query parameters (params).
            response = requests.request(method=method, url=url,
                                        headers=self._headers,
                                        json=data, params=params, timeout=20)
            try:
                # Raise HTTPError if response is 4xx or 5xx.
                response.raise_for_status()
            except HTTPError as exc:
                # Log diagnostic information before re-raising so errors
                # are easier to debug.
                print("GitHub returned error:", response.status_code, exc)
                print("Response Body:", response.text)
                raise
            # If no exception was raised, return the successful response.
            return response

    def _get_authenticated_login(self):
            """
            Return the login for the token used by this client.
            The value is cached after the first successful lookup.
            """
            if self._authenticated_login:
                return self._authenticated_login
            try:
                url = f"{self._base_url}/user"
                response = self._request("GET", url)
                self._authenticated_login = response.json().get("login")
            except Exception:
                self._authenticated_login = None
            return self._authenticated_login

    def list_repositories(self):
            """
            List repositories accessible to the authenticated user.
            Internally calls:
              GET /user/repos
            :return: A list of repository JSON objects where repo["owner"]["login"]
                     matches self._owner (case-insensitive).
            """
            # GitHub API endpoint for listing repositories of the
            # authenticated user.
            url = f"{self._base_url}/user/repos"
            # Pagination parameters:
            # - per_page: max number of items per page.
            # - page:     page index starting from 1.
            params = {"per_page": 100, "page": 1, "type": all}
            all_repos = []
            # Loop over pages until we receive an empty page (no more repos).
            while True:
                # Perform GET request for the current page.
                r = self._request("GET", url, data=None, params=params)
                page = r.json()
                # If page is empty, we've reached the end of the list.
                if not page:
                    break
                # Accumulate repositories into all_repos.
                all_repos.extend(page)
                # If this page has fewer results than per_page, we know
                # there are no more pages.
                if len(page) < params["per_page"]:
                    break
                # Otherwise, increment page and continue.
                params["page"] += 1
            return all_repos

    def list_pull_requests(self, repo: str, state: str = "open"):
            """
            List pull requests for a given repository.
            Internally calls:
              GET /repos/{owner}/{repo}/pulls
            :param repo:  Repository name (without owner).
            :param state: PR state filter ("open", "closed", "all").
            :return:      A list of PR JSON objects.
            """
            # GitHub endpoint for listing PRs on a specific repo.
            url = f"{self._base_url}/repos/{self._repo_path(repo)}/pulls"
            # Pagination + filter for PRs (e.g., only "open" by default).
            params = {"state": state, "per_page": 100, "page": 1}
            all_prs = []
            # Paginate until there are no more PRs returned.
            while True:
                r = self._request("GET", url, data=None, params=params)
                page = r.json()
                if not page:
                    break
                all_prs.extend(page)
                if len(page) < params["per_page"]:
                    break
                params["page"] += 1
            return all_prs

    def add_comment(self, repo: str, pr_number: int, comment_text: str):
            """
            Add a general (conversation) comment to a pull request.
            Internally calls:
              POST /repos/{owner}/{repo}/issues/{pr_number}/comments
            :param repo:         Repository name.
            :param pr_number:    Pull request number.
            :param comment_text: Comment body text.
            :return:             The response object from GitHub.
            """
            # Issue comments in GitHub are also used for PR conversation comments.
            url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}/issues/{pr_number}/comments"
            )
            # JSON body for the POST request.
            data = {"body": comment_text}
            # Use _request helper to send the POST.
            response = self._request("POST", url, data=data)
            return response

    def get_files_in_pr(self, repo: str, pr_number: int):
            """
            Return the raw file objects for a PR.
            Each item in the returned list includes:
              - filename
              - additions
              - deletions
              - and other metadata.
            Internally calls:
              GET /repos/{owner}/{repo}/pulls/{pr_number}/files
            :param repo:      Repository name.
            :param pr_number: Pull request number.
            :return:          A list of file JSON objects (may be empty).
            """
            cache_key = (repo, pr_number)
            cached_entry = self._pr_files_cache.get(cache_key)
            if cached_entry and (time.time() - cached_entry[0]) < 45:
                return cached_entry[1]

            url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}/pulls/{pr_number}/files"
            )
            # Pagination for PR files (usually up to 3000 changed files).
            params = {"per_page": 100, "page": 1}
            all_files = []
            while True:
                r = self._request("GET", url, data=None, params=params)
                page = r.json()
                if not page:
                    break
                all_files.extend(page)
                if len(page) < params["per_page"]:
                    break
                params["page"] += 1
            if not all_files:
                # Optional: log to stdout for debugging;
                # UI may also show “no files”.
                print("No files found in this Pull Request.")
                return []
            # Log a quick summary in stdout for debugging or CLI usage.
            print(f"\nFiles in Pull Request #{pr_number}:")
            for idx, f in enumerate(all_files, start=1):
                print(
                    f"{idx}. {f['filename']} "
                    f"(additions: {f['additions']}, deletions: {f['deletions']})"
                )
            # Return the full file objects so the UI can show filenames and stats.
            self._pr_files_cache[cache_key] = (time.time(), all_files)
            return all_files

    def get_base_and_head_sha(self, repo: str, pr_number: int):
            """
            Get base and head SHAs for a pull request.
            GET /repos/{owner}/{repo}/pulls/{pr_number}
            :return: (base_sha, head_sha) or (None, None) on error
            """
            self._last_pr_lookup_error = None
            cache_key = (repo, pr_number)
            if cache_key in self._sha_cache:
                return self._sha_cache[cache_key]

            url = f"{self._base_url}/repos/{self._repo_path(repo)}/pulls/{pr_number}"
            r = requests.get(url, headers=self._headers, timeout=20)
            if r.status_code != 200:
                api_message = ""
                try:
                    payload = r.json()
                    api_message = str(payload.get("message", "")).strip()
                    print("Response:", payload)
                except Exception:
                    print("Response Text:", r.text)

                if r.status_code == 401:
                    self._last_pr_lookup_error = (
                        "User-side configuration/authentication error: GitHub authentication failed "
                        "(401 Bad credentials). The token configured in GITHUB_TOKEN is invalid, expired, "
                        "or revoked. Generate a new token, update GITHUB_TOKEN, and restart the MCP server/web app."
                    )
                elif r.status_code == 403 and "rate limit" in api_message.lower():
                    self._last_pr_lookup_error = (
                        "GitHub API rate limit reached (403). Wait for reset or use a token with higher limits."
                    )
                elif r.status_code == 404:
                    self._last_pr_lookup_error = (
                        "User-side access/configuration error: PR not found or the configured token has no access "
                        "to this repository (404). Verify repo owner/name and token repository permissions."
                    )
                else:
                    suffix = f": {api_message}" if api_message else ""
                    self._last_pr_lookup_error = f"GitHub API error while fetching PR details ({r.status_code}){suffix}"

                print("Failed to get Pull Request details.")
                print("Status Code:", r.status_code)
                print("Reason:", self._last_pr_lookup_error)
                return None, None

            pr = r.json()
            base_sha = pr["base"]["sha"]   # old version
            head_sha = pr["head"]["sha"]   # new version
            self._sha_cache[cache_key] = (base_sha, head_sha)
            self._pr_title_cache[cache_key] = str(pr.get("title", "")).strip()
            return base_sha, head_sha

    def get_cached_pr_title(self, repo: str, pr_number: int) -> str:
            """Return a cached PR title when available; empty string otherwise."""
            return self._pr_title_cache.get((repo, pr_number), "")

    def get_file_content_at_ref(self, repo: str, file_path: str, ref: str):
            """
            Fetch the content of a file at a specific commit reference.
    
            Internally calls:
              GET /repos/{owner}/{repo}/contents/{file_path}?ref={ref}
    
            The GitHub API returns file contents encoded in base64,
             which this function decodes into readable UTF-8 text.
    
             :param repo: Repository name.
             :param file_path: Path to the file in the repository.
             :param ref: Commit SHA, branch, or tag.
             :return: Decoded file content as a string.
            """
            _fc_key = (repo, file_path, ref)
            if _fc_key in self._file_content_cache:
                return self._file_content_cache[_fc_key]
            url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}/contents/{file_path}"
            )
            params = {"ref": ref}
            r = requests.get(url, headers=self._headers, params=params)
            # File might not exist in this ref (added or deleted)
            if r.status_code == 404:
                self._file_content_cache[_fc_key] = ""
                return ""
            if r.status_code != 200:
                print("Failed to fetch file:", r.status_code)
                return ""
            data = r.json()
            if data.get("encoding") != "base64":
                return ""
            try:
                _fc_result = base64.b64decode(data["content"]).\
                          decode("utf-8", errors="replace")
                self._file_content_cache[_fc_key] = _fc_result
                return _fc_result
            except Exception:
                return ""

    def file_with_line_no_and_diff(self, repo: str,
                                       pr_number: int, file_path: str):
            """"
            Return structured diff data:
            [
                {
                    "old_lineno": 1,
                    "old_line": "print('old')",
                    "new_lineno": 1,
                    "new_line": "print('new')",
                    "type": "changed" | "added" | "removed" | "unchanged"
                },
                ...
            ]
            """
            base_sha, head_sha = self.get_base_and_head_sha(repo, pr_number)
            if not base_sha or not head_sha:
                return []
            old_text = self.get_file_content_at_ref(repo, file_path, base_sha)
            new_text = self.get_file_content_at_ref(repo, file_path, head_sha)
            old_lines = old_text.splitlines()
            new_lines = new_text.splitlines()
            diff = difflib.ndiff(old_lines, new_lines)
            result = []
            old_line_no = 0
            new_line_no = 0
            for line in diff:
                code = line[:2]
                content = line[2:]
                if code == "  ":
                    old_line_no += 1
                    new_line_no += 1
                    result.append({
                        "old_lineno": old_line_no,
                        "old_line": content,
                        "new_lineno": new_line_no,
                        "new_line": content,
                        "type": "unchanged"
                    })
                elif code == "- ":
                    old_line_no += 1
                    result.append({
                        "old_lineno": old_line_no,
                        "old_line": content,
                        "new_lineno": "",
                        "new_line": "",
                        "type": "removed"
                    })
                elif code == "+ ":
                    new_line_no += 1
                    result.append({
                        "old_lineno": "",
                        "old_line": "",
                        "new_lineno": new_line_no,
                        "new_line": content,
                        "type": "added"
                    })
            return result

    def add_file_level_comment(
            self,
            repo: str,
            pr_number: int,
            head_sha: str,
            comment_body: str,
            selected_path: str,
        ):
            """
            Add a FILE-LEVEL review comment on a specific file in a PR.
            This corresponds to the GitHub UI action: "Add comment" on the
            file header in the "Files changed" tab (i.e. comment on the file
            as a whole, not tied to a specific line).
            Internally calls:
              POST /repos/{owner}/{repo}/pulls/{pr_number}/comments
            Payload uses:
              - body        : Comment text.
              - commit_id   : SHA of the head commit of the PR.
              - path        : File path (relative to repo root).
              - subject_type: "file" to indicate file-level comment.
            :param repo:          Repository name.
            :param pr_number:     Pull request number.
            :param head_sha:      SHA of the head commit of the PR.
            :param comment_body:  Comment text.
            :param selected_path: File path within the repo.
            """
            url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}"
                f"/pulls/{pr_number}/comments"
            )
            data = {
                "body": comment_body,
                "commit_id": head_sha,
                "path": selected_path,
                "subject_type": "file",  # key: file-level, not line-level
            }
            r = self._request("POST", url, data=data)
            if r.status_code != 201:
                print("\nFailed to create file-level comment.")
                print("Status Code:", r.status_code)
                try:
                    print("Response:", r.json())
                except Exception:
                    print("Response text:", r.text)
                return
            comment = r.json()
            print("\nFile-level comment created successfully!")
            print(f"URL: {comment.get('html_url')}")
            return comment

    def add_inline_comment(self, repo: str, pr_number: int,
                               head_sha: str, comment_body: str,
                               selected_path: str, start_line: int,
                               end_line: int, side: str):
            """
            Add an inline review comment on a specific file and
            line range in a pull request.
            If inline attachment fails (misplaced), falls back to
            posting as a general PR comment with file/line context.
            This internally calls:
            POST /repos/{owner}/{repo}/pulls/{pr_number}/comments
            The payload uses:
            - commit_id  : SHA of the head commit of the PR
            - path       : file path (relative to repo root)
            - start_line : starting line in the diff
            - line       : ending line in the diff
            - side       : "RIGHT" (new code) or "LEFT" (old code)
            :param repo:          Repository name.
            :param pr_number:     Pull request number.
            :param head_sha:      SHA of the head commit of the PR.
            :param comment_body:  Comment text.
            :param selected_path: File path within the repo.
            :param start_line:    Starting line number in the diff.
            :param end_line:      Ending line number in the diff.
            :param side:          "RIGHT": new code, "LEFT": old code.
            """
            if side not in ("RIGHT", "LEFT"):
                raise ValueError('Parameter "side" must be "RIGHT" or "LEFT".')
            url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}"
                f"/pulls/{pr_number}/comments"
            )
            # Determine payload based on single-line vs range
            if start_line == end_line:
                # Single-line comment
                data = {
                    "body": comment_body,
                    "commit_id": head_sha,
                    "path": selected_path,
                    "line": start_line,
                    "side": side,
                }
            else:
                # Range comment
                data = {
                    "body": comment_body,
                    "commit_id": head_sha,
                    "path": selected_path,
                    "start_line": start_line,
                    "start_side": side,
                    "line": end_line,
                    "side": side,
                }
            r = self._request("POST", url, data=data)
            if r.status_code != 201:
                # FALLBACK: If inline comment fails, post as general comment instead
                print(f"\nFailed to create inline comment at {selected_path}:{start_line}-{end_line}")
                print("Status Code:", r.status_code)
                try:
                    error_detail = r.json()
                    print("Response:", error_detail)
                except Exception:
                    print("Response text:", r.text)
            
                # Fallback: Post as general PR comment with context
                fallback_comment = (
                    f"**Comment for {selected_path} (lines {start_line}-{end_line}):**\n\n"
                    f"{comment_body}"
                )
                print(f"\nFalling back to general PR comment...")
                fallback_response = self.add_comment(repo, pr_number, fallback_comment)
            
                if fallback_response.status_code == 201:
                    print("[OK] Successfully posted as general comment instead!")
                    return fallback_response
                else:
                    print(f"[ERROR] Fallback also failed with status {fallback_response.status_code}")
                    return fallback_response
            comment = r.json()
            print("\nInline comment created successfully!")
            print(f"URL: {comment.get('html_url')}")
            return r

    def show_comments(self, repo: str, pr_number: int):
            """
            Retrieve both general and inline comments for a given PR.
            Internally calls:
              - GET /repos/{owner}/{repo}/issues/{pr_number}/comments
              - GET /repos/{owner}/{repo}/pulls/{pr_number}/comments
            :param repo:      Repository name.
            :param pr_number: Pull request number.
            :return:          (general_comments, inline_comments) as two lists.
            """
            # General (conversation) comments endpoint.
            general_url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}/issues/"
                f"{pr_number}/comments"
            )
            # Inline (review) comments endpoint.
            inline_url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}/pulls/"
                f"{pr_number}/comments"
            )
            # Fetch both sets of comments using the shared _request helper.
            general_resp = self._request("GET", general_url)
            inline_resp = self._request("GET", inline_url)
            general_comments = general_resp.json()
            inline_comments = inline_resp.json()
            return general_comments, inline_comments

    def reply_comment(
            self,
            repo: str,
            pr_number: int,
            comment_id: int,
            reply_text: str,
        ):
            """
            Reply to a comment on a PR.
            This method auto-detects whether the given comment_id refers to:
              - an inline PR review comment, or
              - a general issue comment on the PR.
            Behaviour:
              * If it's an inline comment:
                  - Create a true threaded reply using:
                    POST /repos/{owner}/{repo}/pulls/{pr_number}/comments
                    with 'in_reply_to' = comment_id
              * If it's a general PR-level comment:
                  - Post a new issue comment that @-mentions and quotes
                  the original using:
                    POST /repos/{owner}/{repo}/issues/{pr_number}/comments
            :param repo:       Repository name.
            :param pr_number:  Pull request number.
            :param comment_id: ID of the existing comment
            (either general or inline).
            :param reply_text: Body of the reply.
            """
            # -------------------------------------------------------------- #
            # 1) Try to treat the comment ID as an inline review comment     #
            # -------------------------------------------------------------- #
            inline_url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}"
                f"/pulls/comments/{comment_id}"
            )
            inline_resp = requests.get(inline_url, headers=self._headers)
            if inline_resp.status_code == 200:
                # At this point, we know the comment ID corresponds to a
                # PR review (inline) comment.
                # This endpoint allows us to create a threaded reply to the
                # existing inline comment by using "in_reply_to".
                reply_url = (
                    f"{self._base_url}/repos/{self._repo_path(repo)}"
                    f"/pulls/{pr_number}/comments"
                )
                data = {
                    "body": reply_text,
                    "in_reply_to": comment_id,
                }
                resp = self._request("POST", reply_url, data=data)
                print("\nInline reply created successfully "
                      "threaded review comment)!")
                print("URL:", resp.json().get("html_url"))
                return resp
            # -------------------------------------------------------------- #
            # 2) Otherwise, try as a general issue (PR-level) comment        #
            # -------------------------------------------------------------- #
            general_url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}"
                f"/issues/comments/{comment_id}"
            )
            general_resp = requests.get(general_url, headers=self._headers)
            if general_resp.status_code == 200:
                # The comment ID refers to a general issue/PR comment.
                original = general_resp.json()
                author = original["user"]["login"]
                original_body = original["body"]
                # Compose a reply that mentions the original author and
                # quotes their comment for context.
                reply_body = (
                    f"Replying to @{author}'s comment:\n"
                    f"> {original_body}\n\n"
                    f"{reply_text}"
                )
                issues_url = (
                    f"{self._base_url}/repos/{self._repo_path(repo)}"
                    f"/issues/{pr_number}/comments"
                )
                data = {"body": reply_body}
                resp = self._request("POST", issues_url, data=data)
                print("\nGeneral reply created successfully (issue comment)!")
                print("URL:", resp.json().get("html_url"))
                return resp
            # -------------------------------------------------------------- #
            # 3) Not found as either inline or general                      #
            # -------------------------------------------------------------- #
            # If neither inline nor general endpoints return 200,
            # the ID is not recognized as a comment for this repo/PR.
            raise ValueError(
                f"Comment ID {comment_id} was not found as an inline or "
                "general comment "
                f"for repo '{repo}'."
            )

    def delete_comment(self, repo: str, comment_id: int, comment_type: str = "issue"):
            """
            Delete a single GitHub PR comment.
            :param repo: Repository name.
            :param comment_id: Comment ID to delete.
            :param comment_type: "issue" for footer/general comments,
                                 "review" for inline review comments.
            :return: Dict with deletion status.
            """
            if comment_type not in ("issue", "review"):
                raise ValueError('comment_type must be "issue" or "review".')
            if comment_type == "issue":
                url = (
                    f"{self._base_url}/repos/{self._repo_path(repo)}"
                    f"/issues/comments/{comment_id}"
                )
            else:
                url = (
                    f"{self._base_url}/repos/{self._repo_path(repo)}"
                    f"/pulls/comments/{comment_id}"
                )
            response = self._request("DELETE", url)
            return {
                "deleted": response.status_code == 204,
                "status_code": response.status_code,
                "comment_id": comment_id,
                "comment_type": comment_type,
            }

    def cleanup_old_bot_comments(
            self,
            repo: str,
            pr_number: int,
            keep_latest: int = 1,
            include_inline: bool = False,
        ):
            """
            Delete older automation comments while keeping the latest summary comment.
            This targets comments authored by the authenticated user and generated by
            review automation (checklist footer comments and flake8 summary comments).
            :param repo: Repository name.
            :param pr_number: Pull request number.
            :param keep_latest: Number of latest summary comments to keep.
            :param include_inline: If True, also delete bot-authored flake8 inline comments.
            :return: Dict with cleanup stats and deleted IDs.
            """
            keep_latest = max(1, int(keep_latest))
            current_login = self._get_authenticated_login()
            general_comments, inline_comments = self.show_comments(repo, pr_number)
            def is_owned_by_bot(comment: dict) -> bool:
                author = (comment or {}).get("user", {}).get("login")
                if current_login:
                    return author == current_login
                return author == self._owner
            def is_automation_footer(body: str) -> bool:
                body = (body or "").strip()
                if not body:
                    return False
                if "**Automated PR Review Summary**" in body:
                    return True
                if "**Flake8 Check**" in body or "Flake8 Compliance Summary" in body:
                    return True
                if body.startswith("**") and "** check:" in body:
                    return True
                return False
            summary_comments = [
                c for c in general_comments
                if is_owned_by_bot(c) and "**Automated PR Review Summary**" in (c.get("body") or "")
            ]
            summary_comments.sort(key=lambda c: c.get("created_at", ""), reverse=True)
            keep_ids = {c.get("id") for c in summary_comments[:keep_latest]}
            deleted_issue_ids = []
            failed_issue = []
            for comment in general_comments:
                comment_id = comment.get("id")
                if not comment_id:
                    continue
                if not is_owned_by_bot(comment):
                    continue
                body = comment.get("body") or ""
                if not is_automation_footer(body):
                    continue
                if comment_id in keep_ids:
                    continue
                try:
                    self.delete_comment(repo=repo, comment_id=comment_id, comment_type="issue")
                    deleted_issue_ids.append(comment_id)
                except Exception as exc:
                    failed_issue.append({"id": comment_id, "error": str(exc)})
            deleted_inline_ids = []
            failed_inline = []
            if include_inline:
                for comment in inline_comments:
                    comment_id = comment.get("id")
                    body = (comment.get("body") or "").strip()
                    if not comment_id or not is_owned_by_bot(comment):
                        continue
                    if not body.startswith("Flake8 "):
                        continue
                    try:
                        self.delete_comment(repo=repo, comment_id=comment_id, comment_type="review")
                        deleted_inline_ids.append(comment_id)
                    except Exception as exc:
                        failed_inline.append({"id": comment_id, "error": str(exc)})
            return {
                "repo": repo,
                "pr_number": pr_number,
                "kept_summary_count": len(keep_ids),
                "deleted_issue_comments": len(deleted_issue_ids),
                "deleted_issue_comment_ids": deleted_issue_ids,
                "failed_issue_deletions": failed_issue,
                "deleted_inline_comments": len(deleted_inline_ids),
                "deleted_inline_comment_ids": deleted_inline_ids,
                "failed_inline_deletions": failed_inline,
            }
