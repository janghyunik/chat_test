"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

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

const CHAT_SESSION_EVENT = "chat-sessions-changed";

export function AppSidebar({ onCreateChat }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeChatId = searchParams.get("chat");
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const chatHref = useMemo(() => {
    if (activeChatId) {
      return `/?chat=${activeChatId}`;
    }
    return "/";
  }, [activeChatId]);

  const informHref = useMemo(() => {
    if (activeChatId) {
      return `/inform?chat=${activeChatId}`;
    }
    return "/inform";
  }, [activeChatId]);

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
    let cancelled = false;

    async function run() {
      if (!cancelled) {
        await loadSessions();
      }
    }

    run();

    function handleChanged() {
      void run();
    }

    window.addEventListener(CHAT_SESSION_EVENT, handleChanged);
    return () => {
      cancelled = true;
      window.removeEventListener(CHAT_SESSION_EVENT, handleChanged);
    };
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

  async function handleDeleteChat(sessionId: string) {
    const approved = window.confirm("이 채팅을 삭제하시겠습니까?");
    if (!approved) return;

    setDeletingId(sessionId);
    try {
      await apiFetch(`/api/chat/sessions/${sessionId}`, { method: "DELETE" });
      window.dispatchEvent(new Event(CHAT_SESSION_EVENT));
      if (activeChatId === sessionId && pathname === "/") {
        router.replace("/");
      }
      if (activeChatId === sessionId && pathname === "/inform") {
        router.replace("/inform");
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "채팅 삭제에 실패했습니다.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <aside className="app-sidebar">
      <div className="sidebar-brand">chat_test</div>

      <div className="sidebar-top">
        <button
          className="new-chat-button"
          type="button"
          onClick={async () => {
            if (onCreateChat) {
              await onCreateChat();
              await loadSessions();
              window.dispatchEvent(new Event(CHAT_SESSION_EVENT));
            } else {
              router.push("/");
            }
          }}
        >
          + 새 채팅
        </button>

        <nav className="sidebar-nav">
          <Link className={pathname === "/" ? "nav-link active" : "nav-link"} href={chatHref}>
            채팅
          </Link>
          <Link className={pathname === "/inform" ? "nav-link active" : "nav-link"} href={informHref}>
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
            <div
              key={session.id}
              className={activeChatId === session.id ? "history-item active" : "history-item"}
            >
              <Link className="history-link" href={`/?chat=${session.id}`}>
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
              <button
                aria-label="채팅 삭제"
                className="history-delete"
                disabled={deletingId === session.id}
                type="button"
                onClick={() => handleDeleteChat(session.id)}
              >
                {deletingId === session.id ? "..." : "×"}
              </button>
            </div>
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
