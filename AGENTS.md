# Repository notes for agents

## Python environment

This project uses **uv2nix**. The environment is managed by Nix — do not use pip
or create virtualenvs manually.

**Run Python:**

```bash
direnv allow   # first time
python scripts/sync.py --resolve-only
```

Outside an interactive shell:

```bash
direnv exec . python scripts/sync.py --resolve-only
```

**Add a dependency:**

1. Add it to `[project.dependencies]` in `pyproject.toml`.
2. Run:

   ```bash
   UV_PYTHON="" UV_PYTHON_DOWNLOADS=never uv lock
   ```

3. Rebuild the shell:

   ```bash
   touch flake.nix && direnv reload
   ```

**Do not** run `uv sync`, `pip install`, or `python -m venv`. The venv is built by
Nix and is read-only.

The GitHub Actions workflows use `uv sync --frozen` from the same `pyproject.toml`
and `uv.lock`, so CI and local runs resolve the same dependency graph.
