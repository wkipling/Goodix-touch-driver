"""Drive synthetic touches through CDP and report what Home Assistant decided.

Input.dispatchTouchEvent delivers trusted input at the browser level, the same
path real touches take after Chromium's own touch handling. So dispatching a
clean press-and-release tells us whether a well-formed tap produces a tap
action, independently of the panel and the filter.

Usage: cdp-tap.py X Y [hold_ms]
Prints the recorder's event log around the synthetic touch.
"""
import asyncio
import json
import sys
import time
import urllib.request

import websockets

DEBUG_URL = "http://127.0.0.1:9222/json"


def find_target():
    with urllib.request.urlopen(DEBUG_URL, timeout=5) as r:
        for t in json.load(r):
            if t.get("type") == "page" and "8123" in t.get("url", ""):
                return t
    return None


async def main():
    x, y = int(sys.argv[1]), int(sys.argv[2])
    hold_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 90

    t = find_target()
    if t is None:
        print("no kiosk page target")
        return

    async with websockets.connect(t["webSocketDebuggerUrl"], max_size=None,
                                  open_timeout=5) as ws:
        counter = [0]

        async def call(method, params=None):
            counter[0] += 1
            rid = counter[0]
            await ws.send(json.dumps({"id": rid, "method": method,
                                      "params": params or {}}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                if msg.get("id") == rid:
                    return msg

        async def evaluate(expr):
            r = await call("Runtime.evaluate",
                           {"expression": expr, "returnByValue": True})
            return r.get("result", {}).get("result", {}).get("value")

        await evaluate("window.__ev && (window.__ev.length = 0, 'cleared')")

        point = [{"x": x, "y": y, "id": 1, "radiusX": 3, "radiusY": 3,
                  "force": 1}]
        await call("Input.dispatchTouchEvent",
                   {"type": "touchStart", "touchPoints": point})
        await asyncio.sleep(hold_ms / 1000.0)
        await call("Input.dispatchTouchEvent",
                   {"type": "touchEnd", "touchPoints": []})

        # HA's action handler runs on a timer; give it room to decide.
        await asyncio.sleep(1.2)
        log = await evaluate("JSON.stringify(window.__ev || [])")
        print("synthetic tap at (%d,%d), press %dms" % (x, y, hold_ms))
        for row in json.loads(log or "[]"):
            print("   ", row)


asyncio.run(main())
