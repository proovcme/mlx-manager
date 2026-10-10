# LES documents in MLX Manager

MLX Manager remains the chat/image interface and model owner. [LES RAG](https://github.com/proovcme/LES)
keeps document ingestion, OCR, indexing and storage. No corpus is copied into Manager.

Open **⋯ next to the message field → Документы LES**. Enter the address of the
running LES API, select **LES API**, and choose **Подключить и проверить**. If LES
requires authentication, enter its `X-API-Key` in the password field. Then choose a
user document set and enable **Использовать документы в этом чате**. Turn it off
for ordinary chat. Connection checking retrieves dataset names and factual counts;
it does not start indexing or load/unload models.

The model must support OpenAI-compatible function calls. Manager offers only
`search_sources(query)` and `read_source(source_id)`. The model chooses the query;
Manager enforces the selected dataset IDs, validates responses and retrieves
read-only evidence. `read_source` returns the surrounding context already supplied
by LES search, not the whole document. There are at most four planning rounds and
six tool operations before the final answer streams normally. An unsupported model,
unready index or failed retrieval produces an explicit error; there is no silent
answer without the requested document search. Planning responses are not displayed
as the final answer. Citation identifiers are `[D1]`, `[D2]`, etc.; links and excerpts
remain available under the response. Retrieved documents are treated as untrusted
data, not instructions. The search tool query limit is 4000 characters; this does
not impose that limit on the user's chat message.

API endpoints used in LES:

- `GET /api/documents/datasets?limit=1000`
- `POST /api/search` with explicit `dataset_ids`, `include_context` and `include_trace`
- Original-document viewer links returned as provenance.

Manager connection endpoints:

- `GET /api/rag/connection` returns settings without the key.
- `POST /api/rag/connection` stores the explicitly selected URL and transport.
- `POST /api/rag/datasets` checks the saved connection and lists user datasets.
- `POST /api/chat` accepts `rag: ["dataset-id"]` and emits `rag` SSE metadata.

The optional **MCP · Streamable HTTP** transport expects a running HTTP MCP
endpoint exposing LES `list_datasets` and `search_sources`. It initializes protocol
`2025-03-26`, handles JSON/SSE responses and session headers, and calls only those
two tools. It does not execute arbitrary stdio commands. LES's bundled
`tools/light_mcp_server.py` currently runs over **stdio**, so use the direct API
connection with the standard LES application. The HTTP MCP mode is for a separately
configured compatible gateway, not an automatic conversion of that script.
Transport contract: [MCP specification](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports).

Connection credentials are stored server-side in `DATA_ROOT/rag-connection.json`
with file mode `0600`; they are never returned to the browser or included in
workspace records. Keep the data directory private. A key is retained when saving
the same endpoint without entering a new one; changing the URL drops the old key.
Remote authenticated endpoints require HTTPS. Redirects and environment HTTP
proxies are disabled for LES requests.

Only the query chosen by the model is sent to LES; LES does not receive chat
history or system prompts. Retrieved snippets are sent to the selected chat
provider, which may be remote if explicitly configured. Original files, uploads,
indexing and embedding runtime management remain in LES. Manager does not schedule
LES's independent GPU workloads: configure its embedding service separately and
avoid heavy indexing alongside image generation on a shared GPU.

Integration tests use a synthetic HTTP LES service and synthetic model responses.
They verify API/MCP transport, source provenance, model-selected tools, dataset
scope, cancellation and errors. These tests do not establish compatibility with
an installed LES instance or a particular loaded model; verify those explicitly
once their endpoint is configured.
