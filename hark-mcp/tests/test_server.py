"""End-to-end tests: start server.py on a free port, talk to it with the official MCP client
over streamable HTTP. On Linux, run_powershell/run_cmd fall back to bash.

    pip install -r hark-mcp/requirements.txt pytest httpx
    pytest -q hark-mcp/tests
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

HERE = Path(__file__).resolve().parent.parent
# never route localhost test traffic through an environment proxy
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
TOKEN = "t" * 20 + "est-token-for-hark-mcp-0123456789"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    bot = tmp_path_factory.mktemp("bot")
    port = _free_port()
    env = dict(os.environ, HARK_MCP_TOKEN=TOKEN, HARK_MCP_PORT=str(port), HARK_MCP_DATA_DIR=str(data),
               HARK_MCP_BOT_DIR=str(bot), HARK_MCP_LIVE_TRADING="0", HARK_MCP_READONLY="0")
    proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(100):
        try:
            httpx.get(url, timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError(proc.stdout.read().decode())
    yield {"url": url, "data": data, "bot": bot}
    proc.terminate()
    proc.wait(10)


async def _call(url, tool, args=None, token=TOKEN):
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            if tool is None:
                return await s.list_tools()
            return await s.call_tool(tool, args or {})


def call(url, tool, args=None):
    res = asyncio.run(_call(url, tool, args))
    return res


def data_of(res):
    assert not res.isError, res.content
    if res.structuredContent is not None:
        sc = res.structuredContent
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def test_refuses_without_token_on_startup(tmp_path):
    env = dict(os.environ, HARK_MCP_TOKEN="short", HARK_MCP_DATA_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(HERE / "server.py")], env=env, capture_output=True, timeout=30)
    assert r.returncode == 2 and b"HARK_MCP_TOKEN" in r.stderr


def test_401_without_or_with_wrong_token(server):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    h = {"Accept": "application/json, text/event-stream"}
    assert httpx.post(server["url"], json=body, headers=h).status_code == 401
    assert httpx.post(server["url"], json=body, headers={**h, "Authorization": "Bearer wrong"}).status_code == 401
    assert httpx.post(server["url"], json=body, headers={**h, "Authorization": TOKEN}).status_code == 401
    ok = httpx.post(server["url"], json=body, headers={**h, "Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


def test_list_tools(server):
    names = {t.name for t in call(server["url"], None).tools}
    for n in ["run_powershell", "run_cmd", "job_status", "job_output", "read_file", "write_file", "list_dir",
              "delete_path", "search_files", "upload_file", "download_file", "git", "pm2", "tail_log",
              "system_info", "screenshot_desktop"]:
        assert n in names


def test_shell_and_persian(server):
    d = data_of(call(server["url"], "run_powershell", {"command": "echo سلام; echo err >&2; exit 3"}))
    assert d["exit_code"] == 3 and "سلام" in d["stdout"] and "err" in d["stderr"]
    assert d["cwd"] == str(server["bot"])  # default cwd = bot folder
    d = data_of(call(server["url"], "run_cmd", {"command": "pwd", "cwd": "/tmp"}))
    assert d["exit_code"] == 0 and d["stdout"].strip() == "/tmp"


def test_truncation_and_timeout(server):
    d = data_of(call(server["url"], "run_cmd", {"command": "python3 -c \"print('x'*200000)\""}))
    assert "truncated" in d["stdout"] and len(d["stdout"]) < 70000
    d = data_of(call(server["url"], "run_cmd", {"command": "sleep 10", "timeout_sec": 1}))
    assert d.get("timed_out") and d["exit_code"] == -1


def test_background_job(server):
    d = data_of(call(server["url"], "run_powershell",
                     {"command": "for i in 1 2 3; do echo line$i; sleep 0.3; done", "background": True}))
    jid = d["job_id"]
    for _ in range(40):
        st = data_of(call(server["url"], "job_status", {"job_id": jid}))
        if not st["running"]:
            break
        time.sleep(0.25)
    assert st["running"] is False and st["exit_code"] == 0
    out = data_of(call(server["url"], "job_output", {"job_id": jid, "tail_lines": 2}))
    assert out["output"].split() == ["line2", "line3"]


def test_files_roundtrip(server):
    base = server["bot"] / "پوشه تست"
    f = base / "a.txt"
    data_of(call(server["url"], "write_file", {"path": str(f), "content": "یک\ntwo\nthree\n"}))
    d = data_of(call(server["url"], "read_file", {"path": str(f), "offset": 1, "limit": 1}))
    assert d["content"] == "two" and d["total_lines"] == 3
    d = data_of(call(server["url"], "list_dir", {"path": str(server["bot"]), "recursive": True, "pattern": "*.txt"}))
    assert any(e["path"].endswith("a.txt") for e in d["entries"])
    d = data_of(call(server["url"], "search_files", {"root": str(server["bot"]), "regex": "^thr", "glob": "*.txt"}))
    assert d["matches"][0]["line"] == 3
    d = data_of(call(server["url"], "tail_log", {"path": str(f), "lines": 2}))
    assert d["content"] == "two\nthree"
    blob = bytes(range(256)) * 10
    data_of(call(server["url"], "upload_file", {"path": str(base / "b.bin"), "base64": base64.b64encode(blob).decode()}))
    d = data_of(call(server["url"], "download_file", {"path": str(base / "b.bin")}))
    assert base64.b64decode(d["base64"]) == blob
    d = data_of(call(server["url"], "delete_path", {"path": str(base)}))
    assert not base.exists() and Path(d["backup"], "a.txt").exists()
    assert str(server["data"]) in d["backup"]


def test_kill_switch(server):
    bot = server["bot"]
    (bot / "order_candidates.py").write_text("print('ORDER SENT')\n")
    (bot / "morning_orders.py").write_text("print('MORNING')\n")
    (bot / "lot_exits.py").write_text("print('LOT')\n")
    blocked = ["python3 order_candidates.py", "python3 morning_orders.py", "python3 lot_exits.py --live",
               "node pw/flow.js X", "node pw/cancel_order.js 1", "python broker_pw.py order ABC IR123 10",
               "cd x && python3 .\\intraday_exit.py"]
    for cmd in blocked:
        d = data_of(call(server["url"], "run_powershell", {"command": cmd}))
        assert d.get("refused"), cmd
        assert "KILL-SWITCH" in d["error"]
    d = data_of(call(server["url"], "run_cmd", {"command": "python3 order_candidates.py"}))
    assert d.get("refused")
    d = data_of(call(server["url"], "pm2", {"args": "start order_candidates.py --name x"}))
    assert d.get("refused")
    # allowed variants
    d = data_of(call(server["url"], "run_powershell", {"command": "FLOW_DRY=1 python3 order_candidates.py"}))
    assert d["exit_code"] == 0 and "ORDER SENT" in d["stdout"]
    for cmd in ["python3 morning_orders.py --dry", "python3 lot_exits.py", "echo pm2 restart order_supervisor"]:
        d = data_of(call(server["url"], "run_powershell", {"command": cmd}))
        assert not d.get("refused"), cmd


def test_git_pm2_sysinfo(server):
    d = data_of(call(server["url"], "git", {"args": "--version"}))
    assert d["exit_code"] == 0 and "git version" in d["stdout"]
    d = data_of(call(server["url"], "pm2", {"args": "logs warrobot"}))  # pm2 absent on test box
    assert "exit_code" in d
    d = data_of(call(server["url"], "system_info"))
    assert d["time_tehran"].endswith("+03:30") and d["live_trading"] is False


def test_screenshot_optional(server):
    res = call(server["url"], "screenshot_desktop")
    # headless Linux has no display: an error result is acceptable, a crash is not
    if not res.isError:
        assert res.content[0].type == "image"


def test_audit_log(server):
    lines = (server["data"] / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(x) for x in lines]
    assert any(r["tool"] == "run_powershell" and r.get("refused") for r in recs)
    up = [r for r in recs if r["tool"] == "upload_file"][0]
    assert up["args"]["base64"].startswith("<")  # content not logged
    assert all("duration_sec" in r for r in recs)


def test_readonly_mode(tmp_path):
    port = _free_port()
    env = dict(os.environ, HARK_MCP_TOKEN=TOKEN, HARK_MCP_PORT=str(port), HARK_MCP_DATA_DIR=str(tmp_path),
               HARK_MCP_BOT_DIR=str(tmp_path), HARK_MCP_READONLY="1")
    proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(100):
            try:
                httpx.get(url, timeout=0.5)
                break
            except Exception:
                time.sleep(0.1)
        assert data_of(call(url, "run_cmd", {"command": "echo hi"}))["refused"]
        assert data_of(call(url, "write_file", {"path": str(tmp_path / "x"), "content": "x"}))["refused"]
        assert data_of(call(url, "git", {"args": "push"}))["refused"]
        assert data_of(call(url, "pm2", {"args": "restart all"}))["refused"]
        assert data_of(call(url, "list_dir", {"path": str(tmp_path)}))["root"] == str(tmp_path)
    finally:
        proc.terminate()
        proc.wait(10)
