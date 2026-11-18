#!/usr/bin/env python3
"""Record what the page does with each press, including motion and slop.

Based on /home/kiosk/cdp-record.py, with pointermove distance and click events
added.  The point is to distinguish, for a tap that does not toggle:

  * the driver never delivered it (no pointerdown),
  * Chromium turned it into a scroll (pointercancel / large max distance),
  * the page got a clean tap but Home Assistant dispatched nothing.

Usage:
  cdp-record-touch.py arm
  cdp-record-touch.py read
"""
import asyncio
import json
import sys
import urllib.request

import websockets

DEBUG_URL = "http://127.0.0.1:9222/json"

ARM_JS = r"""
(() => {
  if (window.__touchlog && window.__touchlog.bound) {
    window.__touchlog.log.length = 0;
    window.__touchlog.live = {};
    return "rearmed";
  }
  const H = window.__touchlog = {bound: true, log: [], live: {}};
  const path = (el) => {
    const p = []; let e = el, d = 0;
    while (e && d < 5) {
      p.push(e.tagName.toLowerCase() + (e.id ? "#" + e.id : "")
        + (typeof e.className === "string" && e.className
           ? "." + e.className.split(/\s+/)[0] : ""));
      e = e.parentElement || (e.getRootNode && e.getRootNode().host); d++;
    }
    return p.join(" < ");
  };
  const push = (o) => { H.log.push(o); if (H.log.length > 500) H.log.shift(); };

  document.addEventListener("pointerdown", (e) => {
    H.live[e.pointerId] = {t: performance.now(), x: e.clientX, y: e.clientY,
                           max: 0, moves: 0, target: path(e.target)};
    push({t: Math.round(performance.now()), e: "pointerdown",
          pt: e.pointerType, pid: e.pointerId,
          x: Math.round(e.clientX), y: Math.round(e.clientY),
          target: path(e.target)});
  }, true);

  document.addEventListener("pointermove", (e) => {
    const d = H.live[e.pointerId];
    if (!d) return;
    const dist = Math.hypot(e.clientX - d.x, e.clientY - d.y);
    if (dist > d.max) d.max = dist;
    d.moves++;
  }, true);

  document.addEventListener("pointerup", (e) => {
    const d = H.live[e.pointerId]; delete H.live[e.pointerId];
    push({t: Math.round(performance.now()), e: "pointerup", pt: e.pointerType,
          pid: e.pointerId, held: d ? Math.round(performance.now() - d.t) : null,
          x: Math.round(e.clientX), y: Math.round(e.clientY),
          max: d ? Math.round(d.max) : null, moves: d ? d.moves : null,
          target: d ? d.target : null});
  }, true);

  document.addEventListener("pointercancel", (e) => {
    const d = H.live[e.pointerId]; delete H.live[e.pointerId];
    push({t: Math.round(performance.now()), e: "pointercancel", pt: e.pointerType,
          pid: e.pointerId, held: d ? Math.round(performance.now() - d.t) : null,
          max: d ? Math.round(d.max) : null, moves: d ? d.moves : null,
          target: d ? d.target : null});
  }, true);

  document.addEventListener("touchcancel", (e) => {
    push({t: Math.round(performance.now()), e: "touchcancel", n: e.touches.length});
  }, true);

  document.addEventListener("click", (e) => {
    push({t: Math.round(performance.now()), e: "click", target: path(e.target)});
  }, true);

  document.addEventListener("dblclick", (e) => {
    push({t: Math.round(performance.now()), e: "dblclick", target: path(e.target)});
  }, true);

  window.addEventListener("hass-action", (e) => {
    const d = e.detail || {};
    const cfg = d.config || {};
    const which = Object.keys(cfg).filter(k => /_action$/.test(k) && cfg[k] && cfg[k].action)
      .map(k => k.replace("_action", "") + "=" + cfg[k].action);
    push({t: Math.round(performance.now()), e: "HASS-ACTION",
          action: d.action || "(none)", cfg: which,
          holdTime: cfg.hold_time !== undefined ? cfg.hold_time : undefined});
  }, true);

  document.addEventListener("contextmenu", () => push({t: Math.round(performance.now()), e: "contextmenu"}), true);
  return "armed";
})()
"""


def find_target():
    with urllib.request.urlopen(DEBUG_URL, timeout=5) as r:
        for t in json.load(r):
            if t.get("type") == "page" and "8123" in t.get("url", ""):
                return t
    return None


async def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "read"
    expr = ARM_JS if action == "arm" else \
        "JSON.stringify(window.__touchlog ? window.__touchlog.log : 'NOT ARMED')"

    target = find_target()
    if target is None:
        print("no kiosk page target")
        return
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None,
                                  open_timeout=5) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": expr,
                                             "returnByValue": True}}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if m.get("id") == 1:
                v = m.get("result", {}).get("result", {}).get("value")
                if action == "read" and isinstance(v, str):
                    rows = json.loads(v) if v.startswith("[") else v
                    if isinstance(rows, str):
                        print(rows)
                        return
                    for r in rows:
                        bits = " ".join("%s=%s" % (k, r[k]) for k in r
                                        if k not in ("t", "e"))
                        print("%8d  %-14s %s" % (r.get("t", 0), r.get("e", ""), bits))
                else:
                    print(v)
                return


asyncio.run(main())
