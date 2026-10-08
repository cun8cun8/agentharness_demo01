import { notFound } from "next/navigation";
import WorkbenchRoute from "../workbench-route";

export default async function Page({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!["tasks", "repositories", "runs", "approvals", "strategies", "models", "evaluations", "research", "health", "memory", "datasets", "training", "registry", "extensions", "system", "audit"].includes(section)) notFound();
  return <WorkbenchRoute key={section} section={section} />;
}
