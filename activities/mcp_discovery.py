"""Discover Azure resources via hosted MCP server using Function App managed identity.

Uses httpx for Streamable HTTP transport and httpx-sse for legacy SSE fallback.
Auth is handled via DefaultAzureCredential (managed identity in Azure, CLI locally).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Callable, Coroutine
from urllib.parse import urljoin, urlparse

import httpx
from azure.identity.aio import DefaultAzureCredential

logger = logging.getLogger(__name__)

RESOURCE_ID_RE = re.compile(
    r"/subscriptions/[^\"'\s]+/resourceGroups/[^\"'\s]+/providers/[^\"'\s]+",
    re.IGNORECASE,
)

DEFAULT_MCP_URL = (
    ""
)


# ---------------------------------------------------------------------------
# Token acquisition
# ---------------------------------------------------------------------------

async def _detect_mcp_auth(base_url: str) -> tuple[str | None, str | None]:
    """Read MCP oauth-protected-resource metadata for scope and tenant."""
    url = f"{base_url.rstrip('/')}/.well-known/oauth-protected-resource"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None, None
            data = resp.json()
    except Exception:
        return None, None

    scope = None
    tenant_id = None

    scopes = data.get("scopes_supported", [])
    if scopes and isinstance(scopes[0], str):
        scope = scopes[0]

    servers = data.get("authorization_servers", [])
    if servers and isinstance(servers[0], str):
        parts = urlparse(servers[0]).path.strip("/").split("/")
        if parts and re.match(r"^[0-9a-f-]{36}$", parts[0], re.IGNORECASE):
            tenant_id = parts[0]

    return scope, tenant_id


def _build_scope_candidates(raw: str) -> list[str]:
    """Build token-request scope candidates from a raw scope string."""
    cleaned = raw.strip().rstrip("/")
    candidates: list[str] = []

    def _add(s: str) -> None:
        if s not in candidates:
            candidates.append(s)

    _add(cleaned)
    if re.match(r"^[0-9a-f-]{36}/", cleaned, re.IGNORECASE):
        _add(f"api://{cleaned}")
    for s in list(candidates):
        if not s.endswith("/.default"):
            _add(f"{s}/.default")
    return candidates


async def _acquire_mcp_token(base_url: str) -> str:
    """Acquire a bearer token for the MCP server using managed identity."""
    override = os.getenv("MCP_SERVER_SCOPE", "").strip() or None
    detected_scope, _tenant = await _detect_mcp_auth(base_url)
    raw_scope = override or detected_scope
    if not raw_scope:
        raise RuntimeError(
            "Cannot resolve MCP auth scope. Set MCP_SERVER_SCOPE app setting."
        )

    candidates = _build_scope_candidates(raw_scope)
    credential = DefaultAzureCredential()
    errors: list[str] = []
    try:
        for scope in candidates:
            try:
                token = await credential.get_token(scope)
                if token and token.token:
                    logger.info("Acquired MCP token with scope %s", scope)
                    return token.token
            except Exception as exc:
                errors.append(f"[{scope}] {str(exc)[:200]}")
    finally:
        await credential.close()

    raise RuntimeError(
        f"Failed to acquire MCP token via managed identity. "
        f"Tried: {', '.join(candidates)}. "
        f"Ensure the Function App MI has an app-role assignment on the MCP "
        f"app registration. Errors: {' | '.join(errors)}"
    )


# ---------------------------------------------------------------------------
# Lightweight MCP JSON-RPC helpers
# ---------------------------------------------------------------------------

RpcFn = Callable[..., Coroutine[Any, Any, dict]]


async def _streamable_rpc_session(
    url: str, token: str, resource_group: str,
) -> dict:
    """Complete MCP discovery using Streamable HTTP (POST) transport."""
    session_id: str | None = None
    request_id = 0
    headers_base = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30)) as client:

        async def rpc(method: str, params: dict | None = None) -> dict:
            nonlocal request_id, session_id
            request_id += 1
            msg: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                msg["params"] = params
            h = dict(headers_base)
            if session_id:
                h["mcp-session-id"] = session_id

            async with client.stream("POST", url, json=msg, headers=h) as resp:
                if resp.status_code not in (200, 202):
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    raise RuntimeError(f"HTTP {resp.status_code}: {body}")
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    session_id = sid
                if resp.status_code == 202:
                    return {}
                ct = resp.headers.get("content-type", "")
                if "text/event-stream" in ct:
                    return await _read_sse_result(resp)
                body = await resp.aread()
                data = json.loads(body)
                if "error" in data:
                    raise RuntimeError(f"MCP error: {data['error']}")
                return data.get("result", {})

        async def notify(method: str, params: dict | None = None) -> None:
            nonlocal session_id
            msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            h = dict(headers_base)
            if session_id:
                h["mcp-session-id"] = session_id
            await client.post(url, json=msg, headers=h)

        await rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cost-optimiser-func", "version": "0.1.0"},
        })
        await notify("notifications/initialized")
        tools_result = await rpc("tools/list", {})
        tools = tools_result.get("tools", [])
        return await _discover_with_tools(tools, rpc, resource_group, "streamable-http", url)


async def _sse_rpc_session(
    url: str, token: str, resource_group: str,
) -> dict:
    """Complete MCP discovery using legacy SSE (GET + POST) transport."""
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30)) as client:
        # Pre-flight: check the endpoint returns 200 with text/event-stream
        # before opening the full SSE connection (avoids opaque httpx-sse errors)
        probe = await client.get(url, headers=headers, follow_redirects=True)
        ct = probe.headers.get("content-type", "")
        if probe.status_code != 200:
            body = probe.text[:300]
            raise RuntimeError(
                f"SSE endpoint returned HTTP {probe.status_code} "
                f"(content-type: {ct!r}): {body}"
            )
        if "text/event-stream" not in ct:
            raise RuntimeError(
                f"SSE endpoint returned content-type {ct!r} (expected text/event-stream). "
                f"Body preview: {probe.text[:200]}"
            )

        try:
            from httpx_sse import aconnect_sse
        except ImportError:
            raise RuntimeError("httpx-sse is required for legacy SSE MCP transport.")

        async with aconnect_sse(client, "GET", url, headers=headers) as event_source:
            msg_url: str | None = None
            async for sse in event_source.aiter_sse():
                if sse.event == "endpoint":
                    raw = sse.data
                    msg_url = raw if raw.startswith("http") else urljoin(url, raw)
                    break
            if not msg_url:
                raise RuntimeError("No endpoint event from SSE stream.")

            pending: dict[int, asyncio.Future[dict]] = {}
            request_id = 0

            async def _reader() -> None:
                try:
                    async for sse in event_source.aiter_sse():
                        if sse.event in ("message", None):
                            try:
                                data = json.loads(sse.data)
                                mid = data.get("id")
                                if mid is not None and mid in pending:
                                    pending[mid].set_result(data)
                            except (json.JSONDecodeError, TypeError):
                                pass
                except Exception:
                    pass

            reader_task = asyncio.create_task(_reader())
            try:
                async def rpc(method: str, params: dict | None = None) -> dict:
                    nonlocal request_id
                    request_id += 1
                    mid = request_id
                    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": mid, "method": method}
                    if params is not None:
                        msg["params"] = params
                    loop = asyncio.get_running_loop()
                    future: asyncio.Future[dict] = loop.create_future()
                    pending[mid] = future
                    post_h = {**headers, "Content-Type": "application/json"}
                    await client.post(msg_url, json=msg, headers=post_h)
                    try:
                        result = await asyncio.wait_for(future, timeout=60)
                    finally:
                        pending.pop(mid, None)
                    if "error" in result:
                        raise RuntimeError(f"MCP error: {result['error']}")
                    return result.get("result", {})

                async def notify(method: str, params: dict | None = None) -> None:
                    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
                    if params is not None:
                        msg["params"] = params
                    post_h = {**headers, "Content-Type": "application/json"}
                    await client.post(msg_url, json=msg, headers=post_h)

                await rpc("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cost-optimiser-func", "version": "0.1.0"},
                })
                await notify("notifications/initialized")
                tools_result = await rpc("tools/list", {})
                tools = tools_result.get("tools", [])
                return await _discover_with_tools(tools, rpc, resource_group, "sse", url)
            finally:
                reader_task.cancel()
                try:
                    await reader_task
                except asyncio.CancelledError:
                    pass


async def _read_sse_result(resp: httpx.Response) -> dict:
    """Extract the first JSON-RPC result from an SSE streaming response."""
    async for line in resp.aiter_lines():
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                if "result" in data or "error" in data:
                    if "error" in data:
                        raise RuntimeError(f"MCP error: {data['error']}")
                    return data.get("result", {})
            except json.JSONDecodeError:
                continue
    return {}


# ---------------------------------------------------------------------------
# Tool ranking + resource discovery
# ---------------------------------------------------------------------------

def _score_tool(tool: dict) -> int:
    haystack = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
    score = 0
    if "resource" in haystack and "group" in haystack:
        score += 30
    if "list" in haystack and "resource" in haystack:
        score += 50
    if "resource graph" in haystack or "resourcegraph" in haystack:
        score += 70
    if "search" in haystack:
        score += 45
    if "query" in haystack:
        score += 40
    if "group_list" in haystack:
        score += 20
    return score


def _build_arg_candidates(tool_name: str, rg: str) -> list[dict]:
    base = [
        {"resourceGroup": rg},
        {"resource_group": rg},
        {"resourceGroupName": rg},
        {"name": rg},
        {"group": rg},
    ]
    queries = [
        {"query": f"resources | where resourceGroup =~ '{rg}' | project id, name, type, location"},
        {"query": f"resources | where resourceGroup == '{rg}'"},
        {"resourceGraphQuery": f"resources | where resourceGroup =~ '{rg}'"},
    ]
    lowered = tool_name.lower()
    if "search" in lowered or "graph" in lowered or "query" in lowered:
        return queries + base
    return base + queries


def _estimate_cost(resource_type: str, resource_id: str) -> float:
    hint = f"{resource_type} {resource_id}".lower()
    if "virtualmachines" in hint or "/vm" in hint:
        return 320
    if any(k in hint for k in ("microsoft.sql", "postgres", "mysql", "cosmos")):
        return 260
    if "disks" in hint or "snapshots" in hint:
        return 80
    if "appservice" in hint or "sites" in hint:
        return 140
    if "kubernetes" in hint or "container" in hint:
        return 190
    if "storage" in hint:
        return 55
    return 110


def _pick_str(*values: Any) -> str | None:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _in_group(rid: str, rg: str) -> bool:
    return f"/resourcegroups/{rg.lower()}/" in rid.lower()


def _collect_objects(obj: Any, out: list[dict]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _collect_objects(item, out)
    elif isinstance(obj, dict):
        out.append(obj)
        for v in obj.values():
            _collect_objects(v, out)


def _collect_texts(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            _collect_texts(item, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_texts(v, out)


def _normalise_resources(raw: Any, rg: str) -> list[dict]:
    resources: dict[str, dict] = {}

    objects: list[dict] = []
    _collect_objects(raw, objects)
    for c in objects:
        rid = _pick_str(c.get("resource_id"), c.get("resourceId"), c.get("id"))
        if not rid or not _in_group(rid, rg):
            continue
        rtype = _pick_str(c.get("type"), c.get("resourceType")) or "unknown"
        rname = _pick_str(c.get("name"), c.get("resourceName")) or rid
        loc = _pick_str(c.get("location"), c.get("region")) or "unknown"
        cost = c.get("monthly_cost") or c.get("monthlyCost") or c.get("cost")
        try:
            cost = float(cost) if cost is not None else None
        except (ValueError, TypeError):
            cost = None
        if cost is None:
            cost = _estimate_cost(rtype, rid)
        resources[rid.lower()] = {
            "resource_id": rid,
            "monthly_cost": round(cost, 2),
            "metrics": {
                "source": "azure-mcp", "resourceType": rtype,
                "resourceName": rname, "location": loc, "resourceGroup": rg,
            },
        }

    texts: list[str] = []
    _collect_texts(raw, texts)
    for blob in texts:
        for match in RESOURCE_ID_RE.findall(blob[:8000]):
            rid = match.strip()
            if not _in_group(rid, rg):
                continue
            key = rid.lower()
            if key in resources:
                continue
            resources[key] = {
                "resource_id": rid,
                "monthly_cost": _estimate_cost("unknown", rid),
                "metrics": {
                    "source": "azure-mcp", "resourceType": "unknown",
                    "resourceName": rid.split("/")[-1], "location": "unknown",
                    "resourceGroup": rg,
                },
            }
    return list(resources.values())[:60]


async def _discover_with_tools(
    tools: list[dict], rpc: RpcFn, rg: str, transport: str, endpoint: str,
) -> dict:
    scored = sorted(tools, key=_score_tool, reverse=True)
    positive = [t for t in scored if _score_tool(t) > 0]
    candidates = (positive or scored)[:8]
    if not candidates:
        raise RuntimeError("Azure MCP returned no tools.")

    call_errors: list[str] = []
    for tool in candidates:
        for args in _build_arg_candidates(tool["name"], rg):
            try:
                result = await rpc("tools/call", {"name": tool["name"], "arguments": args})
                content = result.get("content", [])
                parsed: list[Any] = []
                for item in content:
                    text = item.get("text") if isinstance(item, dict) else None
                    if text:
                        try:
                            parsed.append(json.loads(text))
                        except json.JSONDecodeError:
                            parsed.append(text)
                discovered = _normalise_resources(parsed, rg)
                if discovered:
                    return {
                        "resources": discovered,
                        "tool_name": tool["name"],
                        "transport": transport,
                        "endpoint": endpoint,
                    }
            except Exception as exc:
                call_errors.append(f"{tool['name']}: {str(exc)[:100]}")

    summary = " | ".join(call_errors[:4])
    raise RuntimeError(
        f"MCP did not return resources for '{rg}'. {summary or 'No tool produced results.'}"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def discover_resources(resource_group: str) -> dict:
    """Connect to hosted Azure MCP server, discover resources in the given RG.

    Returns dict with keys: resources, tool_name, transport, endpoint.
    Authentication uses the Function App's managed identity.
    """
    base_url = os.getenv("MCP_SERVER_URL", DEFAULT_MCP_URL).rstrip("/")
    token = await _acquire_mcp_token(base_url)
    token_preview = f"{token[:12]}...{token[-6:]}" if len(token) > 20 else "(short)"

    # Try Streamable HTTP first (POST-based), then legacy SSE (GET-based)
    transport_attempts = [
        ("streamable", f"{base_url}/mcp", _streamable_rpc_session),
        ("streamable", base_url, _streamable_rpc_session),
        ("sse", f"{base_url}/sse", _sse_rpc_session),
        ("sse", base_url, _sse_rpc_session),
    ]

    connect_errors: list[str] = []
    for _label, url, session_fn in transport_attempts:
        try:
            return await session_fn(url, token, resource_group)
        except Exception as exc:
            connect_errors.append(f"[{_label} {url}] {str(exc)[:200]}")

    raise RuntimeError(
        f"Could not connect to Azure MCP server at {base_url}. "
        f"Token acquired OK ({token_preview}). "
        f"Errors: {' | '.join(connect_errors)}"
    )
