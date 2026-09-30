"""Static egress allowlist.

Walks app/ and tools/ without importing them, so it passes on an empty tree and
on any tree whose only outbound hosts are Goodreads and the Anthropic API.
Health data stays home: any other host is a failing test.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("app", "tools")

ALLOWED_HOSTS = frozenset({"www.goodreads.com", "goodreads.com", "api.anthropic.com"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
LAN_PREFIX = "192.168."

BANNED_IMPORTS = (("requests",), ("urllib", "request"))

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


def host_allowed(host: str) -> bool:
    return host in ALLOWED_HOSTS or host in LOOPBACK_HOSTS or host.startswith(LAN_PREFIX)


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
        if parts == ("urllib",) and any(a.name == "request" for a in node.names):
            return "urllib.request"
    return None


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
        if banned:
            found.append(Violation(path, node.lineno, "import", f"{banned} (use httpx)"))

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            for match in URL_RE.finditer(node.value):
                host = host_from_url(match.group(0))
                if host and not host_allowed(host):
                    found.append(Violation(path, node.lineno, "url", host))

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
