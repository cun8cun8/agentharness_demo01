import { ImageResponse } from "next/og";
export const size = { width: 64, height: 64 };
export const contentType = "image/png";
export default function Icon() {
  return new ImageResponse(<div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 64, height: 64, background: "#126c66", color: "white", fontSize: 27, fontWeight: 700 }}>RF</div>, size);
}
