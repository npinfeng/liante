"""Reference AOI producer: replace the file iterator with a camera frame source."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Push consecutive AOI frames with backpressure")
    parser.add_argument("--base-url", required=True, help="Example: http://10.254.7.25:8000")
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--frames-dir", type=Path, required=True, help="Ordered frame directory for integration testing")
    parser.add_argument("--fps", type=float, default=5.0)
    args = parser.parse_args()
    if args.fps <= 0:
        parser.error("--fps must be positive")
    files = sorted(path for path in args.frames_dir.iterdir()
                   if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"})
    if not files:
        parser.error("--frames-dir contains no image frames")

    base = args.base_url.rstrip("/")
    session = requests.Session()
    response = session.post(f"{base}/api/v1/aoi/streams",
                            json={"product_id": args.product_id, "source_id": args.source_id}, timeout=15)
    response.raise_for_status()
    task_id = response.json()["data"]["task_id"]
    endpoint = f"{base}/api/v1/aoi/streams/{task_id}"
    print(f"stream task_id={task_id}")
    next_send = time.monotonic()
    try:
        for sequence, path in enumerate(files, 1):
            delay = next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            frame = path.read_bytes()
            while True:
                response = session.post(f"{endpoint}/frames",
                                        data={"sequence": str(sequence), "product_id": args.product_id},
                                        files={"image": (path.name, frame)}, timeout=30)
                if response.status_code == 429:
                    retry_after = max(0.1, float(response.headers.get("Retry-After", "1")))
                    print(f"backpressure frame={sequence}, retry in {retry_after}s")
                    time.sleep(retry_after)
                    continue
                response.raise_for_status()
                print(f"accepted frame={sequence} queue={response.json()['data']['stream']['queue_depth']}")
                break
            next_send = max(next_send + 1 / args.fps, time.monotonic())
    finally:
        close = session.post(f"{endpoint}/close", timeout=15)
        print(f"close status={close.status_code}")
        print(f"stream status: {endpoint}")


if __name__ == "__main__":
    main()
