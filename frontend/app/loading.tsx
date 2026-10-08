export default function Loading() {
  return <main className="route-loading" aria-busy="true" aria-label="正在加载">
    <section className="route-loading-panel">
      <span className="skeleton-bar route-loading-eyebrow" />
      <span className="skeleton-bar route-loading-title" />
      <span className="skeleton-bar route-loading-line" />
      <span className="skeleton-bar route-loading-line" />
      <span className="skeleton-bar route-loading-line" />
    </section>
  </main>;
}
