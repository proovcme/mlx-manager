"""Small CLI for the local UZEL; no heavy model starts on import."""

import argparse
import json
import sys
import urllib.error
import urllib.request

import config


BASE = f"http://{config.MANAGER_HOST}:{config.MANAGER_PORT}"


def call(method: str, path: str, data: dict | None = None) -> dict:
    payload = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(BASE + path, data=payload, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=240 if path == "/api/mode" else 15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("error", str(exc))
        except ValueError:
            message = str(exc)
        raise SystemExit(f"uzel: {message}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"uzel: service unavailable at {BASE}: {exc.reason}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(prog="uzel")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    mode = sub.add_parser("mode")
    mode.add_argument("target", choices=("idle", "external_chat", "image"))
    image = sub.add_parser("image")
    image.add_argument("prompt")
    image.add_argument("--width", type=int, default=1024)
    image.add_argument("--height", type=int, default=1024)
    image.add_argument("--steps", type=int, default=30)
    image.add_argument("--seed", type=int, default=42)
    image.add_argument("--cache", choices=("off", "balanced"), default="balanced")
    sub.add_parser("job")
    sub.add_parser("cancel")
    logs = sub.add_parser("logs")
    logs.add_argument("service", choices=("external_chat", "image", "manager"))
    args = parser.parse_args()
    if args.command == "status":
        result = call("GET", "/api/status")
    elif args.command == "mode":
        result = call("POST", "/api/mode", {"mode": args.target})
    elif args.command == "image":
        result = call("POST", "/api/image/jobs", {
            "prompt": args.prompt, "width": args.width, "height": args.height,
            "steps": args.steps, "seed": args.seed, "cache": args.cache})
    elif args.command == "job":
        result = call("GET", "/api/image/job")
    elif args.command == "cancel":
        result = call("POST", "/api/image/cancel", {})
    else:
        result = call("GET", f"/api/logs?service={args.service}")
        print(result["text"], end="")
        return
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
