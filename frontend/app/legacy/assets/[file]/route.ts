import { readFile } from "node:fs/promises";
import path from "node:path";
export async function GET(_: Request, { params }: { params: Promise<{ file: string }> }) {
  const { file } = await params;
  if (!["app.js", "operations.js", "styles.css"].includes(file)) return new Response("Not found", { status: 404 });
  let content = file === "app.js" ? await readFile(path.join(process.cwd(), "app.js"), "utf8")
    : file === "operations.js" ? await readFile(path.join(process.cwd(), "operations.js"), "utf8")
    : await readFile(path.join(process.cwd(), "styles.css"), "utf8");
  if (file.endsWith(".js")) content = content.replaceAll("http://127.0.0.1:8001", "").replaceAll("http://localhost:8001", "");
  return new Response(content, { headers: { "Content-Type": file.endsWith(".css") ? "text/css" : "application/javascript" } });
}
