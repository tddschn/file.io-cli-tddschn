# Project Guidelines

## Code Style
- Keep behavior-first compatibility for the CLI in `file_io_cli_tddschn/cli.py`.
- Keep stdout/stderr semantics stable: progress and diagnostics on stderr, final link(s) on stdout.
- Preserve existing CLI flags and entry point names (`file.io-cli` and `file.io`).

## Architecture
- Main entry points are defined in `pyproject.toml` and call `main_sync()` in `file_io_cli_tddschn/cli.py`.
- `main()` orchestrates argument parsing, input source setup (file/stdin/tar), multipart upload, and response rendering.
- Upload plumbing and terminal UX are implemented in `file_io_cli_tddschn/cli.py` via:
  - `MultipartFileEncoder`
  - `GeneratorFileReader`
  - `FileMonitor`
  - `ProgressDisplay`
- README content is generated from templates in `templates/` by `scripts/gen_readme.py`.

## Build and Test
- Use uv commands for development tasks. Ignore legacy Poetry/Make targets unless explicitly requested.
- Basic CLI check: `uv run file.io-cli --help`
- Run module entrypoint: `uv run python -m file_io_cli_tddschn.cli --help`
- Regenerate README from templates: `uv run --with-editable . scripts/gen_readme.py`
- Format Python files: `ruff format file_io_cli_tddschn/*.py scripts/*.py`
- Current repo has no automated test suite.

## Conventions
- Do not edit `README.md` directly for usage text changes. Update templates in `templates/` and regenerate.
- Keep streaming upload behavior memory-efficient (prefer generators/iterators over full buffering).
- For API behavior changes, keep verbose mode (`-v`) useful by surfacing response diagnostics.
