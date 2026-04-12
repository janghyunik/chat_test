"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { apiFetch, clearAuthToken } from "@/lib/api";

type ChatSessionSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

type Props = {
  onCreateChat?: () => Promise<void> | void;
};

export function AppSidebar({ onCreateChat }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeChatId = searchParams.get("chat");
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  async function loadSessions() {
    try {
      const data = await apiFetch<ChatSessionSummary[]>("/api/chat/sessions");
      setSessions(data);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSessions();
  }, [pathname]);

  async function handleLogout() {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {
      // 로그아웃 API 실패여도 프런트 토큰은 지웁니다.
    }
    clearAuthToken();
    router.replace("/login");
  }

  return (
    <aside className="app-sidebar">
      <div className="sidebar-top">
        <button
          className="primary-button"
          type="button"
          onClick={async () => {
            if (onCreateChat) {
              await onCreateChat();
              await loadSessions();
            } else {
              router.push("/");
            }
          }}
        >
          + 새 채팅
        </button>

        <nav className="sidebar-nav">
          <Link className={pathname === "/" ? "nav-link active" : "nav-link"} href="/">
            채팅
          </Link>
          <Link
            className={pathname === "/inform" ? "nav-link active" : "nav-link"}
            href="/inform"
          >
            인폼노트 DB
          </Link>
        </nav>
      </div>

      <div className="history-block">
        <p className="sidebar-label">이전 채팅</p>
        {loading ? <p className="muted-text">불러오는 중...</p> : null}
        {!loading && sessions.length === 0 ? <p className="muted-text">저장된 대화가 없습니다.</p> : null}

        <div className="history-list">
          {sessions.map((session) => (
            <Link
              key={session.id}
              className={activeChatId === session.id ? "history-item active" : "history-item"}
              href={`/?chat=${session.id}`}
            >
              <span className="history-title">{session.title}</span>
              <span className="history-time">
                {new Date(session.updated_at).toLocaleString("ko-KR", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </Link>
          ))}
        </div>
      </div>

      <div className="sidebar-bottom">
        <button className="ghost-button" type="button" onClick={handleLogout}>
          로그아웃
        </button>
      </div>
    </aside>
  );
}
