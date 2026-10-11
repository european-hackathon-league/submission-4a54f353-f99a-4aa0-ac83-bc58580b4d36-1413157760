"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Bot, CalendarCheck, Check, ChevronLeft, ChevronRight, Download, Pause, Play, PlayCircle, ShieldCheck, ShieldX, X } from "lucide-react";
import { FactorKey as FactorKeys, factorLabel, marketLabel, releaseLabel, type ReplayEvent, type ReplayResponse, type ScoreSnapshot } from "@/contracts";
import { useApi } from "@/lib/api/hooks";
import { caseSankey, sayLabel, topicLabels } from "@/lib/sankey";
import { downloadText, renderDossier } from "@/lib/dossier";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Pill, ReqStatusBadge } from "@/components/common/badges";
import { UserAvatar, useUsers } from "@/components/common/people";
import { SankeyChart } from "@/components/sankey/sankey-chart";
import { Button } from "@/components/ui/button";
import { BEATS, useReplayClock, type BeatId } from "./use-replay-clock";

/** Opens the trace replay of a requirement in a full-screen overlay. */
export function ReplayButton({ requirementId, size = "sm" }: { requirementId: string; size?: "sm" | "xs" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size={size} variant="outline" onClick={() => setOpen(true)} title="Play the whole life of this requirement, from the first quote to the plan (about 15 s)">
        <PlayCircle className="size-3.5" /> Replay
      </Button>
      {open && createPortal(<ReplayOverlay id={requirementId} onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

const REVEAL: Record<BeatId, number> = { opening: 1, rewind: 0, voices: 0.3, signals: 0.74, score: 1, decisions: 1, planned: 1, chain: 1 };

function ReplayOverlay({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useApi("requirementReplay", { params: { id } });
  const taxonomy = useApi("taxonomy");
  const clock = useReplayClock();
  const [scrub, setScrub] = useState<number | null>(null);
  const { toggle, step, setPlaying } = clock;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === " ") {
        e.preventDefault();
        toggle();
      } else if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
    };
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [onClose, toggle, step]);

  return (
    <div role="dialog" aria-modal="true" aria-label={`Trace replay of ${id}`} className="fixed inset-0 z-[100] flex flex-col bg-background">
      {q.data ? (
        <Stage x={q.data} graphTopics={topicLabels(taxonomy.data)} clock={clock} scrub={scrub} setScrub={(i) => (setPlaying(false), setScrub(i))} onClose={onClose} />
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">{q.error ? `Could not load the replay: ${q.error.message}` : "Loading the chain…"}</div>
      )}
    </div>
  );
}

type Clock = ReturnType<typeof useReplayClock>;

function Stage({ x, graphTopics, clock, scrub, setScrub, onClose }: { x: ReplayResponse; graphTopics: Map<string, string>; clock: Clock; scrub: number | null; setScrub: (i: number | null) => void; onClose: () => void }) {
  const { name } = useUsers();
  const r = x.requirement;
  const graph = useMemo(() => caseSankey(x.case, x.signals, graphTopics), [x.case, x.signals, graphTopics]);
  const beat = clock.beatId;
  const p = clock.progress;
  const motion = !clock.reduced;
  const revealMs = !motion ? 0 : beat === "rewind" ? 1000 / clock.speed : beat === "opening" ? 0 : 900 / clock.speed;
  const highlight =
    beat === "voices" ? graph.nodes.filter((n) => n.column === 0).map((n) => n.id) : beat === "signals" ? graph.nodes.filter((n) => n.column === 1).map((n) => n.id) : beat === "score" ? graph.nodes.filter((n) => n.hero).map((n) => n.id) : undefined;

  // How far the timeline has been played at each beat (the static base shows all events, played ones solid).
  const firstCase = Math.max(0, x.events.findIndex((e) => e.objectType === "case"));
  const firstScore = x.events.findIndex((e) => e.scoreSnapshot);
  const played =
    beat === "rewind"
      ? 0
      : beat === "voices" || beat === "signals"
        ? firstCase
        : beat === "score"
          ? (firstScore >= 0 ? firstScore : firstCase) + 1
          : beat === "decisions"
            ? Math.round(((firstScore >= 0 ? firstScore : firstCase) + 1) + p * (x.events.length - (firstScore >= 0 ? firstScore : firstCase) - 1))
            : x.events.length;
  const at = scrub ?? Math.max(0, played - 1);
  const intact = x.integrity.every((c) => c.ok);

  return (
    <>
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">
            Trace replay · <span className="font-mono">{r.id}</span>
          </p>
          <h2 className="truncate text-sm font-semibold" title={r.title}>
            {r.title}
          </h2>
        </div>
        <nav aria-label="Beats" className="hidden items-center gap-1 lg:flex">
          {BEATS.filter((b) => b.id !== "rewind").map((b) => {
            const i = BEATS.findIndex((x) => x.id === b.id);
            return (
              <button
                key={b.id}
                onClick={() => (clock.setPlaying(false), clock.goTo(i), setScrub(null))}
                className={cn("rounded-full px-2.5 py-1 text-xs transition", i === clock.beat ? "bg-foreground text-background" : i < clock.beat ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
              >
                {b.label}
              </button>
            );
          })}
        </nav>
        <div className="flex items-center gap-1">
          <Button size="icon-sm" variant="ghost" onClick={() => clock.step(-1)} aria-label="Previous beat (←)">
            <ChevronLeft className="size-4" />
          </Button>
          <Button size="sm" onClick={() => (setScrub(null), clock.toggle())} aria-label={clock.playing ? "Pause (Space)" : "Play (Space)"}>
            {clock.playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />} {clock.playing ? "Pause" : clock.beat === BEATS.length - 1 ? "Replay" : "Play"}
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={() => clock.step(1)} aria-label="Next beat (→)">
            <ChevronRight className="size-4" />
          </Button>
          <Button size="sm" variant="outline" onClick={() => clock.setSpeed(clock.speed === 1 ? 2 : 1)} aria-label="Speed">
            {clock.speed}×
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={onClose} aria-label="Close (Esc)">
            <X className="size-4" />
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto p-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <section className="relative min-w-0">
          <SankeyChart graph={graph} heroValue={x.counts.customers} reveal={REVEAL[beat]} revealMs={revealMs} highlight={highlight} />
          {beat === "opening" && (
            <div className="absolute inset-0 flex items-center justify-center bg-background/70 backdrop-blur-[1px]">
              <RequirementCard x={x} owner={name(r.ownerId)} />
            </div>
          )}
          {beat === "rewind" && <p className="absolute top-0 right-0 animate-pulse text-xs text-muted-foreground motion-reduce:animate-none">Rewinding to the first quote…</p>}
        </section>
        <aside className="min-w-0 space-y-3">
          <BeatPanel x={x} beat={beat} progress={p} graph={graph} intact={intact} name={name} />
        </aside>
      </div>

      <footer className="border-t px-4 py-3">
        <Timeline events={x.events} played={scrub !== null ? scrub + 1 : played} motion={motion} />
        <Scrubber x={x} at={at} onChange={setScrub} />
      </footer>
    </>
  );
}

function RequirementCard({ x, owner }: { x: ReplayResponse; owner: string }) {
  const r = x.requirement;
  const m = r.metrics[0];
  return (
    <div className="w-full max-w-md rounded-2xl border bg-card p-5 shadow-lg">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-muted-foreground">{r.id}</span>
        <ReqStatusBadge status={r.status} />
        <Pill>v{r.version}</Pill>
        <Pill>{releaseLabel[r.releaseTarget]}</Pill>
      </div>
      <p className="mt-3 text-base leading-snug font-semibold">{r.title}</p>
      {m && (
        <p className="mt-2 text-sm">
          <span className="text-muted-foreground">{m.question}: </span>
          <span className="font-semibold">{m.target}</span>
        </p>
      )}
      <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        <UserAvatar id={r.ownerId} size="xs" /> {owner} · {x.events.length} steps since the first record
      </p>
    </div>
  );
}

function BeatPanel({ x, beat, progress, graph, intact, name }: { x: ReplayResponse; beat: BeatId; progress: number; graph: ReturnType<typeof caseSankey>; intact: boolean; name: (id: string) => string }) {
  const quotes = x.quoteIds.map((id) => x.signals.find((s) => s.id === id)!).filter(Boolean);
  const snap = x.events.find((e) => e.scoreSnapshot && e.objectType === "case")?.scoreSnapshot ?? x.events.find((e) => e.scoreSnapshot)?.scoreSnapshot ?? null;
  switch (beat) {
    case "opening":
      return <Panel title="Where it ended up">This is the requirement today. The replay rewinds to the first customer quote and plays its whole life forward, from the stored rows and the audit log.</Panel>;
    case "rewind":
      return <Panel title="Rewinding">Back to the first record…</Panel>;
    case "voices": {
      const shown = Math.max(1, Math.ceil(progress * quotes.length));
      return (
        <Panel title="1 · Voices" lead={`${Math.round(progress * x.counts.customers)} customers · ${x.counts.markets.length} markets · ${x.counts.sources} sources`}>
          <p className="mb-2 text-xs text-muted-foreground">
            Sample of {quotes.length}, all {x.counts.customers} counted. Real BMW records first; web records are synthetic in the demo.
          </p>
          <ul className="space-y-1.5">
            {quotes.slice(0, shown).map((s) => (
              <li key={s.id} className="animate-in fade-in slide-in-from-left-1 rounded-md border bg-card px-2.5 py-1.5 text-xs duration-300 motion-reduce:animate-none">
                <p className="line-clamp-2">“{s.quoteEn ?? s.quote}”</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {marketLabel[s.market]} · {s.sourceName} · <span className="font-mono">{s.id}</span>
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      );
    }
    case "signals": {
      const mids = graph.nodes.filter((n) => n.column === 1);
      return (
        <Panel title="2 · Signals" lead="Each quote is classified onto the fixed taxonomy">
          <ul className="space-y-1.5">
            {mids.slice(0, Math.max(1, Math.ceil(progress * mids.length))).map((n) => {
              const sigs = n.signalIds.map((id) => x.signals.find((s) => s.id === id)!).filter(Boolean);
              const bmw = sigs.filter((s) => s.classification?.method === "bmw_label");
              const label = mostCommon(bmw.map((s) => s.classification!.label ?? ""));
              const model = sigs.find((s) => s.classification?.method === "llm")?.classification;
              return (
                <li key={n.id} className="rounded-md border bg-card px-2.5 py-1.5 text-xs">
                  <p className="font-medium">
                    {n.label}
                    {n.say && <span className="font-normal text-muted-foreground"> · {sayLabel[n.say]}</span>} <span className="text-muted-foreground">({n.value})</span>
                  </p>
                  {bmw.length > 0 && <p className="text-[11px] text-muted-foreground">BMW label (lookup): “{label}”{bmw.length < sigs.length ? ` · ${bmw.length} of ${sigs.length}` : ""}</p>}
                  {model && (
                    <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                      <Bot className="size-3" /> {model.model} · {model.promptVersion}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </Panel>
      );
    }
    case "score":
      return snap ? <ScoreBuild snap={snap} today={x.scoreToday} progress={progress} /> : <Panel title="3 · Case and score">No score was recorded at a decision for this case.</Panel>;
    case "decisions": {
      const human = x.events.filter((e) => e.actorType === "human").length;
      return (
        <Panel title="4 · Decisions" lead={`${human} human decisions · ${x.events.length - human} agent steps`}>
          <p className="text-xs text-muted-foreground">Solid cards are people, with their quoted reason. Dashed cards are agents: they propose and flag, they never decide.</p>
          {x.alerts.length > 0 && (
            <ul className="mt-2 space-y-1.5">
              {x.alerts.map((a) => (
                <li key={a.id} className="flex gap-2 rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-xs dark:border-amber-800 dark:bg-amber-950/40">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
                  <span>
                    <span className="font-medium">{a.title}</span>
                    {a.resolution && <span className="block text-muted-foreground">{a.resolution}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      );
    }
    case "planned":
      return (
        <Panel title="5 · Planned">
          {x.plannedFeature ? (
            <div className="animate-in fade-in slide-in-from-top-3 rounded-lg border bg-card p-3 text-sm duration-500 motion-reduce:animate-none">
              <p className="flex items-center gap-1.5 font-medium">
                <CalendarCheck className="size-4 text-positive" /> {x.plannedFeature.name}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Vehicle plan {x.requirement.series} · {releaseLabel[x.plannedFeature.release]} · today: {x.plannedFeature.todayDetail}
              </p>
              <p className="mt-2 text-xs">
                <span className="font-mono">{x.requirement.id}</span> lands in this slot{x.plannedFeature.requirementIds.length > 1 ? ` with ${x.plannedFeature.requirementIds.filter((i) => i !== x.requirement.id).join(", ")}` : ""}.
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Not in the vehicle plan yet (status {x.requirement.status}).</p>
          )}
        </Panel>
      );
    case "chain":
      return <ChainBadge x={x} intact={intact} name={name} />;
  }
}

function Panel({ title, lead, children }: { title: string; lead?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-card/50 p-3">
      <p className="text-sm font-semibold">{title}</p>
      {lead && <p className="mb-2 text-xs font-medium tabular-nums text-primary">{lead}</p>}
      <div className="text-sm text-muted-foreground">{children}</div>
    </div>
  );
}

function ScoreBuild({ snap, today, progress }: { snap: ScoreSnapshot; today: ReplayResponse["scoreToday"]; progress: number }) {
  const factors = FactorKeys.options.filter((f) => (snap.weights[f] ?? 0) > 0).sort((a, b) => (snap.contributions[b] ?? 0) - (snap.contributions[a] ?? 0));
  const shown = Math.max(1, Math.ceil(progress * factors.length));
  const max = Math.max(...factors.map((f) => snap.contributions[f] ?? 0), 0.001);
  const built = factors.slice(0, shown).reduce((s, f) => s + (snap.contributions[f] ?? 0), 0);
  return (
    <Panel title="3 · Case and score" lead={`${built.toFixed(3)} on ${fmtDate(snap.at)} · today ${today.score.toFixed(3)}`}>
      <p className="mb-2 text-xs">
        Under <span className="font-medium text-foreground">{snap.profileName} v{snap.profileVersion}</span>, the profile active on the decision date. Rank {snap.rank} of {snap.of} then; {today.rank} of {today.of} today.
      </p>
      <ul className="space-y-1">
        {factors.map((f, i) => (
          <li key={f} className={cn("grid grid-cols-[110px_1fr_44px] items-center gap-2 text-xs transition-opacity duration-300", i >= shown && "opacity-0")}>
            <span className="truncate" title={`weight ${snap.weights[f]} × factor ${snap.factors[f]}`}>
              {factorLabel[f]}
            </span>
            <span className="h-2 rounded-full bg-muted">
              <span className="block h-2 rounded-full bg-primary transition-[width] duration-500 motion-reduce:transition-none" style={{ width: i < shown ? `${((snap.contributions[f] ?? 0) / max) * 100}%` : 0 }} />
            </span>
            <span className="text-right tabular-nums">+{(snap.contributions[f] ?? 0).toFixed(3)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px]">Contribution = weight × factor ÷ Σ weights. Hover a factor for its weight and value.</p>
    </Panel>
  );
}

function ChainBadge({ x, intact, name }: { x: ReplayResponse; intact: boolean; name: (id: string) => string }) {
  return (
    <div className={cn("rounded-xl border p-3", intact ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-800 dark:bg-emerald-950/30" : "border-red-300 bg-red-50/60 dark:border-red-800 dark:bg-red-950/30")}>
      <p className={cn("flex items-center gap-2 text-base font-semibold", intact ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300")}>
        {intact ? <ShieldCheck className="size-5" /> : <ShieldX className="size-5" />} {intact ? "Chain intact" : "Chain broken"}
      </p>
      <ul className="mt-2 space-y-1.5">
        {x.integrity.map((c) => (
          <li key={c.id} className="text-xs">
            <p className={cn("flex items-start gap-1.5 font-medium", c.ok ? "text-foreground" : "text-red-700 dark:text-red-300")}>
              {c.ok ? <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" /> : <X className="mt-0.5 size-3.5 shrink-0" />} {c.label}
            </p>
            <p className="pl-5 text-muted-foreground">{c.detail}</p>
            {c.failures.map((f) => (
              <p key={f} className="pl-5 text-red-700 dark:text-red-300">
                {f}
              </p>
            ))}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-muted-foreground">Recomputed now from the stored records and the append-only log ({fmtDate(x.generatedAt)}).</p>
      <Button size="sm" variant="outline" className="mt-2" onClick={() => downloadText(`audit-chain_${x.requirement.id}.md`, renderDossier(x, name))}>
        <Download className="size-3.5" /> Export dossier
      </Button>
    </div>
  );
}

function Timeline({ events, played, motion }: { events: ReplayEvent[]; played: number; motion: boolean }) {
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = ref.current?.children[Math.max(0, played - 1)] as HTMLElement | undefined;
    el?.scrollIntoView({ inline: "center", block: "nearest", behavior: motion ? "smooth" : "auto" });
  }, [played, motion]);
  return (
    <ol ref={ref} aria-label="Decisions timeline" className="flex gap-2 overflow-x-auto pb-2">
      {events.map((e, i) => {
        const on = i < played;
        const agent = e.actorType === "agent";
        return (
          <li
            key={e.seq}
            className={cn(
              "w-52 shrink-0 rounded-lg border px-2.5 py-2 text-xs transition-all",
              motion ? "duration-500" : "duration-0",
              agent ? "border-dashed bg-muted/30" : "border-solid bg-card shadow-xs",
              on ? "opacity-100" : "translate-y-1 opacity-30",
              e.action.startsWith("ripple.") || e.action === "alert.raised" ? "border-amber-400" : "",
            )}
          >
            <p className="flex items-center gap-1.5">
              {agent ? <Bot className="size-3.5 shrink-0 text-muted-foreground" /> : <UserAvatar id={e.actorId} size="xs" />}
              <span className="truncate font-medium">{e.label}</span>
            </p>
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
              {fmtDate(e.at)} · {e.actorName}
              {e.version ? ` · v${e.version}` : ""}
            </p>
            {e.rationale && <p className={cn("mt-1 line-clamp-2 text-[11px]", agent ? "text-muted-foreground" : "italic")}>{agent ? e.rationale : `“${e.rationale}”`}</p>}
            {e.scoreSnapshot && <p className="mt-1 text-[11px] tabular-nums text-primary">score {e.scoreSnapshot.score.toFixed(3)} · {e.scoreSnapshot.profileName} v{e.scoreSnapshot.profileVersion}</p>}
          </li>
        );
      })}
    </ol>
  );
}

function Scrubber({ x, at, onChange }: { x: ReplayResponse; at: number; onChange: (i: number | null) => void }) {
  const e = x.events[at];
  if (!e) return null;
  const version = [...x.events.slice(0, at + 1)].reverse().find((v) => v.version)?.version ?? null;
  const snap = [...x.events.slice(0, at + 1)].reverse().find((v) => v.scoreSnapshot)?.scoreSnapshot ?? null;
  const evidence = x.signals.filter((s) => s.fetchedAt <= e.at).length;
  return (
    <div className="flex flex-wrap items-center gap-3 pt-1 text-xs">
      <label className="flex min-w-0 flex-1 items-center gap-2">
        <span className="shrink-0 text-muted-foreground">Scrub</span>
        <input type="range" min={0} max={x.events.length - 1} value={at} onChange={(ev) => onChange(Number(ev.target.value))} className="w-full accent-primary" aria-label="Scrub the timeline" />
      </label>
      <p className="tabular-nums" aria-live="polite">
        As of <span className="font-medium">{fmtDate(e.at)}</span>: {version ? `v${version}` : "no requirement yet"} · {snap ? `score ${snap.score.toFixed(3)} (${snap.profileName} v${snap.profileVersion})` : "not scored at a decision yet"} · {evidence} records
      </p>
    </div>
  );
}

function mostCommon(xs: string[]) {
  const n = new Map<string, number>();
  xs.forEach((v) => n.set(v, (n.get(v) ?? 0) + 1));
  return [...n].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
}
