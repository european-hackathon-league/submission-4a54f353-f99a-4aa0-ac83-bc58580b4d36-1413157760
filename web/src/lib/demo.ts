/**
 * Demo helpers owned by the UI (prefill texts for the stage demo).
 * Not part of the API contract.
 */

/** Strategy docs a PM can paste during onboarding; the AI turns them into brief proposals. */
export const sampleStrategyDocs: Record<string, { title: string; text: string }> = {
  "u-wei": {
    title: "5 Series Li China strategy 2030 (excerpt)",
    text: `5 Series Li successor, China market strategy (draft for the product line meeting)

Thesis: In China the 5 Series Li must win buyers from NIO, Xiaomi and Li Auto: digital experience and rear-seat comfort first, driving dynamics second.

Target customer: we need conquest buyers from local EV brands, not only BMW owners renewing their lease. Tier stays premium.

Ambition: this is a revolution for China, not an evolution: we want 4 real innovations the customer notices in the showroom.

Givens: range targets are set in CLTC, never WLTP. The mid-life update is still open for China-specific content.

Cost: low sensitivity, local sourcing in Shenyang allows richer standard equipment.

Do not fight on: entry-level price.`,
  },
};
export const sampleMeetingNotes: Record<string, { title: string; attendees: string[]; text: string }> = {
  "CASE-G60-0005": {
    title: "Climate sync with engineering and design",
    attendees: ["Lena Hofmann", "Climate systems (Engineering)", "Interior design", "Controlling 5 Series"],
    text: `Climate sync 10.10.

- Engineering: today the G60 needs about 9 minutes to cool the cabin from 45 °C to 22 °C. The Mercedes E-Class does it in roughly 6 minutes in our benchmark. A 6-minute target is realistic with the new compressor.
- Design: a slim physical climate bar below the display is possible and fits the design language if it uses the same glass-look surface.
- Controlling: the climate bar costs about €18-25 per car.
- Next: Lena to update the case and draft a requirement.`,
  },
};
