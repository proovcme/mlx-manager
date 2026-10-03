# MLX Manager

[Русская документация](README.ru.md)

A local workbench for MLX models on Apple Silicon: discover installed models,
start one, chat with streaming, or generate images with visible progress.
The control plane uses Python's standard library and listens on loopback only.
Models and inference engines remain separate installations.

## Run

Requirements: macOS on Apple Silicon, Python 3.12+, and a locally installed
[oMLX](https://github.com/jundot/omlx) or
[MLX LM](https://github.com/ml-explore/mlx-lm). The manager discovers executables
on PATH, in Homebrew, and in existing `uv tool` environments. It never downloads
models or starts one automatically at login.

```sh
python3.12 server.py
```

Open **http://127.0.0.1:1924/**. Pick a model, select Auto/oMLX/MLX LM, and click
Start. Text models use chat; Qwen Image switches to image controls. Only one
heavy workload is managed at a time. Models with vision configurations require
oMLX; the current composer accepts text prompts, not image attachments.

For an optional per-user login service, stop a manually running manager first:

```sh
python3.12 install_service.py
```

The generated LaunchAgent uses the current checkout and interpreter; no machine
paths are committed. Existing LaunchAgents are never overwritten.

## Everyday work

- **Chats:** streaming text, a reasoning disclosure when the engine emits it,
  prompt-processing/thinking/answering states, elapsed time, and interruption.
  New Chat keeps previous conversations in the history selector.
- **Persistence:** chats, partial replies, drafts, system prompts and parameters
  are saved locally. An explicit saving/error indicator and retry button expose
  failures. Concurrent tabs cannot silently overwrite a newer saved revision.
- **Images:** preparation, encoder/transformer loading, denoising step/total,
  cache counters, decoding, elapsed time, and cancellation. Completed images
  appear in a gallery with PNG download and parameter reuse. Older images
  without recorded prompts remain visible; their parameters cannot be recovered.
- **Recovery:** after a service restart, the manager can reattach to its recorded
  model or image worker only when PID, birth time, command and process group
  still match. It reacquires the heavy-workload lock. Unrecognized processes
  are reported as conflicts and are never stopped by the manager. A chat stream
  interrupted by a page or service restart retains its last saved partial reply;
  it does not resume token generation automatically.
- **Delete:** preview the exact folder and size, type its name, then move it to
  same-volume Trash. HF cache deletion includes blobs, refs and all snapshots of
  that model. Active jobs and heavy-lock reservations block deletion. Restore
  by moving the folder back to its original location. oMLX KV caches remain;
  space is freed when the user empties Trash.

Local data lives in `~/.local/share/mlx-manager`: private SQLite workspace,
worker state, logs and `outputs/`. Conversations and image prompts are stored
there by design. Data stays on this computer; it is not part of this repository.
Back up this directory together with any custom configuration. The service
has no authentication and is intended for a trusted, single-user local machine.
Do not expose its port to a network.

## Models and configuration

Default discovery roots: the Hugging Face cache (`HF_HUB_CACHE` / `HF_HOME`
are respected), `~/models`, `~/.omlx/models`, and `~/.lmstudio/models`.
Incomplete shard sets and embedding models are excluded. Symlink aliases share
one model identity. Scanning is bounded to model directories, not documents.

Create an ignored `config.local.json` in this checkout for extra roots or an
image engine installation. Values may use `~`:

```json
{
  "MODEL_ROOTS": ["~/extra-models"],
  "IMAGE_ROOT": "~/tools/mlx-image-kit",
  "IMAGE_PYTHON": "~/tools/mlx-image-kit/.venv/bin/python"
}
```

Image generation uses the existing
[MLX Image Kit](https://github.com/proovcme/mlx-image-kit) checkout and its Python
environment. The default location is a sibling `mlx-image-kit` directory.
A complete cached `mlx-community/Qwen-Image-2.1-MLX-4bit` snapshot is required.
The tested image integration is Image Kit 0.5.0; loading, cache, sampling and
numerical behavior are delegated unchanged to that engine.
`IMAGE_CACHE` can override the HF model repository directory.

Path overrides also support `MLX_MANAGER_<NAME>` environment variables, such
as `MLX_MANAGER_DATA_ROOT` and `MLX_MANAGER_IMAGE_ROOT`. Environment values take
precedence over the local JSON file. `MODEL_ROOTS` is a JSON-only list.

An optional legacy ExternalChat/ChatProxy profile can be configured through `EXTERNAL_CHAT_MODEL`,
`EXTERNAL_CHAT_LABEL`, `EXTERNAL_CHAT_PLIST`, `EXTERNAL_CHAT_LOG`, `CHAT_PROXY_LABEL` and `CHAT_PROXY_PLIST`.
Its local ports are 1926 and 1927; the stable proxied routes are `/v1/models`,
`/v1/chat/completions` and `/chat_proxy/sessions`. This integration is optional:
ordinary models run through the managed inference server on port 1925.

## CLI and checks

```sh
python3.12 cli.py status
python3.12 cli.py mode image
python3.12 cli.py image 'A blue ceramic cup' --width 512 --height 512 --steps 4
python3.12 cli.py job
python3.12 cli.py cancel
python3.12 cli.py mode idle
python3.12 -m unittest discover -s tests -v
node --check static/app.js
node tests/test_stream_parser.js
```

There is no frontend build step. Node is only needed for the stream-parser test.
The control plane imports no MLX libraries. Direct inference launches outside
this manager can bypass its file lock; conflict detection is a guard, not OS
resource isolation. Interrupting a text stream closes the client connection;
how promptly inference stops depends on the selected engine.

`ci/checks.yml.example` is an optional GitHub Actions template. Copy it to
`.github/workflows/checks.yml` with repository workflow permissions to enable
the same checks on macOS CI. It is not installed automatically.
