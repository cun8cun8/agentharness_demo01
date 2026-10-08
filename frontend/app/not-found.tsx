import Link from "next/link";

export default function NotFound() {
  return <main className="error-page">
    <section className="error-panel" role="status">
      <p className="eyebrow">RESEARCHFORGE / 404</p>
      <h1>页面不存在</h1>
      <p>当前工作台地址无效，请返回代码任务。</p>
      <Link className="primary error-link" href="/tasks">返回代码任务</Link>
    </section>
  </main>;
}
