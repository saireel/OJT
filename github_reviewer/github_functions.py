"""
GitHubFunctions.py

This module defines the GitHubAPI class, which encapsulates all HTTP
interactions with the GitHub REST API that are needed by the web app.

It provides methods to:
- List repositories for a user/owner
- List pull requests for a repository
- List files in a pull request
- Get PR head ref and SHA
- Fetch and display file contents with line numbers
- Add general comments on a PR
- Add inline review comments on specific files/lines
- Show all comments (general + inline) on a PR
- Reply to an existing comment (auto-detecting general vs inline)
"""

# Used to perform HTTP requests to GitHub's REST API

import difflib
import re
import requests
# Holds configuration values like BASE_URL, OWNER, GITHUB_TOKEN
import config
# Used to decode base64-encoded file contents from GitHub
import base64
# Used to handle HTTP error responses
from requests.exceptions import HTTPError


class GitHubAPI:
    """
    GitHubAPI wraps GitHub's REST API endpoints relevant to pull requests
    and comments.

    This class is designed for clarity and traceability; each public method
    maps directly to one or more GitHub API endpoints and hides the low-level
    HTTP details from the rest of the application.
    """

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
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ------------------------------------------------------------------ #
    # Internal helper: _request                                          #
    # ------------------------------------------------------------------ #

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
                                    json=data, params=params,)

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

    # ------------------------------------------------------------------ #
    # Repo & PR listing                                                  #
    # ------------------------------------------------------------------ #

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
        url = f"{self._base_url}/repos/{self._owner}/{repo}/pulls"

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
    
    def check_flake8_compliance(self, repo: str, pr_number: int, files: list) -> dict:
        """
        Check Python files in the PR for flake8 compliance (PEP 8, naming, complexity).
        
        Runs flake8 on all .py files in the repo and reports violations found.
        Only checks files that were modified in the PR.
        
        :param repo: Repository name
        :param pr_number: Pull request number
        :param files: List of file objects from get_files_in_pr()
        :return: Dict with {
            "compliant": bool,
            "violations_count": int,
            "violations": [
                {"file": "path.py", "line": 10, "col": 5, "code": "E302", "message": "..."}
            ]
        }
        """
        import subprocess
        import tempfile
        import os
        
        python_files = [f["filename"] for f in files if f["filename"].endswith(".py")]
        if not python_files:
            return {"compliant": True, "violations_count": 0, "violations": []}
        
        violations = []
        
        for py_file in python_files:
            try:
                # Get file content at head SHA
                base_sha, head_sha = self.get_base_and_head_sha(repo, pr_number)
                if not head_sha:
                    continue
                content = self.get_file_content_at_ref(repo, py_file, head_sha)
                
                if not content:
                    continue
                
                # Write to temp file and check with flake8
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                
                try:
                    result = subprocess.run(
                        ['flake8', tmp_path, '--show-source', '--statistics'],
                        capture_output=True,
                        text=True
                    )
                    
                    # Parse flake8 output
                    for line in result.stdout.strip().split('\n'):
                        if line and ':' in line:
                            # Format: filepath:line:col: CODE message
                            parts = line.split(':')
                            if len(parts) >= 4:
                                try:
                                    line_no = int(parts[1])
                                    col = int(parts[2])
                                    msg_part = ':'.join(parts[3:]).strip()
                                    # Extract code (E302, W503, etc.)
                                    code_parts = msg_part.split(' ')
                                    code = code_parts[0] if code_parts else 'UNKNOWN'
                                    message = ' '.join(code_parts[1:]) if len(code_parts) > 1 else msg_part
                                    
                                    violations.append({
                                        "file": py_file,
                                        "line": line_no,
                                        "col": col,
                                        "code": code,
                                        "message": message
                                    })
                                except (ValueError, IndexError):
                                    pass
                finally:
                    os.unlink(tmp_path)
            except Exception as e:
                print(f"Warning: Could not check {py_file} with flake8: {e}")
        
        return {
            "compliant": len(violations) == 0,
            "violations_count": len(violations),
            "violations": violations[:50]  # Return first 50 violations
        }

    def check_consistency(self, repo: str, pr_number: int, files: list) -> dict:
        """
        Detect consistency issues across changed files:
        1. Same term spelled differently (e.g., "Project X" vs "ProjectX")
        2. Same metric with different values  
        3. Formatting variations in identifiers

        :param repo: Repository name
        :param pr_number: Pull request number
        :param files: List of changed files
        :return: Dict with consistency issues found
        """
        import re
        from collections import defaultdict

        issues = {
            "spelling_variations": [],
            "metric_inconsistencies": [],
            "formatting_variations": [],
            "total_issues": 0,
        }

        try:
            # Get PR base and head SHAs
            base_sha, head_sha = self.get_base_and_head_sha(repo, pr_number)

            # Collect all text from changed files
            all_text = ""
            file_contents = {}

            for file_obj in files:
                file_path = file_obj.get("filename", "")
                if not file_path.endswith((".py", ".js", ".ts", ".md", ".txt")):
                    continue

                try:
                    if not head_sha:
                        continue
                    content = self.get_file_content_at_ref(repo, file_path, head_sha)
                    if content:
                        file_contents[file_path] = content
                        all_text += content + "\n"
                except Exception:
                    pass

            # 1. DETECT SPELLING VARIATIONS
            term_patterns = {
                "Project X": [r"Project\s+X", r"ProjectX", r"Proj\s+X", r"PROJECTX"],
                "Config": [r"Config", r"Configuration", r"cfg", r"settings"],
                "Database": [r"Database", r"DB", r"data base", r"db"],
                "API": [r"API", r"api", r"Api"],
            }

            for canonical_term, variations in term_patterns.items():
                found_variants = {}
                for variant_pattern in variations:
                    matches = re.finditer(variant_pattern, all_text, re.IGNORECASE)
                    for match in matches:
                        variant = match.group()
                        found_variants[variant] = found_variants.get(variant, 0) + 1

                if len(found_variants) > 1:
                    issues["spelling_variations"].append({
                        "term": canonical_term,
                        "variations": found_variants,
                    })

            # 2. DETECT METRIC INCONSISTENCIES
            metric_pattern = r"(version|timeout|limit|max_retries|batch_size)\s*[:=]\s*([\d.]+|\d+)"
            metric_values = defaultdict(set)

            for metric_match in re.finditer(metric_pattern, all_text, re.IGNORECASE):
                metric_name = metric_match.group(1).lower()
                metric_value = metric_match.group(2)
                metric_values[metric_name].add(metric_value)

            for metric_name, values in metric_values.items():
                if len(values) > 1:
                    issues["metric_inconsistencies"].append({
                        "metric": metric_name,
                        "inconsistent_values": list(values),
                        "count": len(values),
                    })

            # 3. DETECT FORMATTING VARIATIONS
            issues["total_issues"] = (
                len(issues["spelling_variations"]) +
                len(issues["metric_inconsistencies"]) +
                len(issues["formatting_variations"])
            )

            return issues

        except Exception as e:
            print(f"Error during consistency check: {e}")
            return {"error": str(e), "total_issues": 0}



    # ------------------------------------------------------------------ #
    # Fetch review instructions from repo                                #
    # ------------------------------------------------------------------ #

    # Pattern to match instruction/checklist filenames (case-insensitive)
    _INSTRUCTION_FILENAME_PATTERN = re.compile(
        r"^(instructions|review_instructions|review|pr_review|checklist)"
        r"\.md$",
        re.IGNORECASE,
    )

    def fetch_review_instructions(self, repo: str, ref: str = "main") -> dict:
        """
        Search the repository for a review instruction/checklist markdown file.

        Fetches the repo file tree in a single API call, then matches filenames
        against a pattern. Searches root, .github/, and docs/ directories.

        :param repo: Repository name.
        :param ref:  Git ref to search (branch, tag, or SHA). Defaults to "main".
        :return: Dict with keys:
                 - "found": bool
                 - "file_path": str (path that matched, or "")
                 - "content": str (file content, or "")
                 - "ref": str (the ref used)
        """
        import re as _re

        # Fetch the full file tree in one API call
        tree_url = (
            f"{self._base_url}/repos/{self._owner}/{repo}"
            f"/git/trees/{ref}?recursive=1"
        )
        resp = requests.get(tree_url, headers=self._headers)
        if resp.status_code != 200:
            print(f"Could not fetch repo tree: {resp.status_code}")
            return {"found": False, "file_path": "", "content": "", "ref": ref}

        tree = resp.json().get("tree", [])

        # Directories we care about (empty string = repo root)
        search_dirs = ("", ".github/", "docs/")

        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item["path"]
            # Check if file is in one of our target directories
            for prefix in search_dirs:
                if not path.startswith(prefix):
                    continue
                filename = path[len(prefix):]
                # Skip files in subdirectories deeper than our target
                if "/" in filename:
                    continue
                if self._INSTRUCTION_FILENAME_PATTERN.match(filename):
                    content = self.get_file_content_at_ref(repo, path, ref)
                    if content:
                        print(f"Found review instructions: {path} (ref: {ref})")
                        return {
                            "found": True,
                            "file_path": path,
                            "content": content,
                            "ref": ref,
                        }

        print(f"No review instruction file found in {repo} at ref '{ref}'.")
        return {"found": False, "file_path": "", "content": "", "ref": ref}

    def review_pull_request(self, repo: str, pr_number: int, checklist: list):
        """
        Review a single pull request based on the provided checklist.

        Behavior:
        - Runs flake8 on changed Python files.
        - Posts flake8 issues inline when issue count is <= 10.
        - Posts flake8 issue summary in a footer comment when issue count is > 10.
        - Publishes one consolidated footer summary to avoid comment spam.

        :param repo: Repository name
        :param pr_number: Pull request number (from the link)
        :param checklist: List of review rules (dict) with fields like:
                        id, name, description, enabled, execution_order
        :return: None
        """
         # Get PR details
        head_sha = self.get_base_and_head_sha(repo, pr_number)[1]
        
        # Add this guard clause
        if not head_sha:
            print(f"Failed to get PR #{pr_number} details (head_sha is None).")
            return
        
        pr_title = next(
            (pr["title"] for pr in self.list_pull_requests(repo) if pr["number"] == pr_number),
            "Unknown PR",
        )
        print(f"\nReviewing PR #{pr_number}: {pr_title}")

        # Auto-fetch review instructions from the repository
        instructions = self.fetch_review_instructions(repo, ref=head_sha)
        if instructions["found"]:
            print(f"Using review instructions from: {instructions['file_path']}")
        else:
            print("No review instruction file found; using default review behavior.")

        files = self.get_files_in_pr(repo, pr_number)
        if not files:
            print("No changed files found in this PR.")
            return

        # Run flake8 check for changed Python files.
        flake8_results = self.check_flake8_compliance(repo, pr_number, files)
        flake8_count = flake8_results["violations_count"]
        inline_posted = 0
        inline_failed = []

        if 0 < flake8_count <= 10:
            # Small issue sets are easier to address as inline review comments.
            for violation in flake8_results["violations"]:
                inline_text = (
                    f"Flake8 {violation['code']}: {violation['message']} "
                    f"(line {violation['line']}, col {violation['col']})."
                )
                try:
                    self.add_inline_comment(
                        repo=repo,
                        pr_number=pr_number,
                        head_sha=head_sha,
                        comment_body=inline_text,
                        selected_path=violation["file"],
                        start_line=violation["line"],
                        end_line=violation["line"],
                        side="RIGHT",
                    )
                    inline_posted += 1
                except Exception as exc:
                    inline_failed.append(f"{violation['file']}:{violation['line']}:{violation['col']} ({exc})")


        # Run consistency checks
        consistency_results = self.check_consistency(repo, pr_number, files)
        consistency_count = consistency_results.get("total_issues", 0)
        consistency_spinner = ""
        if consistency_count == 0:
            consistency_spinner = "(No issues found)"
        elif consistency_count <= 3:
            consistency_spinner = f"({consistency_count} issue{'' if consistency_count == 1 else 's'} detected)"
        else:
            consistency_spinner = f"({consistency_count} issues detected - review recommended)"


        enabled_items = [
            item for item in sorted(checklist, key=lambda x: x["execution_order"])
            if item.get("enabled", True)
        ]

        # Consolidated footer summary: one comment only.
        summary_lines = [
            "**Automated PR Review Summary**",
            f"- PR: #{pr_number} - {pr_title}",
            f"- Files changed: {len(files)}",
            f"- Checklist checks executed: {len(enabled_items)}",
        ]

        # Include instruction file info in summary
        if instructions["found"]:
            summary_lines.append(
                f"- Review instructions: `{instructions['file_path']}` (ref: {instructions['ref'][:7]})"
            )

        if flake8_count == 0:
            summary_lines.append("- Flake8: Approved (0 issues).")
        elif flake8_count <= 10:
            summary_lines.append(
                f"- Flake8: {flake8_count} issue(s) found; posted {inline_posted} inline comment(s)."
            )
            if inline_failed:
                summary_lines.append(
                    f"- Inline fallback needed for {len(inline_failed)} issue(s) that could not be attached to diff lines."
                )
                for item in inline_failed[:5]:
                    summary_lines.append(f"  - {item}")
        else:
            summary_lines.append(f"- Flake8: {flake8_count} issue(s) found (not approved).")
            summary_lines.append("- Showing first 10 issues in this comment:")
            for violation in flake8_results["violations"][:10]:
                summary_lines.append(
                    f"  - {violation['file']}:{violation['line']}:{violation['col']} "
                    f"{violation['code']} {violation['message']}"
                )
            summary_lines.append(f"  ... and {flake8_count - 10} more")

        self.add_comment(repo, pr_number, "\n".join(summary_lines))
        print(f"\nPR #{pr_number} review completed with consolidated summary.")

    # ------------------------------------------------------------------ #
    # Comments: add general comment                                      #
    # ------------------------------------------------------------------ #

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
            f"{self._base_url}/repos/{self._owner}/"
            f"{repo}/issues/{pr_number}/comments"
        )

        # JSON body for the POST request.
        data = {"body": comment_text}

        # Use _request helper to send the POST.
        response = self._request("POST", url, data=data)
        return response

    # ------------------------------------------------------------------ #
    # PR files listing                                                   #
    # ------------------------------------------------------------------ #

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
        url = (
            f"{self._base_url}/repos/{self._owner}/"
            f"{repo}/pulls/{pr_number}/files"
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
        return all_files

    # ------------------------------------------------------------------ #
    # PR head ref & SHA                                                 #
    # ------------------------------------------------------------------ #

    def get_base_and_head_sha(self, repo: str, pr_number: int):
        """
        Get base and head SHAs for a pull request.

        GET /repos/{owner}/{repo}/pulls/{pr_number}

        :return: (base_sha, head_sha) or (None, None) on error
        """

        url = f"{self._base_url}/repos/{self._owner}/{repo}/pulls/{pr_number}"

        r = requests.get(url, headers=self._headers)

        if r.status_code != 200:
            print("Failed to get Pull Request details.")
            print("Status Code:", r.status_code)
            try:
                print("Response:", r.json())
            except Exception:
                print("Response Text:", r.text)
            return None, None

        pr = r.json()

        base_sha = pr["base"]["sha"]   # old version
        head_sha = pr["head"]["sha"]   # new version

        return base_sha, head_sha

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
        url = (
            f"{self._base_url}/repos/{self._owner}/{repo}/contents/{file_path}"
        )
        params = {"ref": ref}

        r = requests.get(url, headers=self._headers, params=params)

        # File might not exist in this ref (added or deleted)
        if r.status_code == 404:
            return ""

        if r.status_code != 200:
            print("Failed to fetch file:", r.status_code)
            return ""

        data = r.json()

        if data.get("encoding") != "base64":
            return ""

        try:
            content = base64.b64decode(data["content"]).\
                      decode("utf-8", errors="replace")
            return content
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # File content with line numbers                                    #
    # ------------------------------------------------------------------ #

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

        # ------------------------------------------------------------------ #
    # Comments: add file-level review comment on a PR file              #
    # ------------------------------------------------------------------ #

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
            f"{self._base_url}/repos/{self._owner}/{repo}"
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

    # ------------------------------------------------------------------ #
    # Add inline review comment                                         #
    # ------------------------------------------------------------------ #

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
            f"{self._base_url}/repos/{self._owner}/{repo}"
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
            f"{self._base_url}/repos/{self._owner}/{repo}/issues/"
            f"{pr_number}/comments"
        )

        # Inline (review) comments endpoint.
        inline_url = (
            f"{self._base_url}/repos/{self._owner}/{repo}/pulls/"
            f"{pr_number}/comments"
        )

        # Fetch both sets of comments using the shared _request helper.
        general_resp = self._request("GET", general_url)
        inline_resp = self._request("GET", inline_url)

        general_comments = general_resp.json()
        inline_comments = inline_resp.json()

        return general_comments, inline_comments

    # ------------------------------------------------------------------ #
    # Reply to an existing comment (auto-detect type)                   #
    # ------------------------------------------------------------------ #

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
            f"{self._base_url}/repos/{self._owner}/{repo}"
            f"/pulls/comments/{comment_id}"
        )
        inline_resp = requests.get(inline_url, headers=self._headers)

        if inline_resp.status_code == 200:
            # At this point, we know the comment ID corresponds to a
            # PR review (inline) comment.

            # This endpoint allows us to create a threaded reply to the
            # existing inline comment by using "in_reply_to".
            reply_url = (
                f"{self._base_url}/repos/{self._owner}/{repo}"
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
            f"{self._base_url}/repos/{self._owner}/{repo}"
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
                f"{self._base_url}/repos/{self._owner}/{repo}"
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
                f"{self._base_url}/repos/{self._owner}/{repo}"
                f"/issues/comments/{comment_id}"
            )
        else:
            url = (
                f"{self._base_url}/repos/{self._owner}/{repo}"
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


github_api = GitHubAPI(
    base_url=config.GITHUB_BASE_URL,
    owner=config.GITHUB_OWNER,
    github_token=config.GITHUB_TOKEN
)