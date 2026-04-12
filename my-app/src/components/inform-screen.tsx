"use client";

import { useEffect, useState } from "react";

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
};

type InformResponse = {
  data: InformRecord[];
  full: InformRecord[];
  options: {
    라인?: string[];
    설비명?: string[];
  };
};

export function InformScreen() {
  const [process, setProcess] = useState("MP");
  const [line, setLine] = useState("");
  const [equip, setEquip] = useState("");
  const [records, setRecords] = useState<InformRecord[]>([]);
  const [options, setOptions] = useState<{ 라인?: string[]; 설비명?: string[] }>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadRecords(params?: { process?: string; line?: string; equip?: string }) {
    setLoading(true);
    setError(null);
    const currentProcess = params?.process ?? process;
    const currentLine = params?.line ?? line;
    const currentEquip = params?.equip ?? equip;

    const search = new URLSearchParams({ process: currentProcess });
    if (currentLine) search.set("line", currentLine);
    if (currentEquip) search.set("equip", currentEquip);

    try {
      const data = await apiFetch<InformResponse>(`/api/inform/records?${search.toString()}`);
      setRecords(data.data);
      setOptions(data.options ?? {});
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "인폼노트 조회에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRecords();
  }, []);

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
          <div className="filter-grid">
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
          </div>

          <div className="filter-actions">
            <button
              className="primary-button"
              type="button"
              onClick={() => loadRecords({ process, line, equip })}
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
                loadRecords({ process: "MP", line: "", equip: "" });
              }}
            >
              초기화
            </button>
          </div>
        </section>

        <section className="table-card">
          {loading ? <p className="muted-text">인폼노트 데이터를 불러오는 중입니다...</p> : null}
          {error ? <p className="error-text">{error}</p> : null}

          {!loading && !error ? (
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
                  </tr>
                </thead>
                <tbody>
                  {records.map((row) => (
                    <tr key={`${row.No}-${row.에러명}-${row.날짜}`}>
                      <td>{row.No}</td>
                      <td>{String(row.날짜 ?? "").slice(0, 10)}</td>
                      <td>{row.라인}</td>
                      <td>{row.설비명}</td>
                      <td>{row.에러명}</td>
                      <td className="multiline-cell">{row.점검이력}</td>
                      <td>{row.중복수 ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
