"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  ArrowDownLeft, ArrowRight, Check, CheckCheck, ChevronRight, CircleHelp,
  Clock3, CreditCard, Download, History, LayoutDashboard, LoaderCircle,
  LockKeyhole, LogOut, MessageSquare, Plus, Send, ShieldCheck, Sparkles,
  TriangleAlert, Wallet, X,
} from "lucide-react";
import { apiUrl, money, supabase, type Health, type Payment, type Summary } from "@/lib/client";

type Message = { role: "user" | "assistant"; text: string };
const suggestions = [
  { title: "Collect a payment", prompt: "Collect USD 25.00 for design consultation", icon: CreditCard },
  { title: "Try a larger checkout", prompt: "Collect USD 350.00 for website development", icon: ShieldCheck },
  { title: "Create a euro checkout", prompt: "Collect EUR 45.00 for a product workshop", icon: Wallet },
];

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [authReady, setAuthReady] = useState(!supabase);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [summary, setSummary] = useState<Summary>({ completed_count: 0, totals: {}, label: "" });
  const [selected, setSelected] = useState<Payment | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [riskAccepted, setRiskAccepted] = useState(false);
  const [tab, setTab] = useState<"assistant" | "history">("assistant");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const returnHandled = useRef(false);
  const messageEnd = useRef<HTMLDivElement>(null);

  const api = useCallback(async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
    const response = await fetch(`${apiUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Check the payment details and try again.");
    }
    return data as T;
  }, [token]);

  const refresh = useCallback(async () => {
    const [history, totals] = await Promise.all([
      api<Payment[]>("/api/payments"),
      api<Summary>("/api/summary"),
    ]);
    setPayments(history);
    setSummary(totals);
  }, [api]);

  useEffect(() => {
    fetch(`${apiUrl}/api/health`)
      .then((response) => {
        if (!response.ok) throw new Error("Backend health check failed.");
        return response.json();
      })
      .then(setHealth)
      .catch(() => setError("Backend unavailable. Start FastAPI on port 8000, then refresh."));
    if (!supabase) return;
    supabase.auth.getSession().then(({ data, error }) => {
      if (error) setError(error.message);
      setToken(data.session?.access_token ?? null);
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setToken(session?.access_token ?? null);
      setAuthReady(true);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const canAccess = !!health && authReady && (health.auth_mode === "local" || !!token);

  useEffect(() => {
    if (!canAccess) return;
    let active = true;
    async function load() {
      try {
        await refresh();
        if (!active) return;
        const query = new URLSearchParams(window.location.search);
        const paymentId = query.get("payment");
        if (!paymentId || returnHandled.current) return;
        returnHandled.current = true;
        if (query.get("checkout") === "cancel") {
          setNotice("Checkout was cancelled. No capture was requested. You can resume it from history.");
          window.history.replaceState({}, "", "/");
          return;
        }
        if (query.get("checkout") === "return") {
          setBusy(true);
          setNotice("Checking buyer approval and verifying your Sandbox payment...");
          const payment = await api<Payment>(`/api/payments/${encodeURIComponent(paymentId)}/capture`, { method: "POST" });
          if (active) {
            setSelected(payment);
            setNotice("Sandbox payment verified. Your receipt is ready.");
            await refresh();
            window.history.replaceState({}, "", "/");
          }
        }
      } catch (caught) {
        if (active) {
          setNotice("");
          setError(caught instanceof Error ? caught.message : "Could not load payments.");
        }
      } finally {
        if (active) setBusy(false);
      }
    }
    void load();
    return () => { active = false; };
  }, [api, canAccess, refresh]);

  useEffect(() => { messageEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim() || busy || !canAccess) return;
    const text = prompt.trim();
    setMessages((items) => [...items, { role: "user", text }]);
    setPrompt("");
    setBusy(true);
    setError("");
    setNotice("");
    setSelected(null);
    setRiskAccepted(false);
    try {
      const payment = await api<Payment>("/api/plans", { method: "POST", body: JSON.stringify({ prompt: text }) });
      setSelected(payment);
      setMessages((items) => [...items, {
        role: "assistant",
        text: `I've prepared a ${money(payment.amount, payment.currency)} checkout for ${payment.description}. `
          + "Review the details on the right. Nothing is charged until the buyer approves on PayPal.",
      }]);
      await refresh();
    } catch (caught) {
      const text = caught instanceof Error ? caught.message : "Could not prepare payment.";
      setError(text);
      setMessages((items) => [...items, { role: "assistant", text }]);
    } finally { setBusy(false); }
  }

  async function checkout() {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      const payment = await api<Payment>(`/api/payments/${selected.id}/checkout`, {
        method: "POST", body: JSON.stringify({ approved: true, acknowledge_risk: riskAccepted }),
      });
      setSelected(payment);
      await refresh();
      if (payment.status !== "COMPLETED" && payment.approval_url) {
        const url = new URL(payment.approval_url);
        if (url.protocol !== "https:" || url.hostname !== "www.sandbox.paypal.com") {
          throw new Error("Untrusted checkout URL blocked.");
        }
        window.location.assign(url.href);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Checkout could not start.");
    } finally { setBusy(false); }
  }

  async function reconcile(payment: Payment) {
    setBusy(true);
    setError("");
    try {
      const result = await api<Payment>(`/api/payments/${payment.id}/capture`, { method: "POST" });
      setSelected(result);
      setNotice("Sandbox capture verified. Your receipt is ready.");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Payment is not verified yet.");
    } finally { setBusy(false); }
  }

  async function downloadReceipt(payment: Payment) {
    setBusy(true);
    setError("");
    try {
      const receipt = await api<Record<string, string>>(`/api/payments/${payment.id}/receipt`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(receipt, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `paypilot-receipt-${payment.id}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Receipt unavailable.");
    } finally { setBusy(false); }
  }

  async function authenticate(signUp = false) {
    if (!supabase) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const { error } = signUp
        ? await supabase.auth.signUp({ email, password })
        : await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
      if (signUp) setNotice("Account created. If confirmation is enabled, check your email before signing in.");
      setPassword("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Authentication failed.");
    } finally { setBusy(false); }
  }

  function choose(payment: Payment) {
    setSelected(payment);
    setRiskAccepted(false);
    setTab("assistant");
  }

  const awaiting = payments.filter((payment) => ["CREATING", "CREATED", "CAPTURE_PENDING"].includes(payment.status));
  const highRisk = !!selected && Number(selected.amount) >= 250;
  const needsLogin = health?.auth_mode === "supabase" && !token;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link href="/" className="brand"><span className="brand-mark"><Send size={20} /></span>PayPilot<span className="brand-dot">.</span></Link>
        <div className="workspace-label">YOUR WORKSPACE <span>01</span></div>
        <nav aria-label="Main navigation">
          <button className={tab === "assistant" ? "nav-item active" : "nav-item"} onClick={() => setTab("assistant")}><LayoutDashboard size={18} /> Overview <ChevronRight size={15} /></button>
          <button className={tab === "history" ? "nav-item active" : "nav-item"} onClick={() => setTab("history")}><History size={18} /> Payment history <span className="nav-count">{payments.length}</span></button>
        </nav>
        <div className="sidebar-note"><div className="note-icon"><ShieldCheck size={20} /></div><strong>Built for peace of mind.</strong><p>You review. The buyer approves. PayPilot verifies.</p><span><span className="status-dot" /> Sandbox only</span></div>
        <div className="sidebar-bottom"><div className="avatar">P</div><div><strong>{health?.auth_mode === "supabase" ? "Secure workspace" : "Local workspace"}</strong><small>PayPilot / Hackathon edition</small></div>{token && <button aria-label="Sign out" className="icon-button" onClick={async () => { await supabase?.auth.signOut(); setPayments([]); setSelected(null); setMessages([]); setSummary({ completed_count: 0, totals: {}, label: "" }); }}><LogOut size={16} /></button>}</div>
      </aside>

      <main>
        <header className="topbar"><div className="breadcrumb">Workspace <ChevronRight size={13} /><strong>{tab === "assistant" ? "Overview" : "Payment history"}</strong></div><div className="topbar-right"><span className="sandbox-pill"><span className="status-dot" />Sandbox environment</span><span className="topbar-help" title="Only synthetic test payments. No real money moves."><CircleHelp size={18} /></span><div className="avatar small">P</div></div></header>
        <div className="page-content">
          <div className="page-heading"><div><div className="eyebrow">LESS ADMIN. MORE MOMENTUM.</div><h1>Your payments, on autopilot<span>.</span></h1><p>From a simple instruction to a verified checkout. You&apos;re always in control.</p></div><span className="version-badge"><Sparkles size={13} /> PAYPILOT / V0.1</span></div>

          {error && <div role="alert" className="banner error"><TriangleAlert size={18} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError("")}><X size={16} /></button></div>}
          {notice && <div role="status" className="banner notice"><ShieldCheck size={18} /><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice("")}><X size={16} /></button></div>}

          {needsLogin ? <section className="auth-card"><LockKeyhole size={28} /><h2>Your payment workspace awaits.</h2><p>Sign in with Supabase to keep your payment history private.</p>{!supabase ? <p className="warning-text">Configure the frontend Supabase URL and anon key to enable sign-in.</p> : <form onSubmit={(event) => { event.preventDefault(); void authenticate(); }}><label>Email<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label><label>Password<input type="password" autoComplete="current-password" minLength={8} required value={password} onChange={(event) => setPassword(event.target.value)} /></label><button className="primary-button" disabled={busy}>Sign in <ArrowRight size={16} /></button><button type="button" className="text-button" disabled={busy || !email || password.length < 8} onClick={() => void authenticate(true)}>Create a free account</button></form>}</section> : <>
            <section className="stats-grid" aria-label="Payment summary">
              <div className="stat-card"><div className="stat-title">Verified collections <span className="stat-icon mint"><ArrowDownLeft size={17} /></span></div><div className="stat-value">{Object.keys(summary.totals).length ? Object.entries(summary.totals).map(([currency, amount]) => <span className="currency-total" key={currency}>{money(amount, currency)}</span>) : "$0.00"}</div><div className="stat-footer"><span className="tiny-dot" /> Completed Sandbox payments only</div></div>
              <div className="stat-card"><div className="stat-title">Payments completed <span className="stat-icon blue"><CheckCheck size={17} /></span></div><div className="stat-value">{summary.completed_count.toString().padStart(2, "0")}<span className="stat-unit">verified</span></div><div className="stat-footer">Every capture checked against PayPal</div></div>
              <div className="stat-card"><div className="stat-title">Awaiting completion <span className="stat-icon amber"><Clock3 size={17} /></span></div><div className="stat-value">{awaiting.length.toString().padStart(2, "0")}<span className="stat-unit">in progress</span></div><div className="stat-footer">Buyer approval is always required</div></div>
            </section>

            {tab === "assistant" && <div className="work-grid">
              <section className="assistant-panel">
                <div className="panel-heading"><div className="panel-title"><span className="assistant-icon"><Sparkles size={17} /></span><div><h2>Payment copilot</h2><span>{health?.parser === "gemini" ? "Powered by Gemini" : "Rule-based demo parser"}</span></div></div><span className="online-label"><span className="status-dot" />{health ? "Ready to help" : "Connecting"}</span></div>
                <div className="conversation">
                  {messages.length === 0 ? <div className="welcome"><div className="welcome-symbol"><Sparkles size={28} /></div><span className="welcome-label">YOUR NEXT PAYMENT STARTS HERE</span><h2>What can I take off<br />your plate today?</h2><p>Tell me what you need to collect. I&apos;ll prepare<br className="desktop-break" /> the checkout and double-check the details.</p><div className="suggestions">{suggestions.map(({ title, prompt, icon: Icon }) => <button key={title} disabled={!canAccess || busy} onClick={() => setPrompt(prompt)}><Icon size={16} /><span>{title}</span><ArrowRight size={14} /></button>)}</div></div> : <div className="message-list">{messages.map((message, index) => <div key={index} className={`message ${message.role}`}><span className="message-avatar">{message.role === "assistant" ? <Sparkles size={14} /> : "You"}</span><div><strong>{message.role === "assistant" ? "PayPilot" : "You"}</strong><p>{message.text}</p></div></div>)}{busy && <div className="thinking"><LoaderCircle size={15} className="spin" /> Working through the details...</div>}<div ref={messageEnd} /></div>}
                </div>
                <div className="composer-wrap"><form className="composer" onSubmit={submit}><label className="sr-only" htmlFor="prompt">Payment instruction</label><textarea id="prompt" placeholder="Collect USD 25.00 for design consultation..." maxLength={1000} rows={2} value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} /><div className="composer-bottom"><span><LockKeyhole size={12} /> Synthetic data only. No real money.</span><button type="submit" className="send-button" aria-label="Prepare payment" disabled={!canAccess || busy || prompt.trim().length < 8}>{busy ? <LoaderCircle size={17} className="spin" /> : <ArrowRight size={19} />}</button></div></form><p className="composer-disclaimer">AI assists with the plan. Your approval stays non-negotiable.</p></div>
              </section>

              <aside className="review-panel">
                <div className="review-title"><h2>Payment workspace</h2><span className="step-count">01 / 03</span></div>
                {selected ? <div className="review-body"><div className="review-state"><span className={selected.status === "COMPLETED" ? "review-state-icon complete" : "review-state-icon"}>{selected.status === "COMPLETED" ? <CheckCheck size={24} /> : <CreditCard size={24} />}</span><span className="eyebrow">{selected.status === "COMPLETED" ? "CAPTURE VERIFIED" : "CHECKOUT PLAN"}</span><h3>{money(selected.amount, selected.currency)}</h3><p>{selected.description}</p></div><dl className="payment-details"><div><dt>Payment method</dt><dd>PayPal Sandbox</dd></div><div><dt>Currency</dt><dd>{selected.currency}</dd></div><div><dt>Status</dt><dd className="status-value">{selected.status.replaceAll("_", " ")}</dd></div>{selected.capture_id && <div><dt>Capture ID</dt><dd className="mono">{selected.capture_id}</dd></div>}</dl>{selected.risks.length > 0 && <div className="risk-box"><TriangleAlert size={16} /><div>{selected.risks.map((risk) => <p key={risk}>{risk}</p>)}</div></div>}{highRisk && selected.status === "DRAFT" && <label className="risk-checkbox"><input type="checkbox" checked={riskAccepted} onChange={(event) => setRiskAccepted(event.target.checked)} />I have reviewed this high-value payment.</label>}{selected.status === "COMPLETED" ? <button className="primary-button" disabled={busy} onClick={() => void downloadReceipt(selected)}><Download size={16} /> Download receipt</button> : <><button className="primary-button" onClick={() => void checkout()} disabled={busy || !health?.paypal_configured || selected.duplicate || (selected.status === "DRAFT" && highRisk && !riskAccepted)}>{busy ? <LoaderCircle size={16} className="spin" /> : <>{selected.status === "DRAFT" || selected.status === "CREATING" ? "Approve plan & open PayPal" : "Resume PayPal checkout"}<ArrowRight size={16} /></>}</button>{selected.order_id && <button className="text-button" disabled={busy} onClick={() => void reconcile(selected)}>Already approved? Verify payment</button>}<p className="review-footnote">The buyer must approve on PayPal before capture.</p></>}</div> : <div className="empty-review"><div className="empty-visual"><div /><CreditCard size={27} /><span><Check size={11} /></span></div><h3>A little clarity before checkout.</h3><p>Your payment details, safety checks, and approval steps will appear here.</p><span className="waiting-label"><span className="tiny-dot" /> Waiting for your first instruction</span></div>}
                <div className="workflow"><span className="eyebrow">HOW PAYPILOT WORKS</span><div><span className="workflow-number">1</span><p><strong>You describe it</strong><small>A simple instruction, in your words.</small></p><Check size={13} /></div><div><span className="workflow-number">2</span><p><strong>Review & approve</strong><small>We check details. You stay in control.</small></p></div><div><span className="workflow-number">3</span><p><strong>PayPal confirms it</strong><small>Verified capture. Receipt ready.</small></p></div></div>
                {!health?.paypal_configured && <div className="config-note"><LockKeyhole size={15} /><p><strong>Checkout is not connected yet.</strong> Add Sandbox credentials in the backend .env to enable payments.</p></div>}
              </aside>
            </div>}

            <section className="history-panel"><div className="history-heading"><div><h2>{tab === "history" ? "Payment history" : "Recent activity"}</h2><p>Your checkout trail, with nothing left to guess.</p></div><button className="text-button" disabled={!canAccess || busy} onClick={async () => { setError(""); try { await refresh(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Refresh failed."); } }}>Refresh <History size={14} /></button></div>{payments.length === 0 ? <div className="history-empty"><span><MessageSquare size={20} /></span><div><strong>A fresh start. A clear ledger.</strong><p>Create your first payment plan and it will appear here.</p></div><Plus size={18} /></div> : <div className="table-scroll"><table><thead><tr><th>Payment</th><th>Created</th><th>Amount</th><th>Status</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>{payments.slice(0, tab === "history" ? 100 : 5).map((payment) => <tr key={payment.id}><td><div className="table-payment"><span><ArrowDownLeft size={16} /></span><div><strong>{payment.description}</strong><small>{payment.id.slice(0, 8)} / PayPal Sandbox</small></div></div></td><td>{new Date(payment.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</td><td className="table-amount">{money(payment.amount, payment.currency)}</td><td><span className={`status-badge ${payment.status === "COMPLETED" ? "completed" : ""}`}>{payment.status === "COMPLETED" ? <Check size={11} /> : <Clock3 size={11} />}{payment.status.replaceAll("_", " ").toLowerCase()}</span></td><td><button className="icon-button" aria-label={`Review ${payment.description}`} onClick={() => choose(payment)}><ArrowRight size={17} /></button></td></tr>)}</tbody></table></div>}</section>
          </>}
          <footer className="page-footer"><span><ShieldCheck size={13} /> Approval-first. Sandbox-only. Always verifiable.</span><span>Made for moving forward <ArrowRight size={12} className="inline footer-arrow" /></span></footer>
        </div>
      </main>
    </div>
  );
}
