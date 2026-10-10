# Remote access

The manager supports a path prefix behind an authenticated reverse proxy. It still listens on localhost; the gateway supplies authentication and HTTPS.

For a deployment at `/mlx/`, replace the HTML meta value `uzel-base` with `/mlx` in the gateway response. The CSS and JavaScript use relative URLs; API calls, streaming chat, image previews and downloads use the same prefix.

The gateway must authenticate **every** request, including API calls, static assets and generated images. Validate the public Origin for mutations, then remove Origin, Cookie and Authorization before forwarding. Supply the manager's localhost Host header. Keep response buffering disabled for SSE and allow long responses. Propagate client disconnects to the upstream.

A reverse SSH tunnel can connect a private Mac to a VPS. Use a dedicated key and account with only remote forwarding to one loopback port, no shell, and server/client keepalives. A local LaunchAgent can reconnect after network interruptions.

A PWA manifest can open the manager directly. A service worker must bypass the entire manager prefix: private history, prompts and images must not enter its offline cache. Model inference and history remain on the Mac; the VPS transports requests and responses. The Mac and manager must be running.

The local manager has no authentication for direct Internet exposure. Deploy the authentication gateway and private connection together.
