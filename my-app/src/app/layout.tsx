import type { Metadata } from "next";
import { MonitoringClient } from "@/components/monitoring-client";
import "./globals.css";

export const metadata: Metadata = {
  title: "chat_test",
  description: "Next.js + FastAPI 기반 챗봇 전환 프로젝트",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>
        <MonitoringClient />
        {children}
      </body>
    </html>
  );
}
