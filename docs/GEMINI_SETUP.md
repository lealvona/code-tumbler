# Google Gemini Setup Guide

Code Tumbler now supports Google Gemini 2.0 Flash for fast, affordable code generation!

## Getting Your API Key

1. **Go to Google AI Studio**: https://aistudio.google.com/app/apikey

2. **Create an API key**:
   - Click "Get API Key"
   - Select "Create API key in new project" (or use existing project)
   - Copy your API key (starts with `AIza...`)

3. **Set the environment variable**:

   **Windows (PowerShell)**:
   ```powershell
   $env:GOOGLE_API_KEY="AIza..."
   ```

   **Windows (Command Prompt)**:
   ```batch
   set GOOGLE_API_KEY=AIza...
   ```

   **Linux/macOS**:
   ```bash
   export GOOGLE_API_KEY="AIza..."
   ```

   **Or create a .env file** (recommended):
   ```bash
   # Copy the example
   cp .env.example .env

   # Edit .env and add your key
   GOOGLE_API_KEY=AIza...
   ```

## Installing the Gemini SDK

The Google GenAI SDK (new unified SDK) is required:

```bash
# Activate your virtual environment first
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install/upgrade dependencies
pip install -r requirements.txt
```

This will install `google-genai>=1.62.0` (the new unified SDK that replaced `google-generativeai`).

## Configuration

The `config.yaml` file is already configured with two Gemini providers:

### Gemini 2.0 Flash (Recommended for Testing)

```yaml
gemini_flash:
  type: gemini
  model: gemini-2.0-flash-exp
  cost_per_1k_input_tokens: 0.00   # FREE during preview!
  cost_per_1k_output_tokens: 0.00  # FREE during preview!
  temperature: 0.7
  max_tokens: 8192
  timeout: 300
```

**Benefits**:
- ✅ **Free during preview** (as of Feb 2026)
- ✅ **Fast** - Low latency responses
- ✅ **Good code quality** - Optimized for coding tasks
- ✅ **Large context** - 8K token output

### Gemini 1.5 Pro (More Powerful)

```yaml
gemini_pro:
  type: gemini
  model: gemini-1.5-pro
  cost_per_1k_input_tokens: 0.00125  # $1.25 per 1M tokens
  cost_per_1k_output_tokens: 0.005   # $5 per 1M tokens
  temperature: 0.7
  max_tokens: 8192
  timeout: 300
```

**Use when**:
- You need higher quality code
- Complex architectural planning
- Willing to pay for better results

## Agent Configuration

The config is already set to use Gemini Flash for all agents:

```yaml
agent_providers:
  architect: gemini_flash
  engineer: gemini_flash
  verifier: gemini_flash
```

You can mix and match:

```yaml
agent_providers:
  architect: gemini_flash    # Fast, free planning
  engineer: gemini_pro       # Higher quality code
  verifier: gemini_flash     # Fast, free verification
```

## Testing the Setup

### 1. Test API Connectivity

```bash
# Windows
python -c "from google import genai; import os; client = genai.Client(api_key=os.getenv('GOOGLE_API_KEY')); models = list(client.models.list()); print('Connected! Found', len(models), 'models')"

# Linux/macOS
python3 -c "from google import genai; import os; client = genai.Client(api_key=os.getenv('GOOGLE_API_KEY')); models = list(client.models.list()); print('Connected! Found', len(models), 'models')"
```

Expected output:
```
Connected! Found 20+ models
```

### 2. List Available Models

```bash
# Windows
list_models.bat

# Linux/macOS
./list_models.sh
```

You should see Gemini models listed:
```
========================================
Provider: gemini_flash
Type: ProviderType.GEMINI
Model: gemini-2.0-flash-exp
========================================
Fetching model list...

Found 20+ models:

📝 CODE GENERATION MODELS:
  1. gemini-2.0-flash-exp
  2. gemini-1.5-pro
  3. gemini-1.5-flash
  ...
```

### 3. Run Agent Tests

```bash
# Windows
test_agents.bat

# Linux/macOS
./test_agents.sh
```

This will run the complete Architect → Engineer → Verifier cycle using Gemini.

### 4. Start the Orchestrator

```bash
# Windows
run_orchestrator.bat

# Linux/macOS
./run_orchestrator.sh
```

The example project will automatically start tumbling!

## Troubleshooting

### "API key not found"

Make sure `GOOGLE_API_KEY` is set:

```bash
# Windows (PowerShell)
echo $env:GOOGLE_API_KEY

# Linux/macOS
echo $GOOGLE_API_KEY
```

If empty, set it as shown above.

### "No module named 'google.genai'"

Install the new SDK:

```bash
pip install google-genai
```

**Note**: The old `google-generativeai` package is deprecated. Make sure you install `google-genai` (with hyphen, not underscore).

### "Permission denied" (403 error)

- Check that your API key is valid
- Ensure you've enabled the Gemini API in your Google Cloud project
- Verify your API key hasn't expired

### Rate Limits

Gemini has generous rate limits during preview:
- **Free tier**: 15 requests/minute, 1500 requests/day
- **Paid tier**: Higher limits

If you hit limits, the provider will retry with exponential backoff.

## Cost Comparison

| Provider | Model | Input (1M tokens) | Output (1M tokens) | Speed |
|----------|-------|-------------------|--------------------|-------|
| **Gemini** | 2.0 Flash | **FREE** | **FREE** | ⚡️ Fast |
| Gemini | 1.5 Pro | $1.25 | $5.00 | 🚀 Medium |
| OpenAI | GPT-4o | $2.50 | $10.00 | 🚀 Medium |
| Anthropic | Claude Sonnet | $3.00 | $15.00 | 🐌 Slow |
| Ollama | DeepSeek Coder | FREE | FREE | 🐌 Slow (local) |

**For testing**: Gemini 2.0 Flash is the best choice - it's free, fast, and good quality!

## Switching Between Providers

You can easily switch providers in `config.yaml`:

```yaml
# Use Gemini for everything (recommended for testing)
agent_providers:
  architect: gemini_flash
  engineer: gemini_flash
  verifier: gemini_flash

# Mix local and cloud
agent_providers:
  architect: gemini_flash      # Fast cloud planning
  engineer: ollama_coder       # Local code generation
  verifier: gemini_flash       # Fast cloud verification

# All cloud, high quality
agent_providers:
  architect: gemini_pro
  engineer: openai_gpt4
  verifier: anthropic_sonnet
```

## Next Steps

1. ✅ Set your `GOOGLE_API_KEY`
2. ✅ Install dependencies (`pip install -r requirements.txt`)
3. ✅ Test connectivity (`list_models.bat` or `list_models.sh`)
4. ✅ Run agent tests (`test_agents.bat` or `test_agents.sh`)
5. ✅ Start orchestrator (`run_orchestrator.bat` or `run_orchestrator.sh`)

**Ready to test!** The orchestrator will automatically process the example project at:
```
projects/example-hello-cli/01_input/requirements.txt
```

---

**Questions?** Check the [main README](README.md) or [Orchestrator README](ORCHESTRATOR_README.md).
