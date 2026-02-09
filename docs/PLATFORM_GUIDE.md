# Cross-Platform Compatibility Guide

Code Tumbler is designed to run on **Windows, Linux, and macOS** with minimal differences.

## Running on Different Platforms

### Windows

Use the `.bat` batch files:

```batch
# List available models
list_models.bat

# Run agent tests
test_agents.bat

# Start the orchestrator daemon
run_orchestrator.bat
```

Or run Python scripts directly:
```batch
python list_models.py
python test_agents.py
python run_orchestrator.py
```

### Linux / macOS

Use the `.sh` shell scripts:

```bash
# Make scripts executable (first time only)
chmod +x *.sh

# List available models
./list_models.sh

# Run agent tests
./test_agents.sh

# Start the orchestrator daemon
./run_orchestrator.sh
```

Or run Python scripts directly:
```bash
python3 list_models.py
python3 test_agents.py
python3 run_orchestrator.py
```

## Virtual Environment Setup

### Windows

```batch
# Create virtual environment
python -m venv venv

# Activate
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Linux / macOS

```bash
# Create virtual environment
python3 -m venv venv

# Activate
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Path Handling

The codebase uses Python's `pathlib.Path` for cross-platform path handling:

- **Path normalization**: Uses `Path.as_posix()` to convert paths to forward slashes on all platforms
- **Path construction**: Uses `/` operator (e.g., `path / "subdir"`) instead of string concatenation
- **Path separators**: Automatically handled by `pathlib`

### Example

```python
from pathlib import Path

# Works on Windows, Linux, and macOS
project_path = Path("projects") / "my-project" / "01_input"
requirements_file = project_path / "requirements.txt"

# Cross-platform path comparison
if '/01_input/requirements.txt' in file_path.as_posix():
    # This works on all platforms
    pass
```

## File System Watching

The orchestrator uses `watchdog` library which is cross-platform:

- **Windows**: Uses native Windows API
- **Linux**: Uses inotify
- **macOS**: Uses FSEvents

No code changes needed - it works automatically on all platforms.

## Dependencies

All dependencies in `requirements.txt` are cross-platform:

```txt
fastapi==0.109.0           # ✓ Cross-platform
uvicorn[standard]==0.27.0  # ✓ Cross-platform
pydantic==2.5.3            # ✓ Cross-platform
openai>=1.50.0             # ✓ Cross-platform
anthropic>=0.40.0          # ✓ Cross-platform
requests==2.31.0           # ✓ Cross-platform
watchdog==3.0.0            # ✓ Cross-platform (uses native APIs)
psycopg2-binary==2.9.9     # ✓ Cross-platform (binary wheels)
pyyaml==6.0.1              # ✓ Cross-platform
python-dotenv==1.0.0       # ✓ Cross-platform
structlog==24.1.0          # ✓ Cross-platform
pytest==7.4.4              # ✓ Cross-platform
```

## Configuration Files

### config.yaml

Use forward slashes or let Python handle paths:

```yaml
# ✓ Good (works on all platforms)
providers:
  ollama_local:
    base_url: http://localhost:11434
    model: llama3.1:latest

# ✗ Avoid hardcoded Windows paths
# Bad example:
#   base_url: C:\Users\name\ollama  # Windows-only

# ✓ Use relative paths or environment variables
# Good example:
#   base_url: ${OLLAMA_URL}  # Set in .env
```

### .env Files

Environment variables work the same on all platforms:

```bash
# Works on Windows, Linux, and macOS
OLLAMA_BASE_URL=http://localhost:11434
VLLM_BASE_URL=http://localhost:8000
OPENAI_API_KEY=sk-...
```

## Testing Cross-Platform Compatibility

### Path Tests

```python
from pathlib import Path

def test_path_handling():
    """Test that paths work on all platforms."""
    project_path = Path("projects") / "test-project"
    requirements = project_path / "01_input" / "requirements.txt"

    # This should work on Windows, Linux, and macOS
    assert requirements.as_posix().endswith('/01_input/requirements.txt')
```

### File System Tests

```python
def test_file_watching():
    """Test that file watching works on all platforms."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    observer = Observer()
    handler = FileSystemEventHandler()

    # Works on all platforms
    observer.schedule(handler, path=".", recursive=True)
    observer.start()
    observer.stop()
```

## Known Platform Differences

### Line Endings

- **Windows**: `\r\n` (CRLF)
- **Linux/macOS**: `\n` (LF)

**Solution**: Git should handle this automatically with `.gitattributes`:

```gitattributes
# Ensure consistent line endings
* text=auto
*.py text
*.sh text eol=lf
*.bat text eol=crlf
```

### Case Sensitivity

- **Windows**: Case-insensitive file system
- **Linux**: Case-sensitive file system
- **macOS**: Case-insensitive by default (but can be case-sensitive)

**Solution**: Use consistent casing for all file names:
```python
# ✓ Good - consistent casing
Path("projects") / "my-project" / "PLAN.md"

# ✗ Bad - mixed casing
Path("Projects") / "My-Project" / "plan.md"  # May fail on Linux
```

### Process Management

Background processes differ slightly:

**Windows**:
```batch
# Run in background (PowerShell)
Start-Process python -ArgumentList "run_orchestrator.py" -NoNewWindow

# Or use pythonw (no console window)
start pythonw run_orchestrator.py
```

**Linux/macOS**:
```bash
# Run in background
nohup python run_orchestrator.py > orchestrator.log 2>&1 &

# Or use systemd/launchd for proper daemon management
```

## Docker (Ultimate Cross-Platform Solution)

For true platform independence, use Docker:

```bash
# Works identically on Windows, Linux, and macOS
docker-compose up -d
```

This eliminates all platform differences:
- Same Python version
- Same dependencies
- Same file paths (inside container)
- Same process management

## Troubleshooting Platform-Specific Issues

### Windows: "python not found"

Use `py` launcher instead:
```batch
py -m pip install -r requirements.txt
py run_orchestrator.py
```

### Linux: Permission denied on .sh files

Make scripts executable:
```bash
chmod +x *.sh
```

### macOS: watchdog SSL certificate issues

Install certificates:
```bash
# If using Homebrew Python
/Applications/Python\ 3.11/Install\ Certificates.command

# Or install certifi
pip install --upgrade certifi
```

### All Platforms: Path encoding issues

Ensure UTF-8 encoding:
```python
# When reading/writing files
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()
```

## Summary

✅ **Fully Cross-Platform Components**:
- All Python code (uses `pathlib.Path`)
- File watching (`watchdog` library)
- Configuration files (YAML, .env)
- Database access (PostgreSQL)
- LLM provider clients

✅ **Platform-Specific (Provided)**:
- Shell scripts (`.sh` for Linux/Mac, `.bat` for Windows)
- Virtual environment activation commands

✅ **No Platform-Specific Code**:
- No `os.system()` with platform-specific commands
- No hardcoded Windows/Linux paths
- No platform-specific imports

**Result**: Code Tumbler runs identically on Windows, Linux, and macOS! 🎉

---

**Testing on Multiple Platforms**: If you develop on Windows, consider testing on Linux via WSL (Windows Subsystem for Linux) or a Docker container to ensure compatibility.
