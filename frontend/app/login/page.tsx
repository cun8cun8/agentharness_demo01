"use client";
import { useEffect, useState, type FormEvent } from "react";
function returnPath() {
  const candidate = new URLSearchParams(window.location.search).get("returnTo") || "/tasks";
  if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.includes("\\")) return "/tasks";
  const target = new URL(candidate, window.location.origin);
  return target.origin === window.location.origin && target.pathname !== "/login" ? target.pathname + target.search : "/tasks";
}
export default function LoginPage() {
  const [key, setKey] = useState(""); const [error, setError] = useState("");
  const [busy, setBusy] = useState(false); const [checking, setChecking] = useState(true);
  useEffect(() => {
    const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), 15000);
    void fetch("/api/v1/auth/session", { credentials: "include", cache: "no-store", signal: controller.signal })
      .then((response) => { if (response.ok) window.location.replace(returnPath()); })
      .catch(() => setError("无法连接登录服务，请稍后重试。"))
      .finally(() => { window.clearTimeout(timeout); setChecking(false); });
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("/api/v1/auth/session", { credentials: "include", cache: "no-store", signal: controller.signal,
        headers: { "X-API-Key": key.trim() } });
      if (!response.ok) { setError(response.status === 401 ? "密钥无效，请检查后重试。" : "登录服务暂不可用，请稍后重试。"); return; }
      setKey(""); window.location.replace(returnPath());
    } catch { setError("登录请求未完成，请检查连接后重试。"); }
    finally { window.clearTimeout(timeout); setBusy(false); }
  }
  return <main className="auth-page"><section className="auth-card" aria-labelledby="login-title">
    <div className="auth-brand"><span className="auth-logo">RF</span><span>ResearchForge<small>智能体工作台</small></span></div>
    <h1 id="login-title">开发者登录</h1><p>登录后继续访问工作台。此入口用于开发者与运维账号。</p>
    <form onSubmit={(event) => void submit(event)}><label htmlFor="developer-key">开发者 API 密钥</label>
      <input id="developer-key" name="key" type="password" value={key} onChange={(event) => setKey(event.target.value)}
        autoComplete="off" required autoFocus disabled={checking || busy} aria-describedby="login-error" />
      {error && <p id="login-error" role="alert" className="auth-error">{error}</p>}
      <button type="submit" disabled={checking || busy || !key.trim()}>{checking ? "检查会话…" : busy ? "登录中…" : "登录"}</button>
    </form><small className="auth-note">密钥仅用于本次登录，不会保存到浏览器存储。</small>
  </section></main>;
}
