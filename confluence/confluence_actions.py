import logging
import re
from typing import Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

class ConfluenceAPI:
    """Minimal Confluence REST API client with session and retries."""

    def __init__(self, email: str, api_token: str, base_url: str, timeout: int = 30):
        """Store Confluence credentials and initialize the shared HTTP session."""
        self.email = email
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry handling and basic auth configured."""
        session = requests.Session()
        retry_strategy = Retry(
            total=getattr(config, "MAX_RETRIES", 3),
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.auth = (self.email, self.api_token)
        return session

    def _request(self, method: str, endpoint: str, **kwargs) -> Tuple[Optional[requests.Response], Optional[str]]:
        """Send a Confluence API request and return either the response or an error message."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response, None
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None, str(e)

    # Spaces
    def create_space(self, name: str, key: str, description: str) -> Tuple[Optional[requests.Response], Optional[str]]:
        """Create a new Confluence space using the provided name, key, and description."""
        if not name or not key:
            return None, "name and key are required"
        return self._request("POST", "/rest/api/space", json={"name": name, "key": key, "description": {"plain": {"value": description, "representation": "plain"}}})

    # Pages
    def create_page(self, title: str, space_key: str, content: str) -> Tuple[Optional[requests.Response], Optional[str]]:
        """Create a Confluence page in the target space with storage-format content."""
        if not title or not space_key or not content:
            return None, "title, space_key, and content are required"
        data = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": content, "representation": "storage"}}
        }
        return self._request("POST", "/rest/api/content", json=data)

    def update_page(self, page_id: str, title: str, content: str, version: int, message: str) -> Tuple[Optional[requests.Response], Optional[str]]:
        """Update an existing page by sending a new title, body, and incremented version."""
        if not page_id or not title or version < 1:
            return None, "page_id, title, and valid version are required"
        data = {
            "type": "page",
            "title": title,
            "body": {"storage": {"value": content, "representation": "storage"}},
            "version": {"number": version + 1},
            "message": message or "Updated page"
        }
        return self._request("PUT", f"/rest/api/content/{page_id}", json=data)

    def get_page_storage(self, page_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Fetch a page's Confluence storage-format body for downstream processing."""
        if not page_id:
            return None, "page_id is required"

        response, error = self._request(
            "GET",
            f"/rest/api/content/{page_id}",
            params={"expand": "body.storage"}
        )

        if error:
            return None, error

        try:
            storage = response.json().get("body", {}).get("storage", {}).get("value", "")
            return storage, None
        except Exception as e:
            return None, f"Failed to parse storage content: {e}"

    def _get_document_title(self, page_id: str) -> Optional[str]:
        """Fetch the page title from Confluence API."""
        try:
            response, error = self._request(
                "GET",
                f"/rest/api/content/{page_id}",
                params={"expand": ""}
            )
            if not error and response:
                return response.json().get("title")
        except Exception:
            pass
        return None

    # Comments
    @staticmethod
    def _markdown_to_confluence_html(text: str) -> str:
        """Convert common Markdown patterns to Confluence storage-format HTML."""
        # If it already looks like HTML, return as-is
        if re.search(r"<(?:p|ul|ol|h[1-6]|table|div|strong|em)", text):
            return text
        lines = text.split("\n")
        html_parts: list[str] = []
        in_list = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                continue
            # Headings
            heading_match = re.match(r"^(#{1,6})\s+(.*)", stripped)
            if heading_match:
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2)
                html_parts.append(f"<h{level}>{heading_text}</h{level}>")
                continue
            # Horizontal rule
            if re.match(r"^-{3,}$", stripped):
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                html_parts.append("<hr/>")
                continue
            # List items
            list_match = re.match(r"^[-*+]\s+(.*)", stripped)
            if list_match:
                if not in_list:
                    html_parts.append("<ul>")
                    in_list = True
                item_text = list_match.group(1)
                html_parts.append(f"<li>{item_text}</li>")
                continue
            # Regular paragraph
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{stripped}</p>")
        if in_list:
            html_parts.append("</ul>")
        # Inline formatting: **bold** and *italic*
        result = "".join(html_parts)
        result = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", result)
        result = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", result)
        return result

    def post_footer_comment(self, page_id: str, comment: str) -> Tuple[Optional[requests.Response], Optional[str]]:
        """Post a footer comment to the target Confluence page."""
        if not page_id or not comment:
            return None, "page_id and comment are required"
        html_comment = self._markdown_to_confluence_html(comment)
        data = {"type": "comment", "body": {"storage": {"value": html_comment, "representation": "storage"}}, "container": {"type": "page", "id": page_id}}
        return self._request("POST", "/rest/api/content", json=data)

    def post_inline_comment(
        self,
        page_id: str,
        comment: str,
        text_selection: str,
        original_position: Optional[int] = None,
        page_text: Optional[str] = None,
    ) -> Tuple[Optional[dict], Optional[str]]:
        """Post an inline comment anchored to matching text on a page.
        The method resolves the anchor occurrence automatically before posting.
        Pass *page_text* to skip the page-fetch when the caller already has it."""

        if not page_id or not comment or not text_selection:
            return None, "`page_id`, `comment`, and `text_selection` are required"

        if not hasattr(self, "_syntax_actions") or self._syntax_actions is None:
            from .confluence_syntax_actions import SyntaxActions
            self._syntax_actions = SyntaxActions(self)
        syntax_actions = self._syntax_actions

        if page_text is None:
            page_text, error = syntax_actions._get_page_plain_text(page_id)
            if error:
                return None, error

        selection = syntax_actions._normalize_inline_text(text_selection)
        if not selection:
            return None, "text_selection is empty after normalization"

        matches = [m.start() for m in re.finditer(re.escape(selection), page_text)]
        if not matches:
            return None, f"text_selection '{selection}' not found in page"

        match_count = len(matches)
        if isinstance(original_position, int) and original_position >= 0:
            match_index = min(range(match_count), key=lambda i: abs(matches[i] - original_position))
        else:
            match_index = 0

        path = "/api/v2/inline-comments"
        payload = {
            "pageId": page_id,
            "body": {
                "representation": "storage",
                "value": comment,
            },
            "inlineCommentProperties": {
                "textSelection": selection,
                "textSelectionMatchCount": match_count,
                "textSelectionMatchIndex": match_index,
            },
        }

        response, error = self._request("POST", path, json=payload)
        if error:
            return None, error

        try:
            data = response.json()
            return {
                "status": response.status_code,
                "comment_id": data.get("id"),
                "text_selection": selection,
                "occurrences_found": match_count,
                "match_index": match_index,
            }, None
        except Exception:
            return {"status": response.status_code}, None

# Reusable instance
confluence_api = ConfluenceAPI(
    base_url=config.BASE_URL,
    email=config.EMAIL,
    api_token=config.CONFLUENCE_API_TOKEN
)
