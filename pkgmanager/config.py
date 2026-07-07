"""
Optional user configuration for pkgmanager (~/.config/pkgmanager/config.json).

This is a convenience layer only -- PackageManager() works fine with no
config file at all; from_config() is just a shortcut for reading one.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CONFIG_PATH = Path(os.path.expanduser('~/.config/pkgmanager/config.json'))

DEFAULT_CONFIG: Dict[str, Any] = {
    'force_helper': None,
    'use_noconfirm': True,
    'permission_method': 'auto',  # 'auto', 'pkexec', or 'sudo'
}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load user configuration, falling back to defaults for anything missing.
    A missing or malformed config file is not an error -- config is a
    convenience, not a hard requirement -- so both cases just fall back to
    DEFAULT_CONFIG rather than raising.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    config = dict(DEFAULT_CONFIG)
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as fh:
                user_config = json.load(fh)
            if isinstance(user_config, dict):
                config.update(user_config)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: Dict[str, Any], path: Optional[Path] = None) -> None:
    config_path = path or DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as fh:
        json.dump(config, fh, indent=2)
