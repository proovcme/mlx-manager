# Model and runtime discovery

Model paths are local configuration, not machine-specific values embedded in code. The manager reads these sources:

- `MODEL_ROOTS` in ignored `config.local.json`, or `MLX_MANAGER_MODEL_ROOTS` as a JSON array. Environment configuration takes precedence.
- Hugging Face: `HF_HUB_CACHE`, then `HUGGINGFACE_HUB_CACHE`, then `HF_HOME/hub`, then `XDG_CACHE_HOME/huggingface/hub` (default cache home is `~/.cache`). Repository caches work even when the cache folder is not named `hub`.
- oMLX: `OMLX_BASE_PATH`, the macOS application's `Library/Application Support/oMLX/base-path` bootstrap file, and `settings.json` under that base directory. Both `model.model_dirs` and legacy `model.model_dir` are supported. `OMLX_MODEL_DIR` can contain comma-separated directories.
- MLX/oMLX user LaunchAgents: model paths from `ProgramArguments`, runtime environment variables, and absolute or working-directory-relative model paths. oMLX `--model-dir` overrides the launch environment and saved settings; `--base-path` selects its settings directory. MLX LM `--model` can point directly to a local model.
- Running MLX/oMLX command lines containing explicit model directory arguments. Process environment and working directories are not guessed; unquoted paths containing spaces may not be recoverable from `ps`. Use settings, LaunchAgents, or explicit manager roots for those paths.
- Standard `~/models`, the effective oMLX base directory's `models` folder when no model directories are configured, and `~/.lmstudio/models` as fallbacks. The optional locally configured model profile is also included.

For an arbitrary model location:

```sh
export MLX_MANAGER_MODEL_ROOTS='["/path/to/models", "/path/with spaces/models"]'
```

Or use the ignored local configuration:

```json
{
  "MODEL_ROOTS": ["~/extra-models", "/path/to/models"]
}
```

Restart the manager when changing its own environment or local JSON configuration. Press the catalog refresh button to reread runtime settings and LaunchAgents. Paths are expanded, resolved, checked for existence, and deduplicated, including symlink aliases. Model identity remains based on the resolved model path, preserving existing chat associations.

Runtime executables are found through `PATH`, recognized MLX/oMLX LaunchAgents, uv tool environments (`UV_TOOL_DIR` or `XDG_DATA_HOME/uv/tools`), and the standard local bin directory. `MLX_MANAGER_OMLX_EXECUTABLE` and `MLX_MANAGER_MLX_EXECUTABLE` override the executable candidates; the same keys without the prefix work in local JSON configuration. No fixed Homebrew installation prefix is required.

Discovery reads configuration only; it does not execute LaunchAgents or settings, import model runtimes, download weights, or traverse the whole home directory. Model directory traversal remains bounded to three directory levels; HF repositories use their cached `refs/main` snapshot. Incomplete shard sets and embedding models remain excluded. Unreadable or malformed third-party configuration is skipped; malformed explicit manager `MODEL_ROOTS` configuration produces an error instead of silently ignoring it.
