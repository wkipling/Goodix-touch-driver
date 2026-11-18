#!/usr/bin/env python3
"""List CDP targets and report each page's DOM state.

Usage: cdp-targets.py
"""
import base64
import json
import os
import socket
import struct
import urllib.request

ENDPOINT = "http://127.0.0.1:9222"


def connect(url):
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
            raise EOFError
        buf += chunk
    return sock


def exact(sock, n):
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise EOFError
        out += chunk
    return out


def send(sock, obj):
    data = json.dumps(obj).encode()
    header = bytearray([0x81])
    header.append(0x80 | len(data)) if len(data) < 126 else (
        header.append(0x80 | 126), header.extend(struct.pack(">H", len(data))))
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


EXPR = ("JSON.stringify({url:location.href,ready:document.readyState,"
        "root:(document.documentElement||{}).tagName,"
        "body:!!document.body,scripts:document.scripts.length,"
        "title:document.title})")

targets = json.load(urllib.request.urlopen(ENDPOINT + "/json/list", timeout=5))
for t in targets:
    print("%-6s %-14s %s | %s" % (t.get("type"), t.get("id", "")[:12],
                                  t.get("url"), t.get("title")))
    if t.get("type") != "page":
        continue
    try:
        sock = connect(t["webSocketDebuggerUrl"])
        send(sock, {"id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": EXPR, "returnByValue": True}})
        while True:
            opcode, data = recv(sock)
            if opcode != 1:
                continue
            msg = json.loads(data)
            if msg.get("id") == 1:
                result = msg.get("result", {})
                if "exceptionDetails" in result:
                    print("   eval failed:", result["exceptionDetails"].get("text"))
                else:
                    print("   ", result.get("result", {}).get("value"))
                break
        sock.close()
    except Exception as exc:
        print("   connect failed:", exc)
