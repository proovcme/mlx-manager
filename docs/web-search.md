# Web search in chat

Enable **Поиск в интернете** above the chat composer. The manager searches for the latest user message, sends up to five titles and snippets to the selected local text model, and streams its response. The **Источники** section shows clickable URLs, excerpts and the exact search query. Sources are saved with the response and remain available when reopening the conversation.

Search is off by default and only appears for text models. Only the latest message is sent to the search provider; previous conversation turns and the system prompt are not sent to it. A search message must contain 1–500 characters. This is search-result context, not full-page reading. The model is instructed to cite sources by number and acknowledge missing evidence. Citations are model-generated and need judgment; the source list contains the actual returned URLs.

## Setup

The manager remains standard-library-only. Install the optional search worker in a separate environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r manager/requirements-web.txt
```

Restart the manager after changing its configuration. It detects `.venv/bin/python` at the repository root or inside `manager/`; alternatively set `MLX_MANAGER_WEB_PYTHON` or `WEB_PYTHON` in the ignored `config.local.json`.

The worker uses [DDGS](https://github.com/deedy5/ddgs). The default backend is `yandex`; override it with `MLX_MANAGER_WEB_BACKEND` or `WEB_BACKEND` in local configuration. DDGS supports other search backends, but their availability depends on network access and provider restrictions. No paid API key is required for the default backend. Do not put private configuration in Git.

## Behavior and boundaries

Search and model generation share the chat request lifecycle. Progress displays searching, processing retrieved excerpts, thinking and answering. **Прервать ответ** also stops an in-flight search worker. A worker has a 20-second overall deadline, independent of provider timeouts. On search failure the manager reports the failure instead of quietly answering without search.

Retrieved text is explicitly marked as untrusted data. Only HTTP(S) source URLs without embedded credentials are retained; links and snippets render as text, not HTML. The manager does not fetch arbitrary result pages, execute page instructions, or expose shell/model-management tools to retrieved content. Context is added as a leading message, preserving the original conversation turns for history/checkpoint adapters.

The SSE API accepts `web_search: true` and emits metadata events with `type: "web_search"`, first `phase: "searching"`, then `phase: "ready"` including `query`, `provider`, `searched_at` and `sources`. Normal OpenAI-style delta events follow. Non-streaming chat returns the same source metadata in the `web_search` field. Existing clients without the option keep their original behavior.
