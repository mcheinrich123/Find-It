// Claim page: shows the found item in ?id= (from the database, ./items-api.js) next to the form,
// sends the claim to Flask (POST /api/claims, reviewed on the admin Claims page) and swaps the
// form for a confirmation.

const claimForm = document.querySelector("#claim-form");
const claimSuccess = document.querySelector("#claim-success");
const submitButton = claimForm.querySelector('[type="submit"]');

// Old links without ?id= show the newest item, like the item details page.
const claimId = new URLSearchParams(window.location.search).get("id");
let claimItem = null;

const ERROR_MESSAGES = {
  login_required: "Please log in to submit a claim.",
  item_unavailable: "This item can't be claimed anymore. It may already have been returned to its owner.",
  missing_fields: "Please describe the item and tick the box to certify that it's yours.",
  invalid_date: "That date isn't valid.",
  duplicate_claim: "You've already sent a claim for this item. You can follow it on My Reports.",
  bad_file_type: "Supporting files must be images (PNG, JPG, GIF or WebP) or PDFs.",
  file_too_large: "Your files are too large together (10 MB at most). Choose smaller ones.",
};

function showItem(item) {
  const detailsLink = `/item-details?id=${encodeURIComponent(item.id)}`;
  document.title = `FindIt | Claim ${item.name}`;
  document.querySelector("#crumb-item").textContent = item.name;
  document.querySelector("#crumb-item").href = detailsLink;
  document.querySelector("#claim-cancel").href = detailsLink;
  document.querySelector("#summary-heading").textContent = item.name;
  document.querySelector("#summary-ref").textContent = item.id;
  document.querySelector("#summary-found").textContent = Locations.label(item.locationId, item.locationOther);
  if (item.image) document.querySelector("#summary-img").src = item.image;

  const date = document.querySelector("#summary-date");
  date.dateTime = item.dateFound;
  date.textContent = new Date(`${item.dateFound}T00:00:00`)
    .toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function showUnavailable(text) {
  document.querySelector("#summary-heading").textContent = text;
  submitButton.disabled = true;
}

async function loadItem() {
  try {
    const item = claimId ? await ItemsAPI.get(claimId) : (await ItemsAPI.available())[0];
    // Only found items that nobody has collected yet can be claimed.
    if (!item || item.type !== "found" || item.status === "claimed") {
      showUnavailable("This item isn't available to claim");
      return;
    }
    claimItem = item;
    showItem(item);
  } catch {
    showUnavailable("Couldn't load this item. Refresh to try again.");
  }
}

claimForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!claimItem) return;

  submitButton.disabled = true;
  try {
    const form = new FormData(claimForm);
    form.set("itemId", claimItem.id);
    const response = await fetch("/api/claims", { method: "POST", credentials: "same-origin", body: form });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "server");

    document.querySelector("#track-claim").href = `/my-reports?id=${encodeURIComponent(result.reference)}`;
    claimForm.hidden = true;
    claimSuccess.hidden = false;
    claimSuccess.focus();
  } catch (error) {
    alert(ERROR_MESSAGES[error.message] || "Couldn't submit your claim. Try again.");
  } finally {
    submitButton.disabled = false;
  }
});

loadItem();
