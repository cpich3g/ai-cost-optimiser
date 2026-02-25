import { NextRequest, NextResponse } from "next/server";

function getAgentConfig() {
  const baseUrl = process.env.AGENT_FUNCTION_BASE_URL?.replace(/\/+$/, "");
  const functionKey = process.env.AGENT_FUNCTION_KEY;
  if (!baseUrl || !functionKey) {
    return null;
  }
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
  const target = `${config.baseUrl}/api/cost-optimization/report?code=${encodeURIComponent(config.functionKey)}`;
  const response = await fetch(target, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    cache: "no-store",
  });

  const text = await response.text();
  const contentType = response.headers.get("content-type") ?? "application/json";
  return new Response(text, {
    status: response.status,
    headers: { "content-type": contentType },
  });
}

