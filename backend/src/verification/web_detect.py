"""Web application detection for Active Specification Alignment.

Determines whether a project is a web application and, if so, identifies
the framework, dev server command, and expected port.  Used by the sandbox
to decide whether to run the E2E verification phase.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WebAppInfo:
    """Result of web application detection."""

    is_web_app: bool = False
    framework: Optional[str] = None       # "react", "nextjs", "vue", "angular", etc.
    dev_server_command: Optional[str] = None
    dev_server_port: int = 3000
    language: str = "javascript"          # "javascript" or "python"


# -----------------------------------------------------------------------
# JavaScript / TypeScript framework markers
# -----------------------------------------------------------------------
# Maps a package.json dependency name to (framework_label, dev_command, port).
_JS_FRAMEWORK_DEPS = {
    "next":             ("nextjs",   "npx next dev",     3000),
    "react-scripts":    ("react",    "npx react-scripts start", 3000),
    "@angular/core":    ("angular",  "npx ng serve --host 0.0.0.0", 4200),
    "vue":              ("vue",      "npx vite",         5173),
    "@vitejs/plugin-vue": ("vue",    "npx vite",         5173),
    "svelte":           ("svelte",   "npx vite",         5173),
    "@sveltejs/kit":    ("sveltekit", "npx vite dev",    5173),
    "express":          ("express",  "node index.js",    3000),
    "fastify":          ("fastify",  "node index.js",    3000),
    "koa":              ("koa",      "node index.js",    3000),
}

# -----------------------------------------------------------------------
# Python framework markers
# -----------------------------------------------------------------------
_PY_FRAMEWORK_MARKERS = {
    "flask":  ("flask",  "flask run --host=0.0.0.0", 5000),
    "django": ("django", "python manage.py runserver 0.0.0.0:8000", 8000),
    "streamlit": ("streamlit", "streamlit run app.py --server.headless true", 8501),
}


def detect_web_app(plan: str, project_path: Path) -> WebAppInfo:
    """Detect whether a project is a web application.

    Detection priority:
      1. package.json dependencies → known JS frameworks
      2. package.json scripts ("dev" / "start") → extract command + port
      3. Python requirements / imports → Flask / Django
      4. Plan text keyword analysis

    Returns WebAppInfo with is_web_app=False when not a web app.
    """
    # 1. Try JavaScript detection via package.json
    info = _detect_js_web_app(project_path)
    if info.is_web_app:
        return info

    # 2. Try Python detection via requirements.txt / source files
    info = _detect_py_web_app(project_path)
    if info.is_web_app:
        return info

    # 3. Fallback: plan text analysis
    return _detect_from_plan(plan)


# -----------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------

def _detect_js_web_app(project_path: Path) -> WebAppInfo:
    """Detect JS/TS web frameworks from package.json."""
    pkg_file = project_path / "package.json"
    if not pkg_file.exists():
        return WebAppInfo()

    try:
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to parse package.json: %s", exc)
        return WebAppInfo()

    # Collect all declared dependencies
    all_deps: set = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        all_deps.update(pkg.get(key, {}).keys())

    # Check for known frameworks (order matters — more specific first)
    for dep, (framework, cmd, port) in _JS_FRAMEWORK_DEPS.items():
        if dep in all_deps:
            # Prefer the package.json "dev" script if it exists
            scripts = pkg.get("scripts", {})
            dev_cmd = None
            if "dev" in scripts:
                dev_cmd = "npm run dev"
            elif "start" in scripts:
                dev_cmd = "npm start"
            else:
                dev_cmd = cmd

            # Try to detect port from scripts
            detected_port = _extract_port_from_scripts(scripts) or port

            logger.info("Web app detected: %s (dep: %s, port: %d)", framework, dep, detected_port)
            return WebAppInfo(
                is_web_app=True,
                framework=framework,
                dev_server_command=dev_cmd,
                dev_server_port=detected_port,
                language="javascript",
            )

    # Check if package.json has a "dev" or "start" script that looks web-server-ish
    scripts = pkg.get("scripts", {})
    for script_key in ("dev", "start"):
        script_val = scripts.get(script_key, "")
        if any(kw in script_val.lower() for kw in ("serve", "server", "vite", "next", "webpack", "react-scripts")):
            detected_port = _extract_port_from_scripts(scripts) or 3000
            logger.info("Web app detected from script '%s': %s", script_key, script_val)
            return WebAppInfo(
                is_web_app=True,
                framework="unknown-js",
                dev_server_command=f"npm run {script_key}",
                dev_server_port=detected_port,
                language="javascript",
            )

    return WebAppInfo()


def _detect_py_web_app(project_path: Path) -> WebAppInfo:
    """Detect Python web frameworks from requirements or source files."""
    # Check requirements.txt
    req_file = project_path / "requirements.txt"
    req_text = ""
    if req_file.exists():
        try:
            req_text = req_file.read_text(encoding="utf-8").lower()
        except OSError:
            pass

    for marker, (framework, cmd, port) in _PY_FRAMEWORK_MARKERS.items():
        if marker in req_text:
            logger.info("Web app detected: %s (from requirements.txt)", framework)
            return WebAppInfo(
                is_web_app=True,
                framework=framework,
                dev_server_command=cmd,
                dev_server_port=port,
                language="python",
            )

    # Check pyproject.toml
    pyproj = project_path / "pyproject.toml"
    if pyproj.exists():
        try:
            toml_text = pyproj.read_text(encoding="utf-8").lower()
            for marker, (framework, cmd, port) in _PY_FRAMEWORK_MARKERS.items():
                if marker in toml_text:
                    logger.info("Web app detected: %s (from pyproject.toml)", framework)
                    return WebAppInfo(
                        is_web_app=True,
                        framework=framework,
                        dev_server_command=cmd,
                        dev_server_port=port,
                        language="python",
                    )
        except OSError:
            pass

    # Scan Python source files for framework imports (shallow — top 5 .py files)
    py_files = sorted(project_path.rglob("*.py"))[:5]
    for pf in py_files:
        try:
            src = pf.read_text(encoding="utf-8", errors="ignore")[:2000]
        except OSError:
            continue
        if re.search(r"from\s+flask\s+import|import\s+flask", src):
            logger.info("Web app detected: flask (from source import in %s)", pf.name)
            return WebAppInfo(is_web_app=True, framework="flask",
                              dev_server_command="flask run --host=0.0.0.0",
                              dev_server_port=5000, language="python")
        if re.search(r"from\s+django|import\s+django", src):
            logger.info("Web app detected: django (from source import in %s)", pf.name)
            return WebAppInfo(is_web_app=True, framework="django",
                              dev_server_command="python manage.py runserver 0.0.0.0:8000",
                              dev_server_port=8000, language="python")

    return WebAppInfo()


def _detect_from_plan(plan: str) -> WebAppInfo:
    """Last-resort detection from plan text keywords."""
    plan_lower = plan.lower()

    # JS/TS web frameworks
    js_signals = [
        ("next.js", "nextjs", "npx next dev", 3000),
        ("react app", "react", "npm start", 3000),
        ("vue.js", "vue", "npm run dev", 5173),
        ("angular", "angular", "npx ng serve --host 0.0.0.0", 4200),
        ("svelte", "svelte", "npm run dev", 5173),
        ("express server", "express", "node index.js", 3000),
    ]
    for keyword, framework, cmd, port in js_signals:
        if keyword in plan_lower:
            logger.info("Web app detected from plan text: %s", framework)
            return WebAppInfo(is_web_app=True, framework=framework,
                              dev_server_command=cmd, dev_server_port=port,
                              language="javascript")

    # Python web frameworks
    py_signals = [
        ("flask", "flask", "flask run --host=0.0.0.0", 5000),
        ("django", "django", "python manage.py runserver 0.0.0.0:8000", 8000),
    ]
    for keyword, framework, cmd, port in py_signals:
        if keyword in plan_lower and any(w in plan_lower for w in ("web", "api", "server", "endpoint", "route")):
            logger.info("Web app detected from plan text: %s", framework)
            return WebAppInfo(is_web_app=True, framework=framework,
                              dev_server_command=cmd, dev_server_port=port,
                              language="python")

    return WebAppInfo()


def _extract_port_from_scripts(scripts: dict) -> Optional[int]:
    """Try to extract a port number from package.json scripts."""
    for key in ("dev", "start"):
        val = scripts.get(key, "")
        # Match patterns like --port 3001, -p 8080, PORT=4000
        m = re.search(r"(?:--port|(?:^|\s)-p)\s+(\d{4,5})", val)
        if m:
            return int(m.group(1))
        m = re.search(r"PORT=(\d{4,5})", val)
        if m:
            return int(m.group(1))
    return None
