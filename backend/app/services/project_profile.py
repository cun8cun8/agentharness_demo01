"""Bounded, read-only repository manifest diagnostics. Never returns manifest contents."""
import json
import tomllib
from pathlib import Path

MANIFESTS = ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pytest.ini", "uv.lock", "verify.py")

def inspect_project(path):
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError("REPOSITORY_NOT_AVAILABLE")
    present = []
    warnings = []
    for name in MANIFESTS:
        candidate = root / name
        if candidate.exists() and candidate.resolve().is_relative_to(root) and candidate.is_file():
            present.append(name)
    languages, recipes = [], []
    python = any(x in present for x in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "pytest.ini", "uv.lock")) or any(root.glob("*.py"))
    if python:
        languages.append("python")
        setup = []
        if "requirements.txt" in present:
            setup = ["python -m pip install --target .researchforge/python --no-index --find-links wheelhouse -r requirements.txt"]
        recipes.append({"language": "python", "test_command": "python verify.py" if "verify.py" in present else "python -m pytest -q", "setup_commands": setup, "network_required": False, "preparation": "Supply pinned dependencies in wheelhouse for offline installation." if setup else "Dependencies must be available in the sandbox image."})
        if "pyproject.toml" in present:
            try:
                f = root / "pyproject.toml"
                if f.stat().st_size > 262144: raise ValueError("manifest too large")
                data = tomllib.loads(f.read_text(encoding="utf-8-sig"))
                if data.get("project", {}).get("dependencies") and not setup:
                    warnings.append("PYTHON_DEPENDENCIES_REQUIRE_PREPARATION")
            except (ValueError, OSError): warnings.append("PYPROJECT_INVALID")
    if "package.json" in present:
        languages.append("node")
        try:
            f = root / "package.json"
            if f.stat().st_size > 262144: raise ValueError("manifest too large")
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            scripts = data.get("scripts", {})
            has_test = isinstance(scripts, dict) and isinstance(scripts.get("test"), str) and bool(scripts["test"].strip())
            locked = "package-lock.json" in present
            recipes.append({"language": "node", "test_command": "npm test" if has_test else None, "setup_commands": ["npm ci --offline --ignore-scripts --cache .npm-cache"] if locked else [], "network_required": False, "preparation": "Supply .npm-cache and a lockfile, or use an image with dependencies installed."})
            if not locked: warnings.append("NODE_LOCKFILE_REQUIRED")
            if not has_test: warnings.append("NODE_TEST_SCRIPT_REQUIRED")
        except (ValueError, OSError): warnings.append("PACKAGE_JSON_INVALID")
    if not languages: warnings.append("SUPPORTED_PROJECT_NOT_DETECTED")
    return {"status": "action_required" if warnings else "configured", "languages": languages, "manifests": present, "recipes": recipes, "warnings": warnings, "network_default": "disabled", "diagnostic_only": True}
