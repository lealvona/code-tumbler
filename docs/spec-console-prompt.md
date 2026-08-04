# Build: LLM Spec Console + local Copilot bridge

You are building a two-part local dev tool:

1. A single-file web app (`index.html`) — a "spec console" that turns a one-line
   app idea into a structured, multi-file YAML specification suite via an LLM,
   with live streaming feedback, an editor, and export/import as JSON.
2. A standalone Node.js script (`tools/<slug>-bridge.js`) — a local OpenAI-compatible
   proxy that authenticates to GitHub Copilot and lets the console (and other local
   tools) call Copilot models without embedding secrets in the browser.

## Step 0 — Ask the user for these before writing any code

Do not invent or reuse example values as real config. Ask, then substitute
throughout:

| Placeholder | What to ask | Sensible default if the user has no preference |
| --- | --- | --- |
| `{{APP_NAME}}` | Name of this console/tool itself (becomes page title, kebab-case slug, archive format id) | none — required |
| `{{SLUG}}` | Derived kebab-case slug of `{{APP_NAME}}` (confirm or let them override) | slugify(`{{APP_NAME}}`) |
| `{{ARCHIVE_FORMAT_ID}}` | Discriminator string stamped into every exported/imported JSON archive so imports can be validated | `{{SLUG}}-spec-archive` |
| `{{TOKEN_CACHE_PATH}}` | Where the bridge caches its GitHub token on disk | `~/.{{SLUG}}-copilot-bridge.json` |
| `{{BRIDGE_LOG_ENV_VAR}}` | Env var name controlling bridge log verbosity | `{{SLUG_UPPER}}_BRIDGE_LOG_LEVEL` |
| `{{STATIC_PORT}}` | Port for the static file server serving `index.html` | `8765` |
| `{{BRIDGE_PORT}}` | Port for the local Copilot bridge's OpenAI-compatible API | `11436` |
| `{{COPILOT_TIER}}` | Which Copilot API host: individual / business / enterprise | individual → `api.githubcopilot.com` |
| Provider set | Which completion backends to support: local/OpenAI-compatible endpoint, Copilot via bridge, Copilot direct (device flow in-browser), Anthropic direct | default: all four |
| Demo domain (optional) | A throwaway example idea to sanity-check the generator during development | any; must never leak into the app's default naming/slug fallback logic |

Confirm the table back to the user before proceeding.

## Step 1 — `tools/{{SLUG}}-bridge.js`

A dependency-free Node script (only builtins: `http`, `https`, `fs`, `os`, `path`,
`crypto`) that:

- Implements **GitHub Device Flow** OAuth to obtain a GitHub token, then exchanges
  it for a short-lived Copilot bearer token. Caches the GitHub token at
  `{{TOKEN_CACHE_PATH}}` (0600 perms) and reuses it on startup if still valid,
  refreshing the bearer transparently when it expires.
- Serves an **OpenAI-compatible API** on `http://localhost:{{BRIDGE_PORT}}/v1`:
  - `GET /v1/models` — proxies Copilot's model list.
  - `POST /v1/chat/completions` — proxies to `https://{{COPILOT_TIER_HOST}}/chat/completions`.
- Sends the **full Copilot header contract** on every upstream call (not just the
  bearer):
  - `Authorization: Bearer <token>`
  - `Copilot-Integration-Id: vscode-chat`
  - `Editor-Version`, `Editor-Plugin-Version`, `User-Agent` (current, non-stale values)
  - `openai-intent: conversation-panel`
  - `x-github-api-version: 2025-04-01` (or whatever is current — confirm before hardcoding)
  - `x-request-id`: fresh UUID per request
  - `x-vscode-user-agent-library-version`
  - `X-Initiator`: `agent` if any message in the payload has role `assistant`/`tool`, else `user`
  - `Copilot-Vision-Request: true` — only when the payload contains an image content part
- **True streaming pass-through**: when the request body has `"stream": true`,
  pipe the upstream response body straight through as SSE instead of buffering
  the whole response — log a `streamed` marker distinct from buffered responses.
- **Model validation**: before sending, check the requested model id against the
  cached `/v1/models` list; if absent, fail fast locally with the list of valid ids
  instead of firing a doomed request.
- **`max_tokens` default**: if the caller omits it, fill from the selected model's
  `capabilities.limits.max_output_tokens`.
- **Leveled logger**: `{{BRIDGE_LOG_ENV_VAR}}` accepts a name or 0–5 (silent →
  verbose), default `info`. Lines look like `[HH:MM:SS] <LEVEL> <msg>`, with
  request/response pairs logged as `-> #n METHOD url model=… body=NB` /
  `<- #n STATUS NB in Nms`. On non-2xx, log the (truncated) upstream error body at
  WARN so failures are diagnosable. **Never log the token or bearer value** — only
  byte counts, timings, model names, and expiry timestamps.
- On startup, print: whether it reused a cached token, bearer-OK confirmation,
  active log level, and the listening URL — then keep running in the foreground.
- Endpoint host must be selectable per `{{COPILOT_TIER}}`:
  `api.githubcopilot.com` / `api.business.githubcopilot.com` /
  `api.enterprise.githubcopilot.com`.

This exact script's source must also be embedded verbatim as a JS template string
constant (e.g. `BRIDGE_SRC`) inside `index.html`, so the console's Admin panel can
offer a **"Download bridge"** action that writes this file out for the user to run
locally. Keep the two copies in sync — treat `tools/{{SLUG}}-bridge.js` as the
source of truth and regenerate the embedded copy from it.

## Step 2 — `index.html` (the spec console)

Single static HTML file, vanilla JS (no build step), CSS Grid app shell.

### Layout

- App shell (`.shell`) as CSS Grid: file tree/tabs on one side, editor (`.work`)
  filling the rest. **Grid items default to `min-height: auto`**, which breaks
  internal scrolling on the editor's textarea when content is long — explicitly
  set `min-height: 0` on `.work` (and any other grid child that needs to scroll
  internally) so `flex:1; min-height:0` on the inner scroll container actually works.
- A floating **Admin (⚙) panel** with:
  - Provider configuration for each enabled backend (endpoint URL / API key /
    bridge URL, per the Step-0 provider set).
  - Editable **onboarding prompts** (at minimum the "suite generation prompt"),
    persisted to `localStorage`, each with a **reset-to-default** control.
  - **Download bridge** button (serves the embedded `BRIDGE_SRC`).
- A floating **Activity console** panel:
  - Toggle via a floating button (with an unseen-event-count badge) and a
    keyboard shortcut (e.g. Ctrl+`).
  - Copy / Clear / autoscroll controls.
  - `ddlog(kind, msg, detail)` renders colour-coded, timestamped rows for kinds:
    `progress`, `action`, `request`, `response`, `ok`, `error`. Any toast shown to
    the user is mirrored into this log.
  - Instrument every provider call site (request sent, response received/failed)
    and every connectivity/health check through this logger.

### Generation flow

- User enters a free-text app idea. On submit, build a prompt instructing the
  model to:
  1. Invent a project name and derive a kebab-case slug from the idea — **never**
     reuse any demo/example name seen during development.
  2. Apply that slug consistently across the generated spec (base `meta.project`,
     file header comments, any `secret:`/keychain-style references, export/import
     filenames).
  3. Return a single JSON envelope: `{"files":[{"path":..., "content":..., "guide": {...}}]}`
     — see Step 3 for the exact registry and per-file shape.
- Request `stream:true` from the active provider and parse the **partial** JSON
  incrementally as bytes arrive:
  - An escape-aware string scanner (tracks `"..."` boundaries through the raw
    stream) locates the current `"path"` and the in-progress `"content"` value,
    reporting whether that string is still open (mid-write) or closed (done).
  - Drive two live UI elements from this: (a) a "thinking" line showing the
    current file path plus a tail snippet of its streaming content, and (b) an
    animated index of every YAML layer — rows animate in as they start, show a
    pulsing indicator while writing, and settle to a "done" state once closed.
  - Once the stream completes, flip from the generation view to the full
    editor UI populated with the finished files.
- No fake/simulated progress — every displayed row and snippet must be derived
  from bytes the model actually emitted.

### Provider completion functions

Implement one function per enabled provider from Step 0 (e.g. `chatComplete` for
local/OpenAI-compatible, `copilotComplete` for bridge + direct Copilot API,
`anthropicComplete`, and a generic `relayComplete` for any custom relay). All must:

- Accept `stream: true` and read Server-Sent-Events incrementally rather than
  blocking on one full JSON response.
- Route through the same `busyShow()`/`busyThinking()` progress UI and `ddlog()`
  instrumentation.
- Read model lists live (e.g. cache the bridge's `/v1/models` result) instead of
  hardcoding model ids in the UI's defaults.

### Naming and persistence

- `specName(fileMap)` helper: reads the generated project name back out of the
  base spec file (`meta.project`) for use in session labeling; fall back to a
  first-sentence heuristic from the user's original idea only if absent.
- `localStorage` key(s) for persisting current session state and saved onboarding
  prompt overrides — name these after `{{SLUG}}`, not any demo project.

### Export / Import

- Export produces a JSON archive:

  ```json
  {
    "format": "{{ARCHIVE_FORMAT_ID}}",
    "format_version": 1,
    "exported_at": "<ISO datetime>",
    "spec_version": "0.1.0",
    "session": { "name": "<generated project name>", "description": "<user's original idea>" },
    "files": [ { "path": "...", "content": "...", "guide": { "lede": "...", "next": ["..."], "prompts": [{"t": "...", "p": "..."}] } } ]
  }
  ```

- Import validates the `format` discriminator before loading and rejects/ignores
  unrecognized fields. This discriminator identifies the console's own interchange
  protocol — do not conflate it with the generated project's name.

## Step 3 — YAML meta-spec registry the generator must target

Every generation run must produce this fixed, ordered file set (paths are literal,
not project-specific):

- `spec/00-base.yaml` — project meta, conventions (`id_style: kebab-case`,
  required file header comment, a `cross_reference_syntax` like
  `"<file-stem>#<path>"`, a closed set of statuses e.g. `[draft, review, stable]`,
  and an explicit secrets policy such as "never store secret literals; reference
  by name only"), `shared_schemas` (the normalized data model — entities,
  a generic `data_source` shape, a `signal` shape for event-driven behavior, and a
  `view` shape for UI screens), and a `registry.files` listing every file below.
- `spec/10-product.yaml` — one-liner, problem statement, goals, non-goals,
  measurable success criteria.
- `spec/20-architecture.yaml` — components (each with an id, responsibility, and
  optional cross-reference), an ordered data-flow, cross-source linking/dedupe
  strategy, error-handling per failure condition, and a security posture section
  (least-privilege, no secret literals, no unnecessary data egress).
- `spec/30-ui.yaml` — named views (id, purpose, primary entities, key actions),
  interaction model, keyboard model, and health/status surfacing tied back to
  the architecture's error handling.
- `spec/40-interchange.yaml` — export/import formats and, per external source,
  a field-level mapping from that source's native shape to the shared normalized
  entity.
- `spec/50-agent-bootstrap.yaml` — least-privilege setup steps per integration:
  entitlement scope, a named secret reference (never a literal), a single cheap
  verification call with expected result, and a failure protocol (mark
  degraded/red, cap retries, log status only — never secrets).
- `spec/60-glitz.yaml` — motion tokens (duration/easing/meaning), choreographed
  sequences for key state transitions, optional sound cues (off by default),
  atmosphere/theme notes, celebration moments with cooldowns, and accessibility
  fallbacks (reduced motion, screen reader text, respecting system mute).
- `spec/sources/_template.yaml` — a fill-in-the-blanks template for onboarding any
  new external or internal source (id, kind, entitlement, rate limit, health
  check, mapping reference, signals, error modes).
- `spec/sources/<source-id>.yaml` — one concrete file per real source the idea
  requires, following `_template.yaml`'s shape exactly.

Every file object in the JSON envelope carries a `guide`:

```json
{
  "lede": "one sentence describing what this layer is for",
  "next": ["2-3 concrete open questions or follow-ups for this layer"],
  "prompts": [{"t": "short label", "p": "a ready-to-run follow-up prompt referencing this file"}]
}
```

## Step 4 — Verify before handing back

- `node --check` both the standalone bridge file and the embedded `BRIDGE_SRC`
  template's evaluated body.
- Start the bridge (`node tools/{{SLUG}}-bridge.js`), confirm cached-token reuse
  or a clean device-flow prompt, and confirm `GET /v1/models` returns 200.
- Serve `index.html` on `{{STATIC_PORT}}`, load it, run one real generation
  end-to-end with streaming enabled, and confirm: the live index animates per
  file, the console panel logs request/response pairs, and export produces a
  valid `{{ARCHIVE_FORMAT_ID}}` archive that re-imports cleanly.
- Confirm the YAML editor scrolls internally for a long file (grid `min-height`
  fix from Step 2).
