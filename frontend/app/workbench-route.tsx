"use client";

import dynamic from "next/dynamic";

const Workbench = dynamic(() => import("./workbench"), {
  ssr: false,
  loading: () => (
    <main className="route-loading" aria-label="正在加载工作台">
      <section className="route-loading-panel">
        <p className="eyebrow">RESEARCHFORGE</p>
        <div className="route-loading-title skeleton-bar" />
        <div className="route-loading-line skeleton-bar" />
        <div className="route-loading-line skeleton-bar" />
      </section>
    </main>
  ),
});

export default function WorkbenchRoute({ section }: { section: string }) {
  return <Workbench section={section} />;
}
