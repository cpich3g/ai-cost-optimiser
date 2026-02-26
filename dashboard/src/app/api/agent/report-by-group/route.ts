import { NextRequest, NextResponse } from "next/server";

/**
 * Thin proxy to the Function App's /api/cost-optimization/report-by-group endpoint.
 * MCP discovery + auth is handled server-side by the Function App's managed identity.
 */

function getAgentConfig() {
  const baseUrl = process.env.AGENT_FUNCTION_BASE_URL?.replace(/\/+$/, "");
  const functionKey = process.env.AGENT_FUNCTION_KEY;
  if (!baseUrl || !functionKey) return null;
  return { baseUrl, functionKey };
}

export async function POST(request: NextRequest) {
  const config = getAgentConfig();
  if (!config) {
    return NextResponse.json(
      {
        error:
          "Missing AGENT_FUNCTION_BASE_URL or AGENT_FUNCTION_KEY in dashboard environment.",
      },
      { status: 500 },
    );
  }

  const body = await request.text();
  const target = `${config.baseUrl}/api/cost-optimization/report-by-group?code=${encodeURIComponent(config.functionKey)}`;

  try {
    const upstream = await fetch(target, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
    });

    const text = await upstream.text();
    const ct = upstream.headers.get("content-type") ?? "application/json";
    return new Response(text, {
      status: upstream.status,
      headers: { "content-type": ct },
    });
  } catch (err) {
    return NextResponse.json(
      { error: `Failed to reach Function App: ${err}` },
      { status: 502 },
    );
  }
}
