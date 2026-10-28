#!/usr/bin/env python3
"""Mock stand-in for the TeaStore image provider, used to dry-run the measurement
harness before pointing it at the real Docker Compose stack.

It speaks the same REST contract as the real service, so switching from the mock to
the real run changes only the --target URL:

    POST /rest/image/getProductImages   {"<productId>": "<width>x<height>"}
    GET  /rest/image/state              text/plain, incl. rendered-cache counters
    POST /rest/image/setCacheSize       new byte budget

The cache is byte-budgeted with LRU eviction, mirroring the real ImageProvider, so a
mock size sweep exercises the same eviction behaviour the real one will.

Render costs are simulated with sleeps and payload sizes are synthetic. Neither is a
model of the real service; only the request/response shape is faithful.
"""

import argparse
import base64
import json
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

RENDER_SECONDS = 0.008
CACHE_HIT_SECONDS = 0.0003
DEFAULT_CACHE_BYTES = 300 * 1024
# Product ids in TeaStore's seeded database; any other id gets the placeholder image,
# as the real service does, so measure.py's id range check behaves the same here.
FIRST_PRODUCT_ID = 7
PRODUCT_COUNT = 500


class ImageStub:
    """Byte-budgeted LRU cache of rendered images with the counters the real service exposes."""

    def __init__(self, cache_enabled, max_bytes):
        self.cache_enabled = cache_enabled
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.used_bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get_product_image(self, product_id, size):
        key = f"{product_id}:{size[0]}x{size[1]}"
        with self.lock:
            if self.cache_enabled and key in self.cache:
                self.cache.move_to_end(key)
                self.hits += 1
                payload = self.cache[key]
                hit = True
            else:
                self.misses += 1
                hit = False

        if hit:
            time.sleep(CACHE_HIT_SECONDS)
            return payload

        time.sleep(RENDER_SECONDS)
        known = FIRST_PRODUCT_ID <= product_id < FIRST_PRODUCT_ID + PRODUCT_COUNT
        prefix = f"product {product_id}:" if known else "notFound:"
        payload = base64.b64encode(prefix.encode() + b"x" * (size[0] * size[1] // 8)).decode()

        if self.cache_enabled:
            with self.lock:
                if key not in self.cache:
                    self.cache[key] = payload
                    self.used_bytes += len(payload)
                    while self.used_bytes > self.max_bytes and len(self.cache) > 1:
                        _, evicted = self.cache.popitem(last=False)
                        self.used_bytes -= len(evicted)
                        self.evictions += 1
        return payload

    def set_cache_size(self, max_bytes):
        with self.lock:
            self.max_bytes = max_bytes
            while self.used_bytes > max_bytes and len(self.cache) > 1:
                _, evicted = self.cache.popitem(last=False)
                self.used_bytes -= len(evicted)
                self.evictions += 1
        return True

    def state_text(self):
        """Reproduce the counter lines SetupController.getState() appends."""
        with self.lock:
            total = self.hits + self.misses
            hit_rate = f"{(100.0 * self.hits) / total:.2f}%" if total else "n/a"
            return "\n".join([
                f"Rendered Image Cache Entries: {len(self.cache)}",
                f"Rendered Image Cache Size (Bytes): {self.used_bytes} / {self.max_bytes}",
                f"Rendered Image Cache Hits: {self.hits}",
                f"Rendered Image Cache Misses: {self.misses}",
                f"Rendered Image Cache Hit Rate: {hit_rate}",
                f"Rendered Image Cache Evictions: {self.evictions}",
            ]) + "\n"


def make_handler(stub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if urlparse(self.path).path == "/rest/image/state":
                self._respond(stub.state_text().encode(), "text/plain")
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if path == "/rest/image/getProductImages":
                requested = json.loads(body)
                images = {}
                for product_id, raw_size in requested.items():
                    width, _, height = raw_size.partition("x")
                    images[product_id] = stub.get_product_image(
                        int(product_id), (int(width), int(height)))
                self._respond(json.dumps(images).encode(), "application/json")
            elif path == "/rest/image/setCacheSize":
                self._respond(json.dumps(stub.set_cache_size(int(body))).encode(),
                              "application/json")
            else:
                self.send_error(404)

        def _respond(self, payload, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--cache", choices=["on", "off"], default="off",
                        help="mirrors the runtime cache toggle of the real service")
    parser.add_argument("--cache-size", type=int, default=DEFAULT_CACHE_BYTES,
                        help="cache byte budget")
    args = parser.parse_args()

    stub = ImageStub(cache_enabled=args.cache == "on", max_bytes=args.cache_size)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(stub))
    print(f"stub image provider on http://127.0.0.1:{args.port}  "
          f"cache={args.cache} budget={args.cache_size} B")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n" + stub.state_text())


if __name__ == "__main__":
    main()
