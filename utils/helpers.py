"""
Utility functions for CLI Gateway
"""
import os
import re
import yaml
from typing import Any, Dict


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Load configuration from YAML file with environment variable substitution
    
    Supports ${VAR_NAME} syntax for environment variables
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config_str = f.read()
    
    # Replace environment variables
    def replace_env(match):
        var_name = match.group(1)
        value = os.getenv(var_name)
        if value is None:
            raise ValueError(f"Environment variable {var_name} is not set")
        return value
    
    config_str = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', replace_env, config_str)
    
    config = yaml.safe_load(config_str)
    return config


def sanitize_session_id(session_id: str) -> str:
    """Validate and sanitize session ID (prevent path traversal)"""
    if not re.match(r'^[a-f0-9]{8}$', session_id):
        raise ValueError(f"Invalid session ID format: {session_id}")
    return session_id


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to max_length, preserving word boundaries if possible"""
    if len(text) <= max_length:
        return text
    
    truncated = text[:max_length - len(suffix)]
    # Try to truncate at last newline or space
    last_newline = truncated.rfind('\n')
    last_space = truncated.rfind(' ')
    
    if last_newline > max_length * 0.8:
        truncated = truncated[:last_newline]
    elif last_space > max_length * 0.8:
        truncated = truncated[:last_space]
    
    return truncated + suffix
