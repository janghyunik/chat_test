"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AppSidebar } from "@/components/app-sidebar";
import { apiFetch } from "@/lib/api";

type InformRecord = {
  No: number;
  날짜: string;
  라인: string;
  공정: string;
  설비명: string;
  에러명: string;
  점검이력: string;
  중복수?: number;
  중복키?: string;
};

type InformPageInfo = {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  block_size: number;
  block_start: number;
  block_end: number;
  has_prev_block: boolean;
  has_next_block: boolean;
};

type InformResponse = {
  data: InformRecord[];
  full: InformRecord[];
  options: {
    라인?: string[];
    설비명?: string[];
  };
  page_info: InformPageInfo;
};

const DEFAULT_PAGE_INFO: InformPageInfo = {
  page: 1,
  page_size: 20,
  total_items: 0,
  total_pages: 0,
  block_size: 9,
  block_start: 1,
  block_end: 1,
  has_prev_block: false,
  has_next_block: false,
};

function toDateValue(value: string) {
  const raw = String(value ?? "").trim();
  return raw.length >= 10 ? raw.slice(0, 10) : raw;
}

export function InformScreen() {
  const searchParams = useSearchParams();
  const initialProcess = searchParams.get("process") || "MP";
  const initialLine = searchParams.get("line") || "";
  const initialEquip = searchParams.get("equip") || "";
  const initialKeyword = searchParams.get("keyword") || "";
  const initialStartDate = searchParams.get("start") || "";
  const initialEndDate = searchParams.get("end") || "";

  const [process, setProcess] = useState(initialProcess);
  const [line, setLine] = useState(initialLine);
  const [equip, setEquip] = useState(initialEquip);
  const [keyword, setKeyword] = useState(initialKeyword);
  const [startDate, setStartDate] = useState(initialStartDate);
  const [endDate, setEndDate] = useState(initialEndDate);
  const [page, setPage] = useState(1);
  const [records, setRecords] = useState<InformRecord[]>([]);
  const [fullRecords, setFullRecords] = useState<InformRecord[]>([]);
  const [options, setOptions] = useState<{ 라인?: string[]; 설비명?: string[] }>({});
  const [pageInfo, setPageInfo] = useState<InformPageInfo>(DEFAULT_PAGE_INFO);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [historyRows, setHistoryRows] = useState<InformRecord[] | null>(null);
  const [historyTitle, setHistoryTitle] = useState("");

  async function loadRecords(params?: {
    process?: string;
    line?: string;
    equip?: string;
    keyword?: string;
    startDate?: string;
    endDate?: string;
    page?: number;
  }) {
    setLoading(true);
    setError(null);
    const currentProcess = params?.process ?? process;
    const currentLine = params?.line ?? line;
    const currentEquip = params?.equip ?? equip;
    const currentKeyword = params?.keyword ?? keyword;
    const currentStartDate = params?.startDate ?? startDate;
    const currentEndDate = params?.endDate ?? endDate;
    const currentPage = params?.page ?? page;

    const search = new URLSearchParams({
      process: currentProcess,
      page: String(currentPage),
      page_size: String(DEFAULT_PAGE_INFO.page_size),
    });
    if (currentLine) search.set("line", currentLine);
    if (currentEquip) search.set("equip", currentEquip);
    if (currentKeyword.trim()) search.set("keyword", currentKeyword.trim());
    if (currentStartDate) search.set("start", currentStartDate);
    if (currentEndDate) search.set("end", currentEndDate);

    try {
      const data = await apiFetch<InformResponse>(`/api/inform/records?${search.toString()}`);
      setRecords(data.data);
      setFullRecords(data.full ?? []);
      setOptions(data.options ?? {});
      setPageInfo(data.page_info ?? DEFAULT_PAGE_INFO);
      setPage(data.page_info?.page ?? currentPage);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "인폼노트 조회에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRecords({
      process: initialProcess,
      line: initialLine,
      equip: initialEquip,
      keyword: initialKeyword,
      startDate: initialStartDate,
      endDate: initialEndDate,
      page: 1,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visiblePages = useMemo(() => {
    const pages: number[] = [];
    for (let current = pageInfo.block_start; current <= pageInfo.block_end; current += 1) {
      pages.push(current);
    }
    return pages;
  }, [pageInfo.block_end, pageInfo.block_start]);

  const hasRecords = records.length > 0;

  function handleOpenHistory(row: InformRecord) {
    const groupRows = fullRecords
      .filter(
        (item) =>
          String(item.라인 ?? "") === String(row.라인 ?? "") &&
          String(item.설비명 ?? "") === String(row.설비명 ?? "") &&
          String(item.에러명 ?? "") === String(row.에러명 ?? ""),
      )
      .sort((a, b) => String(b.날짜 ?? "").localeCompare(String(a.날짜 ?? "")));

    setHistoryTitle(`${row.라인} / ${row.설비명} / ${row.에러명}`);
    setHistoryRows(groupRows);
  }

  return (
    <div className="app-shell">
      <AppSidebar />

      <main className="main-panel inform-panel">
        <header className="inform-header">
          <div>
            <p className="welcome-label">INFORM NOTE DB</p>
            <h1>인폼노트 DB</h1>
            <p className="muted-text">기존 Django 시스템에서 유지하기로 한 유일한 DB 탭입니다.</p>
          </div>
        </header>

        <section className="filter-card">
          <div className="filter-grid inform-filter-grid">
            <label>
              <span>공정</span>
              <select value={process} onChange={(event) => setProcess(event.target.value)}>
                <option value="MP">MP</option>
                <option value="DA">DA</option>
                <option value="SMT">SMT</option>
              </select>
            </label>

            <label>
              <span>라인</span>
              <select value={line} onChange={(event) => setLine(event.target.value)}>
                <option value="">전체</option>
                {(options.라인 ?? []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>설비명</span>
              <select value={equip} onChange={(event) => setEquip(event.target.value)}>
                <option value="">전체</option>
                {(options.설비명 ?? []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>키워드 검색</span>
              <input
                placeholder="설비명, 에러명, 점검이력 키워드 입력"
                type="text"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
            </label>

            <label>
              <span>시작일</span>
              <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </label>

            <label>
              <span>종료일</span>
              <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </label>
          </div>

          <div className="filter-actions">
            <button
              className="primary-button"
              type="button"
              onClick={() => loadRecords({ process, line, equip, keyword, startDate, endDate, page: 1 })}
            >
              조회
            </button>
            <button
              className="ghost-button"
              type="button"
              onClick={() => {
                setProcess("MP");
                setLine("");
                setEquip("");
                setKeyword("");
                setStartDate("");
                setEndDate("");
                setPage(1);
                loadRecords({ process: "MP", line: "", equip: "", keyword: "", startDate: "", endDate: "", page: 1 });
              }}
            >
              초기화
            </button>
          </div>
        </section>

        <section className="table-card inform-table-card">
          <div className="table-header-row">
            <div>
              <p className="table-title">조회 결과</p>
              <p className="muted-text small-text">
                총 {pageInfo.total_items.toLocaleString()}건 중 {pageInfo.total_pages > 0 ? pageInfo.page : 0} / {pageInfo.total_pages} 페이지
              </p>
            </div>
          </div>

          {loading ? <p className="muted-text">인폼노트 데이터를 불러오는 중입니다...</p> : null}
          {error ? <p className="error-text">{error}</p> : null}

          {!loading && !error ? (
            <>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>No</th>
                      <th>날짜</th>
                      <th>라인</th>
                      <th>설비명</th>
                      <th>에러명</th>
                      <th>점검이력</th>
                      <th>중복수</th>
                      <th>이력보기</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hasRecords ? (
                      records.map((row) => (
                        <tr key={`${row.No}-${row.라인}-${row.설비명}-${row.에러명}-${row.날짜}`}>
                          <td>{row.No}</td>
                          <td>{String(row.날짜 ?? "").slice(0, 10)}</td>
                          <td>{row.라인}</td>
                          <td>{row.설비명}</td>
                          <td>{row.에러명}</td>
                          <td className="multiline-cell">{row.점검이력}</td>
                          <td>{row.중복수 ?? "-"}</td>
                          <td>
                            <button className="ghost-button inline-table-button" type="button" onClick={() => handleOpenHistory(row)}>
                              전체 이력
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td className="empty-table-cell" colSpan={8}>
                          조회 조건에 맞는 데이터가 없습니다.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {pageInfo.total_pages > 1 ? (
                <div className="pagination-bar">
                  <button
                    className="ghost-button pagination-arrow"
                    disabled={!pageInfo.has_prev_block}
                    type="button"
                    onClick={() => loadRecords({ process, line, equip, keyword, startDate, endDate, page: Math.max(1, pageInfo.block_start - 1) })}
                  >
                    ←
                  </button>

                  <div className="pagination-pages">
                    {visiblePages.map((pageNumber) => (
                      <button
                        key={pageNumber}
                        className={`pagination-page ${pageNumber === pageInfo.page ? "active" : ""}`}
                        type="button"
                        onClick={() => loadRecords({ process, line, equip, keyword, startDate, endDate, page: pageNumber })}
                      >
                        {pageNumber}
                      </button>
                    ))}
                  </div>

                  <button
                    className="ghost-button pagination-arrow"
                    disabled={!pageInfo.has_next_block}
                    type="button"
                    onClick={() => loadRecords({ process, line, equip, keyword, startDate, endDate, page: pageInfo.block_end + 1 })}
                  >
                    →
                  </button>
                </div>
              ) : null}
            </>
          ) : null}
        </section>
      </main>

      {historyRows ? (
        <div className="inform-history-backdrop" role="dialog" aria-modal="true">
          <div className="inform-history-modal">
            <div className="inform-history-header">
              <div>
                <p className="welcome-label">DUPLICATE HISTORY</p>
                <h3>{historyTitle}</h3>
                <p className="muted-text">동일한 라인 / 설비명 / 에러명 기준 이력을 최신순으로 표시합니다.</p>
              </div>
              <button className="inform-history-close" type="button" onClick={() => setHistoryRows(null)}>
                ×
              </button>
            </div>

            <div className="inform-history-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>No</th>
                    <th>날짜</th>
                    <th>라인</th>
                    <th>설비명</th>
                    <th>에러명</th>
                    <th>점검이력</th>
                  </tr>
                </thead>
                <tbody>
                  {historyRows.map((row, index) => (
                    <tr key={`${row.No}-${index}-${row.날짜}`}>
                      <td>{index + 1}</td>
                      <td>{toDateValue(String(row.날짜 ?? ""))}</td>
                      <td>{row.라인}</td>
                      <td>{row.설비명}</td>
                      <td>{row.에러명}</td>
                      <td className="multiline-cell">{row.점검이력}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
