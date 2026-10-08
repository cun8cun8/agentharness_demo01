"use client";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <main className="error-page">
    <section className="error-panel" role="alert">
      <p className="eyebrow">RESEARCHFORGE</p>
      <h1>页面暂时无法加载</h1>
      <p>工作台遇到了一次临时错误，可以重新尝试打开当前页面。</p>
      <button type="button" className="primary" onClick={() => reset()}>重试</button>
    </section>
  </main>;
}
