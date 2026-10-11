import type { Case, ChatSegment, Citation, Dataset, Market, Powertrain, Signal } from "@/contracts";
import { marketLabel } from "@/contracts";
import { cite, type ProposalDraft } from "./chat";
import { liveChatEnabled, liveJson } from "./live";

/**
 * "Ask the segment": the PM asks what one customer segment says about a case.
 * Answers come only from real BMW verbatims of that segment (no synthetic
 * signals, no web findings, no simulated customers). With no verbatims the
 * assistant says so and proposes collecting evidence instead of guessing.
 */

const MIN_POWERTRAIN = 3;
const MAX_SEGMENTS = 6;

function verbatims(d: Dataset, c: Case): Signal[] {
  return c.signalIds
    .map((id) => d.signals.find((s) => s.id === id))
    .filter((s): s is Signal => !!s && s.agent === "bmw_data" && s.sourceType === "bmw_feedback" && !s.synthetic);
}

const keyOf = (market: Market, powertrain: Powertrain | null) => (powertrain ? `${market}-${powertrain}` : market);
const labelOf = (market: Market, powertrain: Powertrain | null) => `${marketLabel[market]}${powertrain ? ` ${powertrain}` : ""} owners`;

export function caseSegments(d: Dataset, c: Case): ChatSegment[] {
  const vs = verbatims(d, c);
  const markets = [...new Set<Market>([...c.markets, ...vs.map((s) => s.market)])].filter((m) => m !== "unknown");
  const byMarket = markets
    .map((market) => ({ market, list: vs.filter((s) => s.market === market) }))
    .sort((a, b) => b.list.length - a.list.length);
  const out: ChatSegment[] = [];
  for (const { market, list } of byMarket) {
    out.push({ key: keyOf(market, null), label: labelOf(market, null), market, powertrain: null, verbatims: list.length });
    const pts = [...new Set(list.map((s) => s.powertrain))].filter((p) => p !== "unknown");
    for (const powertrain of pts) {
      const n = list.filter((s) => s.powertrain === powertrain).length;
      // A powertrain chip only helps when it narrows the market and still has enough voices.
      if (n >= MIN_POWERTRAIN && n < list.length) out.push({ key: keyOf(market, powertrain), label: labelOf(market, powertrain), market, powertrain, verbatims: n });
    }
  }
  const withData = out.filter((s) => s.verbatims > 0).slice(0, MAX_SEGMENTS);
  const empty = out.filter((s) => s.verbatims === 0);
  return [...withData, ...empty];
}

export interface SegmentReply {
  text: string;
  citations: Citation[];
  proposals: ProposalDraft[];
}

function strongest(list: Signal[]) {
  const rank = (s: Signal) => ({ complaint: 0, wish: 1, like: 2 })[s.kind as "complaint"] ?? 3;
  const sev = (s: Signal) => ({ defect: 0, difficult_to_use: 0.2, wish: 0.4, none: 0.8 })[s.severity] ?? 1;
  const sorted = [...list].sort((a, b) => rank(a) - rank(b) || sev(a) - sev(b));
  const negatives = sorted.filter((s) => s.kind !== "like").slice(0, 3);
  const likes = sorted.filter((s) => s.kind === "like").slice(0, negatives.length ? 1 : 3);
  return [...negatives, ...likes];
}

const quoteOf = (s: Signal) => {
  const q = (s.quoteEn ?? s.quote).replace(/\s+/g, " ").trim();
  return q.length > 220 ? `${q.slice(0, 217)}…` : q;
};

const SYSTEM = `You summarize what one customer segment says about a BMW product problem, for the product manager.
Rules:
- Use only the verbatims given. Each is a real BMW customer comment with an ID in square brackets.
- Write in the third person ("US owners say..."). Never speak as a customer, never invent quotes, customers, numbers or IDs.
- Cite every claim with the verbatim ID, e.g. [G60-1430].
- Name complaints, wishes and likes separately when present. If the voices disagree, say so.
- Max ~150 words, short bullets.`;

export async function segmentReply(d: Dataset, c: Case, objectType: "case" | "requirement", objectId: string, key: string): Promise<SegmentReply | null> {
  const segments = caseSegments(d, c);
  const seg = segments.find((s) => s.key === key);
  if (!seg) return null;
  const list = verbatims(d, c).filter((s) => s.market === seg.market && (!seg.powertrain || s.powertrain === seg.powertrain));
  const scope = [marketLabel[seg.market], seg.powertrain].filter(Boolean).join(" · ");

  if (!list.length) {
    const closest = segments.find((s) => s.verbatims > 0);
    return {
      text: [
        `**No evidence for ${seg.label}** in the BMW data for this case: 0 verbatims.`,
        "",
        `I won't guess what they think. ${closest ? `The closest segment with data is **${closest.label}** (${closest.verbatims} verbatims), but their view may not transfer.` : "No segment of this case has verbatims yet."}`,
        "",
        "I propose an open question to collect evidence first.",
      ].join("\n"),
      citations: [],
      proposals: [
        {
          objectType,
          objectId,
          source: "chat",
          kind: "new_question",
          summary: `Collect evidence from ${seg.label}`,
          changes: [
            {
              field: "question",
              before: null,
              after: {
                addressee: "market_pm",
                question: `What do ${seg.label} say about "${c.featureName}"? Please share dealer feedback, local reviews or study results.`,
                why: `The case has no ${scope} verbatims, so the segment cannot be assessed from BMW data.`,
              },
            },
          ],
          citations: [],
        },
      ],
    };
  }

  const complaints = list.filter((s) => s.kind === "complaint").length;
  const wishes = list.filter((s) => s.kind === "wish").length;
  const likes = list.filter((s) => s.kind === "like").length;
  const header = `_Grounded in ${list.length} real verbatim${list.length === 1 ? "" : "s"} (${scope})._`;
  const counts = [complaints && `${complaints} complaint${complaints === 1 ? "" : "s"}`, wishes && `${wishes} wish${wishes === 1 ? "" : "es"}`, likes && `${likes} like${likes === 1 ? "" : "s"}`]
    .filter(Boolean)
    .join(", ");
  const ids = new Set(list.map((s) => s.id));

  if (liveChatEnabled()) {
    const block = list
      .slice(0, 60)
      .map((s) => `[${s.id}] (${s.kind}, ${s.powertrain}) ${quoteOf(s)}`)
      .join("\n");
    const live = await liveJson(SYSTEM, `Case: ${c.title}\nSegment: ${seg.label} (${counts})\nVERBATIMS:\n${block}`);
    if (live) {
      const clean = live.replace(/\[([A-Z0-9-]+)\]/g, (m, id: string) => (ids.has(id) ? m : ""));
      const cited = [...new Set([...clean.matchAll(/\[([A-Z0-9-]+)\]/g)].map((m) => m[1]))];
      return { text: `${header}\n\n${clean}`, citations: cited.map((id) => cite(list.find((s) => s.id === id)!)), proposals: [] };
    }
  }

  const top = strongest(list);
  const kindWord = { complaint: "Complaint", wish: "Wish", like: "Like" } as Record<string, string>;
  return {
    text: [
      header,
      "",
      `**${seg.label}** on "${c.featureName}": ${counts}.`,
      "",
      ...top.map((s) => `- ${kindWord[s.kind] ?? "Comment"}: "${quoteOf(s)}" [${s.id}]`),
      "",
      list.length > top.length ? `_${list.length - top.length} more in the evidence diagram._` : "",
    ]
      .filter((l, i, a) => l !== "" || a[i - 1] !== "")
      .join("\n")
      .trim(),
    citations: top.map(cite),
    proposals: [],
  };
}
