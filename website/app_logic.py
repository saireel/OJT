from flask import Flask

# Delegate core runtime/agent/review functionality to their canonical modules.
from .mcp_runtime import (
    call_llm, mcp_client, TOOL_REGISTRY, normalize_user_auth, set_active_user_auth, clear_active_user_auth, get_active_user_auth
)
from .review_logic import (
    _build_checklist_from_panel, _extract_prs_from_text, _get_cached_chat_link_metadata, _get_cached_pr_checklist, _get_cached_pr_files, _make_review_coalesce_key, _run_review_with_coalescing, _try_fast_confluence_spelling_review, _extract_base_urls_from_text, _normalize_confluence_base_url, _augment_user_auth_with_detected_base_urls, _resolve_confluence_checklist_page_id
)
from .agent_engine import (
    AGENT_SYSTEM_PROMPT, _build_agent_prompt, _build_deterministic_execution_summary, format_result, run_agent
)

# Create Flask app and register routes (compatibility facade)
app = Flask(__name__)
try:
    from .routes import register_routes
except Exception:
    from routes import register_routes

register_routes(app)

# Re-export commonly used symbols for compatibility
__all__ = [
    'app', 'call_llm', 'mcp_client', 'TOOL_REGISTRY', 'normalize_user_auth', 'set_active_user_auth', 'clear_active_user_auth', 'get_active_user_auth',
    'run_agent', 'AGENT_SYSTEM_PROMPT', 'format_result',
    '_build_checklist_from_panel', '_get_cached_pr_checklist', '_get_cached_pr_files'
]
