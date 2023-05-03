from pathlib import Path

_REPO_ROOT_DIR = Path(__file__).parent.parent
TEMPLATE_DIR = _REPO_ROOT_DIR / 'templates'

_README_PATH = _REPO_ROOT_DIR / 'README.md'
_README_COMMANDS = ['file.io-cli']
_README_TEMPLATE = TEMPLATE_DIR / 'README.jinja.md'
_COMMAND_USAGE_TEMPLATE = TEMPLATE_DIR / 'readme-command-usage.jinja.md'
