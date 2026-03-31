const state = {
  editingId: null,
  scheduled: [],
  delivered: [],
  status: null,
};

const elements = {
  form: document.getElementById("reminder-form"),
  formTitle: document.getElementById("form-title"),
  message: document.getElementById("message"),
  date: document.getElementById("date"),
  time: document.getElementById("time"),
  timezone: document.getElementById("timezone"),
  submitButton: document.getElementById("submit-button"),
  cancelEdit: document.getElementById("cancel-edit"),
  formError: document.getElementById("form-error"),
  statusBanner: document.getElementById("status-banner"),
  upcomingList: document.getElementById("upcoming-list"),
  activityList: document.getElementById("activity-list"),
  reminderTemplate: document.getElementById("reminder-template"),
  activityTemplate: document.getElementById("activity-template"),
  currentTime: document.getElementById("current-time"),
  currentDate: document.getElementById("current-date"),
};

function getBrowserTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

function roundToNextFiveMinutes(date) {
  const rounded = new Date(date.getTime());
  rounded.setSeconds(0, 0);
  const minutes = rounded.getMinutes();
  const offset = (5 - (minutes % 5)) % 5;
  rounded.setMinutes(minutes + (offset === 0 ? 5 : offset));
  return rounded;
}

function setDefaultDateTime() {
  const rounded = roundToNextFiveMinutes(new Date());
  elements.date.value = rounded.toISOString().slice(0, 10);
  elements.time.value = rounded.toTimeString().slice(0, 5);
}

function setClock() {
  const now = new Date();
  elements.currentTime.textContent = now.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  elements.currentDate.textContent = now.toLocaleDateString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function setFormError(message) {
  elements.formError.hidden = !message;
  elements.formError.textContent = message || "";
}

function resetForm() {
  state.editingId = null;
  elements.form.reset();
  elements.timezone.value = getBrowserTimezone();
  setDefaultDateTime();
  elements.formTitle.textContent = "Create reminder";
  elements.submitButton.textContent = "Save reminder";
  elements.cancelEdit.hidden = true;
  setFormError("");
}

function toLocalDate(utcText) {
  return new Date(utcText);
}

function formatLocalDateTime(utcText) {
  const date = toLocalDate(utcText);
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function isEditable(reminder) {
  if (reminder.status !== "scheduled") {
    return false;
  }
  return new Date(reminder.scheduled_at_utc) > new Date();
}

function renderStatusBanner() {
  const status = state.status;
  if (!status) {
    return;
  }

  elements.statusBanner.className = "status-banner";
  if (status.pi_delivery_state === "delivering") {
    elements.statusBanner.classList.add("status-delivering");
    elements.statusBanner.textContent = "Delivering reminder to OLED now.";
    return;
  }
  if (status.pi_delivery_state === "retrying") {
    elements.statusBanner.classList.add("status-retrying");
    elements.statusBanner.textContent = status.last_delivery_error
      ? `Retrying delivery: ${status.last_delivery_error}`
      : "Retrying OLED delivery.";
    return;
  }
  if (status.last_delivery_error) {
    elements.statusBanner.classList.add("status-error");
    elements.statusBanner.textContent = `Last delivery failed: ${status.last_delivery_error}`;
    return;
  }

  elements.statusBanner.classList.add("status-idle");
  elements.statusBanner.textContent = "OLED connected recently. Scheduler idle.";
}

function renderUpcoming() {
  const container = elements.upcomingList;
  container.innerHTML = "";
  if (state.scheduled.length === 0) {
    container.className = "stack empty-state";
    container.textContent = "No reminders scheduled.";
    return;
  }

  container.className = "stack";
  state.scheduled.forEach((reminder) => {
    const fragment = elements.reminderTemplate.content.cloneNode(true);
    fragment.querySelector(".scheduled-time").textContent = formatLocalDateTime(reminder.scheduled_at_utc);
    fragment.querySelector(".status-badge").textContent = reminder.status;
    fragment.querySelector(".reminder-message").textContent = reminder.message;
    fragment.querySelector(".reminder-detail").textContent =
      `Timezone ${reminder.timezone} • Attempts ${reminder.attempt_count}`;

    const editButton = fragment.querySelector(".edit-button");
    const cancelButton = fragment.querySelector(".cancel-button");
    const editable = isEditable(reminder);
    editButton.disabled = !editable;
    cancelButton.disabled = reminder.status !== "scheduled";

    editButton.addEventListener("click", () => populateForm(reminder));
    cancelButton.addEventListener("click", async () => {
      await fetch(`/api/reminders/${reminder.id}`, { method: "DELETE" });
      await refresh();
    });

    container.appendChild(fragment);
  });
}

function renderActivity() {
  const retrying = state.scheduled.filter((item) => item.attempt_count > 0 || item.last_error);
  const activity = [...state.delivered, ...retrying]
    .sort((left, right) => new Date(right.updated_at_utc) - new Date(left.updated_at_utc))
    .slice(0, 10);

  const container = elements.activityList;
  container.innerHTML = "";
  if (activity.length === 0) {
    container.className = "stack empty-state";
    container.textContent = "No delivery activity yet.";
    return;
  }

  container.className = "stack";
  activity.forEach((reminder) => {
    const fragment = elements.activityTemplate.content.cloneNode(true);
    fragment.querySelector(".activity-message").textContent = reminder.message;
    fragment.querySelector(".activity-badge").textContent = reminder.status;

    const detail = reminder.status === "delivered"
      ? `Delivered ${formatLocalDateTime(reminder.delivered_at_utc)}`
      : `Retry queued • ${reminder.last_error || "Waiting for OLED connection"}`;
    fragment.querySelector(".activity-detail").textContent = detail;
    container.appendChild(fragment);
  });
}

function populateForm(reminder) {
  state.editingId = reminder.id;
  elements.formTitle.textContent = "Edit reminder";
  elements.submitButton.textContent = "Update reminder";
  elements.cancelEdit.hidden = false;
  elements.message.value = reminder.message;
  elements.date.value = reminder.scheduled_at_local.slice(0, 10);
  elements.time.value = reminder.scheduled_at_local.slice(11, 16);
  elements.timezone.value = reminder.timezone;
  setFormError("");
}

function collectPayload() {
  return {
    message: elements.message.value,
    scheduled_at_local: `${elements.date.value}T${elements.time.value}`,
    timezone: elements.timezone.value,
  };
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Request failed.");
  }
  return data;
}

async function refresh() {
  const [status, scheduled, delivered] = await Promise.all([
    fetch("/api/status").then(readJson),
    fetch("/api/reminders?status=scheduled&limit=50").then(readJson),
    fetch("/api/reminders?status=delivered&limit=10").then(readJson),
  ]);
  state.status = status;
  state.scheduled = scheduled;
  state.delivered = delivered;
  renderStatusBanner();
  renderUpcoming();
  renderActivity();
}

async function submitForm(event) {
  event.preventDefault();
  setFormError("");
  const payload = collectPayload();
  const url = state.editingId ? `/api/reminders/${state.editingId}` : "/api/reminders";
  const method = state.editingId ? "PATCH" : "POST";

  try {
    await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(readJson);
    resetForm();
    await refresh();
  } catch (error) {
    setFormError(error.message);
  }
}

function bindEvents() {
  elements.form.addEventListener("submit", submitForm);
  elements.cancelEdit.addEventListener("click", resetForm);
}

async function initialize() {
  elements.timezone.value = getBrowserTimezone();
  setDefaultDateTime();
  setClock();
  bindEvents();
  await refresh();
  window.setInterval(setClock, 1000);
  window.setInterval(refresh, 10000);
}

initialize().catch((error) => {
  setFormError(error.message);
});
