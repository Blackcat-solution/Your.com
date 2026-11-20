import { createHmac, timingSafeEqual } from "node:crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const secret = process.env.FRONTEND_WEBHOOK_SECRET;
  if (!secret || Buffer.byteLength(secret) < 32) return Response.json({ error: "Webhook secret is not configured" }, { status: 503 });
  const body = Buffer.from(await request.arrayBuffer());
  const supplied = request.headers.get("x-nexus-signature")?.replace(/^sha256=/, "") ?? "";
  const expected = createHmac("sha256", secret).update(body).digest("hex");
  const left = Buffer.from(supplied, "hex");
  const right = Buffer.from(expected, "hex");
  if (left.length !== right.length || !timingSafeEqual(left, right)) return Response.json({ error: "Invalid signature" }, { status: 401 });
  let event: { event?: string; run_id?: string; approval_id?: string };
  try { event = JSON.parse(body.toString("utf8")) as typeof event; }
  catch { return Response.json({ error: "Invalid JSON" }, { status: 400 }); }
  if (event.event !== "approval_required" || !event.run_id || !event.approval_id) return Response.json({ error: "Invalid event" }, { status: 422 });
  return Response.json({ accepted: true }, { status: 202 });
}

