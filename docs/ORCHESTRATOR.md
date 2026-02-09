# Code Tumbler Orchestrator

The Orchestrator is a file-watching daemon that automatically coordinates the Architect, Engineer, and Verifier agents through iterative code refinement cycles.

## How It Works

The orchestrator watches the `projects/` directory for trigger files and executes agents in sequence:

```
User creates requirements.txt
         ↓
   ARCHITECT runs
         ↓
   Creates PLAN.md
         ↓
   ENGINEER runs (iteration 1)
         ↓
   Creates code in 03_staging/
         ↓
   VERIFIER runs
         ↓
   Creates REPORT.md with score
         ↓
   ┌─────────┴────────┐
   │                  │
Score < 8.0       Score ≥ 8.0
   │                  │
   ↓                  ↓
ENGINEER         Archive to
(iteration 2)     05_final/
   │
   └──→ (Refinement loop continues...)
```

## File System Structure

Each project follows this structure:

```
projects/
  my-project/                  # Your project name
    01_input/
      requirements.txt         # Create this to start! ← TRIGGER
    02_plan/
      PLAN.md                  # Architect's output → TRIGGER
    03_staging/                # Engineer's workspace
      src/
        main.py
      tests/
        test_main.py
      requirements.txt
      .manifest.json           # Signals completion → TRIGGER
    04_feedback/
      REPORT_iter1.md          # Verifier's output → TRIGGER
      REPORT_iter2.md
    05_final/                  # Successful builds archived here
      my-project_20260207_103045.zip
    .tumbler/                  # System state (auto-created)
      state.json               # Current iteration, status, score
      usage.json               # Token usage and costs
      logs/                    # Agent logs
```

## Running the Orchestrator

### Windows

```batch
run_orchestrator.bat
```

### Linux/Mac

```bash
python run_orchestrator.py
```

## Creating a New Project

1. **Start the orchestrator** (it will watch for changes)

2. **Create a new project directory**:
   ```
   projects/my-awesome-app/
   ```

3. **Create the requirements file**:
   ```
   projects/my-awesome-app/01_input/requirements.txt
   ```

4. **Write your requirements** in `requirements.txt`:
   ```
   Create a Python command-line application that:
   1. Takes a URL as input
   2. Fetches the web page
   3. Extracts all links from the page
   4. Saves the links to a JSON file
   5. Includes comprehensive tests using pytest
   6. Has proper error handling and logging
   ```

5. **Save the file** - The orchestrator will automatically:
   - Run the Architect to create a plan
   - Run the Engineer to generate code
   - Run the Verifier to test the code
   - Loop until quality threshold is met (score ≥ 8.0/10)
   - Archive the final code to `05_final/`

## Monitoring Progress

Watch the orchestrator console output to see:
- Which agent is currently running
- Current iteration number
- Quality scores
- Token usage and costs
- Any errors or issues

## State Files

### `.tumbler/state.json`

Tracks the current state of the project:

```json
{
  "name": "my-awesome-app",
  "status": "verifying",
  "current_phase": "verifying",
  "iteration": 2,
  "max_iterations": 10,
  "quality_threshold": 8.0,
  "start_time": "2026-02-07T10:00:00Z",
  "last_update": "2026-02-07T10:15:32Z",
  "last_score": 7.5,
  "provider": "ollama_coder",
  "model": "deepseek-coder-v2:latest"
}
```

### `.tumbler/usage.json`

Tracks token usage and costs:

```json
{
  "total_tokens": 125000,
  "total_cost": 0.0,
  "by_agent": {
    "architect": {
      "tokens": 15000,
      "cost": 0.0,
      "calls": 1
    },
    "engineer": {
      "tokens": 85000,
      "cost": 0.0,
      "calls": 2
    },
    "verifier": {
      "tokens": 25000,
      "cost": 0.0,
      "calls": 2
    }
  },
  "history": [...]
}
```

## Configuration

The orchestrator uses settings from `config.yaml`:

```yaml
# Which provider to use for each agent
agent_providers:
  architect: ollama_local       # Planning
  engineer: ollama_coder        # Code generation
  verifier: ollama_local        # Verification

# Quality settings
quality_threshold: 8.0          # Score needed to finalize (0-10)
max_iterations: 10              # Maximum refinement loops
```

## Stopping the Orchestrator

Press `Ctrl+C` to gracefully stop the daemon. In-progress projects will resume automatically when you restart.

## Troubleshooting

### "Provider health check failed"

- Check that Ollama/VLLM is running
- Verify the `base_url` in `config.yaml`
- Test connectivity: `curl http://localhost:11434/api/tags`

### "Could not determine project root"

- Ensure your project follows the directory structure
- Each project needs an `01_input/` directory

### "All JSON parsing strategies failed"

- The Engineer model may not be following JSON format
- Try a different model (e.g., DeepSeek Coder)
- Check `test_output/*/engineer_raw_output_iter*.txt` for debugging

### Project stuck in a phase

- Check the `.tumbler/state.json` file
- Look for error messages in the console
- Manually create the next trigger file if needed:
  - Touch `02_plan/PLAN.md` to trigger Engineer
  - Touch `03_staging/.manifest.json` to trigger Verifier

## Tips

### Start Small

For your first project, use simple requirements:
```
Create a Python script that prints "Hello, World!"
```

This helps verify the orchestrator is working before attempting complex projects.

### Watch the Logs

The orchestrator provides detailed logs showing:
- File system events detected
- Debouncing (waiting for files to stabilize)
- Agent execution progress
- Scores and iteration counts

### Multiple Projects

You can have multiple projects in the workspace. The orchestrator processes them independently:

```
projects/
  project-a/     # Iteration 3, score 9.0 ✓
  project-b/     # Iteration 1, score 5.0
  project-c/     # Just started
```

### Cost Control

For cloud LLM providers, set reasonable limits:
- Use cheaper models for Architect (planning is forgiving)
- Use better models for Engineer (code quality matters)
- Use fast models for Verifier (just parsing output)

### Manual Intervention

You can manually edit files at any time:
- Edit `PLAN.md` to change the architecture
- Edit code in `03_staging/` to fix issues
- Touch trigger files to re-run specific agents

## Advanced Usage

### Changing Quality Threshold

Edit the orchestrator initialization in `run_orchestrator.py`:

```python
orchestrator = Orchestrator(
    workspace_root=workspace_root,
    architect=architect,
    engineer=engineer,
    verifier=verifier,
    quality_threshold=9.5,  # Higher bar
    max_iterations=15       # More attempts
)
```

### Custom State Management

Access project state programmatically:

```python
from orchestrator import StateManager

state_mgr = StateManager(Path("projects/my-project"))
state = state_mgr.load_state()
print(f"Current score: {state['last_score']}")
```

### Background Execution

On Linux/Mac, run as a background service:

```bash
nohup python run_orchestrator.py > orchestrator.log 2>&1 &
```

## Next Steps

After the orchestrator is working:
1. ✅ Phase 3 complete - Orchestrator daemon operational
2. 🔜 Phase 4 - Build the Next.js frontend UI
3. 🔜 Phase 5 - Add PostgreSQL database integration
4. 🔜 Phase 6 - Implement cost tracking dashboard
5. 🔜 Phase 7 - Docker deployment

---

**Questions?** Check the main project README or create an issue.
