"""Engineer Agent - Generates code based on architectural plans.

The Engineer reads the architectural plan and generates complete, working codebases
in any programming language with all necessary files and configurations.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class EngineerParseError(ValueError):
    """Engineer output could not be parsed into a file envelope.

    Distinct from generic ValueError so the orchestrator can treat it as a
    retryable iteration failure (like DegenerateOutputError) instead of
    killing the whole run."""


class EngineerAgent(BaseAgent):
    """The Engineer Agent generates code.

    Responsibilities:
    - Read and understand the architectural plan
    - Generate all specified files with complete implementations
    - Follow language best practices
    - Include tests, configs, and documentation
    - Handle refinement iterations based on feedback

    Output: Writes multiple files to the project's 03_staging directory
    """

    # Engineer needs a large output budget to generate full codebases as JSON.
    # Capped at 16K to fit within 38K context models (leaves ~22K for input).
    default_max_tokens = 16384

    def __init__(self, provider, system_prompt_path: Path = None,
                 nothink_override: Optional[bool] = None):
        """Initialize the Engineer Agent.

        Args:
            provider: LLM provider instance
            system_prompt_path: Path to system prompt file (optional)
            nothink_override: Per-agent override for nothink (True/False/None=auto)
        """
        # Load system prompt from file or use default path
        if system_prompt_path is None:
            try:
                backend_dir = Path(__file__).parent.parent.parent
                system_prompt_path = backend_dir / "prompts" / "engineer_system.txt"
            except:
                system_prompt_path = Path("prompts/engineer_system.txt")

        system_prompt = self._load_prompt(system_prompt_path)
        super().__init__(provider, system_prompt, name="Engineer",
                         nothink_override=nothink_override)

    def _load_prompt(self, path: Path) -> str:
        """Load system prompt from file."""
        if not path.exists():
            return """You are a Senior Software Engineer.
            Generate complete, production-quality code based on architectural plans.
            Output as JSON array of files."""

        return path.read_text(encoding='utf-8')

    def _build_messages(self, context: Dict[str, Any]) -> List[Dict[str, str]]:
        """Build messages for the Engineer LLM request.

        Args:
            context: Dictionary containing:
                - plan: str - The architectural plan (PLAN.md content)
                - iteration: int - Current iteration number
                - feedback: str (optional) - Verifier feedback for refinement
                - previous_code: Dict (optional) - Previous file tree summary
                - chunk_info: Dict (optional) - Chunked generation info with
                    target_files, chunk_num, total_chunks

        Returns:
            List of messages for LLM
        """
        plan = context.get('plan', '')
        iteration = context.get('iteration', 1)
        feedback = context.get('feedback') or ''
        previous_code = context.get('previous_code') or {}
        chunk_info = context.get('chunk_info')
        # rules may come via context or be stashed on the instance by generate_code
        # (the single vs chunked paths build contexts separately).
        rules = context.get('rules') or getattr(self, '_active_rules', None)
        rubric = context.get('rubric') or getattr(self, '_active_rubric', None)

        # Build the chunk-specific task instruction suffix
        if chunk_info:
            file_list = "\n".join(f"- `{f}`" for f in chunk_info['target_files'])
            chunk_task = f"""
# Chunked Generation (Part {chunk_info['chunk_num']} of {chunk_info['total_chunks']})

Generate ONLY the following files in this request:
{file_list}

Do NOT generate files not in this list. Other files will be generated in separate requests.

Output a single JSON object with a "files" array (only the files listed above):
{{"files": [{{"path": "...", "content": "..."}}, ...]}}
"""
        else:
            chunk_task = None

        if iteration == 1:
            # First iteration - implement from scratch
            user_message = f"""<compress>
# Architectural Plan

{plan}
</compress>

# Your Task

This is **iteration 1** - implement the project from scratch according to the plan.
"""
            if chunk_task:
                user_message += chunk_task
            else:
                user_message += """
Generate ALL files specified in the plan. Each file should have:
- `path`: Relative path from project root
- `content`: Complete file content

Ensure:
1. All files from the plan are included
2. All imports are correct
3. All tests are comprehensive
4. All configuration files are complete
5. Code is production-ready

Output a single pure JSON object (no markdown fences, no prose). The top-level
value MUST be an object with one key "files" (strict JSON modes require an
object, not a bare array):
{{"files": [{{"path": "...", "content": "..."}}, ...]}}
"""
        else:
            # Refinement iteration - include actual previous code for context
            user_message = f"""<compress>
# Architectural Plan

{plan}

# Iteration {iteration} - Refinement

## Previous Implementation

"""
            if previous_code:
                # Dynamic content budget based on actual model context window
                budget = self._context_manager.calculate_budget(
                    self.provider.config, self.system_prompt, self.default_max_tokens
                )
                # Reserve ~2000 tokens for plan, feedback, and task instructions
                code_budget_tokens = max(1000, budget.content_budget - 2000)
                max_total_content = int(code_budget_tokens * 3.8)
                logger.debug(
                    f"Engineer code budget: {code_budget_tokens} tokens "
                    f"(~{max_total_content} chars) of {budget.context_length} context"
                )
                total_chars = 0
                for file_path, content in previous_code.items():
                    if content and not content.startswith('['):
                        if total_chars + len(content) < max_total_content:
                            user_message += f"### {file_path}\n```\n{content}\n```\n\n"
                            total_chars += len(content)
                        else:
                            user_message += f"- {file_path} [content omitted for size]\n"
                    else:
                        user_message += f"- {file_path} {content}\n"

            user_message += f"""
## Verifier Feedback

{feedback}
</compress>

# Your Task

Fix the issues identified in the feedback while preserving working parts of the code.
"""
            if chunk_task:
                user_message += chunk_task
            else:
                user_message += """
Focus on:
1. Fixing failing tests
2. Resolving build errors
3. Addressing linting issues

REGRESSION GUARD (critical):
- Output ONLY the files you are CHANGING or ADDING. All other files are
  preserved automatically — do NOT re-emit them.
- Never touch a file whose tests already pass unless the feedback directly
  implicates it. Rewriting working code is how scores regress.
- Keep changes minimal and targeted at the specific failures in the feedback.
- When you change a file, output its COMPLETE new content (no diffs/ellipses).

LAYOUT DISCIPLINE (critical):
- Use EXACTLY the directory layout from the plan. Never invent new top-level
  directories, and never create a second copy of a package at a different
  root (e.g. both `driftvault/` and `src/driftvault/`) — stale duplicates
  shadow imports and break every test.
- If stale/duplicate files from earlier iterations exist (see Previous
  Implementation), list them in "delete" to remove them.

Output a single pure JSON object with a "files" array containing ONLY the
changed or added files, and an optional "delete" array of stale paths to
remove:
{{"files": [{{"path": "...", "content": "..."}}, ...], "delete": ["stale/path.py", ...]}}
"""

        # Inject the spec-derived rubric just before the task instructions,
        # OUTSIDE the <compress> block — the verifier grades against EXACTLY
        # these items, so spec-mandated details (env var names, behaviors)
        # must reach the engineer verbatim, not via the plan's paraphrase.
        if rubric:
            rubric_block = (
                "\n# Specification Rubric (your code is graded against EXACTLY these items)\n\n"
                "Every item below comes from the normative spec. Use the exact "
                "names, env vars, file paths, and behaviors stated here.\n\n"
                f"```yaml\n{rubric}\n```\n"
            )
            user_message = user_message.replace(
                "\n# Your Task\n", rubric_block + "\n# Your Task\n", 1
            )

        # Inject "Rules & Lessons Learned" just before the task instructions,
        # OUTSIDE the <compress> block (normative; capped by the ledger upstream).
        if rules:
            rules_block = f"\n# Rules & Lessons Learned (must follow)\n\n{rules}\n"
            user_message = user_message.replace(
                "\n# Your Task\n", rules_block + "\n# Your Task\n", 1
            )

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]

    # -- File list extraction from plan -----------------------------------

    # Patterns to find file paths in architectural plans
    _FILE_PATH_PATTERNS = [
        # Tree-style: ├── src/main.py or └── tests/test.py
        re.compile(r'[├└│─\s]+([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)'),
        # Bullet list: - src/main.py or * src/main.py
        re.compile(r'^\s*[-*]\s+`?([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)`?', re.MULTILINE),
        # Numbered list: 1. src/main.py
        re.compile(r'^\s*\d+\.\s+`?([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)`?', re.MULTILINE),
    ]

    def _extract_planned_files(self, plan: str) -> List[str]:
        """Extract expected file paths from the plan's directory structure.

        Looks for file paths in tree diagrams, bullet lists, and numbered lists.
        Returns deduplicated list preserving order.
        """
        seen = set()
        result = []
        for pattern in self._FILE_PATH_PATTERNS:
            for match in pattern.finditer(plan):
                path = match.group(1).strip()
                # Filter out obvious non-file-paths
                if path and '.' in path and '/' in path and path not in seen:
                    seen.add(path)
                    result.append(path)
        return result

    # -- Chunk threshold estimation -----------------------------------------

    def _needs_chunking(self, planned_files: List[str], budget) -> bool:
        """Determine if chunked generation is needed.

        Returns True if the estimated output would exceed the output budget.
        """
        if not planned_files:
            return False
        # ~550 tokens per file on average (code + JSON wrapper)
        estimated_output = len(planned_files) * 550
        return estimated_output > budget.max_output_tokens

    # -- Single-request generation ------------------------------------------

    def _is_truncated_json(self, response: str) -> bool:
        """Check if the response looks like truncated JSON output."""
        cleaned = response.strip()
        # Strip markdown fences for analysis
        if cleaned.startswith('```'):
            first_nl = cleaned.find('\n')
            if first_nl != -1:
                cleaned = cleaned[first_nl + 1:]
        cleaned = cleaned.rstrip('`').strip()
        # Truncated if it starts with [ but doesn't end with ]
        if cleaned.startswith('[') and not cleaned.rstrip().endswith(']'):
            return True
        # Or starts with { and is clearly a partial object
        if cleaned.startswith('{') and not cleaned.rstrip().endswith(']'):
            return True
        return False

    def _request_completion(self, partial_response: str, **kwargs) -> str:
        """Ask the model to complete a truncated JSON response.

        Sends the partial output back as an assistant message and asks
        the model to continue from where it left off.

        Returns:
            The continuation text (to be appended to partial_response)
        """
        messages = [
            {"role": "system", "content": (
                "You were generating a JSON array of files but your output was "
                "truncated. Continue EXACTLY from where you left off. Do NOT "
                "repeat any content already generated. Output only the remaining "
                "JSON — no commentary, no markdown fences."
            )},
            {"role": "assistant", "content": partial_response[-3000:]},
            {"role": "user", "content": "Continue the JSON output from where you stopped."},
        ]

        max_tokens = kwargs.pop('max_tokens', None) or self.default_max_tokens
        temperature = kwargs.pop('temperature', None)

        # Use streaming for the completion too
        chunks = []
        stream = self.provider.stream_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        for chunk in stream:
            chunks.append(chunk)
            if self._on_chunk:
                self._on_chunk(chunk)

        return "".join(chunks)

    def _generate_single(
        self, plan, iteration, feedback, previous_code, output_dir, **kwargs
    ) -> Dict[str, str]:
        """Generate all files in a single request (existing behavior)."""
        # compression_config is handled by execute(), but we must ensure it doesn't
        # leak into _request_completion via kwargs
        compression_config = kwargs.pop('compression_config', None)
        
        context = {
            'plan': plan,
            'iteration': iteration,
            'feedback': feedback,
            'previous_code': previous_code or {},
        }

        # Save input messages for debugging
        if output_dir:
            debug_dir = output_dir.parent / ".tumbler" / "logs"
            debug_dir.mkdir(parents=True, exist_ok=True)
            # Re-inject compression_config for execute() if needed, or pass separately
            # Actually, base_agent.execute expects it in kwargs if we want compression.
            # So we pass it explicitly to execute, but NOT to _request_completion.
            
        # Force JSON mode for OpenAI/vLLM providers to prevent conversational output
        if self.provider.config.type.value in ('openai', 'vllm'):
            kwargs['response_format'] = {"type": "json_object"}

        # We need to pass compression_config to execute
        exec_kwargs = kwargs.copy()
        if compression_config:
            exec_kwargs['compression_config'] = compression_config

        response = self.execute(context, **exec_kwargs)

        if output_dir:
            debug_dir = output_dir.parent / ".tumbler" / "logs"
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f"engineer_raw_output_iter{iteration}.txt"
            debug_file.write_text(response, encoding='utf-8')

        # If the response looks truncated, request completion (up to 2 retries)
        for attempt in range(2):
            if not self._is_truncated_json(response):
                break
            logger.info(
                f"Engineer output appears truncated ({len(response)} chars), "
                f"requesting completion (attempt {attempt + 1}/2)"
            )
            continuation = self._request_completion(response, **kwargs)
            response += continuation
            if output_dir:
                debug_file = debug_dir / f"engineer_raw_output_iter{iteration}_cont{attempt + 1}.txt"
                debug_file.write_text(response, encoding='utf-8')

        try:
            return self._parse_files_json(response)
        except (json.JSONDecodeError, ValueError) as e:
            error_msg = (
                f"Failed to parse Engineer output as JSON.\n\n"
                f"Error: {e}\n\n"
                f"Response length: {len(response)} characters\n"
                f"Response preview (first 500 chars):\n{response[:500]}\n\n"
                f"Raw output saved to: {debug_dir / f'engineer_raw_output_iter{iteration}.txt' if output_dir else 'N/A'}"
            )
            raise EngineerParseError(error_msg)

    # -- Chunk generation ---------------------------------------------------

    def _generate_chunk(
        self, plan, iteration, feedback, previous_code,
        target_files, chunk_num, total_chunks, output_dir, **kwargs
    ) -> Dict[str, str]:
        """Generate a subset of files as one chunk request."""
        # compression_config handling
        compression_config = kwargs.pop('compression_config', None)

        context = {
            'plan': plan,
            'iteration': iteration,
            'feedback': feedback,
            'previous_code': {
                k: v for k, v in (previous_code or {}).items()
                if k in target_files
            },
            'chunk_info': {
                'chunk_num': chunk_num,
                'total_chunks': total_chunks,
                'target_files': target_files,
            },
        }

        # Force JSON mode for OpenAI/vLLM providers
        if self.provider.config.type.value in ('openai', 'vllm'):
            kwargs['response_format'] = {"type": "json_object"}

        # Pass compression_config to execute
        exec_kwargs = kwargs.copy()
        if compression_config:
            exec_kwargs['compression_config'] = compression_config

        response = self.execute(context, **exec_kwargs)

        # Save debug output per chunk
        if output_dir:
            debug_dir = output_dir.parent / ".tumbler" / "logs"
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f"engineer_raw_output_iter{iteration}_chunk{chunk_num}.txt"
            debug_file.write_text(response, encoding='utf-8')

        try:
            return self._parse_files_json(response)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                f"Chunk {chunk_num}/{total_chunks} failed to parse: {e}. "
                f"Target files: {target_files}"
            )
            return {}  # Return empty — partial results are better than total failure

    # -- Main entry point ---------------------------------------------------

    def generate_code(
        self,
        plan: str,
        iteration: int = 1,
        feedback: str = None,
        previous_code: Dict[str, str] = None,
        output_dir: Path = None,
        **kwargs
    ) -> Dict[str, str]:
        """Generate code based on architectural plan.

        Automatically detects when the output would exceed the model's token
        budget and splits generation into multiple concurrent chunk requests
        (up to 7 in parallel).

        Args:
            plan: The architectural plan (PLAN.md content)
            iteration: Current iteration number (1 for first attempt)
            feedback: Verifier feedback for refinement (optional)
            previous_code: Previous code files for context (optional)
            output_dir: Directory to write files to (optional)
            **kwargs: Additional LLM parameters

        Returns:
            Dictionary mapping file paths to content

        Raises:
            ValueError: If output is not valid JSON
        """
        # Stash rules/rubric for _build_messages (single & chunked paths build
        # contexts separately); pop so they never leak into provider kwargs.
        self._active_rules = kwargs.pop('rules', None)
        self._active_rubric = kwargs.pop('rubric', None)

        # Calculate budget to decide if chunking is needed
        budget = self._context_manager.calculate_budget(
            self.provider.config, self.system_prompt, self.default_max_tokens
        )
        planned_files = self._extract_planned_files(plan)

        # Refinement iterations are incremental (only changed files) — always a
        # single request. Chunking would regenerate the full tree and reintroduce
        # regression risk; it exists for the big iteration-1 build-out only.
        if iteration > 1 or not planned_files or not self._needs_chunking(planned_files, budget):
            # Single-request path (normal case)
            files = self._generate_single(
                plan, iteration, feedback, previous_code, output_dir, **kwargs
            )
        else:
            # Chunked generation path
            chunks = self._context_manager.plan_chunks(
                planned_files, budget.max_output_tokens
            )
            logger.info(
                f"Chunked generation: {len(planned_files)} files split into "
                f"{len(chunks)} chunk(s), max 7 concurrent"
            )

            all_files: Dict[str, str] = {}

            if len(chunks) == 1:
                # Only one chunk after planning — use single request
                all_files = self._generate_single(
                    plan, iteration, feedback, previous_code, output_dir, **kwargs
                )
            else:
                # Run chunks concurrently (up to 7)
                max_workers = min(7, len(chunks))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {}
                    for idx, chunk_files in enumerate(chunks):
                        future = executor.submit(
                            self._generate_chunk,
                            plan, iteration, feedback, previous_code,
                            chunk_files, idx + 1, len(chunks),
                            output_dir, **kwargs,
                        )
                        futures[future] = idx + 1

                    for future in as_completed(futures):
                        chunk_num = futures[future]
                        try:
                            chunk_result = future.result()
                            all_files.update(chunk_result)
                            logger.info(
                                f"Chunk {chunk_num}/{len(chunks)} completed: "
                                f"{len(chunk_result)} files"
                            )
                        except Exception as e:
                            logger.error(
                                f"Chunk {chunk_num}/{len(chunks)} failed: {e}"
                            )

                if not all_files:
                    raise ValueError(
                        f"Chunked generation produced no files. "
                        f"{len(chunks)} chunks were attempted."
                    )

            files = all_files

        # Syntax gate: a single broken .py file costs an entire verify round
        # ("0 tests collected"). Pure compile() check — no imports executed —
        # with ONE bounded targeted regeneration of just the broken files.
        broken = self._syntax_check_python(files)
        if broken:
            try:
                summary = "\n".join(f"- {p}: {e}" for p, e in list(broken.items())[:10])
                logger.info(f"Syntax gate: {len(broken)} broken file(s), regenerating")
                fixed = self._generate_chunk(
                    plan, iteration,
                    f"These files have Python syntax errors — regenerate them "
                    f"completely and correctly:\n{summary}",
                    files, list(broken)[:10], 1, 1, None, **kwargs,
                )
                for path, content in (fixed or {}).items():
                    if path in broken:
                        files[path] = content
                still = self._syntax_check_python(
                    {p: files[p] for p in broken if p in files})
                if still:
                    logger.warning(f"Syntax gate: {len(still)} file(s) still broken: {list(still)[:5]}")
            except Exception as e:
                logger.warning(f"Syntax-gate regeneration failed (continuing): {e}")

        # Apply requested deletions of stale files (path-contained, files only,
        # then empty parent dirs bottom-up — per the no-force-deletion policy).
        pending = getattr(self, '_pending_deletes', None)
        self._pending_deletes = None
        if pending and output_dir:
            self._apply_deletes(pending, output_dir)

        # Completeness gate: one bounded, targeted pass when the generated set is
        # missing critical files (dependency manifest, tests, or planned files).
        # Models frequently truncate/omit on large plans; a small "generate ONLY
        # these" request is far more reliable than hoping the next full iteration
        # fixes it — and cheaper than burning a verify cycle on incomplete code.
        try:
            files = self._ensure_critical_files(plan, files, iteration, feedback,
                                                existing=previous_code, **kwargs)
        except Exception as e:
            logger.warning(f"Completeness pass failed (continuing with generated set): {e}")

        # Write files if output_dir provided
        if output_dir:
            self._write_files(files, output_dir)

        return files

    # Dependency-manifest filenames per supported runtime
    _MANIFEST_NAMES = ("requirements.txt", "pyproject.toml", "package.json",
                       "go.mod", "Cargo.toml", "pom.xml")

    @staticmethod
    def _is_test_path(path: str) -> bool:
        p = path.lower()
        name = p.rsplit("/", 1)[-1]
        return (p.startswith("tests/") or "/tests/" in p or "/test/" in p
                or name.startswith("test_") or name.endswith("_test.py")
                or ".test." in name or ".spec." in name)

    def _ensure_critical_files(self, plan: str, files: Dict[str, str],
                               iteration: int, feedback: Optional[str],
                               existing: Optional[Dict[str, str]] = None,
                               **kwargs) -> Dict[str, str]:
        """One bounded completion request for critical/planned files that are
        missing from the generated set. Never overwrites files already produced.

        `existing` = files already in staging from prior iterations; refinement
        outputs are incremental, so completeness is judged on the union.
        """
        if not files:
            return files

        effective = set(files) | set(existing or {})
        planned = self._extract_planned_files(plan) or []
        missing_planned = [p for p in planned if p not in effective]
        has_manifest = any(f.rsplit("/", 1)[-1] in self._MANIFEST_NAMES for f in effective)
        has_tests = any(self._is_test_path(f) for f in effective)

        if has_manifest and has_tests and not missing_planned:
            return files  # complete — no extra call

        targets = list(missing_planned[:15])  # bounded
        if not has_manifest and not any(t.rsplit("/", 1)[-1] in self._MANIFEST_NAMES for t in targets):
            targets.append("the project's dependency manifest (e.g. requirements.txt or pyproject.toml)")
        if not has_tests and not any(self._is_test_path(t) for t in targets if "/" in t or t.endswith(".py")):
            targets.append("a tests/ suite that actually exercises the generated code")
        if not targets:
            return files

        logger.info(
            f"Completeness gate: {len(files)} files generated, requesting "
            f"{len(targets)} missing item(s): {targets[:5]}{'...' if len(targets) > 5 else ''}"
        )
        extra = self._generate_chunk(
            plan, iteration, feedback, files,   # previous_code=what exists already
            targets, 1, 1, None, **kwargs,
        )
        added = {p: c for p, c in (extra or {}).items() if p not in files}
        if added:
            logger.info(f"Completeness gate added {len(added)} file(s): {sorted(added)[:8]}")
            files = {**files, **added}
        return files

    def _parse_files_json(self, response: str) -> Dict[str, str]:
        """Parse JSON response into file dictionary with robust error handling.

        Args:
            response: JSON string from LLM (may have formatting issues)

        Returns:
            Dictionary mapping file paths to content

        Raises:
            ValueError: If JSON cannot be parsed after all attempts
        """
        import re

        # Clean up response
        response = response.strip()

        # Try to extract JSON from markdown code blocks (handles truncated output
        # where the closing ``` may be missing)
        json_block_pattern = r'```(?:json)?\s*\n(.*?)```'
        match = re.search(json_block_pattern, response, re.DOTALL)
        if match:
            response = match.group(1).strip()
        elif response.startswith('```'):
            # Truncated output — strip the opening fence without a closing one
            first_newline = response.find('\n')
            if first_newline != -1:
                response = response[first_newline + 1:].strip()

        # Strategy 1: Try parsing as-is
        # (ValueError included: a parsed-but-wrong shape should still fall through
        # to the regex strategy rather than aborting the whole parse.)
        try:
            files_array = json.loads(response)
            return self._convert_to_file_dict(files_array)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Fix common issues with Python docstrings in JSON
        # The model often writes Python's """ as \" \" \" which breaks JSON
        # We need to properly escape these
        try:
            # Fix incomplete triple-quote escaping
            fixed = response.replace('\\"\\"\\"', '\\\\"\\\\"\\\\"')  # """ -> \"\"\"
            files_array = json.loads(fixed)
            return self._convert_to_file_dict(files_array)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 3: Parse file-by-file using regex
        # This is more forgiving and can handle malformed JSON
        try:
            return self._parse_with_regex(response)
        except Exception as e:
            raise ValueError(f"All JSON parsing strategies failed. Last error: {e}")

    def _convert_to_file_dict(self, files_array: Any) -> Dict[str, str]:
        """Convert parsed JSON array to file dictionary."""
        # response_format=json_object forces an OBJECT — models then wrap the
        # array, e.g. {"files": [...]}. Unwrap before validating. An optional
        # "delete" list (stale paths to remove) is stashed for generate_code.
        if isinstance(files_array, dict):
            dels = files_array.get("delete")
            if isinstance(dels, list):
                self._pending_deletes = [d for d in dels if isinstance(d, str)]
            for key in ("files", "Files", "FILES"):
                if isinstance(files_array.get(key), list):
                    files_array = files_array[key]
                    break
            else:
                lists = [v for v in files_array.values() if isinstance(v, list)]
                if len(lists) == 1:
                    files_array = lists[0]
                else:
                    # Dialect: flat {path: content} map — same shape the
                    # specifier accepts; small models emit it reliably.
                    flat = [(k, v) for k, v in files_array.items()
                            if isinstance(k, str) and isinstance(v, str)
                            and ('/' in k or '.' in k)]
                    if flat and len(flat) >= max(1, int(0.5 * len(files_array))):
                        files_array = [{"path": p, "content": c}
                                       for p, c in flat]

        if not isinstance(files_array, list):
            raise ValueError("Expected JSON array of files")

        files = {}
        skipped = 0
        for file_obj in files_array:
            if not isinstance(file_obj, dict):
                skipped += 1
                continue
            # Key aliases: models drift between path/file_path/filename.
            path = (file_obj.get('path') or file_obj.get('file_path')
                    or file_obj.get('filepath') or file_obj.get('filename')
                    or file_obj.get('name'))
            content = file_obj.get('content')
            if content is None and isinstance(file_obj.get('code'), str):
                content = file_obj['code']

            if not path or content is None:
                skipped += 1
                continue
            files[path] = content

        if not files:
            raise ValueError("No valid file objects (need 'path' + 'content')")
        if skipped:
            logger.warning(
                f"Engineer envelope: skipped {skipped} malformed file object(s), "
                f"kept {len(files)}")
        return files

    def _parse_with_regex(self, response: str) -> Dict[str, str]:
        """Parse JSON using regex - more forgiving than json.loads()."""
        import re

        files = {}

        # Pattern to match file objects:
        # "path": "...", "content": "..."
        # This is very lenient and handles multi-line content
        pattern = (r'"(?:file_?path|path|filename)"\s*:\s*"([^"]+)"\s*,\s*'
                   r'"(?:content|code)"\s*:\s*"((?:[^"\\]|\\.)*)"')

        for match in re.finditer(pattern, response, re.DOTALL):
            path = match.group(1)
            content = match.group(2)

            # Unescape JSON escapes
            content = content.replace('\\n', '\n')
            content = content.replace('\\t', '\t')
            content = content.replace('\\"', '"')
            content = content.replace('\\\\', '\\')

            files[path] = content

        if not files:
            raise ValueError("No files found in response using regex parsing")

        return files

    @staticmethod
    def _normalize_file_paths(files: Dict[str, str]) -> Dict[str, str]:
        """Strip common root directory prefix from engineer-generated paths.

        LLMs frequently wrap all paths in a project-name directory, e.g.
        ``my-app/package.json`` instead of ``package.json``.  This causes
        path mismatches in the sandbox (which expects marker files at the
        workspace root).

        Also strips leading ``./`` from paths.

        Heuristic: if ALL paths share a single common first directory
        segment **and** no root-level files exist in the dict, strip
        that prefix.
        """
        if not files:
            return files

        # Normalise slashes and strip leading ./
        cleaned: Dict[str, str] = {}
        for p, content in files.items():
            norm = p.replace("\\", "/")
            while norm.startswith("./"):
                norm = norm[2:]
            cleaned[norm] = content
        files = cleaned

        # Root marker files that should live at the project root
        root_markers = {
            "package.json", "requirements.txt", "pyproject.toml",
            "go.mod", "Cargo.toml", "pom.xml", "Makefile",
        }

        # If any marker already exists at top level, paths are fine
        for p in files:
            if "/" not in p and p in root_markers:
                return files

        # Check whether every path starts with the same single directory
        first_segments: set = set()
        for p in files:
            parts = p.split("/")
            if len(parts) < 2:
                # A bare filename with no prefix — can't safely strip
                return files
            first_segments.add(parts[0])

        if len(first_segments) != 1:
            return files  # multiple prefixes, ambiguous

        prefix = next(iter(first_segments)) + "/"
        stripped = {p[len(prefix):]: c for p, c in files.items()}
        logger.info(
            "Stripped common root prefix '%s' from %d engineer file paths",
            prefix, len(stripped),
        )
        return stripped

    @staticmethod
    def _syntax_check_python(files: Dict[str, str]) -> Dict[str, str]:
        """Compile-check generated .py files (syntax only, nothing executed)."""
        broken: Dict[str, str] = {}
        for path, content in files.items():
            if not path.endswith(".py") or not isinstance(content, str):
                continue
            try:
                compile(content, path, "exec")
            except SyntaxError as e:
                broken[path] = f"line {e.lineno}: {e.msg}"
        return broken

    def _apply_deletes(self, paths: List[str], output_dir: Path) -> None:
        """Safely remove engineer-requested stale paths from staging.

        Policy-compliant deletion: every path is resolved and validated inside
        output_dir; symlinks are never followed; files are unlinked one by one
        and emptied directories removed bottom-up with rmdir. Anything that
        fails is logged and skipped — never escalated.
        """
        root = output_dir.resolve()
        deleted = 0
        for rel in paths[:50]:  # bounded
            target = (output_dir / rel.strip().lstrip("/")).resolve()
            if not str(target).startswith(str(root) + "/"):
                logger.warning("Delete request outside staging ignored: %r", rel)
                continue
            try:
                if target.is_symlink() or target.is_file():
                    target.unlink(missing_ok=True)
                    deleted += 1
                elif target.is_dir():
                    for f in sorted(target.rglob("*"), reverse=True):
                        try:
                            if f.is_symlink() or f.is_file():
                                f.unlink(missing_ok=True)
                                deleted += 1
                            elif f.is_dir():
                                f.rmdir()
                        except OSError as e:
                            logger.warning("Skip undeletable %s: %s", f, e)
                    target.rmdir()
            except OSError as e:
                logger.warning("Skip undeletable %s: %s", target, e)
        if deleted:
            logger.info("Engineer deletions applied: %d file(s) from %d request(s)",
                        deleted, len(paths))

    def _write_files(self, files: Dict[str, str], output_dir: Path) -> None:
        """Write files to disk.

        Args:
            files: Dictionary mapping file paths to content
            output_dir: Base directory to write files to
        """
        files = self._normalize_file_paths(files)
        output_dir.mkdir(parents=True, exist_ok=True)

        for file_path, content in files.items():
            full_path = output_dir / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding='utf-8')

        # Write manifest file to signal completion
        manifest = {
            'file_count': len(files),
            'files': list(files.keys()),
        }
        manifest_path = output_dir / '.manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
