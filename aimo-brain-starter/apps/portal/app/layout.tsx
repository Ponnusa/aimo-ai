import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AIMO BRAIN Portal",
  description: "AIMO BRAIN management portal (M0 placeholder shell).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-slate-50 text-slate-900">{children}</body>
    </html>
  );
}
