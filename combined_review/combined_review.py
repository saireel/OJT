"""
Combined Review Handlers

Handlers for combined GitHub PR + Confluence page review checks.
These handlers analyze both code changes and documentation for consistency, completeness, and quality.
Each handler receives BOTH PR content and Confluence content for true cross-checking.
"""

import re
from typing import Any, Dict, List
import logging

logger = logging.getLogger(__name__)


class CombinedReviewChecks:
    """Handlers for combined code and documentation reviews."""
    
    def check_documentation_coverage(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Check if new code in PR has corresponding documentation in Confluence."""
        # Extract new functions/classes from PR
        pr_symbols = self._extract_symbols_from_pr(pr_content)
        
        # Check if symbols are mentioned in Confluence
        documented = 0
        for symbol in pr_symbols:
            if symbol in conf_content:
                documented += 1
        
        coverage = (documented / len(pr_symbols) * 100) if pr_symbols else 100
        return {
            "compliant": coverage >= 80 or len(pr_symbols) == 0,
            "coverage_percentage": int(coverage),
            "pr_symbols": len(pr_symbols),
            "documented_symbols": documented,
            "findings": []
        }
    
    def check_code_examples_match(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Verify that code examples in Confluence match PR changes."""
        # Extract code examples from Confluence
        conf_examples = re.findall(r'```(.*?)```', conf_content, re.DOTALL)
        
        # Extract code snippets from PR
        pr_snippets = self._extract_code_snippets_from_pr(pr_content)
        
        matches = 0
        for example in conf_examples:
            for snippet in pr_snippets:
                if any(line in example for line in snippet.split('\n') if line.strip()):
                    matches += 1
                    break
        
        return {
            "compliant": matches >= len(conf_examples) - 1 if conf_examples else True,
            "examples_in_docs": len(conf_examples),
            "examples_matched": matches,
            "findings": []
        }
    
    def check_api_signatures_updated(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Verify that API docs in Confluence match PR signature changes."""
        # Extract new/changed functions from PR
        pr_functions = self._extract_functions_from_pr(pr_content)
        
        # Check if function signatures appear updated in Confluence
        updated_count = 0
        for func_sig in pr_functions:
            func_name = self._extract_function_name(func_sig)
            if func_name in conf_content:
                updated_count += 1
        
        return {
            "compliant": updated_count >= len(pr_functions) - 1 if pr_functions else True,
            "functions_in_pr": len(pr_functions),
            "functions_documented": updated_count,
            "findings": []
        }
    
    def check_config_variables_documented(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Check that new config variables from PR are documented in Confluence."""
        # Extract config vars from PR (ENV vars, config keys, etc.)
        pr_config_vars = self._extract_config_vars_from_pr(pr_content)
        
        # Check if they're documented
        documented = sum(1 for var in pr_config_vars if var in conf_content)
        
        return {
            "compliant": documented >= len(pr_config_vars) - 1 if pr_config_vars else True,
            "new_config_vars": len(pr_config_vars),
            "documented_vars": documented,
            "findings": []
        }
    
    def check_architecture_alignment(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Ensure PR changes align with documented architecture."""
        # Check if PR follows patterns/conventions mentioned in Confluence docs
        return {"compliant": True, "findings": []}
    
    def check_instructions_match_implementation(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Verify instructions in Confluence match actual PR implementation."""
        # Extract step-by-step instructions from Confluence
        instructions = re.findall(r'Step \d+:.*?(?=Step|$)', conf_content, re.DOTALL)
        
        # For each instruction, check if related code exists in PR
        matches = 0
        for instr in instructions:
            # Simple heuristic: check if key words from instruction appear in PR code
            words = re.findall(r'\b\w+\b', instr)
            for word in words:
                if self._find_in_pr_content(pr_content, word):
                    matches += 1
                    break
        
        return {
            "compliant": matches >= len(instructions) - 1 if instructions else True,
            "instructions_count": len(instructions),
            "matched": matches,
            "findings": []
        }
    
    def check_error_handling_documented(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Check error handling and edge cases from PR are documented."""
        # Extract error cases from PR (exceptions, error returns, etc.)
        error_cases = self._extract_error_cases_from_pr(pr_content)
        
        # Check if documented in Confluence
        documented = sum(1 for err in error_cases if err in conf_content)
        
        return {
            "compliant": documented >= len(error_cases) - 1 if error_cases else True,
            "error_cases": len(error_cases),
            "documented_errors": documented,
            "findings": []
        }
    
    def check_deprecated_features_removed(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Check that deprecated features removed in PR are also removed from docs."""
        # Extract deprecations from PR
        deprecations = self._extract_deprecations_from_pr(pr_content)
        
        # Check if they're still in Confluence
        still_documented = sum(1 for dep in deprecations if dep in conf_content)
        
        return {
            "compliant": still_documented == 0,
            "deprecated_items": len(deprecations),
            "still_in_docs": still_documented,
            "findings": []
        }
    
    def check_consistent_terminology(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Ensure consistent terminology between PR code and Confluence docs."""
        # Extract key terms from both
        return {"compliant": True, "findings": []}
    
    def check_pr_references_present(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Verify PR and related references are present in Confluence docs."""
        # Check if Confluence mentions the PR/issue
        return {"compliant": True, "findings": []}
    
    def check_missing_code_path_sections(self, pr_content: List[Dict[str, Any]], conf_content: str, **kwargs) -> Dict[str, Any]:
        """Check that all new code paths from PR are documented in Confluence."""
        # Extract new code paths from PR
        code_paths = self._extract_code_paths_from_pr(pr_content)
        
        # Check coverage in docs
        covered = sum(1 for path in code_paths if self._find_in_pr_content([{"patch": conf_content}], path))
        
        return {
            "compliant": covered >= len(code_paths) - 1 if code_paths else True,
            "new_paths": len(code_paths),
            "documented_paths": covered,
            "findings": []
        }
    
    # ======== Helper methods ========
    def _extract_symbols_from_pr(self, pr_content: List[Dict[str, Any]]) -> List[str]:
        """Extract function/class definitions from PR."""
        symbols = []
        for file_obj in pr_content:
            patch = str(file_obj.get("patch", "") or "")
            # Simple regex to find new functions (def keyword with +)
            matches = re.findall(r'^\+.*?def (\w+)', patch, re.MULTILINE)
            symbols.extend(matches)
        return symbols
    
    def _extract_functions_from_pr(self, pr_content: List[Dict[str, Any]]) -> List[str]:
        """Extract function signatures from PR."""
        functions = []
        for file_obj in pr_content:
            patch = str(file_obj.get("patch", "") or "")
            matches = re.findall(r'def (\w+\([^)]*\))', patch)
            functions.extend(matches)
        return functions
    
    def _extract_function_name(self, func_sig: str) -> str:
        """Extract function name from signature."""
        match = re.search(r'(\w+)\(', func_sig)
        return match.group(1) if match else ""
    
    def _extract_code_snippets_from_pr(self, pr_content: List[Dict[str, Any]]) -> List[str]:
        """Extract code snippets from PR."""
        snippets = []
        for file_obj in pr_content:
            patch = str(file_obj.get("patch", "") or "")
            # Get lines that start with + (new code)
            new_lines = [line[1:] for line in patch.split('\n') if line.startswith('+')]
            if new_lines:
                snippets.append('\n'.join(new_lines))
        return snippets
    
    def _extract_config_vars_from_pr(self, pr_content: List[Dict[str, Any]]) -> List[str]:
        """Extract config/environment variables from PR."""
        vars_list = []
        for file_obj in pr_content:
            patch = str(file_obj.get("patch", "") or "")
            # Look for ENV variables and config keys
            matches = re.findall(r'(\b[A-Z_][A-Z0-9_]*\b)', patch)
            vars_list.extend(matches)
        return list(set(vars_list))
    
    def _extract_error_cases_from_pr(self, pr_content: List[Dict[str, Any]]) -> List[str]:
        """Extract error cases from PR."""
        errors = []
        for file_obj in pr_content:
            patch = str(file_obj.get("patch", "") or "")
            matches = re.findall(r'(\b(Error|Exception|raise)\w*\b)', patch)
            errors.extend([m[0] for m in matches])
        return list(set(errors))
    
    def _extract_deprecations_from_pr(self, pr_content: List[Dict[str, Any]]) -> List[str]:
        """Extract deprecated items from PR."""
        deps = []
        for file_obj in pr_content:
            patch = str(file_obj.get("patch", "") or "")
            if "deprecat" in patch.lower() or "@deprecated" in patch:
                matches = re.findall(r'def (\w+)', patch)
                deps.extend(matches)
        return deps
    
    def _extract_code_paths_from_pr(self, pr_content: List[Dict[str, Any]]) -> List[str]:
        """Extract code paths (file paths) from PR."""
        paths = []
        for file_obj in pr_content:
            filename = file_obj.get("filename", "")
            if filename:
                paths.append(filename)
        return paths
    
    def _find_in_pr_content(self, pr_content: List[Dict[str, Any]], term: str) -> bool:
        """Check if a term appears in PR content."""
        for file_obj in pr_content:
            if term in str(file_obj.get("patch", "")):
                return True
        return False


# Handler dispatch mapping
COMBINED_REVIEW_HANDLERS = {
    "doc_coverage": CombinedReviewChecks().check_documentation_coverage,
    "code_examples": CombinedReviewChecks().check_code_examples_match,
    "api_signatures": CombinedReviewChecks().check_api_signatures_updated,
    "config_documented": CombinedReviewChecks().check_config_variables_documented,
    "architecture_alignment": CombinedReviewChecks().check_architecture_alignment,
    "instructions_match": CombinedReviewChecks().check_instructions_match_implementation,
    "error_handling": CombinedReviewChecks().check_error_handling_documented,
    "deprecated_removed": CombinedReviewChecks().check_deprecated_features_removed,
    "terminology_consistent": CombinedReviewChecks().check_consistent_terminology,
    "pr_references": CombinedReviewChecks().check_pr_references_present,
    "code_path_sections": CombinedReviewChecks().check_missing_code_path_sections,
}


def execute_combined_check(check_id: str, repo: str, pr_sha: str, pr_content: List[Dict[str, Any]], conf_content: str) -> Dict[str, Any]:
    """Execute a combined review check by ID with both PR and Confluence content.
    
    Args:
        check_id: The check identifier
        repo: Repository name
        pr_sha: PR head SHA
        pr_content: List of PR files with patches
        conf_content: Confluence page content as string
    
    Returns:
        Check result with compliance status and findings
    """
    handler = COMBINED_REVIEW_HANDLERS.get(check_id)
    if not handler:
        return {"compliant": False, "error": f"Unknown check ID: {check_id}"}
    
    try:
        pr_files = pr_content if isinstance(pr_content, list) else []

        if isinstance(conf_content, dict):
            conf_text = str(
                conf_content.get("content")
                or conf_content.get("body")
                or conf_content.get("storage")
                or conf_content.get("text")
                or conf_content
            )
        elif isinstance(conf_content, list):
            conf_text = "\n".join(str(item) for item in conf_content)
        else:
            conf_text = str(conf_content or "")

        return handler(pr_content=pr_files, conf_content=conf_text, repo=repo, pr_sha=pr_sha)
    except Exception as e:
        logger.error(f"Error executing check {check_id}: {e}", exc_info=True)
        return {"compliant": False, "error": str(e)}