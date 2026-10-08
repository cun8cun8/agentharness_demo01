import type { Metadata } from "next";
import "./globals.css";
import "./run-polish.css";

export const metadata: Metadata = { title: "ResearchForge · 智能体工作台", description: "ResearchForge Agent Harness" };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
