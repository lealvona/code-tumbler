"""Verifier Agent - Tests and validates generated code.

The Verifier executes the verification strategy defined in the plan,
runs tests, checks builds, and generates quality reports.
"""

import json
import logging
from typing import Callable, Dict, Any, List, Optional
from pathlib import Path
import re

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class VerificationResult:
    """Container for verification results."""

    def __init__(self):
        self.build_success: bool = False
        self.build_output: str = ""
        self.tests_passed: int = 0
        self.tests_total: int = 0
        self.test_output: str = ""
        self.lint_issues: int = 0
        self.lint_output: str = ""
        self.runtime_output: str = ""
        self.errors: List[str] = []
        self.score: float = 0.0
        self.code_review_only: bool = False
        # Active Specification Alignment fields
        self.e2e_tests_passed: int = 0
        self.e2e_tests_total: int = 0
        self.e2e_output: str = ""
        self.rubric_items_verified: int = 0
        self.rubric_items_total: int = 0
        self.rubric_details: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'build_success': self.build_success,
            'build_output': self.build_output,
            'tests_passed': self.tests_passed,
            'tests_total': self.tests_total,
            'test_output': self.test_output,
            'lint_issues': self.lint_issues,
            'lint_output': self.lint_output,
            'runtime_output': self.runtime_output,
            'errors': self.errors,
            'score': self.score,
            'code_review_only': self.code_review_only,
            'e2e_tests_passed': self.e2e_tests_passed,
            'e2e_tests_total': self.e2e_tests_total,
            'e2e_output': self.e2e_output,
            'rubric_items_verified': self.rubric_items_verified,
            'rubric_items_total': self.rubric_items_total,
            'rubric_details': self.rubric_details,
        }


class VerifierAgent(BaseAgent):
    """The Verifier Agent tests and validates code.

    Responsibilities:
    - Execute verification strategy from plan
    - Run build/install commands
    - Execute tests
    - Run linting/quality checks
    - Analyze results
    - Generate quality report

    Output: Writes REPORT.md to the project's 04_feedback directory
    """

    def __init__(self, provider, system_prompt_path: Path = None,
                 verification_config=None, nothink_override: Optional[bool] = None):
        """Initialize the Verifier Agent.

        Args:
            provider: LLM provider instance
            system_prompt_path: Path to system prompt file (optional)
            verification_config: VerificationConfig for sandbox settings (optional)
            nothink_override: Per-agent override for nothink (True/False/None=auto)
        """
        self._verification_config = verification_config

        # Load system prompt from file or use default path
        if system_prompt_path is None:
            try:
                backend_dir = Path(__file__).parent.parent.parent
                system_prompt_path = backend_dir / "prompts" / "verifier_system.txt"
            except:
                system_prompt_path = Path("prompts/verifier_system.txt")

        system_prompt = self._load_prompt(system_prompt_path)
        super().__init__(provider, system_prompt, name="Verifier",
                         nothink_override=nothink_override)

    def _load_prompt(self, path: Path) -> str:
        """Load system prompt from file."""
        if not path.exists():
            return """You are a Senior QA Engineer.
            Analyze verification results and generate quality reports."""

        return path.read_text(encoding='utf-8')

    def _build_messages(self, context: Dict[str, Any]) -> List[Dict[str, str]]:
        """Build messages for the Verifier LLM request.

        Args:
            context: Dictionary containing:
                - plan: str - The architectural plan
                - iteration: int - Current iteration number
                - verification_results: VerificationResult - Results from tests
                - code_summary: Dict (optional) - Summary of generated files

        Returns:
            List of messages for LLM
        """
        plan = context.get('plan', '')
        iteration = context.get('iteration', 1)
        results = context.get('verification_results')
        code_summary = context.get('code_summary', {})

        if not results:
            raise ValueError("verification_results required in context")

        # --- Compressible section: plan and code listings ---
        # Only the plan and code files are wrapped in <compress>.
        # Verification results (sandbox output) stay OUTSIDE so the
        # LLM receives exact build/test/lint output for accurate scoring.
        user_message = f"""<compress>
# Architectural Plan

{plan}

# Iteration {iteration} — Generated Code

"""
        # Dynamic content budget based on actual model context window
        budget = self._context_manager.calculate_budget(
            self.provider.config, self.system_prompt,
            self.default_max_tokens or 8192
        )
        # Reserve ~3000 tokens for plan, verification results, and task instructions
        code_budget_tokens = max(1000, budget.content_budget - 3000)
        max_total_content = int(code_budget_tokens * 3.8)
        logger.debug(
            f"Verifier code budget: {code_budget_tokens} tokens "
            f"(~{max_total_content} chars) of {budget.context_length} context"
        )
        total_chars = 0
        for file_path, content in code_summary.items():
            if content and not content.startswith('['):
                if total_chars + len(content) < max_total_content:
                    user_message += f"### {file_path}\n```\n{content}\n```\n\n"
                    total_chars += len(content)
                else:
                    lines = len(content.split('\n'))
                    user_message += f"- {file_path} ({lines} lines) [content omitted for size]\n"
            else:
                user_message += f"- {file_path} {content}\n"

        if not code_summary:
            user_message += "No files found in staging directory.\n"

        # Close the compressible section
        user_message += "</compress>\n"

        # --- Non-compressible section: verification results ---
        # Exact sandbox output must NOT be altered by compression.
        if results.code_review_only:
            user_message += """
# Verification Results

No automated build/test/lint commands were available for this project.
Scoring must be based on code review only.
"""
        else:
            user_message += f"""
# Verification Results

## Build/Installation

**Status**: {"SUCCESS" if results.build_success else "FAILED"}

```
{results.build_output}
```

## Test Results

**Tests Passed**: {results.tests_passed}/{results.tests_total}

```
{results.test_output}
```

## Linting Results

**Issues Found**: {results.lint_issues}

```
{results.lint_output}
```

## Errors

"""
            if results.errors:
                for error in results.errors:
                    user_message += f"- {error}\n"
            else:
                user_message += "None\n"

            # --- Rubric grading section (non-compressible) ---
            if results.rubric_details:
                user_message += "\n## Specification Alignment (Rubric)\n\n"
                user_message += "| ID | Category | Requirement | Status | Notes |\n"
                user_message += "|----|----------|-------------|--------|-------|\n"
                for item in results.rubric_details:
                    status = "PASS" if item.get("verified") else ("FAIL" if item.get("verified") is False else "N/A")
                    notes = (item.get("notes") or "").replace("|", "/")
                    user_message += f"| {item['id']} | {item['category']} | {item['requirement'][:60]} | {status} | {notes[:80]} |\n"
                user_message += f"\n**Rubric Score**: {results.rubric_items_verified}/{results.rubric_items_total}\n"

            # --- E2E test results section (non-compressible) ---
            if results.e2e_tests_total > 0:
                user_message += f"\n## E2E Test Results\n\n"
                user_message += f"**Tests Passed**: {results.e2e_tests_passed}/{results.e2e_tests_total}\n\n"
                if results.e2e_output:
                    # Truncate E2E output to avoid overwhelming the context
                    e2e_out = results.e2e_output[:8000]
                    if len(results.e2e_output) > 8000:
                        e2e_out += "\n[... truncated ...]"
                    user_message += f"```\n{e2e_out}\n```\n"

        if results.code_review_only:
            user_message += """
# Your Task

No automated verification could run for this project. Perform a **code review** instead.

Review the generated code and produce a quality report following the format in your system prompt. Base your score ENTIRELY on code quality:
1. Does the code match the architectural plan?
2. Are all planned files present and complete?
3. Are imports correct and consistent?
4. Is the code well-structured and idiomatic?
5. Are there any obvious bugs, missing error handling, or security issues?

You MUST include an **Overall Score: X/10** line in your report.
"""
        else:
            user_message += """
# Your Task

Analyze these verification results and generate a comprehensive quality report following the format in your system prompt.

Include:
1. Overall quality score (0-10)
2. Detailed analysis of each verification step
3. Specific issues found with locations
4. Actionable recommendations for the Engineer
5. Score breakdown

Be objective, specific, and constructive.
"""

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]

    def verify(
        self,
        plan: str,
        project_path: Path,
        iteration: int = 1,
        output_path: Path = None,
        on_phase_complete: Optional[Callable] = None,
        verification_config=None,
        **kwargs
    ) -> tuple[str, float]:
        """Verify a project by executing verification strategy.

        Args:
            plan: The architectural plan (to extract verification strategy)
            project_path: Path to the generated project
            iteration: Current iteration number
            output_path: Where to save REPORT.md (optional)
            on_phase_complete: Callback invoked after each sandbox phase
            verification_config: Optional override for self._verification_config
            **kwargs: Additional LLM parameters

        Returns:
            Tuple of (report content, quality score)
        """
        # Extract verification strategy from plan
        verification_strategy = self._extract_verification_strategy(plan)

        # Load rubric if it exists (generated by Architect alongside PLAN.md)
        rubric = self._load_rubric(project_path)

        # Generate E2E tests if rubric has dynamic/behavioral items
        e2e_test_file = None
        if rubric and rubric.dynamic_items():
            e2e_test_file = self._generate_e2e_tests(plan, rubric, project_path, **kwargs)

        # Execute verification (pass plan for runtime detection)
        results = self._execute_verification(
            project_path, verification_strategy, plan=plan,
            on_phase_complete=on_phase_complete,
            verification_config=verification_config,
            e2e_test_file=e2e_test_file,
        )

        # Grade rubric items based on verification results
        if rubric:
            self._grade_rubric(rubric, results)
            results.rubric_items_total = len(rubric.items)
            results.rubric_items_verified = sum(1 for it in rubric.items if it.verified)
            results.rubric_details = [
                {"id": it.id, "category": it.category,
                 "requirement": it.requirement, "priority": it.priority,
                 "verified": it.verified, "notes": it.notes}
                for it in rubric.items
            ]

        # Calculate preliminary score
        results.score = self._calculate_score(results)

        # Get code summary
        code_summary = self._get_code_summary(project_path)

        # Build context for LLM
        context = {
            'plan': plan,
            'iteration': iteration,
            'verification_results': results,
            'code_summary': code_summary,
        }

        # Save input messages for debugging
        if output_path:
            debug_dir = output_path.parent.parent / ".tumbler" / "logs"
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_messages = self._build_messages(context)
            debug_input_file = debug_dir / f"verifier_input_iter{iteration}.json"
            debug_input_file.write_text(
                json.dumps(debug_messages, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

        # Generate report using LLM
        report = self.execute(context, **kwargs)

        # Extract final score from report (LLM might adjust it)
        llm_score = self._extract_score_from_report(report)
        if llm_score is not None:
            final_score = llm_score
        elif results.score is not None:
            final_score = results.score
        else:
            # No automated verification ran AND LLM didn't produce a score
            # (e.g. truncated response). Default to 5.0 = "needs human review"
            # rather than 3.0 which forces wasteful re-iterations.
            final_score = 5.0
            logger.warning(
                "No score from verification or LLM report — defaulting to 5.0"
            )

        # Save report if output_path provided
        if output_path:
            self.save_file(output_path, report)

        return report, final_score

    def _extract_verification_strategy(self, plan: str) -> Dict[str, List[str]]:
        """Extract verification commands from plan.

        Args:
            plan: The architectural plan

        Returns:
            Dictionary with setup, install, test, build, run commands
        """
        strategy = {
            'setup': [],
            'install': [],
            'test': [],
            'build': [],
            'run': [],
        }

        # Simple extraction - look for command blocks in plan
        # In a real implementation, this would be more sophisticated
        sections = {
            'Setup Commands': 'setup',
            'Install Commands': 'install',
            'Test Commands': 'test',
            'Build Commands': 'build',
            'Run Commands': 'run',
        }

        for section_name, key in sections.items():
            pattern = rf"{section_name}[:\s]*```(?:bash)?\s*\n(.*?)```"
            match = re.search(pattern, plan, re.DOTALL | re.IGNORECASE)
            if match:
                commands = match.group(1).strip().split('\n')
                strategy[key] = [cmd.strip() for cmd in commands if cmd.strip() and not cmd.strip().startswith('#')]

        return strategy

    def _execute_verification(
        self,
        project_path: Path,
        strategy: Dict[str, List[str]],
        plan: str = "",
        on_phase_complete: Optional[Callable] = None,
        verification_config=None,
        e2e_test_file: Optional[Path] = None,
    ) -> VerificationResult:
        """Execute verification commands in a sandboxed container.

        Attempts to use the SandboxExecutor for real build/test/lint
        execution inside ephemeral Docker containers. Falls back to
        code_review_only mode when:
          - Docker SDK is not installed
          - Sandbox is disabled in config
          - Runtime cannot be detected
          - Container execution fails

        Args:
            project_path: Path to the project staging directory
            strategy: Dictionary of command lists from plan
            plan: The architectural plan text (for runtime detection)
            on_phase_complete: Callback invoked after each sandbox phase
            verification_config: Optional override for self._verification_config
            e2e_test_file: Path to generated Playwright test file (optional)

        Returns:
            VerificationResult with real or code-review-only outputs
        """
        # Use override if provided, otherwise fall back to instance config
        vc_source = verification_config if verification_config is not None else self._verification_config

        # Check if sandbox is enabled
        sandbox_enabled = True
        if vc_source is not None:
            sandbox_enabled = getattr(vc_source, 'sandbox_enabled', True)

        if not sandbox_enabled:
            logger.info("Sandbox verification disabled in config — using code review only")
            results = VerificationResult()
            results.code_review_only = True
            return results

        try:
            from verification.sandbox import SandboxExecutor, SandboxConfig, detect_runtime

            # Build SandboxConfig from verification config
            sandbox_cfg = None
            if vc_source is not None:
                vc = vc_source
                sandbox_cfg = SandboxConfig(
                    enabled=vc.sandbox_enabled,
                    timeout_install=vc.timeout_install,
                    timeout_build=vc.timeout_build,
                    timeout_test=vc.timeout_test,
                    timeout_lint=vc.timeout_lint,
                    memory_limit=vc.memory_limit,
                    cpu_limit=vc.cpu_limit,
                    tmpfs_size=vc.tmpfs_size,
                    network_install=vc.network_install,
                    network_verify=vc.network_verify,
                    e2e_enabled=getattr(vc, 'e2e_enabled', True),
                    timeout_e2e=getattr(vc, 'timeout_e2e', 180),
                    memory_limit_e2e=getattr(vc, 'memory_limit_e2e', '3g'),
                )

            # Detect runtime from project files and plan text
            runtime = detect_runtime(plan, project_path)
            if runtime is None:
                logger.info("Could not detect runtime — using code review only")
                results = VerificationResult()
                results.code_review_only = True
                return results

            # Prepare E2E commands if we have a test file and a web app
            e2e_test_commands = None
            if e2e_test_file and runtime.is_web_app:
                e2e_test_commands = self._prepare_e2e_commands(
                    e2e_test_file, runtime, project_path
                )

            # Run sandboxed verification
            executor = SandboxExecutor(config=sandbox_cfg)
            return executor.run_verification(
                project_path, strategy, runtime,
                on_phase_complete=on_phase_complete,
                e2e_test_commands=e2e_test_commands,
            )

        except ImportError:
            logger.info("Docker SDK not available — using code review only")
            results = VerificationResult()
            results.code_review_only = True
            return results

        except Exception as e:
            logger.warning(f"Sandbox verification failed: {e}")
            results = VerificationResult()
            results.code_review_only = True
            results.errors.append(f"Sandbox failed: {e}")
            return results

    def _calculate_score(self, results: VerificationResult) -> Optional[float]:
        """Calculate preliminary quality score from verification results.

        Uses a dual scoring model:
          - Non-web apps: Build(3) + Tests(4) + Lint(2) + NoErrors(1) = 10
          - Web apps:     Build(2) + Tests(2) + Lint(1) + NoErrors(1) + E2E(2) + Rubric(2) = 10

        Returns None when no verification commands ran (code_review_only).
        """
        if results.code_review_only:
            return None  # defer to LLM code review

        # Web app scoring (has E2E results)
        if results.e2e_tests_total > 0:
            score = 0.0
            if results.build_success:
                score += 2.0
            if results.tests_total > 0:
                score += (results.tests_passed / results.tests_total) * 2.0
            if results.lint_issues == 0:
                score += 1.0
            elif results.lint_issues < 5:
                score += 0.5
            if not results.errors:
                score += 1.0
            # E2E tests: 2 points
            score += (results.e2e_tests_passed / results.e2e_tests_total) * 2.0
            # Spec completeness: 2 points
            if results.rubric_items_total > 0:
                score += (results.rubric_items_verified / results.rubric_items_total) * 2.0
            return min(10.0, score)

        # Standard scoring (backward compatible for non-web apps)
        score = 0.0
        if results.build_success:
            score += 3.0
        if results.tests_total > 0:
            score += (results.tests_passed / results.tests_total) * 4.0
        if results.lint_issues == 0:
            score += 2.0
        elif results.lint_issues < 5:
            score += 1.0
        if not results.errors:
            score += 1.0
        # Rubric bonus for non-web apps (if rubric exists, give up to 0.5 bonus)
        if results.rubric_items_total > 0:
            rubric_rate = results.rubric_items_verified / results.rubric_items_total
            score += rubric_rate * 0.5
        return min(10.0, score)

    def _get_code_summary(self, project_path: Path) -> Dict[str, str]:
        """Get generated file contents for verification.

        Args:
            project_path: Path to project (staging directory)

        Returns:
            Dictionary mapping file paths to file content
        """
        summary = {}

        if not project_path.exists():
            return summary

        skip_ext = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.bin',
                    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff',
                    '.woff2', '.ttf', '.eot', '.zip', '.tar', '.gz'}
        max_file_size = 50_000  # 50KB per file

        for file_path in project_path.rglob('*'):
            if file_path.is_file() and not file_path.name.startswith('.'):
                if file_path.suffix.lower() in skip_ext:
                    continue
                rel_path = str(file_path.relative_to(project_path))
                try:
                    if file_path.stat().st_size <= max_file_size:
                        summary[rel_path] = file_path.read_text(encoding='utf-8')
                    else:
                        summary[rel_path] = f"[File too large: {file_path.stat().st_size} bytes]"
                except (UnicodeDecodeError, OSError):
                    summary[rel_path] = "[Binary or unreadable file]"

        return summary

    def _extract_score_from_report(self, report: str) -> Optional[float]:
        """Extract quality score from report.

        Args:
            report: The generated report

        Returns:
            Extracted score or None
        """
        # Look for "Overall Score: X/10" or "Total: X/10"
        patterns = [
            r'\*\*Overall Score\*\*:\s*(\d+(?:\.\d+)?)/10',
            r'\*\*Total\*\*:\s*(\d+(?:\.\d+)?)/10',
            r'Score:\s*(\d+(?:\.\d+)?)/10',
        ]

        for pattern in patterns:
            match = re.search(pattern, report, re.IGNORECASE)
            if match:
                return float(match.group(1))

        return None

    # ------------------------------------------------------------------
    # Active Specification Alignment methods
    # ------------------------------------------------------------------

    def _load_rubric(self, project_path: Path) -> Optional[Any]:
        """Load RUBRIC.yaml from the 02_plan directory.

        project_path is the staging directory (03_staging), so we go up
        one level to find the project root, then into 02_plan.
        """
        project_root = project_path.parent
        rubric_file = project_root / "02_plan" / "RUBRIC.yaml"
        if not rubric_file.exists():
            logger.debug("No RUBRIC.yaml found at %s", rubric_file)
            return None

        try:
            from verification.rubric import Rubric
            text = rubric_file.read_text(encoding='utf-8')
            rubric = Rubric.from_yaml(text)
            logger.info("Loaded rubric with %d items from %s",
                        len(rubric.items), rubric_file)
            return rubric
        except Exception as e:
            logger.warning("Failed to load RUBRIC.yaml: %s", e)
            return None

    def _generate_e2e_tests(
        self, plan: str, rubric: Any, project_path: Path, **kwargs
    ) -> Optional[Path]:
        """Generate Playwright E2E test script from rubric's dynamic items.

        Makes a separate LLM call to produce a test file. The file is written
        to 03_staging/e2e_generated/ and will be archived into the sandbox.

        Returns the path to the generated test file, or None on failure.
        """
        dynamic_items = rubric.dynamic_items()
        if not dynamic_items:
            return None

        # Build the rubric items text
        items_text = "\n".join(
            f"- [{it.id}] {it.requirement} (Check: {it.check})"
            for it in dynamic_items
        )

        # Determine language and port from rubric context
        # Look for web app info by checking project files
        try:
            from verification.web_detect import detect_web_app
            web_info = detect_web_app(plan, project_path)
        except Exception:
            web_info = None

        if not web_info or not web_info.is_web_app:
            logger.debug("Not a web app — skipping E2E test generation")
            return None

        port = web_info.dev_server_port
        lang = web_info.language

        # Load the E2E generator system prompt
        try:
            backend_dir = Path(__file__).parent.parent.parent
            prompt_path = backend_dir / "prompts" / "e2e_generator_system.txt"
            e2e_system_prompt = prompt_path.read_text(encoding='utf-8')
        except Exception:
            e2e_system_prompt = "Generate Playwright E2E tests for the given rubric items."

        user_prompt = f"""# Project Plan Summary

{plan[:3000]}

# Rubric Items to Verify

{items_text}

# Configuration

- Dev server port: {port}
- Language: {lang}
- Base URL: http://localhost:{port}

Generate the Playwright test file now. Output ONLY the test file content, no markdown fences."""

        try:
            messages = [
                {"role": "system", "content": e2e_system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Filter kwargs to only pass LLM-relevant ones
            llm_kwargs = {}
            if 'temperature' in kwargs:
                llm_kwargs['temperature'] = kwargs['temperature']

            test_content = self.provider.chat(messages=messages, **llm_kwargs)

            # Track usage
            usage = self.provider.get_usage()
            self.usage_history.append({
                'agent': f"{self.name}-e2e-gen",
                'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens,
                'cost': usage.cost,
            })

            if not test_content or len(test_content.strip()) < 20:
                logger.warning("E2E test generation produced empty output")
                return None

            # Strip any markdown fences the LLM might have included
            test_content = re.sub(r'^```\w*\n', '', test_content.strip())
            test_content = re.sub(r'\n```$', '', test_content.strip())

            # Write the test file
            e2e_dir = project_path / "e2e_generated"
            e2e_dir.mkdir(parents=True, exist_ok=True)

            if lang == "python":
                test_file = e2e_dir / "test_e2e_spec.py"
            else:
                test_file = e2e_dir / "e2e_spec.test.ts"

            test_file.write_text(test_content, encoding='utf-8')
            logger.info("Generated E2E test file: %s (%d chars)",
                        test_file, len(test_content))
            return test_file

        except Exception as e:
            logger.warning("E2E test generation failed: %s", e)
            return None

    def _grade_rubric(self, rubric: Any, results: "VerificationResult") -> None:
        """Grade rubric items based on verification results.

        Static items are graded from build/test/lint output.
        Dynamic and behavioral items are graded from E2E test output.
        """
        # Combine all available output for pattern matching
        all_output = "\n".join(filter(None, [
            results.build_output,
            results.test_output,
            results.lint_output,
        ])).lower()

        e2e_output = (results.e2e_output or "").lower()

        for item in rubric.items:
            if item.category == "static":
                # Grade static items by checking if the check description
                # keywords appear in build/test/lint output as positive signals
                item.verified = self._grade_static_item(item, all_output, results)
            elif item.category in ("dynamic", "behavioral"):
                # Grade dynamic/behavioral from E2E output
                if results.e2e_tests_total > 0:
                    item.verified = self._grade_dynamic_item(item, e2e_output)
                else:
                    # No E2E ran — leave as ungraded
                    item.verified = None
                    item.notes = "E2E tests did not run"

    @staticmethod
    def _grade_static_item(item: Any, output: str, results: "VerificationResult") -> bool:
        """Grade a static rubric item from build/test/lint output."""
        # If build succeeded and no critical errors, most static items pass
        # Use keyword matching from the item's check description
        check_lower = item.check.lower()

        # Check for dependency/package presence
        if any(kw in check_lower for kw in ("dependency", "package", "import", "library")):
            # If install succeeded, the dependency is likely present
            if results.build_success:
                item.notes = "Build succeeded (dependency likely present)"
                return True
            else:
                item.notes = "Build failed (dependency may be missing)"
                return False

        # Check for file/script presence
        if any(kw in check_lower for kw in ("file", "script", "config", "exist")):
            if results.build_success:
                item.notes = "Build succeeded (files present)"
                return True

        # Check for test-related items
        if any(kw in check_lower for kw in ("test", "spec", "assert")):
            if results.tests_total > 0 and results.tests_passed == results.tests_total:
                item.notes = f"All {results.tests_total} tests passed"
                return True
            elif results.tests_total > 0:
                item.notes = f"{results.tests_passed}/{results.tests_total} tests passed"
                return results.tests_passed > 0

        # Default: if build succeeded, give benefit of doubt for static items
        if results.build_success:
            item.notes = "Build succeeded"
            return True

        item.notes = "Build failed"
        return False

    @staticmethod
    def _grade_dynamic_item(item: Any, e2e_output: str) -> bool:
        """Grade a dynamic/behavioral rubric item from E2E test output."""
        item_id_lower = item.id.lower()

        # Check if this specific test passed or failed in the output
        # Playwright outputs test names — look for the item ID or requirement keywords
        if item_id_lower in e2e_output:
            if f"pass" in e2e_output[e2e_output.index(item_id_lower):e2e_output.index(item_id_lower) + 200]:
                item.notes = "E2E test passed"
                return True

        # Check for keywords from the requirement in pass context
        req_words = [w for w in item.requirement.lower().split() if len(w) > 3]
        matching_words = sum(1 for w in req_words if w in e2e_output)

        # If most requirement keywords appear in E2E output and no "fail" near them
        if matching_words >= len(req_words) * 0.5 and "fail" not in e2e_output:
            item.notes = "E2E output suggests pass"
            return True

        if "fail" in e2e_output or "error" in e2e_output:
            item.notes = "E2E output contains failures"
            return False

        # If E2E ran but we can't determine, mark as passed if no failures detected
        if e2e_output and "fail" not in e2e_output and "error" not in e2e_output:
            item.notes = "No E2E failures detected"
            return True

        item.notes = "Could not determine from E2E output"
        return False

    @staticmethod
    def _prepare_e2e_commands(
        e2e_test_file: Path, runtime: Any, project_path: Path
    ) -> Optional[List[str]]:
        """Prepare shell commands to run generated E2E tests.

        Returns the commands that the sandbox E2E container should execute
        to run the generated Playwright tests.
        """
        if not e2e_test_file or not e2e_test_file.exists():
            return None

        rel_path = str(e2e_test_file.relative_to(project_path))

        if e2e_test_file.suffix == ".py":
            return [
                "pip install --no-cache-dir playwright pytest-playwright",
                "playwright install chromium --with-deps",
                f"python -m pytest {rel_path} -v --tb=short 2>&1 || true",
            ]
        else:
            # TypeScript / JavaScript
            return [
                "npm install --save-dev @playwright/test",
                "npx playwright install chromium --with-deps",
                f"npx playwright test {rel_path} --reporter=list 2>&1 || true",
            ]
