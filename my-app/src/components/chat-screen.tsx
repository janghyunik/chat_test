"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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

type PendingState = {
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
} | null;

const CHAT_SESSION_EVENT = "chat-sessions-changed";

function buildPendingState(content: string): PendingState {
  const now = new Date().toISOString();
  return {
    userMessage: {
      id: `pending-user-${crypto.randomUUID()}`,
      role: "user",
      content,
      created_at: now,
    },
    assistantMessage: {
      id: `pending-assistant-${crypto.randomUUID()}`,
      role: "assistant",
      content: "",
      created_at: now,
    },
  };
}

export function ChatScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const chatId = searchParams.get("chat");
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);

  const [session, setSession] = useState<ChatSession | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingState, setPendingState] = useState<PendingState>(null);

  const isEmpty = useMemo(() => !session || session.messages.length === 0, [session]);
  const visibleMessages = useMemo(() => {
    const base = session?.messages ?? [];
    if (!pendingState) return base;
    return [...base, pendingState.userMessage, pendingState.assistantMessage];
  }, [pendingState, session?.messages]);

  async function createChatAndMove() {
    const created = await apiFetch<ChatSession>("/api/chat/sessions", {
      method: "POST",
      body: JSON.stringify({ process: "MP" }),
    });
    window.dispatchEvent(new Event(CHAT_SESSION_EVENT));
    router.replace(`/?chat=${created.id}`);
  }

  async function loadSession(targetId: string) {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ChatSession>(`/api/chat/sessions/${targetId}`);
      setSession(data);
    } catch (loadError) {
      setSession(null);
      setError(loadError instanceof Error ? loadError.message : "채팅을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!chatId) {
      setSession(null);
      setPendingState(null);
      setLoading(false);
      setError(null);
      return;
    }
    void loadSession(chatId);
  }, [chatId]);

  useEffect(() => {
    const element = scrollAreaRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, [loading, pendingState, session?.messages.length]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!chatId || !input.trim() || sending) return;

    const content = input.trim();
    setInput("");
    setSending(true);
    setError(null);
    setPendingState(buildPendingState(content));

    try {
      const data = await apiFetch<{ session: ChatSession; answer: string }>(
        `/api/chat/sessions/${chatId}/messages`,
        {
          method: "POST",
          body: JSON.stringify({ content }),
        },
      );
      setSession(data.session);
      setPendingState(null);
      window.dispatchEvent(new Event(CHAT_SESSION_EVENT));
    } catch (sendError) {
      setPendingState(null);
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
        <div className="chat-scroll-area" ref={scrollAreaRef}>
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

          {!loading && !error ? (
            <div className="chat-scroll-inner">
              {isEmpty && !pendingState ? (
                <section className="welcome-card">
                  <p className="welcome-label">CHAT TEST</p>
                  <h1>무엇을 도와드릴까요?</h1>
                  <p className="muted-text">
                    왼쪽의 새 채팅 버튼을 눌러 새로운 대화를 시작하세요. 기존 채팅은 사이드바에서
                    다시 이어서 볼 수 있습니다.
                  </p>
                </section>
              ) : null}

              {visibleMessages.map((message) => {
                const isPendingAssistant =
                  pendingState?.assistantMessage.id === message.id && message.role === "assistant";

                return (
                  <article
                    key={message.id}
                    className={message.role === "user" ? "message-row user" : "message-row assistant"}
                  >
                    <div className={isPendingAssistant ? "message-bubble thinking" : "message-bubble"}>
                      <div className="message-role">
                        {message.role === "user" ? "나" : "Assistant"}
                        {isPendingAssistant ? <span className="message-status">답변 생성 중...</span> : null}
                      </div>

                      {isPendingAssistant ? (
                        <div className="typing-indicator" aria-label="답변 생성 중">
                          <span />
                          <span />
                          <span />
                        </div>
                      ) : (
                        <p>{message.content}</p>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : null}
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <div className="composer-inner">
            <textarea
              className="composer-input"
              disabled={!chatId || sending}
              rows={4}
              placeholder={
                chatId
                  ? "반도체 설비 에러, 점검 이력, 일반 질문 등을 입력하세요. 관련 점검 이력은 자동 검색 후 바로 답변합니다."
                  : "왼쪽의 새 채팅 버튼을 눌러 대화를 시작하세요."
              }
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
            <div className="composer-bottom">
              <span className="muted-text small">
                {sending ? "질문을 전송했고 답변을 생성하고 있습니다..." : "공정 기본값: MP"}
              </span>
              <button className="primary-button" disabled={sending || !chatId} type="submit">
                {sending ? "답변 생성 중..." : "질문 보내기"}
              </button>
            </div>
          </div>
        </form>
      </main>
    </div>
  );
}
