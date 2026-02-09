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

        # Execute verification (pass plan for runtime detection)
        results = self._execute_verification(
            project_path, verification_strategy, plan=plan,
            on_phase_complete=on_phase_complete,
            verification_config=verification_config,
        )

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
                )

            # Detect runtime from project files and plan text
            runtime = detect_runtime(plan, project_path)
            if runtime is None:
                logger.info("Could not detect runtime — using code review only")
                results = VerificationResult()
                results.code_review_only = True
                return results

            # Run sandboxed verification
            executor = SandboxExecutor(config=sandbox_cfg)
            return executor.run_verification(
                project_path, strategy, runtime,
                on_phase_complete=on_phase_complete,
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

        Returns None when no verification commands ran (code_review_only),
        signalling that the LLM report score should be used instead.

        Args:
            results: Verification results

        Returns:
            Score from 0 to 10, or None if no automated verification ran.
        """
        if results.code_review_only:
            return None  # defer to LLM code review

        score = 0.0

        # Build success: 3 points
        if results.build_success:
            score += 3.0

        # Test pass rate: 4 points
        if results.tests_total > 0:
            test_rate = results.tests_passed / results.tests_total
            score += test_rate * 4.0

        # Linting: 2 points (fewer issues = higher score)
        if results.lint_issues == 0:
            score += 2.0
        elif results.lint_issues < 5:
            score += 1.0

        # No critical errors: 1 point
        if not results.errors:
            score += 1.0

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
