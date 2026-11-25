"""hark-mcp: remote MCP server that lets Hark operate the WarRobot Windows server.

Transport : MCP streamable HTTP at http://127.0.0.1:8765/mcp (expose via Cloudflare Tunnel)
Auth      : Authorization: Bearer <HARK_MCP_TOKEN>  (>= 32 chars, constant-time compare)
Safety    : audit log (audit.jsonl), trading kill-switch (guard.json, HARK_MCP_LIVE_TRADING),
            optional read-only mode (HARK_MCP_READONLY=1), backups before delete.

Environment variables
  HARK_MCP_TOKEN          required bearer token (min 32 chars)
  HARK_MCP_HOST / PORT    bind address, default 127.0.0.1 / 8765
  HARK_MCP_BOT_DIR        WarRobot folder (default C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot)
  HARK_MCP_LIVE_TRADING   "1" disables the trading kill-switch (default "0")
  HARK_MCP_READONLY       "1" = read-only mode
  HARK_MCP_DATA_DIR       where audit.jsonl / backups / jobs live (default: this folder)
"""
from __future__ import annotations

import base64
import datetime as _dt
import fnmatch
import functools
import hmac
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP, Image

try:  # older/newer SDKs
    from mcp.server.transport_security import TransportSecuritySettings
except Exception:  # pragma: no cover
    TransportSecuritySettings = None  # type: ignore

IS_WIN = os.name == "nt"
HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("HARK_MCP_DATA_DIR") or HERE)
AUDIT = DATA_DIR / "audit.jsonl"
BACKUPS = DATA_DIR / "backups"
JOBS = DATA_DIR / "jobs"
GUARD_FILE = Path(os.environ.get("HARK_MCP_GUARD") or (HERE / "guard.json"))
BOT_DIR = os.environ.get(
    "HARK_MCP_BOT_DIR", r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot"
)
MAX_OUT = 60_000          # bytes per stream
MAX_FILE = 10 * 1024 * 1024
TEHRAN_OFFSET = _dt.timedelta(hours=3, minutes=30)  # Iran has no DST since 2022


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


READONLY = _flag("HARK_MCP_READONLY")


def live_trading() -> bool:
    return _flag("HARK_MCP_LIVE_TRADING")


# --------------------------------------------------------------------------- helpers
class Refused(Exception):
    """A call refused by policy (kill-switch / read-only)."""


def _truncate(s: str, limit: int = MAX_OUT) -> str:
    b = s.encode("utf-8", "replace")
    if len(b) <= limit:
        return s
    head = b[: limit // 3].decode("utf-8", "ignore")
    tail = b[-(limit - limit // 3):].decode("utf-8", "ignore")
    return f"{head}\n\n... [truncated {len(b) - limit} bytes of {len(b)}] ...\n\n{tail}"


def _decode(b: bytes | None) -> str:
    if not b:
        return ""
    for enc in ("utf-8",):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    if IS_WIN:
        try:
            return b.decode("cp1256")  # Persian Windows ANSI code page
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("HARK_MCP_TOKEN", None)  # never leak the token to children
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if extra:
        env.update(extra)
    return env


def _default_cwd(cwd: str | None) -> str:
    if cwd:
        p = Path(cwd).expanduser()
        if not p.is_dir():
            raise ValueError(f"cwd does not exist: {cwd}")
        return str(p)
    return BOT_DIR if Path(BOT_DIR).is_dir() else str(Path.home())


def _shell_argv(kind: str, command: str) -> list[str] | str:
    """Build the process command for 'powershell' or 'cmd'. On non-Windows both map to bash."""
    if not IS_WIN:
        return ["bash", "-c", command]
    if kind == "cmd":
        # /s strips the outer quotes so the inner command is passed verbatim
        return f'cmd.exe /d /s /c "chcp 65001>nul & {command}"'
    prelude = (
        "$ErrorActionPreference='Continue';"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$PSDefaultParameterValues['*:Encoding']='utf8';\n"
    )
    script = prelude + command + "\nif ($LASTEXITCODE -ne $null) { exit $LASTEXITCODE }"
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    exe = shutil.which("powershell.exe") or "powershell.exe"
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, 9)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run(kind: str, command: str, cwd: str | None, timeout_sec: float) -> dict:
    argv = _shell_argv(kind, command)
    kw: dict[str, Any] = dict(cwd=_default_cwd(cwd), env=_child_env(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    if IS_WIN:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    t0 = time.time()
    proc = subprocess.Popen(argv, **kw)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = b"", b""
    res = {
        "exit_code": proc.returncode if not timed_out else -1,
        "stdout": _truncate(_decode(out)),
        "stderr": _truncate(_decode(err)),
        "duration_sec": round(time.time() - t0, 2),
        "cwd": kw["cwd"],
    }
    if timed_out:
        res["timed_out"] = True
        res["stderr"] += f"\n[hark-mcp] killed after timeout of {timeout_sec}s (use background=true for long jobs)"
    return res


# --------------------------------------------------------------------------- jobs
_JOB_PROCS: dict[str, subprocess.Popen] = {}


def _start_job(kind: str, command: str, cwd: str | None) -> dict:
    JOBS.mkdir(parents=True, exist_ok=True)
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    log = JOBS / f"{jid}.log"
    argv = _shell_argv(kind, command)
    fh = open(log, "wb")
    kw: dict[str, Any] = dict(cwd=_default_cwd(cwd), env=_child_env(), stdout=fh,
                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    if IS_WIN:
        kw["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(argv, **kw)
    fh.close()
    _JOB_PROCS[jid] = proc
    meta = {"job_id": jid, "pid": proc.pid, "command": command, "cwd": kw["cwd"],
            "kind": kind, "started": _dt.datetime.now().isoformat(timespec="seconds"), "log": str(log)}
    (JOBS / f"{jid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _pid_alive(pid: int) -> bool:
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except ImportError:
        pass
    if IS_WIN:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _job_meta(job_id: str) -> dict:
    if not re.fullmatch(r"[0-9A-Za-z\-]+", job_id or ""):
        raise ValueError("bad job_id")
    f = JOBS / f"{job_id}.json"
    if not f.exists():
        raise ValueError(f"unknown job_id {job_id}")
    meta = json.loads(f.read_text(encoding="utf-8"))
    proc = _JOB_PROCS.get(job_id)
    if proc is not None:
        rc = proc.poll()
        meta["running"] = rc is None
        meta["exit_code"] = rc
    else:  # server restarted since the job started
        meta["running"] = _pid_alive(meta["pid"])
        meta["exit_code"] = None
    return meta


def _tail(path: Path, lines: int) -> str:
    lines = max(1, min(int(lines), 20000))
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        block, data = 65536, b""
        pos = size
        while pos > 0 and data.count(b"\n") <= lines:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            if len(data) > 8 * MAX_OUT:
                break
    text = _decode(data)
    return _truncate("\n".join(text.splitlines()[-lines:]))


# --------------------------------------------------------------------------- guard
def _load_guard() -> dict:
    try:
        return json.loads(GUARD_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # fail closed: a broken guard file blocks the known names
        return {"allow_if": r"FLOW_DRY\s*=\s*[\"']?1\b",
                "rules": [{"name": f"guard.json unreadable ({e}); fallback", "pattern":
                           r"order_candidates\.py|morning_orders\.py|intraday_exit\.py|preopen_exits\.py|"
                           r"rebalance\.py|order_retry\.py|order_supervisor\.py|cancel_\w*\.(py|js)|"
                           r"flow\.js|api_order\.js|broker_pw\.py\s+order|lot_exits\.py.*--live"}]}


def check_trading_guard(text: str) -> None:
    if live_trading():
        return
    g = _load_guard()
    flags = re.IGNORECASE
    allow = g.get("allow_if")
    if allow and re.search(allow, text, flags):
        return
    for rule in g.get("rules", []):
        if re.search(rule["pattern"], text, flags):
            unless = rule.get("unless")
            if unless and re.search(unless, text, flags):
                continue
            raise Refused(
                f"TRADING KILL-SWITCH: refused because the command launches a real-order script "
                f"(rule: {rule.get('name', rule['pattern'])}). HARK_MCP_LIVE_TRADING is not '1'. "
                f"Add FLOW_DRY=1 to the same command for a dry run, or the owner can set "
                f"HARK_MCP_LIVE_TRADING=1 / edit {GUARD_FILE.name}."
            )


def _require_writable(what: str) -> None:
    if READONLY:
        raise Refused(f"READ-ONLY MODE (HARK_MCP_READONLY=1): '{what}' is not allowed.")


# --------------------------------------------------------------------------- audit
_SECRET_KEY = re.compile(r"(pass(word)?|pwd|token|secret|api[_-]?key|auth|cookie|otp)", re.I)
_SECRET_INLINE = re.compile(
    r"((?:pass(?:word)?|pwd|token|secret|api[_-]?key|authorization|bearer)[\"']?\s*[:=\s]\s*[\"']?)([^\s\"';&|]+)", re.I)


def _redact(v: Any, key: str = "") -> Any:
    if isinstance(v, dict):
        return {k: _redact(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact(x, key) for x in v]
    if isinstance(v, str):
        if key in ("content", "base64", "b64"):
            return f"<{len(v)} chars>"
        if _SECRET_KEY.fullmatch(key or "") or (key and _SECRET_KEY.search(key)):
            return "***"
        s = _SECRET_INLINE.sub(r"\1***", v)
        return s if len(s) <= 4000 else s[:4000] + "...<cut>"
    return v


def _audit(rec: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- server
_ts_kw: dict[str, Any] = {}
if TransportSecuritySettings is not None:
    # Host header will be the tunnel hostname; bearer auth is the protection layer.
    _ts_kw["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP(
    "hark-mcp-warrobot",
    instructions=(
        "Operate the WarRobot Windows trading server (pm2, Python, Node, Chrome). "
        f"Bot folder: {BOT_DIR}. Real-order scripts are blocked unless HARK_MCP_LIVE_TRADING=1 "
        "or FLOW_DRY=1 is in the same command. Use background=true for long commands."
    ),
    streamable_http_path="/mcp",
    **_ts_kw,
)


def tool(fn):
    """Register fn as an MCP tool: runs in a worker thread, audited, policy errors returned as text."""

    @functools.wraps(fn)
    async def wrapper(**kwargs):
        t0 = time.time()
        rec: dict[str, Any] = {"time": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                               "tool": fn.__name__, "args": _redact(kwargs)}
        try:
            result = await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))
            if isinstance(result, dict):
                rec["exit_code"] = result.get("exit_code")
            rec["ok"] = True
            return result
        except Refused as e:
            rec.update(ok=False, refused=True, error=str(e))
            return {"refused": True, "error": str(e), "exit_code": None}
        except Exception as e:
            rec.update(ok=False, error=f"{type(e).__name__}: {e}")
            raise
        finally:
            rec["duration_sec"] = round(time.time() - t0, 3)
            _audit(rec)

    return mcp.tool()(wrapper)


# ---- shell
def _shell_tool(kind: str, command: str, cwd: str | None, timeout_sec: float, background: bool) -> dict:
    _require_writable(f"run_{kind}")
    check_trading_guard(command)
    if background:
        return _start_job(kind, command, cwd)
    return _run(kind, command, cwd, max(1.0, min(float(timeout_sec), 3600.0)))


@tool
def run_powershell(command: str, cwd: str | None = None, timeout_sec: float = 120,
                   background: bool = False) -> dict:
    """Run a PowerShell command (powershell.exe -NoProfile -NonInteractive, UTF-8 output).
    Default cwd is the WarRobot bot folder. Returns exit_code, stdout, stderr (each truncated ~60KB).
    background=true starts a detached process and returns a job_id (see job_status / job_output).
    On non-Windows hosts (tests) bash is used instead."""
    return _shell_tool("powershell", command, cwd, timeout_sec, background)


@tool
def run_cmd(command: str, cwd: str | None = None, timeout_sec: float = 120,
            background: bool = False) -> dict:
    """Run a cmd.exe command (code page 65001). Same contract as run_powershell."""
    return _shell_tool("cmd", command, cwd, timeout_sec, background)


@tool
def job_status(job_id: str = "") -> dict:
    """Status of a background job (running / exit_code / pid / log path). Empty job_id lists recent jobs."""
    if not job_id:
        JOBS.mkdir(parents=True, exist_ok=True)
        ids = sorted((p.stem for p in JOBS.glob("*.json")), reverse=True)[:30]
        return {"jobs": [_job_meta(i) for i in ids]}
    return _job_meta(job_id)


@tool
def job_output(job_id: str, tail_lines: int = 200) -> dict:
    """Last tail_lines lines of a background job's combined stdout/stderr."""
    meta = _job_meta(job_id)
    meta["output"] = _tail(Path(meta["log"]), tail_lines)
    return meta


@tool
def job_kill(job_id: str) -> dict:
    """Kill a background job and its process tree."""
    _require_writable("job_kill")
    meta = _job_meta(job_id)
    proc = _JOB_PROCS.get(job_id)
    if proc is not None:
        _kill_tree(proc)
    elif meta.get("running"):
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(meta["pid"])], capture_output=True)
        else:
            try:
                os.killpg(meta["pid"], 9)
            except OSError:
                pass
    time.sleep(0.5)
    return _job_meta(job_id)


# ---- files
def _read_text(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("cp1256")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


@tool
def read_file(path: str, offset: int = 0, limit: int = 2000) -> dict:
    """Read a text file. offset = first line (0-based), limit = number of lines (max 20000).
    Handles UTF-8, UTF-16 and Windows-1256. Output capped at ~60KB."""
    p = Path(path).expanduser()
    if p.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("file > 50MB; use tail_log or download_file")
    lines = _read_text(p).splitlines()
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 20000))
    chunk = lines[offset: offset + limit]
    return {"path": str(p), "total_lines": len(lines), "offset": offset, "returned_lines": len(chunk),
            "content": _truncate("\n".join(chunk))}


@tool
def write_file(path: str, content: str, create_dirs: bool = True, append: bool = False) -> dict:
    """Write (or append) UTF-8 text to a file. If the file exists it is first copied into backups/."""
    _require_writable("write_file")
    p = Path(path).expanduser()
    if create_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(p) if p.exists() and p.is_file() else None
    with open(p, "a" if append else "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return {"path": str(p), "bytes": p.stat().st_size, "backup": backup}


@tool
def list_dir(path: str, recursive: bool = False, pattern: str = "*", max_entries: int = 2000) -> dict:
    """List a directory (name, type, size, modified). recursive walks subfolders; pattern is a glob on names."""
    root = Path(path).expanduser()
    items, n = [], 0
    it = root.rglob("*") if recursive else root.iterdir()
    for e in it:
        if not fnmatch.fnmatch(e.name.lower(), pattern.lower()):
            continue
        n += 1
        if len(items) >= max_entries:
            continue
        try:
            st = e.stat()
            items.append({"path": str(e.relative_to(root)), "type": "dir" if e.is_dir() else "file",
                          "size": st.st_size if e.is_file() else None,
                          "modified": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        except OSError as ex:
            items.append({"path": str(e.relative_to(root)), "error": str(ex)})
    return {"root": str(root), "count": n, "truncated": n > len(items), "entries": items}


def _backup(p: Path) -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe = re.sub(r"[:\\/]+", "_", str(p.resolve())).strip("_")
    dest = BACKUPS / stamp / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    if p.is_dir():
        shutil.copytree(p, dest)
    else:
        shutil.copy2(p, dest)
    return str(dest)


@tool
def delete_path(path: str) -> dict:
    """Delete a file or folder. A full backup copy is made into hark-mcp/backups/ first."""
    _require_writable("delete_path")
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(path)
    if p.resolve() in (Path(p.anchor).resolve(), Path.home().resolve()):
        raise Refused("refusing to delete a drive root or the home folder")
    backup = _backup(p)
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"deleted": str(p), "backup": backup}


_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "pw-profile", "backups"}


@tool
def search_files(root: str, regex: str, glob: str = "*", max_matches: int = 500,
                 ignore_case: bool = False) -> dict:
    """Grep: search text files under root for a regex (line by line). glob filters file names.
    Skips .git, node_modules, venvs, files > 5MB and binary files."""
    rx = re.compile(regex, re.IGNORECASE if ignore_case else 0)
    out, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(Path(root).expanduser()):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not fnmatch.fnmatch(name.lower(), glob.lower()):
                continue
            fp = Path(dirpath) / name
            try:
                if fp.stat().st_size > 5 * 1024 * 1024:
                    continue
                raw = fp.read_bytes()
                if b"\x00" in raw[:4096]:
                    continue
                scanned += 1
                for i, line in enumerate(_decode(raw).splitlines(), 1):
                    if rx.search(line):
                        out.append({"file": str(fp), "line": i, "text": line[:400]})
                        if len(out) >= max_matches:
                            return {"matches": out, "truncated": True, "files_scanned": scanned}
            except OSError:
                continue
    return {"matches": out, "truncated": False, "files_scanned": scanned}


@tool
def upload_file(path: str, base64: str, create_dirs: bool = True) -> dict:
    """Write binary content (base64) to path. Max 10MB. Existing file is backed up first."""
    _require_writable("upload_file")
    import base64 as b64mod
    data = b64mod.b64decode(base64)
    if len(data) > MAX_FILE:
        raise ValueError("file larger than 10MB")
    p = Path(path).expanduser()
    if create_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(p) if p.exists() and p.is_file() else None
    p.write_bytes(data)
    return {"path": str(p), "bytes": len(data), "backup": backup}


@tool
def download_file(path: str) -> dict:
    """Return a file's bytes as base64 (max 10MB)."""
    p = Path(path).expanduser()
    size = p.stat().st_size
    if size > MAX_FILE:
        raise ValueError(f"file is {size} bytes; limit is 10MB")
    return {"path": str(p), "bytes": size, "base64": base64.b64encode(p.read_bytes()).decode()}


@tool
def tail_log(path: str, lines: int = 200) -> dict:
    """Last N lines of a (log) file, efficiently, without reading the whole file."""
    p = Path(path).expanduser()
    return {"path": str(p), "size": p.stat().st_size,
            "modified": _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "content": _tail(p, lines)}


# ---- git / pm2
_GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "describe", "tag", "shortlog", "reflog"}
_PM2_READ = {"status", "list", "ls", "l", "jlist", "prettylist", "logs", "log", "describe", "desc", "info", "show", "env", "monit"}


@tool
def git(args: str, cwd: str | None = None, timeout_sec: float = 120) -> dict:
    """Run `git <args>` (default cwd = bot folder), e.g. args="status", "log --oneline -20", "pull"."""
    sub = (args.split() or [""])[0]
    if READONLY and sub not in _GIT_READ:
        raise Refused(f"READ-ONLY MODE: git {sub} not allowed")
    return _run("powershell", f"git {args}", cwd, timeout_sec)


@tool
def pm2(args: str, lines: int = 100, timeout_sec: float = 120) -> dict:
    """Run `pm2 <args>`, e.g. "status", "logs warrobot", "restart warrobot", "describe hark-mcp".
    `logs` automatically gets --nostream --lines N. Restarting/starting the trading daemons is allowed;
    starting a real-order script directly is blocked by the kill-switch."""
    parts = args.split()
    sub = parts[0] if parts else "status"
    if READONLY and sub not in _PM2_READ:
        raise Refused(f"READ-ONLY MODE: pm2 {sub} not allowed")
    if sub in ("logs", "log"):
        if "--nostream" not in args:
            args += " --nostream"
        if "--lines" not in args:
            args += f" --lines {int(lines)}"
    check_trading_guard(args)
    return _run("powershell", f"pm2 {args}", None, timeout_sec)


# ---- system
def _ntp_offset(server: str = "pool.ntp.org", timeout: float = 2.0) -> float | None:
    try:
        pkt = b"\x1b" + 47 * b"\0"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t1 = time.time()
            s.sendto(pkt, (server, 123))
            data, _ = s.recvfrom(48)
            t4 = time.time()
        def ts(off):
            sec, frac = struct.unpack("!II", data[off:off + 8])
            return sec - 2208988800 + frac / 2**32
        t2, t3 = ts(32), ts(40)
        return round(((t2 - t1) + (t3 - t4)) / 2, 3)
    except Exception:
        return None


def _version(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, shell=IS_WIN)
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else None
    except Exception:
        return None


@tool
def system_info() -> dict:
    """CPU, RAM, disks, uptime, local/UTC/Tehran time, NTP clock offset, python/node/pm2 versions, pm2 list."""
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        teh = now.astimezone(ZoneInfo("Asia/Tehran"))
    except Exception:
        teh = now.astimezone(_dt.timezone(TEHRAN_OFFSET))
    info: dict[str, Any] = {
        "hostname": socket.gethostname(), "os": platform.platform(),
        "time_local": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "time_utc": now.isoformat(timespec="seconds"), "time_tehran": teh.isoformat(timespec="seconds"),
        "ntp_offset_sec": _ntp_offset(), "server_python": sys.version.split()[0],
        "bot_dir": BOT_DIR, "bot_dir_exists": Path(BOT_DIR).is_dir(),
        "live_trading": live_trading(), "readonly": READONLY,
    }
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        info["cpu_count"] = psutil.cpu_count()
        info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        info["ram_total_gb"] = round(vm.total / 2**30, 2)
        info["ram_used_percent"] = vm.percent
        info["uptime_hours"] = round((time.time() - psutil.boot_time()) / 3600, 1)
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                disks.append({"mount": part.mountpoint, "total_gb": round(u.total / 2**30, 1),
                              "free_gb": round(u.free / 2**30, 1), "used_percent": u.percent})
            except Exception:
                pass
        info["disks"] = disks
    except ImportError:
        info["cpu_count"] = os.cpu_count()
        u = shutil.disk_usage(Path(BOT_DIR).anchor if IS_WIN else "/")
        info["disks"] = [{"mount": Path(BOT_DIR).anchor if IS_WIN else "/", "total_gb": round(u.total / 2**30, 1),
                          "free_gb": round(u.free / 2**30, 1)}]
        info["note"] = "pip install psutil for RAM/CPU/uptime"
    info["python_version"] = _version(["python", "--version"])
    info["node_version"] = _version(["node", "--version"])
    info["pm2_version"] = _version(["pm2", "--version"])
    try:
        r = subprocess.run("pm2 jlist", capture_output=True, timeout=30, shell=True)
        procs = json.loads(_decode(r.stdout) or "[]")
        info["pm2"] = [{"name": p.get("name"), "status": p.get("pm2_env", {}).get("status"),
                        "restarts": p.get("pm2_env", {}).get("restart_time"),
                        "uptime_since": p.get("pm2_env", {}).get("pm_uptime"),
                        "cpu": p.get("monit", {}).get("cpu"),
                        "mem_mb": round((p.get("monit", {}).get("memory") or 0) / 2**20, 1)} for p in procs]
    except Exception as e:
        info["pm2"] = f"unavailable: {type(e).__name__}"
    return info


def _grab_png() -> bytes:
    import io
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore
        with mss.mss() as s:
            shot = s.grab(s.monitors[0])
            return mss.tools.to_png(shot.rgb, shot.size)
    except ImportError:
        from PIL import ImageGrab  # type: ignore
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()


@tool
def screenshot_desktop(max_width: int = 1600):
    """PNG screenshot of the whole desktop (e.g. to see the broker Chrome window).
    Needs an interactive desktop session (pm2 must run in the logged-in user session, not as a service)."""
    png = _grab_png()
    try:  # downscale to keep payload small
        import io
        from PIL import Image as PILImage  # type: ignore
        im = PILImage.open(io.BytesIO(png))
        if im.width > max_width:
            im = im.resize((max_width, int(im.height * max_width / im.width)))
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            png = buf.getvalue()
    except Exception:
        pass
    return Image(data=png, format="png")


# --------------------------------------------------------------------------- auth + app
def _load_token() -> str:
    tok = os.environ.get("HARK_MCP_TOKEN", "").strip()
    if not tok and IS_WIN:  # pm2 resurrect may lose env; fall back to the persisted user/machine var
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        tok = str(winreg.QueryValueEx(k, "HARK_MCP_TOKEN")[0]).strip()
                        if tok:
                            break
                except OSError:
                    pass
        except ImportError:
            pass
    return tok


class BearerAuth:
    """Pure ASGI middleware: every HTTP/WebSocket request must carry the bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        got = b""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                got = v.strip()
                break
        if hmac.compare_digest(got, self.expected):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        body = b'{"error":"unauthorized"}'
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"www-authenticate", b'Bearer realm="hark-mcp"'),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def build_app(token: str):
    return BearerAuth(mcp.streamable_http_app(), token)


def main() -> None:
    token = _load_token()
    if len(token) < 32:
        print("FATAL: HARK_MCP_TOKEN is missing or shorter than 32 characters. Run install.ps1.",
              file=sys.stderr)
        sys.exit(2)
    if IS_WIN:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    import uvicorn
    host = os.environ.get("HARK_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("HARK_MCP_PORT", "8765"))
    print(f"hark-mcp listening on http://{host}:{port}/mcp  live_trading={live_trading()} readonly={READONLY}",
          flush=True)
    uvicorn.run(build_app(token), host=host, port=port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
