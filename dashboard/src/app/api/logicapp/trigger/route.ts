import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  const triggerUrl = process.env.LOGICAPP_TRIGGER_URL;
  if (!triggerUrl) {
    return NextResponse.json(
      { error: "Missing LOGICAPP_TRIGGER_URL in dashboard environment." },
      { status: 500 },
    );
  }

  const body = await request.text();
  const response = await fetch(triggerUrl, {
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

