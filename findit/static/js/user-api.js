// UserAPI: the student side's data, from Flask (app.py). Used by Messages and My Reports.
// Every method returns a Promise.

const localDate = (iso) => new Date(iso).toLocaleDateString("en-CA"); // YYYY-MM-DD

// Each student has one conversation with SWAFO; this is its id on the Messages page.
const SWAFO_CONVERSATION = "swafo";

async function requestJSON(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.error || "server");
  return data;
}

// Runs the callback every few seconds while the tab is visible, and as soon as it's shown again,
// so new messages and status changes show up without a reload.
function whileVisible(callback, seconds = 10) {
  setInterval(() => {
    if (document.visibilityState === "visible") callback();
  }, seconds * 1000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") callback();
  });
}

// The student's reports and claims, shaped the way js/my-reports.js expects them.
async function myReportEntries() {
  const response = await fetch("/api/my-reports", { credentials: "same-origin" });
  if (!response.ok) return [];
  const { reports = [], claims = [] } = await response.json();

  const mine = reports.map((r) => ({
    id: r.id, type: r.type, name: r.name, category: r.category,
    locationId: r.locationId || r.location, locationOther: r.locationOther || "",
    date: r.dateLost || r.dateFound,
    submittedAt: r.dateReported, reviewedAt: null, matchedAt: null, match: null,
    status: r.status, item: null, claim: null,
  }));

  // Claims the student sent on found items
  const claimed = claims.map((c) => ({
    id: c.reference, type: "claim", name: c.itemName, category: "",
    locationId: Locations.OTHER, locationOther: "",
    date: localDate(c.dateSubmitted),
    submittedAt: c.dateSubmitted, reviewedAt: null, matchedAt: null, match: null,
    status: { approved: "ready", released: "claimed" }[c.status] || "pending",
    item: null,
    claim: {
      id: c.reference,
      status: c.status === "pending" ? "under_review" : c.status,
      submittedAt: c.dateSubmitted,
      reviewedAt: c.reviewedAt,
    },
  }));

  return [...mine, ...claimed].sort((a, b) => b.date.localeCompare(a.date));
}

const UserAPI = {
  // The office details SWAFO set in admin Settings (js/settings-store.js).
  getOffice() {
    const { office } = SettingsStore.get();
    return Promise.resolve({
      ...office,
      initial: (office.name || "S").charAt(0).toUpperCase(),
      activity: `Office hours: ${office.pickupHours}`,
    });
  },

  // Always exactly one conversation: the student's thread with SWAFO, even before anyone wrote.
  async getConversations() {
    const messages = await requestJSON("/api/messages");
    const last = messages[messages.length - 1];
    return [{
      id: SWAFO_CONVERSATION,
      unread: messages.filter((m) => m.sender !== "user" && !m.read).length,
      lastMessage: last ? last.text || "Sent a photo" : "Ask SWAFO about a lost or found item.",
      updatedAt: last ? last.time : null,
      startedBy: messages[0]?.sender === "user" ? "user" : "swafo",
    }];
  },

  getMessages: () => requestJSON("/api/messages"),

  markRead: () => requestJSON("/api/messages/read", { method: "POST" }),

  // Rejects if the server refuses it (for example, a photo that's too big).
  sendMessage: (_conversationId, { text = "", image = null }) =>
    requestJSON("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, image }),
    }),

  onChange: (callback) => whileVisible(callback),

  // My Reports: the student's lost and found reports plus claims, newest first.
  getMyReports: () => myReportEntries(),

  // SWAFO doesn't send matches to students yet, so My Reports never offers "Not mine".
  rejectMatch: () => Promise.reject(new Error("Matches aren't available yet.")),

  // Only while the report is still pending; rejects with "not_pending" otherwise.
  cancelReport: (reportId) => requestJSON(`/api/my-reports/${encodeURIComponent(reportId)}`, { method: "DELETE" }),

  // Calls back when a report or claim may have changed (SWAFO reviewed it, for example).
  onReportsChange: (callback) => whileVisible(callback, 30),
};
