import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NexusRelay",
  description: "Durable work with explicit human approvals.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

