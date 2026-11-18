"""Record what Home Assistant decides for each press, and why.

Hooks the events HA's action handler actually uses, so each line shows:
  - pointerdown, with the pointer id and target
  - how long that pointer was held
  - the action HA dispatched (tap / hold / double_tap)
  - touchcancel, if Chromium withdrew the touch

If an action comes back as "hold" with a duration well under the hold
threshold, the press is being reported to the handler in a way that does not
match the finger.

Usage:
  cdp-record.py arm      reset and install the hooks
  cdp-record.py read     print what has been recorded
"""
import asyncio
import json
import sys
import urllib.request

import websockets

DEBUG_URL = "http://127.0.0.1:9222/json"

ARM_JS = r"""
(() => {
  if (window.__hold && window.__hold.bound) { window.__hold.log.length = 0; window.__hold.live = {}; return "rearmed"; }
  const H = window.__hold = {bound: true, log: [], live: {}};
  const path = (el) => { const p = []; let e = el, d = 0;
    while (e && d < 6) { p.push(e.tagName.toLowerCase() + (e.id ? "#" + e.id : "")
      + (typeof e.className === "string" && e.className ? "." + e.className.split(/\s+/)[0] : ""));
      e = e.parentElement || (e.getRootNode && e.getRootNode().host); d++; }
    return p.join(" < "); };
  const push = (o) => { H.log.push(o); if (H.log.length > 500) H.log.shift(); };

  document.addEventListener("pointerdown", (e) => {
    H.live[e.pointerId] = {t: performance.now(), type: e.pointerType, x: Math.round(e.clientX), y: Math.round(e.clientY), target: path(e.target)};
    push({t: Math.round(performance.now()), e: "pointerdown", pt: e.pointerType, pid: e.pointerId, x: Math.round(e.clientX), y: Math.round(e.clientY), target: path(e.target)});
  }, true);

  document.addEventListener("pointerup", (e) => {
    const d = H.live[e.pointerId];
    delete H.live[e.pointerId];
    push({t: Math.round(performance.now()), e: "pointerup", pt: e.pointerType, pid: e.pointerId, held: d ? Math.round(performance.now() - d.t) : null, target: d ? d.target : null});
  }, true);

  document.addEventListener("pointercancel", (e) => {
    const d = H.live[e.pointerId];
    delete H.live[e.pointerId];
    push({t: Math.round(performance.now()), e: "pointercancel", pt: e.pointerType, pid: e.pointerId, held: d ? Math.round(performance.now() - d.t) : null, target: d ? d.target : null});
  }, true);

  document.addEventListener("touchcancel", (e) => {
    push({t: Math.round(performance.now()), e: "touchcancel", n: e.touches.length});
  }, true);

  window.addEventListener("hass-action", (e) => {
    const d = e.detail || {};
    const cfg = d.config || {};
    const which = Object.keys(cfg).filter(k => /_action$/.test(k) && cfg[k] && cfg[k].action)
      .map(k => k.replace("_action", "") + "=" + cfg[k].action);
    push({t: Math.round(performance.now()), e: "HASS-ACTION", action: d.action || "(none)", cfg: which,
          holdTime: cfg.hold_time !== undefined ? cfg.hold_time : (cfg.hold_time === 0 ? 0 : undefined)});
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
    expr = ARM_JS if action == "arm" else "JSON.stringify(window.__hold ? window.__hold.log : 'NOT ARMED')"

    t = find_target()
    if t is None:
        print("no kiosk page target")
        return
    async with websockets.connect(t["webSocketDebuggerUrl"], max_size=None,
                                  open_timeout=5) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": expr, "returnByValue": True}}))
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
                        bits = " ".join("%s=%s" % (k, r[k]) for k in r if k not in ("t", "e"))
                        print("%8d  %-14s %s" % (r.get("t", 0), r.get("e", ""), bits))
                else:
                    print(v)
                return


asyncio.run(main())
