"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { apiFetch } from "@/lib/api";
import { AppSidebar } from "@/components/app-sidebar";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

type ChatSession = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  process: string;
  messages: ChatMessage[];
};

export function ChatScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const chatId = searchParams.get("chat");

  const [session, setSession] = useState<ChatSession | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isEmpty = useMemo(() => !session || session.messages.length === 0, [session]);

  async function createChatAndMove() {
    const created = await apiFetch<ChatSession>("/api/chat/sessions", {
      method: "POST",
      body: JSON.stringify({ process: "MP" }),
    });
    router.replace(`/?chat=${created.id}`);
  }

  async function loadSession(targetId: string) {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ChatSession>(`/api/chat/sessions/${targetId}`);
      setSession(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "채팅을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!chatId) {
      createChatAndMove();
      return;
    }
    loadSession(chatId);
  }, [chatId]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!chatId || !input.trim() || sending) return;

    const content = input.trim();
    setInput("");
    setSending(true);
    setError(null);

    try {
      const data = await apiFetch<{ session: ChatSession; answer: string }>(
        `/api/chat/sessions/${chatId}/messages`,
        {
          method: "POST",
          body: JSON.stringify({ content }),
        },
      );
      setSession(data.session);
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "질문 전송에 실패했습니다.");
      setInput(content);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="app-shell">
      <AppSidebar onCreateChat={createChatAndMove} />

      <main className="main-panel chat-panel">
        {loading ? (
          <div className="empty-state">
            <p className="muted-text">채팅을 준비하는 중입니다...</p>
          </div>
        ) : null}

        {!loading && error ? (
          <div className="empty-state">
            <p className="error-text">{error}</p>
          </div>
        ) : null}

        {!loading && !error && session ? (
          <>
            <div className="chat-scroll-area">
              {isEmpty ? (
                <section className="welcome-card">
                  <p className="welcome-label">CHAT TEST</p>
                  <h1>무엇을 도와드릴까요?</h1>
                  <p className="muted-text">
                    Django 기반 화면은 제거하고, ChatGPT 스타일의 메인 채팅 화면만 남긴 1차 전환본입니다.
                  </p>
                </section>
              ) : null}

              {session.messages.map((message) => (
                <article
                  key={message.id}
                  className={message.role === "user" ? "message-row user" : "message-row assistant"}
                >
                  <div className="message-bubble">
                    <div className="message-role">{message.role === "user" ? "나" : "Assistant"}</div>
                    <p>{message.content}</p>
                  </div>
                </article>
              ))}
            </div>

            <form className="composer" onSubmit={handleSubmit}>
              <textarea
                className="composer-input"
                rows={4}
                placeholder="반도체 설비 에러, 점검 이력, 일반 질문 등을 입력하세요. 번호 선택 단계에서는 숫자만 입력해도 됩니다."
                value={input}
                onChange={(event) => setInput(event.target.value)}
              />
              <div className="composer-bottom">
                <span className="muted-text small">공정 기본값: MP</span>
                <button className="primary-button" disabled={sending} type="submit">
                  {sending ? "전송 중..." : "질문 보내기"}
                </button>
              </div>
            </form>
          </>
        ) : null}
      </main>
    </div>
  );
}
