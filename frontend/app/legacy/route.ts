import { readFile } from "node:fs/promises";
import path from "node:path";
export async function GET() {
  const html = await readFile(path.join(process.cwd(), "index.html"), "utf8");
  return new Response(html.replace("<head>", '<head><base href="/legacy/assets/">'), { headers: { "Content-Type": "text/html; charset=utf-8" } });
}
