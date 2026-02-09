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

        # Build the chunk-specific task instruction suffix
        if chunk_info:
            file_list = "\n".join(f"- `{f}`" for f in chunk_info['target_files'])
            chunk_task = f"""
# Chunked Generation (Part {chunk_info['chunk_num']} of {chunk_info['total_chunks']})

Generate ONLY the following files in this request:
{file_list}

Do NOT generate files not in this list. Other files will be generated in separate requests.

Output as a JSON array (only the files listed above):
[{{"path": "...", "content": "..."}}, ...]
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
Generate ALL files specified in the plan as a JSON array. Each file should have:
- `path`: Relative path from project root
- `content`: Complete file content

Ensure:
1. All files from the plan are included
2. All imports are correct
3. All tests are comprehensive
4. All configuration files are complete
5. Code is production-ready

Output pure JSON (no markdown fences):
```json
[{{"path": "...", "content": "..."}}, ...]
```
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
4. Improving code quality

Generate the COMPLETE file tree again as JSON (even files that didn't change).

Output as a JSON array:
[{{"path": "...", "content": "..."}}, ...]
"""

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

    def _generate_single(
        self, plan, iteration, feedback, previous_code, output_dir, **kwargs
    ) -> Dict[str, str]:
        """Generate all files in a single request (existing behavior)."""
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
            debug_messages = self._build_messages(context)
            debug_input_file = debug_dir / f"engineer_input_iter{iteration}.json"
            debug_input_file.write_text(
                json.dumps(debug_messages, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

        response = self.execute(context, **kwargs)

        if output_dir:
            debug_dir = output_dir.parent / ".tumbler" / "logs"
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f"engineer_raw_output_iter{iteration}.txt"
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
            raise ValueError(error_msg)

    # -- Chunk generation ---------------------------------------------------

    def _generate_chunk(
        self, plan, iteration, feedback, previous_code,
        target_files, chunk_num, total_chunks, output_dir, **kwargs
    ) -> Dict[str, str]:
        """Generate a subset of files as one chunk request."""
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

        response = self.execute(context, **kwargs)

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
        # Calculate budget to decide if chunking is needed
        budget = self._context_manager.calculate_budget(
            self.provider.config, self.system_prompt, self.default_max_tokens
        )
        planned_files = self._extract_planned_files(plan)

        if not planned_files or not self._needs_chunking(planned_files, budget):
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

        # Write files if output_dir provided
        if output_dir:
            self._write_files(files, output_dir)

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

        # Try to extract JSON from markdown code blocks
        json_block_pattern = r'```(?:json)?\s*\n(.*?)```'
        match = re.search(json_block_pattern, response, re.DOTALL)
        if match:
            response = match.group(1).strip()

        # Strategy 1: Try parsing as-is
        try:
            files_array = json.loads(response)
            return self._convert_to_file_dict(files_array)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Fix common issues with Python docstrings in JSON
        # The model often writes Python's """ as \" \" \" which breaks JSON
        # We need to properly escape these
        try:
            # Fix incomplete triple-quote escaping
            fixed = response.replace('\\"\\"\\"', '\\\\"\\\\"\\\\"')  # """ -> \"\"\"
            files_array = json.loads(fixed)
            return self._convert_to_file_dict(files_array)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Parse file-by-file using regex
        # This is more forgiving and can handle malformed JSON
        try:
            return self._parse_with_regex(response)
        except Exception as e:
            raise ValueError(f"All JSON parsing strategies failed. Last error: {e}")

    def _convert_to_file_dict(self, files_array: Any) -> Dict[str, str]:
        """Convert parsed JSON array to file dictionary."""
        if not isinstance(files_array, list):
            raise ValueError("Expected JSON array of files")

        files = {}
        for file_obj in files_array:
            if not isinstance(file_obj, dict):
                raise ValueError(f"Invalid file object: {file_obj}")

            path = file_obj.get('path')
            content = file_obj.get('content')

            if not path or content is None:
                raise ValueError(f"File object missing 'path' or 'content'")

            files[path] = content

        return files

    def _parse_with_regex(self, response: str) -> Dict[str, str]:
        """Parse JSON using regex - more forgiving than json.loads()."""
        import re

        files = {}

        # Pattern to match file objects:
        # "path": "...", "content": "..."
        # This is very lenient and handles multi-line content
        pattern = r'"path"\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"((?:[^"\\]|\\.)*)"'

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

    def _write_files(self, files: Dict[str, str], output_dir: Path) -> None:
        """Write files to disk.

        Args:
            files: Dictionary mapping file paths to content
            output_dir: Base directory to write files to
        """
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
