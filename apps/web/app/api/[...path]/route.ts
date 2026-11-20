import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await context.params;
  const base = process.env.AGENT_API_URL ?? "http://localhost:8000";
  const target = new URL(`/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, base);
  const headers = new Headers();
  for (const name of ["authorization", "x-user-id", "last-event-id", "content-type", "x-merchant-token"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const method = request.method;
  const upstream = await fetch(target, {
    method,
    headers,
    body: method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
    signal: request.signal,
  });
  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.set("Cache-Control", "no-cache, no-transform");
  responseHeaders.set("X-Accel-Buffering", "no");
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;

