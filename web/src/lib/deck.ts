import { evidenceLabel, factorLabel, marketLabel, regionLabel, releaseLabel, requirementStatusLabel, type DeckResponse } from "@/contracts";

/**
 * Engineering-round deck: one slide per requirement with the argument behind it
 * (SMART text, customer quotes, score breakdown, competition, decision log).
 * Built in the browser from the /plans/:series/deck payload.
 */

const ink = "161A22";
const muted = "5E6673";
const blue = "1C69D4";
const blueSoft = "E8F0FC";
const green = "1F7A4D";
const amber = "A35F00";
const red = "B42318";
const track = "E5E7EB";
const font = "Arial";

export async function downloadDeck(deck: DeckResponse) {
  const pptx = await buildDeck(deck);
  await pptx.writeFile({ fileName: deck.filename });
}

export async function buildDeck(deck: DeckResponse) {
  const { default: PptxGenJS } = await import("pptxgenjs");
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE"; // 13.33 x 7.5 in
  pptx.title = `${deck.successorName}: engineering round`;

  const cover = pptx.addSlide();
  cover.background = { color: "F3F4F6" };
  cover.addText(`${deck.successorName}: customer requirements for the engineering round`, { x: 0.6, y: 1.6, w: 12, h: 1, fontSize: 30, bold: true, color: ink, fontFace: font });
  cover.addText(`${deck.slides.length} signed-off or planned requirements, ranked. Every argument traces back to BMW customer evidence. Generated ${deck.generatedAt.slice(0, 10)}.`, {
    x: 0.6,
    y: 2.6,
    w: 12,
    h: 0.5,
    fontSize: 15,
    color: muted,
    fontFace: font,
  });
  cover.addText(
    deck.slides.map((s) => ({ text: `#${s.rank}  ${s.requirementId}  ${s.title}`, options: { breakLine: true } })),
    { x: 0.6, y: 3.4, w: 12, h: 3.6, fontSize: 13, color: ink, fontFace: font, valign: "top", paraSpaceAfter: 4 },
  );

  for (const s of deck.slides) {
    const slide = pptx.addSlide();
    slide.addText(
      `#${s.rank}  ${s.requirementId}  ·  ${requirementStatusLabel[s.status]}  ·  ${releaseLabel[s.release]}  ·  ${regionLabel[s.marketScope]}  ·  ${evidenceLabel[s.evidenceLevel]}  ·  owner ${s.ownerName}`,
      { x: 0.5, y: 0.25, w: 10.3, h: 0.35, fontSize: 10.5, color: muted, fontFace: font },
    );
    if (s.openIssues.red || s.openIssues.amber) {
      slide.addText(`Quality check: ${[s.openIssues.red && `${s.openIssues.red} to fix`, s.openIssues.amber && `${s.openIssues.amber} to review`].filter(Boolean).join(" · ")}`, {
        x: 10.0,
        y: 0.25,
        w: 2.8,
        h: 0.35,
        fontSize: 10,
        bold: true,
        align: "right",
        color: s.openIssues.red ? red : amber,
        fontFace: font,
      });
    }
    slide.addText(s.title, { x: 0.5, y: 0.6, w: 12.3, h: 0.8, fontSize: 22, bold: true, color: ink, fontFace: font, valign: "top", fit: "shrink" });

    // SMART box: the requirement as engineering will read it.
    slide.addShape(pptx.ShapeType.rect, { x: 0.5, y: 1.45, w: 12.3, h: 1.2, fill: { color: blueSoft }, line: { color: blueSoft } });
    slide.addText(
      [
        { text: s.description, options: { breakLine: true } },
        { text: s.metrics.map((m) => `${m.question}: ${m.target}${m.unit && !m.target.includes(m.unit) ? ` ${m.unit}` : ""} (${m.basis})`).join("   |   "), options: { bold: true, color: blue } },
      ],
      { x: 0.65, y: 1.5, w: 12, h: 1.1, fontSize: 11.5, color: ink, fontFace: font, valign: "top", fit: "shrink", paraSpaceAfter: 4 },
    );

    // Left: what customers say.
    slide.addText("What customers say", { x: 0.5, y: 2.8, w: 6.6, h: 0.3, fontSize: 12, bold: true, color: ink, fontFace: font });
    if (!s.quotes.length) slide.addText("No BMW verbatims linked; evidence comes from studies or web sources.", { x: 0.5, y: 3.15, w: 6.6, h: 0.4, fontSize: 11, italic: true, color: muted, fontFace: font });
    s.quotes.forEach((q, i) => {
      const y = 3.15 + i * 0.78;
      slide.addShape(pptx.ShapeType.rect, { x: 0.5, y, w: 0.06, h: 0.68, fill: { color: green }, line: { color: green } });
      slide.addText(
        [
          { text: `“${q.text}”`, options: { italic: true, breakLine: true } },
          { text: `${q.ref} · ${marketLabel[q.market]}`, options: { color: muted, fontSize: 9 } },
        ],
        { x: 0.65, y, w: 6.45, h: 0.7, fontSize: 10.5, color: ink, fontFace: font, valign: "top", fit: "shrink" },
      );
    });

    // Right: why it ranks where it does.
    slide.addText(`Why it ranks #${s.rank} (score ${s.score.toFixed(2)})`, { x: 7.4, y: 2.8, w: 5.4, h: 0.3, fontSize: 12, bold: true, color: ink, fontFace: font });
    const barW = 2.4;
    s.factors.forEach((f, i) => {
      const y = 3.18 + i * 0.29;
      slide.addText(factorLabel[f.factor], { x: 7.4, y, w: 1.9, h: 0.24, fontSize: 9.5, color: ink, fontFace: font });
      slide.addShape(pptx.ShapeType.rect, { x: 9.35, y: y + 0.06, w: barW, h: 0.13, fill: { color: track }, line: { color: track } });
      if (f.value > 0) slide.addShape(pptx.ShapeType.rect, { x: 9.35, y: y + 0.06, w: Math.max(0.02, barW * f.value), h: 0.13, fill: { color: blue }, line: { color: blue } });
      slide.addText(`${f.value.toFixed(2)} × ${Math.round(f.weight * 100)}%`, { x: 11.85, y, w: 1.0, h: 0.24, fontSize: 8.5, color: muted, fontFace: font, align: "right" });
    });

    // Competition strip.
    slide.addShape(pptx.ShapeType.rect, { x: 0.5, y: 5.6, w: 0.06, h: 0.45, fill: { color: amber }, line: { color: amber } });
    slide.addText(`Competition: ${s.competitor.join("; ") || "not researched yet"}`, { x: 0.65, y: 5.6, w: 12.15, h: 0.45, fontSize: 10.5, color: ink, fontFace: font, valign: "middle", fit: "shrink" });

    // Decision log footer.
    slide.addText(
      [
        { text: `Decision log (source case ${s.caseId}: ${s.caseTitle})`, options: { bold: true, breakLine: true } },
        ...(s.decisions.length
          ? s.decisions.map((e) => ({ text: `${e.at.slice(0, 10)} · ${e.actor} · ${e.action}${e.rationale ? `: ${e.rationale}` : ""}`, options: { breakLine: true } }))
          : [{ text: "No decisions logged yet.", options: { breakLine: true } }]),
      ],
      { x: 0.5, y: 6.15, w: 12.3, h: 1.15, fontSize: 8.5, color: muted, fontFace: font, valign: "top", fit: "shrink" },
    );
  }

  return pptx;
}
