"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { Maximize2, X } from "lucide-react";
import { carAreaLabel, seriesShort, type Requirement, type Series } from "@/contracts";
import { areaForCarArea, areas, carModels, getZone, isModelled, zoneForCase, type AreaId } from "@/lib/car/zones";
import { cn } from "@/lib/utils";
import { ReqStatusDot } from "@/components/common/badges";
import { Button } from "@/components/ui/button";

// Loaded only when the preview is switched on, so the 3D code never weighs on the first paint.
const CarScene = dynamic(() => import("./car-scene"), { ssr: false });

const AREAS_WIDTH = 232;
const LIST_WIDTH = 344;

/** Weak or saving devices keep the still picture; so does a device with no WebGL. `?car3d=on` / `off` overrides, for testing. */
function canRun3d() {
  const force = new URLSearchParams(location.search).get("car3d");
  if (force === "on") return true;
  if (force === "off") return false;
  const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } };
  if (nav.hardwareConcurrency <= 2 || (nav.deviceMemory !== undefined && nav.deviceMemory <= 2) || nav.connection?.saveData) return false;
  const probe = document.createElement("canvas");
  const gl = probe.getContext("webgl2") ?? probe.getContext("webgl");
  gl?.getExtension("WEBGL_lose_context")?.loseContext();
  return !!gl;
}

/**
 * The car on top of the vehicle plan: a small preview that turns slowly, and a click opens it large with
 * the car areas and their requirements. One canvas serves both sizes, so the model never loads twice.
 *
 * Built so it cannot slow the page down: the still picture shows first, the 3D scene starts when the
 * browser is idle, draws about 20 frames a second and none while it is off screen or the tab is hidden.
 */
export function CarPreview({ series, reqs, onOpenRequirement }: { series: Series; reqs: Requirement[]; onOpenRequirement: (id: string) => void }) {
  const model = isModelled(series) ? carModels[series] : null;
  const { resolvedTheme } = useTheme();
  const slot = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [armed, setArmed] = useState(false);
  const [slow, setSlow] = useState(false);
  const [ready, setReady] = useState(false);
  const [inView, setInView] = useState(true);
  const [hidden, setHidden] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [areaId, setAreaId] = useState<AreaId | null>(null);
  const [pickedId, setPickedId] = useState<string | null>(null);

  // Switch the 3D scene on once the page has settled, if this device should run it.
  useEffect(() => {
    if (!model) return;
    const start = () => setArmed(canRun3d());
    const w = window as Window & { requestIdleCallback?: (cb: () => void, opts: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(start, { timeout: 1500 });
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(start, 600);
    return () => window.clearTimeout(id);
  }, [model]);

  // Nothing is drawn while the card is scrolled away or the tab is in the background.
  useEffect(() => {
    const el = slot.current;
    if (!el) return;
    const seen = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { rootMargin: "80px" });
    seen.observe(el);
    const onVisibility = () => setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      seen.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [model]);

  const close = useCallback(() => {
    setExpanded(false);
    setAreaId(null);
    setPickedId(null);
    opener.current?.focus();
  }, []);
  useEffect(() => {
    if (!expanded) return;
    closeButton.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, close]);

  const handleSlow = useCallback(() => setSlow(true), []);
  const handleReady = useCallback(() => setReady(true), []);

  if (!model || !isModelled(series)) return null;

  const counts = Object.fromEntries(areas.map((a) => [a.id, reqs.filter((r) => areaForCarArea(r.carArea) === a.id).length])) as Record<AreaId, number>;
  const area = areas.find((a) => a.id === areaId) ?? null;
  const inArea = area ? reqs.filter((r) => areaForCarArea(r.carArea) === area.id) : [];
  const picked = reqs.find((r) => r.id === pickedId) ?? null;
  const focus = picked ? getZone(zoneForCase({ title: picked.title, featureName: picked.shortTitle, carArea: picked.carArea })) : area;
  const name = seriesShort[series];
  const showScene = (armed && !slow) || expanded;

  return (
    <div className="px-6 pt-6">
      <div ref={slot} className="relative h-44">
        {expanded && <div className="fixed inset-0 z-40 animate-in bg-black/60 duration-200 fade-in" onClick={close} />}
        <section
          role={expanded ? "dialog" : undefined}
          aria-modal={expanded || undefined}
          aria-label={expanded ? `${name} in 3D` : undefined}
          className={cn(
            "overflow-hidden rounded-xl border bg-gradient-to-b from-card to-muted/50",
            expanded ? "fixed inset-4 z-50 animate-in shadow-2xl duration-200 fade-in zoom-in-95" : "absolute inset-0",
          )}
        >
          {/* The still picture: shown first, and for good on devices that should not run 3D. */}
          <div
            aria-hidden
            className={cn("absolute inset-0 bg-contain bg-center bg-no-repeat transition-opacity duration-500", ready && showScene ? "opacity-0" : "opacity-100")}
            style={{ backgroundImage: `url(${model.file.replace(/\.glb$/, ".webp")})` }}
          />
          {showScene && (
            <CarScene
              model={model}
              focus={focus}
              dark={resolvedTheme !== "light"}
              mode={expanded ? "big" : "small"}
              paused={hidden || (!expanded && !inView)}
              insetLeft={expanded ? AREAS_WIDTH : 0}
              insetRight={expanded && focus ? LIST_WIDTH : 0}
              onReady={handleReady}
              onSlow={expanded ? undefined : handleSlow}
            />
          )}

          {!expanded && (
            <>
              <div className="pointer-events-none absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-card/90 to-transparent" />
              <div className="pointer-events-none absolute top-5 left-6 flex flex-col gap-0.5">
                <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Your car in 3D</span>
                <span className="text-xl font-semibold">{name}</span>
                <span className="text-xs text-muted-foreground">{reqs.length} requirements across 5 car areas</span>
              </div>
              <span className="pointer-events-none absolute right-4 bottom-3 flex items-center gap-1.5 rounded-full border bg-card/90 px-3 py-1 text-xs font-medium">
                <Maximize2 className="size-3.5" /> Explore
              </span>
              <button
                type="button"
                aria-label={`Explore the ${name} in 3D`}
                className="absolute inset-0 cursor-pointer focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset"
                onClick={(e) => {
                  opener.current = e.currentTarget;
                  setExpanded(true);
                }}
              />
            </>
          )}

          {expanded && (
            <>
              <div className="pointer-events-none absolute top-4 left-5 flex flex-col">
                <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Explore in 3D</span>
                <h2 className="text-2xl font-semibold">{name}</h2>
              </div>
              <Button ref={closeButton} size="icon" variant="outline" className="absolute top-4 right-4" aria-label="Close 3D view" onClick={close}>
                <X className="size-4" />
              </Button>

              <nav aria-label="Areas of the car" className="absolute bottom-5 left-5 flex flex-col items-start gap-2">
                <span className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">Areas · requirements</span>
                {areas.map((a) => (
                  <button
                    key={a.id}
                    type="button"
                    aria-pressed={a.id === areaId}
                    onClick={() => {
                      setAreaId(a.id === areaId ? null : a.id);
                      setPickedId(null);
                    }}
                    className={cn(
                      "inline-flex h-9 items-center gap-2 rounded-full border bg-card/90 px-4 text-sm font-medium whitespace-nowrap transition-colors duration-150 hover:border-border-strong",
                      a.id === areaId && "border-primary bg-primary text-primary-foreground hover:border-primary",
                    )}
                  >
                    {a.label}
                    <span className="text-xs tabular-nums opacity-80">{counts[a.id]}</span>
                  </button>
                ))}
              </nav>

              {area && (
                <aside aria-label={`${area.label} requirements`} className="absolute top-16 right-4 bottom-4 flex animate-in flex-col gap-2 overflow-hidden rounded-lg border bg-card p-4 shadow-xl duration-300 fade-in slide-in-from-right-6" style={{ width: LIST_WIDTH - 24 }}>
                  <div className="flex items-baseline justify-between">
                    <h3 className="text-[15px] font-semibold">{area.label}</h3>
                    <span className="text-xs text-muted-foreground">{inArea.length} requirements</span>
                  </div>
                  <ul className="-mx-1 flex-1 divide-y overflow-y-auto overscroll-contain px-1">
                    {inArea.map((r) => (
                      <li key={r.id}>
                        <button
                          type="button"
                          aria-pressed={r.id === pickedId}
                          onClick={() => setPickedId(r.id === pickedId ? null : r.id)}
                          className={cn("flex w-full flex-col gap-1 rounded-md px-2 py-2.5 text-left transition-colors duration-150 hover:bg-muted/60", r.id === pickedId && "bg-muted")}
                        >
                          <span className="text-sm font-medium">{r.shortTitle}</span>
                          <span className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                            {carAreaLabel[r.carArea]}
                            <ReqStatusDot status={r.status} />
                          </span>
                        </button>
                      </li>
                    ))}
                    {inArea.length === 0 && <li className="py-6 text-center text-sm text-muted-foreground">No requirements in this area yet.</li>}
                  </ul>
                  {picked && (
                    <div className="flex flex-col gap-2 border-t pt-3">
                      <p className="line-clamp-4 text-[13px] leading-[18px] text-muted-foreground">{picked.title}</p>
                      <Button
                        size="sm"
                        onClick={() => {
                          close();
                          onOpenRequirement(picked.id);
                        }}
                      >
                        Open requirement
                      </Button>
                    </div>
                  )}
                </aside>
              )}
              {!area && (
                <p className="pointer-events-none absolute right-5 bottom-5 max-w-56 text-right text-xs text-muted-foreground">Pick an area to light it up on the car. Drag to turn it, scroll to zoom.</p>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
