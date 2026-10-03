# Optional external chat service

Clean installations use the runtime discovered in the catalog. No particular model,
proxy, service label or private model directory is built into the public configuration.
An existing per-user macOS LaunchAgent can be explicitly connected instead:

```json
{
  "EXTERNAL_ENABLED": true,
  "EXTERNAL_MODEL": "~/models/example-chat-model",
  "EXTERNAL_MODEL_ID": "example-chat-model",
  "EXTERNAL_ENDPOINT": "http://127.0.0.1:1926",
  "EXTERNAL_LABEL": "org.example.chat",
  "EXTERNAL_PLIST": "~/Library/LaunchAgents/org.example.chat.plist"
}
```

Save this in ignored `config.local.json` and restart the manager. The catalog marks
that exact resolved model path as externally managed. Its model identity and chats
keep the same IDs. Start and stop verify that the listening PID belongs to the
configured LaunchAgent; active connections prevent unloading.

An optional local proxy uses `PROXY_ENDPOINT`, `PROXY_LABEL`, and `PROXY_PLIST`.
Without a separate proxy the external OpenAI-compatible service is called directly.
Endpoints must be loopback HTTP origins. No credentials are required by the adapter.

Provider-specific session integration is opt-in through `SESSION_PATH` and
`SESSION_HEADER`. The frontend uses generic `/api/chat/sessions` routes; the adapter
maps them to the configured upstream path. Leave both empty for ordinary stateless
OpenAI chat. `STATS_PATH` optionally reads proxy statistics. Session creation must
accept a JSON object and return `session_id`; this is an adapter contract, not a
claim that every OpenAI-compatible server implements sessions.

Environment overrides use `MLX_MANAGER_` plus the same setting name. The service
installer preserves supported path/runtime variables and these adapter settings,
but does not copy arbitrary credentials from the terminal environment.
