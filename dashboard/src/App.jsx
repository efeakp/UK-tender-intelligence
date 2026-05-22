import React, { useState, useEffect, useCallback } from "react";

// ─── API Configuration ────────────────────────────────────────────────────────

const API_BASE = "http://localhost:8000";

// ─── Category config ──────────────────────────────────────────────────────────

const CATEGORY_CONFIG = {
  // Primary categories (used in filter strip)
  "Early Engagement":   { color: "#f5a142", bg: "rgba(245,161,66,0.12)",  border: "rgba(245,161,66,0.3)"  },
  "Future Opportunity": { color: "#64a0ff", bg: "rgba(100,160,255,0.12)", border: "rgba(100,160,255,0.3)" },
  "Opportunity":        { color: "#00e5a0", bg: "rgba(0,229,160,0.12)",   border: "rgba(0,229,160,0.3)"   },
  "Awarded Contract":   { color: "#b48ef5", bg: "rgba(180,142,245,0.12)", border: "rgba(180,142,245,0.3)" },
  // FaT legacy aliases → same colours as their unified equivalents
  "Tender":             { color: "#00e5a0", bg: "rgba(0,229,160,0.12)",   border: "rgba(0,229,160,0.3)"   },
  "Planning":           { color: "#f5a142", bg: "rgba(245,161,66,0.12)",  border: "rgba(245,161,66,0.3)"  },
  "Pipeline":           { color: "#64a0ff", bg: "rgba(100,160,255,0.12)", border: "rgba(100,160,255,0.3)" },
  "Award":              { color: "#b48ef5", bg: "rgba(180,142,245,0.12)", border: "rgba(180,142,245,0.3)" },
  // Additional FaT stages
  "Contract":           { color: "#9e9e9e", bg: "rgba(158,158,158,0.12)", border: "rgba(158,158,158,0.3)" },
  "Termination":        { color: "#ff6b6b", bg: "rgba(255,107,107,0.08)", border: "rgba(255,107,107,0.2)" },
};

// Unified procurement stages across FaT and Contracts Finder
// FaT:  Pipeline | Planning (Early Engagement) | Tender (Opportunity) | Award | Contract | Termination
// CF:   Future Opportunity | Early Engagement | Opportunity | Awarded Contract
const ALL_CATEGORIES = [
  "All Categories",
  "Opportunity",          // FaT: Tender stage | CF: Opportunity
  "Future Opportunity",   // FaT: Pipeline stage (UK1) | CF: Future Opportunity
  "Early Engagement",     // FaT: Planning stage (UK2) | CF: Early Engagement
  "Awarded Contract",     // FaT: Award stage | CF: Awarded Contract
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function scoreLabel(score) {
  if (score >= 7) return { label: "Strong match",    color: "#00e5a0" };
  if (score >= 4) return { label: "Likely relevant", color: "#f5c842" };
  return              { label: "Weak match",         color: "#ff6b6b" };
}

function formatDate(dateStr) {
  if (!dateStr) return "—";
  try {
    return new Date(dateStr).toLocaleDateString("en-GB", {
      day: "numeric", month: "short", year: "numeric",
    });
  } catch { return dateStr; }
}

function deadlineColor(dateStr) {
  if (!dateStr) return "rgba(255,255,255,0.65)";
  const days = Math.ceil((new Date(dateStr) - new Date()) / (1000 * 60 * 60 * 24));
  if (days <= 0)  return "rgba(255,255,255,0.3)";
  if (days <= 7)  return "#ff6b6b";
  if (days <= 14) return "#f5c842";
  return "rgba(255,255,255,0.65)";
}

function deadlineUrgency(dateStr) {
  if (!dateStr) return null;
  const days = Math.ceil((new Date(dateStr) - new Date()) / (1000 * 60 * 60 * 24));
  if (days <= 0)  return "CLOSED";
  if (days <= 7)  return `${days}d`;
  if (days <= 14) return `${days}d`;
  return null;
}

function deadlineUrgencyBg(dateStr) {
  if (!dateStr) return "transparent";
  const days = Math.ceil((new Date(dateStr) - new Date()) / (1000 * 60 * 60 * 24));
  if (days <= 7)  return "rgba(255,107,107,0.15)";
  if (days <= 14) return "rgba(245,200,66,0.15)";
  return "transparent";
}

function normaliseTender(t) {
  return {
    ...t,
    matched:  t.matched_keywords ?? t.matched ?? [],
    scopes:   t.matched_scopes   ?? t.scopes  ?? [],
    value:    t.value ?? "Value not stated",
    category: t.category ?? "Unknown",
  };
}

// ─── CSV Download ─────────────────────────────────────────────────────────────

function buildCsvUrl({ source, scope, category, search }) {
  const params = new URLSearchParams();
  params.set("min_score", "0");
  if (source   && source   !== "All")            params.set("source",   source);
  if (scope    && scope    !== "All")            params.set("scope",    scope);
  if (category && category !== "All Categories") params.set("category", category);
  if (search   && search.trim())                 params.set("q",        search.trim());
  return `${API_BASE}/export/csv?${params.toString()}`;
}

async function downloadCsv({ source, scope, category, search }) {
  const url = buildCsvUrl({ source, scope, category, search });
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : "nordic-energy-tenders.csv";
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(objectUrl);
    return null;
  } catch (err) {
    return err.message;
  }
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function ScoreBar({ score }) {
  const { color } = scoreLabel(score);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <div style={{ width: "80px", height: "4px", background: "rgba(255,255,255,0.1)", borderRadius: "2px", overflow: "hidden" }}>
        <div style={{ width: `${score * 10}%`, height: "100%", background: color, borderRadius: "2px", transition: "width 0.6s ease" }} />
      </div>
      <span style={{ fontSize: "11px", color, fontWeight: 600, letterSpacing: "0.05em" }}>
        {scoreLabel(score).label}
      </span>
    </div>
  );
}

function CategoryBadge({ category }) {
  const c = CATEGORY_CONFIG[category] || { color: "#aaa", bg: "rgba(255,255,255,0.08)", border: "rgba(255,255,255,0.2)" };
  return (
    <span style={{
      fontSize: "10px", padding: "2px 8px", borderRadius: "20px",
      background: c.bg, border: `1px solid ${c.border}`, color: c.color,
      fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase",
      whiteSpace: "nowrap",
    }}>
      {category}
    </span>
  );
}

function ScopeTag({ scope }) {
  const colors = {
    "Service 01: Renewable Energy Opportunity Identification": { bg: "rgba(0,229,160,0.12)",   border: "rgba(0,229,160,0.3)",   text: "#00e5a0" },
    "Service 02: Energy Feasibility Studies":                  { bg: "rgba(245,200,66,0.12)",  border: "rgba(245,200,66,0.3)",  text: "#f5c842" },
    "Service 03: Energy System Optimisation":                  { bg: "rgba(100,160,255,0.12)", border: "rgba(100,160,255,0.3)", text: "#64a0ff" },
    "Service 04: Business Case Development":                   { bg: "rgba(180,142,245,0.12)", border: "rgba(180,142,245,0.3)", text: "#b48ef5" },
    // Legacy scope labels (backwards compat)
    "Energy generation / renewables":  { bg: "rgba(0,229,160,0.12)",   border: "rgba(0,229,160,0.3)",   text: "#00e5a0" },
    "Heat networks / district energy": { bg: "rgba(245,200,66,0.12)",  border: "rgba(245,200,66,0.3)",  text: "#f5c842" },
    "Energy consulting / advisory":    { bg: "rgba(100,160,255,0.12)", border: "rgba(100,160,255,0.3)", text: "#64a0ff" },
  };
  const c = colors[scope] || { bg: "rgba(255,255,255,0.08)", border: "rgba(255,255,255,0.2)", text: "#ccc" };
  // Show short label: "S01" style for service scopes, original short name for legacy
  const short = scope.startsWith("Service")
    ? scope.replace("Service 0", "S0").split(":")[0]
    : scope.split(" / ")[0]
        .replace("Energy generation", "Renewables")
        .replace("Heat networks", "Heat Networks")
        .replace("Energy consulting", "Advisory");
  return (
    <span title={scope} style={{ fontSize: "10px", padding: "2px 8px", borderRadius: "20px", background: c.bg, border: `1px solid ${c.border}`, color: c.text, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", cursor: "help" }}>
      {short}
    </span>
  );
}

function SourceBadge({ source }) {
  const cfg = {
    "Find a Tender":             { bg: "rgba(138,99,210,0.2)",  border: "rgba(138,99,210,0.4)",  color: "#b48ef5", label: "FaT" },
    "Contracts Finder":          { bg: "rgba(30,144,255,0.2)",  border: "rgba(30,144,255,0.4)",  color: "#5babff", label: "CF"  },
    "Sell2Wales":                { bg: "rgba(220,50,50,0.2)",   border: "rgba(220,50,50,0.4)",   color: "#ff7070", label: "S2W" },
    "Public Contracts Scotland": { bg: "rgba(0,180,120,0.2)",   border: "rgba(0,180,120,0.4)",   color: "#00c878", label: "PCS" },
  };
  const c = cfg[source] || { bg: "rgba(255,255,255,0.1)", border: "rgba(255,255,255,0.2)", color: "#ccc", label: source };
  return (
    <span style={{ fontSize: "10px", padding: "2px 7px", borderRadius: "4px", background: c.bg, border: `1px solid ${c.border}`, color: c.color, fontWeight: 700, letterSpacing: "0.04em" }}>
      {c.label}
    </span>
  );
}

function RouteBadge({ route, eligible }) {
  const cfg = {
    "Further Competition": { color: "#00e5a0", bg: "rgba(0,229,160,0.12)",   border: "rgba(0,229,160,0.3)"   },
    "DPS":                 { color: "#64a0ff", bg: "rgba(100,160,255,0.12)", border: "rgba(100,160,255,0.3)" },
    "Open Market":         { color: "#aaa",    bg: "rgba(255,255,255,0.06)", border: "rgba(255,255,255,0.12)" },
    "Restricted":          { color: "#f5a142", bg: "rgba(245,161,66,0.12)",  border: "rgba(245,161,66,0.3)"  },
    "Unknown":             { color: "#555",    bg: "rgba(255,255,255,0.04)", border: "rgba(255,255,255,0.08)" },
  };
  const c = cfg[route] || cfg["Unknown"];
  if (route === "Unknown") return null;
  return (
    <span style={{ fontSize: "10px", padding: "2px 7px", borderRadius: "4px", background: c.bg, border: `1px solid ${c.border}`, color: c.color, fontWeight: 600, letterSpacing: "0.04em", display: "flex", alignItems: "center", gap: "3px" }}>
      {eligible && <span title="Nordic Energy is registered on this framework" style={{ color: "#00e5a0" }}>★</span>}
      {route}
    </span>
  );
}

function WatchlistBadge({ authority }) {
  if (!authority) return null;
  return (
    <span title={`Watched authority: ${authority}`} style={{
      fontSize: "10px", padding: "2px 7px", borderRadius: "4px",
      background: "rgba(255,200,0,0.15)", border: "1px solid rgba(255,200,0,0.4)",
      color: "#ffc800", fontWeight: 700, letterSpacing: "0.04em",
      display: "flex", alignItems: "center", gap: "3px", cursor: "help"
    }}>
      👁 WATCHED
    </span>
  );
}

// Notice type badge — shows UK1/UK2/UK3/UK4 with UK3 highlighted as urgent
function NoticeBadge({ noticeType }) {
  if (!noticeType) return null;
  const isUK3 = noticeType === "UK3";
  const isUK1 = noticeType === "UK1";
  const isUK2 = noticeType === "UK2";
  const label = noticeType;
  const bg     = isUK3 ? "rgba(255,80,80,0.15)"  : isUK1 ? "rgba(100,160,255,0.12)" : isUK2 ? "rgba(245,161,66,0.12)" : "rgba(255,255,255,0.06)";
  const border = isUK3 ? "rgba(255,80,80,0.5)"   : isUK1 ? "rgba(100,160,255,0.3)"  : isUK2 ? "rgba(245,161,66,0.3)"  : "rgba(255,255,255,0.15)";
  const color  = isUK3 ? "#ff5050"               : isUK1 ? "#64a0ff"                : isUK2 ? "#f5a142"               : "rgba(255,255,255,0.5)";
  const title  = isUK3
    ? "UK3: Planned Procurement — tender dropping within 40 days to 1 year. Tendering period may be only 10 days — prepare now"
    : isUK1 ? "UK1: Pipeline — potential future contract >£2m in next 18 months"
    : isUK2 ? "UK2: Preliminary Market Engagement — opportunity to influence scope before tender"
    : `Notice type: ${noticeType}`;
  return (
    <span title={title} style={{
      fontSize: "10px", padding: "2px 7px", borderRadius: "4px",
      background: bg, border: `1px solid ${border}`, color,
      fontWeight: 700, letterSpacing: "0.04em", cursor: "help",
      display: "flex", alignItems: "center", gap: "3px"
    }}>
      {isUK3 && "⚡ "}{label}
    </span>
  );
}

function TenderCard({ tender, onClick, selected }) {
  const { color } = scoreLabel(tender.score);
  return (
    <div
      onClick={() => onClick(tender)}
      style={{ cursor: "pointer", padding: "16px 20px", borderRadius: "10px", background: selected ? "rgba(0,229,160,0.06)" : "rgba(255,255,255,0.03)", border: `1px solid ${selected ? "rgba(0,229,160,0.3)" : "rgba(255,255,255,0.07)"}`, transition: "all 0.2s", marginBottom: "8px" }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = "rgba(255,255,255,0.055)"; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = "rgba(255,255,255,0.03)"; }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", marginBottom: "8px" }}>
        <div style={{ flex: 1 }}>
          {/* Badges row */}
          <div style={{ display: "flex", gap: "6px", alignItems: "center", marginBottom: "6px", flexWrap: "wrap" }}>
            <SourceBadge source={tender.source} />
            <CategoryBadge category={tender.category} />
            {tender.notice_type && <NoticeBadge noticeType={tender.notice_type} />}
            <RouteBadge route={tender.procurement_route} eligible={tender.nordic_eligible} />
            {tender.watchlist_match && <WatchlistBadge authority={tender.watchlist_authority} />}
            {tender.scopes.map(s => <ScopeTag key={s} scope={s} />)}
          </div>
          <div style={{ fontFamily: "'DM Serif Display', Georgia, serif", fontSize: "14px", color: "#f0ede8", lineHeight: "1.4", fontWeight: 400 }}>
            {tender.title}
          </div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontSize: "13px", fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>{tender.score}/10</div>
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "11px", color: "rgba(255,255,255,0.4)" }}>{tender.authority}</span>
        <div style={{ display: "flex", gap: "16px", fontSize: "11px", color: "rgba(255,255,255,0.4)", alignItems: "center" }}>
          <span>Value: <span style={{ color: "rgba(255,255,255,0.65)" }}>{tender.value}</span></span>
          <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            Deadline: <span style={{ color: deadlineColor(tender.deadline) }}>{formatDate(tender.deadline)}</span>
            {deadlineUrgency(tender.deadline) && <span style={{ fontSize: "9px", padding: "1px 5px", borderRadius: "8px", background: deadlineUrgencyBg(tender.deadline), color: deadlineColor(tender.deadline), fontWeight: 700 }}>{deadlineUrgency(tender.deadline)}</span>}
          </span>
        </div>
      </div>
      <div style={{ marginTop: "8px" }}>
        <ScoreBar score={tender.score} />
      </div>
    </div>
  );
}

function AiPanel({ summary, loading, error }) {
  if (loading) return (
    <div style={{ marginTop: "16px", padding: "16px", borderRadius: "8px", background: "rgba(138,99,210,0.08)", border: "1px solid rgba(138,99,210,0.2)", textAlign: "center" }}>
      <div style={{ fontSize: "12px", color: "#b48ef5", letterSpacing: "0.06em" }}>⚡ ANALYSING WITH AI...</div>
    </div>
  );
  if (error) return (
    <div style={{ marginTop: "16px", padding: "12px", borderRadius: "8px", background: "rgba(198,40,40,0.08)", border: "1px solid rgba(198,40,40,0.2)", fontSize: "12px", color: "#ef9a9a" }}>
      ⚠ AI analysis unavailable: {error}
    </div>
  );
  if (!summary) return null;

  const recColors = {
    "Go":      { bg: "rgba(0,229,160,0.1)",  border: "rgba(0,229,160,0.3)",  text: "#00e5a0" },
    "Consider":{ bg: "rgba(245,200,66,0.1)", border: "rgba(245,200,66,0.3)", text: "#f5c842" },
    "No-go":   { bg: "rgba(255,107,107,0.1)",border: "rgba(255,107,107,0.3)",text: "#ff6b6b" },
  };
  const rc = recColors[summary.recommendation] || recColors["Consider"];

  return (
    <div style={{ marginTop: "16px", padding: "16px", borderRadius: "8px", background: "rgba(138,99,210,0.06)", border: "1px solid rgba(138,99,210,0.2)" }}>
      <div style={{ fontSize: "10px", color: "#b48ef5", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "12px", display: "flex", alignItems: "center", gap: "6px" }}>
        ⚡ AI Analysis
        <span style={{ fontSize: "9px", padding: "1px 6px", borderRadius: "10px", background: "rgba(138,99,210,0.2)", color: "#b48ef5" }}>
          {summary.confidence} confidence
        </span>
      </div>

      {/* Recommendation badge */}
      <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
        <span style={{ padding: "4px 14px", borderRadius: "20px", background: rc.bg, border: `1px solid ${rc.border}`, color: rc.text, fontSize: "13px", fontWeight: 700, letterSpacing: "0.04em" }}>
          {summary.recommendation}
        </span>
        <span style={{ fontSize: "12px", color: "rgba(255,255,255,0.5)" }}>{summary.reasoning}</span>
      </div>

      {/* Summary */}
      <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.65)", lineHeight: "1.6", marginBottom: "12px", borderLeft: "2px solid rgba(138,99,210,0.4)", paddingLeft: "10px" }}>
        {summary.summary}
      </div>

      {/* Key requirements */}
      {summary.key_requirements?.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <div style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: "6px" }}>Key Requirements</div>
          {summary.key_requirements.map((req, i) => (
            <div key={i} style={{ fontSize: "11px", color: "rgba(255,255,255,0.55)", marginBottom: "3px", paddingLeft: "10px" }}>• {req}</div>
          ))}
        </div>
      )}

      {/* Fit assessment */}
      <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.55)", lineHeight: "1.6", marginBottom: "10px" }}>
        <span style={{ color: "rgba(138,99,210,0.8)", fontWeight: 600 }}>Fit: </span>{summary.fit_assessment}
      </div>

      {/* Matched services */}
      {summary.matched_services?.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
          {summary.matched_services.map((s, i) => (
            <span key={i} style={{ fontSize: "10px", padding: "2px 8px", borderRadius: "10px", background: "rgba(138,99,210,0.15)", border: "1px solid rgba(138,99,210,0.25)", color: "#b48ef5" }}>
              {s.split(":")[0]}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function DetailPanel({ tender, onClose }) {
  const [aiSummary, setAiSummary]   = React.useState(null);
  const [aiLoading, setAiLoading]   = React.useState(false);
  const [aiError, setAiError]       = React.useState(null);
  const [aiRequested, setAiRequested] = React.useState(false);
  const [record, setRecord]               = React.useState(null);
  const [recordLoading, setRecordLoading] = React.useState(false);
  const [recordError, setRecordError]     = React.useState(null);
  const [recordOpen, setRecordOpen]       = React.useState(false);

  // Reset all state when tender changes
  React.useEffect(() => {
    setAiSummary(null);
    setAiLoading(false);
    setAiError(null);
    setAiRequested(false);
    setRecord(null);
    setRecordLoading(false);
    setRecordError(null);
    setRecordOpen(false);
  }, [tender?.id]);

  const requestAiAnalysis = async () => {
    setAiRequested(true);
    setAiLoading(true);
    setAiError(null);
    try {
      const res = await fetch(`${API_BASE}/tenders/${encodeURIComponent(tender.id)}/summarise`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setAiSummary(data);
    } catch (err) {
      setAiError(err.message || "Unknown error");
    } finally {
      setAiLoading(false);
    }
  };

  const fetchRecord = async () => {
    setRecordOpen(true);
    if (record) return;
    setRecordLoading(true);
    setRecordError(null);
    try {
      const ocidParam = tender.ocid ? `?ocid=${encodeURIComponent(tender.ocid)}` : "";
      const res = await fetch(`${API_BASE}/tenders/${encodeURIComponent(tender.id)}/record${ocidParam}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRecord(data);
    } catch (err) {
      setRecordError(err.message || "Unknown error");
    } finally {
      setRecordLoading(false);
    }
  };

  if (!tender) return null;
  return (
    <div style={{ position: "sticky", top: "0", padding: "24px", background: "rgba(14,20,30,0.95)", borderRadius: "12px", border: "1px solid rgba(0,229,160,0.2)", backdropFilter: "blur(20px)", maxHeight: "90vh", overflowY: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px" }}>
        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
          <SourceBadge source={tender.source} />
          <CategoryBadge category={tender.category} />
          {tender.scopes.map(s => <ScopeTag key={s} scope={s} />)}
        </div>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.4)", cursor: "pointer", fontSize: "18px", padding: "0", lineHeight: 1 }}>✕</button>
      </div>
      <h2 style={{ fontFamily: "'DM Serif Display', Georgia, serif", fontSize: "20px", color: "#f0ede8", lineHeight: "1.35", marginBottom: "16px", fontWeight: 400 }}>
        {tender.title}
      </h2>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "20px" }}>
        {[
          ["Authority",          tender.authority],
          ["Contract Value",     tender.lot_count > 0 ? `${tender.value} (${tender.lot_count} lots)` : tender.value],
          ["Published",          formatDate(tender.published)],
          ["Deadline",           formatDate(tender.deadline)],
          ["Reference",          tender.id],
          ["Procurement Stage",  tender.category],
          ["Route to Market",    tender.procurement_route],
          ["Framework",          tender.framework_name !== "Unknown" ? tender.framework_name : "Open market / not specified"],
        ].map(([k, v]) => (
          <div key={k} style={{ background: "rgba(255,255,255,0.04)", borderRadius: "8px", padding: "10px 12px" }}>
            <div style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "3px" }}>{k}</div>
            <div style={{ fontSize: "13px", color: "#e8e4df", wordBreak: "break-all" }}>{v}</div>
          </div>
        ))}
        <div style={{ background: "rgba(0,229,160,0.06)", borderRadius: "8px", padding: "10px 12px", border: "1px solid rgba(0,229,160,0.15)" }}>
          <div style={{ fontSize: "10px", color: "rgba(0,229,160,0.6)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "3px" }}>Relevance Score</div>
          <div style={{ fontSize: "20px", fontWeight: 700, color: scoreLabel(tender.score).color }}>
            {tender.score}<span style={{ fontSize: "12px", color: "rgba(255,255,255,0.3)" }}>/10</span>
          </div>
        </div>
      </div>
      {tender.nordic_eligible && (
        <div style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "8px", background: "rgba(0,229,160,0.08)", border: "1px solid rgba(0,229,160,0.25)", display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "14px" }}>★</span>
          <span style={{ fontSize: "12px", color: "#00e5a0", fontWeight: 600 }}>Nordic Energy is registered on the {tender.framework_name} framework — eligible to bid directly.</span>
        </div>
      )}
      <div style={{ marginBottom: "20px" }}>
        <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "8px" }}>Description</div>
        <div style={{ maxHeight: "140px", overflowY: "auto", paddingRight: "6px" }}>
          <p style={{ fontSize: "13px", color: "rgba(255,255,255,0.65)", lineHeight: "1.65", margin: 0 }}>{tender.description || "No description available."}</p>
        </div>
      </div>
      {(tender.contact_name || tender.contact_email || tender.contact_phone || tender.contact_url) && (
        <div style={{ marginBottom: "20px" }}>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "8px" }}>Contact Point</div>
          <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: "8px", padding: "12px 14px" }}>
            {tender.contact_name && (
              <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.7)", marginBottom: "5px" }}>
                <span style={{ color: "rgba(255,255,255,0.35)", marginRight: "6px" }}>Name</span>{tender.contact_name}
              </div>
            )}
            {tender.contact_email && (
              <div style={{ fontSize: "12px", marginBottom: "5px" }}>
                <span style={{ color: "rgba(255,255,255,0.35)", marginRight: "6px" }}>Email</span>
                <a href={`mailto:${tender.contact_email}`} style={{ color: "#00e5a0", textDecoration: "none" }}>{tender.contact_email}</a>
              </div>
            )}
            {tender.contact_phone && (
              <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.7)", marginBottom: "5px" }}>
                <span style={{ color: "rgba(255,255,255,0.35)", marginRight: "6px" }}>Phone</span>{tender.contact_phone}
              </div>
            )}
            {tender.contact_url && (
              <div style={{ fontSize: "12px" }}>
                <span style={{ color: "rgba(255,255,255,0.35)", marginRight: "6px" }}>Web</span>
                <a href={tender.contact_url} target="_blank" rel="noopener noreferrer" style={{ color: "#64a0ff", textDecoration: "none" }}>{tender.contact_url}</a>
              </div>
            )}
          </div>
        </div>
      )}
      {tender.matched.length > 0 && (
        <div style={{ marginBottom: "20px" }}>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "8px" }}>Matched Keywords</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {tender.matched.map(kw => (
              <span key={kw} style={{ fontSize: "11px", padding: "3px 9px", borderRadius: "20px", background: "rgba(0,229,160,0.1)", border: "1px solid rgba(0,229,160,0.2)", color: "#00e5a0" }}>{kw}</span>
            ))}
          </div>
        </div>
      )}
      {tender.ocid && (
        <div style={{ marginBottom: "12px" }}>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "4px" }}>Procurement Family (OCID)</div>
          <div style={{ fontSize: "11px", fontFamily: "monospace", color: "rgba(255,255,255,0.4)" }}>{tender.ocid}</div>
          <div style={{ fontSize: "10px", color: "rgba(255,255,255,0.25)", marginTop: "2px" }}>
            All notices sharing this OCID are part of the same procurement.
            {tender.notice_type === "UK1" && " A UK4 tender notice will follow — prepare now."}
            {tender.notice_type === "UK2" && " Respond to this engagement to influence the scope before UK4 tender drops."}
            {tender.notice_type === "UK3" && " ⚡ Tender expected within 40 days–1 year. May have only 10-day tendering window."}
          </div>
          {tender.notice_type === "UK3" && tender.future_notice_date && (
            <div style={{ fontSize: "11px", color: "#f5a142", marginTop: "4px", fontWeight: 600 }}>
              Expected tender date: {formatDate(tender.future_notice_date)}
            </div>
          )}
        </div>
      )}
      {/* Procurement history — FaT tenders with OCID only */}
      {tender.source === "Find a Tender" && tender.ocid && (
        <div style={{ marginBottom: "16px" }}>
          {!recordOpen ? (
            <button onClick={fetchRecord} style={{ width: "100%", padding: "8px", borderRadius: "8px", background: "rgba(100,160,255,0.08)", border: "1px solid rgba(100,160,255,0.2)", color: "#64a0ff", fontSize: "12px", fontWeight: 600, cursor: "pointer", letterSpacing: "0.04em" }}>
              📋 View Full Procurement History
            </button>
          ) : recordLoading ? (
            <div style={{ padding: "10px", fontSize: "12px", color: "rgba(255,255,255,0.4)", textAlign: "center" }}>Loading history…</div>
          ) : recordError ? (
            <div style={{ padding: "10px", fontSize: "12px", color: "#ef9a9a" }}>⚠ {recordError}</div>
          ) : record ? (
            <div style={{ background: "rgba(100,160,255,0.05)", borderRadius: "8px", border: "1px solid rgba(100,160,255,0.15)", padding: "12px" }}>
              <div style={{ fontSize: "10px", color: "rgba(100,160,255,0.7)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "8px" }}>
                Procurement Timeline — {record.notices.length} notice{record.notices.length !== 1 ? "s" : ""}
              </div>
              {record.notices.map((n, i) => (
                <div key={n.notice_id || i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderTop: i > 0 ? "1px solid rgba(255,255,255,0.05)" : "none", gap: "8px" }}>
                  <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                    <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "4px", background: "rgba(100,160,255,0.15)", border: "1px solid rgba(100,160,255,0.25)", color: "#64a0ff", fontWeight: 700, fontFamily: "monospace" }}>{n.notice_type || "—"}</span>
                    <span style={{ fontSize: "11px", color: "rgba(255,255,255,0.5)" }}>{formatDate(n.date)}</span>
                  </div>
                  <a href={n.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: "11px", color: "rgba(100,160,255,0.7)", textDecoration: "none" }}>View →</a>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
      {tender.cpv_codes && tender.cpv_codes.length > 0 && (
        <div style={{ marginBottom: "20px" }}>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "8px" }}>CPV Codes</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {tender.cpv_codes.map(cpv => (
              <span key={cpv} style={{ fontSize: "10px", padding: "2px 8px", borderRadius: "4px", fontFamily: "monospace", background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)", color: "rgba(255,255,255,0.5)" }}>{cpv}</span>
            ))}
          </div>
        </div>
      )}
      {/* AI Analysis */}
      {!aiRequested ? (
        <button onClick={requestAiAnalysis}
          style={{ width: "100%", marginBottom: "12px", padding: "10px", borderRadius: "8px", background: "rgba(138,99,210,0.12)", border: "1px solid rgba(138,99,210,0.3)", color: "#b48ef5", fontSize: "12px", fontWeight: 600, cursor: "pointer", letterSpacing: "0.04em", display: "flex", alignItems: "center", justifyContent: "center", gap: "6px" }}>
          ⚡ AI Analysis — Go / No-go Assessment
        </button>
      ) : (
        <AiPanel summary={aiSummary} loading={aiLoading} error={aiError} />
      )}

      <a href={tender.url} target="_blank" rel="noopener noreferrer"
        style={{ display: "block", textAlign: "center", marginTop: "12px", padding: "11px", borderRadius: "8px", background: "rgba(0,229,160,0.15)", border: "1px solid rgba(0,229,160,0.35)", color: "#00e5a0", textDecoration: "none", fontSize: "13px", fontWeight: 600, letterSpacing: "0.04em" }}>
        View on {tender.source} →
      </a>
    </div>
  );
}

function ErrorBanner({ message, onRetry }) {
  return (
    <div style={{ margin: "0 0 20px", padding: "14px 18px", borderRadius: "10px", background: "rgba(198,40,40,0.1)", border: "1px solid rgba(198,40,40,0.3)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
      <div>
        <div style={{ fontSize: "12px", fontWeight: 700, color: "#ef9a9a", marginBottom: "3px" }}>⚠ Could not reach API</div>
        <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.45)", fontFamily: "monospace" }}>{message}</div>
        <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.3)", marginTop: "4px" }}>
          Ensure the FastAPI server is running: <span style={{ fontFamily: "monospace", color: "rgba(255,255,255,0.5)" }}>uvicorn app.main:app --reload --port 8000</span>
        </div>
      </div>
      <button onClick={onRetry} style={{ flexShrink: 0, padding: "7px 14px", borderRadius: "7px", background: "rgba(198,40,40,0.2)", border: "1px solid rgba(198,40,40,0.4)", color: "#ef9a9a", fontSize: "12px", fontWeight: 600, cursor: "pointer" }}>
        Retry
      </button>
    </div>
  );
}

function ApiStatusDot({ healthy }) {
  return (
    <span style={{ display: "inline-block", width: "7px", height: "7px", borderRadius: "50%", background: healthy ? "#00e5a0" : "#ff6b6b", marginRight: "5px", boxShadow: healthy ? "0 0 6px #00e5a0" : "0 0 6px #ff6b6b" }} />
  );
}

// ─── Category stats strip ─────────────────────────────────────────────────────

function CategoryStrip({ tenders, activeCategory, onSelect }) {
  const counts = tenders.reduce((acc, t) => {
    acc[t.category] = (acc[t.category] || 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ display: "flex", gap: "8px", marginBottom: "20px", flexWrap: "wrap" }}>
      {ALL_CATEGORIES.map(cat => {
        const isAll    = cat === "All Categories";
        const count    = isAll ? tenders.length : (counts[cat] ?? 0);
        const active   = activeCategory === cat;
        const cfg      = CATEGORY_CONFIG[cat] || { color: "#aaa", bg: "rgba(255,255,255,0.06)", border: "rgba(255,255,255,0.12)" };
        return (
          <button key={cat} onClick={() => onSelect(cat)}
            style={{
              padding: "6px 14px", borderRadius: "20px", cursor: "pointer", fontSize: "12px", fontWeight: 600,
              background: active ? cfg.bg : "rgba(255,255,255,0.04)",
              border: `1px solid ${active ? cfg.border : "rgba(255,255,255,0.08)"}`,
              color: active ? cfg.color : "rgba(255,255,255,0.45)",
              transition: "all 0.2s",
            }}>
            {cat} <span style={{ opacity: 0.7, fontWeight: 400 }}>({count})</span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Competitor tracking ──────────────────────────────────────────────────────

const COMPETITOR_COLORS = {
  "Advanced Infrastructure":       "#64a0ff",
  "City Science":                  "#00e5a0",
  "Grid Edge":                     "#f5a142",
  "Tibo Energy":                   "#b48ef5",
  "Centre for Sustainable Energy": "#f5c842",
  "Element Energy":                "#ff7070",
  "Regen":                         "#00c878",
  "Living Places":                 "#ff9f5b",
  "Vital Energi":                  "#e040fb",
};

function CompetitorRow({ tender, onClick, selected }) {
  const color = COMPETITOR_COLORS[tender.competitor_name] || "#aaa";
  return (
    <div
      onClick={onClick}
      style={{ cursor: "pointer", padding: "16px 20px", borderRadius: "10px", background: selected ? `${color}0d` : "rgba(255,255,255,0.03)", border: `1px solid ${selected ? color + "44" : "rgba(255,255,255,0.07)"}`, transition: "all 0.2s", marginBottom: "8px" }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = "rgba(255,255,255,0.055)"; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = "rgba(255,255,255,0.03)"; }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", gap: "6px", marginBottom: "6px", alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: "10px", padding: "2px 8px", borderRadius: "20px", background: `${color}22`, border: `1px solid ${color}55`, color, fontWeight: 700, letterSpacing: "0.05em" }}>
              {tender.competitor_name}
            </span>
            <SourceBadge source={tender.source} />
          </div>
          <div style={{ fontFamily: "'DM Serif Display', Georgia, serif", fontSize: "14px", color: "#f0ede8", lineHeight: "1.4", fontWeight: 400 }}>
            {tender.title}
          </div>
          <div style={{ marginTop: "5px", fontSize: "11px", color: "rgba(255,255,255,0.4)" }}>{tender.authority}</div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.65)" }}>{tender.value}</div>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.3)", marginTop: "4px" }}>{formatDate(tender.published)}</div>
        </div>
      </div>
      {(tender.contact_name || tender.contact_email) && (
        <div style={{ marginTop: "8px", display: "flex", gap: "14px", fontSize: "11px", color: "rgba(255,255,255,0.4)" }}>
          {tender.contact_name  && <span>👤 {tender.contact_name}</span>}
          {tender.contact_email && <span>✉ {tender.contact_email}</span>}
        </div>
      )}
    </div>
  );
}

function ContactPanel({ tender, onClose }) {
  const color = COMPETITOR_COLORS[tender.competitor_name] || "#aaa";
  const hasContact = tender.contact_name || tender.contact_email || tender.contact_phone || tender.contact_url;
  return (
    <div style={{ position: "sticky", top: "0", padding: "24px", background: "rgba(14,20,30,0.95)", borderRadius: "12px", border: `1px solid ${color}44`, backdropFilter: "blur(20px)", maxHeight: "90vh", overflowY: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px" }}>
        <span style={{ fontSize: "10px", padding: "2px 10px", borderRadius: "20px", background: `${color}22`, border: `1px solid ${color}55`, color, fontWeight: 700, letterSpacing: "0.05em" }}>
          {tender.competitor_name}
        </span>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.4)", cursor: "pointer", fontSize: "18px", padding: "0", lineHeight: 1 }}>✕</button>
      </div>
      <h2 style={{ fontFamily: "'DM Serif Display', Georgia, serif", fontSize: "18px", color: "#f0ede8", lineHeight: "1.35", marginBottom: "16px", fontWeight: 400 }}>
        {tender.title}
      </h2>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "20px" }}>
        {[
          ["Authority", tender.authority],
          ["Value",     tender.value],
          ["Awarded",   formatDate(tender.published)],
          ["Source",    tender.source],
        ].map(([k, v]) => (
          <div key={k} style={{ background: "rgba(255,255,255,0.04)", borderRadius: "8px", padding: "10px 12px" }}>
            <div style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "3px" }}>{k}</div>
            <div style={{ fontSize: "13px", color: "#e8e4df" }}>{v}</div>
          </div>
        ))}
      </div>
      {hasContact && (
        <div style={{ marginBottom: "20px", padding: "14px", borderRadius: "8px", background: `${color}0d`, border: `1px solid ${color}33` }}>
          <div style={{ fontSize: "10px", color, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "10px", fontWeight: 700 }}>Contact Point</div>
          {tender.contact_name && (
            <div style={{ fontSize: "13px", color: "#e8e4df", marginBottom: "7px" }}>
              <span style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", marginRight: "8px" }}>Name</span>
              {tender.contact_name}
            </div>
          )}
          {tender.contact_email && (
            <div style={{ fontSize: "13px", marginBottom: "7px" }}>
              <span style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", marginRight: "8px" }}>Email</span>
              <a href={`mailto:${tender.contact_email}`} style={{ color: "#00e5a0", textDecoration: "none" }}>{tender.contact_email}</a>
            </div>
          )}
          {tender.contact_phone && (
            <div style={{ fontSize: "13px", color: "#e8e4df", marginBottom: "7px" }}>
              <span style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", marginRight: "8px" }}>Phone</span>
              {tender.contact_phone}
            </div>
          )}
          {tender.contact_url && (
            <div style={{ fontSize: "13px" }}>
              <span style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", marginRight: "8px" }}>Web</span>
              <a href={tender.contact_url} target="_blank" rel="noopener noreferrer" style={{ color: "#64a0ff", textDecoration: "none", wordBreak: "break-all" }}>{tender.contact_url}</a>
            </div>
          )}
        </div>
      )}
      <a href={tender.url} target="_blank" rel="noopener noreferrer"
        style={{ display: "block", textAlign: "center", padding: "11px", borderRadius: "8px", background: `${color}22`, border: `1px solid ${color}55`, color, textDecoration: "none", fontSize: "13px", fontWeight: 600, letterSpacing: "0.04em" }}>
        View Award Notice →
      </a>
    </div>
  );
}

function CompetitorTab({ tenders }) {
  const [selected, setSelected] = useState(null);
  const competitorWins = tenders.filter(t => t.competitor_win);

  const byCompetitor = competitorWins.reduce((acc, t) => {
    const name = t.competitor_name || "Unknown";
    if (!acc[name]) acc[name] = [];
    acc[name].push(t);
    return acc;
  }, {});

  return (
    <div className="fade-up">
      {Object.keys(byCompetitor).length > 0 && (
        <div style={{ display: "flex", gap: "10px", marginBottom: "24px", flexWrap: "wrap" }}>
          {Object.entries(byCompetitor).sort((a, b) => b[1].length - a[1].length).map(([name, wins]) => {
            const color = COMPETITOR_COLORS[name] || "#aaa";
            return (
              <div key={name} style={{ padding: "8px 16px", borderRadius: "20px", background: `${color}22`, border: `1px solid ${color}55` }}>
                <span style={{ color, fontWeight: 700, fontSize: "13px" }}>{name}</span>
                <span style={{ marginLeft: "8px", color: "rgba(255,255,255,0.45)", fontSize: "12px" }}>{wins.length} win{wins.length !== 1 ? "s" : ""}</span>
              </div>
            );
          })}
        </div>
      )}
      {competitorWins.length === 0 ? (
        <div style={{ textAlign: "center", padding: "60px 20px", color: "rgba(255,255,255,0.3)" }}>
          <div style={{ fontSize: "32px", marginBottom: "12px" }}>🔍</div>
          <div style={{ fontSize: "14px" }}>No competitor wins in current data — refresh to check the latest awarded contracts.</div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 380px" : "1fr", gap: "20px", alignItems: "start" }}>
          <div>
            {competitorWins.map((t, i) => (
              <div key={t.id} className="fade-up" style={{ animationDelay: `${i * 0.03}s` }}>
                <CompetitorRow
                  tender={t}
                  selected={selected?.id === t.id}
                  onClick={() => setSelected(selected?.id === t.id ? null : t)}
                />
              </div>
            ))}
          </div>
          {selected && <ContactPanel tender={selected} onClose={() => setSelected(null)} />}
        </div>
      )}
    </div>
  );
}

// ─── Market Intelligence ──────────────────────────────────────────────────────

function MarketAwardRow({ tender, onClick, selected }) {
  const competitorColor = tender.competitor_win ? (COMPETITOR_COLORS[tender.competitor_name] || "#aaa") : null;
  return (
    <div
      onClick={onClick}
      style={{ cursor: "pointer", padding: "14px 20px", borderRadius: "10px", background: selected ? "rgba(100,160,255,0.06)" : "rgba(255,255,255,0.03)", border: `1px solid ${selected ? "rgba(100,160,255,0.3)" : "rgba(255,255,255,0.07)"}`, transition: "all 0.2s", marginBottom: "6px" }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = "rgba(255,255,255,0.055)"; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = "rgba(255,255,255,0.03)"; }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", gap: "5px", marginBottom: "5px", flexWrap: "wrap", alignItems: "center" }}>
            <SourceBadge source={tender.source} />
            {tender.competitor_win && (
              <span style={{ fontSize: "10px", padding: "2px 7px", borderRadius: "20px", background: `${competitorColor}22`, border: `1px solid ${competitorColor}55`, color: competitorColor, fontWeight: 700, letterSpacing: "0.04em" }}>
                {tender.competitor_name}
              </span>
            )}
            {(tender.scopes ?? []).slice(0, 2).map(s => <ScopeTag key={s} scope={s} />)}
          </div>
          <div style={{ fontFamily: "'DM Serif Display', Georgia, serif", fontSize: "13px", color: "#f0ede8", lineHeight: "1.35", marginBottom: "4px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {tender.title}
          </div>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)" }}>{tender.authority}</div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.65)", fontWeight: 600 }}>{tender.value}</div>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.3)", marginTop: "3px" }}>{formatDate(tender.published)}</div>
        </div>
      </div>
      {tender.awarded_supplier && (
        <div style={{ marginTop: "6px", fontSize: "11px", color: "rgba(255,255,255,0.4)" }}>
          <span style={{ color: "rgba(255,255,255,0.22)" }}>Winner: </span>
          <span style={{ color: tender.competitor_win ? competitorColor : "rgba(255,255,255,0.55)" }}>
            {tender.awarded_supplier}
          </span>
        </div>
      )}
    </div>
  );
}

function MarketTab() {
  const [status, setStatus]         = useState(null);
  const [awards, setAwards]         = useState([]);
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError]           = useState(null);
  const [selected, setSelected]     = useState(null);
  const [sourceFilter, setSourceFilter] = useState("All");
  const [scopeFilter, setScopeFilter]   = useState("All");
  const [competitorOnly, setCompetitorOnly] = useState(false);

  const loadAwards = async () => {
    try {
      const res = await fetch(`${API_BASE}/market/awards?page_size=1000&sort_by=published&sort_dir=desc`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setAwards((data.tenders ?? []).map(normaliseTender));
    } catch (err) {
      setError(err.message);
    }
  };

  const checkStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/market/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setStatus(data);
      if (data.populated) await loadAwards();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const triggerRefresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/market/refresh`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setStatus({ populated: true, award_count: data.awards_found });
      await loadAwards();
    } catch (err) {
      setError(err.message);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => { checkStatus(); }, []);

  const byCompetitor = awards
    .filter(t => t.competitor_win)
    .reduce((acc, t) => { const n = t.competitor_name || "Unknown"; acc[n] = (acc[n] || 0) + 1; return acc; }, {});

  const filtered = awards.filter(t => {
    if (sourceFilter !== "All" && t.source !== sourceFilter) return false;
    if (scopeFilter  !== "All" && !(t.scopes ?? []).includes(scopeFilter)) return false;
    if (competitorOnly && !t.competitor_win) return false;
    return true;
  });

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "60px", color: "rgba(255,255,255,0.35)" }}>
        <div style={{ width: "32px", height: "32px", border: "2px solid rgba(100,160,255,0.2)", borderTop: "2px solid #64a0ff", borderRadius: "50%", animation: "spin 0.8s linear infinite", margin: "0 auto 16px" }} />
        <div style={{ fontSize: "13px" }}>Checking market data…</div>
      </div>
    );
  }

  return (
    <div className="fade-up">
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px", flexWrap: "wrap", gap: "12px" }}>
        <div>
          <div style={{ fontSize: "14px", color: "#f0ede8", fontWeight: 600, marginBottom: "3px" }}>CPV-Matched Awarded Contracts</div>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)" }}>
            FaT 30d · Contracts Finder 6 months · Sell2Wales + PCS 12 months — filtered to Nordic Energy CPV codes
          </div>
        </div>
        <button onClick={triggerRefresh} disabled={refreshing}
          style={{ padding: "8px 16px", borderRadius: "7px", background: refreshing ? "rgba(255,255,255,0.04)" : "rgba(100,160,255,0.12)", border: `1px solid ${refreshing ? "rgba(255,255,255,0.1)" : "rgba(100,160,255,0.3)"}`, color: refreshing ? "rgba(255,255,255,0.3)" : "#64a0ff", fontSize: "12px", fontWeight: 600, cursor: refreshing ? "not-allowed" : "pointer", letterSpacing: "0.04em", whiteSpace: "nowrap" }}>
          {refreshing ? "⏳ Fetching (2–5 min)…" : status?.populated ? "↻ Refresh" : "Load Market Data"}
        </button>
      </div>

      {error && (
        <div style={{ marginBottom: "16px", padding: "12px 14px", borderRadius: "8px", background: "rgba(198,40,40,0.1)", border: "1px solid rgba(198,40,40,0.3)", fontSize: "12px", color: "#ef9a9a" }}>⚠ {error}</div>
      )}

      {refreshing && (
        <div style={{ padding: "24px", borderRadius: "10px", background: "rgba(100,160,255,0.05)", border: "1px solid rgba(100,160,255,0.15)", textAlign: "center", marginBottom: "20px" }}>
          <div style={{ width: "28px", height: "28px", border: "2px solid rgba(100,160,255,0.2)", borderTop: "2px solid #64a0ff", borderRadius: "50%", animation: "spin 0.8s linear infinite", margin: "0 auto 12px" }} />
          <div style={{ fontSize: "13px", color: "#64a0ff", fontWeight: 600, marginBottom: "6px" }}>Fetching market data…</div>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)" }}>
            Querying all sources for CPV-matched awarded contracts.<br />
            This typically takes 2–5 minutes.
          </div>
        </div>
      )}

      {!status?.populated && !refreshing && (
        <div style={{ textAlign: "center", padding: "60px 20px", color: "rgba(255,255,255,0.3)" }}>
          <div style={{ fontSize: "36px", marginBottom: "12px" }}>📊</div>
          <div style={{ fontSize: "14px", marginBottom: "8px", color: "rgba(255,255,255,0.45)" }}>No market data loaded yet.</div>
          <div style={{ fontSize: "12px" }}>Click "Load Market Data" to fetch CPV-relevant awarded contracts from the past year.</div>
        </div>
      )}

      {status?.populated && !refreshing && awards.length > 0 && (<>
        {/* Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px", marginBottom: "20px" }}>
          {[
            { label: "Total Awards",   value: awards.length,                                               color: "#64a0ff" },
            { label: "Competitor Wins",value: awards.filter(t => t.competitor_win).length,                 color: "#ff6b6b" },
            { label: "With Contact",   value: awards.filter(t => t.contact_email || t.contact_name).length,color: "#00e5a0" },
            { label: "Total Value",    value: "£" + (awards.reduce((s, t) => s + (t.value_amount || 0), 0) / 1e6).toFixed(1) + "m", color: "#f5c842", isText: true },
          ].map(({ label, value, color, isText }) => (
            <div key={label} style={{ background: "rgba(255,255,255,0.03)", borderRadius: "10px", border: "1px solid rgba(255,255,255,0.06)", padding: "14px 16px" }}>
              <div style={{ fontSize: "10px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "5px" }}>{label}</div>
              <div style={{ fontSize: isText ? "18px" : "24px", fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>{value}</div>
            </div>
          ))}
        </div>

        {/* Competitor wins chips */}
        {Object.keys(byCompetitor).length > 0 && (
          <div style={{ display: "flex", gap: "8px", marginBottom: "18px", flexWrap: "wrap", alignItems: "center" }}>
            <span style={{ fontSize: "11px", color: "rgba(255,255,255,0.3)", marginRight: "2px" }}>Competitor wins:</span>
            {Object.entries(byCompetitor).sort((a, b) => b[1] - a[1]).map(([name, count]) => {
              const color = COMPETITOR_COLORS[name] || "#aaa";
              return (
                <span key={name} style={{ padding: "4px 10px", borderRadius: "20px", background: `${color}22`, border: `1px solid ${color}55`, color, fontSize: "11px", fontWeight: 600 }}>
                  {name} <span style={{ opacity: 0.6, fontWeight: 400 }}>{count}</span>
                </span>
              );
            })}
          </div>
        )}

        {/* Filters */}
        <div style={{ display: "flex", gap: "10px", marginBottom: "16px", flexWrap: "wrap", alignItems: "center" }}>
          {[
            { label: "Source", value: sourceFilter, set: setSourceFilter, options: ["All", "Find a Tender", "Contracts Finder", "Sell2Wales", "Public Contracts Scotland"] },
            { label: "Scope",  value: scopeFilter,  set: setScopeFilter,  options: ["All", "Service 01: Renewable Energy Opportunity Identification", "Service 02: Energy Feasibility Studies", "Service 03: Energy System Optimisation", "Service 04: Business Case Development"] },
          ].map(({ label, value, set, options }) => (
            <select key={label} value={value} onChange={e => set(e.target.value)}
              style={{ padding: "7px 10px", borderRadius: "7px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: "#f0ede8", fontSize: "12px", outline: "none", cursor: "pointer" }}>
              {options.map(o => (
                <option key={o} value={o} style={{ background: "#1a2030" }}>{o === "All" ? `All ${label}s` : o.split(":")[0]}</option>
              ))}
            </select>
          ))}
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "rgba(255,255,255,0.55)", cursor: "pointer" }}>
            <input type="checkbox" checked={competitorOnly} onChange={e => setCompetitorOnly(e.target.checked)} style={{ accentColor: "#ff6b6b" }} />
            Competitor wins only
          </label>
          <span style={{ fontSize: "11px", color: "rgba(255,255,255,0.3)" }}>{filtered.length} of {awards.length} awards</span>
        </div>

        {/* Awards list + detail panel */}
        <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 380px" : "1fr", gap: "20px", alignItems: "start" }}>
          <div>
            {filtered.length === 0 ? (
              <div style={{ textAlign: "center", padding: "40px", color: "rgba(255,255,255,0.3)", fontSize: "13px" }}>No awards match current filters.</div>
            ) : filtered.map((t, i) => (
              <div key={t.id} className="fade-up" style={{ animationDelay: `${Math.min(i, 20) * 0.02}s` }}>
                <MarketAwardRow
                  tender={t}
                  selected={selected?.id === t.id}
                  onClick={() => setSelected(selected?.id === t.id ? null : t)}
                />
              </div>
            ))}
          </div>
          {selected && <DetailPanel tender={selected} onClose={() => setSelected(null)} />}
        </div>
      </>)}
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────

export default function NordicTenderFinder() {
  const [tenders, setTenders]               = useState([]);
  const [filtered, setFiltered]             = useState([]);
  const [selected, setSelected]             = useState(null);
  const [loading, setLoading]               = useState(true);
  const [error, setError]                   = useState(null);
  const [search, setSearch]                 = useState("");
  const [sourceFilter, setSourceFilter]     = useState("All");
  const [scopeFilter, setScopeFilter]       = useState("All");
  const [categoryFilter, setCategoryFilter] = useState("All Categories");
  const [minScore, setMinScore]             = useState(5);
  const [regionFilter, setRegionFilter]     = useState("");
  const [cpvFilter, setCpvFilter]           = useState("");
  const [lastRefresh, setLastRefresh]       = useState(null);
  const [refreshing, setRefreshing]         = useState(false);
  const [apiHealthy, setApiHealthy]         = useState(true);
  const [totalFromApi, setTotalFromApi]     = useState(0);
  const [downloading, setDownloading]       = useState(false);
  const [downloadError, setDownloadError]   = useState(null);
  const [activeTab, setActiveTab]           = useState("tenders"); // "tenders" | "competitors"

  // ── Fetch from live API ───────────────────────────────────────────────────
  const loadTenders = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/tenders?min_score=0&page_size=10000&sort_by=score&sort_dir=desc`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const data = await res.json();
      const normalised = (data.tenders ?? []).map(normaliseTender);
      setTenders(normalised);
      setTotalFromApi(data.total ?? normalised.length);
      setLastRefresh(new Date());
      setApiHealthy(true);
    } catch (err) {
      setError(err.message || "Unknown error");
      setApiHealthy(false);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // ── Trigger server-side refresh ───────────────────────────────────────────
  const triggerRefresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      await fetch(`${API_BASE}/refresh/sync`, { method: "POST" });
      await loadTenders();
    } catch (err) {
      setError(err.message || "Refresh failed");
      setRefreshing(false);
    }
  }, [loadTenders]);

  // ── CSV download ──────────────────────────────────────────────────────────
  const handleDownload = useCallback(async () => {
    setDownloading(true);
    setDownloadError(null);
    const err = await downloadCsv({
      source:   sourceFilter   !== "All"            ? sourceFilter   : null,
      scope:    scopeFilter    !== "All"            ? scopeFilter    : null,
      category: categoryFilter !== "All Categories" ? categoryFilter : null,
      search:   search.trim() || null,
    });
    if (err) setDownloadError(err);
    setDownloading(false);
  }, [sourceFilter, scopeFilter, categoryFilter, search]);

  useEffect(() => { loadTenders(); }, [loadTenders]);

  // ── Client-side filtering ─────────────────────────────────────────────────
  useEffect(() => {
    let result = [...tenders];
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(t =>
        t.title.toLowerCase().includes(q) ||
        t.authority.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q)
      );
    }
    if (sourceFilter   !== "All")            result = result.filter(t => t.source   === sourceFilter);
    if (scopeFilter    !== "All")            result = result.filter(t => (t.scopes ?? []).includes(scopeFilter));
    if (categoryFilter !== "All Categories") result = result.filter(t => t.category === categoryFilter);
    if (regionFilter.trim()) {
      const prefix = regionFilter.trim().toUpperCase();
      result = result.filter(t => (t.nuts_codes ?? []).some(r => r.startsWith(prefix)));
    }
    if (cpvFilter.trim()) {
      const prefix = cpvFilter.replace(/-/g, "").trim();
      result = result.filter(t => (t.cpv_codes ?? []).some(c => c.replace(/-/g, "").startsWith(prefix)));
    }
    result = result.filter(t => t.score >= minScore);
    setFiltered(result);
  }, [tenders, search, sourceFilter, scopeFilter, categoryFilter, regionFilter, cpvFilter, minScore]);

  const strongMatches  = tenders.filter(t => t.score >= 7).length;
  const likelyRelevant = tenders.filter(t => t.score >= 4 && t.score < 7).length;
  const sourceCounts   = tenders.reduce((acc, t) => { acc[t.source] = (acc[t.source] || 0) + 1; return acc; }, {});

  // ── Loading screen ────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={{ minHeight: "100vh", background: "#0a0f1a", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", fontFamily: "'DM Sans', sans-serif" }}>
        <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500;600;700&display=swap'); @keyframes spin { to { transform: rotate(360deg); } }`}</style>
        <div style={{ width: "40px", height: "40px", border: "2px solid rgba(0,229,160,0.2)", borderTop: "2px solid #00e5a0", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
        <p style={{ color: "rgba(255,255,255,0.4)", marginTop: "16px", fontSize: "13px", letterSpacing: "0.06em" }}>CONNECTING TO TENDER API</p>
        <p style={{ color: "rgba(255,255,255,0.2)", marginTop: "6px", fontSize: "11px", fontFamily: "monospace" }}>{API_BASE}</p>
      </div>
    );
  }

  // ── Main render ───────────────────────────────────────────────────────────
  return (
    <div style={{ minHeight: "100vh", background: "#0a0f1a", fontFamily: "'DM Sans', sans-serif", color: "#f0ede8" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500;600;700&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(0,229,160,0.2); border-radius: 2px; }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        .fade-up { animation: fadeUp 0.4s ease forwards; }
      `}</style>

      {/* ── Header ── */}
      <div style={{ borderBottom: "1px solid rgba(255,255,255,0.06)", padding: "20px 32px", display: "flex", justifyContent: "space-between", alignItems: "center", background: "rgba(10,15,26,0.95)", backdropFilter: "blur(20px)", position: "sticky", top: 0, zIndex: 100 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <img
            src="/logo.png"
            alt="Nordic Energy"
            style={{ height: "40px", width: "auto", objectFit: "contain" }}
          />
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.06em", textTransform: "uppercase" }}>Tender Intelligence</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", display: "flex", alignItems: "center" }}>
            <ApiStatusDot healthy={apiHealthy} />
            {apiHealthy ? "API connected" : "API unreachable"}
          </div>
          {lastRefresh && (
            <span style={{ fontSize: "11px", color: "rgba(255,255,255,0.3)" }}>
              Last scan: {lastRefresh.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <button onClick={triggerRefresh} disabled={refreshing}
            style={{ padding: "7px 14px", borderRadius: "7px", background: "rgba(0,229,160,0.12)", border: "1px solid rgba(0,229,160,0.25)", color: "#00e5a0", fontSize: "12px", fontWeight: 600, cursor: refreshing ? "not-allowed" : "pointer", letterSpacing: "0.04em", opacity: refreshing ? 0.5 : 1 }}>
            {refreshing ? "Scanning…" : "↻ Refresh"}
          </button>
        </div>
      </div>

      <div style={{ maxWidth: "1300px", margin: "0 auto", padding: "28px 32px" }}>

        {error && <ErrorBanner message={error} onRetry={loadTenders} />}

        {/* ── Tab navigation ── */}
        <div style={{ display: "flex", gap: "8px", marginBottom: "24px", borderBottom: "1px solid rgba(255,255,255,0.07)", paddingBottom: "16px" }}>
          {[
            { id: "tenders",     label: "Tenders",              count: tenders.length },
            { id: "competitors", label: "Competitor Activity",   count: tenders.filter(t => t.competitor_win).length },
            { id: "market",      label: "Market Intelligence",   count: null },
          ].map(tab => (
            <button key={tab.id} onClick={() => setActiveTab(tab.id)}
              style={{
                padding: "8px 18px", borderRadius: "8px", cursor: "pointer", fontSize: "13px", fontWeight: 600, border: "none",
                background: activeTab === tab.id ? "rgba(0,229,160,0.12)" : "rgba(255,255,255,0.04)",
                outline: activeTab === tab.id ? "1px solid rgba(0,229,160,0.3)" : "1px solid rgba(255,255,255,0.08)",
                color: activeTab === tab.id ? "#00e5a0" : "rgba(255,255,255,0.45)",
                transition: "all 0.2s",
              }}>
              {tab.label}{tab.count !== null && <span style={{ opacity: 0.65, fontWeight: 400 }}> ({tab.count})</span>}
            </button>
          ))}
        </div>

        {activeTab === "competitors" && <CompetitorTab tenders={tenders} />}

        {activeTab === "market" && <MarketTab />}

        {activeTab === "tenders" && <>

        {/* ── Stats row ── */}
        <div className="fade-up" style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: "12px", marginBottom: "28px" }}>
          {[
            { label: "Total Tenders",   value: tenders.length, color: "#f0ede8" },
            { label: "Strong Matches",  value: strongMatches,  color: "#00e5a0" },
            { label: "Likely Relevant", value: likelyRelevant, color: "#f5c842" },
            { label: "Sources Active",  value: Object.keys(sourceCounts).length, color: "#64a0ff" },
            { label: "NE Eligible",  value: tenders.filter(t => t.nordic_eligible).length, color: "#00e5a0" },
            { label: "Watchlist",    value: tenders.filter(t => t.watchlist_match).length,  color: "#ffc800" },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: "rgba(255,255,255,0.03)", borderRadius: "10px", border: "1px solid rgba(255,255,255,0.06)", padding: "16px 20px" }}>
              <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "6px" }}>{label}</div>
              <div style={{ fontSize: "28px", fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>{value}</div>
            </div>
          ))}
        </div>

        {/* ── Category strip ── */}
        <div className="fade-up">
          <CategoryStrip
            tenders={tenders}
            activeCategory={categoryFilter}
            onSelect={setCategoryFilter}
          />
        </div>

        {/* ── Filters + Download bar ── */}
        <div className="fade-up" style={{ display: "flex", gap: "10px", marginBottom: "20px", flexWrap: "wrap", alignItems: "center" }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search tenders, authorities…"
            style={{ flex: "1", minWidth: "200px", padding: "9px 14px", borderRadius: "8px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: "#f0ede8", fontSize: "13px", outline: "none" }} />

          {[
            { label: "Source", value: sourceFilter, setValue: setSourceFilter, options: ["All", "Find a Tender", "Contracts Finder", "Sell2Wales", "Public Contracts Scotland"] },
            { label: "Scope",  value: scopeFilter,  setValue: setScopeFilter,  options: ["All", "Service 01: Renewable Energy Opportunity Identification", "Service 02: Energy Feasibility Studies", "Service 03: Energy System Optimisation", "Service 04: Business Case Development"] },
          ].map(({ label, value, setValue, options }) => (
            <select key={label} value={value} onChange={e => setValue(e.target.value)}
              style={{ padding: "9px 12px", borderRadius: "8px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: "#f0ede8", fontSize: "13px", outline: "none", cursor: "pointer" }}>
              {options.map(o => (
                <option key={o} value={o} style={{ background: "#1a2030" }}>
                  {o === "All" ? `All ${label}s` : o.split(" / ")[0]}
                </option>
              ))}
            </select>
          ))}

          <input
            value={regionFilter}
            onChange={e => setRegionFilter(e.target.value)}
            placeholder="Region (e.g. UKE)"
            title="Filter by NUTS delivery region prefix. UKE = Yorkshire, UKD = North West, UKH = East of England, UKI = London, UKJ = South East"
            style={{ width: "130px", padding: "9px 12px", borderRadius: "8px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: "#f0ede8", fontSize: "13px", outline: "none" }}
          />
          <input
            value={cpvFilter}
            onChange={e => setCpvFilter(e.target.value)}
            placeholder="CPV (e.g. 71314)"
            title="Filter by CPV code prefix. E.g. 71314 matches all energy services codes (71314000, 71314100…)"
            style={{ width: "140px", padding: "9px 12px", borderRadius: "8px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: "#f0ede8", fontSize: "13px", outline: "none", fontFamily: "monospace" }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ fontSize: "11px", color: "rgba(255,255,255,0.4)", whiteSpace: "nowrap" }}>
              Min score: <strong style={{ color: "#f0ede8" }}>{minScore}</strong>
            </span>
            <input type="range" min="0" max="9" value={minScore} onChange={e => setMinScore(Number(e.target.value))}
              style={{ width: "80px", accentColor: "#00e5a0" }} />
          </div>

          <span style={{ fontSize: "12px", color: "rgba(255,255,255,0.35)", whiteSpace: "nowrap" }}>
            {filtered.length} result{filtered.length !== 1 ? "s" : ""} of {tenders.length} tenders
          </span>

          <button onClick={handleDownload} disabled={downloading || !apiHealthy}
            title="Download all tenders as CSV. Active filters applied."
            style={{ display: "flex", alignItems: "center", gap: "6px", padding: "8px 14px", borderRadius: "7px", background: downloading ? "rgba(255,255,255,0.04)" : "rgba(245,200,66,0.12)", border: `1px solid ${downloading ? "rgba(255,255,255,0.1)" : "rgba(245,200,66,0.3)"}`, color: downloading ? "rgba(255,255,255,0.3)" : "#f5c842", fontSize: "12px", fontWeight: 600, cursor: downloading || !apiHealthy ? "not-allowed" : "pointer", letterSpacing: "0.04em", whiteSpace: "nowrap", transition: "all 0.2s" }}>
            <span style={{ fontSize: "14px" }}>{downloading ? "⏳" : "⬇"}</span>
            {downloading ? "Downloading…" : "Export CSV"}
          </button>
        </div>

        {downloadError && (
          <div style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "8px", background: "rgba(198,40,40,0.1)", border: "1px solid rgba(198,40,40,0.3)", fontSize: "12px", color: "#ef9a9a" }}>
            ⚠ CSV download failed: {downloadError}
          </div>
        )}

        {/* ── CSV info banner ── */}
        <div style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "8px", background: "rgba(245,200,66,0.06)", border: "1px solid rgba(245,200,66,0.15)", display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ fontSize: "13px" }}>📋</span>
          <span style={{ fontSize: "11px", color: "rgba(255,255,255,0.45)", lineHeight: "1.5" }}>
            <strong style={{ color: "#f5c842" }}>Export CSV</strong> downloads <strong style={{ color: "rgba(255,255,255,0.7)" }}>all {tenders.length} tenders</strong> including out-of-scope ones.
            Active source, scope, category and search filters are applied. Min score slider is ignored.
          </span>
        </div>

        {/* Source legend */}
        <div style={{ display: "flex", gap: "16px", marginBottom: "16px", flexWrap: "wrap" }}>
          {[
            { code: "FaT", label: `Find a Tender (${sourceCounts["Find a Tender"] ?? 0})`,       color: "#b48ef5" },
            { code: "CF",  label: `Contracts Finder (${sourceCounts["Contracts Finder"] ?? 0})`, color: "#5babff" },
            { code: "S2W", label: `Sell2Wales (${sourceCounts["Sell2Wales"] ?? 0})`,              color: "#ff7070" },
            { code: "PCS", label: `Public Contracts Scotland (${sourceCounts["Public Contracts Scotland"] ?? 0})`, color: "#00c878" },
          ].map(({ code, label, color }) => (
            <div key={code} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "rgba(255,255,255,0.45)" }}>
              <span style={{ padding: "1px 6px", borderRadius: "4px", background: `${color}22`, border: `1px solid ${color}55`, color, fontWeight: 700, fontSize: "10px" }}>{code}</span>
              {label}
            </div>
          ))}
        </div>

        {/* ── Main grid ── */}
        <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 380px" : "1fr", gap: "20px", alignItems: "start" }}>
          <div>
            {filtered.length === 0 ? (
              <div style={{ textAlign: "center", padding: "60px 20px", color: "rgba(255,255,255,0.3)" }}>
                <div style={{ fontSize: "32px", marginBottom: "12px" }}>🔍</div>
                <div style={{ fontSize: "14px" }}>
                  {tenders.length === 0 && !error
                    ? "No tenders in cache — click ↻ Refresh to fetch live data"
                    : "No tenders match your current filters"}
                </div>
              </div>
            ) : (
              filtered.map((t, i) => (
                <div key={t.id} className="fade-up" style={{ animationDelay: `${i * 0.03}s` }}>
                  <TenderCard tender={t} onClick={setSelected} selected={selected?.id === t.id} />
                </div>
              ))
            )}
          </div>
          {selected && <DetailPanel tender={selected} onClose={() => setSelected(null)} />}
        </div>

        </> /* end activeTab === "tenders" */ }

        {/* ── Footer ── */}
        <div style={{ marginTop: "40px", paddingTop: "20px", borderTop: "1px solid rgba(255,255,255,0.06)", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
          <p style={{ fontSize: "11px", color: "rgba(255,255,255,0.25)", lineHeight: "1.6" }}>
            Scores computed server-side against Nordic Energy's scope: Renewables · Heat Networks · Advisory.<br />
            Data from Find a Tender, Contracts Finder, Sell2Wales and Public Contracts Scotland (OCDS) via FastAPI at <span style={{ fontFamily: "monospace" }}>{API_BASE}</span>.
          </p>
          <div style={{ display: "flex", gap: "12px" }}>
            <a href="https://www.find-tender.service.gov.uk" target="_blank" rel="noopener noreferrer" style={{ fontSize: "11px", color: "rgba(138,99,210,0.6)", textDecoration: "none" }}>Find a Tender ↗</a>
            <a href="https://www.contractsfinder.service.gov.uk" target="_blank" rel="noopener noreferrer" style={{ fontSize: "11px", color: "rgba(30,144,255,0.6)", textDecoration: "none" }}>Contracts Finder ↗</a>
            <a href="https://www.sell2wales.gov.wales" target="_blank" rel="noopener noreferrer" style={{ fontSize: "11px", color: "rgba(220,50,50,0.6)", textDecoration: "none" }}>Sell2Wales ↗</a>
            <a href="https://www.publiccontractsscotland.gov.uk" target="_blank" rel="noopener noreferrer" style={{ fontSize: "11px", color: "rgba(0,180,120,0.6)", textDecoration: "none" }}>Public Contracts Scotland ↗</a>
          </div>
        </div>
      </div>
    </div>
  );
}
