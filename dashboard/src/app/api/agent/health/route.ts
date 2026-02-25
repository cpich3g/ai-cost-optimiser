import { NextResponse } from "next/server";

function getAgentConfig() {
  const baseUrl = process.env.AGENT_FUNCTION_BASE_URL?.replace(/\/+$/, "");
  const functionKey = process.env.AGENT_FUNCTION_KEY;
  if (!baseUrl || !functionKey) {
    return null;
  }
  return { baseUrl, functionKey };
}

export async function GET() {
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

  const target = `${config.baseUrl}/api/health?code=${encodeURIComponent(config.functionKey)}`;
  const response = await fetch(target, { cache: "no-store" });
  const text = await response.text();
  const contentType = response.headers.get("content-type") ?? "application/json";
  return new Response(text, {
    status: response.status,
    headers: { "content-type": contentType },
  });
}

