refreshStatus();

async function addSchedule(event) {
    event.preventDefault();
    const data = {
        start_time: document.getElementById("start_time").value,
        end_time: document.getElementById("end_time").value,
        target_soc: document.getElementById("target_soc").value
    };
    console.log("Sending schedule:", data);
    const res = await fetch("/putSchedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
    });
    const result = await res.json();
    alert(result.message);
    loadSchedules();
}

async function loadSchedules() {
    const res = await fetch("/getPendingSchedules");
    const schedules = await res.json();

    const tbody = document.getElementById("schedule-table-body");
    tbody.innerHTML = "";
    const statusRes = await fetch("/status");
    const status = await statusRes.json();
    const activeId = status.active_schedule_id;


    schedules.forEach(s => {
        const tr = document.createElement("tr");

        // Determine row class
        let rowClass = "";
        if (s.id === activeId) {
            rowClass = "schedule-active";
        } else if (s.start_time === status.next_schedule_time) {
            rowClass = "schedule-upcoming";
        }

        tr.className = rowClass;
        tr.innerHTML = `
            <td>${s.id}</td>
            <td>${s.start_time}</td>
            <td>${s.end_time}</td>
            <td>${s.target_soc}</td>
            <td>${s.price_p_per_kwh}</td>
            <td>${s.source}</td>
            <td><button class="delete-btn" data-id="${s.id}" title="Delete">🗑️</button></td>
        `;
        tbody.appendChild(tr);
    });
}

function safeSet(id, value) {
    const el = document.getElementById(id);
    if (el) el.innerText = value;
}

async function refreshStatus() {
    try {
        const response = await fetch("/status");
        const data = await response.json();

        safeSet("executor_status", data.message || data.executor_status_msg || "Idle");
        safeSet("next_schedule_time", data.next_schedule_time || "Pending");
        safeSet("last_scheduler_run", data.last_scheduler_run || "Not yet run");
        safeSet("active_schedule_id", data.active_schedule_id || "None");
        safeSet("current_price", data.current_price ?? "-");
        safeSet("soc", data.soc ?? "-");
        safeSet("solar_power", data.solar_power ?? "-");
        safeSet("island", data.island ?? "-");
        if (data.uptime !== undefined) safeSet("uptime", data.uptime.toFixed(0));document.getElementById("executor_status").innerText = data.message || data.executor_status_msg || "Idle";

    } catch (err) {
        console.error("Failed to fetch status:", err);
    }
}


// Load schedules on page load
document.addEventListener("DOMContentLoaded", () => {
    loadSchedules();
    setInterval(loadSchedules, 1200000); // refresh every 2m

    refreshStatus(); 
    setInterval(refreshStatus, 5000); // Refresh every 5 seconds
});

document.addEventListener("click", async (event) => {
  const btn = event.target.closest(".delete-btn");
  if (!btn) return;

  const scheduleId = btn.dataset.id;
  if (!scheduleId) return;

  if (!confirm(`Are you sure you want to delete schedule ${scheduleId}?`)) return;

  try {
    const res = await fetch(`/delSchedule/${scheduleId}`, { method: "DELETE" });
    const result = await res.json();
    
    if (result.status === "ok") {
      alert(result.message);
      loadSchedules(); // refresh table after deletion
    } else {
      alert(`Failed to delete: ${result.message}`);
    }
  } catch (err) {
    console.error("Error deleting schedule:", err);
  }
});

async function refreshHeaderStatus() {
    try {
        const res = await fetch("/status");
        const data = await res.json();
        document.getElementById("active-schedule-label").innerText =
            "Active Schedule: " + (data.active_schedule_id || "None");
    } catch (err) {
        console.error("Failed to update header status:", err);
    }
}

// Call it on page load and optionally every 5-10 seconds
document.addEventListener("DOMContentLoaded", () => {
    refreshHeaderStatus();
    setInterval(refreshHeaderStatus, 5000);
});

document.getElementById("logout-btn")?.addEventListener("click", async function() {
    try {
        const res = await fetch("/logout", { method: "POST" });
        if (res.ok) {
            window.location.href = "/"; // redirect to login page
        } else {
            alert("Logout failed.");
        }
    } catch (err) {
        console.error("Logout error:", err);
        alert("Logout error, see console.");
    }
});
