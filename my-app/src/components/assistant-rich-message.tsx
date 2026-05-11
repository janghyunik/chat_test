"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

type MessageBlock =
  | { type: "heading"; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "kv"; rows: Array<{ key: string; value: string }> }
  | { type: "table"; headers: string[]; rows: string[][] };

const orderedListPattern = /^\s*(\d+)[.)]\s+(.*)$/;
const bulletListPattern = /^\s*(?:[-*•]|[✅✔︎☑️])\s+(.*)$/;
const markdownHeadingPattern = /^#{1,3}\s+(.*)$/;
const colonHeadingPattern = /^([^:\n]{1,24})\s*:\s*$/;
const keyValuePattern = /^([^:\n]{1,18})\s*:\s+(.+)$/;

function renderInline(text: string): ReactNode[] {
  const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return tokens.map((token, index) => {
    if (token.startsWith("**") && token.endsWith("**")) {
      return <strong key={index}>{token.slice(2, -2)}</strong>;
    }
    if (token.startsWith("`") && token.endsWith("`")) {
      return <code key={index}>{token.slice(1, -1)}</code>;
    }
    return token;
  });
}

function looksLikeMarkdownTable(lines: string[], index: number): boolean {
  if (index + 1 >= lines.length) return false;
  const header = lines[index].trim();
  const separator = lines[index + 1].trim();
  return header.includes("|") && /^\|?\s*[:-]+(?:\s*\|\s*[:-]+)+\s*\|?$/.test(separator);
}

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function parseBlocks(content: string): MessageBlock[] {
  const normalized = content.replace(/\r\n/g, "\n").trim();
  if (!normalized) return [];

  const lines = normalized.split("\n");
  const blocks: MessageBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const current = lines[index].trim();

    if (!current) {
      index += 1;
      continue;
    }

    const markdownHeading = current.match(markdownHeadingPattern);
    if (markdownHeading) {
      blocks.push({ type: "heading", text: markdownHeading[1].trim() });
      index += 1;
      continue;
    }

    const colonHeading = current.match(colonHeadingPattern);
    if (colonHeading) {
      blocks.push({ type: "heading", text: colonHeading[1].trim() });
      index += 1;
      continue;
    }

    if (looksLikeMarkdownTable(lines, index)) {
      const headers = splitTableRow(lines[index]);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].trim().includes("|")) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push({ type: "table", headers, rows });
      continue;
    }

    const listMatch = current.match(orderedListPattern) ?? current.match(bulletListPattern);
    if (listMatch) {
      const ordered = orderedListPattern.test(current);
      const items: string[] = [];
      while (index < lines.length) {
        const line = lines[index].trim();
        const orderedItem = line.match(orderedListPattern);
        const bulletItem = line.match(bulletListPattern);
        if (ordered && orderedItem) {
          items.push(orderedItem[2].trim());
          index += 1;
          continue;
        }
        if (!ordered && bulletItem) {
          items.push(bulletItem[1].trim());
          index += 1;
          continue;
        }
        break;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    const keyValue = current.match(keyValuePattern);
    if (keyValue) {
      const rows: Array<{ key: string; value: string }> = [];
      while (index < lines.length) {
        const line = lines[index].trim();
        const match = line.match(keyValuePattern);
        if (!match) break;
        rows.push({ key: match[1].trim(), value: match[2].trim() });
        index += 1;
      }
      if (rows.length >= 2) {
        blocks.push({ type: "kv", rows });
        continue;
      }
      if (rows.length === 1) {
        blocks.push({ type: "paragraph", text: `${rows[0].key}: ${rows[0].value}` });
        continue;
      }
    }

    const paragraphLines: string[] = [];
    while (index < lines.length) {
      const line = lines[index].trim();
      if (!line) break;
      if (markdownHeadingPattern.test(line) || colonHeadingPattern.test(line)) break;
      if (looksLikeMarkdownTable(lines, index)) break;
      if (orderedListPattern.test(line) || bulletListPattern.test(line)) break;
      if (keyValuePattern.test(line) && paragraphLines.length > 0) break;
      paragraphLines.push(line);
      index += 1;
    }

    if (paragraphLines.length > 0) {
      blocks.push({ type: "paragraph", text: paragraphLines.join(" ") });
      continue;
    }

    index += 1;
  }

  return blocks;
}

function normalizeHeader(text: string) {
  return text.replace(/\s+/g, "").trim();
}

function findHeaderIndex(headers: string[], ...candidates: string[]) {
  const normalized = headers.map(normalizeHeader);
  return normalized.findIndex((header) => candidates.some((candidate) => header === normalizeHeader(candidate)));
}

function isReferenceTable(headers: string[]) {
  return (
    findHeaderIndex(headers, "날짜") >= 0 &&
    findHeaderIndex(headers, "설비명") >= 0 &&
    findHeaderIndex(headers, "에러명") >= 0
  );
}

function buildInformHref(headers: string[], row: string[], activeChatId?: string) {
  const dateIndex = findHeaderIndex(headers, "날짜");
  const lineIndex = findHeaderIndex(headers, "라인");
  const equipIndex = findHeaderIndex(headers, "설비명");
  const errorIndex = findHeaderIndex(headers, "에러명");

  const params = new URLSearchParams();
  const date = dateIndex >= 0 ? (row[dateIndex] ?? "").trim() : "";
  const line = lineIndex >= 0 ? (row[lineIndex] ?? "").trim() : "";
  const equip = equipIndex >= 0 ? (row[equipIndex] ?? "").trim() : "";
  const error = errorIndex >= 0 ? (row[errorIndex] ?? "").trim() : "";

  if (activeChatId) params.set("chat", activeChatId);
  if (line && line !== "-") params.set("line", line);
  if (equip && equip !== "-") params.set("equip", equip);
  if (error && error !== "-") params.set("keyword", error);
  if (/^\d{4}-\d{2}-\d{2}/.test(date)) {
    const day = date.slice(0, 10);
    params.set("start", day);
    params.set("end", day);
  }

  const query = params.toString();
  return query ? `/inform?${query}` : "/inform";
}

function TableActionBar({
  headers,
  rows,
  onOpenModal,
  activeChatId,
}: {
  headers: string[];
  rows: string[][];
  onOpenModal: () => void;
  activeChatId?: string;
}) {
  const firstLink = useMemo(() => {
    if (!isReferenceTable(headers) || rows.length === 0) return null;
    return buildInformHref(headers, rows[0], activeChatId);
  }, [activeChatId, headers, rows]);

  return (
    <div className="assistant-table-actions">
      <button className="assistant-table-action-button" type="button" onClick={onOpenModal}>
        전체 표 보기
      </button>
      {firstLink ? (
        <Link className="assistant-table-action-link" href={firstLink}>
          인폼노트 DB에서 보기
        </Link>
      ) : null}
    </div>
  );
}

function TableModal({
  headers,
  rows,
  activeChatId,
  onClose,
}: {
  headers: string[];
  rows: string[][];
  activeChatId?: string;
  onClose: () => void;
}) {
  const reference = isReferenceTable(headers);
  return (
    <div className="assistant-modal-backdrop" role="dialog" aria-modal="true">
      <div className="assistant-modal-card">
        <div className="assistant-modal-header">
          <div>
            <p className="assistant-modal-label">REFERENCE TABLE</p>
            <h3>참조 이력 전체 보기</h3>
          </div>
          <button className="assistant-modal-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="assistant-modal-table-wrap">
          <table className="assistant-table full">
            <thead>
              <tr>
                {headers.map((header, headerIndex) => (
                  <th key={`modal-header-${headerIndex}`}>{header}</th>
                ))}
                {reference ? <th>이동</th> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`modal-row-${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`modal-cell-${rowIndex}-${cellIndex}`}>{renderInline(cell)}</td>
                  ))}
                  {reference ? (
                    <td>
                      <Link className="assistant-inline-link" href={buildInformHref(headers, row, activeChatId)}>
                        DB 조회
                      </Link>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function AssistantRichMessage({ content, activeChatId }: { content: string; activeChatId?: string }) {
  const blocks = parseBlocks(content);
  const [modalTable, setModalTable] = useState<{ headers: string[]; rows: string[][] } | null>(null);

  if (blocks.length === 0) {
    return <p className="assistant-paragraph">응답 내용이 없습니다.</p>;
  }

  return (
    <>
      <div className="assistant-rich-body">
        {blocks.map((block, index) => {
          if (block.type === "heading") {
            return (
              <section className="assistant-section" key={`heading-${index}`}>
                <div className="assistant-section-heading">{renderInline(block.text)}</div>
              </section>
            );
          }

          if (block.type === "paragraph") {
            return (
              <section className="assistant-section" key={`paragraph-${index}`}>
                <p className="assistant-paragraph">{renderInline(block.text)}</p>
              </section>
            );
          }

          if (block.type === "list") {
            const ListTag = block.ordered ? "ol" : "ul";
            return (
              <section className="assistant-section" key={`list-${index}`}>
                <ListTag className={block.ordered ? "assistant-list ordered" : "assistant-list unordered"}>
                  {block.items.map((item, itemIndex) => (
                    <li key={`item-${itemIndex}`}>{renderInline(item)}</li>
                  ))}
                </ListTag>
              </section>
            );
          }

          if (block.type === "kv") {
            return (
              <section className="assistant-section" key={`kv-${index}`}>
                <div className="assistant-kv-grid">
                  {block.rows.map((row, rowIndex) => (
                    <div className="assistant-kv-card" key={`row-${rowIndex}`}>
                      <div className="assistant-kv-key">{row.key}</div>
                      <div className="assistant-kv-value">{renderInline(row.value)}</div>
                    </div>
                  ))}
                </div>
              </section>
            );
          }

          const reference = isReferenceTable(block.headers);

          return (
            <section className="assistant-section" key={`table-${index}`}>
              <TableActionBar
                headers={block.headers}
                rows={block.rows}
                onOpenModal={() => setModalTable({ headers: block.headers, rows: block.rows })}
                activeChatId={activeChatId}
              />
              <div className="assistant-table-wrap">
                <table className="assistant-table">
                  <thead>
                    <tr>
                      {block.headers.map((header, headerIndex) => (
                        <th key={`header-${headerIndex}`}>{header}</th>
                      ))}
                      {reference ? <th>조회</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={`body-${rowIndex}`}>
                        {row.map((cell, cellIndex) => (
                          <td key={`cell-${rowIndex}-${cellIndex}`}>{renderInline(cell)}</td>
                        ))}
                        {reference ? (
                          <td>
                            <Link className="assistant-inline-link" href={buildInformHref(block.headers, row, activeChatId)}>
                              DB 조회
                            </Link>
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          );
        })}
      </div>

      {modalTable ? (
        <TableModal
          headers={modalTable.headers}
          rows={modalTable.rows}
          activeChatId={activeChatId}
          onClose={() => setModalTable(null)}
        />
      ) : null}
    </>
  );
}
