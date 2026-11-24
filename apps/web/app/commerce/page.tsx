"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { getOrCreateApprovalKey, stableJson, toBase64, type ApprovalKey } from "@/lib/approvalKey";

type KeyBundle = ApprovalKey;
type Intent = { id: string; merchant_name: string; merchant_id: string; order_id: string; amount_minor: number; currency: string; merchant_category: string; signing_payload: string };
type Issued = { checkout_token: string; expires_at: string; amount_minor: number; currency: string; merchant_id: string };

async function api(path: string, body?: unknown): Promise<Response> {
  return fetch(`/api/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-User-ID": "local-dev-user" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
}

export default function CommercePage() {
  const [merchantId, setMerchantId] = useState("demo-merchant");
  const [merchantName, setMerchantName] = useState("Demo Merchant");
  const [origin, setOrigin] = useState("https://shop.example.com");
  const [checkoutEndpoint, setCheckoutEndpoint] = useState("https://shop.example.com/checkout");
  const [orderId, setOrderId] = useState("order-demo-001");
  const [category, setCategory] = useState("computer_software_stores");
  const [signingKeyId, setSigningKeyId] = useState("merchant-key-1");
  const [manifestSignature, setManifestSignature] = useState("");
  const [amount, setAmount] = useState("1299");
  const [currency, setCurrency] = useState("usd");
  const [intent, setIntent] = useState<Intent | null>(null);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [status, setStatus] = useState("Ready for a signed purchase approval");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const keyRef = useRef<KeyBundle | null>(null);

  useEffect(() => {
    let live = true;
    void getOrCreateApprovalKey().then(async (bundle) => {
      const registered = await api("approval-keys", { public_key: bundle.publicKey });
      if (!registered.ok) throw new Error("Register the browser approval key before purchasing");
      if (live) keyRef.current = bundle;
    }).catch((cause: unknown) => { if (live) setError(cause instanceof Error ? cause.message : "Approval key unavailable"); });
    return () => { live = false; };
  }, []);

  async function createIntent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setIssued(null); setIntent(null);
    try {
      const amountMinor = Number(amount);
      if (!Number.isSafeInteger(amountMinor) || amountMinor < 1) throw new Error("Enter a positive amount in minor units, for example 1299 for USD 12.99.");
      const manifest = {
        protocol_version: "oacp/0.1",
        merchant_id: merchantId,
        merchant_name: merchantName,
        origin,
        checkout_endpoint: checkoutEndpoint,
        accepted_currencies: [currency.toUpperCase()],
        mcc_category: category,
        payment_capabilities: ["oacp_checkout_token_v1"],
        signing_key_id: signingKeyId,
        signature: manifestSignature,
      };
      const response = await api("commerce/cart-intents", {
        merchant_id: merchantId,
        order_id: orderId,
        amount_minor: amountMinor,
        currency,
        merchant_category: category,
        lines: [{ sku: "demo-sku", title: "Demo purchase", quantity: 1, unit_amount_minor: amountMinor }],
        merchant_manifest: manifest,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Could not create cart intent");
      setIntent(result as Intent);
      setStatus("Cart locked; review and sign to issue a spend-limited card");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create cart intent"); }
    finally { setBusy(false); }
  }

  async function approvePurchase() {
    if (!intent || !keyRef.current) return;
    setBusy(true); setError("");
    try {
      const signature = new Uint8Array(await crypto.subtle.sign("Ed25519", keyRef.current.privateKey, new TextEncoder().encode(stableJson(JSON.parse(intent.signing_payload)))));
      const response = await api(`commerce/cart-intents/${intent.id}/approve`, { signature: toBase64(signature) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Stripe Issuing did not approve card creation");
      setIssued(result as Issued);
      setStatus("Approved · one-time checkout token issued");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Purchase approval failed"); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto min-h-screen max-w-4xl px-5 py-8 sm:px-10 lg:py-12">
    <a className="text-sm font-semibold text-[#137565]" href="/">← NexusRelay workbench</a>
    <header className="mb-8 mt-8"><p className="text-xs font-bold uppercase tracking-[0.22em] text-[#137565]">OACP · secure checkout</p><h1 className="mt-3 text-3xl font-semibold tracking-tight">Approve a purchase with a spend cap.</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-[#66807b]">Review the merchant, items, exact total and category. After you sign, the payment service creates a virtual card limited to that amount and category.</p></header>
    <div className="grid gap-6 md:grid-cols-[1.1fr_0.9fr]">
      <form onSubmit={createIntent} className="space-y-4 rounded-3xl border border-[#dbe7e3] bg-white p-6 shadow-sm">
        <label className="block text-sm font-medium">Merchant ID<input value={merchantId} onChange={(e) => setMerchantId(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label>
        <label className="block text-sm font-medium">Merchant name<input value={merchantName} onChange={(e) => setMerchantName(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label>
        <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm font-medium">Merchant origin<input type="url" value={origin} onChange={(e) => setOrigin(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label><label className="block text-sm font-medium">Checkout endpoint<input type="url" value={checkoutEndpoint} onChange={(e) => setCheckoutEndpoint(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label></div>
        <label className="block text-sm font-medium">Order ID<input value={orderId} onChange={(e) => setOrderId(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label>
        <label className="block text-sm font-medium">Stripe Issuing merchant category<input value={category} onChange={(e) => setCategory(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label>
        <label className="block text-sm font-medium">Merchant manifest signing key ID<input value={signingKeyId} onChange={(e) => setSigningKeyId(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label>
        <label className="block text-sm font-medium">Merchant manifest signature (base64url)<input value={manifestSignature} onChange={(e) => setManifestSignature(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5 font-mono text-xs" placeholder="Signature from the merchant's signed manifest" required /></label>
        <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm font-medium">Total in minor units<input inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value)} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label><label className="block text-sm font-medium">Currency<input value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toLowerCase())} className="mt-2 w-full rounded-xl border border-[#dbe7e3] px-3 py-2.5" required /></label></div>
        <button disabled={busy} className="w-full rounded-xl bg-[#e9f5ef] px-4 py-3 text-sm font-semibold text-[#137565] disabled:opacity-50">Create cart intent</button>
      </form>
      <section className="rounded-3xl border border-[#dbe7e3] bg-[#fafffc] p-6">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-[#137565]">Purchase flow</p><h2 className="mt-2 text-xl font-semibold">{status}</h2>
        {intent && <div className="mt-5 space-y-3 rounded-2xl bg-white p-4 text-sm"><div className="flex justify-between gap-3"><span className="text-[#66807b]">Merchant</span><strong>{intent.merchant_name}</strong></div><div className="flex justify-between gap-3"><span className="text-[#66807b]">Order</span><strong>{intent.order_id}</strong></div><div className="flex justify-between gap-3"><span className="text-[#66807b]">Category</span><strong>{intent.merchant_category}</strong></div><div className="flex justify-between gap-3 border-t border-[#edf2f0] pt-3"><span className="text-[#66807b]">Exact cap</span><strong>{intent.currency.toUpperCase()} {(intent.amount_minor / 100).toFixed(2)}</strong></div><button disabled={busy || !!issued} onClick={() => void approvePurchase()} className="mt-2 w-full rounded-xl bg-[#137565] px-4 py-3 font-semibold text-white disabled:opacity-50">{busy ? "Signing…" : "Approve Purchase"}</button></div>}
        {issued && <div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50 p-4"><p className="text-sm font-semibold text-emerald-900">Checkout token issued</p><p className="mt-2 break-all font-mono text-xs text-emerald-800">{issued.checkout_token}</p><button onClick={() => void navigator.clipboard.writeText(issued.checkout_token)} className="mt-3 rounded-lg bg-white px-3 py-2 text-xs font-semibold text-[#137565]">Copy token</button><p className="mt-3 text-xs leading-5 text-emerald-900">This opaque token expires {new Date(issued.expires_at).toLocaleTimeString()}. No card number or CVC is returned to this page.</p></div>}
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        <p className="mt-5 text-xs leading-5 text-[#66807b]">The merchant redeems this token from its server using its registered credential and an ephemeral X25519 public key. The payment envelope is encrypted to the merchant and is single-use.</p>
      </section>
    </div>
  </main>;
}

