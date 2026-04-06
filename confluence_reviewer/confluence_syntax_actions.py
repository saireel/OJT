import html
import logging
import os
import re
import shutil
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

class SyntaxActions:
    """Text parsing, normalization, and language checking utilities."""

    def __init__(self, confluence_api):
        """Store reference to ConfluenceAPI instance for making requests."""
        self.api = confluence_api
        self._language_tool = None
        self._java_checked = False
        self._java_available = False

    def _extract_plain_text_from_storage(self, storage: str) -> str:
        """Convert Confluence storage HTML into normalized plain text for analysis."""

        if not storage:
            return ""

        class _StorageTextParser(HTMLParser):
            BLOCK_TAGS = {
                "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
                "section", "article", "header", "footer", "blockquote", "table",
                "tbody", "thead", "tfoot", "ul", "ol", "pre"
            }

            def __init__(self) -> None:
                """Initialize the parser fragment buffer used while walking HTML content."""
                super().__init__(convert_charrefs=False)
                self.fragments: list[str] = []

            def handle_starttag(self, tag: str, attrs) -> None:
                """Insert separators for block-level and line-break tags while parsing."""
                lower_tag = (tag or "").lower()
                if lower_tag in {"br", "hr"}:
                    self.fragments.append("\n")
                elif lower_tag == "li":
                    self.fragments.append("\n- ")
                elif lower_tag in self.BLOCK_TAGS:
                    self.fragments.append("\n")

            def handle_endtag(self, tag: str) -> None:
                """Insert a newline when a block-level HTML tag closes."""
                lower_tag = (tag or "").lower()
                if lower_tag in self.BLOCK_TAGS:
                    self.fragments.append("\n")

            def handle_data(self, data: str) -> None:
                """Append raw text nodes to the fragment buffer."""
                if data:
                    self.fragments.append(data)

            def handle_entityref(self, name: str) -> None:
                """Preserve named HTML entities until the final unescape pass."""
                self.fragments.append(f"&{name};")

            def handle_charref(self, name: str) -> None:
                """Preserve numeric HTML entities until the final unescape pass."""
                self.fragments.append(f"&#{name};")

        parser = _StorageTextParser()
        parser.feed(storage)
        parser.close()

        text = "".join(parser.fragments)
        text = html.unescape(text)
        text = unicodedata.normalize("NFKC", text).replace("\u00a0", " ")
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


    def _extract_tables_from_storage(self, storage: str) -> list:
        """Extract tables from Confluence storage HTML as list of row-lists.

        Returns a list of tables, each table being a list of rows,
        each row being a list of cell text strings.
        """
        if not storage:
            return []

        tables = []
        # Split on <table> boundaries
        table_blocks = re.findall(
            r"<table[^>]*>(.*?)</table>", storage, re.DOTALL | re.IGNORECASE
        )
        for block in table_blocks:
            rows = []
            for row_match in re.finditer(
                r"<tr[^>]*>(.*?)</tr>", block, re.DOTALL | re.IGNORECASE
            ):
                cells = []
                for cell_match in re.finditer(
                    r"<t[hd][^>]*>(.*?)</t[hd]>",
                    row_match.group(1),
                    re.DOTALL | re.IGNORECASE,
                ):
                    cell_html = cell_match.group(1)
                    cell_text = re.sub(r"<[^>]+>", " ", cell_html)
                    cell_text = html.unescape(cell_text).strip()
                    cell_text = re.sub(r"\s+", " ", cell_text)
                    cells.append(cell_text)
                if cells:
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return tables

    def _get_page_plain_text(self, page_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Fetch a page and return its normalized plain-text content."""
        storage, error = self.api.get_page_storage(page_id)
        if error:
            return None, error
        return self._extract_plain_text_from_storage(storage), None

    def _normalize_inline_text(self, text: str) -> str:
        """Normalize whitespace and entities so inline anchors match page text reliably."""

        normalized = html.unescape(text or "")
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _extract_surrounding_sentence(self, token: str, full_text: str) -> str:
        """Find token in full_text and return the enclosing line as a longer anchor."""
        if not token or not full_text:
            return token or ""

        pos = full_text.lower().find(token.lower())
        if pos == -1:
            return token

        line_start = full_text.rfind("\n", 0, pos)
        line_start = line_start + 1 if line_start != -1 else 0
        line_end = full_text.find("\n", pos)
        line_end = line_end if line_end != -1 else len(full_text)
        line = full_text[line_start:line_end].strip()

        if len(line.split()) < 6 and line_start > 0:
            prev_end = line_start - 1
            prev_start = full_text.rfind("\n", 0, prev_end)
            prev_start = prev_start + 1 if prev_start != -1 else 0
            prev_line = full_text[prev_start:prev_end].strip()
            if prev_line:
                line = prev_line + " " + line

        words = line.split()
        if len(words) > 15:
            token_lower = token.lower()
            centre = 0
            for i, w in enumerate(words):
                if token_lower in w.lower():
                    centre = i
                    break
            start_w = max(0, centre - 7)
            line = " ".join(words[start_w: start_w + 15])

        return line if line else token

    def _build_inline_candidates(self, text: str) -> list[str]:
        """Generate anchor candidates starting with the exact text, then expanding."""
        normalized = self._normalize_inline_text(text)
        if not normalized:
            return []

        candidates = []

        def add_candidate(value: str) -> None:
            candidate = self._normalize_inline_text(value)
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        # 1. Try the exact text first (even short words)
        add_candidate(normalized)

        # 2. If the text is already long, also try progressively smaller slices
        words = normalized.split()
        for size in (3, 4, 6, 8, 10, 12):
            if len(words) >= size:
                add_candidate(" ".join(words[:size]))

        # 3. Try clause-level fragments
        for fragment in re.split(r"[,;:.!?]\s+", normalized):
            add_candidate(fragment)

        return candidates[:20]

    def get_page_content_by_sections(
        self,
        page_id: str,
        chunk_size: int = 2500,
        max_sections: int = 5
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Retrieve a page and split its plain text into review-friendly sections.
        Boundaries prefer sentence endings so chunks do not cut words or sentences abruptly."""

        if not page_id:
            return None, "page_id is required"

        text, error = self._get_page_plain_text(page_id)
        if error:
            return None, error
        text = text or ""

        if chunk_size < 1:
            return None, "chunk_size must be greater than 0"
        if max_sections < 1:
            return None, "max_sections must be greater than 0"

        def find_chunk_end(content: str, start: int) -> int:
            """Choose the nearest sentence boundary for the next chunk end."""
            sentence_endings = ".!?"
            proposed_end = min(start + chunk_size, len(content))
            if proposed_end >= len(content):
                return len(content)

            previous_boundary = max(
                content.rfind(mark, start, proposed_end)
                for mark in sentence_endings
            )
            next_candidates = [content.find(mark, proposed_end) for mark in sentence_endings]
            next_candidates = [index for index in next_candidates if index != -1]
            next_boundary = min(next_candidates) if next_candidates else -1

            if previous_boundary == -1 and next_boundary == -1:
                next_space = content.find(" ", proposed_end)
                return next_space if next_space != -1 else len(content)

            if previous_boundary == -1:
                return next_boundary + 1
            if next_boundary == -1:
                return previous_boundary + 1

            distance_to_previous = proposed_end - previous_boundary
            distance_to_next = next_boundary - proposed_end
            if distance_to_next <= distance_to_previous:
                return next_boundary + 1
            return previous_boundary + 1

        chunks = []
        start = 0
        while start < len(text) and len(chunks) < max_sections:
            end = find_chunk_end(text, start)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = max(end, start + 1)

        return {
            "page_id": page_id,
            "sections_returned": len(chunks),
            "chunk_size": chunk_size,
            "sections": chunks,
        }, None

    def _detect_java_home(self) -> Optional[str]:
        """Try common Windows locations to find a usable Java installation."""

        configured = os.environ.get("JAVA_HOME")
        if configured:
            java_exe = Path(configured) / "bin" / "java.exe"
            if java_exe.exists():
                return str(Path(configured))

        java_on_path = shutil.which("java")
        if java_on_path:
            return str(Path(java_on_path).resolve().parent.parent)

        search_roots = [
            Path("C:/Program Files/Java"),
            Path("C:/Program Files/Eclipse Adoptium"),
            Path("C:/Program Files/Amazon Corretto"),
            Path("C:/Program Files/Microsoft"),
        ]

        discovered = []
        for root in search_roots:
            if not root.exists():
                continue

            for java_exe in root.glob("**/bin/java.exe"):
                discovered.append(java_exe)

        if not discovered:
            return None

        discovered.sort(reverse=True)
        return str(discovered[0].parent.parent)

    def _ensure_java_runtime(self) -> bool:
        """Ensure Java is available so language-tool based checks can run."""
        if self._java_checked:
            return self._java_available

        if shutil.which("java"):
            self._java_checked = True
            self._java_available = True
            return True

        detected_home = self._detect_java_home()
        if not detected_home:
            self._java_checked = True
            self._java_available = False
            return False

        os.environ["JAVA_HOME"] = detected_home
        java_bin = str(Path(detected_home) / "bin")
        existing_path = os.environ.get("PATH", "")
        path_parts = existing_path.split(os.pathsep) if existing_path else []
        if java_bin not in path_parts:
            os.environ["PATH"] = java_bin + os.pathsep + existing_path if existing_path else java_bin

        self._java_available = shutil.which("java") is not None
        self._java_checked = True
        return self._java_available

    def _collect_language_issues_adaptive(self, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Review text for grammar/spelling issues using LanguageTool.

        Runs a primary pass on the full text, then additional chunk-based passes
        for longer pages to catch issues missed in the first scan."""
        words = text.split()
        word_count = len(words)

        if not text.strip():
            return {
                "issues_found": 0,
                "issues_returned": 0,
                "issues": [],
                "word_count": word_count,
                "language_passes": 0,
            }, None

        try:
            if not self._ensure_java_runtime():
                return None, "Language review failed: Java runtime not found. Set JAVA_HOME or add java to PATH."

            if self._language_tool is None:
                import language_tool_python
                self._language_tool = language_tool_python.LanguageTool("en-US")
            tool = self._language_tool
        except Exception as e:
            return None, f"Language review failed: {e}"

        def _pick_attr(obj, *names, default=None):
            for name in names:
                if hasattr(obj, name):
                    return getattr(obj, name)
            return default

        def _run_check(input_text: str, limit: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
            """Run LanguageTool on a text segment and normalize the matches."""
            try:
                matches = tool.check(input_text)
            except Exception as e:
                return None, f"Language review failed: {e}"

            issues = []
            for match in matches[:limit]:
                issue_type = str(_pick_attr(match, "rule_issue_type", "ruleIssueType", default="grammar") or "grammar").lower()
                matched_text = _pick_attr(match, "matched_text", "matchedText", default="")
                replacements = _pick_attr(match, "replacements", default=[]) or []
                context = _pick_attr(match, "context", default="")
                category = str(_pick_attr(match, "category", default="") or "").upper()
                priority = 0
                if issue_type in {"typographical", "misspelling", "grammar", "style"}:
                    priority = 0
                elif issue_type in {"whitespace", "typography"} or category == "TYPOS":
                    priority = 2
                else:
                    priority = 1

                rule_id = str(_pick_attr(match, "ruleId", "rule_id", default="") or "")
                offset = _pick_attr(match, "offset", default=None)

                issues.append({
                    "type": issue_type,
                    "message": match.message,
                    "text": matched_text,
                    "suggestions": replacements[:3],
                    "context": context,
                    "rule_id": rule_id,
                    "priority": priority,
                    "offset": int(offset) if isinstance(offset, int) and offset >= 0 else None,
                })

            issues.sort(key=lambda x: x.get("priority", 1))
            return {
                "issues_found": len(matches),
                "issues_returned": len(issues),
                "issues": [{k: v for k, v in issue.items() if k != "priority"} for issue in issues],
            }, None

        # Primary pass on the full text
        primary_limit = min(300, max(80, word_count // 10 if word_count else 80))
        base_result, base_error = _run_check(text, limit=primary_limit)
        if base_error:
            return None, base_error
        if not base_result:
            return {
                "issues_found": 0,
                "issues_returned": 0,
                "issues": [],
                "word_count": word_count,
                "language_passes": 0,
            }, None

        merged_issues: list[Dict[str, Any]] = []
        seen: set[tuple] = set()

        def add_issue(issue: Dict[str, Any]) -> None:
            fingerprint = (
                str(issue.get("type", "")).lower(),
                str(issue.get("text", "")).strip().lower(),
                str(issue.get("message", "")).strip().lower(),
            )
            if fingerprint in seen:
                return
            seen.add(fingerprint)
            merged_issues.append(issue)

        for issue in base_result.get("issues", []):
            add_issue(issue)

        # Additional chunk-based passes for longer pages
        language_passes = 1
        if word_count > 900:
            chunk_size = 3500 if word_count <= 3000 else 2800
            max_chunks = min(10, max(3, word_count // 700))
            per_chunk_limit = 35 if word_count <= 5000 else 45
            chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

            for chunk in chunks[:max_chunks]:
                chunk_result, chunk_error = _run_check(chunk, limit=per_chunk_limit)
                if chunk_error or not chunk_result:
                    continue
                language_passes += 1
                for issue in chunk_result.get("issues", []):
                    add_issue(issue)

        return {
            "issues_found": max(base_result.get("issues_found", 0), len(merged_issues)),
            "issues_returned": len(merged_issues),
            "issues": merged_issues,
            "word_count": word_count,
            "language_passes": language_passes,
        }, None
