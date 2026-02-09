# Testing Your Providers

## Quick Start

### 1. Set up the environment

**On Windows:**
```bash
# Run the setup script
setup.bat
```

**Manual setup:**
```bash
# Create virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate  # Windows
# or
source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure your providers

Your providers are already configured in `config.yaml`:

1. **Ollama** at `http://localhost:11434`
   - Model: `your-model-name`

2. **OpenAI-compatible endpoint** (e.g., Open WebUI)
   - Model: `your-model-name`
   - API Key: Set in `.env` file

### 3. Run the tests

**Test both providers:**
```bash
python test_my_providers.py
```

**Or test using config.yaml:**
```bash
# Tests whatever is set as active_provider in config.yaml
python test_provider.py
```

## What the tests do

Each test will:
1. ✓ Check if the provider is accessible (health check)
2. ✓ List available models
3. ✓ Send a simple chat completion request
4. ✓ Test streaming completions
5. ✓ Display token usage and costs

## Troubleshooting

### "Provider not accessible"
- **Ollama**: Check that Ollama is running at `http://localhost:11434`
  - Test with: `curl http://localhost:11434/api/tags`

- **OpenAI-compatible**: Check that the endpoint is accessible
  - Test with: `curl https://your-server.example.com/v1/models -H "Authorization: Bearer $CUSTOM_API_KEY"`

### "API key is required"
Make sure your `.env` file exists and contains:
```
CUSTOM_API_KEY=your-api-key-here
```

### Import errors
Make sure you're in the activated virtual environment:
```bash
venv\Scripts\activate  # Windows
```

## Next Steps

Once tests pass:
1. Continue to Phase 2: Implement the agent framework
2. Start building the Architect, Engineer, and Verifier agents
3. Set up the file system orchestrator

## Configuration

To change the active provider, edit `config.yaml`:

```yaml
# Use Ollama
active_provider: ollama_local

# Or use the OpenAI-compatible endpoint
active_provider: openwebui_chat
```

## Environment Variables

All environment variables are loaded from `.env` file:
- `OLLAMA_BASE_URL` - Override Ollama URL
- `CUSTOM_API_KEY` - API key for your OpenAI-compatible endpoint
- `LOG_LEVEL` - Set logging level (DEBUG, INFO, WARNING, ERROR)
