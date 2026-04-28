"""Bootstrap to load the Flask app from website.app_logic.
This file may be executed directly for local development ("python website/app.py").
When executed as a script, ensure the repository root is on sys.path so package-relative imports succeed.
"""

try:
    # Preferred: import using package relative import when running as a package
    from .app_logic import app
except Exception:
    # Fallback: running this file as a script (no package context).
    # Insert repository root so `import website.app_logic` works and resolves relative imports.
    import os, sys
    repo_root = os.path.dirname(os.path.dirname(__file__))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from website.app_logic import app

if __name__ == "__main__":
    # Simple development server
    app.run(host="127.0.0.1", port=5000, debug=True)