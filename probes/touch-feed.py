#!/usr/bin/env python3
"""Serve the touch test page together with a live feed of raw HID contacts.

Usage:
    touch-feed.py [PORT] [DOCROOT]

Serves, on 127.0.0.1 only:

    GET /                 index.html from DOCROOT
    GET /api/raw?since=N  {"seq": n, "reports": [...]} — reports after N
    GET /api/mark?x=&y=   record a tap position, paired with the nearest frame
    GET /api/marks        every recorded tap and its frame
    GET /api/health       {"ok": true, "reports": n}

Each report is {"seq", "t", "count", "contacts": [[id, x, y], ...]} read from
report 0x0D on /dev/hidraw0, which needs root.  The point is to put what the
firmware emitted next to what the OS delivered: the page sees pointer events
from the driver's virtual device, so any contact in this feed that never
becomes a pointer event is one the driver or the filter rejected.

Read-only with respect to the panel.  Bounded ring buffer; nothing is written
to disk.
"""
import collections
import json
import os
import select
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
DOCROOT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/touch-test"

HID = "/dev/hidraw0"
REPORT_ID = 0x0D
FINGERS = 10
BLOCK = 5
CONTACT_COUNT_OFFSET = FINGERS * BLOCK + 2      # 52
REPORT_LEN = CONTACT_COUNT_OFFSET + 1           # 53

reports = collections.deque(maxlen=400)
marks = collections.deque(maxlen=200)
seq = 0
lock = threading.Lock()
last_report_at = 0.0


def reader():
    global seq, last_report_at
    fd = os.open(HID, os.O_RDONLY)
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                continue
            data = os.read(fd, 64)
            if len(data) < 2 or data[0] != REPORT_ID:
                continue
            payload = data[1:]
            if len(payload) < REPORT_LEN:
                continue
            contacts = []
            for n in range(FINGERS):
                b0 = payload[BLOCK * n]
                if b0 & 0x01:
                    contacts.append([
                        (b0 >> 4) & 0x0F,
                        payload[BLOCK * n + 1] | (payload[BLOCK * n + 2] << 8),
                        payload[BLOCK * n + 3] | (payload[BLOCK * n + 4] << 8),
                    ])
            with lock:
                seq += 1
                reports.append({
                    "seq": seq,
                    "t": round(time.time() * 1000),
                    "count": payload[CONTACT_COUNT_OFFSET],
                    "contacts": contacts,
                })
                last_report_at = time.time()
    finally:
        os.close(fd)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            path = os.path.join(DOCROOT, "index.html")
            try:
                with open(path, "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(b"touch-test.html not found\n", "text/plain")
            return

        if url.path == "/api/raw":
            since = int(parse_qs(url.query).get("since", ["0"])[0])
            with lock:
                current = seq
                fresh = [r for r in reports if r["seq"] > since]
            body = json.dumps({"seq": current, "reports": fresh}).encode()
            self._send(body, "application/json")
            return

        if url.path == "/api/mark":
            query = parse_qs(url.query)
            try:
                cx = float(query["x"][0])
                cy = float(query["y"][0])
            except (KeyError, ValueError):
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            now = time.time() * 1000
            with lock:
                best, delta = None, 1e9
                for rep in reports:
                    if not rep["contacts"]:
                        continue
                    d = abs(rep["t"] - now)
                    if d < delta:
                        best, delta = rep, d
                marks.append({
                    "t": round(now),
                    "css_x": cx,
                    "css_y": cy,
                    "dt_ms": round(delta) if best else None,
                    "contacts": best["contacts"] if best else [],
                })
            self._send(b'{"ok": true}', "application/json")
            return

        if url.path == "/api/marks":
            with lock:
                body = json.dumps({"marks": list(marks)}).encode()
            self._send(body, "application/json")
            return

        if url.path == "/api/health":
            with lock:
                body = json.dumps({
                    "ok": True,
                    "seq": seq,
                    "age_ms": round((time.time() - last_report_at) * 1000)
                    if last_report_at else None,
                }).encode()
            self._send(body, "application/json")
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("serving %s on http://127.0.0.1:%d/ (raw feed from %s)"
          % (DOCROOT, PORT, HID))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
