import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Optional, Tuple

import config

logger = logging.getLogger(__name__)

class ReviewActions:
    """Review orchestration, checks, and evaluation logic."""


    @staticmethod
    def _calculate_flesch_reading_ease(text: str) -> float:
        """Calculate Flesch Reading Ease score. Higher = easier to read (0-100+)."""
        sentences = len([s for s in text.split('.') if s.strip()])
        if sentences == 0:
            return 0.0
        
        words = text.split()
        word_count = len(words)
        if word_count == 0:
            return 0.0
        
        def count_syllables(word):
            word = word.lower()
            vowels = 'aeiouy'
            syllable_count = 0
            previous_was_vowel = False
            for char in word:
                is_vowel = char in vowels
                if is_vowel and not previous_was_vowel:
                    syllable_count += 1
                previous_was_vowel = is_vowel
            if word.endswith('e'):
                syllable_count -= 1
            if word.endswith('le') and len(word) > 2 and word[-3] not in vowels:
                syllable_count += 1
            return max(1, syllable_count)
        
        syllable_count = sum(count_syllables(w) for w in words)
        fre = 206.835 - 1.015 * (word_count / sentences) - 84.6 * (syllable_count / word_count)
        return max(0, min(100, fre))

    @staticmethod
    def _calculate_flesch_kincaid_grade(text: str) -> float:
        """Calculate Flesch-Kincaid Grade Level (US grade)."""
        sentences = len([s for s in text.split('.') if s.strip()])
        if sentences == 0:
            return 0.0
        
        words = text.split()
        word_count = len(words)
        if word_count == 0:
            return 0.0
        
        def count_syllables(word):
            word = word.lower()
            vowels = 'aeiouy'
            syllable_count = 0
            previous_was_vowel = False
            for char in word:
                is_vowel = char in vowels
                if is_vowel and not previous_was_vowel:
                    syllable_count += 1
                previous_was_vowel = is_vowel
            if word.endswith('e'):
                syllable_count -= 1
            if word.endswith('le') and len(word) > 2 and word[-3] not in vowels:
                syllable_count += 1
            return max(1, syllable_count)
        
        syllable_count = sum(count_syllables(w) for w in words)
        grade = 0.39 * (word_count / sentences) + 11.8 * (syllable_count / word_count) - 15.59
        return max(0, grade)


    def __init__(self, confluence_api, syntax_actions):
        """Store references to API and syntax modules."""
        self.api = confluence_api
        self.syntax = syntax_actions

    def _is_env_enabled(self, env_name: str) -> bool:
        """Check whether an environment flag is enabled using common truthy values."""
        value = os.environ.get(env_name, "")
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    # Severity mapping: each issue type -> default severity
    _SEVERITY_MAP = {
        "misspelling": "error",
        "grammar": "error",
        "statistics_validation": "error",
        "table_validation": "error",
        "citation": "warning",
        "context_noise": "warning",
        "repeated_word": "warning",
        "long_sentence": "warning",
        "long_paragraph": "warning",
        "duplicate_content": "warning",
        "structure": "info",
        "readability": "info",
        "spelling_consistency": "info",
        "capitalization_consistency": "info",
        "metric_inconsistency": "info",
    }

    def _record_issue(
        self,
        state: Dict[str, Any],
        issue_type: str,
        message: str,
        excerpt: str = "",
        position: Optional[int] = None,
        severity: str = "",
    ) -> None:
        """Append a normalized issue entry to the in-memory review state."""
        if not severity:
            severity = self._SEVERITY_MAP.get(issue_type, "info")
        state["issues"].append({
            "type": issue_type,
            "severity": severity,
            "message": message,
            "text": excerpt,
            "position": position,
        })

    def _post_issue_inline(
        self,
        page_id: str,
        state: Dict[str, Any],
        comment: str,
        *anchors: str,
        original_position: Optional[int] = None,
    ) -> None:
        """Try to post an inline comment with candidate expansion and state tracking."""
        # If deferring, queue for later (footer posts first)
        if state.get("_defer_inline"):
            state["_deferred_inlines"].append({
                "page_id": page_id,
                "comment": comment,
                "anchors": list(anchors),
                "original_position": original_position,
            })
            return

        time.sleep(0.3)

        # Fetch page text once (cached in state to avoid repeat fetches across calls)
        page_text = state.get("_cached_page_text")
        if page_text is None:
            page_text, fetch_err = self.syntax._get_page_plain_text(page_id)
            if fetch_err:
                state["inline_failures"].append({
                    "comment": comment, "error": fetch_err, "anchors": [],
                })
                return
            state["_cached_page_text"] = page_text

        seen: set[str] = set()
        attempted: list[str] = []
        last_error = None

        def _try_anchor(option: str) -> bool:
            nonlocal last_error
            if not option or option in seen:
                return False
            seen.add(option)
            attempted.append(option)
            result, error = self.api.post_inline_comment(
                page_id=page_id,
                comment=comment,
                text_selection=option,
                original_position=original_position,
                page_text=page_text,
            )
            if result is not None and not error:
                state["comments_posted"] += 1
                logger.info("[REVIEW] Inline comment posted (anchor=%s)", option[:80])
                return True
            if error:
                last_error = error
                logger.debug("[REVIEW] Inline anchor failed: %s | error: %s", option[:60], error)
            return False

        # Phase 1: try each raw anchor as-is
        for anchor in anchors:
            exact = self.syntax._normalize_inline_text(anchor)
            if _try_anchor(exact):
                return

        # Phase 2: expand candidates with surrounding context
        for anchor in anchors:
            for option in self.syntax._build_inline_candidates(anchor):
                if _try_anchor(option):
                    return

        # All attempts failed -- record failure and footer fallback
        fallback_anchor = attempted[0] if attempted else ""
        if not fallback_anchor:
            for raw in anchors:
                normalized = self.syntax._normalize_inline_text(raw)
                if normalized:
                    fallback_anchor = normalized
                    break

        logger.warning("[REVIEW] Inline comment failed after %d attempts | comment: %s | last_error: %s", len(attempted), comment[:80], last_error or "No matching anchor")
        state["inline_failures"].append({
            "comment": comment,
            "error": last_error or "No matching anchor found",
            "anchors": attempted[:5],
        })
        preview = fallback_anchor[:120] + ("..." if len(fallback_anchor) > 120 else "")
        state["footer_fallback_comments"].append(
            f"Inline review note could not be attached. Anchor: '{preview}'. Comment: {comment}"
        )

    def _run_grammar_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Run grammar and spelling checks, then record or post the findings."""
        logger.info("[REVIEW] Running grammar check for page %s (%d chars)", page_id, len(text))
        result, error = self.syntax._collect_language_issues_adaptive(text)
        if error:
            logger.warning("[REVIEW] Grammar check error: %s", error)
            state["footer_notes"].append(error)
            return

        if not result:
            logger.info("[REVIEW] Grammar check: no issues found")
            return

        state["language_issues_found"] = int(result.get("issues_found", 0))
        logger.info("[REVIEW] Grammar check found %d issues", state["language_issues_found"])
        for issue in result.get("issues", []):
            issue_type = str(issue.get("type", "grammar") or "grammar").lower()
            if issue_type not in {"grammar", "misspelling", "typographical", "style"}:
                continue

            text_str = str(issue.get("text", "")).strip()
            message = str(issue.get("message", "Potential language issue found.")).strip()
            suggestions = issue.get("suggestions", []) or []
            if issue_type in {"misspelling", "typographical"} and text_str:
                if suggestions:
                    suggestion_text = ", ".join(f"'{s}'" for s in suggestions[:3])
                    message = (
                        f"Possible spelling mistake: '{text_str}'. "
                        f"Suggested correction: {suggestion_text}."
                    )
                else:
                    message = f"Possible spelling mistake: '{text_str}'. Please verify the spelling."
            elif suggestions:
                suggestion_text = ", ".join(f"'{s}'" for s in suggestions[:3])
                message = f"{message} Suggestion: replace with {suggestion_text}."
            context_str = str(issue.get("context", "")).strip()
            if context_str and len(context_str) >= 20:
                anchor = context_str
            elif text_str:
                anchor = self.syntax._extract_surrounding_sentence(text_str, text)
            else:
                anchor = context_str
            excerpt = text_str or context_str
            issue_offset = issue.get("offset")
            self._record_issue(state, issue_type, message, excerpt, position=issue_offset if isinstance(issue_offset, int) else None)
            if anchor:
                self._post_issue_inline(
                    page_id,
                    state,
                    message,
                    anchor,
                    excerpt,
                    original_position=issue_offset if isinstance(issue_offset, int) else None,
                )

    def _run_context_noise_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Detect suspicious tokens or malformed terms that may need manual review."""
        logger.info("[REVIEW] Running context_noise check")
        seen = set()

        # Pattern 1: Original - very long words (18+ chars) or words with embedded digits
        pattern_long = re.compile(r"\b(?:[A-Za-z]{18,}|[A-Za-z]*\d[A-Za-z]\w*)\b")

        # Pattern 2: Short garbled words - 4+ consecutive consonants (not in known patterns)
        # Catches: "ngeks", "adwasd", "kjasdkas", "iadawdasdasncluding"
        pattern_garble = re.compile(r"\b[a-zA-Z]*[bcdfghjklmnpqrstvwxyz]{4,}[a-zA-Z]*\b", re.IGNORECASE)

        # Pattern 3: Concatenated words - lowercase letter followed by uppercase mid-word
        # without a space/hyphen (e.g., "astorage", "blockbased", "objectbased")
        pattern_concat = re.compile(r"\b[a-z]+[A-Z][a-z]+\b")

        # Known technical abbreviations and terms to skip
        _skip_patterns = re.compile(
            r"^(?:https?|www|smtp|snmp|rdma|nvme|iscsi|vxlan|"
            r"strong|through|length|strength|ights|ights|ught|oughtn|ckstr)$",
            re.IGNORECASE,
        )

        # Common English words with 4+ consecutive consonants to whitelist
        _common_consonant_words = {
            "strengths", "lengths", "months", "rights", "lights", "nights",
            "flights", "weights", "heights", "insights", "highlights",
            "eighths", "twelfths", "lymph", "nymphs", "rhythm", "rhythms",
            "lengths", "amongst", "handspring", "offspring", "backstroke",
            "downstream", "upstream", "infrastructure", "instructed",
            "construct", "constructed", "abstract", "abstraction",
            "transcript", "encryption", "description", "subscription",
            "partnerships", "birthplace", "christmas", "gangster",
            "dumpster", "hamster", "monster", "minster", "workshop",
            "worldview", "strengthen", "strengthening",
        }

        def _should_skip(word: str) -> bool:
            w = word.lower()
            if w in _common_consonant_words:
                return True
            if _skip_patterns.match(w):
                return True
            if len(w) <= 3:
                return True
            return False

        def _flag(match, reason):
            word = match.group()
            key = word.lower()
            if key in seen or _should_skip(word):
                return
            seen.add(key)
            self._record_issue(
                state,
                "context_noise",
                f"Suspicious or malformed term detected: '{word}'. "
                f"Suggestion: {reason}.",
                excerpt=word,
                position=match.start(),
                severity="warning",
            )
            self._post_issue_inline(
                page_id, state,
                f"[Context Noise] Suspicious term: '{word}'. {reason}.",
                word,
                original_position=match.start(),
            )

        # Run all patterns
        for match in pattern_long.finditer(text):
            _flag(match, "verify spelling -- remove if it is a concatenation error, "
                  "or split into separate words if terms were accidentally joined")

        for match in pattern_garble.finditer(text):
            word = match.group()
            # Only flag if it's not a common English word
            if len(word) >= 4 and not _should_skip(word):
                _flag(match, "this appears to be garbled or nonsensical text -- "
                      "remove or replace with the intended word")

        for match in pattern_concat.finditer(text):
            _flag(match, "possible concatenation error -- "
                  "check if a space or hyphen is missing")

    def _run_repeated_word_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Find repeated consecutive words and flag them as readability issues."""
        logger.info("[REVIEW] Running repeated_word check")
        pattern = re.compile(r"\b([A-Za-z']{2,})\s+\1\b", re.IGNORECASE)
        seen = set()
        for match in pattern.finditer(text):
            repeated = f"{match.group(1)} {match.group(1)}"
            lowered = repeated.lower()
            if lowered in seen:
                continue
            seen.add(lowered)

            message = (
                f"Repeated consecutive word found: '{repeated}'. "
                f"Suggestion: remove one occurrence of '{match.group(1)}'."
            )
            anchor = self.syntax._extract_surrounding_sentence(repeated, text)
            self._record_issue(state, "repeated_word", message, repeated, position=match.start())
            self._post_issue_inline(page_id, state, message, anchor, repeated, original_position=match.start())
            if len(seen) >= 12:
                break

    def _run_long_sentence_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Flag long sentences that may be harder to read or review."""
        logger.info("[REVIEW] Running long_sentence check")
        sentence_pattern = re.compile(r"[^.!?\n]+[.!?]?")
        flagged = 0

        for sentence in sentence_pattern.findall(text):
            sentence = sentence.strip()

            # skip empty lines
            if not sentence:
                continue

            words = sentence.split()
            if len(words) <= 60:
                continue

            message = (
                f"Long sentence detected ({len(words)} words). "
                "Suggestion: split into two or more shorter sentences, each expressing a single idea."
            )
            self._record_issue(state, "long_sentence", message, sentence)
            self._post_issue_inline(page_id, state, message, sentence)

            flagged += 1
            if flagged >= 8:
                break

    def _run_long_paragraph_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Flag oversized paragraphs that would benefit from being split up."""
        logger.info("[REVIEW] Running long_paragraph check")
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        flagged = 0
        for paragraph in paragraphs:
            words = paragraph.split()
            if len(words) <= 200:
                continue

            excerpt = " ".join(words[:24])
            message = (
                f"Long paragraph detected ({len(words)} words). "
                "Suggestion: break this paragraph into 2-3 shorter blocks, each focused on a single point."
            )
            self._record_issue(state, "long_paragraph", message, excerpt)
            self._post_issue_inline(page_id, state, message, excerpt, paragraph[:220])
            flagged += 1
            if flagged >= 6:
                break

    def _run_structure_check(self, storage: str, state: Dict[str, Any]) -> None:
        """Inspect heading and paragraph structure for obvious organization issues."""
        logger.info("[REVIEW] Running structure check")
        headings = re.findall(r"<h[1-6][^>]*>", storage or "", flags=re.IGNORECASE)
        paragraphs = re.findall(r"<p[^>]*>", storage or "", flags=re.IGNORECASE)

        if not headings:
            self._record_issue(
                state,
                "structure",
                "No headings detected. Add section headings to improve navigation.",
                "",
            )

        if len(paragraphs) > 0 and len(headings) == 1 and len(paragraphs) > 8:
            self._record_issue(
                state,
                "structure",
                "Page appears text-heavy under a single heading; consider adding subsections.",
                "",
            )

    def _run_statistics_validation_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Validate statistical claims: percentages, counts, breakdowns, and consistency."""
        logger.info("[REVIEW] Running statistics_validation check")
        def _close_enough(a: float, b: float, tol: float = 0.5) -> bool:
            return abs(a - b) <= tol

        sentence_like = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
        flagged: set = set()
        locate_cursor = 0

        def _locate_anchor_position(excerpt: str, hinted_position: Optional[int] = None) -> Optional[int]:
            nonlocal locate_cursor
            if isinstance(hinted_position, int) and hinted_position >= 0:
                return hinted_position
            if not excerpt:
                return None
            idx = text.find(excerpt, locate_cursor)
            if idx == -1:
                idx = text.find(excerpt)
            if idx != -1:
                locate_cursor = idx + max(1, len(excerpt) // 2)
                return idx
            return None

        def _flag(message: str, excerpt: str, position: Optional[int] = None) -> None:
            key = (message, excerpt[:120].lower())
            if key in flagged:
                return
            flagged.add(key)
            anchor_position = _locate_anchor_position(excerpt, position)
            self._record_issue(state, "statistics_validation", message, excerpt, position=anchor_position)
            if excerpt:
                self._post_issue_inline(
                    page_id,
                    state,
                    message,
                    excerpt,
                    original_position=anchor_position,
                )

        # Discover the document-level total
        doc_total: int | None = None
        _total_pat = re.search(
            r"\b(?:total|n)\s*(?:of|=|:)?\s*(\d{1,6})\b[^.\n]{0,60}"
            r"(?:students?|respondents?|participants?|people|users?)\b",
            text, re.IGNORECASE,
        )
        if not _total_pat:
            _total_pat = re.search(
                r"\b(\d{1,6})\s+(?:students?|respondents?|participants?|people|users?)\s+participated\b",
                text, re.IGNORECASE,
            )
        if _total_pat:
            doc_total = int(_total_pat.group(1))

        if doc_total and doc_total > 0:
            _unit_pct = re.compile(
                r"\b(\d{1,6})\s+(?:students?|respondents?|participants?|people|users?)"
                r"[^.\n%]{0,30}\((\d{1,3}(?:\.\d+)?)\s*%\)",
                re.IGNORECASE,
            )
            for m in _unit_pct.finditer(text):
                count = int(m.group(1))
                claimed = float(m.group(2))
                computed = (count / doc_total) * 100.0
                if not _close_enough(claimed, computed, tol=0.6):
                    excerpt = self.syntax._extract_surrounding_sentence(m.group(0), text)
                    msg = (
                        f"Percentage mismatch: {count}/{doc_total} = {computed:.1f}%, "
                        f"not {claimed:.1f}% as stated. "
                        f"Suggestion: update the percentage to {computed:.1f}%."
                    )
                    _flag(msg, excerpt, position=m.start())
                if len(flagged) >= 10:
                    break

        # Check percentage group sums
        sections = re.split(r"\n\s*\n|\n(?=\d+\.\d)", text)
        section_search_start = 0
        for section in sections:
            pct_items = re.findall(r"(\d{1,3}(?:\.\d+)?)\s*%", section)
            if len(pct_items) < 2:
                continue
            pct_vals = [float(x) for x in pct_items]
            if not all(v < 95.0 for v in pct_vals):
                continue
            pct_sum = sum(pct_vals)
            if pct_sum < 98.0 or pct_sum > 102.0:
                first_line = section.strip().splitlines()[0][:150] if section.strip() else ""
                msg = (
                    f"Percentage group in this section sums to {pct_sum:.1f}%, "
                    f"expected ~100% for a mutually exclusive breakdown."
                )
                section_pos = text.find(section, section_search_start) if section else -1
                if section_pos != -1:
                    section_search_start = section_pos + max(1, len(section) // 2)
                _flag(msg, first_line, position=section_pos if section_pos != -1 else None)
            if len(flagged) >= 10:
                break

        # Per-sentence checks
        sentence_search_start = 0
        for sentence in sentence_like:
            if len(flagged) >= 15:
                break
            lower = sentence.lower()
            sentence_pos = text.find(sentence, sentence_search_start)
            if sentence_pos == -1:
                sentence_pos = text.find(sentence)
            if sentence_pos != -1:
                sentence_search_start = sentence_pos + max(1, len(sentence) // 2)

            # "X out of Y (Z%)"
            out_of_match = re.search(r"\b(\d{1,6})\s+out\s+of\s+(\d{1,6})\b", lower)
            if out_of_match:
                part = int(out_of_match.group(1))
                whole = int(out_of_match.group(2))
                perc_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", lower)
                if whole > 0 and perc_match:
                    claimed = float(perc_match.group(1))
                    computed = (part / whole) * 100.0
                    if not _close_enough(claimed, computed, tol=0.6):
                        msg = f"Percentage mismatch: {part} out of {whole} is {computed:.1f}%, not {claimed:.1f}%."
                        _flag(msg, sentence, position=sentence_pos if sentence_pos != -1 else None)

            # "X/Y (Z%)"
            frac_match = re.search(r"\b(\d{1,6})\s*/\s*(\d{1,6})\b", lower)
            if frac_match:
                part = int(frac_match.group(1))
                whole = int(frac_match.group(2))
                perc_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", lower)
                if whole > 0 and perc_match:
                    claimed = float(perc_match.group(1))
                    computed = (part / whole) * 100.0
                    if not _close_enough(claimed, computed, tol=0.6):
                        msg = f"Percentage mismatch: {part}/{whole} is {computed:.1f}%, not {claimed:.1f}%."
                        _flag(msg, sentence, position=sentence_pos if sentence_pos != -1 else None)

            # "majority" + low %
            if "majority" in lower:
                perc_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", lower)
                if perc_match:
                    value = float(perc_match.group(1))
                    if value <= 50.0:
                        msg = "Wording mismatch: 'majority' should be above 50%."
                        _flag(msg, sentence, position=sentence_pos if sentence_pos != -1 else None)

            # Percent-change vs percentage-point confusion
            trend_match = re.search(
                r"from\s+(\d{1,3}(?:\.\d+)?)%\s+to\s+(\d{1,3}(?:\.\d+)?)%[^.\n]*?(\d{1,3}(?:\.\d+)?)%\s+(increase|decrease)",
                lower,
            )
            if trend_match:
                seg_start = float(trend_match.group(1))
                seg_end   = float(trend_match.group(2))
                claimed   = float(trend_match.group(3))
                change_word = trend_match.group(4)
                if seg_start > 0:
                    relative_change = ((seg_end - seg_start) / seg_start) * 100.0
                    relative_change = abs(relative_change) if change_word == "increase" else -abs(relative_change)
                    if not _close_enough(abs(claimed), abs(relative_change), tol=1.0):
                        msg = (
                            "Possible percentage-points vs percent-change confusion: "
                            f"from {seg_start:.1f}% to {seg_end:.1f}% is {abs(relative_change):.1f}% {change_word}."
                        )
                        _flag(msg, sentence, position=sentence_pos if sentence_pos != -1 else None)

        # Fake precision detection
        for fp_match in re.finditer(r"(\d+\.\d{3,})%", text):
            fp_excerpt_start = max(0, fp_match.start() - 40)
            fp_excerpt_end = min(len(text), fp_match.end() + 40)
            fp_raw = text[fp_excerpt_start:fp_excerpt_end].strip()
            fp_excerpt = fp_raw.splitlines()[0] if fp_raw else fp_match.group(0)
            msg = (
                f"Overly precise percentage detected: '{fp_match.group(0)}'. "
                "Suggestion: round to 1-2 decimal places for readability."
            )
            _flag(msg, fp_excerpt, position=fp_match.start())
            if len(flagged) >= 15:
                break

        # Cross-line group consistency
        _line_entry = re.compile(
            r"^[^:\n]{1,60}:\s*(\d{1,6})\s+"
            r"(?:students?|respondents?|participants?|people|users?)"
            r"[^.\n%]{0,30}\((\d{1,3}(?:\.\d+)?)\s*%\)",
            re.IGNORECASE,
        )
        all_lines = text.splitlines()
        idx = 0
        while idx < len(all_lines) and len(flagged) < 15:
            group_lines: list = []
            group_pcts: list = []
            j = idx
            while j < len(all_lines):
                m = _line_entry.match(all_lines[j].strip())
                if m:
                    group_lines.append(all_lines[j].strip())
                    group_pcts.append(float(m.group(2)))
                    j += 1
                else:
                    break
            if len(group_pcts) >= 2:
                pct_sum = sum(group_pcts)
                if pct_sum < 98.0 or pct_sum > 102.0:
                    anchor = group_lines[0][:150]
                    msg = (
                        f"Cross-line percentage group sums to {pct_sum:.1f}% "
                        f"across {len(group_pcts)} rows -- "
                        "expected ~100% for a mutually exclusive breakdown. "
                        "Suggestion: verify each row's percentage against the total."
                    )
                    _flag(msg, anchor, position=text.find(anchor))
                idx = j
            else:
                idx += 1

        # Enhanced within-group count verification
        _detailed_entry = re.compile(
            r"^\s*([^:\n]{1,50}):\s*(\d{1,6})\s+"
            r"(?:students?|respondents?|participants?|people|users?)"
            r"[^.\n%]{0,30}\((\d{1,3}(?:\.\d+)?)\s*%\)",
            re.IGNORECASE,
        )
        all_lines = text.splitlines()
        idx = 0
        while idx < len(all_lines) and len(flagged) < 20:
            group_data: list = []
            j = idx
            while j < len(all_lines):
                m = _detailed_entry.match(all_lines[j].strip())
                if m:
                    label = m.group(1).strip()
                    count = int(m.group(2))
                    pct = float(m.group(3))
                    group_data.append((label, count, pct))
                    j += 1
                else:
                    break

            if len(group_data) >= 2:
                total_count = sum(c for _, c, _ in group_data)
                total_pct = sum(p for _, _, p in group_data)

                for label, count, claimed_pct in group_data:
                    computed_pct = (count / total_count * 100) if total_count > 0 else 0
                    if not _close_enough(claimed_pct, computed_pct, tol=1.0):
                        excerpt = f"{label}: {count} ({claimed_pct:.1f}%)"
                        msg = (
                            f"Count-percentage mismatch for '{label}': "
                            f"{count}/{total_count} = {computed_pct:.1f}%, not {claimed_pct:.1f}%. "
                            f"Suggestion: update to {computed_pct:.1f}% or verify the count."
                        )
                        pos = text.find(excerpt)
                        _flag(msg, excerpt, position=pos if pos != -1 else None)

                if total_pct < 98.0 or total_pct > 102.0:
                    excerpt = group_data[0][0]
                    msg = (
                        f"Group percentages sum to {total_pct:.1f}% (expected ~100%) - "
                        f"this suggests miscalculation or non-mutually exclusive categories. "
                        f"Suggestion: verify all percentages add up to 100%."
                    )
                    _flag(msg, excerpt, position=text.find(excerpt))

                idx = j
            else:
                idx += 1

    def _run_consistency_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Check for consistency issues: spelling variations, metric values, capitalization."""
        logger.info("[REVIEW] Running consistency check")
        term_patterns = {
            "Project X": [r"Project\s+X", r"ProjectX", r"Proj\s+X", r"project\s+x"],
            "Database": [r"\bDatabase\b", r"\bDB\b", r"\bdata base\b", r"\bdb\b"],
            "Configuration": [r"\bConfiguration\b", r"\bConfig\b", r"\bcfg\b", r"\bconfig\b"],
            "API": [r"\bAPI\b", r"\bapi\b", r"\bApi\b"],
        }

        for canonical_term, variations in term_patterns.items():
            found_variants = {}
            for variant_pattern in variations:
                matches = list(re.finditer(variant_pattern, text, re.IGNORECASE))
                for match in matches:
                    variant = match.group()
                    found_variants[variant] = found_variants.get(variant, 0) + 1

            if len(found_variants) > 1:
                variants_list = ", ".join(found_variants.keys())
                message = (
                    f"Inconsistent spelling: '{canonical_term}' appears as {variants_list}. "
                    f"Recommendation: standardize to one spelling."
                )
                anchor = f"{canonical_term} variants"
                self._record_issue(state, "spelling_consistency", message, anchor)

                for match in re.finditer(f"({canonical_term}|{variations[0]})", text, re.IGNORECASE):
                    anchor_text = self.syntax._extract_surrounding_sentence(match.group(), text)
                    self._post_issue_inline(page_id, state, message, anchor_text, match.group(), original_position=match.start())
                    break

        # Metric inconsistencies
        metric_pattern = r"(version|timeout|limit|max_retries|batch_size)\s*[:=]\s*([\d.]+)"
        metric_values = defaultdict(set)

        for metric_match in re.finditer(metric_pattern, text, re.IGNORECASE):
            metric_name = metric_match.group(1).lower()
            metric_value = metric_match.group(2)
            metric_values[metric_name].add(metric_value)

        for metric_name, values in metric_values.items():
            if len(values) > 1:
                values_list = ", ".join(sorted(values))
                message = (
                    f"Metric '{metric_name}' has inconsistent values: {values_list}. "
                    f"Review and standardize if needed."
                )
                self._record_issue(state, "metric_inconsistency", message, metric_name)

        # Capitalization
        key_terms = ["Feature", "Update", "Change", "Issue", "Error", "Success"]
        for term in key_terms:
            pattern = f"\b{term}\b"
            lower_pattern = term.lower()
            has_title = len(re.findall(pattern, text, re.IGNORECASE)) > 0
            has_lower = len(re.findall(f"\b{lower_pattern}\b", text, re.IGNORECASE)) > 0

            if has_title and has_lower:
                match = re.search(f"\b{term}\b", text, re.IGNORECASE)
                if match:
                    message = (
                        f"Term '{term}' uses inconsistent capitalization. "
                        f"Recommendation: standardize formatting throughout."
                    )
                    anchor = self.syntax._extract_surrounding_sentence(term, text)
                    self._record_issue(state, "capitalization_consistency", message, anchor)
                    break


    # ------------------------------------------------------------------
    # Feature 1: Citation & Reference Validation
    # ------------------------------------------------------------------
    def _run_citation_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Flag broken, placeholder, or inconsistent citations."""
        logger.info("[REVIEW] Running citation check")
        # Placeholder citations
        placeholder_pats = [
            (r"\[(?:X|x|\?|citation needed|ref|TODO)\]", "Placeholder citation found"),
            (r"\((?:Author|AUTHOR),?\s*(?:YEAR|\d{4}\??)\)", "Placeholder author-year citation"),
            (r"\[\d*\?\.?\s*\]", "Empty or partial bracket citation"),
        ]
        flagged = 0
        for pat, msg in placeholder_pats:
            for m in re.finditer(pat, text, re.IGNORECASE):
                if flagged >= 10:
                    break
                excerpt = self.syntax._extract_surrounding_sentence(m.group(0), text)
                full_msg = f"{msg}: '{m.group(0)}'. Suggestion: replace with a proper reference."
                self._record_issue(state, "citation", full_msg, excerpt, position=m.start())
                self._post_issue_inline(page_id, state, full_msg, excerpt, original_position=m.start())
                flagged += 1

        # Detect citation style inconsistency (numeric [1] vs author-year (Smith, 2020))
        numeric_refs = re.findall(r"\[\d{1,3}\]", text)
        author_year_refs = re.findall(
            r"\([A-Z][a-z]+(?:\s+(?:et\s+al\.?|&|and)\s+[A-Z][a-z]+)?,?\s*\d{4}\)",
            text,
        )
        if numeric_refs and author_year_refs:
            msg = (
                f"Mixed citation styles: {len(numeric_refs)} numeric references "
                f"and {len(author_year_refs)} author-year references found. "
                "Suggestion: standardize to one citation format."
            )
            self._record_issue(state, "citation", msg, "")

        # Check for references in text [N] with no bibliography section
        if numeric_refs and not re.search(
            r"(?:references|bibliography|works cited|sources)\b", text, re.IGNORECASE
        ):
            self._record_issue(
                state, "citation",
                f"{len(numeric_refs)} numbered citation(s) found but no References/Bibliography section detected.",
                "",
            )

    # ------------------------------------------------------------------
    # Feature 2: Readability Scoring (Flesch-Kincaid)
    # ------------------------------------------------------------------
    def _count_syllables(self, word: str) -> int:
        """Estimate syllable count for an English word."""
        word = word.lower().strip()
        if len(word) <= 2:
            return 1
        # Remove trailing silent e
        if word.endswith("e") and not word.endswith("le"):
            word = word[:-1]
        vowels = "aeiouy"
        count = 0
        prev_vowel = False
        for ch in word:
            is_vowel = ch in vowels
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel
        return max(count, 1)

    def _run_readability_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Compute Flesch-Kincaid readability and flag hard-to-read sections."""
        logger.info("[REVIEW] Running readability check")
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 3]
        if len(sentences) < 3:
            return

        words = text.split()
        total_words = len(words)
        total_sentences = len(sentences)
        total_syllables = sum(self._count_syllables(w) for w in words)

        # Flesch Reading Ease: higher = easier (60-70 is standard)
        fre = 206.835 - 1.015 * (total_words / total_sentences) - 84.6 * (total_syllables / total_words)
        fre = round(max(0.0, min(100.0, fre)), 1)

        # Flesch-Kincaid Grade Level
        fkgl = 0.39 * (total_words / total_sentences) + 11.8 * (total_syllables / total_words) - 15.59
        fkgl = round(max(0.0, fkgl), 1)

        state["readability"] = {"flesch_ease": fre, "fk_grade": fkgl}

        if fre < 30:
            level = "very difficult"
        elif fre < 50:
            level = "difficult"
        elif fre < 60:
            level = "fairly difficult"
        else:
            level = None

        if level:
            self._record_issue(
                state, "readability",
                f"Overall readability is {level} (Flesch score: {fre}, grade level: {fkgl}). "
                "Suggestion: simplify sentence structure and use shorter words where possible.",
                "",
            )

        # Per-paragraph readability: flag paragraphs much harder than the average
        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.split()) >= 20]
        flagged = 0
        for para in paragraphs:
            if flagged >= 4:
                break
            p_words = para.split()
            p_sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if len(s.split()) >= 3]
            if len(p_sents) < 1:
                continue
            p_syl = sum(self._count_syllables(w) for w in p_words)
            p_fre = 206.835 - 1.015 * (len(p_words) / max(len(p_sents), 1)) - 84.6 * (p_syl / max(len(p_words), 1))
            p_fre = max(0.0, min(100.0, p_fre))
            # Flag if paragraph is 20+ points harder than doc average
            if p_fre < fre - 20 and p_fre < 40:
                excerpt = para[:200]
                self._record_issue(
                    state, "readability",
                    f"This paragraph is significantly harder to read (Flesch: {p_fre:.0f}) "
                    f"than the document average ({fre}). Suggestion: simplify or split.",
                    excerpt,
                )
                self._post_issue_inline(
                    page_id, state,
                    f"Hard-to-read paragraph (Flesch: {p_fre:.0f} vs doc avg {fre}). Consider simplifying.",
                    excerpt, para[:80],
                    original_position=text.find(para),
                )
                flagged += 1

    # ------------------------------------------------------------------
    # Feature 3: Semantic Duplicate Detection (TF-IDF cosine, no deps)
    # ------------------------------------------------------------------
    def _run_duplicate_check(self, page_id: str, text: str, state: Dict[str, Any]) -> None:
        """Detect near-duplicate paragraphs using TF-IDF cosine similarity."""
        logger.info("[REVIEW] Running duplicate_content check")
        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.split()) >= 10]
        if len(paragraphs) < 2:
            return

        import math as _math

        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into", "through",
            "during", "before", "after", "and", "but", "or", "nor", "not", "so",
            "yet", "both", "either", "neither", "each", "every", "all", "any",
            "this", "that", "these", "those", "it", "its", "he", "she", "they",
            "them", "their", "we", "our", "you", "your", "which", "who", "whom",
        }

        def tokenize(txt):
            return [w for w in re.findall(r"[a-z]+", txt.lower()) if w not in stop_words and len(w) > 2]

        # Build document frequency
        para_tokens = [tokenize(p) for p in paragraphs]
        df = Counter()
        for tokens in para_tokens:
            for w in set(tokens):
                df[w] += 1

        n = len(paragraphs)

        def tfidf_vec(tokens):
            tf = Counter(tokens)
            vec = {}
            for w, count in tf.items():
                idf = _math.log((n + 1) / (df.get(w, 0) + 1)) + 1
                vec[w] = count * idf
            return vec

        def cosine_sim(v1, v2):
            common = set(v1) & set(v2)
            if not common:
                return 0.0
            dot = sum(v1[w] * v2[w] for w in common)
            mag1 = _math.sqrt(sum(x * x for x in v1.values()))
            mag2 = _math.sqrt(sum(x * x for x in v2.values()))
            if mag1 == 0 or mag2 == 0:
                return 0.0
            return dot / (mag1 * mag2)

        vectors = [tfidf_vec(t) for t in para_tokens]
        flagged = set()
        for i in range(len(vectors)):
            if i in flagged:
                continue
            for j in range(i + 1, len(vectors)):
                if j in flagged:
                    continue
                sim = cosine_sim(vectors[i], vectors[j])
                if sim > 0.75:
                    excerpt_i = paragraphs[i][:120]
                    excerpt_j = paragraphs[j][:120]
                    msg = (
                        f"Near-duplicate content detected (similarity: {sim:.0%}). "
                        f"Paragraph starting with \"{excerpt_j}...\" "
                        "overlaps significantly with an earlier paragraph. "
                        "Suggestion: merge or remove redundant content."
                    )
                    self._record_issue(
                        state, "duplicate_content", msg,
                        excerpt_j, position=text.find(paragraphs[j]),
                    )
                    self._post_issue_inline(
                        page_id, state, msg, excerpt_j, paragraphs[j][:80],
                        original_position=text.find(paragraphs[j]),
                    )
                    flagged.add(j)
            if len(flagged) >= 5:
                break

    # ------------------------------------------------------------------
    # Feature 4: Table/Data Validation
    # ------------------------------------------------------------------
    def _run_table_validation_check(self, page_id: str, storage: str, text: str, state: Dict[str, Any]) -> None:
        """Validate tables extracted from page storage HTML."""
        logger.info("[REVIEW] Running table_validation check")
        tables = self.syntax._extract_tables_from_storage(storage)
        if not tables:
            return

        flagged = 0
        for t_idx, table in enumerate(tables):
            if flagged >= 8:
                break

            # Skip tiny tables (likely layout, not data)
            if len(table) < 2:
                continue

            # Determine column count consistency
            col_counts = [len(row) for row in table]
            expected_cols = max(set(col_counts), key=col_counts.count)
            for r_idx, row in enumerate(table):
                if len(row) != expected_cols and flagged < 8:
                    excerpt = " | ".join(row)[:150]
                    msg = (
                        f"Table {t_idx + 1}, row {r_idx + 1}: has {len(row)} columns "
                        f"but table expects {expected_cols}. Possible missing or extra cell."
                    )
                    self._record_issue(state, "table_validation", msg, excerpt)
                    flagged += 1

            # Check for empty cells in data rows (skip header)
            for r_idx, row in enumerate(table[1:], start=2):
                for c_idx, cell in enumerate(row):
                    if not cell.strip() and flagged < 8:
                        header = table[0][c_idx] if c_idx < len(table[0]) else f"col {c_idx + 1}"
                        msg = f"Table {t_idx + 1}, row {r_idx}, column \"{header}\": empty cell detected."
                        self._record_issue(state, "table_validation", msg, header)
                        flagged += 1

            # Numeric column validation: check if a column looks numeric and verify sums
            if len(table) < 3:
                continue
            header_row = table[0]
            data_rows = table[1:]

            for c_idx in range(min(expected_cols, len(header_row))):
                col_values = []
                for row in data_rows:
                    if c_idx < len(row):
                        # Try to parse as number
                        cell = row[c_idx].replace(",", "").replace("%", "").strip()
                        try:
                            col_values.append(float(cell))
                        except ValueError:
                            col_values = []
                            break

                if len(col_values) < 2:
                    continue

                # Check percentage columns: should they sum to ~100?
                header_text = header_row[c_idx].lower()
                is_pct_col = "%" in header_row[c_idx] or "percent" in header_text or "rate" in header_text
                if is_pct_col:
                    col_sum = sum(col_values)
                    if all(0 <= v <= 100 for v in col_values) and (col_sum < 95 or col_sum > 105):
                        msg = (
                            f"Table {t_idx + 1}, column \"{header_row[c_idx]}\": "
                            f"percentages sum to {col_sum:.1f}% (expected ~100%). "
                            "Suggestion: verify values."
                        )
                        self._record_issue(state, "table_validation", msg, header_row[c_idx])
                        flagged += 1

                # Check if last row looks like a total row
                last_label = data_rows[-1][0].lower() if data_rows[-1] else ""
                if c_idx > 0 and any(kw in last_label for kw in ("total", "sum", "all")):
                    expected_sum = sum(col_values[:-1])
                    actual_total = col_values[-1]
                    if abs(expected_sum - actual_total) > 0.5:
                        msg = (
                            f"Table {t_idx + 1}, column \"{header_row[c_idx]}\": "
                            f"total row shows {actual_total} but sum of above is {expected_sum:.1f}. "
                            "Suggestion: verify the total."
                        )
                        self._record_issue(state, "table_validation", msg, header_row[c_idx])
                        flagged += 1

    def _fetch_review_checklist(self, page_id: str) -> list:
        """Fetch the review checklist from a Confluence page containing JSON."""

        _DEFAULT = [
            {"id": "grammar",              "execution_order": 1,  "enabled": True, "required_env": ""},
            {"id": "context_noise",        "execution_order": 2,  "enabled": True, "required_env": ""},
            {"id": "repeated_word",        "execution_order": 3,  "enabled": True, "required_env": ""},
            {"id": "long_sentence",        "execution_order": 4,  "enabled": True, "required_env": ""},
            {"id": "long_paragraph",       "execution_order": 5,  "enabled": True, "required_env": ""},
            {"id": "structure",            "execution_order": 6,  "enabled": True, "required_env": ""},
            {"id": "statistics_validation","execution_order": 7,  "enabled": True, "required_env": ""},
            {"id": "citation",             "execution_order": 8,  "enabled": True, "required_env": ""},
            {"id": "readability",          "execution_order": 9,  "enabled": True, "required_env": ""},
            {"id": "duplicate_content",    "execution_order": 10, "enabled": True, "required_env": ""},
            {"id": "table_validation",     "execution_order": 11, "enabled": True, "required_env": ""},
        ]

        if isinstance(page_id, str) and page_id.strip().upper() in {"__GRAMMAR_ONLY__", "GRAMMAR_ONLY"}:
            return [{"id": "grammar", "execution_order": 1, "enabled": True, "required_env": ""}]

        if not page_id:
            return _DEFAULT

        storage, error = self.api.get_page_storage(page_id)
        if error or not storage:
            return _DEFAULT

        cdata_matches = re.findall(r"<!\[CDATA\[(.*?)\]\]>", storage, re.DOTALL)
        candidates = cdata_matches + [storage]

        for candidate in candidates:
            match = re.search(r"(\[[\s\S]*\])", candidate)
            if not match:
                continue
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, list) and parsed:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                continue

        return _DEFAULT

    def _classify_document_type(self, title: str, text: str) -> str:
        """Classify the document type based on title and content."""
        combined_text = f"{title} {text}".lower()

        meeting_indicators = [
            r"meeting\s+(?:notes|minutes)", r"attendees", r"action\s+items",
            r"discussion\s+points", r"decisions?", r"next\s+(?:steps|meeting)"
        ]

        stats_indicators = [
            r"\b(metrics|kpis?|benchmarks?)\b", r"\d+\s*%",
            r"\bstatistics?\b", r"\bdata\s+analysis\b", r"\btrend\b"
        ]

        report_indicators = [
            r"\breport\b", r"executive\s+summary", r"findings?", r"conclusion",
            r"\banalysis\b"
        ]

        plan_indicators = [
            r"\b(plan|roadmap|strategy)\b", r"milestones?", r"objectives?",
            r"timeline", r"phases?", r"deliverables?"
        ]

        guideline_indicators = [
            r"\b(guidelines?|standards?|best\s+practices?)\b",
            r"procedures?", r"policies?", r"requirements?", r"rules?",
            r"\bcompliance\b", r"\bsecurity\b", r"\bprotection\b",
            r"\bencryption\b", r"\baudit\b", r"\bgovernance\b"
        ]
        def count_matches(patterns, text):
            return sum(len(re.findall(p, text, re.IGNORECASE)) for p in patterns)

        scores = {
            "meeting_notes": count_matches(meeting_indicators, combined_text),
            "statistics": count_matches(stats_indicators, combined_text),
            "report": count_matches(report_indicators, combined_text),
            "plan": count_matches(plan_indicators, combined_text),
            "guidelines": count_matches(guideline_indicators, combined_text),
        }

        doc_type = max(scores, key=lambda x: scores[x])
        return doc_type if scores[doc_type] > 0 else "generic"

    def _get_type_specific_checks(self, doc_type: str) -> list:
        """Return prioritized checks for each document type."""
        type_check_map = {
            "meeting_notes": ["grammar", "consistency_checks", "citation"],
            "statistics": ["statistics_validation", "table_validation", "consistency_checks", "long_paragraph", "grammar"],
            "report": ["grammar", "long_paragraph", "consistency_checks", "citation", "table_validation"],
            "plan": ["consistency_checks", "grammar", "long_paragraph", "table_validation"],
            "guidelines": ["grammar", "consistency_checks", "citation"],
            "generic": ["grammar", "long_paragraph", "consistency_checks", "citation"],
        }

        always_checks = ["context_noise", "repeated_word", "long_sentence", "structure", "readability", "duplicate_content"]
        selected = type_check_map.get(doc_type, type_check_map["generic"])
        for check_id in always_checks:
            if check_id not in selected:
                selected.append(check_id)
        return selected

    def _build_footer_review_comment(self, state: Dict[str, Any]) -> str:
        """Build comprehensive footer summary with readability metrics, severity breakdown, and structured analysis."""
        issue_types = Counter(issue.get("type", "unknown") for issue in state["issues"])
        total_issues = len(state["issues"])

        # Severity breakdown
        severity_counts = Counter(issue.get("severity", "info") for issue in state["issues"])
        errors = severity_counts.get("error", 0)
        warnings = severity_counts.get("warning", 0)
        infos = severity_counts.get("info", 0)

        sections = []

        # === HEADER ===
        sections.append("<h3>Review Summary</h3>")
        if "doc_type" in state:
            doc_type = state["doc_type"]
            sections.append(f"<p><strong>Document:</strong> {doc_type.replace('_', ' ').title()}</p>")

        # === MAIN FINDINGS ===
        if total_issues == 0:
            sections.append("<p><strong>Result:</strong> No issues detected. Excellent work!</p>")
        else:
            if errors > 5:
                severity = "High"
            elif errors > 0 or warnings > 5:
                severity = "Moderate"
            else:
                severity = "Low"
            sections.append(f"<p><strong>Issues Found:</strong> {total_issues} total ({severity} severity)</p>")
            
        # === SEVERITY BREAKDOWN TABLE ===
        sections.append("<p><strong>Severity Breakdown:</strong></p>")
        severity_html = "<table><tr><th>Level</th><th>Count</th></tr>"
        severity_html += f"<tr><td>Errors</td><td>{errors}</td></tr>"
        severity_html += f"<tr><td>Warnings</td><td>{warnings}</td></tr>"
        severity_html += f"<tr><td>Informational</td><td>{infos}</td></tr>"
        severity_html += "</table>"
        sections.append(severity_html)

        # === CATEGORIES/CHECKLIST TRIGGERED ===
        if issue_types:
            sections.append("<p><strong>Issue Categories:</strong></p>")
            category_items = []
            type_names = {
                "grammar": "Grammar & Spelling",
                "misspelling": "Misspelling Detection",
                "malformed_word": "Malformed Words",
                "repeated_word": "Repeated Words",
                "structure": "Page Structure",
                "heading_nesting": "Heading Nesting",
                "acronym_expansion": "Acronym Usage",
                "empty_section": "Empty Sections",
                "consistency": "Writing Consistency",
                "passive_voice": "Passive Voice",
                "list_consistency": "List Consistency",
                "statistics_validation": "Data Validation",
                "context_noise": "Suspicious Text",
                "profanity": "Profanity/Off-Topic",
                "topic_coherence": "Topic Coherence",
                "readability": "Readability Issues",
                "citation": "Citation Gaps",
                "alt_text": "Missing Alt Text",
                "fragment": "Sentence Fragments",
                "staleness": "Outdated References",
                "formatting": "Excessive Formatting",
                "long_sentence": "Long Sentences",
                "long_paragraph": "Long Paragraphs",
                "duplicate_content": "Duplicate Content",
                "table_validation": "Table Issues"
            }
            for issue_type, count in sorted(issue_types.items(), key=lambda x: x[1], reverse=True):
                type_label = type_names.get(issue_type, issue_type.replace("_", " ").title())
                category_items.append(f"{type_label} ({count})")
            
            if category_items:
                items_html = "".join(f"<li>{item}</li>" for item in category_items)
                sections.append(f"<ul>{items_html}</ul>")

        # === READABILITY METRICS ===
        page_text = state.get("text", "")
        if page_text:
            # Calculate readability metrics using static methods
            try:
                flesch_ease = self._calculate_flesch_reading_ease(page_text)
                flesch_grade = self._calculate_flesch_kincaid_grade(page_text)
                
                sections.append("<p><strong>Readability Metrics:</strong></p>")
                readability_html = "<table><tr><th>Metric</th><th>Score</th></tr>"
                readability_html += f"<tr><td>Flesch Reading Ease</td><td>{flesch_ease:.1f}</td></tr>"
                readability_html += f"<tr><td>Flesch-Kincaid Grade Level</td><td>{flesch_grade:.1f}</td></tr>"
                readability_html += "</table>"
                sections.append(readability_html)
            except Exception as e:
                logger.warning("[REVIEW] Could not import readability calculators")

        # === QUALITY ASSESSMENT ===
        sections.append("<p><strong>Overall Quality Assessment:</strong></p>")
        if total_issues == 0:
            assessment = "Excellent - This page is well-written, well-structured, and requires no changes."
        elif errors == 0 and total_issues <= 3:
            assessment = "Good - Minor issues present. The content is generally well-written with only small improvements needed."
        elif errors <= 2 and total_issues <= 10:
            assessment = "Moderate - Several issues found. Content is clear but would benefit from improvements in clarity, consistency, and technical accuracy."
        else:
            assessment = "Needs Improvement - Significant issues affecting clarity, accuracy, and professionalism. Prioritize error-level items for immediate attention."
        sections.append(f"<p>{assessment}</p>")

        # === RECOMMENDATIONS ===
        summary_parts = []
        if state.get("footer_notes"):
            summary_parts.extend(state["footer_notes"])
        
        if issue_types.get("long_sentence", 0) > 0:
            summary_parts.append(f"Break {issue_types.get('long_sentence')} long sentence(s) into shorter, clearer statements")
        if issue_types.get("long_paragraph", 0) > 0:
            summary_parts.append(f"Split {issue_types.get('long_paragraph')} long paragraph(s) for improved readability")
        if issue_types.get("repeated_word", 0) > 0:
            summary_parts.append(f"Remove {issue_types.get('repeated_word')} instance(s) of repeated consecutive words")
        if issue_types.get("grammar", 0) > 0 or issue_types.get("misspelling", 0) > 0:
            summary_parts.append("Review and correct grammar, spelling, and punctuation errors")
        if issue_types.get("context_noise", 0) > 0:
            summary_parts.append("Verify or remove suspicious/malformed words and text")
        if issue_types.get("statistics_validation", 0) > 0:
            summary_parts.append("Validate all numerical data, statistics, and calculations")
        if issue_types.get("citation", 0) > 0:
            summary_parts.append("Add or complete missing citation references and source links")
        if issue_types.get("duplicate_content", 0) > 0:
            summary_parts.append("Eliminate or consolidate duplicate content and paragraphs")
        if issue_types.get("table_validation", 0) > 0:
            summary_parts.append("Review and correct table data, totals, and formatting")
        if issue_types.get("empty_section", 0) > 0:
            summary_parts.append("Fill or remove empty sections with no content")
        if issue_types.get("profanity", 0) > 0:
            summary_parts.append("Remove unprofessional language and off-topic content")
        if issue_types.get("topic_coherence", 0) > 0:
            summary_parts.append("Ensure all content is semantically relevant to the document topic")

        if summary_parts:
            sections.append("<p><strong>Top Recommendations (Priority Order):</strong></p>")
            # Limit to top 5-7 recommendations
            priority_items = summary_parts[:7]
            items_html = "".join(f"<li>{part}</li>" for part in priority_items)
            sections.append(f"<ol>{items_html}</ol>")

        fallback_comments = state.get("footer_fallback_comments", [])
        if fallback_comments:
            sections.append("<p><strong>Additional Inline Placement Notes:</strong></p>")
            items_html = "".join(f"<li>{comment}</li>" for comment in fallback_comments[:10])
            sections.append(f"<ul>{items_html}</ul>")

        return "".join(sections)

    def advanced_confluence_page_review(self, page_id: str, checklist_page_id: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Run the full page review workflow using the configured checklist source."""
        logger.info("[REVIEW] === Starting review for page_id=%s checklist_page_id=%s ===", page_id, checklist_page_id or "(default)")
        if not page_id:
            logger.error("[REVIEW] page_id is required")
            return None, "page_id is required"

        storage, error = self.api.get_page_storage(page_id)
        if error:
            logger.error("[REVIEW] Failed to fetch page storage: %s", error)
            return None, error
        logger.info("[REVIEW] Fetched page storage (%d chars)", len(storage))

        text = self.syntax._extract_plain_text_from_storage(storage)
        if not text:
            logger.error("[REVIEW] Page content is empty after extraction")
            return None, "Page content is empty"
        logger.info("[REVIEW] Extracted plain text (%d chars)", len(text))

        state = {
            "issues": [],
            "text": text,  # Page content for readability metrics
            "comments_posted": 0,
            "footer_fallback_comments_posted": 0,
            "footer_notes": [],
            "footer_fallback_comments": [],
            "inline_failures": [],
            "language_issues_found": 0,
            "executed_checks": [],
            "skipped_checks": [],
            "_cached_page_text": text,
            "_defer_inline": True,
            "_deferred_inlines": [],
        }

        title = self.api._get_document_title(page_id) or "Untitled"
        doc_type = self._classify_document_type(title, text)
        type_specific_checks = self._get_type_specific_checks(doc_type)
        logger.info("[REVIEW] Page title=%r, doc_type=%s, applicable_checks=%s", title, doc_type, type_specific_checks)

        check_handlers = {
            "grammar": lambda: self._run_grammar_check(page_id, text, state),
            "context_noise": lambda: self._run_context_noise_check(page_id, text, state),
            "repeated_word": lambda: self._run_repeated_word_check(page_id, text, state),
            "long_sentence": lambda: self._run_long_sentence_check(page_id, text, state),
            "long_paragraph": lambda: self._run_long_paragraph_check(page_id, text, state),
            "structure": lambda: self._run_structure_check(storage, state),
            "statistics_validation": lambda: self._run_statistics_validation_check(page_id, text, state),
            "consistency_checks": lambda: self._run_consistency_check(page_id, text, state),
            "citation": lambda: self._run_citation_check(page_id, text, state),
            "readability": lambda: self._run_readability_check(page_id, text, state),
            "duplicate_content": lambda: self._run_duplicate_check(page_id, text, state),
            "table_validation": lambda: self._run_table_validation_check(page_id, storage, text, state),
        }

        _checklist_page = checklist_page_id or getattr(config, "REVIEW_CHECKLIST_PAGE_ID", "")
        checklist = self._fetch_review_checklist(_checklist_page)
        if not any(str(item.get("id", "")).strip() == "statistics_validation" for item in checklist):
            checklist.append({"id": "statistics_validation", "execution_order": 7, "enabled": True, "required_env": ""})
        ordered_checks = sorted(checklist, key=lambda item: int(item.get("execution_order", 999)))

        for check in ordered_checks:
            check_id = str(check.get("id", "")).strip()
            if not check_id:
                continue

            if not check.get("enabled", True):
                logger.info("[REVIEW] Skipping check %r (disabled)", check_id)
                state["skipped_checks"].append({"id": check_id, "reason": "disabled"})
                continue

            if check_id not in type_specific_checks:
                logger.info("[REVIEW] Skipping check %r (not relevant for %s)", check_id, doc_type)
                state["skipped_checks"].append({"id": check_id, "reason": f"not relevant for {doc_type}"})
                continue

            required_env = str(check.get("required_env", "")).strip()
            if required_env and not self._is_env_enabled(required_env):
                logger.info("[REVIEW] Skipping check %r (env %s not enabled)", check_id, required_env)
                state["skipped_checks"].append({"id": check_id, "reason": f"env {required_env} is not enabled"})
                continue

            handler = check_handlers.get(check_id)
            if handler is None:
                logger.warning("[REVIEW] Skipping check %r (no handler registered)", check_id)
                state["skipped_checks"].append({"id": check_id, "reason": "no handler registered"})
                continue

            logger.info("[REVIEW] >>> Running check: %s", check_id)
            check_start = time.time()
            handler()
            check_elapsed = time.time() - check_start
            state["executed_checks"].append(check_id)
            logger.info("[REVIEW] <<< Finished check: %s (%.1fs, issues so far: %d, inline posted: %d)", check_id, check_elapsed, len(state["issues"]), state["comments_posted"])

        # --- Phase 2: Post footer FIRST (single API call, before rate limits hit) ---
        logger.info("[REVIEW] Building footer summary (%d issues found, %d deferred inline comments)", len(state["issues"]), len(state["_deferred_inlines"]))
        footer_comment = self._build_footer_review_comment(state)
        footer_response, footer_error = None, None
        for attempt in range(3):
            delay = 2 * (attempt + 1)
            logger.info("[REVIEW] Footer post attempt %d/3 (delay=%ds)", attempt + 1, delay)
            time.sleep(delay)
            footer_response, footer_error = self.api.post_footer_comment(page_id=page_id, comment=footer_comment)
            if footer_response is not None and not footer_error:
                logger.info("[REVIEW] Footer posted successfully on attempt %d", attempt + 1)
                break
            logger.warning("[REVIEW] Footer post failed on attempt %d: %s", attempt + 1, footer_error)
        footer_posted = footer_response is not None and not footer_error

        # --- Phase 3: Now post deferred inline comments ---
        state["_defer_inline"] = False
        deferred = state.pop("_deferred_inlines", [])
        logger.info("[REVIEW] Posting %d deferred inline comments", len(deferred))
        for item in deferred:
            self._post_issue_inline(
                item["page_id"],
                state,
                item["comment"],
                *item["anchors"],
                original_position=item.get("original_position"),
            )

        if state["inline_failures"]:
            state["footer_notes"].append(f"{len(state['inline_failures'])} issue(s) could not be attached inline")

        if footer_posted and state["footer_fallback_comments"]:
            state["footer_fallback_comments_posted"] = 1

        logger.info("[REVIEW] === Review complete for page %s: %d issues, %d inline posted, %d inline failed, footer=%s ===",
                     page_id, len(state["issues"]), state["comments_posted"], len(state["inline_failures"]), "posted" if footer_posted else "FAILED")
        severity_counts = Counter(i.get("severity", "info") for i in state["issues"])
        return {
            "page_id": page_id,
            "issues_found": len(state["issues"]),
            "severity_breakdown": {
                "errors": severity_counts.get("error", 0),
                "warnings": severity_counts.get("warning", 0),
                "info": severity_counts.get("info", 0),
            },
            "readability": state.get("readability"),
            "language_issues_found": state["language_issues_found"],
            "comments_posted": state["comments_posted"],
            "footer_fallback_comments_posted": state["footer_fallback_comments_posted"],
            "inline_failures_count": len(state["inline_failures"]),
            "inline_failures": state["inline_failures"][:20],
            "footer_posted": footer_posted,
            "executed_checks": state["executed_checks"],
            "skipped_checks": state["skipped_checks"],
            "review_checklist": checklist,
            "issues": state["issues"],
            "document_type": doc_type,
            "type_specific_checks_applied": type_specific_checks,
        }, None
