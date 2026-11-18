#!/usr/bin/env python3
"""Evaluate one expression in the kiosk page over the Chrome DevTools Protocol.

Usage:
    cdp-eval.py '<javascript expression>'

Prints the JSON result, or the exception text.  Loopback only; read-only unless
the expression itself does something.
"""
import base64
import json
import os
import socket
import struct
import sys
import urllib.request

ENDPOINT = "http://127.0.0.1:9222"


def ws_connect(url):
    host, rest = url.split("//", 1)[1].split("/", 1)
    name, port = host.split(":")
    sock = socket.create_connection((name, int(port)), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                  "Sec-WebSocket-Version: 13\r\n\r\n" % (rest, host, key)).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise EOFError("handshake failed")
        buf += chunk
    return sock


def exact(sock, n):
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise EOFError("short read")
        out += chunk
    return out


def send(sock, obj):
    data = json.dumps(obj).encode()
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(0x80 | len(data))
    else:
        header.append(0x80 | 126)
        header += struct.pack(">H", len(data))
    mask = os.urandom(4)
    sock.sendall(bytes(header) + mask +
                 bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def recv(sock):
    head = exact(sock, 2)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", exact(sock, 8))[0]
    mask = exact(sock, 4) if head[1] & 0x80 else None
    data = exact(sock, length)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return head[0] & 0x0F, data


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    targets = json.load(urllib.request.urlopen(ENDPOINT + "/json/list", timeout=5))
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        print("no page target")
        return 1
    sock = ws_connect(pages[0]["webSocketDebuggerUrl"])
    try:
        send(sock, {"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": sys.argv[1],
            "returnByValue": True,
            "awaitPromise": True,
        }})
        while True:
            opcode, data = recv(sock)
            if opcode != 1:
                continue
            msg = json.loads(data)
            if msg.get("id") != 1:
                continue
            result = msg.get("result", {})
            if "exceptionDetails" in result:
                print("EXCEPTION:", json.dumps(result["exceptionDetails"])[:600])
            else:
                print(json.dumps(result.get("result", {}).get("value")))
            return 0
    finally:
        sock.close()


if __name__ == "__main__":
    sys.exit(main())
