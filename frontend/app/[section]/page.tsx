import { notFound } from "next/navigation";
import Workbench from "../workbench";

export default async function Page({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!["tasks", "repositories", "runs", "approvals", "strategies", "models", "evaluations", "research", "health", "memory", "datasets", "training", "registry", "extensions", "system"].includes(section)) notFound();
  return <Workbench key={section} section={section} />;
}
