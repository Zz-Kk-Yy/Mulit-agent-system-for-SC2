const STATE_URL = new URL("../dashboard_state.json", window.location.href);
const HERO_PRIORITY = ["player_relative", "unit_type", "height_map", "unit_density", "unit_hit_points"];
const STAGE_LABELS = {
  early: "前期",
  mid: "中期",
  late: "后期",
};
const CATEGORY_LABELS = {
  attack: "进攻",
  defend: "防御",
  gather: "采集",
  build: "建造",
  train: "训练",
  scout: "侦察",
  other: "其他",
};

function formatClock(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function resolveAsset(path, marker) {
  if (!path) return "";
  const cleanPath = String(path).replace(/\\/g, "/").replace(/^\/+/, "");
  const url = new URL(`../${cleanPath}`, window.location.href);
  url.searchParams.set("t", marker || Date.now());
  return url.toString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function chooseHeroFeature(features) {
  if (!features.length) return null;
  for (const name of HERO_PRIORITY) {
    const match = features.find((feature) => feature.name === name);
    if (match) return match;
  }
  return features[0];
}

async function fetchState() {
  const url = new URL(STATE_URL);
  url.searchParams.set("t", Date.now());
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`state file unavailable (${response.status})`);
  }
  return response.json();
}

function renderThumbs(features, marker) {
  const thumbGrid = document.getElementById("thumb-grid");
  if (!features.length) {
    thumbGrid.innerHTML = '<div class="empty-inline">暂无特征图卡片。</div>';
    return;
  }

  thumbGrid.innerHTML = features
    .map(
      (feature) => `
        <article class="thumb-card">
          <img src="${resolveAsset(feature.imagePath, marker)}" alt="${escapeHtml(feature.title)}" />
          <div class="thumb-copy">
            <strong>${escapeHtml(feature.title)}</strong>
            <span>${escapeHtml(feature.name)}</span>
          </div>
        </article>
      `
    )
    .join("");
}

function renderCommands(commands) {
  const commandList = document.getElementById("command-list");
  if (!commands.length) {
    commandList.innerHTML = '<div class="empty-inline">暂无待执行指令。</div>';
    return;
  }

  commandList.innerHTML = commands
    .slice(0, 10)
    .map(
      (command) => `
        <article class="command-card">
          <span class="command-index">#${escapeHtml(command.order)}</span>
          <div class="command-copy">
            <strong>${escapeHtml(command.unit || "--")}</strong>
            <p>${escapeHtml(command.action || "--")} -> ${escapeHtml(command.target || "--")}</p>
          </div>
          <span class="command-type ${escapeHtml(command.category || "other")}">${escapeHtml(
            CATEGORY_LABELS[command.category] || command.category || "其他"
          )}</span>
        </article>
      `
    )
    .join("");
}

function renderState(state) {
  const marker = state.updatedAt || String(Date.now());
  const features = state?.latestFeatureFrame?.features ?? [];
  const heroFeature = chooseHeroFeature(features);
  const currentMessage = state?.messages?.latest || state?.lastMessage;
  const heroImage = document.getElementById("hero-image");
  const heroEmpty = document.getElementById("hero-empty");
  const liveBadge = document.getElementById("live-badge");

  document.getElementById("updated-at").textContent = formatClock(state.updatedAt);
  document.getElementById("stat-start").textContent = state?.game?.startLocation || "--";
  document.getElementById("stat-stage").textContent = STAGE_LABELS[state?.game?.gameStage] || "--";
  document.getElementById("stat-time").textContent = state?.game?.timeFormatted || "--:--";
  document.getElementById("stat-supply").textContent = `${state?.game?.supplyUsed ?? 0} / ${state?.game?.supplyCap ?? 0}`;
  document.getElementById("stat-workers").textContent = String(state?.game?.workerCount ?? 0);
  document.getElementById("stat-army").textContent = String(state?.game?.armyCount ?? 0);

  liveBadge.textContent = state?.status === "running" ? "运行中" : "空闲";
  liveBadge.className = `badge ${state?.status === "running" ? "live" : "idle"}`;

  if (heroFeature) {
    heroImage.src = resolveAsset(heroFeature.imagePath, marker);
    heroImage.alt = heroFeature.title || "主特征图";
    heroImage.style.display = "block";
    heroEmpty.style.display = "none";
    document.getElementById("hero-title").textContent = heroFeature.title || "特征画面";
    document.getElementById("hero-meta").textContent = `${state?.latestFeatureFrame?.time_formatted || "--"} / ${heroFeature.name}`;
  } else {
    heroImage.style.display = "none";
    heroEmpty.style.display = "grid";
    document.getElementById("hero-title").textContent = "等待帧数据";
    document.getElementById("hero-meta").textContent = "暂无帧数据";
  }

  document.getElementById("message-title").textContent = currentMessage?.title || "暂无消息";
  document.getElementById("message-content").textContent =
    currentMessage?.content || "等待下一条大模型回复...";

  renderThumbs(features, marker);
  renderCommands(state?.commandSequence ?? []);
}

async function refresh() {
  try {
    const state = await fetchState();
    renderState(state);
  } catch (error) {
    console.error(error);
    document.getElementById("message-title").textContent = "状态错误";
    document.getElementById("message-content").textContent =
      "无法加载 dashboard_state.json。";
  }
}

refresh();
window.setInterval(refresh, 1200);
