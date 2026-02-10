"""Architect Agent - Plans project architecture and verification strategy.

The Architect analyzes user requirements and produces a comprehensive technical plan
including technology stack, directory structure, and verification strategy.
It also generates a RUBRIC.yaml for specification alignment grading.
"""

import logging
import re
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ArchitectAgent(BaseAgent):
    """The Architect Agent plans project architecture.

    Responsibilities:
    - Analyze user requirements
    - Select appropriate technology stack
    - Design directory structure
    - Define verification strategy
    - Provide implementation guidance

    Output: Writes PLAN.md file to the project's 02_plan directory
    """

    def __init__(self, provider, system_prompt_path: Path = None,
                 nothink_override: Optional[bool] = None):
        """Initialize the Architect Agent.

        Args:
            provider: LLM provider instance
            system_prompt_path: Path to system prompt file (optional)
            nothink_override: Per-agent override for nothink (True/False/None=auto)
        """
        # Load system prompt from file or use default path
        if system_prompt_path is None:
            # Default to prompts/architect_system.txt relative to backend dir
            try:
                from pathlib import Path
                backend_dir = Path(__file__).parent.parent.parent
                system_prompt_path = backend_dir / "prompts" / "architect_system.txt"
            except:
                # Fallback if path resolution fails
                system_prompt_path = Path("prompts/architect_system.txt")

        system_prompt = self._load_prompt(system_prompt_path)
        super().__init__(provider, system_prompt, name="Architect",
                         nothink_override=nothink_override)

    def _load_prompt(self, path: Path) -> str:
        """Load system prompt from file.

        Args:
            path: Path to the prompt file

        Returns:
            Prompt content as string
        """
        if not path.exists():
            # Fallback to a minimal prompt if file doesn't exist
            return """You are a Senior Software Architect.
            Analyze requirements and create a technical plan with:
            - Tech stack selection
            - Directory structure
            - Verification strategy"""

        return path.read_text(encoding='utf-8')

    def _build_messages(self, context: Dict[str, Any]) -> List[Dict[str, str]]:
        """Build messages for the Architect LLM request.

        Args:
            context: Dictionary containing:
                - requirements: str - User's project requirements
                - project_name: str (optional) - Project name
                - constraints: Dict (optional) - Budget/time constraints

        Returns:
            List of messages for LLM
        """
        requirements = context.get('requirements', '')
        project_name = context.get('project_name', 'project')
        constraints = context.get('constraints', {})

        # Build user message — context in <compress> markers, task instructions outside
        user_message = f"""<compress>
# Project Requirements

{requirements}

# Project Details
- **Project Name**: {project_name}
"""

        # Add constraints if provided
        if constraints:
            user_message += "\n# Constraints\n"
            for key, value in constraints.items():
                user_message += f"- **{key}**: {value}\n"

        user_message += "</compress>\n"
        user_message += """
# Your Task

Please analyze these requirements and produce a comprehensive technical plan following the format specified in your system prompt.

Focus on:
1. Selecting the most appropriate technology stack
2. Designing a clear, logical directory structure
3. Defining a complete verification strategy
4. Providing actionable implementation guidance

Remember: Another AI will implement your plan, so be specific and unambiguous.
"""

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]

    def plan_project(
        self,
        requirements: str,
        project_name: str = "project",
        constraints: Dict[str, Any] = None,
        output_path: Path = None,
        **kwargs
    ) -> str:
        """Generate a project plan from requirements.

        Args:
            requirements: User's project requirements
            project_name: Name of the project
            constraints: Optional constraints (budget, time, etc.)
            output_path: Where to save PLAN.md (optional)
            **kwargs: Additional LLM parameters

        Returns:
            The generated plan as a string
        """
        context = {
            'requirements': requirements,
            'project_name': project_name,
            'constraints': constraints or {},
        }

        # Execute the agent
        plan = self.execute(context, **kwargs)

        # Save to file if output_path provided
        if output_path:
            self.save_file(output_path, plan)

        # Extract and save RUBRIC.yaml alongside PLAN.md
        if output_path:
            rubric_yaml = self._extract_rubric_yaml(plan)
            if rubric_yaml:
                rubric_path = output_path.parent / "RUBRIC.yaml"
                self.save_file(rubric_path, rubric_yaml)
                logger.info("Saved specification rubric to %s", rubric_path)
            else:
                logger.debug("No rubric YAML block found in plan output")

        return plan

    def revise_plan(
        self,
        original_plan: str,
        feedback: str,
        output_path: Path = None,
        **kwargs
    ) -> str:
        """Revise an existing plan based on feedback.

        Args:
            original_plan: The original PLAN.md content
            feedback: Feedback from Verifier or user
            output_path: Where to save revised PLAN.md (optional)
            **kwargs: Additional LLM parameters

        Returns:
            The revised plan as a string
        """
        # Build revision context
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""<compress>
# Original Plan

{original_plan}

# Feedback

{feedback}
</compress>

# Your Task

Please revise the plan to address the feedback. Maintain the same format and structure, but incorporate the necessary changes.
"""}
        ]

        # Make the LLM request directly with custom messages
        revised_plan = self.provider.chat(messages=messages, **kwargs)

        # Track usage
        usage = self.provider.get_usage()
        self.usage_history.append({
            'agent': self.name,
            'input_tokens': usage.input_tokens,
            'output_tokens': usage.output_tokens,
            'cost': usage.cost,
        })

        # Save to file if output_path provided
        if output_path:
            self.save_file(output_path, revised_plan)

        return revised_plan

    @staticmethod
    def _extract_rubric_yaml(plan: str) -> Optional[str]:
        """Extract RUBRIC.yaml content from the plan output.

        Looks for a YAML code block containing a top-level 'rubric:' key.
        Returns the raw YAML text (without fences), or None if not found.
        """
        # Match ```yaml ... ``` blocks that contain "rubric:"
        for match in re.finditer(r"```(?:yaml|yml)\s*\n(.*?)```", plan, re.DOTALL):
            block = match.group(1).strip()
            if block.startswith("rubric:") or "\nrubric:" in block:
                return block

        # Fallback: look for a bare "rubric:" section without fences
        # (some models may not always use fences)
        match = re.search(r"^(rubric:\s*\n(?:\s+-\s+.*\n?)+)", plan, re.MULTILINE)
        if match:
            return match.group(1).strip()

        return None
