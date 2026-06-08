from __future__ import annotations

import argparse
import json
import mimetypes
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import read_config_yaml


@dataclass(frozen=True)
class SidecarConfig:
    watch_dir: Path
    server_url: str
    token: str | None = None
    include_patterns: tuple[str, ...] = ("*.pdf", "*.html", "*.htm", "*.mhtml", "*.mht", "*.txt")
    settle_seconds: float = 3.0
    upload_mode: str = "http"
    poll_seconds: float = 2.0

    @classmethod
    def from_file(cls, path: Path) -> "SidecarConfig":
        raw = read_config(path)
        return cls(
            watch_dir=Path(raw.get("watch_dir", ".")).resolve(),
            server_url=str(raw["server_url"]).rstrip("/"),
            token=raw.get("token"),
            include_patterns=tuple(raw.get("include_patterns") or cls.include_patterns),
            settle_seconds=float(raw.get("settle_seconds", 3.0)),
            upload_mode=str(raw.get("upload_mode", "http")),
            poll_seconds=float(raw.get("poll_seconds", 2.0)),
        )


def read_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    parsed = read_config_yaml(path)
    if parsed and "server_url" in parsed:
        return parsed
    return read_flat_yaml(path)


def read_flat_yaml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key:
            result.setdefault(current_list_key, []).append(stripped[2:].strip().strip('"').strip("'"))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "":
            result[key] = []
            current_list_key = key
        else:
            result[key] = value.strip('"').strip("'")
            current_list_key = None
    return result


class PollingSidecar:
    def __init__(self, config: SidecarConfig):
        self.config = config
        self.seen: dict[Path, tuple[int, float]] = {}
        self.uploaded: set[Path] = set()

    def scan_once(self) -> list[dict[str, Any]]:
        now = time.time()
        results: list[dict[str, Any]] = []
        for path in self._candidate_files():
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime)
            previous = self.seen.get(path)
            self.seen[path] = signature
            if path in self.uploaded:
                continue
            if previous != signature:
                continue
            if now - stat.st_mtime < self.config.settle_seconds:
                continue
            result = upload_file(self.config, path)
            self.uploaded.add(path)
            results.append(result)
        return results

    def run_forever(self) -> None:
        while True:
            for result in self.scan_once():
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            time.sleep(self.config.poll_seconds)

    def _candidate_files(self) -> list[Path]:
        files: list[Path] = []
        for pattern in self.config.include_patterns:
            files.extend(path for path in self.config.watch_dir.rglob(pattern) if path.is_file())
        return sorted(set(files))


def upload_file(config: SidecarConfig, path: Path) -> dict[str, Any]:
    if config.upload_mode != "http":
        raise ValueError("Only upload_mode=http is currently supported")
    boundary = "scifinder-route-sidecar"
    content = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        config.server_url.rstrip("/") + "/api/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Scifinder-Route-Token": config.token or ""},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - user configured endpoint
        payload = json.loads(response.read().decode("utf-8"))
    return {"file_path": str(path), "response": payload}


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch a local SciFinder folder and upload stable files to the NAS MCP Admin API.")
    parser.add_argument("config", type=Path, help="sidecar yaml/json config")
    parser.add_argument("--once", action="store_true", help="scan once and exit")
    args = parser.parse_args()
    sidecar = PollingSidecar(SidecarConfig.from_file(args.config))
    if args.once:
        for result in sidecar.scan_once():
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        sidecar.run_forever()


if __name__ == "__main__":
    main()
