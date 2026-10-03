"""Bounded local model/runtime discovery. No imports of MLX, downloads or scans of user documents."""
import hashlib
import json
import os
import plistlib
import shutil
from pathlib import Path

import config


def runtimes():
    candidates = {"omlx": [], "mlx": []}
    try:
        plist = plistlib.loads(config.MARA_PLIST.read_bytes())
        candidates["omlx"].append(Path(plist["ProgramArguments"][0]))
    except (OSError, ValueError, KeyError, IndexError):
        pass
    for engine, executable in (("omlx", "omlx"), ("mlx", "mlx_lm.server")):
        found = shutil.which(executable)
        if found:
            candidates[engine].append(Path(found))
        candidates[engine] += list((Path.home() / ".local/share/uv/tools").glob(f"*/bin/{executable}"))
        candidates[engine] += [Path.home() / ".local/bin" / executable, Path("/opt/homebrew/bin") / executable]
    result = {}
    for engine, paths in candidates.items():
        executable = next((p for p in paths if p.is_file() and os.access(p, os.X_OK)), None)
        if executable:
            result[engine] = {"id": engine, "name": "oMLX" if engine == "omlx" else "MLX LM",
                              "executable": str(executable)}
    return result


def roots():
    home = Path.home()
    hub = Path(os.environ.get("HF_HUB_CACHE", str(Path(os.environ.get("HF_HOME", str(home / ".cache/huggingface"))) / "hub")))
    paths = [hub, home / "models", home / ".omlx/models", home / ".lmstudio/models", *config.MODEL_ROOTS]
    return list(dict.fromkeys(p for p in paths if p.is_dir()))


def model_info(path, name=None):
    path = path.resolve()
    try:
        settings = json.loads((path / "config.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(settings, dict):
        return None
    kind = settings.get("model_type", "")
    architectures = settings.get("architectures", [])
    if ("embedding" in (name or path.name).lower() or kind in ("bert", "roberta", "xlm-roberta", "qwen3_embedding")
            or any("Embedding" in a for a in architectures if isinstance(a, str))):
        return None
    weights = list(path.glob("*.safetensors"))
    if not weights or not (path / "tokenizer_config.json").is_file():
        return None
    try:
        indices = list(path.glob("*.safetensors.index.json"))
        for index in indices:
            mapping = json.loads(index.read_text()).get("weight_map", {})
            if not mapping or any(not (path / shard).is_file() for shard in set(mapping.values())):
                return None
        size = sum(p.stat().st_size for p in weights)
    except (OSError, ValueError, TypeError):
        return None
    vision = (bool(settings.get("vision_config")) or "vl" in kind.lower()) and not any(marker in path.name.lower() for marker in ("text-only", "textonly"))
    return {"id": hashlib.sha256(str(path).encode()).hexdigest()[:16], "name": name or path.name,
            "path": str(path), "kind": "chat", "model_type": kind, "vision": vision,
            "weight_bytes": size, "backends": ["omlx"] if vision else ["omlx", "mlx"],
            "mara": path == config.MARA_MODEL.resolve()}


def discover():
    engines = runtimes()
    models = {}
    for root in roots():
        if root.name == "hub":
            candidates = []
            for repo in sorted(root.glob("models--*")):
                try:
                    revision = (repo / "refs/main").read_text().strip()
                    if not revision or "/" in revision or ".." in revision:
                        continue
                    candidates.append((repo / "snapshots" / revision, repo.name[8:].replace("--", "/")))
                except OSError:
                    continue
        else:
            candidates = [(root, root.name)]
            for level in ("*", "*/*", "*/*/*"):
                candidates.extend((p, p.name) for p in root.glob(level) if p.is_dir())
        for path, name in candidates:
            item = model_info(path, name)
            if item:
                item["backends"] = [b for b in item["backends"] if b in engines]
                item["available"] = bool(item["backends"])
                if item["mara"]:
                    item["name"] = "Mara"
                models[item["id"]] = item
    snapshot = config.image_snapshot()
    if snapshot:
        models["qwen-image-21"] = {"id": "qwen-image-21", "name": "Qwen Image 2.1", "kind": "image",
            "path": str(snapshot), "backends": ["mlx-image"], "available": config.IMAGE_PYTHON.is_file(),
            "weight_bytes": sum((snapshot / p / "model.safetensors").stat().st_size for p in ("text_encoder", "transformer", "vae"))}
    return {"runtimes": list(engines.values()), "models": sorted(models.values(), key=lambda m: (not m.get("mara"), m["kind"], m["name"].lower())),
            "roots": [str(p) for p in roots()]}
