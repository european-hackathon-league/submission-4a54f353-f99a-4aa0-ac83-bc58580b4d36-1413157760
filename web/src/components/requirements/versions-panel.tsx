"use client";

import { useState } from "react";
import { diffWords } from "diff";
import { addresseeLabel, feedbackResponseLabel, releaseLabel, type FeedbackEntry, type RequirementVersion } from "@/contracts";
import { fromNow } from "@/lib/format";
import { Pill } from "@/components/common/badges";
import { useUsers } from "@/components/common/people";
import { ProvenanceLegend } from "@/components/requirements/provenance";
import { cn } from "@/lib/utils";

const causeLabel: Record<RequirementVersion["cause"], string> = { draft: "First draft", feedback: "After feedback", brief_change: "Brief changed", edit: "Edited by the PM", ripple: "After a competitor fact" };

/** Version history of one requirement: what changed, why, and the feedback that caused it. */
export function VersionsPanel({ versions, feedback }: { versions: RequirementVersion[]; feedback: FeedbackEntry[] }) {
  const { name } = useUsers();
  const [open, setOpen] = useState<number | null>(versions[0]?.version ?? null);
  return (
    <div className="space-y-2">
      {versions.map((v, i) => {
        const prev = versions[i + 1];
        const fb = feedback.find((f) => f.id === v.feedbackId);
        const changes = prev ? summary(prev, v) : [];
        return (
          <div key={v.version} className="rounded-lg border bg-card text-sm">
            <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left" onClick={() => setOpen(open === v.version ? null : v.version)}>
              <Pill t={i === 0 ? "blue" : "slate"}>v{v.version}</Pill>
              <span className="font-medium">{causeLabel[v.cause]}</span>
              <span className="truncate text-muted-foreground">{changes.join(", ") || v.rationale}</span>
              <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                {name(v.createdBy)} · {fromNow(v.createdAt)}
              </span>
            </button>
            {open === v.version && (
              <div className="space-y-2 border-t px-3 py-2">
                {fb && (
                  <div className="rounded-md bg-muted/50 p-2 text-xs">
                    <b>{addresseeLabel[fb.from]}:</b> &ldquo;{fb.text}&rdquo;
                    <br />
                    <b>You {feedbackResponseLabel[fb.response].toLowerCase()}:</b> {fb.pmRationale}
                  </div>
                )}
                <p className="text-xs text-muted-foreground">Reason: {v.rationale}</p>
                <div className="text-sm font-medium">{v.snapshot.title}</div>
                {prev ? (
                  <p className="text-xs leading-relaxed">
                    {diffWords(prev.snapshot.description, v.snapshot.description).map((p, k) => (
                      <span key={k} className={cn(p.added && "bg-emerald-100 dark:bg-emerald-900/40", p.removed && "bg-red-100 line-through dark:bg-red-900/40")}>
                        {p.value}
                      </span>
                    ))}
                  </p>
                ) : (
                  <p className="text-xs leading-relaxed text-muted-foreground">{v.snapshot.description}</p>
                )}
                <ProvenanceLegend r={v.snapshot} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function summary(a: RequirementVersion, b: RequirementVersion): string[] {
  const out: string[] = [];
  const ta = a.snapshot.metrics[0]?.target;
  const tb = b.snapshot.metrics[0]?.target;
  if (ta !== tb) out.push(`target ${ta} → ${tb}`);
  if (a.snapshot.releaseTarget !== b.snapshot.releaseTarget) out.push(`release ${releaseLabel[a.snapshot.releaseTarget]} → ${releaseLabel[b.snapshot.releaseTarget]}`);
  if (a.snapshot.cost.size !== b.snapshot.cost.size) out.push(`cost ${a.snapshot.cost.size} → ${b.snapshot.cost.size}`);
  if (a.snapshot.marketScope !== b.snapshot.marketScope) out.push(`scope ${a.snapshot.marketScope} → ${b.snapshot.marketScope}`);
  return out;
}
