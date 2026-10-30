const statusPill = document.querySelector("#top-status");
const heroTitle = document.querySelector("#hero-title");
const heroDescription = document.querySelector("#hero-description");
const serviceStatus = document.querySelector("#service-status");
const serviceIndicator = document.querySelector("#service-indicator");
const consoleStatus = document.querySelector("#console-status");
const consoleIndicator = document.querySelector("#console-indicator");
const startButton = document.querySelector("#start-button");
const stopButton = document.querySelector("#stop-button");
const actionMessage = document.querySelector("#action-message");
const consoleOutput = document.querySelector("#console-output");
const consoleBody = document.querySelector("#console-body");
const commandForm = document.querySelector("#command-form");
const commandInput = document.querySelector("#console-command");
const commandSubmit = document.querySelector("#command-submit");
const commandMessage = document.querySelector("#command-message");
const streamBadge = document.querySelector("#stream-badge");
const lastUpdated = document.querySelector("#last-updated");
const playerList = document.querySelector("#player-list");
const playersBadge = document.querySelector("#players-badge");
const playersTotal = document.querySelector("#players-total");
const playersState = document.querySelector("#players-state");
const playersCount = document.querySelector("#players-count");
const playersCountLabel = document.querySelector("#players-count-label");
const playersUpdated = document.querySelector("#players-updated");
const resourcesBadge = document.querySelector("#resources-badge");
const topCpuValue = document.querySelector("#top-cpu-value");
const topCpuMeter = document.querySelector("#top-cpu-bar").parentElement;
const topCpuBar = document.querySelector("#top-cpu-bar");
const topMemoryValue = document.querySelector("#top-memory-value");
const topMemoryMeter = document.querySelector("#top-memory-bar").parentElement;
const topMemoryBar = document.querySelector("#top-memory-bar");
const topResourcesState = document.querySelector("#top-resources-state");
const cpuPercent = document.querySelector("#cpu-percent");
const cpuDetail = document.querySelector("#cpu-detail");
const cpuMeter = document.querySelector("#cpu-meter");
const cpuBar = document.querySelector("#cpu-bar");
const cpuChart = document.querySelector("#cpu-chart");
const memoryReading = document.querySelector("#memory-reading");
const memoryDetail = document.querySelector("#memory-detail");
const memoryMeter = document.querySelector("#memory-meter");
const memoryBar = document.querySelector("#memory-bar");
const memoryChart = document.querySelector("#memory-chart");
const resourcesUpdated = document.querySelector("#resources-updated");

let lastLogs = null;
let logRequestPending = false;
let playerRequestPending = false;
let resourceRequestPending = false;
const cpuHistory = [];
const memoryHistory = [];

function setActiveTab(tab) {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `${tab}-view`);
  });
  if (tab === "console") refreshLogs();
  if (tab === "players") refreshPlayers();
  if (tab === "resources") refreshResources();
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});
document.querySelectorAll("[data-open-tab]").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.openTab));
});

async function refreshStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not read server status");

    const running = data.running;
    statusPill.classList.toggle("stopped", !running);
    statusPill.querySelector("span").textContent = data.status;
    heroTitle.textContent = running ? "Your server is online" : "Your server is offline";
    heroDescription.textContent = running
      ? "Paper is running and ready for players."
      : `The system service is ${data.status}. Start it whenever you're ready.`;
    serviceStatus.textContent = data.status;
    serviceIndicator.classList.toggle("good", running);
    serviceIndicator.classList.toggle("bad", !running);
    consoleStatus.textContent = data.console_available ? "Connected" : "Not running";
    consoleIndicator.classList.toggle("good", data.console_available);
    consoleIndicator.classList.toggle("bad", !data.console_available);
    startButton.disabled = running;
    stopButton.disabled = !running;
  } catch (error) {
    statusPill.classList.add("stopped");
    statusPill.querySelector("span").textContent = "Unavailable";
    heroTitle.textContent = "Could not reach the service";
    heroDescription.textContent = error.message;
    serviceStatus.textContent = "Unavailable";
    serviceIndicator.classList.remove("good");
    serviceIndicator.classList.add("bad");
    startButton.disabled = true;
    stopButton.disabled = true;
  }
}

async function runAction(action) {
  if (action === "stop" && !window.confirm("Stop the Minecraft server?")) return;
  startButton.disabled = true;
  stopButton.disabled = true;
  actionMessage.classList.remove("error");
  actionMessage.textContent = action === "start" ? "Starting server…" : "Stopping server…";

  try {
    const response = await fetch(`/api/${action}`, {
      method: "POST",
      credentials: "same-origin",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Could not ${action} server`);
    actionMessage.textContent = action === "start" ? "Server started." : "Server stopped.";
    await refreshStatus();
  } catch (error) {
    actionMessage.classList.add("error");
    actionMessage.textContent = error.message;
    await refreshStatus();
  }
}

startButton.addEventListener("click", () => runAction("start"));
stopButton.addEventListener("click", () => runAction("stop"));
document.querySelector("#refresh-button").addEventListener("click", refreshStatus);
document.querySelector("#scroll-button").addEventListener("click", () => {
  consoleBody.scrollTop = consoleBody.scrollHeight;
});

commandForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const command = commandInput.value.trim();
  if (!command) {
    commandMessage.classList.add("error");
    commandMessage.textContent = "Enter a command to send.";
    commandInput.focus();
    return;
  }

  commandSubmit.disabled = true;
  commandMessage.classList.remove("error");
  commandMessage.textContent = "Sending command…";
  try {
    const response = await fetch("/api/console/command", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not send command");
    commandMessage.textContent = "Command sent to Paper.";
    commandInput.value = "";
    commandInput.focus();
    refreshLogs();
  } catch (error) {
    commandMessage.classList.add("error");
    commandMessage.textContent = error.message;
  } finally {
    commandSubmit.disabled = false;
  }
});

function showPlayerEmpty(message) {
  playerList.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "players-empty";
  empty.textContent = message;
  playerList.append(empty);
}

function renderPlayers(names) {
  playerList.replaceChildren();
  if (names.length === 0) {
    showPlayerEmpty("No players are online right now.");
    return;
  }

  for (const name of names) {
    const row = document.createElement("div");
    row.className = "player-row";
    const avatar = document.createElement("span");
    avatar.className = "player-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = name.slice(0, 1);
    const details = document.createElement("span");
    details.className = "player-details";
    const playerName = document.createElement("strong");
    playerName.textContent = name;
    const presence = document.createElement("span");
    presence.textContent = "Connected to Paper";
    details.append(playerName, presence);
    const online = document.createElement("span");
    online.className = "player-online";
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    online.append(dot, document.createTextNode("ONLINE"));
    row.append(avatar, details, online);
    playerList.append(row);
  }
}

async function refreshPlayers() {
  if (playerRequestPending) return;
  playerRequestPending = true;
  try {
    const response = await fetch("/api/players", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not read online players");

    const online = Number.isInteger(data.online) ? data.online : 0;
    const maximum = Number.isInteger(data.maximum) ? data.maximum : 0;
    const names = Array.isArray(data.players)
      ? [...new Set(data.players.filter((name) => typeof name === "string"))]
      : [];
    const available = data.available === true;

    playersBadge.classList.toggle("offline", !available);
    playersBadge.innerHTML = available ? "<i></i> LIVE" : "<i></i> OFFLINE";
    playersTotal.innerHTML = `${online} <small>/ ${maximum}</small>`;
    playersCount.textContent = String(online);
    playersCountLabel.textContent = `${online} ${online === 1 ? "PLAYER" : "PLAYERS"}`;
    playersState.classList.toggle("offline", !available);
    playersState.textContent = available ? "Server responding" : "Server unavailable";
    if (available && online > names.length) {
      showPlayerEmpty(`Paper reports ${online} online, but did not include their names in its status response.`);
    } else if (available) {
      renderPlayers(names);
    } else {
      showPlayerEmpty(data.error || "The Paper server is offline. Start it to see online players.");
    }
    playersUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    playersBadge.classList.add("offline");
    playersBadge.innerHTML = "<i></i> UNAVAILABLE";
    playersState.classList.add("offline");
    playersState.textContent = "Could not connect";
    showPlayerEmpty(error.message);
    playersUpdated.textContent = "Could not refresh";
  } finally {
    playerRequestPending = false;
  }
}

function formatMemory(bytes) {
  return `${(bytes / (1024 ** 3)).toFixed(1)} GiB`;
}

function updateResourceMeter(meter, bar, percent) {
  const clampedPercent = Math.min(100, Math.max(0, percent));
  meter.setAttribute("aria-valuenow", clampedPercent.toFixed(1));
  bar.style.width = `${clampedPercent}%`;
}

function updateResourceChart(chart, history, percent) {
  history.push(Math.min(100, Math.max(0, percent)));
  if (history.length > 60) history.shift();
  const points = history.map((value, index) => {
    const x = history.length === 1 ? 360 : (index / (history.length - 1)) * 360;
    const y = 90 - (value / 100) * 80;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  chart.setAttribute("points", points.join(" "));
}

async function refreshResources() {
  if (resourceRequestPending) return;
  resourceRequestPending = true;
  try {
    const response = await fetch("/api/resources", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not read system resources");
    if (
      typeof data.cpu_percent !== "number" ||
      !Number.isFinite(data.cpu_percent) ||
      !Number.isInteger(data.cpu_count) ||
      data.cpu_count < 1 ||
      typeof data.memory_total_bytes !== "number" ||
      !Number.isFinite(data.memory_total_bytes) ||
      typeof data.memory_used_bytes !== "number" ||
      !Number.isFinite(data.memory_used_bytes) ||
      typeof data.memory_available_bytes !== "number" ||
      !Number.isFinite(data.memory_available_bytes)
    ) {
      throw new Error("System resource response was invalid");
    }

    const cpu = Math.min(100, Math.max(0, data.cpu_percent));
    const memoryPercent = data.memory_total_bytes > 0
      ? (data.memory_used_bytes / data.memory_total_bytes) * 100
      : 0;
    topCpuValue.textContent = `${cpu.toFixed(1)}%`;
    topCpuMeter.setAttribute("aria-valuenow", cpu.toFixed(1));
    topCpuBar.style.width = `${cpu}%`;
    topMemoryValue.textContent = `${formatMemory(data.memory_used_bytes)} / ${formatMemory(data.memory_total_bytes)}`;
    topMemoryMeter.setAttribute("aria-valuenow", memoryPercent.toFixed(1));
    topMemoryBar.style.width = `${Math.min(100, Math.max(0, memoryPercent))}%`;
    topResourcesState.textContent = "LIVE";
    topResourcesState.classList.remove("offline");
    cpuPercent.innerHTML = `${cpu.toFixed(1)}<small>%</small>`;
    cpuDetail.textContent = `${data.cpu_count} logical processors`;
    memoryReading.innerHTML = `${formatMemory(data.memory_used_bytes)}<small> / ${formatMemory(data.memory_total_bytes)}</small>`;
    memoryDetail.textContent = `${formatMemory(data.memory_available_bytes)} available`;
    updateResourceMeter(cpuMeter, cpuBar, cpu);
    updateResourceMeter(memoryMeter, memoryBar, memoryPercent);
    updateResourceChart(cpuChart, cpuHistory, cpu);
    updateResourceChart(memoryChart, memoryHistory, memoryPercent);
    resourcesBadge.classList.remove("offline");
    resourcesBadge.innerHTML = "<i></i> LIVE";
    resourcesUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    resourcesBadge.classList.add("offline");
    resourcesBadge.innerHTML = "<i></i> UNAVAILABLE";
    topResourcesState.textContent = "UNAVAILABLE";
    topResourcesState.classList.add("offline");
    resourcesUpdated.textContent = error.message;
  } finally {
    resourceRequestPending = false;
  }
}

async function refreshLogs() {
  if (logRequestPending) return;
  logRequestPending = true;
  try {
    const response = await fetch("/api/logs", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not read console output");

    streamBadge.classList.toggle("offline", !data.available);
    streamBadge.innerHTML = data.available ? "<i></i> LIVE" : "<i></i> OFFLINE";
    const output = data.available ? data.logs : "The Paper tmux console is not running.";
    if (output !== lastLogs) {
      const shouldFollow = consoleBody.scrollHeight - consoleBody.scrollTop - consoleBody.clientHeight < 70;
      consoleOutput.textContent = output || "Waiting for console output…";
      lastLogs = output;
      if (shouldFollow) consoleBody.scrollTop = consoleBody.scrollHeight;
      lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    }
  } catch (error) {
    streamBadge.classList.add("offline");
    streamBadge.innerHTML = "<i></i> UNAVAILABLE";
    consoleOutput.textContent = error.message;
    lastUpdated.textContent = "Could not refresh";
  } finally {
    logRequestPending = false;
  }
}

refreshStatus();
refreshLogs();
refreshPlayers();
refreshResources();
window.setInterval(refreshStatus, 3000);
window.setInterval(refreshLogs, 1200);
window.setInterval(refreshPlayers, 3000);
window.setInterval(refreshResources, 2000);
