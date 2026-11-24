"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { getOrCreateApprovalKey, stableJson, toBase64, type ApprovalKey } from "@/lib/approvalKey";

type FeedItem = { id: string; kind: string; message: string; at: string; detail?: string };
type Approval = {
  id: string;
  run_id: string;
  action: string;
  summary: Record<string, unknown>;
  expires_at: string;
  signing_payload: string;
};
type KeyBundle = ApprovalKey;

async function api(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  headers.set("X-User-ID", "local-dev-user");
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return fetch(`/api/${path}`, { ...init, headers, cache: "no-store" });
}

export default function Home() {
  const [task, setTask] = useState("");
  const [runId, setRunId] = useState("");
  const [status, setStatus] = useState("Ready");
  const [events, setEvents] = useState<FeedItem[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [working, setWorking] = useState(false);
  const [keyStatus, setKeyStatus] = useState("Checking approval key…");
  const [error, setError] = useState("");
  const [integrationError, setIntegrationError] = useState("");
  const sourceRef = useRef<EventSource | null>(null);
  const keyRef = useRef<KeyBundle | null>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const bundle = await getOrCreateApprovalKey();
        const response = await api("approval-keys", { method: "POST", body: JSON.stringify({ public_key: bundle.publicKey }) });
        if (!response.ok) throw new Error((await response.json()).detail ?? "Could not register approval key");
        if (live) {
          keyRef.current = bundle;
          setKeyStatus("Approval key ready · private key stays in this browser");
        }
      } catch (cause) {
        if (live) setKeyStatus(cause instanceof Error ? cause.message : "Approval key setup failed");
      }
    })();
    return () => { live = false; sourceRef.current?.close(); };
  }, []);

  const loadApproval = useCallback(async (id: string) => {
    const response = await api(`approvals/${id}`);
    if (!response.ok) throw new Error("Approval request is no longer available");
    setApproval(await response.json());
  }, []);

  const connectEvents = useCallback((id: string) => {
    sourceRef.current?.close();
    const source = new EventSource(`/api/runs/${id}/events`);
    sourceRef.current = source;
    const add = (kind: string, event: MessageEvent<string>) => {
      let data: Record<string, unknown> = {};
      try { data = JSON.parse(event.data) as Record<string, unknown>; } catch { /* malformed event is ignored */ }
      const message = typeof data.message === "string" ? data.message : kind.replaceAll("_", " ");
      const detail = typeof data.pull_request_url === "string" ? data.pull_request_url : undefined;
      setEvents((items) => [...items, { id: `${Date.now()}-${Math.random()}`, kind, message, detail, at: new Date().toLocaleTimeString() }]);
      if (kind === "run_status" && typeof data.status === "string") setStatus(data.status.replaceAll("_", " "));
      if (kind === "approval_required" && typeof data.approval_id === "string") {
        setStatus("Waiting for your approval");
        void loadApproval(data.approval_id);
      }
      if (kind === "approval_decided" && data.decision) setStatus(`Approval ${data.decision}`);
      if (kind === "run_completed" || kind === "run_failed") {
        setStatus(kind === "run_completed" ? "Completed" : "Failed");
        source.close();
      }
    };
    for (const kind of ["progress", "run_status", "approval_required", "approval_decided", "run_completed", "run_failed"]) {
      source.addEventListener(kind, (event) => add(kind, event as MessageEvent<string>));
    }
    source.onerror = () => setStatus((value) => value === "Completed" || value === "Failed" ? value : "Reconnecting to run stream…");
  }, [loadApproval]);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setEvents([]);
    setApproval(null);
    if (!keyRef.current) { setError("Approval key is not ready yet."); return; }
    setWorking(true);
    try {
      const response = await api("runs", { method: "POST", body: JSON.stringify({ task }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Could not start RelayFlow");
      setRunId(result.id);
      setStatus("Queued");
      connectEvents(result.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start RelayFlow");
    } finally {
      setWorking(false);
    }
  }

  async function connectIntegration(provider: string) {
    setIntegrationError("");
    try {
      const response = await api(`integrations/${provider}/oauth/start`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? `Could not connect ${provider}`);
      window.location.assign(result.authorization_url as string);
    } catch (cause) {
      setIntegrationError(cause instanceof Error ? cause.message : "Integration setup failed");
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!approval || !keyRef.current) return;
    setWorking(true);
    setError("");
    try {
      const challenge = JSON.parse(approval.signing_payload) as Record<string, unknown>;
      const signedText = stableJson({ ...challenge, decision });
      const signature = new Uint8Array(await crypto.subtle.sign("Ed25519", keyRef.current.privateKey, new TextEncoder().encode(signedText)));
      const response = await api(`approvals/${approval.id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, signature: toBase64(signature) }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Could not submit approval");
      setApproval(null);
      setStatus(decision === "approve" ? "Approved · continuing" : "Rejected · stopping publication");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not submit approval");
    } finally {
      setWorking(false);
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-5 py-8 sm:px-10 lg:py-12">
      <header className="mb-12 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="grid size-11 place-items-center rounded-2xl bg-[#137565] text-white shadow-lg shadow-emerald-950/10">
            <span className="text-xl font-semibold">N</span>
          </div>
          <div>
            <p className="text-lg font-semibold tracking-tight">NexusRelay</p>
            <p className="text-xs text-[#66807b]">RelayFlow · durable workbench</p>
          </div>
        </div>
        <a className="rounded-xl border border-[#dbe7e3] bg-white px-4 py-2.5 text-sm font-semibold text-[#137565] transition hover:border-[#137565]" href="/commerce">OACP checkout ↗</a>
        <div className="hidden items-center gap-2 rounded-full border border-[#dbe7e3] bg-white/75 px-4 py-2 text-xs text-[#66807b] sm:flex">
          <span className="size-2 rounded-full bg-emerald-500" /> {keyStatus}
        </div>
      </header>

      <section className="grid gap-8 lg:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)]">
        <div className="rounded-[2rem] border border-[#dbe7e3] bg-white/90 p-7 shadow-[0_24px_80px_-45px_rgba(21,91,75,0.28)] sm:p-10">
          <div className="mb-9 flex items-start justify-between gap-4">
            <div>
              <p className="mb-3 text-xs font-bold uppercase tracking-[0.22em] text-[#137565]">New autonomous run</p>
              <h1 className="max-w-xl text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">Give long work a place to finish.</h1>
              <p className="mt-4 max-w-xl text-sm leading-6 text-[#66807b]">Research a topic, turn it into a specification, then review each external action before it reaches GitHub or Slack.</p>
            </div>
            <span className="hidden rounded-2xl bg-[#e9f5ef] px-3 py-2 text-xs font-semibold text-[#137565] md:inline-flex">Durable · resumable</span>
          </div>

          <form onSubmit={start} className="space-y-5">
            <label className="block">
              <span className="mb-2 block text-sm font-semibold">What should RelayFlow work on?</span>
              <textarea
                value={task}
                onChange={(event) => setTask(event.target.value)}
                minLength={8}
                maxLength={4000}
                required
                rows={6}
                placeholder="Research the current landscape for… Draft a technical specification with risks and acceptance criteria."
                className="w-full resize-y rounded-2xl border border-[#dbe7e3] bg-[#fbfdfc] px-4 py-4 text-sm leading-6 outline-none transition placeholder:text-[#91a8a2] focus:border-[#137565] focus:ring-4 focus:ring-emerald-900/5"
              />
            </label>
            <div className="flex flex-col justify-between gap-4 border-t border-[#edf2f0] pt-5 sm:flex-row sm:items-center">
              <div>
                <p className="text-sm font-semibold">Connected destinations</p>
                <p className="mt-1 text-xs text-[#66807b]">Repository and Slack channel are configured by the workspace.</p>
              </div>
              <button disabled={working || task.trim().length < 8} className="inline-flex items-center justify-center gap-2 rounded-xl bg-[#137565] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#0d5a4f] disabled:cursor-not-allowed disabled:opacity-50">
                {working ? "Starting…" : "Start RelayFlow"}<span aria-hidden="true">↗</span>
              </button>
            </div>
          </form>
          {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
          <div className="mt-7 flex items-start gap-3 rounded-2xl bg-[#f3f8f5] p-4 text-xs leading-5 text-[#55716a]">
            <span className="mt-0.5 text-base">◈</span>
            <p>{keyStatus}. Only progress summaries are streamed; private model reasoning is not exposed. Publication requires a signed approval from this browser key.</p>
          </div>
        </div>

        <aside className="rounded-[2rem] border border-[#dbe7e3] bg-[#fafffc] p-7 sm:p-8">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-[#137565]">Live activity</p>
              <h2 className="mt-2 text-xl font-semibold">{status}</h2>
            </div>
            {runId && <span className="max-w-32 truncate rounded-full bg-white px-3 py-1.5 font-mono text-[10px] text-[#66807b]">{runId}</span>}
          </div>
          <div className="mt-7 min-h-72 space-y-3">
            {events.length === 0 ? (
              <div className="grid min-h-72 place-items-center rounded-2xl border border-dashed border-[#cfdfd9] px-6 text-center">
                <div><div className="mx-auto mb-3 grid size-11 place-items-center rounded-2xl bg-[#e9f5ef] text-lg text-[#137565]">⌁</div><p className="text-sm font-medium">No activity yet</p><p className="mt-1 text-xs text-[#66807b]">Run updates and approvals will appear here.</p></div>
              </div>
            ) : events.map((item) => (
              <article key={item.id} className="flex gap-3 rounded-2xl border border-[#e5eeea] bg-white p-4">
                <span className={`mt-1 size-2 shrink-0 rounded-full ${item.kind === "approval_required" ? "bg-[#e49b44]" : "bg-[#53a393]"}`} />
                <div className="min-w-0 flex-1"><p className="text-sm leading-5">{item.message}</p>{item.detail && <a className="mt-2 block truncate text-xs text-[#137565] underline" href={item.detail} target="_blank" rel="noreferrer">{item.detail}</a>}<time className="mt-2 block text-[10px] text-[#91a8a2]">{item.at}</time></div>
              </article>
            ))}
          </div>
          <div className="mt-6 border-t border-[#e7efeb] pt-5">
            <p className="text-xs font-semibold uppercase tracking-wider text-[#66807b]">Run lifecycle</p>
            <div className="mt-4 grid grid-cols-4 gap-2 text-center text-[10px] text-[#66807b]">
              {["Research", "Draft", "GitHub", "Slack"].map((step, index) => <div key={step}><div className={`mx-auto mb-2 grid size-7 place-items-center rounded-full ${events.length > index ? "bg-[#137565] text-white" : "bg-[#e9f1ed]"}`}>{index + 1}</div>{step}</div>)}
            </div>
          </div>
        </aside>
        <div className="rounded-3xl border border-[#dbe7e3] bg-white/80 p-6 lg:col-span-2">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
            <div><p className="text-xs font-bold uppercase tracking-[0.2em] text-[#137565]">Connections</p><h2 className="mt-2 text-lg font-semibold">Connect the workspaces RelayFlow can reach</h2><p className="mt-1 text-xs text-[#66807b]">Provider tokens are encrypted at rest and sent to MCP servers only in transport headers.</p></div>
            <div className="flex flex-wrap gap-2">{[["GitHub", "github"], ["Slack", "slack"], ["Google Drive", "google_drive"], ["Microsoft 365", "microsoft_graph"]].map(([label, provider]) => <button key={provider} onClick={() => void connectIntegration(provider)} className="rounded-xl border border-[#dbe7e3] px-3 py-2 text-xs font-semibold text-[#425c56] transition hover:border-[#137565] hover:text-[#137565]">Connect {label}</button>)}</div>
          </div>
          {integrationError && <p role="alert" className="mt-4 text-xs text-red-700">{integrationError}</p>}
        </div>
      </section>

      {approval && <div className="fixed inset-0 z-50 grid place-items-center bg-[#112522]/45 p-4 backdrop-blur-sm" role="presentation">
        <section role="dialog" aria-modal="true" aria-labelledby="approval-title" className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-3xl bg-white p-6 shadow-2xl sm:p-8">
          <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-[0.2em] text-[#b46a20]">Human approval required</p><h2 id="approval-title" className="mt-2 text-2xl font-semibold">Review before it leaves NexusRelay</h2></div><button aria-label="Close approval" onClick={() => setApproval(null)} className="grid size-9 place-items-center rounded-full bg-[#f1f6f3] text-[#66807b]">×</button></div>
          <div className="mt-6 rounded-2xl bg-[#f5faf7] p-5">
            {Object.entries(approval.summary).map(([key, value]) => <div key={key} className="border-b border-[#e4ede8] py-3 last:border-0"><p className="text-[10px] font-bold uppercase tracking-wider text-[#66807b]">{key.replaceAll("_", " ")}</p><p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{typeof value === "string" ? value : JSON.stringify(value)}</p></div>)}
          </div>
          <p className="mt-4 text-xs leading-5 text-[#66807b]">This signature binds the displayed action, target, and content. Approving a GitHub change does not approve the later Slack message.</p>
          <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end"><button disabled={working} onClick={() => void decide("reject")} className="rounded-xl border border-[#dbe7e3] px-5 py-3 text-sm font-semibold text-[#425c56] disabled:opacity-50">Reject</button><button disabled={working} onClick={() => void decide("approve")} className="rounded-xl bg-[#137565] px-5 py-3 text-sm font-semibold text-white disabled:opacity-50">{working ? "Signing…" : "Approve and continue"}</button></div>
        </section>
      </div>}
    </main>
  );
}

