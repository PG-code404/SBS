// === Toast Notification Helper ===
function showMessage(msg, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  document.getElementById("toast-container").appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// === Utility ===
function safeSet(id, value) {
  const el = document.getElementById(id);
  if (el) el.innerText = value;
}

// === Refresh Status ===
async function refreshStatus() {
  try {
    const response = await fetch("/status");
    const data = await response.json();
    safeSet("executor_status", data.message || data.executor_status_msg || "Idle");
    safeSet("next_schedule_time", data.next_schedule_time || "Pending");
    safeSet("last_scheduler_run", data.last_scheduler_run || "Not yet run");
    safeSet("active_schedule_id", data.active_schedule_id || "None");
    if (data.uptime !== undefined) safeSet("uptime", data.uptime.toFixed(0));
  } catch (err) {
    console.error("Failed to fetch status:", err);
  }
}

// === Load Schedules ===
async function loadSchedules() {
  try {
    const res = await fetch("/getPendingSchedules");
    const schedules = await res.json();

    const statusRes = await fetch("/status");
    const status = await statusRes.json();
    const activeId = status.active_schedule_id;

    const tbody = document.getElementById("schedule-table-body");
    tbody.innerHTML = "";

    schedules.forEach(s => {
      const tr = document.createElement("tr");
      if (s.id === activeId) tr.className = "schedule-active";

      tr.innerHTML = `
        <td>${s.id}</td>
        <td>${s.start_time}</td>
        <td>${s.end_time}</td>
        <td>${s.target_soc}</td>
        <td>${s.price_p_per_kwh}</td>
        <td>${s.source}</td>
        <td><button class="delete-btn" data-id="${s.id}"><i class="fa-regular fa-trash-can"></i></button></td>`;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error("Failed to load schedules:", err);
  }
}

// === Add Manual Schedule ===
async function addSchedule(event) {
  event.preventDefault();
  const data = {
    start_time: document.getElementById("start_time").value,
    end_time: document.getElementById("end_time").value,
    target_soc: document.getElementById("target_soc").value
  };
  try {
    const res = await fetch("/putSchedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    const result = await res.json();
    showMessage(result.message, "success");
    loadSchedules();
    // ✅ Clear input fields after adding
    document.getElementById("start_time").value = "";
    document.getElementById("end_time").value = "";
    document.getElementById("target_soc").value = "80"; // optional: reset to default

  } catch (err) {
    showMessage("Error adding schedule.", "error");
  }
}

function showConfirm(message) {
    return new Promise((resolve) => {
        const modal = document.getElementById("confirm-modal");
        const msg = document.getElementById("confirm-message");
        const yesBtn = document.getElementById("confirm-yes");
        const noBtn = document.getElementById("confirm-no");

        msg.textContent = message;
        modal.style.display = "flex";

        yesBtn.onclick = () => { modal.style.display = "none"; resolve(true); };
        noBtn.onclick = () => { modal.style.display = "none"; resolve(false); };
    });
}

// Usage in your delete handler
document.addEventListener("click", async (event) => {
    const btn = event.target.closest(".delete-btn");
    if (!btn) return;
    const id = btn.dataset.id;
    if (!id) return;

    const confirmed = await showConfirm(`Delete schedule ${id}?`);
    if (!confirmed) return;

    // Proceed to delete schedule
    const res = await fetch(`/delSchedule/${id}`, { method: "DELETE" });
    const result = await res.json();
    if (result.status === "ok") {
        showMessage(result.message, "success");
        loadSchedules();
    } else {
        showMessage(`Failed to delete: ${result.message}`, "error");
    }
});
/*
// === Delete Schedule ===
document.addEventListener("click", async (event) => {
  const btn = event.target.closest(".delete-btn");
  if (!btn) return;
  const id = btn.dataset.id;
  if (!confirm(`Delete schedule ${id}?`)) return;
  try {
    const res = await fetch(`/delSchedule/${id}`, { method: "DELETE" });
    const result = await res.json();
    if (result.status === "ok") {
      showMessage(result.message, "success");
      loadSchedules();
    } else {
      showMessage(result.message, "error");
    }
  } catch {
    showMessage("Failed to delete schedule.", "error");
  }
});
*/

// === Logout ===
document.getElementById("logout-btn")?.addEventListener("click", async () => {
  try {
    const res = await fetch("/logout", { method: "POST" });
    if (res.ok) window.location.href = "/";
  } catch {
    showMessage("Logout failed", "error");
  }
});

// === Auto-refresh loops ===
document.addEventListener("DOMContentLoaded", () => {
  loadSchedules();
  refreshStatus();
  setInterval(refreshStatus, 5000);
  setInterval(loadSchedules, 120000);
});
