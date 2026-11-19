const reportForm = document.querySelector("#report-form");
const photoInput = reportForm.querySelector("#photo");
const photoPreview = reportForm.querySelector("#photo-preview");
 
function setReportType(type) {
  // Swap wording that differs between lost and found reports.
  reportForm.querySelectorAll("[data-lost]").forEach((el) => {
    el.textContent = el.dataset[type];
  });
 
  // Show fields that only apply to one type. Disabled fields are left out of the submission.
  reportForm.querySelectorAll("[data-show-for]").forEach((group) => {
    const hidden = group.dataset.showFor !== type;
    group.hidden = hidden;
    group.querySelectorAll("input, select, textarea").forEach((field) => {
      field.disabled = hidden;
    });
  });
}
 
// Category and location lists are shared with Browse and the admin (../js/item-store.js and
// ../js/data/locations.js). "Other / Not sure" shows a box to describe the place.
reportForm.querySelector("#category").insertAdjacentHTML("beforeend",
  ItemStore.categories.map((value) => `<option value="${escapeText(value)}">${escapeText(value)}</option>`).join(""));
 
// The location list depends on the campus picked first. The campus select has no name, so only
// locationId is submitted, same as before.
const campusSelect = reportForm.querySelector("#campus");
const locationSelect = reportForm.querySelector("#location");
const syncOther = Locations.watchOther(locationSelect, reportForm.querySelector("#location-other-group"));
 
function fillLocations(campus, selected = "") {
  if (!campus) {
    locationSelect.innerHTML = '<option value="">Select a campus first</option>';
    locationSelect.disabled = true;
  } else {
    locationSelect.innerHTML = '<option value="">Select a location</option>' + Locations.optionsForCampus(campus, selected);
    locationSelect.disabled = false;
    // "Other / Not sure" has only one choice, so pick it for them and show the describe box.
    if (campus === "other") locationSelect.value = Locations.OTHER;
  }
  syncOther();
}
 
campusSelect.addEventListener("change", () => fillLocations(campusSelect.value));
// After the form is reset the campus goes back to blank, so the location list has to follow.
reportForm.addEventListener("reset", () => setTimeout(() => fillLocations(campusSelect.value)));
fillLocations(campusSelect.value); // in case the browser restored the campus on reload
 
reportForm.querySelectorAll("input[name='type']").forEach((input) => {
  input.addEventListener("change", () => setReportType(input.value));
});
 
// report.html links here with ?type=lost or ?type=found.
const initialType = new URLSearchParams(window.location.search).get("type") === "found" ? "found" : "lost";
reportForm.querySelector(`input[name='type'][value='${initialType}']`).checked = true;
setReportType(initialType);
 
photoInput.addEventListener("change", () => {
  const [file] = photoInput.files;
  photoPreview.replaceChildren();
  photoPreview.hidden = !file;
  if (!file) return;
 
  const img = new Image();
  img.src = URL.createObjectURL(file);
  img.alt = "Preview of the selected photo";
  img.onload = () => URL.revokeObjectURL(img.src);
  photoPreview.append(img);
});

reportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = reportForm.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    // Disabled fields (the other report type's) are left out automatically
    const response = await fetch("/api/items", { method: "POST", body: new FormData(reportForm) });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "database_error");
    window.location.href = `report-submitted?ref=${encodeURIComponent(result.reference)}`;
  }  catch (error) {
    const messages = {
      login_required: "Please log in to submit a report.",
      missing_fields: "Please fill in all the required fields.",
      invalid_category: "Please choose a category.",
      invalid_date: "That date isn't valid.",
      future_date: "The date can't be in the future.",
      photo_required: "Please add a photo of the item you found.",
      bad_file_type: "The photo must be a PNG, JPG, GIF or WebP image.",
      file_too_large: "That photo is too large. Choose a smaller one.",
    };
    alert(messages[error.message] || "Couldn't submit your report. Try again.");
  } finally {
    button.disabled = false;
  }
});