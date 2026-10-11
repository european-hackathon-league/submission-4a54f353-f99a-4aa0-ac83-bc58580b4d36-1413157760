/**
 * Short display names (2-4 words) for lists, cards and diagrams.
 * The full title (the problem statement or the SMART sentence) stays on the detail page.
 */
export const caseShortTitles: Record<string, string> = {
  "CASE-G60-0001": "Physical shortcuts",
  "CASE-G60-0002": "Start-stop memory",
  "CASE-G60-0003": "Winter range & charging",
  "CASE-G60-0004": "Hands-free tailgate",
  "CASE-G60-0005": "Climate control",
  "CASE-G60-0006": "Flat-tire solution",
  "CASE-G60-0007": "Seat massage",
  "CASE-G60-0008": "Frunk",
  "CASE-G60-0009": "Phone connection",
  "CASE-G60-0010": "Opening glass roof",
  "CASE-G60-0011": "Luggage loading",
  "CASE-G60-0012": "Sporty feel",
  "CASE-G60-CN-0001": "Equipment level (China)",
  "CASE-G60-CN-0002": "Rear-seat cooler",
  "CASE-G60-CN-0003": "CLTC range",
  "CASE-G60-CN-0004": "Digital ecosystem (China)",
  "CASE-HMI-0001": "Touch-first operation",
  "CASE-F70-0001": "Printed manual",
  "CASE-F70-0002": "Touch screen",
  "CASE-F70-0003": "Boot space",
  "CASE-F70-0004": "Exhaust pops",
  "CASE-G70-0001": "Rear-view mirror",
  "CASE-G70-0002": "Cabin cool-down",
  "CASE-G70-0003": "Rear space",
  "CASE-G70-0004": "Rear cooler module",
  "CASE-INNO-0001": "AI assistant",
  "CASE-INNO-0002": "Smart-glass roof",
  "CASE-INNO-0003": "Vehicle-to-load",
};

export const requirementShortTitles: Record<string, string> = {
  "REQ-G60-0002": "Start-stop stays off",
  "REQ-G60-0004": "Tailgate opens first kick",
  "REQ-G60-0009": "Phone charging under 40 °C",
  "REQ-G60-0019": "CarPlay stays connected",
  "REQ-G60-CN-0002": "Rear cooler 8 °C",
  "REQ-G60-CN-0003": "750 km CLTC",
  "REQ-G70-0001": "Camera mirror",
  "REQ-F70-0001": "Printed manual on request",
};

/** Fallback for new items: first words of the title, without trailing filler. */
export function shortFromTitle(title: string, words = 4) {
  const w = title.replace(/[:(].*$/, "").trim().split(/\s+/);
  return w.length <= words ? w.join(" ") : w.slice(0, words).join(" ");
}
