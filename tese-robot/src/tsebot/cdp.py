"""Minimal CDP client (no chromedriver).

chromedriver 154 cannot drive Chrome 155 (auto-update mismatch) and the matching
driver download is blocked by the proxy, so the bot talks to Chrome directly over
the DevTools WebSocket. Only Runtime.evaluate is needed: read the panel, set the
Angular model, call the broker's own submit function.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Optional

import websocket


class CDP:
    def __init__(self, port: int = 9333, target_url_part: str = "online.meibours.ir") -> None:
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=6)
                             .read().decode("utf-8"))
        page = next((t for t in targets
                     if t.get("type") == "page" and target_url_part in (t.get("url") or "")), None)
        if page is None:
            page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            raise RuntimeError("no page target")
        self.url = page["url"]
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                              timeout=20, suppress_origin=True)
        self._id = 0

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 25.0) -> Any:
        """Raw CDP call (Input.*, Page.*, Runtime.*)."""
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = __import__("time").time() + timeout
        while __import__("time").time() < deadline:
            data = json.loads(self.ws.recv())
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise RuntimeError(str(data["error"])[:300])
            return data.get("result")
        raise TimeoutError(method)

    def eval(self, expression: str, timeout: float = 25.0) -> Any:
        """Run JS in the page and return its JSON value."""
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({
            "id": msg_id, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True,
                       "awaitPromise": True, "userGesture": True},
        }))
        deadline = __import__("time").time() + timeout
        while __import__("time").time() < deadline:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") != msg_id:
                continue
            res = data.get("result", {})
            if "exceptionDetails" in res:
                raise RuntimeError(str(res["exceptionDetails"])[:300])
            return res.get("result", {}).get("value")
        raise TimeoutError("evaluate timed out")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
