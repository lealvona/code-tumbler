"""Configuration loader for Code Tumbler.

This module loads configuration from config.yaml and environment variables.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Use absolute import to avoid relative import issues when running scripts
try:
    from providers.base import ProviderConfig, ProviderType
except ImportError:
    from ..providers.base import ProviderConfig, ProviderType


@dataclass
class TumblerConfig:
    """Tumbler-specific configuration."""

    max_iterations: int = 10
    quality_threshold: float = 8.0
    project_timeout: int = 3600
    debounce_time: int = 3
    max_cost_per_project: float = 0.0


@dataclass
class DatabaseConfig:
    """Database configuration."""

    url: str = "postgresql://tumbler:changeme@localhost:5432/tumbler"
    pool_size: int = 5
    max_overflow: int = 10


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    format: str = "json"
    file: str = "logs/tumbler.log"


@dataclass
class VerificationConfig:
    """Sandboxed verification configuration."""

    sandbox_enabled: bool = True
    timeout_install: int = 120
    timeout_build: int = 120
    timeout_test: int = 120
    timeout_lint: int = 60
    memory_limit: str = "1g"
    cpu_limit: float = 1.0
    tmpfs_size: str = "256m"
    network_install: bool = True
    network_verify: bool = False


@dataclass
class WorkspaceConfig:
    """Workspace configuration."""

    base_path: str = "./projects"
    auto_archive: bool = True


@dataclass
class Config:
    """Main configuration for Code Tumbler."""

    active_provider: str = "ollama_local"
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)
    agent_providers: Dict[str, str] = field(default_factory=dict)
    agent_nothink: Dict[str, Optional[bool]] = field(default_factory=dict)
    tumbler: TumblerConfig = field(default_factory=TumblerConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)

    def get_provider_config(self, provider_name: Optional[str] = None) -> ProviderConfig:
        """Get configuration for a specific provider.

        Args:
            provider_name: Name of the provider. If None, returns active provider.

        Returns:
            ProviderConfig for the specified provider.

        Raises:
            KeyError: If provider not found.
        """
        name = provider_name or self.active_provider
        if name not in self.providers:
            raise KeyError(f"Provider '{name}' not found in configuration")
        return self.providers[name]

    def get_agent_provider(self, agent_name: str) -> ProviderConfig:
        """Get provider configuration for a specific agent.

        Args:
            agent_name: Name of the agent (architect, engineer, verifier).

        Returns:
            ProviderConfig for the agent's configured provider.
        """
        provider_name = self.agent_providers.get(agent_name, self.active_provider)
        return self.get_provider_config(provider_name)


def resolve_agent_provider(
    config: Config,
    agent_name: str,
    project_overrides: Optional[Dict[str, str]] = None,
) -> ProviderConfig:
    """Resolve which provider an agent should use, considering project overrides.

    Priority: project_overrides[agent_name] > config.agent_providers[agent_name] > config.active_provider

    Args:
        config: Global Config object
        agent_name: "architect", "engineer", or "verifier"
        project_overrides: Per-project overrides dict (from state.json)

    Returns:
        ProviderConfig for the resolved provider
    """
    if project_overrides and agent_name in project_overrides:
        provider_name = project_overrides[agent_name]
    else:
        provider_name = config.agent_providers.get(agent_name, config.active_provider)
    return config.get_provider_config(provider_name)


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from YAML file and environment variables.

    Args:
        config_path: Path to config.yaml file. If None, looks in default locations.

    Returns:
        Loaded Config object.

    Raises:
        FileNotFoundError: If config file not found.
        yaml.YAMLError: If config file is invalid.
    """
    # Load environment variables
    load_dotenv()

    # Find config file
    if config_path is None:
        # Try default locations
        possible_paths = [
            Path("config.yaml"),
            Path("backend/config.yaml"),
            Path("../config.yaml"),
        ]
        config_path = None
        for path in possible_paths:
            if path.exists():
                config_path = path
                break

        if config_path is None:
            raise FileNotFoundError("config.yaml not found in default locations")

    # Load YAML
    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)

    # Parse provider configurations
    providers = {}
    for name, provider_data in data.get('providers', {}).items():
        # Load API key from environment if not in config
        api_key = None
        provider_type = ProviderType(provider_data['type'])

        # Check if there's a custom API key environment variable name
        if 'api_key_env' in provider_data:
            api_key = os.getenv(provider_data['api_key_env'])
        elif provider_type == ProviderType.OPENAI:
            api_key = os.getenv('OPENAI_API_KEY')
        elif provider_type == ProviderType.ANTHROPIC:
            api_key = os.getenv('ANTHROPIC_API_KEY')

        # Override base_url from environment if set
        base_url = provider_data.get('base_url')
        if provider_type == ProviderType.VLLM:
            base_url = os.getenv('VLLM_BASE_URL', base_url)
        elif provider_type == ProviderType.OLLAMA:
            base_url = os.getenv('OLLAMA_BASE_URL', base_url)

        providers[name] = ProviderConfig(
            name=name,
            type=provider_type,
            base_url=base_url,
            api_key=api_key,
            model=provider_data.get('model', ''),
            cost_per_1k_input_tokens=provider_data.get('cost_per_1k_input_tokens', 0.0),
            cost_per_1k_output_tokens=provider_data.get('cost_per_1k_output_tokens', 0.0),
            temperature=provider_data.get('temperature', 0.7),
            max_tokens=provider_data.get('max_tokens'),
            timeout=provider_data.get('timeout', 300),
            context_length=provider_data.get('context_length'),
            nothink=provider_data.get('nothink'),
            concurrency_limit=provider_data.get('concurrency_limit', 4),
            retry_max_attempts=provider_data.get('retry_max_attempts', 3),
            retry_base_delay=float(provider_data.get('retry_base_delay', 1.0)),
            extra_params=provider_data.get('extra_params', {})
        )

    # Parse tumbler config (single source: config.yaml)
    tumbler_data = data.get('tumbler', {})
    tumbler = TumblerConfig(
        max_iterations=tumbler_data.get('max_iterations', 10),
        quality_threshold=tumbler_data.get('quality_threshold', 8.0),
        project_timeout=tumbler_data.get('project_timeout', 3600),
        debounce_time=tumbler_data.get('debounce_time', 3),
        max_cost_per_project=tumbler_data.get('max_cost_per_project', 0.0)
    )

    # Parse database config (DATABASE_URL env var overrides yaml for Docker networking)
    db_data = data.get('database', {})
    database = DatabaseConfig(
        url=os.getenv('DATABASE_URL', db_data.get('url', 'postgresql://tumbler:changeme@localhost:5432/tumbler')),
        pool_size=db_data.get('pool_size', 5),
        max_overflow=db_data.get('max_overflow', 10)
    )

    # Parse logging config (single source: config.yaml)
    log_data = data.get('logging', {})
    logging_config = LoggingConfig(
        level=log_data.get('level', 'INFO'),
        format=log_data.get('format', 'json'),
        file=log_data.get('file', 'logs/tumbler.log')
    )

    # Parse verification config
    verify_data = data.get('verification', {})
    verification = VerificationConfig(
        sandbox_enabled=verify_data.get('sandbox_enabled', True),
        timeout_install=verify_data.get('timeout_install', 120),
        timeout_build=verify_data.get('timeout_build', 60),
        timeout_test=verify_data.get('timeout_test', 60),
        timeout_lint=verify_data.get('timeout_lint', 30),
        memory_limit=verify_data.get('memory_limit', '1g'),
        cpu_limit=float(verify_data.get('cpu_limit', 1.0)),
        tmpfs_size=verify_data.get('tmpfs_size', '256m'),
        network_install=verify_data.get('network_install', True),
        network_verify=verify_data.get('network_verify', False),
    )

    # Parse workspace config
    ws_data = data.get('workspace', {})
    workspace = WorkspaceConfig(
        base_path=ws_data.get('base_path', './projects'),
        auto_archive=ws_data.get('auto_archive', True)
    )

    # Build Config object
    config = Config(
        active_provider=data.get('active_provider', 'ollama_local'),
        providers=providers,
        agent_providers=data.get('agent_providers', {}),
        agent_nothink=data.get('agent_nothink', {}),
        tumbler=tumbler,
        verification=verification,
        database=database,
        logging=logging_config,
        workspace=workspace
    )

    return config
