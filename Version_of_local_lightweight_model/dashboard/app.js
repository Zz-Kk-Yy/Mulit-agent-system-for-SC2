const STATE_URL = new URL("../dashboard_state.json", window.location.href);
/** 与 launch_support_stack.py 默认 --ask-port（SWARMBRAIN_ASK_PORT，默认 8011）一致 */
const ASK_API_BASE = "http://127.0.0.1:8011";
const DEERFLOW_RISK_API_URL = `${ASK_API_BASE}/api/risk/refresh`;
const ASK_API_URL = `${ASK_API_BASE}/api/ask`;
const FORCE_LLM_ADVICE_API_URL = `${ASK_API_BASE}/api/force/llm-advice`;
const STRIKE_TARGETS_ADVICE_API_URL = `${ASK_API_BASE}/api/advice/strike-targets`;
const KILLCHAIN_PLANS_API_URL = `${ASK_API_BASE}/api/killchain/plans`;

const CHANNELS = [
  { key: "latest", label: "方案生成智能体" },
  { key: "parser", label: "指令解析" },
];

const AGENT_VIEWS = [
  { key: "planner", label: "方案生成" },
];

const CATEGORY_LABELS = {
  attack: "进攻",
  defend: "防御",
  gather: "采集",
  build: "建造",
  train: "训练",
  scout: "侦察",
  tech: "科技",
  other: "其他",
};

const TASK_PARTITION_LABELS = {
  all: "全部",
  strike: "打击",
  detect: "探测",
  defense: "防御",
  support: "保障",
};

const MINIMAP_MAPPING_DEFAULTS = {
  offsetX: 0.5,
  offsetY: -3.5,
  overlayScale: 1.14,
  baseScaleX: 0.94,
  baseScaleY: 0.9,
};

// SC2 单元射程表：[匹配正则, 射程百分比(以190格地图宽度为基准)]
// 侦查类=视野范围，打击/防御类=攻击范围
const UNIT_RANGE_TABLE = [
  // ── 虫族 ──
  [/王虫|overlord/i,           6.0],  // 11格视野
  [/监察者|overseer/i,         7.0],  // 13格视野+探测
  [/腐化者|corruptor/i,        4.5],  // 8格
  [/龙爪虫|broodlord/i,        5.0],  // 9格
  [/毒爆虫|banelings/i,        0.5],  // 近战爆炸
  [/棘刺爬行者|spine.?crawler/i, 3.5],// 7格
  [/孢子爬行者|spore.?crawler/i, 3.5],// 7格
  [/蟑螂|roach/i,              2.5],  // 4格
  [/潜伏者|lurker/i,           5.0],  // 9格
  [/感染者|infestor/i,         4.0],  // 7格
  // ── 人族 ──
  [/渡鸦|raven/i,              7.5],  // 14格视野
  [/幽灵|ghost/i,              5.5],  // 10格
  [/维京|viking/i,             5.0],  // 9格
  [/攻城坦克|siege.?tank/i,    6.8],  // 13格(攻城模式)
  [/掠夺者|reaper/i,           3.0],  // 5格
  [/陆战队员|marine/i,         3.0],  // 5格
  [/狙击手|sniper/i,           5.0],  // 9格
  [/导弹塔|missile.?turret/i,  4.0],  // 7格
  [/行星要塞|planetary/i,      3.5],  // 6格
  [/碉堡|bunker/i,             3.0],  // 6格(+陆战队员射程)
  // ── 星灵 ──
  [/观察者|observer/i,         7.0],  // 11格视野+探测
  [/先知|oracle/i,             7.0],  // 11格视野+探测
  [/追猎者|stalker/i,          3.5],  // 6格
  [/巨像|colossus/i,           5.0],  // 9格
  [/不朽者|immortal/i,         3.5],  // 6格
  [/虚空辉光舰|void.?ray/i,    3.0],  // 6格
  [/风暴舰|carrier/i,          5.0],  // 8格(拦截机)
  [/光子炮台|photon.?cannon/i, 3.5],  // 7格
];

// 根据单元名或角色返回射程百分比
function getUnitRangePct(name, role) {
  const n = String(name || "");
  for (const [pattern, pct] of UNIT_RANGE_TABLE) {
    if (pattern.test(n)) return pct;
  }
  if (role === "scout")  return 6.0;
  if (role === "strike") return 3.5;
  if (role === "defend") return 4.0;
  return 0;
}

const MINIMAP_RESOURCE_POINTS = [
  ["A1", 29.5, 65.5],
  ["A2", 35.5, 34.5],
  ["A3", 56.5, 65.5],
  ["A4", 63.5, 26.5],
  ["A5", 80.5, 66.5],
  ["A6", 98.5, 26.5],
  ["A7", 129.5, 28.5],
  ["A8", 33.5, 105.5],
  ["B1", 154.5, 114.5],
  ["B2", 148.5, 145.5],
  ["B3", 127.5, 114.5],
  ["B4", 120.5, 153.5],
  ["B5", 103.5, 113.5],
  ["B6", 85.5, 153.5],
  ["B7", 54.5, 151.5],
  ["B8", 150.5, 74.5],
];

const UNIT_ASSIGN_PRESETS = {
  strike: ["刺蛇", "蟑螂", "破坏者", "飞龙", "陆战队员"],
  detect: ["监察王虫", "王虫", "渡鸦", "先知", "侦测器"],
  defense: ["脊针爬虫", "孢子爬虫", "地堡", "导弹塔", "护盾电池"],
  support: ["工蜂", "SCV", "探机", "女王", "医疗运输机"],
};

/** 与 dashboard_state._cn_unit 一致：去空格/下划线后查表，覆盖 API 大写无空格类型名 */
function normalizeSc2Token(s) {
  return String(s || "")
    .toLowerCase()
    .trim()
    .replace(/[\s_\-]+/g, "");
}

const SC2_UNIT_CN = {
  drone: "工蜂", overlord: "王虫", zergling: "跳虫", baneling: "毒爆虫",
  roach: "蟑螂", ravager: "破坏者", hydralisk: "刺蛇", infestor: "感染虫",
  mutalisk: "飞龙", corruptor: "腐化者", viper: "飞蛇", ultralisk: "雷兽",
  "brood lord": "巢虫领主", queen: "女王", larva: "幼虫",
  hatchery: "孵化场", lair: "虫穴", hive: "蜂巢", extractor: "提炼厂",
  "spawning pool": "孵化池", "evolution chamber": "进化腔",
  "roach warren": "蟑螂繁殖场", "baneling nest": "爆虫巢穴",
  "hydralisk den": "刺蛇巢穴", "infestation pit": "感染深渊",
  spire: "尖塔", "greater spire": "巨型尖塔",
  "ultralisk cavern": "雷兽窟", "spine crawler": "脊针爬虫",
  "spore crawler": "孢子爬虫", "nydus network": "虫道网络",
  "nydus worm": "坑道虫", "creep tumor": "菌毯肿瘤",
  marine: "陆战队员", marauder: "掠夺者", reaper: "死神", ghost: "幽灵",
  hellion: "地狱火", hellbat: "地狱蝙蝠", widowmine: "寡妇雷",
  cyclone: "旋风", "siege tank": "攻城坦克", thor: "雷神",
  viking: "维京战机", medivac: "医疗运输机", liberator: "解放者",
  raven: "渡鸦", banshee: "女妖", battlecruiser: "战列巡航舰", scv: "SCV",
  zealot: "狂热者", stalker: "追猎者", sentry: "哨兵",
  adept: "使徒", "high templar": "高阶圣堂武士", colossus: "巨像",
  carrier: "航母", phoenix: "凤凰", observer: "侦测器",
  probe: "探机", nexus: "星灵枢纽", gateway: "传送门",
  "command center": "指挥中心", "supply depot": "补给站", bunker: "地堡",
  barracks: "兵营", factory: "重工厂", starport: "星港",
  missileturret: "导弹塔", refinery: "精炼厂", "engineering bay": "工程站",
  armory: "军械库", "ghost academy": "幽灵军校", "fusion core": "聚变芯体",
  "orbital command": "轨道指挥部", "planetary fortress": "行星要塞",
  "sensor tower": "感应塔", assimilator: "吸纳仓", pylon: "水晶塔",
  cyberneticscore: "控制芯核", roboticsfacility: "机械台", stargate: "星门",
  roboticsbay: "机械研究所", "fleet beacon": "舰队航标",
  "twilight council": "光影议会", "dark shrine": "黑暗圣坛",
  "shield battery": "护盾电池", "photon cannon": "光子炮台",
  "warp prism": "折跃棱镜", immortal: "不朽者", disruptor: "干扰者",
  voidray: "虚空辉光舰", oracle: "先知", tempest: "风暴战舰",
  egg: "虫卵", banelingcocoon: "毒爆虫茧", ravagercocoon: "破坏者茧",
  supplydepotlowered: "补给站（收起）", factorytechlab: "重工厂科技实验室",
  factoryreactor: "重工厂反应堆", barrackstechlab: "兵营科技实验室",
  barracksreactor: "兵营反应堆", starporttechlab: "星港科技实验室",
  starportreactor: "星港反应堆", helliontank: "地狱火坦克",
  overseer: "监察王虫", swarmhostmp: "虫群宿主", lurker: "潜伏者",
  lurkerden: "潜伏者巢穴", nyduscanal: "虫道网络",
};

const SC2_UNIT_LOOKUP = Object.create(null);
for (const [k, v] of Object.entries(SC2_UNIT_CN)) {
  const n = normalizeSc2Token(k);
  if (n) SC2_UNIT_LOOKUP[n] = v;
}

const SC2_ACTION_CN = {
  build: "建造", train: "训练", morph: "变形",
  attack: "进攻", "attack move": "攻击前进",
  move: "移动", scout: "侦察", patrol: "巡逻",
  "hold position": "驻守", "hold": "驻守",
  "gather minerals": "采矿", "gather gas": "采气",
  gather: "采集资源", "collect minerals": "采矿",
  defend: "防守", retreat: "撤退", rally: "集结",
  reinforce: "增援", expand: "扩张",
  produce: "生产", research: "研究",
  "research upgrade": "研究升级", upgrade: "升级",
  "plant creep tumor": "铺菌毯肿瘤",
  "spawn creep tumor": "铺菌毯肿瘤",
  "inject larva": "注射幼虫", inject: "注射幼虫",
  "spread creep": "扩展菌毯", transfuse: "输血",
  "burrow": "潜地", "unburrow": "出地",
  "load": "装载", "unload": "卸载",
  "set rally point": "设置集结点",
  "nydus worm": "虫道传送", nydus: "虫道传送",
};

function translateUnit(name) {
  if (name === undefined || name === null || String(name).trim() === "") {
    return name;
  }
  const s = String(name).trim();
  // Handle "UnitName, Location" format like "Drone, B2" or "Spine Crawler, A1"
  const commaIdx = s.indexOf(",");
  const unitPart = commaIdx >= 0 ? s.slice(0, commaIdx).trim() : s;
  const locationPart = commaIdx >= 0 ? s.slice(commaIdx).trim() : "";
  const n = normalizeSc2Token(unitPart);
  const translated = (n && SC2_UNIT_LOOKUP[n])
    ? SC2_UNIT_LOOKUP[n]
    : (SC2_UNIT_CN[unitPart.toLowerCase()] || unitPart);
  return locationPart ? `${translated} ${locationPart}` : translated;
}

function translateAction(action) {
  if (!action) return action;
  const key = action.toLowerCase().trim();
  if (SC2_ACTION_CN[key]) return SC2_ACTION_CN[key];
  for (const [en, zh] of Object.entries(SC2_ACTION_CN)) {
    if (key.startsWith(en)) return zh;
  }
  return action;
}

function buildCnSummary(row) {
  const unit = translateUnit(row.unit);
  const action = translateAction(row.action);
  const target = translateUnit(row.target) || row.target;
  const parts = [unit, action, target].filter(Boolean);
  return parts.join(" → ");
}

function buildCnDetail(row) {
  const parts = [];
  if (row.unit && row.unit !== row.target) parts.push(translateUnit(row.unit));
  if (row.action) parts.push(translateAction(row.action));
  if (row.target) parts.push(translateUnit(row.target));
  return parts.join(" / ");
}

function inferTaskPartitionType(textInput) {
  const text = String(textInput || "").toLowerCase();
  if (
    /attack|harass|strike|压制|打击|进攻|强攻|火力|突击|围剿/.test(text)
  ) {
    return "strike";
  }
  if (
    /scout|detect|侦察|探测|扫描|observer|overlord|overseer|raven|oracle|情报|视野/.test(text)
  ) {
    return "detect";
  }
  if (
    /defend|hold|protect|fallback|retreat|防守|防御|驻守|回撤|护航|封锁|阻击/.test(text)
  ) {
    return "defense";
  }
  return "support";
}

function normalizePlannerModelLine(inputLine) {
  let line = String(inputLine || "").trim();
  line = line.replace(/^#+\s*/, "");
  line = line.replace(/^\d+\s*[).:：]\s*/, "");
  line = line.replace(/^[-*]\s+/, "");
  line = line.replace(/\*\*/g, "");
  line = line.replace(/\s+/g, " ");
  return line.trim();
}

function extractPlannerTasksFromReply(replyText) {
  const source = String(replyText || "");
  const lines = source.split(/\r?\n/);
  const tasks = [];
  let order = 1;
  for (const rawLine of lines) {
    const original = String(rawLine || "").trim();
    if (!original) continue;
    if (/^```/.test(original)) continue;
    if (/^[-=]{3,}$/.test(original)) continue;
    if (/^\{|\}$/.test(original)) continue;
    if (/^['"]?\d+['"]?\s*:/.test(original)) continue;
    if (/\([^)]+\)->\([^)]+\)->\([^)]+\)/.test(original)) continue;
    const modelLine = normalizePlannerModelLine(original);
    if (!modelLine || modelLine.length < 4) continue;
    const type = inferTaskPartitionType(modelLine);
    tasks.push({
      order: order++,
      type,
      typeLabel: TASK_PARTITION_LABELS[type],
      summaryCn: modelLine,
      detailCn: "",
    });
    if (tasks.length >= 28) break;
  }
  return tasks;
}

function deriveUnitMappingForTask(task, ownPoolByType) {
  const type = task?.type && TASK_PARTITION_LABELS[task.type] ? task.type : "support";
  const summary = String(task?.summaryCn || "");
  const normSummary = normalizeSc2Token(summary);
  const pool = Array.isArray(ownPoolByType?.[type]) ? ownPoolByType[type] : [];
  const matched = pool
    .filter((unit) => {
      const rawNorm = normalizeSc2Token(unit.rawName || "");
      const displayNorm = normalizeSc2Token(unit.name || "");
      return (rawNorm && normSummary.includes(rawNorm)) || (displayNorm && normSummary.includes(displayNorm));
    })
    .slice(0, 3);

  let mappedUnits = matched;
  let level = "低";
  if (mappedUnits.length && mappedUnits.some((u) => (u.count || 0) > 0)) {
    level = "高";
  } else {
    mappedUnits = pool.slice(0, 3);
    if (mappedUnits.length && mappedUnits.some((u) => (u.count || 0) > 0)) {
      level = "中";
    } else {
      mappedUnits = (UNIT_ASSIGN_PRESETS[type] || []).slice(0, 3).map((name) => ({
        name,
        rawName: name,
        count: 0,
      }));
      level = mappedUnits.length ? "低" : "中";
    }
  }

  return {
    mappedUnits: mappedUnits.map((u) => ({
      name: u.name,
      label: (u.count || 0) > 0 ? `${u.name} ×${u.count}` : `${u.name}（待编配）`,
    })),
    matchLevel: level,
  };
}

const STAGE_LABELS = {
  early: "前期",
  mid: "中期",
  late: "后期",
};

const CAPABILITY_META = {
  strike: { key: "strike", label: "打击类", icon: "⚔" },
  detect: { key: "detect", label: "探测类", icon: "◉" },
  defense: { key: "defense", label: "防御类", icon: "🛡" },
  support: { key: "support", label: "保障类", icon: "✚" },
};

const CAPABILITY_ORDER = ["strike", "detect", "defense", "support"];

const KILLCHAIN_DIMENSION_META = {
  strike: { key: "strike", label: "打击", icon: "⚔", color: "#ff6b7a" },
  detect: { key: "detect", label: "探测", icon: "◉", color: "#65d8ff" },
  defense: { key: "defense", label: "防御", icon: "🛡", color: "#8ddc8a" },
  support: { key: "support", label: "保障", icon: "✚", color: "#d6a8ff" },
};

const KILLCHAIN_DIMENSION_ORDER = ["strike", "detect", "defense", "support"];

const KILLCHAIN_STAGE_ORDER = [
  { key: "discover", label: "侦察发现" },
  { key: "locate", label: "目标定位" },
  { key: "track", label: "持续跟踪" },
  { key: "designate", label: "目标指示" },
  { key: "strike", label: "火力打击" },
  { key: "assess", label: "效果评估" },
];
const KILLCHAIN_LANE_ORDER = ["detect", "defense", "support", "strike"];

function capabilityNodeType(capability) {
  if (capability === "detect") return "侦查单元";
  if (capability === "defense") return "支援单元";
  if (capability === "support") return "支援单元";
  return "打击单元";
}

function buildKillChainPoint(location, stageIndex, chainIndex) {
  const point = criticalTargetPoint(location, stageIndex + chainIndex * 11);
  const offsetX = (chainIndex % 3) * 1.9;
  const offsetY = ((stageIndex % 2) * 1.4) - 0.7;
  return {
    x: Math.max(8, Math.min(92, point.x + offsetX)),
    y: Math.max(8, Math.min(92, point.y + offsetY)),
  };
}

const CRITICAL_TARGET_CATEGORY_META = {
  detect: { key: "detect", label: "探测目标", shortLabel: "探测", color: "#ffbd5c" },
  strike: { key: "strike", label: "打击目标", shortLabel: "打击", color: "#ff5f6d" },
  defense: { key: "defense", label: "防御目标", shortLabel: "防御", color: "#ff8f4d" },
  support: { key: "support", label: "支撑目标", shortLabel: "支撑", color: "#d98cff" },
};

const CRITICAL_TARGET_CATEGORY_ORDER = ["strike", "detect", "defense", "support"];

function classifyCapability(name, kind, action) {
  const n = normalizeSc2Token(name);
  // Broodlord 英文 token 含子串 "overlord"，不能与人口王虫 Overlord 共用同一子串规则，否则主力空军会被归入保障/探测。
  if (n === "broodlord" || n.endsWith("broodlord")) {
    return "strike";
  }
  const actionText = String(action || "").toLowerCase();
  const detectTokens = ["observer", "overseer", "raven", "oracle", "sensor", "scout"];
  const defenseTokens = ["spine", "spore", "bunker", "turret", "cannon", "battery", "fortress"];
  const supportTokens = [
    "drone", "scv", "probe", "queen", "medivac", "overlord", "warpprism",
    "hatchery", "lair", "hive", "extractor", "refinery", "assimilator", "commandcenter", "nexus",
  ];
  if (detectTokens.some((token) => n.includes(token)) || actionText.includes("侦察") || actionText.includes("扫描")) {
    return "detect";
  }
  if ((kind === "structure" && defenseTokens.some((token) => n.includes(token))) || actionText.includes("驻防")) {
    return "defense";
  }
  if (supportTokens.some((token) => n.includes(token)) || actionText.includes("采集") || actionText.includes("生产")) {
    return "support";
  }
  return "strike";
}

/** 与 dashboard_state._is_structure_name 同步：composition 兜底用，区分建筑/单位以分类能力维度 */
const STRUCTURE_KIND_HINTS = [
  "hatchery", "lair", "hive", "extractor", "pool", "chamber", "warren",
  "nest", "den", "pit", "spire", "cavern", "crawler", "network", "worm",
  "nexus", "pylon", "commandcenter", "orbitalcommand", "planetaryfortress",
  "supplydepot", "bunker", "turret", "barracks", "factory", "starport",
  "gateway", "warpgate", "cyberneticscore", "roboticsfacility", "stargate",
  "roboticsbay", "fleetbeacon", "twilightcouncil", "darkshrine",
  "shieldbattery", "photoncannon", "assimilator", "refinery", "armory",
  "engineeringbay", "fusioncore", "ghostacademy", "sensortower", "techlab",
  "reactor",
];

function isLikelyStructureUnitType(name) {
  const key = normalizeSc2Token(name);
  if (!key) return false;
  return STRUCTURE_KIND_HINTS.some((term) => key.includes(term));
}

/** 仅在 forceDetails 为空时用于面板；语义与 dashboard_state._derive_workbench 中 composition 兜底一致 */
function compositionToForceRows(composition, side) {
  if (!Array.isArray(composition)) return [];
  const isEnemy = side === "enemy";
  const out = [];
  for (const row of composition) {
    if (!row || typeof row !== "object") continue;
    const code = String(row.name || row.code || "").trim();
    const count = Number(row.count) || 0;
    if (!code || count <= 0) continue;
    const kind = isLikelyStructureUnitType(code) ? "structure" : "unit";
    out.push({
      name: code,
      count,
      location: "--",
      action: isEnemy ? "当前可见" : "待命/集结",
      kind,
    });
  }
  return out;
}

function normalizeForceRows(rows, side) {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((row) => row && typeof row === "object")
    .map((row) => {
      const name = String(row.name || row.code || "UNKNOWN");
      const kind = String(row.kind || "").toLowerCase() === "structure" ? "structure" : "unit";
      const location = String(row.location || "--").toUpperCase();
      const action = String(row.action || "待命/集结");
      const count = Math.max(1, Number(row.count) || 0);
      const capability = classifyCapability(name, kind, action);
      const xPercent = Number(row.xPercent);
      const yPercent = Number(row.yPercent);
      return {
        side,
        name: translateUnit(name),
        kind,
        location,
        action,
        count,
        capability,
        xPercent: Number.isFinite(xPercent) ? xPercent : null,
        yPercent: Number.isFinite(yPercent) ? yPercent : null,
        worldX: row.worldX,
        worldY: row.worldY,
      };
    });
}

function criticalTargetPoint(location, index) {
  const safeIdx = Math.max(0, Number(index) || 0);
  const value = String(location || "").toUpperCase();
  const match = value.match(/([A-Z])\s*[-_ ]?(\d+)/);
  if (match) {
    const letterIdx = Math.max(0, Math.min(5, match[1].charCodeAt(0) - 65));
    const number = Math.max(1, Math.min(6, Number(match[2]) || 1));
    let x = 14 + letterIdx * 14 + ((number % 2) * 5);
    let y = 16 + (number - 1) * 13;
    // 同格多块目标否则会完全重叠；按索引做小半径圆周偏移，沙盘瞄准才能随点击变化
    const rad = safeIdx * 1.88496; /* 108° · idx，错开更明显 */
    const ring = Math.floor(safeIdx / 6) % 2;
    const r = 2.2 + ring * 2.4;
    x += Math.cos(rad) * r;
    y += Math.sin(rad) * r;
    return {
      x: Math.max(8, Math.min(92, Math.round(x * 10) / 10)),
      y: Math.max(8, Math.min(92, Math.round(y * 10) / 10)),
    };
  }
  let hash = 0;
  for (const char of `${value || "TARGET"}|${safeIdx}`) {
    hash = (hash * 31 + char.charCodeAt(0)) % 997;
  }
  return {
    x: 12 + (hash % 72),
    y: 14 + ((hash * 7) % 68),
  };
}

function formatElapsed(ms) {
  const safeMs = Math.max(0, Number(ms) || 0);
  const totalSeconds = Math.floor(safeMs / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒前`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} 分 ${seconds} 秒前`;
}

const FEATURE_TITLE_LABELS = {
  height_map: "地形高度",
  visibility_map: "视野分布",
  creep: "菌毯覆盖",
  camera: "镜头范围",
  player_relative: "敌我关系",
  unit_type: "单位类型",
  unit_hit_points: "单位血量",
  unit_density: "单位密度",
};

const FEATURE_NAME_LABELS = {
  height_map: "地形高度",
  visibility_map: "视野分布",
  creep: "菌毯覆盖",
  camera: "镜头范围",
  player_relative: "敌我关系",
  unit_type: "单位类型",
  unit_hit_points: "单位血量",
  unit_density: "单位密度",
};

const FEATURE_FOOTER_LABELS = {
  "terrain height": "地形高度说明",
  "visibility states": "视野状态说明",
  "creep coverage": "菌毯覆盖说明",
  "camera window": "镜头范围说明",
  "player relation": "敌我关系说明",
  "unit type IDs": "单位类型说明",
  "unit health": "单位血量说明",
  "unit density": "单位密度说明",
};

const FEATURE_NOTE_LABELS = {
  "dark=low, bright=high": "越暗表示地势越低，越亮表示地势越高",
  "dark=fog, bright=visible": "越暗表示战争迷雾，越亮表示当前可见",
  "purple=creep active": "紫色区域表示当前菌毯覆盖",
  "cyan=current viewport": "青色区域表示当前镜头覆盖范围",
  "1=self, 2=ally, 3=neutral, 4=enemy": "1=我方，2=盟友，3=中立，4=敌方",
  "same ID -> same class": "相同 ID 代表同类单位",
  "brighter = more HP": "越亮表示血量越高",
  "brighter = more overlap": "越亮表示单位越密集",
};

const FEATURE_SAMPLE_CAPTIONS = {
  height_map: "高亮区域为高地坡口，暗区为低洼地带，辅助判断机动路径与地形优势位。",
  visibility_map: "亮区为当前可见视野，暗区为战争迷雾，用于评估侦察覆盖与信息盲区。",
  creep: "紫色区域为虫族菌毯控制范围，边缘走向标记当前推进方向与扩张前沿。",
  camera: "青色区域为当前镜头窗口在全局地图中的对应位置。",
  player_relative: "色板标注我方/盟军/中立/敌方，前线接触带与势力分布可直接判读。",
  unit_type: "按兵种类别分色显示，亮斑密集区为当前交战点或兵力集结位置。",
  unit_hit_points: "越亮单位血量越高，暗弱目标为高优先级打击对象。",
  unit_density: "亮度反映单位密度，高亮区为主力交战区或前压分矿方向。",
};

const MESSAGE_TITLE_LABELS = {
  latest: "最新规划",
  macro: "宏观规划",
  building: "建造规划",
  attack: "进攻研判",
};
const PLANNER_REPLY_COLLAPSE_LINES = 6;

function createEmptyState() {
  return {
    updatedAt: "",
    status: "idle",
    gameView: {
      mode: "external",
      embedUrl: "",
    },
    game: {
      startLocation: "",
      timeFormatted: "",
      iteration: 0,
      gameStage: "early",
      armyCount: 0,
      workerCount: 0,
      supplyUsed: 0,
      supplyCap: 0,
      pendingCommandCount: 0,
      featureSession: "",
      ownForceDetails: [],
      enemyVisibleDetails: [],
      resourceMarkers: [],
      forceCollaborationSuggestion: {
        currentStrategyCommand: "",
        targetType: "",
        recommendationReason: "",
        protectionTarget: "",
        strikeTarget: "",
      },
    },
    latestFeatureFrame: null,
    mapLayout: {},
    messages: {
      latest: null,
      macro: null,
      building: null,
      attack: null,
    },
    lastMessage: null,
    commandSequence: [],
    executedCommands: [],
    agentWorkbench: {
      intentRisk: {
        title: "行为意图与风险识别",
        agentName: "行为意图与风险识别智能体",
        updatedAt: "",
        status: "idle",
        summary: "",
        behaviorIntent: [],
        riskRecognition: [],
      },
      situationAwareness: {
        title: "态势感知智能体",
        agentName: "态势感知智能体",
        updatedAt: "",
        status: "idle",
        summary: "",
        resourcePanel: {
          ownMinerals: 0,
          ownVespene: 0,
          ownSupply: "0/0",
          ownWorkers: 0,
          ownEconomyIndex: 0,
        },
        forcePanel: {
          ownArmyCount: 0,
          enemyVisibleUnits: 0,
          enemyStructures: 0,
          gameStage: "early",
        },
        healthPanel: {
          ownHpTotal: 0,
        },
        ownUnitList: [],
        enemyVisibleUnitList: [],
        ownTopUnits: [],
        enemyTopUnits: [],
        insights: [],
      },
      formationAdvisor: {
        title: "单位布势智能体",
        agentName: "布势调整智能体",
        updatedAt: "",
        status: "idle",
        summary: "",
        situationConclusion: "",
        matchScore: 0,
        matchLabel: "",
        deploymentGoal: "",
        adjustments: [],
        evidence: [],
        sharedMemoryKeys: [],
      },
      spacetimeChain: {
        title: "时空事件链智能体",
        agentName: "时空事件链智能体",
        updatedAt: "",
        status: "idle",
        summary: "",
        headline: "",
        evolutionNarrative: "",
        globalSituation: null,
        scenarioBranches: [],
        timeline: [],
        causalLinks: [],
        hotspots: [],
        insights: [],
      },
    },
    minimapOverlay: [],
  };
}

const PANEL_LAYOUT_STORAGE_KEY = "swarmbrain-dashboard-freeform-layout-v4";
const MINIMAP_BASELINE_STORAGE_KEY = "swarmbrain-dashboard-minimap-baseline-v1";

function readPanelLayout() {
  try {
    const raw = localStorage.getItem(PANEL_LAYOUT_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writePanelLayout(layout) {
  try {
    localStorage.setItem(PANEL_LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch (e) {
    console.warn("dashboard panel layout save failed", e);
  }
}

function readMinimapBaseline() {
  try {
    const raw = localStorage.getItem(MINIMAP_BASELINE_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeMinimapBaseline(minimap) {
  if (!minimap?.imagePath) {
    return;
  }
  try {
    localStorage.setItem(
      MINIMAP_BASELINE_STORAGE_KEY,
      JSON.stringify({
        imagePath: minimap.imagePath,
        title: minimap.title || "底图参考",
        path: minimap.path || "",
      }),
    );
  } catch (e) {
    console.warn("minimap baseline save failed", e);
  }
}

/** 用于判断磁盘上的 state 是否在对局过程中被真正刷新（避免 serve_dashboard 重启后仍显示旧 JSON） */
function fingerprintDashboardState(raw) {
  if (!raw || typeof raw !== "object") {
    return "";
  }
  const g = raw.game || {};
  const lf = raw.latestFeatureFrame || {};
  const feats = lf.features || [];
  return [
    String(raw.updatedAt || ""),
    String(raw.status || ""),
    String(g.iteration ?? ""),
    String(g.featureSession || ""),
    String(g.timeFormatted || ""),
    String((raw.commandSequence || []).length),
    String(feats.length),
    String(lf.game_loop ?? ""),
    String(lf.updatedAt || ""),
  ].join("\u001f");
}

/**
 * liveUiArmed === false：不信任磁盘 JSON，仅展示本地缓存的小地图底图路径，无任何对局/特征/指令数据。
 * liveUiArmed === true：与磁盘同步；running 时完整展示并更新底图缓存；idle 时按原规则收缩。
 */
function prepareDisplayState(raw, liveUiArmed) {
  const empty = createEmptyState();
  if (!raw || typeof raw !== "object") {
    return empty;
  }

  if (!liveUiArmed) {
    const baseline = readMinimapBaseline();
    return {
      ...empty,
      updatedAt: "",
      status: "idle",
      game: { ...empty.game },
      commandSequence: [],
      minimapOverlay: [],
      messages: { ...empty.messages },
      lastMessage: null,
      agentWorkbench: JSON.parse(JSON.stringify(empty.agentWorkbench)),
      latestFeatureFrame:
        baseline && baseline.imagePath
          ? {
              features: [],
              minimap: { ...baseline },
              game_loop: "",
              time_formatted: "",
              updatedAt: "",
            }
          : null,
    };
  }

  const running = raw.status === "running";

  if (running) {
    const out = {
      ...empty,
      ...raw,
      game: { ...empty.game, ...(raw.game || {}) },
      messages: { ...empty.messages, ...(raw.messages || {}) },
    };
    out.agentWorkbench = raw.agentWorkbench
      ? JSON.parse(JSON.stringify(raw.agentWorkbench))
      : JSON.parse(JSON.stringify(empty.agentWorkbench));
    if (out.latestFeatureFrame?.minimap?.imagePath) {
      writeMinimapBaseline(out.latestFeatureFrame.minimap);
    }
    return out;
  }

  const baseline = readMinimapBaseline();
  const serverMini = raw.latestFeatureFrame?.minimap;
  const minimap = serverMini?.imagePath ? serverMini : baseline;

  const out = {
    ...empty,
    ...raw,
    status: "idle",
    game: { ...empty.game },
    commandSequence: [],
    minimapOverlay: [],
    messages: { ...empty.messages },
    lastMessage: null,
    agentWorkbench: JSON.parse(JSON.stringify(empty.agentWorkbench)),
  };
  out.latestFeatureFrame =
    minimap && minimap.imagePath
      ? {
          ...(raw.latestFeatureFrame || {}),
          features: [],
          minimap,
        }
      : null;
  return out;
}

function formatClock(value) {
  if (!value) {
    return "--";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function clamp(value, min = 0, max = 100) {
  const numeric = Number(value);
  const safe = Number.isFinite(numeric) ? numeric : 0;
  return Math.max(min, Math.min(max, Math.round(safe)));
}

function scoreToLevel(value) {
  const score = clamp(value);
  if (score >= 80) return "强";
  if (score >= 60) return "可行";
  return "受限";
}

function normalizeLevelWord(level) {
  const raw = String(level || "").trim();
  if (!raw) return "可行";
  if (raw === "高") return "强";
  if (raw === "中") return "可行";
  if (raw === "低") return "受限";
  return raw;
}

function ratio(part, whole) {
  const denominator = Number(whole) || 0;
  if (!denominator) {
    return 0;
  }
  return (Number(part) || 0) / denominator;
}

function getChannelLabel(channel) {
  return CHANNELS.find((item) => item.key === channel)?.label || "最新";
}

function getMessageDisplayTitle(message) {
  if (!message) {
    return "绛夊緟浠诲姟瑙勫垝";
  }
  return MESSAGE_TITLE_LABELS[message.channel] || message.title || "浠诲姟瑙勫垝缁撴灉";
}

function normalizeFreshness(updatedAt) {
  if (!updatedAt) {
    return 40;
  }

  const timestamp = new Date(updatedAt).getTime();
  if (!Number.isFinite(timestamp)) {
    return 40;
  }

  const diffSeconds = Math.max(0, (Date.now() - timestamp) / 1000);
  if (diffSeconds <= 2) {
    return 100;
  }
  if (diffSeconds <= 8) {
    return 88;
  }
  if (diffSeconds <= 20) {
    return 72;
  }
  if (diffSeconds <= 45) {
    return 58;
  }
  return 40;
}

function keywordScore(text, keywords, weight = 8) {
  const source = String(text || "").toLowerCase();
  let score = 0;

  for (const keyword of keywords) {
    if (source.includes(String(keyword).toLowerCase())) {
      score += weight;
    }
  }

  return clamp(score);
}

function countCategories(commands) {
  const counts = {
    attack: 0,
    defend: 0,
    gather: 0,
    build: 0,
    train: 0,
    scout: 0,
    tech: 0,
    other: 0,
  };

  for (const command of commands || []) {
    const category = Object.prototype.hasOwnProperty.call(counts, command?.category)
      ? command.category
      : "other";
    counts[category] += 1;
  }

  return counts;
}

function translateFeature(feature) {
  const name = feature?.name || "";
  const footerTitle = feature?.footer_title || "";
  const footerNote = feature?.footer_note || "";

  return {
    ...feature,
    displayTitle: FEATURE_TITLE_LABELS[name] || feature?.title || name || "特征图",
    displayName: FEATURE_NAME_LABELS[name] || name || "--",
    displayFooterTitle: FEATURE_FOOTER_LABELS[footerTitle] || footerTitle || "特征说明",
    displayFooterNote: FEATURE_NOTE_LABELS[footerNote] || footerNote || "等待特征说明",
    sampleCaption:
      FEATURE_SAMPLE_CAPTIONS[name] || "样例：以本帧明暗与分色为判读参考。",
  };
}

function buildPlannerFallback(state, channel) {
  const game = state?.game || createEmptyState().game;
  const commands = state?.commandSequence || [];
  const counts = countCategories(commands);

  return [
    `当前通道：${getChannelLabel(channel)}`,
    `比赛时间：${game.timeFormatted || "--:--"}，阶段：${STAGE_LABELS[game.gameStage] || STAGE_LABELS.early}，出生点：${game.startLocation || "--"}`,
    `军队：${game.armyCount ?? 0}，工人：${game.workerCount ?? 0}，补给：${game.supplyUsed ?? 0}/${game.supplyCap ?? 0}`,
    `待执行指令：${commands.length} 条，进攻 ${counts.attack} / 防御 ${counts.defend} / 采集 ${counts.gather} / 建造 ${counts.build} / 训练 ${counts.train} / 侦察 ${counts.scout} / 科技 ${counts.tech}`,
  ].join("\n");
}

function describeSituation(score, highText, mediumText, lowText) {
  if (score >= 75) {
    return highText;
  }
  if (score >= 55) {
    return mediumText;
  }
  return lowText;
}

function scoreBand(score) {
  if (score >= 75) {
    return "高";
  }
  if (score >= 55) {
    return "中";
  }
  return "低";
}

function deriveSituation(state) {
  const game = state?.game || createEmptyState().game;
  const commands = state?.commandSequence || [];
  const counts = countCategories(commands);
  const totalCommands = Math.max(commands.length, 1);
  const messages = state?.messages || {};
  const allMessages = Object.values(messages).filter(Boolean);
  const allText = allMessages.map((message) => `${message.title || ""}\n${message.content || ""}`).join("\n");

  const supplyRatio = ratio(game.supplyUsed, Math.max(game.supplyCap, 1));
  const armyScore = clamp(ratio(game.armyCount, 60) * 100);
  const workerBalance = clamp(100 - Math.abs((game.workerCount || 0) - 78) * 1.6);
  const stageScore = { early: 38, mid: 68, late: 88 }[game.gameStage] || 38;
  const featureCoverage = clamp(ratio((state?.latestFeatureFrame?.features || []).length, 5) * 100);
  const freshScore = normalizeFreshness(state?.updatedAt);
  const messageCoverage = clamp(ratio(allMessages.length, 4) * 100);

  const attackSignal = keywordScore(
    messages.attack?.content,
    ["true", "attack", "push", "pressure", "timing", "engage", "浼樺娍", "鍘嬪埗"],
    10
  );
  const intelSignal = keywordScore(
    allText,
    ["scout", "intel", "vision", "overlord", "detect", "expand", "expansion", "渚﹀療", "鎯呮姤", "瑙嗛噹", "鎵╁紶"],
    8
  );
  const defenseSignal = keywordScore(
    allText,
    ["defend", "defense", "drop", "risk", "vulnerable", "spine", "spore", "retreat", "闃插尽", "椋庨櫓", "绌烘姇", "瀹堜綇"],
    8
  );
  const assemblySignal = keywordScore(
    allText,
    ["rally", "reinforce", "morph", "hydralisk", "roach", "baneling", "assemble", "闆嗙粨", "琛ュ叺", "鎴愬瀷"],
    7
  );
  const overlaySignal = clamp(Math.min((state?.minimapOverlay || []).length, 5) * 20);

  const strike = clamp(
    armyScore * 0.42 +
      supplyRatio * 100 * 0.18 +
      ratio(counts.attack + counts.train, totalCommands) * 100 * 0.18 +
      attackSignal * 0.12 +
      stageScore * 0.1
  );

  const intel = clamp(
    ratio(counts.scout + counts.gather, totalCommands) * 100 * 0.36 +
      intelSignal * 0.22 +
      freshScore * 0.18 +
      featureCoverage * 0.14 +
      messageCoverage * 0.1
  );

  const defense = clamp(
    ratio(counts.defend + counts.build, totalCommands) * 100 * 0.28 +
      workerBalance * 0.18 +
      defenseSignal * 0.18 +
      freshScore * 0.12 +
      stageScore * 0.12 +
      supplyRatio * 100 * 0.12
  );

  const assembly = clamp(
    armyScore * 0.34 +
      ratio(counts.train + counts.attack + counts.build, totalCommands) * 100 * 0.24 +
      assemblySignal * 0.18 +
      supplyRatio * 100 * 0.14 +
      overlaySignal * 0.1
  );

  const cards = [
    {
      key: "strike",
      title: "打击",
      score: strike,
      tone: "attack",
      band: scoreBand(strike),
      status: describeSituation(strike, "具备主动压制窗口", "可以组织局部施压", "仍需继续补兵扩军"),
      insight: describeSituation(
        strike,
        "兵力与节奏接近发力点。",
        "可以试探压制，暂时不宜全压。",
        "先补兵扩军再推进。"
      ),
    },
    {
      key: "intel",
      title: "情报",
      score: intel,
      tone: "intel",
      band: scoreBand(intel),
      status: describeSituation(intel, "侦察链路完整", "情报覆盖基本够用", "需要补足视野与侦察"),
      insight: describeSituation(
        intel,
        "侦察与特征刷新能支撑判断。",
        "基础情报够用，但深度不足。",
        "优先补视野和侦察。"
      ),
    },
    {
      key: "defense",
      title: "防御",
      score: defense,
      tone: "defense",
      band: scoreBand(defense),
      status: describeSituation(defense, "后方稳态较好", "可以承受中等压力", "需要优先加固后方"),
      insight: describeSituation(
        defense,
        "后方稳态较好，可以承压。",
        "守势基本稳定，但仍有风险。",
        "优先补防御点并收拢部队。"
      ),
    },
    {
      key: "assembly",
      title: "兵种集结",
      score: assembly,
      tone: "assembly",
      band: scoreBand(assembly),
      status: describeSituation(assembly, "部队集结效率高", "部队正在成型", "集结程度偏分散"),
      insight: describeSituation(
        assembly,
        "波次与目标点较集中。",
        "部队正在成型，但衔接偏松。",
        "统一目标点，避免分散。"
      ),
    },
  ];

  const sorted = [...cards].sort((left, right) => right.score - left.score);
  const strongest = sorted[0];
  const weakest = sorted[sorted.length - 1];

  const adviceMap = {
    strike: "建议继续补兵并控制进攻节奏，等优势更明确后再打满正面。",
    intel: "建议优先补侦察单位和路径视野，再决定进攻还是转守。",
    defense: "建议把防御资源向主矿和关键分矿倾斜，先稳住再扩张。",
    assembly: "建议收拢兵力、统一目标点，并让训练与战术目标保持一致。",
  };

  return {
    cards,
    headline: `当前强项：${strongest.title}；补强重点：${weakest.title}`,
    advice: adviceMap[weakest.key] || "继续观察态势并滚动修正指令。",
  };
}

if (!window.Vue) {
  const root = document.getElementById("app");
  if (root) {
    root.innerHTML =
      '<div class="page-shell"><section class="panel"><div class="empty-state"><h3>Vue 加载失败</h3><p>请检查网络连接后刷新页面。</p></div></section></div>';
  }
} else {
  const { createApp } = window.Vue;

  createApp({
    data() {
      return {
        state: createEmptyState(),
        agentViews: AGENT_VIEWS,
        activeAgentView: "planner",
        activeRightLayer: "overview",
        activePlannerTab: "bus",
        activePlannerTaskFilter: "all",
        activePlannerUnitFilter: "all",
        plannerTaskShowAllHistory: false,
        activeChannel: "latest",
        activeFeatureKey: "",
        selectedCriticalTargetKey: "",
        // 关键打击节点：仅当用户点击清单后才在地图上显示瞄准（避免默认常驻）
        criticalAimArmed: false,
        // 当前被“锁定瞄准”的小地图 marker.key（确保只出现一个瞄准形状）
        criticalAimMarkerKey: "",
        activeCriticalTargetCategory: "all",
        criticalCategoryMenuOpen: false,
        selectedKillChainKey: "",
        selectedKillPlanKey: "",
        activeKillChainDimension: "strike",
        cachedCriticalTargetItems: [],
        cachedCriticalTargetSeenAtMs: 0,
        cachedCriticalTargetSeenGameTime: "",
        enemyMainTracker: {
          lastSeenAtMs: 0,
          lastSeenGameTime: "",
          lastSeenCount: 0,
        },
        featureRefreshFlash: false,
        featureFlashTimer: null,
        lastFeatureStamp: "",
        minimapBaseAspect: 1,
        minimapBaseImageScaleX: MINIMAP_MAPPING_DEFAULTS.baseScaleX,
        minimapBaseImageScaleY: MINIMAP_MAPPING_DEFAULTS.baseScaleY,
        minimapOverlayScale: MINIMAP_MAPPING_DEFAULTS.overlayScale,
        minimapOverlayOffsetX: MINIMAP_MAPPING_DEFAULTS.offsetX,
        minimapOverlayOffsetY: MINIMAP_MAPPING_DEFAULTS.offsetY,
        minimapMappingOpen: false,
        // 能力类型高亮过滤：null=全显示，非null=只显示该角色的单元
        minimapHighlightRole: null,
        // 风险模块：当前选中的风险维度（null=不高亮）
        riskHighlightGroupKey: null,
        // ── 各模块沙盘独立配置 ──────────────────────────────────────
        // 每项对应"态势分析系统"中的一个模块；以当前版本为基础，
        // 后续针对特定模块的修改只修改该模块的配置，不影响其余模块。
        moduleMinimapConfigs: {
          awareness:  { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          force:      { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          intent:     { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          risk:       { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          killchain:  { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          critical:   { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          plan:       { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
        },
        // 主页（home-clone / overview）沙盘独立配置
        homeMinimapConfig:    { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
        featureAutoPlay: true,
        featureAutoPlayTimer: null,
        plannerHistory: [],
        plannerLiveTurn: null,
        plannerHistoryReady: false,
        plannerAutoFollow: true,
        plannerSessionStartedAt: Date.now(),
        plannerSeenTurnKeys: Object.create(null),
        plannerTypingTimer: null,
        plannerTypingKey: "",
        plannerTypingIndex: 0,
        plannerExpandedReplies: Object.create(null),
        executedCommands: [],
        commandTrackingReady: false,
        isLoading: true,
        fetchError: "",
        fetchFailureCount: 0,
        isFetchingState: false,
        refreshMs: 1200,
        pollTimer: null,
        stageLabels: STAGE_LABELS,
        panelInteractionCleanups: [],
        panelZCounter: 1000,
        newCommandOrders: new Set(),
        newCommandTimer: null,
        prevCommandOrders: new Set(),
        activeExecutedCommandTab: "all",
        activePendingCommandTab: "all",
        panelLayoutSaveTimer: null,
        viewportScrollStep: 180,
        viewportGrabActive: false,
        viewportGrabCleanup: null,
        deerflowRiskLoading: false,
        deerflowRisk: {
          ok: false,
          thread_id: "",
          mode: "replay",
          summary: "",
          intent: {},
          risk: {},
          response: {},
          stages: {},
          traces: [],
          pipeline_stdout: "",
          errorText: "",
        },
        deerflowHistory: {
          intent: [],
          risk: [],
        },
        deerflowHistoryExpand: {
          intent: Object.create(null),
          risk: Object.create(null),
        },
        askPanel: {
          question: "",
          loading: false,
          errorText: "",
          sentAt: "",
          messages: [],
        },
        /** 为 true 后才信任 dashboard_state.json 中的对局数据（避免仅重启静态服务后仍显示旧内容） */
        liveUiArmed: false,
        /** 首次成功拉取后的指纹；后续任一轮指纹变化则视为对局在写盘，解除冷启动 */
        stateBootstrapFingerprint: null,
        /** 兵力布势：智能体协同建议（ask_api_server /api/force/llm-advice） */
        forceLlmAdviceLoading: false,
        forceLlmAdvice: {
          ok: false,
          sections: [],
          errorText: "",
          answerRaw: "",
          traces: [],
          loadedAt: "",
          source: "",
        },
        /** 关键节点页：大模型打击目标建议 */
        strikeTargetAdviceLoading: false,
        strikeTargetAdvice: {
          ok: false,
          advice: "",
          errorText: "",
          loadedAt: "",
          source: "",
        },
        /** 杀伤链发现：大模型生成作战模式与敌链破坏步骤流 */
        killChainLlmPlansLoading: false,
        killChainLlmPlans: {
          ok: false,
          plans: [],
          errorText: "",
          answerRaw: "",
          traces: [],
          loadedAt: "",
          source: "",
          chainSignature: "",
        },
        riskConsoleShowPipeline: false,
        /** 对齐「部署位置明细」卡片外框高度与「双方能力结构」（ResizeObserver） */
        forceCapResizeObserver: null,
        /** 系统架构：战法库顶 ⟿ 意图推理卡底的 SVG path（画布像素坐标） */
        sysArchKbMacroPathD: "",
        /** 决策四卡：相邻竖边中点之间的线段 d（最多三条） */
        sysArchDecisionLinkDs: [],
        /** 多阶段战术分解卡底 ⟿ 战术战法评估卡顶 */
        sysArchBtFeedbackPathD: "",
        sysArchKbMacroResizeObserver: null,
      };
    },
    computed: {
      refreshSeconds() {
        return this.refreshMs / 1000;
      },
      game() {
        return this.state?.game ?? createEmptyState().game;
      },
      stageLabel() {
        return STAGE_LABELS[this.game.gameStage] || STAGE_LABELS.early;
      },
      minimapFrame() {
        return this.state?.latestFeatureFrame?.minimap ?? null;
      },
      minimapMetaLine() {
        if (!this.minimapFrame) {
          return "等待小地图同步";
        }
        if (!this.liveUiArmed) {
          return "底图预览 · 等待对局写盘同步";
        }
        if (!this.state?.latestFeatureFrame) {
          return "等待小地图同步";
        }
        return `${this.state.latestFeatureFrame.time_formatted || "--"} / 回合 ${this.state.latestFeatureFrame.game_loop ?? "--"}`;
      },
      minimapLegend() {
        return [
          { label: "我方单位", note: "绿色：你的单位与建筑", className: "self" },
          { label: "敌方单位", note: "红色：敌对单位与建筑", className: "enemy" },
          { label: "矿区资源", note: "黄色：地图上的中立晶体矿/资源簇位置", className: "resource" },
          { label: "侦察/探测", note: "椭圆：探测、侦察、反隐相关单位", className: "shape-scout" },
          { label: "防御单元", note: "盾形：防御建筑或防守单位", className: "shape-defend" },
          { label: "打击单元", note: "三角：攻击与突击单位", className: "shape-strike" },
          { label: "保障单元", note: "十字：工蜂、后勤、治疗与生产保障单位", className: "shape-support" },
          { label: "基地位置", note: "菱形：双方基地与主建筑", className: "shape-base" },
        ];
      },
      minimapBaseImageStyle() {
        const scaleX = Math.max(0.6, Math.min(1.8, Number(this.minimapBaseImageScaleX) || 1));
        const scaleY = Math.max(0.6, Math.min(1.8, Number(this.minimapBaseImageScaleY) || 1));
        return {
          transform: `scale(${scaleX}, ${scaleY})`,
          transformOrigin: "center center",
        };
      },
      minimapStatus() {
        const lf = this.state?.latestFeatureFrame || null;
        const loop = lf?.game_loop ?? "—";
        const frame = lf?.time_formatted || lf?.updatedAt || "—";
        const markers = (this.minimapUnitMarkers || []).length;
        const scale = Math.max(0.5, Number(this.minimapOverlayScale) || 1).toFixed(2);
        const baseScaleX = Math.max(0.6, Math.min(1.8, Number(this.minimapBaseImageScaleX) || 1)).toFixed(2);
        const baseScaleY = Math.max(0.6, Math.min(1.8, Number(this.minimapBaseImageScaleY) || 1)).toFixed(2);
        const ox = (Number(this.minimapOverlayOffsetX) || 0).toFixed(1);
        const oy = (Number(this.minimapOverlayOffsetY) || 0).toFixed(1);
        return {
          frame,
          loop,
          markers,
          scale,
          baseScaleX,
          baseScaleY,
          offsetX: `${ox}%`,
          offsetY: `${oy}%`,
          offset: `${ox}%, ${oy}%`,
        };
      },
      minimapVisionLines() {
        const s = this.deerflowAwarenessSensing || {};
        const vis = s.vision || {};
        const fmtPct = (raw) => {
          const n = Number(raw);
          if (!Number.isFinite(n)) return null;
          return Math.max(0, Math.min(100, n <= 1 ? n * 100 : n));
        };
        const fmtDuration = (seconds) => {
          const n = Math.max(0, Math.floor(Number(seconds) || 0));
          const m = Math.floor(n / 60);
          const sec = n % 60;
          return `${m}分${String(sec).padStart(2, "0")}秒`;
        };
        const findLastEnemyBaseScoutSecondsAgo = () => {
          const commands = Array.isArray(this.state?.commandSequence) ? this.state.commandSequence : [];
          const enemyPrefix = String(this.game?.startLocation || "").startsWith("B") ? "A" : "B";
          const enemyMain = `${enemyPrefix}1`;
          const scoutRows = commands
            .filter((cmd) => {
              const text = `${cmd.unit || ""} ${cmd.action || ""} ${cmd.target || ""} ${cmd.locationLabel || ""}`.toLowerCase();
              return (
                text.includes("scout") ||
                text.includes("侦察") ||
                text.includes("overlord") ||
                text.includes("监察") ||
                text.includes("王虫")
              ) && String(cmd.locationLabel || cmd.target || "").includes(enemyMain);
            })
            .map((cmd) => new Date(cmd.updatedAt || 0).getTime())
            .filter((ts) => Number.isFinite(ts) && ts > 0)
            .sort((a, b) => b - a);
          if (!scoutRows.length) return null;
          return Math.max(0, Math.floor((Date.now() - scoutRows[0]) / 1000));
        };

        const visiblePct = fmtPct(vis.visible_tiles_pct);
        const fogPct = fmtPct(vis.fog_of_war_pct);
        const resolvedVisiblePct = visiblePct != null ? visiblePct : 0;
        const resolvedFogPct = fogPct != null ? fogPct : (visiblePct != null ? 100 - visiblePct : 0);
        const staleAfter = Number(s.scouting?.stale_after_seconds ?? 150);
        const elapsed = vis.enemy_base_visible ? 0 : findLastEnemyBaseScoutSecondsAgo();
        const countdownLine =
          elapsed == null
            ? {
              label: "侦察",
              value: "已过去：未记录",
              className: "minimap-status-vision-line--stale",
            }
            : {
              label: "侦察",
              value: vis.enemy_base_visible
                ? "敌方主基地当前在视野内"
                : `已过去：${fmtDuration(elapsed)}`,
              className: elapsed > staleAfter ? "minimap-status-vision-line--stale" : "",
            };

        return [
          {
            label: "当前视野",
            value: `${resolvedVisiblePct.toFixed(0)}%`,
            className: "",
          },
          {
            label: "敌主基地",
            value: vis.enemy_base_visible ? "视野内可见" : "当前不可见",
            className: "",
          },
          countdownLine,
        ];
      },
      minimapMarkers() {
        if (!this.liveUiArmed) {
          return [];
        }
        return this.state?.minimapOverlay ?? [];
      },
      minimapResourceMarkers() {
        const liveResources = Array.isArray(this.game?.resourceMarkers)
          ? this.game.resourceMarkers
            .map((point, index) => ({
              label: String(point?.label || point?.location || `M${index + 1}`),
              key: String(point?.key || `${point?.label || "resource"}-${index}`),
              xPercent: Number(point?.xPercent),
              yPercent: Number(point?.yPercent),
            }))
            .filter((point) => Number.isFinite(point.xPercent) && Number.isFinite(point.yPercent))
          : [];
        if (liveResources.length) {
          return liveResources;
        }
        const layout = this.state?.mapLayout && typeof this.state.mapLayout === "object"
          ? this.state.mapLayout
          : null;
        const layoutMarkers = layout
          ? Object.entries(layout)
            .flatMap(([label, point]) => this.resourceClusterFallback(label, point))
            .filter((point) => point.label && Number.isFinite(point.xPercent) && Number.isFinite(point.yPercent))
            .sort((a, b) => a.label.localeCompare(b.label, "zh-CN", { numeric: true }) || String(a.key).localeCompare(String(b.key)))
          : [];
        if (layoutMarkers.length) {
          return layoutMarkers;
        }
        const xs = MINIMAP_RESOURCE_POINTS.map(([, x]) => x);
        const ys = MINIMAP_RESOURCE_POINTS.map(([, , y]) => y);
        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const spanX = Math.max(maxX - minX, 1);
        const spanY = Math.max(maxY - minY, 1);
        return MINIMAP_RESOURCE_POINTS.flatMap(([label, x, y]) =>
          this.resourceClusterFallback(label, {
            xPercent: ((x - minX) / spanX) * 100,
            yPercent: (1 - ((y - minY) / spanY)) * 100,
          })
        );
      },
      minimapUnitMarkers() {
        if (!this.liveUiArmed) return [];
        const pointByLabel = this.minimapResourceMarkers.reduce((acc, point) => {
          acc[point.label] = point;
          return acc;
        }, Object.create(null));
        const stackCounts = Object.create(null);
        const rows = this.forceDeploymentItems
          .filter((row) => row && (this.hasExactMapPoint(row) || (row.location && pointByLabel[row.location])))
          .filter((row) => Number(row.count || 0) > 0);

        return rows.map((row, index) => {
          const point = this.hasExactMapPoint(row)
            ? { xPercent: row.xPercent, yPercent: row.yPercent }
            : pointByLabel[row.location];
          const role = this.unitMarkerRole(row);
          const stackKey = `${Math.round(Number(point.xPercent) * 10) / 10}:${Math.round(Number(point.yPercent) * 10) / 10}:${row.side}:${role}`;
          const stackIndex = stackCounts[stackKey] || 0;
          stackCounts[stackKey] = stackIndex + 1;
          return {
            key: `${row.side}-${role}-${row.location}-${row.name}-${index}`,
            side: row.side === "enemy" ? "enemy" : "own",
            role,
            name: row.name,
            count: row.count,
            capability: row.capability,
            kind: row.kind,
            action: row.action,
            locationLabel: row.location,
            xPercent: point.xPercent,
            yPercent: point.yPercent,
            stackIndex,
          };
        });
      },
      minimapRangeCircles() {
        if (!this.liveUiArmed) return [];
        return this.minimapUnitMarkers
          .filter((m) => !this.minimapHighlightRole || m.role === this.minimapHighlightRole)
          .filter((m) => m.role === "scout" || m.role === "strike" || m.role === "defend")
          .map((m) => ({ ...m, rangePct: getUnitRangePct(m.name, m.role) }))
          .filter((m) => m.rangePct > 0);
      },
      // 按能力类型高亮时只显示该角色单元，否则全显示
      minimapUnitMarkersFiltered() {
        if (!this.minimapHighlightRole) return this.minimapUnitMarkers;
        return this.minimapUnitMarkers.filter((m) => m.role === this.minimapHighlightRole);
      },
      /** 意图识别模块：等级为"高"的意图对应的单元角色集合 */
      intentHighlightRoles() {
        const dims = this.deerflowRisk?.intent?.intent_dimensions || {};
        const dimToRole = { attack: "strike", defense: "defend", detect: "scout", support: "support" };
        return new Set(
          Object.entries(dims)
            .filter(([, d]) => String(d?.level || "").toLowerCase() === "high")
            .map(([k]) => dimToRole[k])
            .filter(Boolean),
        );
      },
      /** 等级为"高"的意图维度行列表（用于卡片顶部概览提示） */
      intentHighRows() {
        return this.deerflowIntentDimensionRows.filter((r) => r.level === "high");
      },
      /** 风险模块：当前选中维度下需要在地图高亮的单元标记 */
      riskHighlightMarkers() {
        const key = this.riskHighlightGroupKey;
        if (!key || this.activeRightLayer !== "risk") return [];
        const grp = (this.deerflowRiskDimensionGroups || []).find((g) => g.key === key);
        if (!grp) return [];
        const allTargets = [
          ...new Set(
            (grp.items || [])
              .map((r) => String(r.target || "").trim())
              .filter(Boolean),
          ),
        ];
        const markers = this.minimapUnitMarkers || [];
        let matched = [];
        if (allTargets.length) {
          matched = markers.filter((m) => {
            const mName = String(m.name || "").toLowerCase();
            const mLoc = String(m.locationLabel || "").toLowerCase();
            return allTargets.some((t) => {
              const tl = t.toLowerCase();
              return (
                mName.includes(tl) ||
                tl.includes(mName) ||
                mLoc.includes(tl) ||
                tl.includes(mLoc)
              );
            });
          });
        }
        // 无精准匹配时：按角色/阵营回退
        if (!matched.length) {
          const roleMap = { attack: "strike", defense: "defend", detect: "scout", support: "support" };
          const sideMap = { attack: "enemy", defense: "own", detect: "enemy", support: "enemy" };
          const role = roleMap[key];
          const side = sideMap[key];
          matched = markers.filter((m) => m.side === side && m.role === role);
          if (!matched.length && key === "attack") {
            matched = markers.filter((m) => m.side === "enemy");
          }
        }
        return matched.map((m) => ({ ...m, riskGroupKey: key }));
      },
      fogCanvasDataUrl() {
        const grid = this.minimapFrame?.visibilityGrid;
        if (!Array.isArray(grid) || grid.length === 0) return null;
        const shape = this.minimapFrame?.visibilityShape;
        const H = (Array.isArray(shape) && shape[0]) ? shape[0] : 64;
        const W = (Array.isArray(shape) && shape[1]) ? shape[1] : 64;
        const SIZE = 128; // 内部画布分辨率
        try {
          const canvas = document.createElement("canvas");
          canvas.width = SIZE;
          canvas.height = SIZE;
          const ctx = canvas.getContext("2d");
          if (!ctx) return null;
          const imageData = ctx.createImageData(SIZE, SIZE);
          const d = imageData.data;
          // 能见度等级 → alpha: 0=战争迷雾, 1=探索但未见, 2=视野内, 3=完全可见
          const alphaMap = [130, 60, 10, 0];
          for (let cy = 0; cy < SIZE; cy++) {
            const gRow = Math.min(H - 1, Math.floor((cy * H) / SIZE));
            for (let cx = 0; cx < SIZE; cx++) {
              const gCol = Math.min(W - 1, Math.floor((cx * W) / SIZE));
              const vis = Math.min(3, Math.max(0, grid[gRow * W + gCol] | 0));
              const alpha = alphaMap[vis];
              const i = (cy * SIZE + cx) * 4;
              d[i]     = 10;   // R
              d[i + 1] = 15;   // G
              d[i + 2] = 28;   // B
              d[i + 3] = alpha;
            }
          }
          ctx.putImageData(imageData, 0, 0);
          return canvas.toDataURL("image/png");
        } catch (_) {
          return null;
        }
      },
      featureCards() {
        return (this.state?.latestFeatureFrame?.features ?? []).map((feature) => translateFeature(feature));
      },
      selectedFeature() {
        return (
          this.featureCards.find((feature) => feature.name === this.activeFeatureKey) ||
          this.featureCards[0] ||
          null
        );
      },
      featureMetaLine() {
        if (!this.state?.latestFeatureFrame) {
          return "等待特征帧";
        }
        const loop = this.state.latestFeatureFrame.game_loop ?? "--";
        const raw = this.state.latestFeatureFrame.updatedAt;
        let timeStr = "--";
        if (raw) {
          const d = new Date(raw);
          if (!Number.isNaN(d.getTime())) {
            const mm = String(d.getMonth() + 1).padStart(2, "0");
            const dd = String(d.getDate()).padStart(2, "0");
            const hh = String(d.getHours()).padStart(2, "0");
            const min = String(d.getMinutes()).padStart(2, "0");
            timeStr = `${mm}/${dd} ${hh}:${min}`;
          }
        }
        return `回合 ${loop}  ·  ${timeStr}`;
      },
      featureStatusLabel() {
        if (!this.state?.latestFeatureFrame) {
          return "绛夊緟鏇存柊";
        }
        return this.featureRefreshFlash ? "鍒氬垰鍒锋柊" : "鎸佺画鏇存柊";
      },
      forceDeploymentItems() {
        if (!this.liveUiArmed) {
          return [];
        }
        const ownFromDetails = normalizeForceRows(this.game?.ownForceDetails || [], "own");
        const ownRows = ownFromDetails.length
          ? ownFromDetails
          : normalizeForceRows(compositionToForceRows(this.game?.ownUnitComposition, "own"), "own");
        const enemyFromDetails = normalizeForceRows(this.game?.enemyVisibleDetails || [], "enemy");
        const enemyRows = enemyFromDetails.length
          ? enemyFromDetails
          : normalizeForceRows(compositionToForceRows(this.game?.enemyVisibleComposition, "enemy"), "enemy");
        return [...ownRows, ...enemyRows];
      },
      forceDeploymentReady() {
        return this.forceDeploymentItems.length > 0;
      },
      liveCriticalTargetItems() {
        return this.buildCriticalTargetItems();
      },
      criticalTargetIsStale() {
        return this.liveCriticalTargetItems.length === 0 && this.cachedCriticalTargetItems.length > 0;
      },
      criticalTargetItems() {
        if (this.liveCriticalTargetItems.length) {
          return this.liveCriticalTargetItems.map((item) => ({ ...item, stale: false }));
        }
        return (this.cachedCriticalTargetItems || []).map((item) => ({ ...item, stale: true }));
      },
      criticalTargetReady() {
        return this.criticalTargetItems.length > 0;
      },
      filteredCriticalTargetItems() {
        if (this.activeCriticalTargetCategory === "all") {
          return this.criticalTargetItems;
        }
        return this.criticalTargetItems.filter((item) => item.category === this.activeCriticalTargetCategory);
      },
      criticalTargetCategoryOptions() {
        return CRITICAL_TARGET_CATEGORY_ORDER.map((key) => {
          const meta = CRITICAL_TARGET_CATEGORY_META[key];
          return {
            key,
            label: meta.label.replace("目标", ""),
            shortLabel: meta.shortLabel,
            color: meta.color,
            count: this.criticalTargetItems.filter((item) => item.category === key).length,
          };
        });
      },
      criticalTargetFilterOptions() {
        return [
          {
            key: "all",
            label: "全部",
            shortLabel: "全部",
            color: "#7dc8ff",
            count: this.criticalTargetItems.length,
          },
          ...this.criticalTargetCategoryOptions,
        ];
      },
      criticalTargetFilterLabel() {
        if (this.activeCriticalTargetCategory === "all") {
          return "全部";
        }
        return this.criticalTargetCategoryOptions.find((item) => item.key === this.activeCriticalTargetCategory)?.label || "全部";
      },
      criticalTargetLastSeenText() {
        if (!this.criticalTargetReady) {
          return "尚未发现敌方关键目标";
        }
        if (!this.criticalTargetIsStale) {
          return "当前可见";
        }
        const now = new Date(this.state?.updatedAt || "").getTime();
        const baseNow = Number.isFinite(now) ? now : Date.now();
        const elapsed = formatElapsed(baseNow - this.cachedCriticalTargetSeenAtMs);
        const gameTime = this.cachedCriticalTargetSeenGameTime || "--:--";
        return `上次发现 ${elapsed} / 对局时间 ${gameTime}`;
      },
      selectedCriticalTarget() {
        return this.filteredCriticalTargetItems.find((item) => item.key === this.selectedCriticalTargetKey) || this.filteredCriticalTargetItems[0] || null;
      },
      criticalTargetMapMarker() {
        if (this.activeRightLayer !== "critical" || !this.selectedCriticalTarget) return null;
        const point = this.resolveCriticalTargetMapPoint(this.selectedCriticalTarget);
        if (!point) return null;
        return {
          ...this.selectedCriticalTarget,
          xPercent: point.xPercent,
          yPercent: point.yPercent,
          location: point.label || this.selectedCriticalTarget.location || "--",
        };
      },
      criticalTargetCategories() {
        return CRITICAL_TARGET_CATEGORY_ORDER.map((key) => {
          const meta = CRITICAL_TARGET_CATEGORY_META[key];
          const items = this.criticalTargetItems.filter((item) => item.category === key);
          return {
            ...meta,
            count: items.length,
            topScore: items[0]?.priorityScore || 0,
          };
        });
      },
      forceCapabilityRows() {
        return CAPABILITY_ORDER.map((key) => {
          const meta = CAPABILITY_META[key];
          const own = this.forceCapabilityBreakdown.own[key]?.count || 0;
          const enemy = this.forceCapabilityBreakdown.enemy[key]?.count || 0;
          const diff = own - enemy;
          let diffLabel = "势均力敌";
          if (diff > 0) diffLabel = `我方 +${diff}`;
          if (diff < 0) diffLabel = `敌方 +${Math.abs(diff)}`;
          const maxSide = Math.max(own, enemy, 1);
          return {
            ...meta,
            own,
            enemy,
            diff,
            diffLabel,
            ownBarPct: Math.round((own / maxSide) * 100),
            enemyBarPct: Math.round((enemy / maxSide) * 100),
          };
        });
      },
      forceCapabilityBreakdown() {
        const initSide = () =>
          Object.fromEntries(
            CAPABILITY_ORDER.map((key) => [key, { count: 0, unitsCounter: Object.create(null), units: [] }])
          );
        const out = { own: initSide(), enemy: initSide() };
        for (const row of this.forceDeploymentItems) {
          const side = row.side === "enemy" ? "enemy" : "own";
          const key = CAPABILITY_META[row.capability] ? row.capability : "strike";
          const bucket = out[side][key];
          bucket.count += Number(row.count) || 0;
          bucket.unitsCounter[row.name] = (bucket.unitsCounter[row.name] || 0) + (Number(row.count) || 0);
        }
        for (const side of ["own", "enemy"]) {
          for (const key of CAPABILITY_ORDER) {
            const bucket = out[side][key];
            bucket.units = Object.entries(bucket.unitsCounter)
              .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
              .map(([name]) => name)
              .slice(0, 6);
          }
        }
        return out;
      },
      forceSideSummary() {
        const ownTotal = this.forceDeploymentItems
          .filter((row) => row.side === "own")
          .reduce((sum, row) => sum + (Number(row.count) || 0), 0);
        const enemyTotal = this.forceDeploymentItems
          .filter((row) => row.side === "enemy")
          .reduce((sum, row) => sum + (Number(row.count) || 0), 0);
        return { ownTotal, enemyTotal };
      },
      forceDeploymentMeta() {
        if (!this.forceDeploymentReady) {
          return "等待兵力部署同步";
        }
        return `我方 ${this.forceSideSummary.ownTotal} · 敌方 ${this.forceSideSummary.enemyTotal} · 能力域 ${CAPABILITY_ORDER.length}`;
      },
      forceThreatNote() {
        if (!this.forceDeploymentReady) {
          return "等待双方兵力部署数据。";
        }
        const enemyRows = this.forceCapabilityRows.filter((row) => row.enemy > 0)
          .sort((a, b) => b.enemy - a.enemy);
        if (!enemyRows.length) {
          return "敌方当前无可见部署，建议继续侦察刷新态势。";
        }
        const top = enemyRows[0];
        const units = (this.forceCapabilityBreakdown.enemy[top.key]?.units || []).slice(0, 3).join("、") || "敌方单位";
        return `重点威胁能力：${top.label}（主要单位：${units}）`;
      },
      forceDeploymentLocationRows() {
        const emptySide = () => ({ total: 0, counter: Object.create(null), actions: new Set() });
        const grouped = new Map();
        for (const row of this.forceDeploymentItems) {
          const location = row.location && row.location !== "--" ? row.location : "未知区域";
          if (!grouped.has(location)) {
            grouped.set(location, { location, own: emptySide(), enemy: emptySide() });
          }
          const side = row.side === "enemy" ? "enemy" : "own";
          const bucket = grouped.get(location)[side];
          const count = Number(row.count) || 0;
          bucket.total += count;
          bucket.counter[row.name] = (bucket.counter[row.name] || 0) + count;
          if (row.action) bucket.actions.add(row.action);
        }
        const toText = (bucket) => {
          const units = Object.entries(bucket.counter)
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .slice(0, 4)
            .map(([name, count]) => `${name}×${count}`);
          return units.length ? units.join("、") : "暂无";
        };
        const toActionText = (bucket) => {
          const actions = Array.from(bucket.actions).filter(Boolean).slice(0, 2);
          return actions.length ? actions.join(" / ") : "待命/集结";
        };
        return Array.from(grouped.values())
          .map((row) => ({
            location: row.location,
            ownTotal: row.own.total,
            enemyTotal: row.enemy.total,
            ownUnits: toText(row.own),
            enemyUnits: toText(row.enemy),
            ownActions: toActionText(row.own),
            enemyActions: toActionText(row.enemy),
          }))
          .sort((a, b) => (b.ownTotal + b.enemyTotal) - (a.ownTotal + a.enemyTotal) || a.location.localeCompare(b.location))
          .slice(0, 8);
      },
      forceLlmSuggestion() {
        const empty = {
          currentStrategyCommand: "",
          targetType: "",
          recommendationReason: "",
          protectionTarget: "",
          strikeTarget: "",
        };
        const candidates = [
          this.state?.agentWorkbench?.formationAdvisor?.collaborationSuggestion,
          this.state?.agentWorkbench?.formationAdvisor?.llmSuggestion,
          this.state?.agentWorkbench?.forceDeployment?.llmSuggestion,
          this.state?.game?.forceCollaborationSuggestion,
          this.state?.messages?.latest?.collaborationSuggestion,
          this.state?.messages?.latest?.llmSuggestion,
        ];
        for (const candidate of candidates) {
          if (!candidate || typeof candidate !== "object") continue;
          return {
            currentStrategyCommand:
              String(
                candidate.currentStrategyCommand ||
                candidate.strategyCommand ||
                candidate.currentStrategy ||
                ""
              ),
            targetType: String(candidate.targetType || candidate.targetCategory || ""),
            recommendationReason:
              String(candidate.recommendationReason || candidate.reason || candidate.recommendation || ""),
            protectionTarget:
              String(candidate.protectionTarget || candidate.defenseTarget || candidate.protectTarget || ""),
            strikeTarget:
              String(candidate.strikeTarget || candidate.attackTarget || candidate.offenseTarget || ""),
          };
        }
        const latest = this.state?.messages?.latest || this.state?.lastMessage || null;
        if (latest && typeof latest === "object") {
          return {
            currentStrategyCommand: String(latest.currentStrategyCommand || latest.strategyCommand || ""),
            targetType: String(latest.targetType || ""),
            recommendationReason: String(latest.recommendationReason || latest.reason || ""),
            protectionTarget: String(latest.protectionTarget || latest.defenseTarget || ""),
            strikeTarget: String(latest.strikeTarget || latest.attackTarget || ""),
          };
        }
        return empty;
      },
      /** 无 API 时的本地启发式四象限（与旧版一致） */
      forceLlmSuggestionSections() {
        const suggestion = this.forceLlmSuggestion || {};
        const enemyMain = this.forceCapabilityRows
          .filter((row) => row.enemy > 0)
          .sort((a, b) => b.enemy - a.enemy)[0];
        const hotspot = this.forceDeploymentLocationRows
          .filter((row) => row.enemyTotal > 0 || row.ownTotal > 0)[0];
        const strikeTarget = suggestion.strikeTarget || hotspot?.location || suggestion.targetType || "等待目标确认";
        const protectionTarget = suggestion.protectionTarget || hotspot?.location || "我方主力与资源线";
        const reason = suggestion.recommendationReason || this.forceThreatNote || "等待大模型建议";
        return [
          {
            key: "strike",
            title: "打击类建议",
            summary: strikeTarget === "等待目标确认"
              ? "等待侦察确认敌方核心目标后再发起打击。"
              : `优先关注 ${strikeTarget}，结合${enemyMain?.label || "敌方主要兵力"}变化选择窗口。`,
            detail: reason,
          },
          {
            key: "defense",
            title: "防御类建议",
            summary: `围绕 ${protectionTarget} 保持防守冗余，避免敌方高机动单位穿插。`,
            detail: suggestion.currentStrategyCommand || "同步当前策略指令后，按威胁方向调整防线。",
          },
          {
            key: "detect",
            title: "侦察探测建议",
            summary: hotspot
              ? `持续刷新 ${hotspot.location} 一带视野，确认敌方后续增援与转场。`
              : "当前部署位置不足，建议优先补齐前线和分矿侦察。",
            detail: `目标类型：${suggestion.targetType || enemyMain?.label || "待识别"}`,
          },
          {
            key: "support",
            title: "保障类建议",
            summary: `资源态势：矿 ${this.awarenessWorkbench.resourcePanel.ownMinerals} · 气 ${this.awarenessWorkbench.resourcePanel.ownVespene} · 工 ${this.awarenessWorkbench.resourcePanel.ownWorkers}。`,
            detail: "根据资源与补给情况衔接生产、补给和前线支援，避免主力行动后续断档。",
          },
        ];
      },
      /** 优先展示 API 返回的大模型建议 */
      forceDispositionAdviceSections() {
        const rows = this.forceLlmAdvice?.sections;
        if (Array.isArray(rows) && rows.length) {
          return rows.map((s, i) => ({
            key: s.key || `api-${i}`,
            title: String(s.title || "协同建议"),
            summary: String(s.summary || ""),
            detail: String(s.detail || ""),
            sourceTag: "api",
          }));
        }
        return this.forceLlmSuggestionSections.map((s) => ({ ...s, sourceTag: "local" }));
      },
      forceDispositionAdviceIsApi() {
        return Array.isArray(this.forceLlmAdvice?.sections) && this.forceLlmAdvice.sections.length > 0;
      },
      killChainDimensionGroups() {
        const wbChains = this.state?.agentWorkbench?.killchainDiscovery?.enemyChains;
        if (Array.isArray(wbChains) && wbChains.length) {
          const grouped = Object.fromEntries(
            KILLCHAIN_DIMENSION_ORDER.map((key) => [key, []])
          );
          for (const chain of wbChains) {
            const dim = KILLCHAIN_DIMENSION_META[chain?.dimension] ? chain.dimension : "strike";
            grouped[dim].push({
              key: String(chain.key || `${dim}-${grouped[dim].length}`),
              dimension: dim,
              title: String(chain.title || ""),
              targetLocation: String(chain.targetLocation || "--"),
              status: String(chain.status || "待激活"),
              confidence: Number(chain.confidence) || 0,
              confidenceLevel: normalizeLevelWord(chain.confidenceLevel || scoreToLevel(chain.confidence)),
              summary: String(chain.summary || ""),
              nodes: Array.isArray(chain.nodes) ? chain.nodes : [],
            });
          }
          return KILLCHAIN_DIMENSION_ORDER.map((key) => ({
            ...KILLCHAIN_DIMENSION_META[key],
            chains: grouped[key],
          }));
        }
        if (!this.liveUiArmed) {
          return KILLCHAIN_DIMENSION_ORDER.map((key) => ({
            ...KILLCHAIN_DIMENSION_META[key],
            chains: [],
          }));
        }
        const enemyRows = this.forceDeploymentItems.filter((row) => row.side === "enemy");
        const enemyHotspots = this.forceDeploymentLocationRows.filter((row) => row.enemyTotal > 0);
        const dimensionPreference = {
          strike: {
            discover: ["detect", "support"],
            locate: ["detect", "strike"],
            track: ["detect", "strike"],
            designate: ["support", "detect"],
            strike: ["strike", "defense"],
            assess: ["detect", "support"],
          },
          detect: {
            discover: ["detect", "support"],
            locate: ["detect", "strike"],
            track: ["detect", "support"],
            designate: ["support", "detect"],
            strike: ["strike", "defense"],
            assess: ["detect", "support"],
          },
          defense: {
            discover: ["detect", "defense"],
            locate: ["defense", "detect"],
            track: ["defense", "support"],
            designate: ["support", "defense"],
            strike: ["defense", "strike"],
            assess: ["support", "detect"],
          },
          support: {
            discover: ["detect", "support"],
            locate: ["support", "detect"],
            track: ["support", "defense"],
            designate: ["support", "detect"],
            strike: ["strike", "support"],
            assess: ["support", "detect"],
          },
        };
        const pickNode = (pool, preferredCaps, targetLocation, usedNames) => {
          if (!pool.length) return null;
          const target = String(targetLocation || "");
          const best = [...pool]
            .map((row, idx) => {
              const capIdx = preferredCaps.indexOf(row.capability);
              const capabilityRank = capIdx >= 0 ? capIdx : 9;
              const locationPenalty = row.location === target ? 0 : 2;
              const usedPenalty = usedNames.has(row.name) ? 1 : 0;
              return {
                row,
                score: capabilityRank * 100 + locationPenalty * 10 + usedPenalty * 7 - (Number(row.count) || 0),
                idx,
              };
            })
            .sort((a, b) => a.score - b.score || a.idx - b.idx)[0];
          return best?.row || null;
        };
        return KILLCHAIN_DIMENSION_ORDER.map((dimensionKey) => {
          const meta = KILLCHAIN_DIMENSION_META[dimensionKey];
          const sourceRows = enemyRows.filter((row) => row.capability === dimensionKey);
          const fallbackRows = enemyRows.length ? enemyRows : [];
          const sourcePool = sourceRows.length ? sourceRows : fallbackRows;
          const rowSeeds = sourcePool
            .filter((row) => row.location && row.location !== "--")
            .map((row) => ({ location: row.location, enemyTotal: row.count || 1, seedRow: row }));
          const dimensionHotspots = enemyHotspots.filter((row) =>
            sourcePool.some((unit) => unit.location === row.location)
          );
          const seeds = dimensionHotspots.length ? dimensionHotspots : rowSeeds;
          if (!seeds.length) {
            return {
              ...meta,
              chains: [],
            };
          }
          const chainCount = Math.min(3, Math.max(1, seeds.length));
          const chains = [];
          for (let chainIdx = 0; chainIdx < chainCount; chainIdx += 1) {
            const seed = seeds[chainIdx % seeds.length] || {};
            const targetLocation = seed.location || "A1";
            const usedNames = new Set();
            const stageNodes = KILLCHAIN_STAGE_ORDER.map((stage, stageIdx) => {
              const preferredCaps = dimensionPreference[dimensionKey]?.[stage.key] || [dimensionKey, "support", "detect", "strike", "defense"];
              const sameAreaPool = sourcePool.filter((row) => row.location === targetLocation);
              const matched = pickNode(sameAreaPool.length ? sameAreaPool : sourcePool, preferredCaps, targetLocation, usedNames);
              if (matched) usedNames.add(matched.name);
              const location = matched?.location || targetLocation;
              const point = buildKillChainPoint(location, stageIdx, chainIdx);
              const capability = matched?.capability || dimensionKey;
              return {
                stageKey: stage.key,
                stageLabel: stage.label,
                nodeType: `敌方${capabilityNodeType(capability)}`,
                unitName: matched?.name || `${meta.label}链路节点`,
                action: matched?.action || "敌方当前可见",
                location,
                capability,
                pointX: point.x,
                pointY: point.y,
              };
            });
            const matchedCount = stageNodes.filter((node) => !String(node.unitName || "").includes("链路节点")).length;
            const confidence = clamp(52 + matchedCount * 6 + Math.min(12, Number(seed.enemyTotal) || 0));
            let status = "待激活";
            if (confidence >= 84) status = "已锁定";
            else if (confidence >= 68) status = "进行中";
            const key = `${dimensionKey}-${chainIdx}`;
            const targetHint = targetLocation || "--";
            const briefNodes = stageNodes.slice(0, 3).map((node) => `${node.stageLabel}·${node.unitName}`);
            chains.push({
              key,
              dimension: dimensionKey,
              title: `敌方${meta.label}链路-${targetHint}`,
              targetLocation: targetHint,
              status,
              confidence,
              summary: briefNodes.join(" / "),
              nodes: stageNodes,
            });
          }
          return {
            ...meta,
            chains,
          };
        });
      },
      killChainReady() {
        return this.killChainDimensionGroups.some((group) => group.chains.length > 0);
      },
      killChainMeta() {
        const chainTotal = this.killChainDimensionGroups.reduce((sum, group) => sum + group.chains.length, 0);
        const planTotal = this.killChainPlans.length;
        if (!chainTotal) return "等待链路数据";
        const src = this.killChainLlmPlans?.ok ? "大模型方案" : "本地兜底方案";
        return `敌链 ${chainTotal} 条 · ${src} ${planTotal} 个`;
      },
      killChainLlmChainSignature() {
        const rows = [];
        for (const group of this.killChainDimensionGroups) {
          for (const chain of group.chains || []) {
            rows.push([
              chain.key,
              group.key,
              chain.targetLocation,
              Math.round(Number(chain.confidence) || 0),
            ].join(":"));
          }
        }
        return rows.sort().join("|");
      },
      killChainTaskPool() {
        const statusBonus = { "已锁定": 16, "进行中": 9, "待激活": 3 };
        const dimensionWeight = { strike: 12, detect: 10, defense: 9, support: 8 };
        const rows = [];
        for (const group of this.killChainDimensionGroups) {
          for (const chain of group.chains || []) {
            const nodeCount = Array.isArray(chain.nodes) ? chain.nodes.length : 0;
            const executability = clamp(Math.round((Number(chain.confidence) || 0) * 0.78 + nodeCount * 4));
            const rankScore = clamp(
              Math.round(
                executability * 0.62 +
                (Number(chain.confidence) || 0) * 0.28 +
                (statusBonus[chain.status] || 0) +
                (dimensionWeight[group.key] || 0)
              )
            );
            rows.push({
              taskKey: `${group.key}:${chain.key}`,
              chainKey: chain.key,
              dimension: group.key,
              dimensionLabel: group.label,
              color: group.color,
              title: chain.title,
              summary: chain.summary,
              status: chain.status,
              confidence: Number(chain.confidence) || 0,
              confidenceLevel: normalizeLevelWord(chain.confidenceLevel || scoreToLevel(chain.confidence)),
              executability,
              executabilityLevel: normalizeLevelWord(scoreToLevel(executability)),
              rankScore,
              priorityLevel: normalizeLevelWord(scoreToLevel(rankScore)),
              targetLocation: chain.targetLocation,
              nodes: chain.nodes || [],
            });
          }
        }
        return rows.sort((a, b) => b.rankScore - a.rankScore || b.executability - a.executability || a.title.localeCompare(b.title));
      },
      killChainPlans() {
        const llmPlans = Array.isArray(this.killChainLlmPlans?.plans) ? this.killChainLlmPlans.plans : [];
        if (this.killChainLlmPlans?.ok && llmPlans.length) {
          const chainByKey = new Map();
          for (const group of this.killChainDimensionGroups) {
            for (const chain of group.chains || []) {
              chainByKey.set(chain.key, { ...chain, dimension: group.key, color: group.color });
            }
          }
          return llmPlans
            .map((plan, idx) => {
              const rawTasks = Array.isArray(plan.tasks) ? plan.tasks : [];
              const tasks = rawTasks.map((task, taskIdx) => {
                const chain = chainByKey.get(String(task?.chainKey || ""));
                const dim = KILLCHAIN_DIMENSION_META[task?.dimension] ? task.dimension : (chain?.dimension || "strike");
                const confidence = Number(task?.confidence ?? chain?.confidence ?? 0) || 0;
                const rankScore = Number(task?.rankScore ?? task?.executability ?? confidence) || 0;
                return {
                  taskKey: String(task?.taskKey || `${plan.key || idx}:${taskIdx}`),
                  chainKey: String(task?.chainKey || chain?.key || ""),
                  dimension: dim,
                  dimensionLabel: KILLCHAIN_DIMENSION_META[dim].label,
                  color: KILLCHAIN_DIMENSION_META[dim].color,
                  title: String(task?.title || chain?.title || ""),
                  summary: String(task?.summary || chain?.summary || ""),
                  status: String(task?.status || chain?.status || "待激活"),
                  confidence,
                  confidenceLevel: normalizeLevelWord(task?.confidenceLevel || scoreToLevel(confidence)),
                  executability: Number(task?.executability ?? confidence) || 0,
                  executabilityLevel: normalizeLevelWord(task?.executabilityLevel || scoreToLevel(task?.executability ?? confidence)),
                  rankScore,
                  priorityLevel: normalizeLevelWord(task?.priorityLevel || scoreToLevel(rankScore)),
                  targetLocation: String(task?.targetLocation || chain?.targetLocation || "--"),
                  nodes: Array.isArray(chain?.nodes) ? chain.nodes : [],
                };
              });
              const chainKeys = Array.isArray(plan.chainKeys)
                ? plan.chainKeys.map((key) => String(key)).filter(Boolean)
                : tasks.map((task) => task.chainKey).filter(Boolean);
              return {
                key: String(plan.key || `llm-plan-${idx + 1}`),
                title: String(plan.title || `方案${idx + 1}`),
                description: String(plan.description || "由大模型基于敌方杀伤链候选池生成。"),
                tasks,
                planScore: Number(plan.planScore) || 0,
                planPriorityLevel: normalizeLevelWord(plan.planPriorityLevel || scoreToLevel(plan.planScore)),
                chainKeys: Array.from(new Set(chainKeys)),
                rank: Number(plan.rank) || (idx + 1),
                enemyStrikeFlow: Array.isArray(plan.enemyStrikeFlow) ? plan.enemyStrikeFlow : [],
                sourceTag: "llm",
              };
            })
            .sort((a, b) => a.rank - b.rank || b.planScore - a.planScore);
        }
        const wbPlans = this.state?.agentWorkbench?.combatPlanning?.plans;
        if (Array.isArray(wbPlans) && wbPlans.length) {
          return wbPlans
            .map((plan, idx) => {
              const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
              return {
                key: String(plan.key || `plan-${idx}`),
                title: String(plan.title || `方案${idx + 1}`),
                description: String(plan.description || ""),
                tasks: tasks.map((task, taskIdx) => {
                  const dim = KILLCHAIN_DIMENSION_META[task?.dimension] ? task.dimension : "strike";
                  return {
                    taskKey: String(task.taskKey || `${plan.key || idx}:${taskIdx}`),
                    chainKey: String(task.chainKey || ""),
                    dimension: dim,
                    dimensionLabel: String(task.dimensionLabel || KILLCHAIN_DIMENSION_META[dim].label),
                    color: KILLCHAIN_DIMENSION_META[dim].color,
                    title: String(task.title || ""),
                    summary: String(task.summary || ""),
                    status: String(task.status || "待激活"),
                    confidence: Number(task.confidence) || 0,
                    confidenceLevel: normalizeLevelWord(task.confidenceLevel || scoreToLevel(task.confidence)),
                    executability: Number(task.executability) || 0,
                    executabilityLevel: normalizeLevelWord(task.executabilityLevel || scoreToLevel(task.executability)),
                    rankScore: Number(task.rankScore) || 0,
                    priorityLevel: normalizeLevelWord(task.priorityLevel || scoreToLevel(task.rankScore)),
                    targetLocation: String(task.targetLocation || "--"),
                    nodes: Array.isArray(task.nodes) ? task.nodes : [],
                  };
                }),
                planScore: Number(plan.planScore) || 0,
                planPriorityLevel: normalizeLevelWord(plan.planPriorityLevel || scoreToLevel(plan.planScore)),
                chainKeys: Array.isArray(plan.chainKeys) ? plan.chainKeys : tasks.map((task) => String(task.chainKey || "")),
                rank: Number(plan.rank) || (idx + 1),
              };
            })
            .sort((a, b) => a.rank - b.rank || b.planScore - a.planScore);
        }
        const tasks = this.killChainTaskPool;
        if (!tasks.length) return [];
        const byDimension = Object.fromEntries(
          KILLCHAIN_DIMENSION_ORDER.map((key) => [key, tasks.filter((task) => task.dimension === key)])
        );
        const titlePool = ["优先压制方案", "联动反制方案", "分层瓦解方案", "稳态推进方案", "弹性补位方案", "持续削弱方案"];
        const planCount = Math.max(2, Math.min(4, Math.ceil(tasks.length / 2)));
        const plans = Array.from({ length: planCount }).map((_, index) => {
          const dims = KILLCHAIN_DIMENSION_ORDER.map((_, dimIdx) => KILLCHAIN_DIMENSION_ORDER[(index + dimIdx) % KILLCHAIN_DIMENSION_ORDER.length]);
          const picked = [];
          const usedTaskKeys = new Set();
          for (const dim of dims) {
            const candidate = (byDimension[dim] || []).find((task) => !usedTaskKeys.has(task.taskKey));
            if (candidate) {
              picked.push(candidate);
              usedTaskKeys.add(candidate.taskKey);
            }
          }
          if (picked.length < 3) {
            for (const fallback of tasks) {
              if (usedTaskKeys.has(fallback.taskKey)) continue;
              picked.push(fallback);
              usedTaskKeys.add(fallback.taskKey);
              if (picked.length >= 4) break;
            }
          }
          const planScore = clamp(
            Math.round(
              picked.reduce((sum, task) => sum + task.rankScore, 0) / Math.max(1, picked.length)
            )
          );
          return {
            key: `plan-${index + 1}`,
            title: titlePool[index % titlePool.length],
            description: "基于敌方可见杀伤链对象生成，用于破坏敌方关键链路。",
            tasks: picked,
            planScore,
            planPriorityLevel: normalizeLevelWord(scoreToLevel(planScore)),
            chainKeys: Array.from(new Set(picked.map((task) => task.chainKey))),
          };
        });
        return plans
          .sort((a, b) => b.planScore - a.planScore || b.tasks.length - a.tasks.length || a.title.localeCompare(b.title))
          .map((plan, idx) => ({ ...plan, rank: idx + 1 }));
      },
      killChainActiveGroup() {
        return this.killChainDimensionGroups.find((group) => group.key === this.activeKillChainDimension) || this.killChainDimensionGroups[0] || null;
      },
      killChainSelectedPlan() {
        return this.killChainPlans.find((plan) => plan.key === this.selectedKillPlanKey) || this.killChainPlans[0] || null;
      },
      killChainPlanVisualTasks() {
        const plan = this.killChainSelectedPlan;
        if (!plan) return [];
        const laneCounter = Object.create(null);
        const totalStages = KILLCHAIN_STAGE_ORDER.length;
        return (plan.tasks || []).map((task, taskIdx) => {
          const laneIdxBase = Math.max(0, KILLCHAIN_LANE_ORDER.indexOf(task.dimension));
          const laneIdx = laneIdxBase >= 0 ? laneIdxBase : 0;
          const laneCount = laneCounter[task.dimension] || 0;
          laneCounter[task.dimension] = laneCount + 1;
          const laneBaseY = 18 + laneIdx * 21;
          const laneOffset = (laneCount % 3) * 3.2 - 3.2;
          const visualNodes = (task.nodes || []).map((node, nodeIdx) => {
            const x = 10 + (nodeIdx / Math.max(1, totalStages - 1)) * 80;
            const y = Math.max(8, Math.min(92, laneBaseY + laneOffset));
            return {
              ...node,
              pointX: Math.round(x * 10) / 10,
              pointY: Math.round(y * 10) / 10,
              stageIndex: nodeIdx + 1,
            };
          });
          return {
            ...task,
            visualNodes,
            laneIdx,
            drawOrder: taskIdx + 1,
          };
        });
      },
      killChainMapLanes() {
        return KILLCHAIN_LANE_ORDER.map((dim, idx) => {
          const meta = KILLCHAIN_DIMENSION_META[dim];
          return {
            key: dim,
            label: meta?.label || dim,
            icon: meta?.icon || "•",
            y: 18 + idx * 21,
          };
        });
      },
      killChainPlanTaskGroups() {
        const plan = this.killChainSelectedPlan;
        const tasks = Array.isArray(plan?.tasks) ? plan.tasks : [];
        return KILLCHAIN_DIMENSION_ORDER.map((dim) => {
          const meta = KILLCHAIN_DIMENSION_META[dim];
          return {
            dimension: dim,
            label: meta?.label || dim,
            icon: meta?.icon || "•",
            color: meta?.color || "#67f3e7",
            items: tasks
              .filter((task) => task.dimension === dim)
              .sort((a, b) => (Number(b.rankScore) || 0) - (Number(a.rankScore) || 0)),
          };
        });
      },
      killChainEnemyStrikeFlow() {
        const plan = this.killChainSelectedPlan;
        const modelFlow = Array.isArray(plan?.enemyStrikeFlow) ? plan.enemyStrikeFlow : [];
        if (modelFlow.length) {
          const taskByChain = new Map((plan.tasks || []).map((task) => [task.chainKey, task]));
          const steps = modelFlow
            .map((step, idx) => {
              const chainKey = String(step?.chainKey || "");
              const task = taskByChain.get(chainKey);
              const dim = KILLCHAIN_DIMENSION_META[step?.dimension] ? step.dimension : (task?.dimension || "strike");
              const location = String(step?.targetLocation || task?.targetLocation || "--");
              return {
                key: `${chainKey || dim}-${idx}`,
                taskKey: task?.taskKey || `${chainKey || dim}-${idx}`,
                chainKey,
                dim,
                dimLabel: KILLCHAIN_DIMENSION_META[dim].label,
                color: KILLCHAIN_DIMENSION_META[dim].color,
                taskTitle: task?.title || "",
                targetLocation: location,
                fallbackPoint: this.killChainTaskFallbackPoint(task),
                title: String(step?.title || `打击${KILLCHAIN_DIMENSION_META[dim].label}链路${location}`),
                subtitle: String(step?.rationale || task?.summary || "由大模型生成敌链破坏步骤。"),
                order: Number(step?.order) || (idx + 1),
              };
            })
            .sort((a, b) => a.order - b.order);
          return {
            steps,
            summary: `大模型生成 ${steps.length} 个敌方链路破坏步骤`,
          };
        }
        const tasks = Array.isArray(plan?.tasks) ? plan.tasks : [];
        const sorted = [...tasks].sort((a, b) => (Number(b.rankScore) || 0) - (Number(a.rankScore) || 0));
        const steps = sorted.map((task, idx) => {
          const dim = KILLCHAIN_DIMENSION_META[task.dimension] ? task.dimension : "strike";
          const dimLabel = KILLCHAIN_DIMENSION_META[dim].label;
          const location = String(task.targetLocation || "--");
          const actionVerb = dim === "detect" ? "打击探测链路" : dim === "defense" ? "打击防御链路" : dim === "support" ? "打击保障链路" : "打击打击链路";
          return {
            key: task.taskKey || `${dim}-${idx}`,
            taskKey: task.taskKey || `${dim}-${idx}`,
            chainKey: task.chainKey || "",
            dim,
            dimLabel,
            color: KILLCHAIN_DIMENSION_META[dim].color,
            taskTitle: task.title || "",
            targetLocation: location,
            fallbackPoint: this.killChainTaskFallbackPoint(task),
            title: `${actionVerb}${location}`,
            subtitle: `${task.status || "待激活"} · 优先级 ${task.priorityLevel || "可行"} · 可执行度 ${task.executabilityLevel || "可行"}`,
            order: idx + 1,
          };
        });
        return {
          steps,
          summary: steps.length
            ? `当前方案按优先级串联 ${steps.length} 个敌方链路打击步骤`
            : "当前方案暂无可执行的敌方链路打击步骤",
        };
      },
      killChainStrikeFlowMapNodes() {
        if (this.activeRightLayer !== "killchain") return [];
        const steps = this.killChainEnemyStrikeFlow?.steps || [];
        const stackCounts = Object.create(null);
        return steps
          .map((step) => {
            const point = this.resolveKillChainStrikeFlowPoint(step);
            if (!point) return null;
            const mapped = this.minimapMappedPointPercent(point);
            const stackKey = `${Math.round(mapped.x * 10) / 10}:${Math.round(mapped.y * 10) / 10}`;
            const stackIndex = stackCounts[stackKey] || 0;
            stackCounts[stackKey] = stackIndex + 1;
            return {
              ...step,
              key: `${step.key}-${step.order}`,
              location: point.label || step.targetLocation || "--",
              xPercent: point.xPercent,
              yPercent: point.yPercent,
              mappedX: mapped.x,
              mappedY: mapped.y,
              stackIndex,
            };
          })
          .filter(Boolean);
      },
      killChainStrikeFlowMapSegments() {
        const nodes = this.killChainStrikeFlowMapNodes || [];
        const segments = [];
        for (let i = 0; i < nodes.length - 1; i += 1) {
          const from = nodes[i];
          const to = nodes[i + 1];
          segments.push({
            key: `${from.key}->${to.key}`,
            // 杀伤链发现：地图联动统一用“荧光绿”箭头，和其它模块颜色区分
            color: "#34d399",
            path: `M ${from.mappedX.toFixed(2)} ${from.mappedY.toFixed(2)} L ${to.mappedX.toFixed(2)} ${to.mappedY.toFixed(2)}`,
          });
        }
        return segments;
      },
      killChainSelected() {
        const chains = this.killChainActiveGroup?.chains || [];
        return chains.find((chain) => chain.key === this.selectedKillChainKey) || chains[0] || null;
      },
      killChainMapRoutes() {
        return this.killChainPlanVisualTasks.map((task, idx) => ({
          key: task.taskKey,
          dimension: task.dimension,
          color: task.color,
          points: (task.visualNodes || []).map((node) => `${node.pointX},${node.pointY}`).join(" "),
          active: true,
          order: idx + 1,
        }));
      },
      killChainMapNodes() {
        const out = [];
        for (const task of this.killChainPlanVisualTasks) {
          for (let i = 0; i < (task.visualNodes || []).length; i += 1) {
            const node = task.visualNodes[i];
            out.push({
              ...node,
              key: `${task.taskKey}-${node.stageKey}-${i}`,
              chainKey: task.chainKey,
              taskKey: task.taskKey,
              dimension: task.dimension,
              stageIndex: i + 1,
              color: task.color,
              active: true,
            });
          }
        }
        return out;
      },
      killChainLegend() {
        return KILLCHAIN_STAGE_ORDER;
      },
      availableChannels() {
        return CHANNELS;
      },
      currentMessage() {
        return this.state?.messages?.latest || this.state?.lastMessage || null;
      },
      emptyMessage() {
        return "等待下一条方案生成结果...";
      },
      activeMessageTitle() {
        return getMessageDisplayTitle(this.currentMessage);
      },
      plannerAgentName() {
        return this.currentMessage?.agentName || "方案生成智能体";
      },
      plannerSourceName() {
        return this.currentMessage?.sourceName || "脚本调度器";
      },
      plannerRequest() {
        return this.currentMessage?.inputSummary || buildPlannerFallback(this.state, this.activeChannel);
      },
      plannerRequestTime() {
        return this.currentMessage?.timeFormatted || this.displayGameTime;
      },
      plannerChannelLabel() {
        return getChannelLabel(this.currentMessage?.channel || this.activeChannel);
      },
      plannerHistoryItems() {
        if (this.activeChannel === "latest") {
          return this.plannerHistory;
        }
        return this.plannerHistory.filter((item) => item.channel === this.activeChannel);
      },
      plannerLiveItem() {
        if (!this.plannerLiveTurn) {
          return null;
        }
        if (this.activeChannel !== "latest" && this.plannerLiveTurn.channel !== this.activeChannel) {
          return null;
        }
        return this.plannerLiveTurn;
      },
      plannerTailItem() {
        if (this.plannerLiveItem) {
          return this.plannerLiveItem;
        }
        return this.plannerHistoryItems[this.plannerHistoryItems.length - 1] || null;
      },
      plannerThinkingVisible() {
        return Boolean(this.plannerLiveItem && !this.plannerLiveItem.isTyping);
      },
      plannerTaskFilterTabs() {
        return Object.keys(TASK_PARTITION_LABELS).map((key) => ({
          key,
          label: TASK_PARTITION_LABELS[key],
        }));
      },
      plannerTaskTurns() {
        const turns = [];
        for (const item of this.plannerHistoryItems) {
          const tasks = extractPlannerTasksFromReply(this.replyTextForItem(item));
          if (!tasks.length) continue;
          turns.push({
            key: item.key,
            title: item.title || "历史规划",
            time: item.promptTime || "--:--",
            isLive: false,
            tasks,
          });
        }
        if (this.plannerLiveItem) {
          const liveTasks = extractPlannerTasksFromReply(this.replyTextForItem(this.plannerLiveItem));
          if (liveTasks.length) {
            turns.push({
              key: `${this.plannerLiveItem.key}-live`,
              title: this.plannerLiveItem.title || "当前规划",
              time: this.plannerLiveItem.promptTime || "--:--",
              isLive: true,
              tasks: liveTasks,
            });
          }
        }
        return turns;
      },
      plannerTaskTurnsFiltered() {
        const filter = this.activePlannerTaskFilter;
        return this.plannerTaskTurns
          .map((turn) => ({
            ...turn,
            tasks: filter === "all" ? turn.tasks : turn.tasks.filter((task) => task.type === filter),
          }))
          .filter((turn) => turn.tasks.length > 0);
      },
      plannerTaskFilterCounts() {
        const counts = { all: 0, strike: 0, detect: 0, defense: 0, support: 0 };
        for (const turn of this.plannerTaskTurns) {
          for (const task of turn.tasks) {
            counts.all += 1;
            if (counts[task.type] !== undefined) {
              counts[task.type] += 1;
            }
          }
        }
        return counts;
      },
      plannerTaskVisibleTurns() {
        const turns = this.plannerTaskTurnsFiltered;
        if (this.plannerTaskShowAllHistory || turns.length <= 3) {
          return turns;
        }
        return turns.slice(-3);
      },
      plannerTaskCanExpandHistory() {
        return this.plannerTaskTurnsFiltered.length > 3;
      },
      plannerUnitOwnPoolByType() {
        const out = {
          strike: [],
          detect: [],
          defense: [],
          support: [],
        };
        for (const row of this.forceDeploymentItems) {
          if (row.side !== "own") continue;
          const type = TASK_PARTITION_LABELS[row.capability] ? row.capability : "strike";
          const name = String(translateUnit(row.name) || row.name || "").trim();
          if (!name) continue;
          out[type].push({
            name,
            rawName: String(row.name || name),
            count: Number(row.count) || 0,
          });
        }
        for (const key of Object.keys(out)) {
          out[key] = out[key]
            .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
            .slice(0, 8);
        }
        return out;
      },
      plannerUnitAssignRows() {
        const rows = [];
        for (const turn of this.plannerTaskTurns) {
          for (const task of turn.tasks) {
            const mapping = deriveUnitMappingForTask(task, this.plannerUnitOwnPoolByType);
            rows.push({
              key: `${turn.key}-${task.order}-${task.summaryCn}`,
              turnTitle: turn.title,
              turnTime: turn.time,
              isLive: turn.isLive,
              type: task.type,
              typeLabel: task.typeLabel,
              taskSummary: task.summaryCn,
              mappedUnits: mapping.mappedUnits,
              matchLevel: mapping.matchLevel,
            });
          }
        }
        return rows;
      },
      plannerUnitAssignFilterCounts() {
        const counts = { all: 0, strike: 0, detect: 0, defense: 0, support: 0 };
        for (const row of this.plannerUnitAssignRows) {
          counts.all += 1;
          if (counts[row.type] !== undefined) {
            counts[row.type] += 1;
          }
        }
        return counts;
      },
      plannerUnitAssignRowsFiltered() {
        const filter = this.activePlannerUnitFilter;
        if (filter === "all") {
          return this.plannerUnitAssignRows;
        }
        return this.plannerUnitAssignRows.filter((row) => row.type === filter);
      },
      commandRows() {
        return (this.state?.commandSequence ?? []).map((row) => ({
          ...row,
          categoryLabel: CATEGORY_LABELS[row.category] || CATEGORY_LABELS.other,
          cnSummary: buildCnSummary(row),
          cnDetail: buildCnDetail(row),
          isNew: this.newCommandOrders.has(row.order),
        }));
      },
      pendingCommandCount() {
        return this.commandRows.length;
      },
      executedCommandRows() {
        return this.executedCommands.map((row, index) => ({
          ...row,
          executionOrder: index + 1,
          categoryLabel: CATEGORY_LABELS[row.category] || CATEGORY_LABELS.other,
          executedTimeLabel: formatClock(row.executedAt),
        }));
      },
      executedCommandCount() {
        return this.executedCommandRows.length;
      },
      recentExecutedCommandRows() {
        return [...this.executedCommandRows].reverse().map((row) => ({
          ...row,
          cnSummary: buildCnSummary(row),
          cnDetail: buildCnDetail(row),
        }));
      },
      pendingCategoryStats() {
        const counts = countCategories(this.state?.commandSequence ?? []);
        return Object.keys(CATEGORY_LABELS).map((key) => ({
          key,
          label: CATEGORY_LABELS[key],
          count: counts[key] || 0,
        }));
      },
      executedCategoryStats() {
        const counts = countCategories(this.executedCommands);
        return Object.keys(CATEGORY_LABELS).map((key) => ({
          key,
          label: CATEGORY_LABELS[key],
          count: counts[key] || 0,
        }));
      },
      commandParserTabs() {
        return [
          { key: "all", label: "全部" },
          ...Object.keys(CATEGORY_LABELS).map((key) => ({
            key,
            label: CATEGORY_LABELS[key],
          })),
        ];
      },
      executedTabCounts() {
        const counts = {
          all: this.executedCommandRows.length,
          attack: 0,
          defend: 0,
          gather: 0,
          build: 0,
          train: 0,
          scout: 0,
          tech: 0,
          other: 0,
        };
        for (const row of this.executedCommandRows) {
          const cat = Object.prototype.hasOwnProperty.call(CATEGORY_LABELS, row.category) ? row.category : "other";
          counts[cat] += 1;
        }
        return counts;
      },
      pendingTabCounts() {
        const counts = {
          all: this.commandRows.length,
          attack: 0,
          defend: 0,
          gather: 0,
          build: 0,
          train: 0,
          scout: 0,
          tech: 0,
          other: 0,
        };
        for (const row of this.commandRows) {
          const cat = Object.prototype.hasOwnProperty.call(CATEGORY_LABELS, row.category) ? row.category : "other";
          counts[cat] += 1;
        }
        return counts;
      },
      filteredExecutedCommandRows() {
        const tab = this.activeExecutedCommandTab;
        const rows = this.recentExecutedCommandRows;
        if (tab === "all") {
          return rows;
        }
        return rows.filter((r) => {
          const cat = Object.prototype.hasOwnProperty.call(CATEGORY_LABELS, r.category) ? r.category : "other";
          return cat === tab;
        });
      },
      filteredPendingCommandRows() {
        const tab = this.activePendingCommandTab;
        const rows = this.commandRows;
        if (tab === "all") {
          return rows;
        }
        return rows.filter((r) => {
          const cat = Object.prototype.hasOwnProperty.call(CATEGORY_LABELS, r.category) ? r.category : "other";
          return cat === tab;
        });
      },
      situationCards() {
        return deriveSituation(this.state).cards;
      },
      awarenessWorkbench() {
        return (
          this.state?.agentWorkbench?.situationAwareness ||
          createEmptyState().agentWorkbench.situationAwareness
        );
      },
      intentRiskWorkbench() {
        return (
          this.state?.agentWorkbench?.intentRisk ||
          createEmptyState().agentWorkbench.intentRisk
        );
      },
      behaviorIntentItems() {
        return this.intentRiskWorkbench.behaviorIntent || [];
      },
      riskRecognitionItems() {
        return this.intentRiskWorkbench.riskRecognition || [];
      },
      situationHeadline() {
        return deriveSituation(this.state).headline;
      },
      situationAdvice() {
        return deriveSituation(this.state).advice;
      },
      supplyLabel() {
        return `${this.game.supplyUsed ?? 0} / ${this.game.supplyCap ?? 0}`;
      },
      displayGameTime() {
        return this.game.timeFormatted || "--:--";
      },
      formattedUpdatedAt() {
        return formatClock(this.state?.updatedAt);
      },
      statusLabel() {
        if (this.fetchError) {
          return "连接中断";
        }
        return this.state?.status === "running" ? "实时运行" : "空闲";
      },
      statusClass() {
        if (this.fetchError) {
          return "danger";
        }
        return this.state?.status === "running" ? "live" : "idle";
      },
      rightPanelMeta() {
        if (!this.plannerTailItem) {
          return "等待方案生成";
        }
        return `方案生成智能体 / ${this.plannerTailItem.title}`;
      },
      rightLayerOptions() {
        return [
          { key: "overview", label: "系统总览" },
          { key: "awareness", label: "态势感知" },
          { key: "force", label: "兵力布势" },
          { key: "intent", label: "意图识别" },
          { key: "risk", label: "风险分析" },
          { key: "killchain", label: "杀伤链发现" },
          { key: "critical", label: "关键打击节点识别" },
          { key: "plan", label: "作战方案生成" },
        ];
      },
      /** 顶栏单独展示「系统总览」，此处仅渲染其余页签 */
      rightLayerTabOptions() {
        return this.rightLayerOptions.filter((opt) => opt.key !== "overview");
      },
      /** 当前激活模块的沙盘独立配置；模块不在映射表中时回退为全开 */
      activeModuleConfig() {
        const fallback = { showFog: true, showResources: true, showRangeCircles: false, showUnits: true };
        return this.moduleMinimapConfigs[this.activeRightLayer] || fallback;
      },
      deerflowAwarenessSensing() {
        // 本地实时读取（dashboard_state.json）——不依赖 智能体 流水线产物。
        const game = this.state?.game || {};
        const frame = this.state?.latestFeatureFrame || {};
        const frameVision = frame.vision && typeof frame.vision === "object" ? frame.vision : {};
        return {
          game_time: game.timeFormatted || "",
          game_loop: frame.game_loop ?? game.iteration ?? "",
          player_id: 1,
          map: {
            name: game.mapName || "",
            start_location: game.startLocation ? [game.startLocation] : [],
            size: null,
          },
          vision: {
            visible_tiles_pct: frameVision.visible_tiles_pct ?? null,
            fog_of_war_pct: frameVision.fog_of_war_pct ?? null,
            enemy_base_visible: Boolean(game.enemyStructuresVisible),
          },
          scouting: {
            stale_after_seconds: 150,
          },
          own: {
            minerals: game.minerals ?? null,
            vespene: game.vespene ?? null,
            supply_used: game.supplyUsed ?? null,
            supply_cap: game.supplyCap ?? null,
            workers: game.workerCount ?? null,
            bases: game.ownBaseCount ?? null,
            units: null,
            buildings: null,
          },
          enemy: {
            visible_supply: game.enemyUnitsVisible ?? null,
            bases_seen: game.enemyStructuresVisible ?? null,
            units: null,
            buildings: null,
          },
        };
      },
      deerflowAwarenessCompute() {
        // 若智能体流水线提供了 S2 compute 产物，则优先使用（用于驱动可视化随分析产物变化）
        const pipelineCompute = this.deerflowRisk?.stages?.compute;
        if (pipelineCompute && typeof pipelineCompute === "object" && Object.keys(pipelineCompute).length > 0) {
          return pipelineCompute;
        }
        // 本地实时读取（dashboard_state.json）——用现有快照指标做轻量计算展示。
        const game = this.state?.game || {};
        const ownSupply = Number(game.supplyUsed ?? 0);
        const enemySupplyProxy = Number(game.enemyUnitsVisible ?? 0); // 仅可见单位数的粗代理
        const ownArmyProxy = Number(game.armyCount ?? 0);
        const enemyArmyProxy = Number(game.enemyUnitsVisible ?? 0);
        const ratioSafe = (a, b) => (Number.isFinite(a) && Number.isFinite(b) && b > 0 ? a / b : null);
        return {
          own: {
            army_value: ownArmyProxy,
            eco_income_est: null,
            avg_ground_range: null,
            supply: ownSupply,
          },
          enemy: {
            army_value: enemyArmyProxy,
            eco_income_est: null,
            avg_ground_range: null,
            visible_supply: enemySupplyProxy,
          },
          ratios: {
            army_value: ratioSafe(ownArmyProxy, enemyArmyProxy),
            supply: ratioSafe(ownSupply, enemySupplyProxy),
          },
          trend_30s: {},
          trend_30s_note: "本面板已切为本地实时读取；敌方为可见代理指标。",
        };
      },
      deerflowAwarenessMeta() {
        if (this.deerflowRiskLoading) return "智能体流水线运行中";
        if (!this.deerflowRisk.thread_id) return "等待分析结果";
        return `线程 ${this.deerflowRisk.thread_id.slice(0, 8)}... · S2 态势计算`;
      },
      /** S1 事实层已与「兵力布势」总览合并；此处供模板展示环境/时间补充 */
      forceDispositionHasPipelineSensing() {
        const s = this.deerflowAwarenessSensing;
        return s && typeof s === "object" && Object.keys(s).length > 0;
      },
      forceDispositionTimeDisplay() {
        const s = this.deerflowAwarenessSensing;
        if (s?.game_time) return String(s.game_time);
        return this.displayGameTime;
      },
      forceDispositionGameLoop() {
        const s = this.deerflowAwarenessSensing;
        if (s?.game_loop != null && s.game_loop !== "") return s.game_loop;
        const loop = this.state?.latestFeatureFrame?.game_loop;
        return loop != null && loop !== "" ? loop : null;
      },
      forceDispositionEnvLine() {
        const s = this.deerflowAwarenessSensing || {};
        const map = s.map || {};
        const vision = s.vision || {};
        if (!this.forceDispositionHasPipelineSensing) return "";
        const parts = [];
        if (map.name || Array.isArray(map.start_location)) {
          const loc = Array.isArray(map.start_location) ? map.start_location.join(", ") : "--";
          parts.push(`地图 ${map.name || "--"} · 开局点 ${loc}`);
        }
        const pct = Math.round((Number(vision.visible_tiles_pct) || 0) * 100);
        parts.push(`视野 ${pct}%${vision.enemy_base_visible ? " · 敌主基地可见" : " · 敌主基地未视野"}`);
        return parts.filter(Boolean).join(" · ");
      },
      forceDispositionSensingEnemyLine() {
        const s = this.deerflowAwarenessSensing || {};
        const e = s.enemy || {};
        const o = s.own || {};
        if (!this.forceDispositionHasPipelineSensing) return "";
        const parts = [];
        if (e.visible_supply != null && e.visible_supply !== "") parts.push(`敌可见供给 ${e.visible_supply}`);
        if (e.units && typeof e.units === "object") {
          const n = Object.values(e.units).reduce((a, b) => a + (Number(b) || 0), 0);
          if (n) parts.push(`敌作战单位(快照) ${n}`);
        }
        if (e.bases_seen != null && e.bases_seen !== "") parts.push(`已见敌基地 ${e.bases_seen}`);
        if (o.workers != null && o.workers !== "") parts.push(`我方工蜂 ${o.workers}`);
        if (o.bases != null && o.bases !== "") parts.push(`我方基地 ${o.bases}`);
        return parts.length ? `流水线 S1 快照：${parts.join(" · ")}` : "";
      },
      deerflowAwarenessComputeRows() {
        const compute = this.deerflowAwarenessCompute || {};
        const own = compute.own || {};
        const enemy = compute.enemy || {};
        const ratios = compute.ratios || {};
        const trend = compute.trend_30s || {};
        const sensing = this.deerflowAwarenessSensing || {};
        const sensingOwn = sensing.own || {};
        const sensingEnemy = sensing.enemy || {};
        const fmt = (v) => (Number.isFinite(Number(v)) ? Number(v).toFixed(1) : "--");
        const fmtRatio = (v) => (Number.isFinite(Number(v)) ? Number(v).toFixed(2) : "--");
        const fmt2 = (v) => (Number.isFinite(Number(v)) ? Number(v).toFixed(2) : "--");
        const safeNum = (v) => {
          const n = Number(v);
          return Number.isFinite(n) ? n : null;
        };
        const pctFromRatio = (ratioNum) => {
          const r = Number(ratioNum);
          if (!Number.isFinite(r) || r <= 0) return { ownPct: 50, enemyPct: 50 };
          const ownPct = Math.max(5, Math.min(95, (r / (1 + r)) * 100));
          return { ownPct, enemyPct: 100 - ownPct };
        };
        const make = (key, label, icon, ownVal, enemyVal, ratioNum, accent) => {
          const ratioText = ratioNum == null ? "--" : fmtRatio(ratioNum);
          const ratioN = Number(ratioNum);
          const ratioOk = Number.isFinite(ratioN) && ratioN > 0;
          const { ownPct, enemyPct } = ratioOk ? pctFromRatio(ratioN) : { ownPct: 50, enemyPct: 50 };
          const hint =
            ratioOk && ratioN >= 1.12
              ? "我方优势"
              : ratioOk && ratioN <= 0.88
                ? "敌方优势"
                : ratioOk
                  ? "接近均势"
                  : "无比值";
          return {
            key,
            label,
            icon,
            accent,
            own: ownVal,
            enemy: enemyVal,
            ratioText,
            ratioNum: ratioOk ? ratioN : null,
            ownPct,
            enemyPct,
            hint,
          };
        };
        // 固定展示 5 行：军力估值 / 补给对比 / 基地数(快照) / 工人数量(快照) / 近30秒军力变化
        const ownBases = safeNum(sensingOwn.bases);
        const enemyBasesSeen = safeNum(sensingEnemy.bases_seen);
        const basesRatio =
          ownBases != null && enemyBasesSeen != null && enemyBasesSeen > 0 ? ownBases / enemyBasesSeen : null;
        const ownWorkers = safeNum(sensingOwn.workers);
        return [
          make(
            "army_value",
            "军力估值",
            "⚔",
            own.army_value ?? "--",
            enemy.army_value ?? "--",
            ratios.army_value,
            "strike",
          ),
          make(
            "supply",
            "补给对比",
            "⛯",
            own.supply ?? "--",
            enemy.visible_supply ?? "--",
            ratios.supply,
            "support",
          ),
          make(
            "bases",
            "基地数（快照）",
            "⌂",
            ownBases != null ? `${ownBases}` : "--",
            enemyBasesSeen != null ? `${enemyBasesSeen}` : "--",
            basesRatio,
            "support",
          ),
          make(
            "workers",
            "工人数量（快照）",
            "⛏",
            ownWorkers != null ? `${ownWorkers}` : "--",
            "--",
            null,
            "support",
          ),
          make(
            "trend_value",
            "近30秒军力变化",
            "↯",
            trend.own_army_value_delta ?? "--",
            trend.enemy_army_value_delta ?? "--",
            null,
            "strike",
          ),
        ];
      },
      /** 智能体 S1 写入 Redis 的 sensing 快照（面板展示用） */
      deerflowAwarenessSensingHasPayload() {
        const s = this.deerflowAwarenessSensing;
        return Boolean(s && typeof s === "object" && Object.keys(s).length > 0);
      },
      deerflowAwarenessSensingBlocks() {
        const s = this.deerflowAwarenessSensing || {};
        if (!this.deerflowAwarenessSensingHasPayload) return [];
        const fmtUnitMap = (m) => {
          if (!m || typeof m !== "object") return "—";
          const parts = Object.entries(m)
            .filter(([, c]) => Number(c) > 0)
            .slice(0, 16)
            .map(([t, c]) => `${t}×${c}`);
          return parts.length ? parts.join(" · ") : "—";
        };
        const fmtBld = (m) => fmtUnitMap(m);
        const buildKeyRegionLines = () => {
          const start = String(this.game?.startLocation || "A1");
          const ownPrefix = start.startsWith("B") ? "B" : "A";
          const enemyPrefix = ownPrefix === "A" ? "B" : "A";
          const markers = Array.isArray(this.state?.minimapOverlay) ? this.state.minimapOverlay : [];
          const ownDetails = Array.isArray(this.game?.ownForceDetails) ? this.game.ownForceDetails : [];
          const enemyDetails = Array.isArray(this.game?.enemyVisibleDetails) ? this.game.enemyVisibleDetails : [];
          const hasMarker = (label) => markers.some((m) => String(m.locationLabel || "") === label);
          const hasOwn = (label) => ownDetails.some((row) => String(row.location || "") === label) || hasMarker(label);
          const hasEnemy = (label) => enemyDetails.some((row) => String(row.location || "") === label);
          const statusFor = (label) => {
            if (label === `${enemyPrefix}1` && s.vision?.enemy_base_visible) return { dot: "ok", status: "在控" };
            if (hasOwn(label)) return { dot: "ok", status: "在控" };
            if (hasEnemy(label)) return { dot: "lost", status: "失控" };
            return { dot: "fog", status: "迷雾中" };
          };
          const regions = [
            { name: "萨尔纳加瞭望塔（中央北）", label: `${ownPrefix}7` },
            { name: "萨尔纳加瞭望塔（中央南）", label: `${enemyPrefix}7` },
            { name: "关键矿区（我方二矿）", label: `${ownPrefix}2` },
            { name: "关键矿区（敌方主矿）", label: `${enemyPrefix}1` },
            { name: "关键矿区（敌方二矿）", label: `${enemyPrefix}2` },
          ];
          return regions.map((region) => {
            const state = statusFor(region.label);
            return {
              text: `${region.name} ${region.label}：${state.status}`,
              dot: state.dot,
              className: `deerflow-sensing-line--${state.dot}`,
            };
          });
        };
        const buildEnemyProjectionLines = () => {
          const game = this.game || {};
          const frame = this.state?.latestFeatureFrame || {};
          const enemyComps = Array.isArray(game.enemyVisibleComposition) ? game.enemyVisibleComposition : [];
          const enemyDetails = Array.isArray(game.enemyVisibleDetails) ? game.enemyVisibleDetails : [];
          const enemyUnitsObj = s.enemy?.units && typeof s.enemy.units === "object" ? s.enemy.units : {};
          const enemyBuildingsObj = s.enemy?.buildings && typeof s.enemy.buildings === "object" ? s.enemy.buildings : {};
          const names = [
            ...enemyComps.map((row) => row?.name),
            ...enemyDetails.map((row) => row?.name),
            ...Object.keys(enemyUnitsObj),
            ...Object.keys(enemyBuildingsObj),
          ].filter(Boolean);
          const normNames = names.map((name) => normalizeSc2Token(name)).filter(Boolean);
          const hasAny = (...tokens) => tokens.some((token) => normNames.some((name) => name.includes(token)));
          const sumMap = (m) => Object.values(m || {}).reduce((sum, value) => sum + (Number(value) || 0), 0);
          const visibleUnits = Math.max(
            Number(game.enemyUnitsVisible || 0),
            enemyComps.reduce((sum, row) => sum + (Number(row?.count) || 0), 0),
            sumMap(enemyUnitsObj),
          );
          const visibleStructures = Math.max(Number(game.enemyStructuresVisible || 0), sumMap(enemyBuildingsObj));
          const timeSeconds = Number(frame.time_seconds);
          const minute = Number.isFinite(timeSeconds) ? timeSeconds / 60 : 0;
          const inferredBases = visibleStructures > 0
            ? Math.max(1, Math.min(4, Number(s.enemy?.bases_seen || 0) || (minute >= 6 ? 2 : 1)))
            : (minute >= 7 ? 3 : minute >= 4 ? 2 : 1);
          const workerLow = Math.max(12, Math.round(12 + minute * 2.2 + (inferredBases - 1) * 5));
          const workerHigh = Math.max(workerLow + 2, Math.round(workerLow + 6 + Math.min(8, minute)));
          const economyIntel = visibleStructures > 0
            ? `敌方经济推演：已见建筑 ${visibleStructures}，估计 ${inferredBases} 基地运转，工人约 ${workerLow}-${workerHigh}。`
            : `敌方经济推演：未见经济建筑，按时间推演约 ${inferredBases} 基地，工人约 ${workerLow}-${workerHigh}，需侦察校准。`;

          let race = "未知种族";
          if (hasAny("marine", "marauder", "reaper", "hellion", "siegetank", "barracks", "factory", "starport", "commandcenter", "scv")) {
            race = "人族";
          } else if (hasAny("zealot", "stalker", "adept", "gateway", "nexus", "stargate", "robotics", "cyberneticscore", "probe")) {
            race = "星灵";
          } else if (hasAny("zergling", "roach", "hydralisk", "hatchery", "lair", "spire", "spawningpool", "drone")) {
            race = "异虫";
          }

          let tech = "科技树未知，当前信息不足";
          if (hasAny("battlecruiser", "fusioncore")) tech = "人族高科技空军 / 大和方向";
          else if (hasAny("starport", "medivac", "viking", "liberator", "banshee", "raven")) tech = "人族星港科技，需防空投或空军压制";
          else if (hasAny("factory", "hellion", "siegetank", "cyclone", "thor")) tech = "人族重工机械线，注意火车/坦克推进";
          else if (hasAny("barracks", "marine", "marauder", "reaper")) tech = "人族兵营生化线或死神侦察开";
          else if (hasAny("carrier", "fleetbeacon", "tempest", "voidray")) tech = "星灵舰队航标 / 空军科技";
          else if (hasAny("stargate", "oracle", "phoenix")) tech = "星灵星门科技，注意先知骚扰";
          else if (hasAny("robotics", "immortal", "observer", "colossus")) tech = "星灵机械台科技，注意不朽者/巨像";
          else if (hasAny("gateway", "stalker", "adept", "zealot", "cyberneticscore")) tech = "星灵折跃门地面科技";
          else if (hasAny("spire", "mutalisk", "corruptor")) tech = "异虫飞行科技，注意飞龙转场";
          else if (hasAny("roachwarren", "roach", "ravager")) tech = "异虫蟑螂/破坏者地面线";
          else if (hasAny("spawningpool", "zergling", "banelingnest", "baneling")) tech = "异虫跳虫/毒爆基础科技";

          const ownArmy = Number(game.armyCount || 0);
          const techBonus = /高科技|星港|空军|机械|舰队|飞行|坦克|毒爆/.test(tech) ? 18 : 6;
          const hiddenBonus = visibleUnits <= 0 && this.enemyMainTracker.lastSeenAtMs ? 16 : 0;
          const threatScore = Math.max(0, Math.min(100, Math.round(visibleUnits * 10 + visibleStructures * 4 + techBonus + hiddenBonus - ownArmy * 3)));
          const threatLevel = threatScore >= 70 ? "高" : threatScore >= 38 ? "中" : "低";
          const forceEval = visibleUnits > 0
            ? `威胁告警与战力评估：${threatLevel}危（${threatScore}/100），敌可见战斗单位 ${visibleUnits}，我方军力 ${ownArmy}。`
            : `威胁告警与战力评估：${threatLevel}危（${threatScore}/100），敌主力不在视野内，当前主要风险来自情报缺口。`;

          let missingLine = "敌方主力消失时间：尚未建立可见接触。";
          if (visibleUnits > 0) {
            missingLine = `敌方主力消失时间：当前可见（${visibleUnits}）。`;
          } else if (this.enemyMainTracker.lastSeenAtMs) {
            const elapsed = Math.max(0, Math.floor((Date.now() - this.enemyMainTracker.lastSeenAtMs) / 1000));
            const mm = Math.floor(elapsed / 60);
            const ss = String(elapsed % 60).padStart(2, "0");
            const seenAt = this.enemyMainTracker.lastSeenGameTime ? `，上次可见于 ${this.enemyMainTracker.lastSeenGameTime}` : "";
            missingLine = `敌方主力消失时间：${mm}分${ss}秒${seenAt}。`;
          }

          return [
            economyIntel,
            `敌方科技树推测：${race} · ${tech}。`,
            forceEval,
            {
              text: missingLine,
              className: visibleUnits <= 0 && this.enemyMainTracker.lastSeenAtMs ? "deerflow-sensing-line--stale" : "",
            },
          ];
        };
        const blocks = [];
        blocks.push({ title: "高价值区域监控状态", lines: buildKeyRegionLines() });
        blocks.push({ title: "敌方状态推演", lines: buildEnemyProjectionLines() });
        const own = s.own || {};
        const ownLines = [];
        if (own.minerals != null) ownLines.push(`晶矿 ${own.minerals}`);
        if (own.vespene != null) ownLines.push(`瓦斯 ${own.vespene}`);
        if (own.supply_used != null || own.supply_cap != null)
          ownLines.push(`补给 ${own.supply_used ?? "—"}/${own.supply_cap ?? "—"}`);
        if (own.workers != null) ownLines.push(`工人 ${own.workers}`);
        if (own.bases != null) ownLines.push(`基地数 ${own.bases}`);
        ownLines.push(`作战单位：${fmtUnitMap(own.units)}`);
        ownLines.push(`建筑：${fmtBld(own.buildings)}`);
        blocks.push({ title: "我方（事实层）", lines: ownLines });
        const en = s.enemy || {};
        const enLines = [];
        if (en.visible_supply != null) enLines.push(`可见供给-used ${en.visible_supply}`);
        if (en.bases_seen != null) enLines.push(`已见敌方基地 ${en.bases_seen}`);
        enLines.push(`可见作战单位：${fmtUnitMap(en.units)}`);
        enLines.push(`可见建筑：${fmtBld(en.buildings)}`);
        blocks.push({ title: "敌方可见（事实层）", lines: enLines });
        return blocks;
      },
      deerflowRiskPipelineStages() {
        const stages =
          this.deerflowRisk?.stages && typeof this.deerflowRisk.stages === "object"
            ? this.deerflowRisk.stages
            : {};
        const order = [
          { key: "sensing", label: "S1 sensing" },
          { key: "compute", label: "S2 compute" },
          { key: "disposition", label: "S3 disposition" },
          { key: "intent", label: "S4 intent" },
          { key: "risk_id", label: "S5 risk_id" },
          { key: "risk_response", label: "S6 risk_response" },
        ];
        return order.map(({ key, label }) => {
          const obj = stages[key];
          const filled = Boolean(obj && typeof obj === "object" && Object.keys(obj).length > 0);
          return {
            key,
            label,
            filled,
            fieldCount: filled ? Object.keys(obj).length : 0,
          };
        });
      },
      deerflowRiskConsoleTracesPreview() {
        const t = Array.isArray(this.deerflowRisk?.traces) ? this.deerflowRisk.traces : [];
        return t.slice(-16);
      },
      deerflowRiskDispositionSnippet() {
        const d = this.deerflowRisk?.stages?.disposition;
        if (!d || typeof d !== "object") return "";
        try {
          const flat = JSON.stringify(d, null, 0);
          return flat.length > 420 ? `${flat.slice(0, 417)}…` : flat;
        } catch {
          return "";
        }
      },
      deerflowRiskResponseSnippet() {
        const r = this.deerflowRisk?.response;
        if (!r || typeof r !== "object" || !Object.keys(r).length) return "";
        try {
          const flat = JSON.stringify(r, null, 2);
          return flat.length > 2400 ? `${flat.slice(0, 2397)}…\n…` : flat;
        } catch {
          return "";
        }
      },
      /** 分析控制台：盘面快照一行（来自 S1 sensing） */
      deerflowConsoleGameSnapshot() {
        const s = this.deerflowRisk?.stages?.sensing;
        if (!s || typeof s !== "object" || !Object.keys(s).length) return "";
        const bits = [];
        if (s.game_time != null && s.game_time !== "") bits.push(`盘面时间 ${s.game_time}`);
        const m = s.map || {};
        if (m.name) bits.push(`地图 ${m.name}`);
        const v = s.vision || {};
        if (typeof v.enemy_base_visible === "boolean") {
          bits.push(v.enemy_base_visible ? "敌方主基地在视野内" : "敌方主基地当前不可见");
        }
        return bits.join(" · ");
      },
      /** 意图摘要（可读） */
      deerflowConsoleIntentSummary() {
        const i = this.deerflowRisk?.intent;
        if (!i || typeof i !== "object" || !Object.keys(i).length) return "";
        const strat = String(i.primary_strategy_cn || i.primary_strategy || "").trim();
        const c = Number(i.confidence);
        if (strat && Number.isFinite(c)) return `敌方策略倾向：${strat}（置信度 ${c.toFixed(2)}）`;
        if (strat) return `敌方策略倾向：${strat}`;
        return "";
      },
      /** 风险量级摘要 */
      deerflowConsoleRiskSummary() {
        const r = this.deerflowRisk?.risk;
        if (!r || typeof r !== "object") return "";
        const level = String(r.overall_risk_level_cn || r.overall_risk_level || "").trim();
        const arr = Array.isArray(r.risks) ? r.risks : [];
        const n = arr.length;
        if (level && n) return `整体风险等级：${level} · 本评估共列出 ${n} 条威胁`;
        if (level) return `整体风险等级：${level}`;
        if (n) return `本评估共列出 ${n} 条威胁`;
        return "";
      },
      deerflowConsoleThreatHighlights() {
        const rows = Array.isArray(this.deerflowRisk?.risk?.risks) ? this.deerflowRisk.risk.risks : [];
        const tyMap = {
          army_push: "正面推进",
          harass: "骚扰牵制",
          tech: "科技威胁",
          economy_denial: "经济压制",
          air_raid: "空袭威胁",
          unknown: "未知",
        };
        return rows.slice(0, 5).map((it, idx) => {
          const ty = String(it?.threat_type || "unknown").toLowerCase();
          const typeLabel = String(it?.threat_type_cn || "").trim() || tyMap[ty] || ty || "威胁";
          const tgt =
            it?.target != null && String(it.target).trim() !== ""
              ? String(it.target).trim()
              : "未命名目标";
          const parts = [`${idx + 1}. ${typeLabel} · ${tgt}`];
          if (it?.threat_score != null && it.threat_score !== "") parts.push(`威胁分 ${it.threat_score}`);
          if (Number.isFinite(Number(it?.time_to_impact_sec))) parts.push(`约 ${it.time_to_impact_sec}s 内`);
          const rs = String(it?.reasoning || "").trim();
          const shortReason =
            rs.length > 140 ? `${rs.slice(0, 137)}…` : rs;
          return {
            key: `th-${idx}-${tgt}`,
            headline: parts.join(" · "),
            reason: shortReason,
          };
        });
      },
      /** risk_response 中用户最关心的处置摘要与兵力调整要点 */
      deerflowConsoleResponsePlan() {
        const r = this.deerflowRisk?.response;
        if (!r || typeof r !== "object" || !Object.keys(r).length) return null;
        const plan = String(r.plan_summary || "").trim();
        const adjustments = Array.isArray(r.force_adjustments) ? r.force_adjustments : [];
        const bullets = adjustments.slice(0, 6).map((a, i) => {
          const act = String(a?.action || "").trim() || "调整";
          const u = String(a?.unit_or_building || "").trim() || "—";
          const q = a?.quantity != null && a.quantity !== "" ? `${a.quantity}` : "";
          const qText = q !== "" ? ` ×${q}` : "";
          const rs = String(a?.reason || "").trim();
          const rsShort = rs.length > 100 ? `${rs.slice(0, 97)}…` : rs;
          let line = `${act} · ${u}${qText}`;
          if (rsShort) line += `：${rsShort}`;
          return { key: `adj-${i}`, line };
        });
        const cmds = Array.isArray(r.commands) ? r.commands : [];
        const hasBody =
          Boolean(plan) ||
          bullets.length > 0 ||
          cmds.length > 0 ||
          Boolean(r.hitl_required) ||
          String(r.hitl_prompt || "").trim().length > 0;
        if (!hasBody) return null;
        return {
          plan,
          bullets,
          mode: String(r.mode || "").trim(),
          commandCount: cmds.length,
          hitl: Boolean(r.hitl_required),
          hitlPrompt: String(r.hitl_prompt || "").trim(),
        };
      },
      /** 给指挥官的步骤提示（非机密调试信息） */
      deerflowConsoleGuidanceBullets() {
        const tips = [];
        if (!this.deerflowRisk.thread_id) {
          tips.push("在「态势分析」点击「刷新分析」，触发现势感知→风险评估全链路（需 Ask API、Redis、LLM 可用）。");
          return tips;
        }
        if (this.deerflowRisk.ok) {
          tips.push("本轮风险评估已通过校验：可到下方「四维风险分析」按进攻/探测等维度细看，并结合「兵力布势」生成协同操作建议。");
        } else if (String(this.deerflowRisk.errorText || "").trim()) {
          tips.push("若流水线失败：请核对本机问答栈是否在 8011 端口监听、Redis 是否有 `situation:current` 以及 LLM Key 是否正常。");
        }
        const stages = this.deerflowRiskPipelineStages || [];
        const missShort = stages.filter((s) => !s.filled).map((s) => s.label);
        if (missShort.length && this.deerflowRisk.thread_id) {
          tips.push(`尚未从 Redis 读到的阶段：${missShort.join("、")}。常见原因是模型未完成 \`sc2_analysis_cache_set\` 或未写入对应 thread。`);
        }
        return tips.slice(0, 4);
      },
      deerflowIntentMeta() {
        if (this.deerflowRiskLoading) return "智能体流水线运行中";
        if (!this.deerflowRisk.thread_id) return "等待分析结果";
        return "最新一轮分析结果";
      },
      deerflowIntentHistoryRecent() {
        return Array.isArray(this.deerflowHistory?.intent) ? this.deerflowHistory.intent.slice(0, 2) : [];
      },
      deerflowIntentDimensionRows() {
        const dims = this.deerflowRisk?.intent?.intent_dimensions || {};
        const order = [
          ["attack", "进攻意图"],
          ["defense", "防守意图"],
          ["detect", "探测意图"],
          ["support", "保障意图"],
        ];
        return order.map(([key, label]) => {
          const d = dims?.[key] || {};
          const levelRaw = String(d.level || "medium").toLowerCase();
          const levelKey = levelRaw === "high" ? "high" : levelRaw === "low" ? "low" : "medium";
          const levelCn = levelKey === "high" ? "高" : levelKey === "low" ? "低" : "中";
          return {
            key,
            label,
            level: levelKey,
            levelCn,
            description: String(d.description_cn || `${label}：证据不足，维持持续观察。`),
          };
        });
      },
      deerflowIntentEvidenceGroups() {
        const ebd = this.deerflowRisk?.intent?.evidence_by_dimension || {};
        const order = [
          ["attack", "进攻意图"],
          ["defense", "防守意图"],
          ["detect", "探测意图"],
          ["support", "保障意图"],
        ];
        return order.map(([key, label]) => ({
          key,
          label,
          items: Array.isArray(ebd?.[key]) ? ebd[key] : [],
        }));
      },
      /** 本轮 intent JSON 驱动的「意图分析智能体」展示块（非历史队列） */
      deerflowIntentLlmAnalysisBlocks() {
        const intent = this.deerflowRisk?.intent;
        if (!intent || typeof intent !== "object") return [];
        const hasDim =
          intent.intent_dimensions &&
          typeof intent.intent_dimensions === "object" &&
          Object.keys(intent.intent_dimensions).length > 0;
        const hasAny =
          hasDim ||
          intent.primary_strategy ||
          intent.primary_strategy_cn ||
          intent.confidence != null ||
          (Array.isArray(intent.evidence) && intent.evidence.length);
        if (!hasAny) return [];

        const blocks = [];
        const strat = String(
          intent.primary_strategy_cn || intent.primary_strategy || "",
        ).trim();
        const conf = Number(intent.confidence);
        let headBody = "";
        if (strat && Number.isFinite(conf)) headBody = `${strat}（置信度 ${conf.toFixed(2)}）`;
        else if (strat) headBody = strat;
        else if (Number.isFinite(conf)) headBody = `综合推断（置信度 ${conf.toFixed(2)}）；与上方四维意图一致`;
        else headBody = hasDim ? "综合推断详见上方四维意图卡片" : "待定";
        blocks.push({ key: "head", title: "策略推断", body: headBody });

        const ev = Array.isArray(intent.evidence) ? intent.evidence.map((x) => String(x).trim()).filter(Boolean) : [];
        if (ev.length) {
          blocks.push({
            key: "evidence",
            title: "模型综合要点",
            body: ev.slice(0, 12).join("；"),
          });
        }

        const tw = intent.timing_window;
        if (tw && typeof tw === "object") {
          const pa = [];
          if (tw.critical_window_start_sec != null || tw.critical_window_end_sec != null) {
            pa.push(
              `关键窗口 ${tw.critical_window_start_sec ?? "--"}–${tw.critical_window_end_sec ?? "--"} s`,
            );
          }
          if (tw.reason) pa.push(String(tw.reason));
          if (pa.length) blocks.push({ key: "timing", title: "时机窗口", body: pa.join("；") });
        }

        const routes = Array.isArray(intent.expected_attack_routes)
          ? intent.expected_attack_routes
          : [];
        if (routes.length) {
          const lines = routes.slice(0, 6).map((r) => {
            const d = String(r?.direction ?? "--");
            const p = Number(r?.probability);
            const eta = Number(r?.eta_seconds);
            let s = d;
            if (Number.isFinite(p)) s += ` · 概率 ${(p <= 1 ? p * 100 : p).toFixed(0)}%`;
            if (Number.isFinite(eta)) s += ` · ETA ${Math.round(eta)}s`;
            const inv = Array.isArray(r?.involved_units) ? r.involved_units.filter(Boolean).slice(0, 4) : [];
            if (inv.length) s += ` · 涉及 ${inv.join("，")}`;
            return s;
          });
          blocks.push({ key: "routes", title: "可能施压轴向", body: lines.join("\n") });
        }

        const ts = intent.tech_switch_risk;
        if (ts && typeof ts === "object" && Object.keys(ts).length) {
          const sw = ts.switching_to;
          const swText =
            sw == null || String(sw).trim() === "" ? "未标明转型目标" : String(sw).trim();
          const p = Number(ts.probability);
          const pt = Number.isFinite(p) ? (p <= 1 ? `${(p * 100).toFixed(0)}%` : `${p}%`) : "--";
          blocks.push({
            key: "tech",
            title: "转型 / 科技风险",
            body: `${swText} · 估算概率 ${pt}`,
          });
        }

        return blocks;
      },
      deerflowRiskMeta() {
        if (this.deerflowRiskLoading) return "智能体流水线运行中";
        if (this.deerflowRisk.errorText) return "执行失败";
        if (!this.deerflowRisk.thread_id) return "等待分析结果";
        return `线程 ${this.deerflowRisk.thread_id.slice(0, 8)}...`;
      },
      deerflowRiskHistoryRecent() {
        return Array.isArray(this.deerflowHistory?.risk) ? this.deerflowHistory.risk.slice(0, 2) : [];
      },
      deerflowRiskDimensionGroups() {
        const rows = Array.isArray(this.deerflowRisk?.risk?.risks) ? this.deerflowRisk.risk.risks : [];
        const groups = {
          attack: { key: "attack", label: "进攻风险", items: [] },
          defense: { key: "defense", label: "防御风险", items: [] },
          detect: { key: "detect", label: "探测风险", items: [] },
          support: { key: "support", label: "保障风险", items: [] },
        };
        const pickKey = (threatType) => {
          const t = String(threatType || "").toLowerCase();
          if (t === "army_push" || t === "harass" || t === "air_raid") return "attack";
          if (t === "tech") return "detect";
          if (t === "economy_denial") return "support";
          return "defense";
        };
        for (const item of rows) {
          const key = pickKey(item?.threat_type);
          groups[key].items.push(item);
        }
        return [groups.attack, groups.defense, groups.detect, groups.support].map((g) => {
          const top = g.items[0] || {};
          return {
            ...g,
            count: g.items.length,
            topTarget: top.target || "无",
            topScore: top.threat_score ?? "--",
            topEta: Number.isFinite(Number(top.time_to_impact_sec)) ? Number(top.time_to_impact_sec) : null,
            topReason: top.reasoning || "",
          };
        });
      },
      /** 态势分析：全页工作台（沙盘 + 模块内容）是否显示 */
      situationModuleOverlayActive() {
        switch (this.activeRightLayer) {
          case "awareness":
          case "force":
          case "intent":
          case "risk":
          case "killchain":
          case "critical":
          case "plan":
            return true;
          default:
            return false;
        }
      },
      situationModuleOverlayTitle() {
        const hit = this.rightLayerOptions.find((opt) => opt.key === this.activeRightLayer);
        return hit ? hit.label : "";
      },
      rightLayerMeta() {
        if (this.activeRightLayer === "overview") {
          return "默认层 · 暂无内容";
        }
        if (this.activeRightLayer === "awareness") {
          return this.deerflowAwarenessMeta;
        }
        if (this.activeRightLayer === "force") {
          return this.forceDeploymentMeta;
        }
        if (this.activeRightLayer === "intent") {
          return this.deerflowIntentMeta;
        }
        if (this.activeRightLayer === "risk") {
          return this.deerflowRiskMeta;
        }
        if (this.activeRightLayer === "killchain") {
          return this.killChainMeta;
        }
        if (this.activeRightLayer === "critical") {
          return `${this.criticalTargetLastSeenText} · 显示 ${this.filteredCriticalTargetItems.length}/${this.criticalTargetItems.length} 个`;
        }
        if (this.activeRightLayer === "plan") {
          return this.rightPanelMeta;
        }
        return "--";
      },
      formationWorkbench() {
        return (
          this.state?.agentWorkbench?.formationAdvisor ||
          createEmptyState().agentWorkbench.formationAdvisor
        );
      },
      formationAdjustments() {
        return this.formationWorkbench.adjustments || [];
      },
      formationEvidence() {
        return this.formationWorkbench.evidence || [];
      },
      formationSharedKeys() {
        return this.formationWorkbench.sharedMemoryKeys || [];
      },
      spacetimeWorkbench() {
        return (
          this.state?.agentWorkbench?.spacetimeChain ||
          createEmptyState().agentWorkbench.spacetimeChain
        );
      },
      spacetimeTimeline() {
        return this.spacetimeWorkbench.timeline || [];
      },
      spacetimeLinks() {
        return this.spacetimeWorkbench.causalLinks || [];
      },
      spacetimeHotspots() {
        return this.spacetimeWorkbench.hotspots || [];
      },
      spacetimeInsights() {
        return this.spacetimeWorkbench.insights || [];
      },
      footerStatus() {
        if (this.fetchError) {
          return this.fetchError;
        }
        return this.state?.game?.featureSession
          ? `态势会话：${this.state.game.featureSession}`
          : "等待运行时数据";
      },
    },
    watch: {
      activeChannel() {
        this.plannerAutoFollow = true;
        this.scrollPlannerToBottom(true);
        this.resumePlannerTyping();
      },
      activeAgentView() {
        if (this.activeAgentView === "planner") {
          this.resumePlannerTyping();
          this.scrollPlannerToBottom();
          return;
        }
        this.stopPlannerTyping();
      },
      activePlannerTab() {
        if (this.activePlannerTab === "bus") {
          this.$nextTick(() => {
            this.scrollPlannerToBottom();
          });
        }
        if (this.activePlannerTab === "tasksplit") {
          this.plannerTaskShowAllHistory = false;
        }
        if (this.activePlannerTab === "unitcomp") {
          this.activePlannerUnitFilter = "all";
        }
      },
      activeRightLayer(next) {
        if ((next === "risk" || next === "intent") && !this.deerflowRisk.thread_id && !this.deerflowRiskLoading) {
          this.refreshDeerflowRisk();
        }
        if (next === "killchain") {
          this.$nextTick(() => this.refreshKillChainLlmPlans());
        }
        if (next === "force") {
          this.$nextTick(() => this.attachForceCapHeightObserver());
        } else {
          this.detachForceCapHeightObserver();
        }
        if (next === "overview") {
          this.$nextTick(() => {
            this.attachSysArchKbMacroLinkObserver();
            this.scheduleSysArchKbMacroLinkUpdate();
          });
        } else {
          this.detachSysArchKbMacroLinkObserver();
          this.sysArchKbMacroPathD = "";
          this.sysArchDecisionLinkDs = [];
          this.sysArchBtFeedbackPathD = "";
        }
        if (next !== "critical") {
          this.criticalAimArmed = false;
          this.criticalAimMarkerKey = "";
        }
      },
      plannerThinkingVisible() {
        if (this.plannerThinkingVisible) {
          this.scrollPlannerToBottom();
        }
      },
      activeCriticalTargetCategory() {
        const filtered = this.filteredCriticalTargetItems || [];
        if (!filtered.some((item) => item.key === this.selectedCriticalTargetKey)) {
          this.selectedCriticalTargetKey = filtered[0]?.key || "";
        }
        // 切换分类后需要用户再次点击清单确认瞄准对象
        this.criticalAimArmed = false;
        this.criticalAimMarkerKey = "";
      },
      activeKillChainDimension() {
        const activeGroup = this.killChainActiveGroup;
        if (!activeGroup?.chains?.length) {
          this.selectedKillChainKey = "";
          return;
        }
        if (!activeGroup.chains.some((chain) => chain.key === this.selectedKillChainKey)) {
          this.selectedKillChainKey = activeGroup.chains[0].key;
        }
      },
      killChainDimensionGroups: {
        handler(groups) {
          const safeGroups = Array.isArray(groups) ? groups : [];
          if (!safeGroups.some((group) => group.key === this.activeKillChainDimension)) {
            this.activeKillChainDimension = safeGroups[0]?.key || "strike";
          }
          const activeGroup = safeGroups.find((group) => group.key === this.activeKillChainDimension);
          if (!activeGroup?.chains?.some((chain) => chain.key === this.selectedKillChainKey)) {
            this.selectedKillChainKey = activeGroup?.chains?.[0]?.key || "";
          }
          if (this.activeRightLayer === "killchain") {
            this.$nextTick(() => this.refreshKillChainLlmPlans());
          }
        },
        immediate: true,
      },
      killChainPlans: {
        handler(plans) {
          const safePlans = Array.isArray(plans) ? plans : [];
          if (!safePlans.some((plan) => plan.key === this.selectedKillPlanKey)) {
            this.selectedKillPlanKey = safePlans[0]?.key || "";
          }
        },
        immediate: true,
      },
      liveCriticalTargetItems: {
        handler(items) {
          this.refreshCriticalTargetCache(items);
        },
        immediate: true,
      },
    },
    methods: {
      formatClock,
      syncForceCapBlockPairHeight() {
        const layout = this.$refs.forceLayoutV2;
        const cap = this.$refs.forceCapBlock;
        if (!layout || !cap) {
          return;
        }
        const h = cap.getBoundingClientRect().height;
        if (!(h > 0)) {
          return;
        }
        layout.style.setProperty("--force-cap-block-height", `${Math.round(h)}px`);
      },
      scheduleSysArchKbMacroLinkUpdate() {
        if (this.activeRightLayer !== "overview") {
          return;
        }
        if (this._sysArchKbMacroLinkRaf != null) {
          cancelAnimationFrame(this._sysArchKbMacroLinkRaf);
        }
        this._sysArchKbMacroLinkRaf = requestAnimationFrame(() => {
          this._sysArchKbMacroLinkRaf = null;
          this.updateSysArchKbMacroLink();
          this.updateSysArchDecisionLinks();
          this.updateSysArchBtFeedbackLink();
        });
      },
      updateSysArchDecisionLinks() {
        if (this.activeRightLayer !== "overview") {
          this.sysArchDecisionLinkDs = [];
          return;
        }
        if (typeof window.matchMedia === "function" && !window.matchMedia("(min-width: 1361px)").matches) {
          this.sysArchDecisionLinkDs = [];
          return;
        }
        const canvas = this.$refs.sysArchCanvas;
        if (!canvas) {
          this.sysArchDecisionLinkDs = [];
          return;
        }
        const cr = canvas.getBoundingClientRect();
        if (!(cr.width > 4 && cr.height > 4)) {
          this.sysArchDecisionLinkDs = [];
          return;
        }
        const pairs = [
          ["sys-arch-anchor-input-right", "sys-arch-anchor-macro-left"],
          ["sys-arch-anchor-macro-right", "sys-arch-anchor-fsm-left"],
          ["sys-arch-anchor-fsm-right", "sys-arch-anchor-bt-left"],
        ];
        const out = [];
        const mid = (r) => ({
          x: r.left + r.width / 2 - cr.left,
          y: r.top + r.height / 2 - cr.top,
        });
        for (const [idA, idB] of pairs) {
          const elA = document.getElementById(idA);
          const elB = document.getElementById(idB);
          if (!elA || !elB) {
            continue;
          }
          const p0 = mid(elA.getBoundingClientRect());
          const p1 = mid(elB.getBoundingClientRect());
          const dx = p1.x - p0.x;
          const dy = p1.y - p0.y;
          if (dx < 6) {
            continue;
          }
          const rowTol = Math.max(96, cr.height * 0.18);
          if (Math.abs(dy) > rowTol) {
            continue;
          }
          out.push(`M ${p0.x.toFixed(1)} ${p0.y.toFixed(1)} L ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`);
        }
        this.sysArchDecisionLinkDs = out;
      },
      updateSysArchKbMacroLink() {
        if (this.activeRightLayer !== "overview") {
          this.sysArchKbMacroPathD = "";
          return;
        }
        const canvas = this.$refs.sysArchCanvas;
        const kb = document.getElementById("sys-arch-anchor-kb-top");
        const macro = document.getElementById("sys-arch-anchor-macro-bottom");
        if (!canvas || !kb || !macro) {
          this.sysArchKbMacroPathD = "";
          return;
        }
        const cr = canvas.getBoundingClientRect();
        if (!(cr.width > 4 && cr.height > 4)) {
          this.sysArchKbMacroPathD = "";
          return;
        }
        const kbR = kb.getBoundingClientRect();
        const macroR = macro.getBoundingClientRect();
        const x0 = kbR.left + kbR.width / 2 - cr.left;
        const y0 = kbR.top + kbR.height / 2 - cr.top;
        const x1 = macroR.left + macroR.width / 2 - cr.left;
        const y1 = macroR.bottom - cr.top;
        const dx = x1 - x0;
        const dy = y1 - y0;
        if (!(Math.abs(dx) + Math.abs(dy) > 8)) {
          this.sysArchKbMacroPathD = "";
          return;
        }
        const cx1 = x0 + dx * 0.12;
        const cy1 = y0 + dy * 0.38;
        const cx2 = x1 - dx * 0.12;
        const cy2 = y1 - dy * 0.38;
        this.sysArchKbMacroPathD = `M ${x0.toFixed(1)} ${y0.toFixed(1)} C ${cx1.toFixed(1)} ${cy1.toFixed(1)} ${cx2.toFixed(1)} ${cy2.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
      },
      updateSysArchBtFeedbackLink() {
        if (this.activeRightLayer !== "overview") {
          this.sysArchBtFeedbackPathD = "";
          return;
        }
        const canvas = this.$refs.sysArchCanvas;
        const bt = document.getElementById("sys-arch-anchor-bt-bottom");
        const fb = document.getElementById("sys-arch-anchor-feedback-top");
        if (!canvas || !bt || !fb) {
          this.sysArchBtFeedbackPathD = "";
          return;
        }
        const cr = canvas.getBoundingClientRect();
        if (!(cr.width > 4 && cr.height > 4)) {
          this.sysArchBtFeedbackPathD = "";
          return;
        }
        const btR = bt.getBoundingClientRect();
        const fbR = fb.getBoundingClientRect();
        const x0 = btR.left + btR.width / 2 - cr.left;
        const y0 = btR.bottom - cr.top;
        const x1 = fbR.left + fbR.width / 2 - cr.left;
        const y1 = fbR.top - cr.top + 1;
        const dx = x1 - x0;
        const dy = y1 - y0;
        if (!(Math.abs(dx) + Math.abs(dy) > 8)) {
          this.sysArchBtFeedbackPathD = "";
          return;
        }
        if (dy < 16) {
          this.sysArchBtFeedbackPathD = "";
          return;
        }
        let cx1 = x0 + dx * 0.12;
        let cy1 = y0 + dy * 0.38;
        let cx2 = x1 - dx * 0.12;
        let cy2 = y1 - dy * 0.38;
        if (Math.abs(dx) < 20) {
          const sway = Math.min(72, Math.max(36, cr.width * 0.055));
          cx1 = x0 + sway;
          cx2 = x1 - sway;
          cy1 = y0 + dy * 0.36;
          cy2 = y1 - dy * 0.36;
        }
        this.sysArchBtFeedbackPathD = `M ${x0.toFixed(1)} ${y0.toFixed(1)} C ${cx1.toFixed(1)} ${cy1.toFixed(1)} ${cx2.toFixed(1)} ${cy2.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
      },
      attachSysArchKbMacroLinkObserver() {
        if (this.sysArchKbMacroResizeObserver) {
          return;
        }
        const canvas = this.$refs.sysArchCanvas;
        if (!canvas) {
          return;
        }
        if (!this._sysArchKbMacroWinResize) {
          this._sysArchKbMacroWinResize = () => this.scheduleSysArchKbMacroLinkUpdate();
        }
        window.addEventListener("resize", this._sysArchKbMacroWinResize);
        if (typeof ResizeObserver !== "undefined") {
          const ro = new ResizeObserver(() => this.scheduleSysArchKbMacroLinkUpdate());
          ro.observe(canvas);
          this.sysArchKbMacroResizeObserver = ro;
        }
        const stack = this.$refs.sysArchStack;
        if (stack && !this._sysArchStackScrollHandler) {
          this._sysArchStackEl = stack;
          this._sysArchStackScrollHandler = () => this.scheduleSysArchKbMacroLinkUpdate();
          stack.addEventListener("scroll", this._sysArchStackScrollHandler, { passive: true });
        }
      },
      detachSysArchKbMacroLinkObserver() {
        if (this.sysArchKbMacroResizeObserver) {
          this.sysArchKbMacroResizeObserver.disconnect();
          this.sysArchKbMacroResizeObserver = null;
        }
        if (this._sysArchStackEl && this._sysArchStackScrollHandler) {
          this._sysArchStackEl.removeEventListener("scroll", this._sysArchStackScrollHandler);
          this._sysArchStackEl = null;
          this._sysArchStackScrollHandler = null;
        }
        if (this._sysArchKbMacroWinResize) {
          window.removeEventListener("resize", this._sysArchKbMacroWinResize);
          this._sysArchKbMacroWinResize = null;
        }
        if (this._sysArchKbMacroLinkRaf != null) {
          cancelAnimationFrame(this._sysArchKbMacroLinkRaf);
          this._sysArchKbMacroLinkRaf = null;
        }
        this.sysArchDecisionLinkDs = [];
        this.sysArchBtFeedbackPathD = "";
      },
      detachForceCapHeightObserver() {
        if (this.forceCapResizeObserver) {
          this.forceCapResizeObserver.disconnect();
          this.forceCapResizeObserver = null;
        }
        const layout = this.$refs.forceLayoutV2;
        if (layout) {
          layout.style.removeProperty("--force-cap-block-height");
        }
      },
      attachForceCapHeightObserver() {
        if (this.forceCapResizeObserver) {
          this.forceCapResizeObserver.disconnect();
          this.forceCapResizeObserver = null;
        }
        const cap = this.$refs.forceCapBlock;
        this.syncForceCapBlockPairHeight();
        if (!cap || typeof ResizeObserver === "undefined") {
          return;
        }
        const ro = new ResizeObserver(() => {
          this.syncForceCapBlockPairHeight();
        });
        ro.observe(cap);
        this.forceCapResizeObserver = ro;
      },
      _pushDeerflowHistory(intentObj, riskObj, summaryText, threadId) {
        const stamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
        if (intentObj && Object.keys(intentObj).length) {
          const dims = intentObj.intent_dimensions || {};
          this.deerflowHistory.intent.unshift({
            time: stamp,
            threadId: threadId || "",
            strategy: intentObj.primary_strategy_cn || intentObj.primary_strategy || "未知",
            confidence: Number(intentObj.confidence || 0),
            evidence: Array.isArray(intentObj.evidence) ? intentObj.evidence.slice(0, 2) : [],
            dimSummary: [
              dims?.attack?.description_cn,
              dims?.defense?.description_cn,
              dims?.detect?.description_cn,
              dims?.support?.description_cn,
            ].filter(Boolean).slice(0, 2),
            summary: summaryText || "",
          });
          this.deerflowHistory.intent = this.deerflowHistory.intent.slice(0, 2);
        }
        if (riskObj && Object.keys(riskObj).length) {
          const top = Array.isArray(riskObj.risks) ? riskObj.risks[0] || {} : {};
          this.deerflowHistory.risk.unshift({
            time: stamp,
            threadId: threadId || "",
            level: riskObj.overall_risk_level_cn || riskObj.overall_risk_level || "未知",
            topTarget: top.target || "无",
            topThreatType: top.threat_type_cn || top.threat_type || "未知",
            topEta: Number.isFinite(Number(top.time_to_impact_sec)) ? Number(top.time_to_impact_sec) : null,
            summary: summaryText || "",
          });
          this.deerflowHistory.risk = this.deerflowHistory.risk.slice(0, 2);
        }
      },
      toggleDeerflowHistoryExpand(moduleKey, idx) {
        const m = moduleKey === "risk" ? "risk" : "intent";
        const map = this.deerflowHistoryExpand[m] || Object.create(null);
        map[idx] = !Boolean(map[idx]);
        this.deerflowHistoryExpand[m] = { ...map };
      },
      isDeerflowHistoryExpanded(moduleKey, idx) {
        const m = moduleKey === "risk" ? "risk" : "intent";
        return Boolean(this.deerflowHistoryExpand?.[m]?.[idx]);
      },
      async submitAskPanel() {
        const q = String(this.askPanel.question || "").trim();
        if (!q || this.askPanel.loading) return;
        this.askPanel.loading = true;
        this.askPanel.errorText = "";
        const sentAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
        this.askPanel.messages.push({
          role: "user",
          content: q,
          time: sentAt,
        });
        this.$nextTick(() => this.scrollAskDialogToBottom());
        try {
          const resp = await fetch(ASK_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: q, session_id: "dashboard-ask-panel" }),
          });
          if (!resp.ok) {
            throw new Error(`ask api unavailable (${resp.status})`);
          }
          const payload = await resp.json();
          this.askPanel.messages.push({
            role: "assistant",
            content: String(payload?.answer || ""),
            time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
            source: String(payload?.source || ""),
            traces: Array.isArray(payload?.tool_traces) ? payload.tool_traces : [],
          });
          this.$nextTick(() => this.scrollAskDialogToBottom());
          this.askPanel.sentAt = sentAt;
          this.askPanel.question = "";
        } catch (e) {
          this.askPanel.errorText = `提问失败：${e?.message || e}`;
        } finally {
          this.askPanel.loading = false;
        }
      },
      scrollAskDialogToBottom() {
        const el = this.$refs?.askPanelDialog;
        if (!el || typeof el.scrollTo !== "function") return;
        el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      },
      async refreshForceLlmAdvice() {
        if (this.forceLlmAdviceLoading) return;
        this.forceLlmAdviceLoading = true;
        this.forceLlmAdvice = {
          ...this.forceLlmAdvice,
          errorText: "",
        };
        try {
          const resp = await fetch(FORCE_LLM_ADVICE_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ timeout_sec: 90 }),
          });
          if (!resp.ok) {
            throw new Error(`force llm api (${resp.status})`);
          }
          const payload = await resp.json();
          const sections = Array.isArray(payload?.sections) ? payload.sections : [];
          this.forceLlmAdvice = {
            ok: Boolean(payload?.ok),
            sections,
            errorText: payload?.ok ? "" : String(payload?.error || "生成失败"),
            answerRaw: String(payload?.answer_raw || ""),
            traces: Array.isArray(payload?.traces) ? payload.traces : [],
            loadedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
            source: String(payload?.source || ""),
          };
        } catch (e) {
          this.forceLlmAdvice = {
            ...this.forceLlmAdvice,
            ok: false,
            sections: [],
            errorText: `调用失败：${e?.message || e}`,
            loadedAt: "",
            source: "",
          };
        } finally {
          this.forceLlmAdviceLoading = false;
        }
      },

      async refreshStrikeTargetAdvice() {
        if (this.strikeTargetAdviceLoading) return;
        this.strikeTargetAdviceLoading = true;
        this.strikeTargetAdvice = {
          ...this.strikeTargetAdvice,
          errorText: "",
        };
        try {
          const targets = (this.criticalTargetItems || []).slice(0, 20).map((t) => ({
            name: t.name,
            location: t.location,
            category: t.category,
            priorityScore: t.priorityScore,
            valueScore: t.valueScore,
            threatScore: t.threatScore,
            reason: t.reason,
            count: t.count,
            action: t.action,
            stale: Boolean(t.stale),
          }));
          const resp = await fetch(STRIKE_TARGETS_ADVICE_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ timeout_sec: 95, targets }),
          });
          if (!resp.ok) {
            throw new Error(`strike advice api (${resp.status})`);
          }
          const payload = await resp.json();
          this.strikeTargetAdvice = {
            ok: Boolean(payload?.ok),
            advice: String(payload?.advice || ""),
            errorText: payload?.ok ? "" : String(payload?.error || "生成失败"),
            loadedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
            source: String(payload?.source || ""),
          };
        } catch (e) {
          this.strikeTargetAdvice = {
            ok: false,
            advice: "",
            errorText: `调用失败：${e?.message || e}`,
            loadedAt: "",
            source: "",
          };
        } finally {
          this.strikeTargetAdviceLoading = false;
        }
      },

      async refreshKillChainLlmPlans({ force = false } = {}) {
        if (this.killChainLlmPlansLoading) return;
        const chains = [];
        for (const group of this.killChainDimensionGroups) {
          for (const chain of group.chains || []) {
            chains.push({
              key: chain.key,
              dimension: group.key,
              title: chain.title,
              targetLocation: chain.targetLocation,
              status: chain.status,
              confidence: chain.confidence,
              confidenceLevel: chain.confidenceLevel,
              summary: chain.summary,
            });
          }
        }
        const chainSignature = this.killChainLlmChainSignature;
        if (!chains.length) {
          this.killChainLlmPlans = {
            ok: false,
            plans: [],
            errorText: "当前视野内没有可用于规划的敌方杀伤链对象。",
            answerRaw: "",
            traces: [],
            loadedAt: "",
            source: "",
            chainSignature,
          };
          return;
        }
        if (!force && this.killChainLlmPlans?.ok && this.killChainLlmPlans.chainSignature === chainSignature) {
          return;
        }
        this.killChainLlmPlansLoading = true;
        this.killChainLlmPlans = {
          ...this.killChainLlmPlans,
          errorText: "",
          chainSignature,
        };
        try {
          const resp = await fetch(KILLCHAIN_PLANS_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ timeout_sec: 120, chains }),
          });
          if (!resp.ok) {
            throw new Error(`killchain plans api (${resp.status})`);
          }
          const payload = await resp.json();
          const plans = Array.isArray(payload?.plans) ? payload.plans : [];
          this.killChainLlmPlans = {
            ok: Boolean(payload?.ok),
            plans,
            errorText: payload?.ok ? "" : String(payload?.error || "生成失败"),
            answerRaw: String(payload?.answer_raw || ""),
            traces: Array.isArray(payload?.traces) ? payload.traces : [],
            loadedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
            source: String(payload?.source || ""),
            chainSignature,
          };
        } catch (e) {
          this.killChainLlmPlans = {
            ...this.killChainLlmPlans,
            ok: false,
            plans: [],
            errorText: `调用失败：${e?.message || e}`,
            loadedAt: "",
            source: "",
            chainSignature,
          };
        } finally {
          this.killChainLlmPlansLoading = false;
        }
      },

      async refreshDeerflowRisk() {
        if (this.deerflowRiskLoading) return;
        this.deerflowRiskLoading = true;
        try {
          const resp = await fetch(DEERFLOW_RISK_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ replay: true, timeout_sec: 500 }),
          });
          if (!resp.ok) {
            throw new Error(`risk api unavailable (${resp.status})`);
          }
          const payload = await resp.json();
          const intentObj = payload?.intent && typeof payload.intent === "object" ? payload.intent : {};
          const riskObj = payload?.risk && typeof payload.risk === "object" ? payload.risk : {};
          const stagesObj =
            payload?.stages && typeof payload.stages === "object" ? payload.stages : {};
          this.deerflowRisk = {
            ok: Boolean(payload?.ok),
            thread_id: String(payload?.thread_id || ""),
            mode: String(payload?.mode || "replay"),
            summary: String(payload?.summary || ""),
            intent: intentObj,
            risk: riskObj,
            response: payload?.response && typeof payload.response === "object" ? payload.response : {},
            stages: stagesObj,
            traces: Array.isArray(payload?.traces) ? payload.traces : [],
            pipeline_stdout: String(payload?.pipeline_stdout || ""),
            errorText: payload?.ok ? "" : String(payload?.summary || "风险分析未返回有效结果"),
          };
          this._pushDeerflowHistory(
            intentObj,
            riskObj,
            String(payload?.summary || ""),
            String(payload?.thread_id || ""),
          );
        } catch (e) {
          this.deerflowRisk = {
            ...this.deerflowRisk,
            errorText: `风险识别失败：${e?.message || e}`,
          };
        } finally {
          this.deerflowRiskLoading = false;
        }
      },

      renderMarkdown(text) {
        if (!text || typeof window.marked === "undefined") {
          return String(text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }
        try {
          return window.marked.parse(String(text), { breaks: true, gfm: true });
        } catch {
          return String(text);
        }
      },
      panDashboard(dx, dy) {
        const offsetX = Number(dx) || 0;
        const offsetY = Number(dy) || 0;
        if (!offsetX && !offsetY) return;
        const panels = Array.from(this.$el?.querySelectorAll(".panel-freeform") || []);
        if (panels.length) {
          for (const panel of panels) {
            const left = parseFloat(panel.style.left) || panel.getBoundingClientRect().left;
            const top = parseFloat(panel.style.top) || panel.getBoundingClientRect().top;
            panel.style.left = `${Math.round(left + offsetX)}px`;
            panel.style.top = `${Math.round(top + offsetY)}px`;
          }
          this.schedulePersistPanelLayout();
          return;
        }
        window.scrollBy({ left: offsetX, top: offsetY, behavior: "smooth" });
      },
      startViewportGrab(event) {
        if (event?.button !== 0) return;
        this.stopViewportGrab();
        this.viewportGrabActive = true;
        const startX = event.clientX;
        const startY = event.clientY;
        let lastX = startX;
        let lastY = startY;
        const onMove = (moveEvent) => {
          const dx = moveEvent.clientX - lastX;
          const dy = moveEvent.clientY - lastY;
          lastX = moveEvent.clientX;
          lastY = moveEvent.clientY;
          this.panDashboard(dx, dy);
        };
        const onUp = () => {
          this.stopViewportGrab();
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        this.viewportGrabCleanup = () => {
          window.removeEventListener("mousemove", onMove);
          window.removeEventListener("mouseup", onUp);
          this.viewportGrabCleanup = null;
        };
      },
      stopViewportGrab() {
        if (typeof this.viewportGrabCleanup === "function") {
          this.viewportGrabCleanup();
        }
        this.viewportGrabActive = false;
      },

      trackNewCommands(nextSequence) {
        const nextOrders = new Set((nextSequence || []).map((c) => c.order));
        const added = new Set();
        for (const order of nextOrders) {
          if (!this.prevCommandOrders.has(order)) added.add(order);
        }
        if (added.size > 0) {
          if (this.newCommandTimer) window.clearTimeout(this.newCommandTimer);
          this.newCommandOrders = added;
          this.newCommandTimer = window.setTimeout(() => {
            this.newCommandOrders = new Set();
          }, 1200);
        }
        this.prevCommandOrders = nextOrders;
      },

      replyShouldCollapse(item) {
        if (!item) {
          return false;
        }
        const text = this.replyTextForItem(item);
        if (!text) {
          return false;
        }
        return String(text).split("\n").length > PLANNER_REPLY_COLLAPSE_LINES;
      },
      isReplyExpanded(item) {
        return Boolean(item?.key && this.plannerExpandedReplies[item.key]);
      },
      toggleReplyExpand(item) {
        if (!item?.key) {
          return;
        }
        const current = this.isReplyExpanded(item);
        this.plannerExpandedReplies[item.key] = !current;
      },
      togglePlannerTaskHistory() {
        this.plannerTaskShowAllHistory = !this.plannerTaskShowAllHistory;
      },
      replyDisplayText(item) {
        const full = this.replyTextForItem(item);
        if (!this.replyShouldCollapse(item) || this.isReplyExpanded(item)) {
          return full;
        }
        const lines = String(full).split("\n");
        return `${lines.slice(0, PLANNER_REPLY_COLLAPSE_LINES).join("\n")}\n...（内容已折叠）`;
      },
      hasExactMapPoint(point) {
        return Number.isFinite(Number(point?.xPercent)) && Number.isFinite(Number(point?.yPercent));
      },
      resourceClusterFallback(label, point) {
        const x = Number(point?.xPercent);
        const y = Number(point?.yPercent);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          return [];
        }
        const offsets = [
          [-2.8, -1.2], [-1.8, -0.4], [-0.8, 0.2], [0.2, 0.7],
          [1.2, 1.0], [2.2, 1.2], [3.0, 0.4], [1.8, -0.8],
        ];
        return offsets.map(([dx, dy], index) => ({
          label: String(label || "矿区"),
          key: `${label || "resource"}-cluster-${index}`,
          xPercent: Math.max(0, Math.min(100, x + dx)),
          yPercent: Math.max(0, Math.min(100, y + dy)),
        }));
      },
      clampMinimapMapping() {
        this.minimapOverlayOffsetX = Math.max(-20, Math.min(20, Number(this.minimapOverlayOffsetX) || 0));
        this.minimapOverlayOffsetY = Math.max(-20, Math.min(20, Number(this.minimapOverlayOffsetY) || 0));
        this.minimapOverlayScale = Math.max(0.7, Math.min(1.4, Number(this.minimapOverlayScale) || 1));
        this.minimapBaseImageScaleX = Math.max(0.6, Math.min(1.8, Number(this.minimapBaseImageScaleX) || 1));
        this.minimapBaseImageScaleY = Math.max(0.6, Math.min(1.8, Number(this.minimapBaseImageScaleY) || 1));
      },
      adjustMinimapMapping(axis, delta) {
        const value = Number(delta) || 0;
        if (axis === "x") {
          this.minimapOverlayOffsetX = (Number(this.minimapOverlayOffsetX) || 0) + value;
        } else if (axis === "y") {
          this.minimapOverlayOffsetY = (Number(this.minimapOverlayOffsetY) || 0) + value;
        } else if (axis === "scale") {
          this.minimapOverlayScale = (Number(this.minimapOverlayScale) || 1) + value;
        }
        this.clampMinimapMapping();
      },
      adjustMinimapBaseScale(axis, delta) {
        const value = Number(delta) || 0;
        if (axis === "x") {
          this.minimapBaseImageScaleX = (Number(this.minimapBaseImageScaleX) || 1) + value;
        } else if (axis === "y") {
          this.minimapBaseImageScaleY = (Number(this.minimapBaseImageScaleY) || 1) + value;
        }
        this.clampMinimapMapping();
      },
      resetMinimapMapping() {
        this.minimapOverlayScale = MINIMAP_MAPPING_DEFAULTS.overlayScale;
        this.minimapOverlayOffsetX = MINIMAP_MAPPING_DEFAULTS.offsetX;
        this.minimapOverlayOffsetY = MINIMAP_MAPPING_DEFAULTS.offsetY;
        this.minimapBaseImageScaleX = MINIMAP_MAPPING_DEFAULTS.baseScaleX;
        this.minimapBaseImageScaleY = MINIMAP_MAPPING_DEFAULTS.baseScaleY;
      },
      // 能力类型 key → 单元 role 的映射
      capabilityToRole(capKey) {
        const map = { strike: "strike", detect: "scout", defense: "defend", support: "support" };
        return map[capKey] || capKey;
      },
      // 按住能力按键：激活过滤高亮
      setMinimapHighlight(capKey) {
        this.minimapHighlightRole = this.capabilityToRole(capKey);
      },
      // 松开按键：清除过滤，恢复全部单元
      clearMinimapHighlight() {
        this.minimapHighlightRole = null;
      },
      // 风险维度：点击切换选中/取消
      toggleRiskHighlight(key) {
        this.riskHighlightGroupKey = this.riskHighlightGroupKey === key ? null : key;
      },
      markerStyle(marker) {
        const stackOffset = Number(marker?.stackIndex ?? 0) * 10;
        const imageAspect = Math.max(0.01, Number(this.minimapBaseAspect) || 1);
        const frameAspect = 1; // minimap-frame 固定为 1:1
        let xPadPct = 0;
        let yPadPct = 0;
        let usableWPct = 100;
        let usableHPct = 100;
        if (imageAspect > frameAspect) {
          usableHPct = (frameAspect / imageAspect) * 100;
          yPadPct = (100 - usableHPct) / 2;
        } else if (imageAspect < frameAspect) {
          usableWPct = (imageAspect / frameAspect) * 100;
          xPadPct = (100 - usableWPct) / 2;
        }
        const xRaw = Number(marker?.xPercent ?? 0);
        const yRaw = Number(marker?.yPercent ?? 0);
        const xMapped = xPadPct + (Math.max(0, Math.min(100, xRaw)) / 100) * usableWPct;
        const yMapped = yPadPct + (Math.max(0, Math.min(100, yRaw)) / 100) * usableHPct;
        return {
          left: `calc(${xMapped}% - 10px + ${stackOffset}px)`,
          top: `calc(${yMapped}% - 10px - ${stackOffset}px)`,
        };
      },
      mapPercentStyle(point, stackIndex = 0, spread = 7) {
        const mapped = this.minimapMappedPointPercent(point);
        const offset = Number(stackIndex || 0) * spread;
        return {
          left: `calc(${mapped.x}% + ${offset}px)`,
          top: `calc(${mapped.y}% - ${offset}px)`,
        };
      },
      minimapMappedPointPercent(point) {
        const imageAspect = Math.max(0.01, Number(this.minimapBaseAspect) || 1);
        const frameAspect = 1;
        let xPadPct = 0;
        let yPadPct = 0;
        let usableWPct = 100;
        let usableHPct = 100;
        if (imageAspect > frameAspect) {
          usableHPct = (frameAspect / imageAspect) * 100;
          yPadPct = (100 - usableHPct) / 2;
        } else if (imageAspect < frameAspect) {
          usableWPct = (imageAspect / frameAspect) * 100;
          xPadPct = (100 - usableWPct) / 2;
        }
        const xRaw = Number(point?.xPercent ?? 0);
        const yRaw = Number(point?.yPercent ?? 0);
        const scale = Math.max(0.7, Math.min(1.4, Number(this.minimapOverlayScale) || 1));
        const offsetX = Math.max(-20, Math.min(20, Number(this.minimapOverlayOffsetX) || 0));
        const offsetY = Math.max(-20, Math.min(20, Number(this.minimapOverlayOffsetY) || 0));
        const xAdjusted = 50 + (Math.max(0, Math.min(100, xRaw)) - 50) * scale + offsetX;
        const yAdjusted = 50 + (Math.max(0, Math.min(100, yRaw)) - 50) * scale + offsetY;
        return {
          x: xPadPct + (Math.max(0, Math.min(100, xAdjusted)) / 100) * usableWPct,
          y: yPadPct + (Math.max(0, Math.min(100, yAdjusted)) / 100) * usableHPct,
        };
      },
      resourceMarkerStyle(resource) {
        return this.mapPercentStyle(resource, 0, 0);
      },
      unitMarkerStyle(marker) {
        return this.mapPercentStyle(marker, marker?.stackIndex || 0, 7);
      },
      killChainTaskFallbackPoint(task) {
        const node = (Array.isArray(task?.nodes) ? task.nodes : []).find((item) =>
          Number.isFinite(Number(item?.pointX)) && Number.isFinite(Number(item?.pointY))
        );
        if (!node) return null;
        return {
          xPercent: Number(node.pointX),
          yPercent: Number(node.pointY),
          label: String(task?.targetLocation || node.location || "--"),
        };
      },
      resolveKillChainStrikeFlowPoint(step) {
        const target = String(step?.targetLocation || "").trim();
        if (target) {
          const matched = (this.minimapResourceMarkers || [])
            .filter((point) => String(point?.label || "").trim() === target);
          if (matched.length) {
            const sum = matched.reduce((acc, point) => {
              acc.x += Number(point.xPercent) || 0;
              acc.y += Number(point.yPercent) || 0;
              return acc;
            }, { x: 0, y: 0 });
            return {
              label: target,
              xPercent: sum.x / matched.length,
              yPercent: sum.y / matched.length,
            };
          }
        }
        const fallback = step?.fallbackPoint;
        if (Number.isFinite(Number(fallback?.xPercent)) && Number.isFinite(Number(fallback?.yPercent))) {
          return fallback;
        }
        return null;
      },
      killChainStrikeFlowNodeStyle(node) {
        const style = this.mapPercentStyle(node, node?.stackIndex || 0, 10);
        return {
          ...style,
          "--flow-color": node?.color || "#ff6b7a",
        };
      },
      resolveCriticalTargetMapPoint(target) {
        const location = String(target?.location || "").trim();
        // 优先用后端提供的精确坐标（与 unit marker 同一坐标系），避免启发式网格导致偏移
        if (Number.isFinite(Number(target?.xPercent)) && Number.isFinite(Number(target?.yPercent))) {
          return {
            label: location || "--",
            xPercent: Number(target.xPercent),
            yPercent: Number(target.yPercent),
          };
        }
        if (location) {
          const matched = (this.minimapResourceMarkers || [])
            .filter((point) => String(point?.label || "").trim() === location);
          if (matched.length) {
            const sum = matched.reduce((acc, point) => {
              acc.x += Number(point.xPercent) || 0;
              acc.y += Number(point.yPercent) || 0;
              return acc;
            }, { x: 0, y: 0 });
            return {
              label: location,
              xPercent: sum.x / matched.length,
              yPercent: sum.y / matched.length,
            };
          }
        }
        // 最后退回启发式网格落点（用于没有坐标、也无法匹配矿区标签时）
        if (Number.isFinite(Number(target?.pointX)) && Number.isFinite(Number(target?.pointY))) {
          return {
            label: location || "--",
            xPercent: Number(target.pointX),
            yPercent: Number(target.pointY),
          };
        }
        return null;
      },
      criticalTargetAimStyle(target) {
        return {
          ...this.mapPercentStyle(target, 0, 0),
          /* 我方选中的待打击目标：统一用绿色瞄准，避免与敌方红系混淆 */
          "--critical-color": "#4ade80",
        };
      },
      rangeCircleStyle(circle) {
        const imageAspect = Math.max(0.01, Number(this.minimapBaseAspect) || 1);
        const frameAspect = 1;
        let xPadPct = 0, yPadPct = 0, usableWPct = 100, usableHPct = 100;
        if (imageAspect > frameAspect) {
          usableHPct = (frameAspect / imageAspect) * 100;
          yPadPct = (100 - usableHPct) / 2;
        } else if (imageAspect < frameAspect) {
          usableWPct = (imageAspect / frameAspect) * 100;
          xPadPct = (100 - usableWPct) / 2;
        }
        const scale = Math.max(0.7, Math.min(1.4, Number(this.minimapOverlayScale) || 1));
        const offsetX = Math.max(-20, Math.min(20, Number(this.minimapOverlayOffsetX) || 0));
        const offsetY = Math.max(-20, Math.min(20, Number(this.minimapOverlayOffsetY) || 0));
        const xRaw = Number(circle?.xPercent ?? 0);
        const yRaw = Number(circle?.yPercent ?? 0);
        const xAdjusted = 50 + (Math.max(0, Math.min(100, xRaw)) - 50) * scale + offsetX;
        const yAdjusted = 50 + (Math.max(0, Math.min(100, yRaw)) - 50) * scale + offsetY;
        const xMapped = xPadPct + (Math.max(0, Math.min(100, xAdjusted)) / 100) * usableWPct;
        const yMapped = yPadPct + (Math.max(0, Math.min(100, yAdjusted)) / 100) * usableHPct;
        const stackOffset = Number(circle?.stackIndex || 0) * 7;
        // 直径随叠加缩放比例变化，保持与地图坐标系一致
        const diameter = circle.rangePct * 2 * scale;
        return {
          left: `calc(${xMapped}% + ${stackOffset}px)`,
          top: `calc(${yMapped}% - ${stackOffset}px)`,
          width: `${diameter}%`,
          height: `${diameter}%`,
          transform: "translate(-50%, -50%)",
        };
      },
      unitMarkerRole(row) {
        const text = normalizeSc2Token(`${row?.name || ""} ${row?.action || ""} ${row?.capability || ""}`);
        const isBase =
          row?.kind === "structure" &&
          /孵化场|虫穴|蜂巢|指挥中心|轨道指挥部|行星要塞|星灵枢纽|hatchery|lair|hive|commandcenter|orbital|planetary|nexus/.test(
            String(row?.name || "").toLowerCase(),
          );
        if (isBase) return "base";
        if (row?.capability === "detect" || /overlord|overseer|observer|raven|oracle|scout|侦察|探测|监察|王虫|观察者|渡鸦|先知/.test(text)) return "scout";
        if (row?.capability === "defense" || row?.kind === "structure") return "defend";
        if (row?.capability === "support" || /support|worker|drone|probe|scv|queen|medivac|工蜂|探机|女王|医疗|保障|包装|采集|生产/.test(text)) return "support";
        if (row?.side === "enemy") return "strike";
        if (row?.capability === "strike") return "strike";
        return "unit";
      },
      unitMarkerTitle(marker) {
        const side = marker.side === "enemy" ? "敌方" : "我方";
        const roleMap = { base: "基地", scout: "侦察", defend: "防御", strike: "打击", support: "保障", unit: "单位" };
        return `${side}${roleMap[marker.role] || "单位"} | ${marker.name}×${marker.count} | ${marker.locationLabel}`;
      },
      onMinimapBaseImageLoad(event) {
        const img = event?.target;
        const w = Number(img?.naturalWidth || 0);
        const h = Number(img?.naturalHeight || 0);
        if (w > 0 && h > 0) {
          this.minimapBaseAspect = w / h;
        }
      },
      updateEnemyMainTracker() {
        const game = this.state?.game || {};
        const visibleCount = Number(game.enemyUnitsVisible || 0);
        const visibleRows = Array.isArray(game.enemyVisibleComposition) ? game.enemyVisibleComposition : [];
        const detailRows = Array.isArray(game.enemyVisibleDetails) ? game.enemyVisibleDetails : [];
        const combatVisible = visibleRows.reduce((sum, row) => sum + (Number(row?.count) || 0), 0);
        const detailCombatVisible = detailRows
          .filter((row) => String(row?.kind || "unit") !== "structure")
          .reduce((sum, row) => sum + (Number(row?.count) || 0), 0);
        const totalVisible = Math.max(visibleCount, combatVisible, detailCombatVisible);
        if (totalVisible > 0) {
          this.enemyMainTracker = {
            lastSeenAtMs: Date.now(),
            lastSeenGameTime: game.timeFormatted || this.state?.latestFeatureFrame?.time_formatted || "",
            lastSeenCount: totalVisible,
          };
        }
      },
      markerTitle(marker) {
        const parts = [
          marker?.category ? CATEGORY_LABELS[marker.category] || marker.category : "标记点",
          marker?.locationLabel || "--",
          marker?.summary || "",
        ].filter(Boolean);
        return parts.join(" | ");
      },
      focusKillChain(dimensionKey, chainKey) {
        if (dimensionKey) {
          this.activeKillChainDimension = dimensionKey;
        }
        if (chainKey) {
          this.selectedKillChainKey = chainKey;
        }
      },
      focusKillPlan(planKey) {
        if (planKey) {
          this.selectedKillPlanKey = planKey;
        }
      },
      focusKillPlanByChain(chainKey) {
        if (!chainKey) return;
        const targetPlan = this.killChainPlans.find((plan) => (plan.chainKeys || []).includes(chainKey));
        if (targetPlan) {
          this.selectedKillPlanKey = targetPlan.key;
        }
      },
      isChainInSelectedPlan(chainKey) {
        if (!chainKey || !this.killChainSelectedPlan) return false;
        return (this.killChainSelectedPlan.chainKeys || []).includes(chainKey);
      },
      buildCriticalTargetItems() {
        const ownRows = this.forceDeploymentItems.filter((row) => row.side === "own");
        return this.forceDeploymentItems
          .filter((row) => row.side === "enemy")
          .map((row, index) => {
            const category = CRITICAL_TARGET_CATEGORY_META[row.capability] ? row.capability : "strike";
            const categoryMeta = CRITICAL_TARGET_CATEGORY_META[category];
            const count = Number(row.count) || 0;
            const capabilityValue = { strike: 30, detect: 28, defense: 24, support: 18 }[category] || 20;
            const structureBonus = row.kind === "structure" ? 14 : 0;
            const actionText = `${row.action || ""}`;
            const pressureBonus = /进攻|前压|压制|打击|骚扰|attack|harass/i.test(actionText) ? 18 : 0;
            const valueScore = Math.min(100, Math.round(capabilityValue + structureBonus + count * 7));
            const threatScore = Math.min(100, Math.round(22 + count * 8 + pressureBonus + (category === "strike" ? 18 : 0) + (category === "detect" ? 10 : 0)));
            const priorityScore = Math.round(valueScore * 0.52 + threatScore * 0.48);
            const impacted = ownRows
              .filter((own) => own.location === row.location || own.capability === "support" || row.capability === "strike")
              .sort((a, b) => b.count - a.count)
              .slice(0, 3)
              .map((own) => `${own.name}×${own.count}`);
            const point = criticalTargetPoint(row.location, index);
            const reasonParts = [
              valueScore >= 70 ? "目标价值高" : "具备体系功能",
              threatScore >= 70 ? "对蓝方威胁强" : "需持续跟踪",
              category === "detect" ? "可能承担侦察预警链路" : "",
              category === "strike" ? "可能处在敌方杀伤链打击环节" : "",
              category === "defense" ? "可能掩护敌方关键区域" : "",
            ].filter(Boolean);
            return {
              key: `${row.name}-${row.location}-${index}`,
              name: row.name,
              count,
              location: row.location || "--",
              action: row.action || "当前可见",
              category,
              categoryLabel: categoryMeta.label,
              categoryShortLabel: categoryMeta.shortLabel,
              color: categoryMeta.color,
              valueScore,
              threatScore,
              priorityScore,
              impactedBlueUnits: impacted.length ? impacted.join("、") : "暂无直接关联蓝方单位",
              chainRole: categoryMeta.label,
              reason: reasonParts.join("；"),
              // 后端若提供精确小地图坐标（0-100），优先用于沙盘联动
              xPercent: Number.isFinite(Number(row.xPercent)) ? Number(row.xPercent) : null,
              yPercent: Number.isFinite(Number(row.yPercent)) ? Number(row.yPercent) : null,
              pointX: point.x,
              pointY: point.y,
            };
          })
          .sort((a, b) => b.priorityScore - a.priorityScore || b.threatScore - a.threatScore)
          .slice(0, 12);
      },
      refreshCriticalTargetCache(items = this.liveCriticalTargetItems) {
        if (!Array.isArray(items) || !items.length) {
          return;
        }
        this.cachedCriticalTargetItems = items.map((item) => ({ ...item }));
        const stateTime = new Date(this.state?.updatedAt || "").getTime();
        this.cachedCriticalTargetSeenAtMs = Number.isFinite(stateTime) ? stateTime : Date.now();
        this.cachedCriticalTargetSeenGameTime = this.displayGameTime;
        if (!this.selectedCriticalTargetKey || !items.some((item) => item.key === this.selectedCriticalTargetKey)) {
          this.selectedCriticalTargetKey = items[0].key;
        }
      },
      forceCapabilityUnitText(side, capabilityKey) {
        const sideKey = side === "enemy" ? "enemy" : "own";
        const bucket = this.forceCapabilityBreakdown?.[sideKey]?.[capabilityKey];
        const units = bucket?.units || [];
        return units.length ? units.join("、") : "暂无可见单位";
      },
      selectCriticalTarget(target) {
        if (!target?.key) {
          return;
        }
        this.selectedCriticalTargetKey = target.key;
        this.criticalAimArmed = true;
        // 点击清单时，基于“当前”小地图 marker 列表挑一个最匹配的敌方 marker，锁定其 key
        const markers = this.minimapUnitMarkers || [];
        const candidates = markers.filter((m) => m && m.side === "enemy");
        const tName = normalizeSc2Token(String(target.name || ""));
        const tLoc = String(target.location || "").trim().toLowerCase();
        const scoreOf = (m) => {
          const mName = normalizeSc2Token(String(m.name || ""));
          const mLoc = String(m.locationLabel || "").trim().toLowerCase();
          let score = 0;
          if (tLoc && mLoc) {
            if (mLoc === tLoc) score += 100;
            else if (mLoc.includes(tLoc) || tLoc.includes(mLoc)) score += 55;
          }
          if (tName && mName) {
            if (mName === tName) score += 70;
            else if (mName.includes(tName) || tName.includes(mName)) score += 35;
          }
          // 若清单条目带精确坐标，则用距离做 tie-break（越近分越高）
          if (Number.isFinite(Number(target.xPercent)) && Number.isFinite(Number(target.yPercent))) {
            const dx = Number(m.xPercent) - Number(target.xPercent);
            const dy = Number(m.yPercent) - Number(target.yPercent);
            const dist = Math.sqrt(dx * dx + dy * dy);
            score += Math.max(0, 40 - dist * 2);
          }
          return score;
        };
        let best = null;
        let bestScore = -1;
        for (const m of candidates) {
          const s = scoreOf(m);
          if (s > bestScore) {
            bestScore = s;
            best = m;
          }
        }
        // 设置阈值，避免误瞄：低于阈值就不锁定，保持不显示
        this.criticalAimMarkerKey = best && bestScore >= 60 ? String(best.key || "") : "";
      },
      isCriticalAimMarker(marker) {
        if (this.activeRightLayer !== "critical") return false;
        if (!this.criticalAimArmed) return false;
        if (!this.criticalAimMarkerKey) return false;
        return Boolean(marker && String(marker.key || "") === this.criticalAimMarkerKey);
      },
      toggleCriticalCategoryMenu() {
        this.criticalCategoryMenuOpen = !this.criticalCategoryMenuOpen;
      },
      selectCriticalCategory(categoryKey) {
        const key = CRITICAL_TARGET_CATEGORY_META[categoryKey] ? categoryKey : "all";
        this.activeCriticalTargetCategory = key;
        this.criticalCategoryMenuOpen = false;
      },
      resetCriticalCategory() {
        this.activeCriticalTargetCategory = "all";
      },
      triggerFeatureFlash() {
        this.featureRefreshFlash = true;
        if (this.featureFlashTimer) {
          window.clearTimeout(this.featureFlashTimer);
        }
        this.featureFlashTimer = window.setTimeout(() => {
          this.featureRefreshFlash = false;
          this.featureFlashTimer = null;
        }, 900);
      },
      toggleFeature(key) {
        this.activeFeatureKey = key;
      },
      barPct(own, enemy) {
        const o = Number(own) || 0;
        const e = Number(enemy) || 0;
        const total = o + e;
        if (!total) return 50;
        return Math.max(4, Math.min(96, Math.round((o / total) * 100)));
      },
      selectFeatureManual(key) {
        this.activeFeatureKey = key;
        this.stopFeatureAutoPlay();
        if (this.featureAutoPlay) {
          this.startFeatureAutoPlay();
        }
      },
      startFeatureAutoPlay() {
        if (this.featureAutoPlayTimer) return;
        this.featureAutoPlayTimer = window.setInterval(() => {
          if (!this.featureAutoPlay || !this.featureCards.length) return;
          const idx = this.featureCards.findIndex((f) => f.name === this.activeFeatureKey);
          const nextIdx = (idx + 1) % this.featureCards.length;
          this.activeFeatureKey = this.featureCards[nextIdx]?.name || this.activeFeatureKey;
        }, 3000);
      },
      stopFeatureAutoPlay() {
        if (this.featureAutoPlayTimer) {
          window.clearInterval(this.featureAutoPlayTimer);
          this.featureAutoPlayTimer = null;
        }
      },
      toggleFeatureAutoPlay() {
        this.featureAutoPlay = !this.featureAutoPlay;
        if (this.featureAutoPlay) {
          this.startFeatureAutoPlay();
        } else {
          this.stopFeatureAutoPlay();
        }
      },
      isPlannerNearBottom(container) {
        if (!container) {
          return true;
        }
        return container.scrollTop + container.clientHeight >= container.scrollHeight - 48;
      },
      handlePlannerScroll() {
        const container = this.$refs.plannerConversation;
        this.plannerAutoFollow = this.isPlannerNearBottom(container);
      },
      scrollPlannerToBottom(force = false) {
        this.$nextTick(() => {
          const container = this.$refs.plannerConversation;
          if (!container) {
            return;
          }
          if (!force && !this.plannerAutoFollow && !this.isPlannerNearBottom(container)) {
            return;
          }
          container.scrollTop = container.scrollHeight;
          this.plannerAutoFollow = true;
        });
      },
      buildPlannerMessageKey(message) {
        const channel = message?.channel || "";
        const title = message?.title || "";
        const prompt = String(message?.inputSummary || "").trim();
        return [channel, title, prompt].join("::");
      },
      shouldDisplayPlannerMessage(message) {
        if (!message) {
          return false;
        }

        const updatedAt = new Date(message?.updatedAt || "").getTime();

        if (!Number.isFinite(updatedAt)) {
          return false;
        }

        if (updatedAt > this.plannerSessionStartedAt) {
          return true;
        }

        return this.isOpeningPlannerMessage(message) && this.isRecentPlannerMessage(message);
      },
      isOpeningPlannerMessage(message) {
        const title = String(message?.title || "").trim();
        const inputSummary = String(message?.inputSummary || "").trim();
        return /^Opening\b/i.test(title) || inputSummary.startsWith("初始化阶段");
      },
      isRecentPlannerMessage(message, maxAgeMs = 5 * 60 * 1000) {
        const updatedAt = new Date(message?.updatedAt || "").getTime();
        const stateUpdatedAt = new Date(this.state?.updatedAt || "").getTime();
        if (!Number.isFinite(updatedAt) || !Number.isFinite(stateUpdatedAt)) {
          return false;
        }
        const ageMs = stateUpdatedAt - updatedAt;
        return ageMs >= 0 && ageMs <= maxAgeMs;
      },
      buildPlannerTurnInstanceKey(message) {
        return `${this.buildPlannerMessageKey(message)}::${message?.updatedAt || ""}`;
      },
      buildPlannerEntry(message, options = {}) {
        const { typing = false } = options;
        const content = message?.content || "";
        const prompt = message?.inputSummary || buildPlannerFallback(this.state, message?.channel || this.activeChannel);
        return {
          key: this.buildPlannerTurnInstanceKey(message),
          signature: this.buildPlannerMessageKey(message),
          channel: message?.channel || this.activeChannel || "latest",
          title: getMessageDisplayTitle(message),
          prompt,
          reply: content || this.emptyMessage,
          displayReply: typing ? "" : content || this.emptyMessage,
          promptTime: message?.timeFormatted || this.displayGameTime,
          updatedAt: message?.updatedAt || "",
          isTyping: typing,
        };
      },
      isPlannerReplyRelated(previousReply, nextReply) {
        const previous = String(previousReply || "");
        const next = String(nextReply || "");
        if (!previous || !next) {
          return true;
        }
        return next.startsWith(previous) || previous.startsWith(next);
      },
      isPlannerTurnRecent(previousUpdatedAt, nextUpdatedAt, maxGapMs = 45 * 1000) {
        const previousTime = new Date(previousUpdatedAt || "").getTime();
        const nextTime = new Date(nextUpdatedAt || "").getTime();
        if (!Number.isFinite(previousTime) || !Number.isFinite(nextTime)) {
          return false;
        }
        return Math.abs(nextTime - previousTime) <= maxGapMs;
      },
      shouldAnimatePlannerTurn(message) {
        if (this.activeAgentView !== "planner") {
          return false;
        }
        if (this.activeChannel === "latest") {
          return true;
        }
        return (message?.channel || "latest") === this.activeChannel;
      },
      stopPlannerTyping(options = {}) {
        const { complete = false } = options;
        if (this.plannerTypingTimer) {
          window.clearTimeout(this.plannerTypingTimer);
          this.plannerTypingTimer = null;
        }
        if (complete && this.plannerLiveTurn) {
          this.plannerLiveTurn.displayReply = this.plannerLiveTurn.reply || this.emptyMessage;
          this.plannerLiveTurn.isTyping = false;
        }
        this.plannerTypingKey = "";
        this.plannerTypingIndex = 0;
      },
      startPlannerTyping() {
        const live = this.plannerLiveTurn;
        if (!live || !live.isTyping) {
          return;
        }
        if (this.activeAgentView !== "planner") {
          return;
        }
        if (this.activeChannel !== "latest" && live.channel !== this.activeChannel) {
          return;
        }

        this.stopPlannerTyping();
        this.plannerTypingKey = live.key;
        this.plannerTypingIndex = Array.from(live.displayReply || "").length;
        const characters = Array.from(live.reply || "");
        const stepDelay = characters.length > 900 ? 8 : characters.length > 480 ? 12 : 18;

        const step = () => {
          const activeLive = this.plannerLiveTurn;
          if (!activeLive || activeLive.key !== this.plannerTypingKey) {
            this.stopPlannerTyping();
            return;
          }
          if (this.activeAgentView !== "planner") {
            this.plannerTypingTimer = null;
            return;
          }
          if (this.activeChannel !== "latest" && activeLive.channel !== this.activeChannel) {
            this.plannerTypingTimer = null;
            return;
          }
          if (this.plannerTypingIndex >= characters.length) {
            activeLive.displayReply = activeLive.reply || this.emptyMessage;
            activeLive.isTyping = false;
            this.stopPlannerTyping();
            return;
          }

          this.plannerTypingIndex += 1;
          activeLive.displayReply = characters.slice(0, this.plannerTypingIndex).join("");
          this.scrollPlannerToBottom();
          this.plannerTypingTimer = window.setTimeout(step, stepDelay);
        };

        this.plannerTypingTimer = window.setTimeout(step, 90);
      },
      resumePlannerTyping() {
        if (this.plannerTypingTimer) {
          return;
        }
        if (!this.plannerLiveTurn || !this.plannerLiveTurn.isTyping) {
          return;
        }
        this.startPlannerTyping();
      },
      pushPlannerHistory(entry) {
        if (!entry) {
          return;
        }
        const finalizedEntry = {
          ...entry,
          displayReply: entry.reply || this.emptyMessage,
          isTyping: false,
        };
        const existingIndex = this.plannerHistory.findIndex((item) => item.key === finalizedEntry.key);
        if (existingIndex >= 0) {
          this.plannerHistory.splice(existingIndex, 1, finalizedEntry);
        } else {
          this.plannerHistory.push(finalizedEntry);
          if (this.plannerHistory.length > 80) {
            this.plannerHistory.splice(0, this.plannerHistory.length - 80);
          }
        }
        this.plannerSeenTurnKeys[finalizedEntry.key] = true;
      },
      finalizePlannerLiveTurn() {
        if (!this.plannerLiveTurn) {
          return;
        }
        this.stopPlannerTyping({ complete: true });
        this.pushPlannerHistory(this.plannerLiveTurn);
        this.plannerLiveTurn = null;
      },
      syncPlannerLiveTurn(message) {
        if (!this.plannerLiveTurn) {
          return false;
        }

        const nextEntry = this.buildPlannerEntry(message, { typing: false });
        const activeLive = this.plannerLiveTurn;
        if (activeLive.key === nextEntry.key) {
          const currentLength = Array.from(activeLive.displayReply || "").length;
          const nextCharacters = Array.from(nextEntry.reply || "");
          this.plannerLiveTurn = {
            ...activeLive,
            ...nextEntry,
            displayReply: nextCharacters.slice(0, currentLength).join(""),
            isTyping: currentLength < nextCharacters.length,
          };
          this.resumePlannerTyping();
          return true;
        }

        if (
          activeLive.signature === nextEntry.signature &&
          this.isPlannerReplyRelated(activeLive.reply, nextEntry.reply) &&
          this.isPlannerTurnRecent(activeLive.updatedAt, nextEntry.updatedAt)
        ) {
          const currentLength = Array.from(activeLive.displayReply || "").length;
          const nextCharacters = Array.from(nextEntry.reply || "");
          this.plannerLiveTurn = {
            ...activeLive,
            ...nextEntry,
            displayReply: nextCharacters.slice(0, currentLength).join(""),
            isTyping: currentLength < nextCharacters.length,
          };
          this.plannerSeenTurnKeys[nextEntry.key] = true;
          this.resumePlannerTyping();
          return true;
        }

        return false;
      },
      beginPlannerLiveTurn(message, options = {}) {
        const { animate = false } = options;
        const nextEntry = this.buildPlannerEntry(message, { typing: animate });
        this.plannerLiveTurn = nextEntry;
        this.plannerSeenTurnKeys[nextEntry.key] = true;
        if (animate) {
          this.startPlannerTyping();
        }
        this.scrollPlannerToBottom();
      },
      ingestPlannerMessage(message, options = {}) {
        const { force = false } = options;
        if (!this.plannerHistoryReady && !force) {
          return;
        }
        if (!this.shouldDisplayPlannerMessage(message)) {
          return;
        }

        const turnKey = this.buildPlannerTurnInstanceKey(message);
        if (!turnKey) {
          return;
        }

        if (this.syncPlannerLiveTurn(message)) {
          return;
        }
        if (this.plannerSeenTurnKeys[turnKey]) {
          return;
        }
        if (this.plannerLiveTurn) {
          this.finalizePlannerLiveTurn();
        }
        this.beginPlannerLiveTurn(message, {
          animate: this.shouldAnimatePlannerTurn(message),
        });
      },
      buildCommandSignature(command) {
        return [
          command?.unit || "",
          command?.action || "",
          command?.target || "",
          command?.category || "",
        ].join("::");
      },
      updateCommandExecutionHistory(previousCommands, nextCommands) {
        if (!this.commandTrackingReady) {
          this.commandTrackingReady = true;
          return;
        }

        const remainingCounts = new Map();
        for (const command of nextCommands || []) {
          const signature = this.buildCommandSignature(command);
          remainingCounts.set(signature, (remainingCounts.get(signature) || 0) + 1);
        }

        const executedNow = [];
        for (const command of previousCommands || []) {
          const signature = this.buildCommandSignature(command);
          const count = remainingCounts.get(signature) || 0;
          if (count > 0) {
            remainingCounts.set(signature, count - 1);
            continue;
          }
          executedNow.push({
            ...command,
            executedAt: new Date().toISOString(),
          });
        }

        if (!executedNow.length) {
          return;
        }

        this.executedCommands = [...this.executedCommands, ...executedNow].slice(-60);
      },
      rememberPlannerTurnsFromState() {
        const seenKeys = new Set();
        const visibleMessages = [];
        const allMessages = [
          ...Object.values(this.state?.messages || {}),
          this.state?.lastMessage,
        ]
          .filter(Boolean)
          .sort((left, right) => {
            const leftTime = new Date(left?.updatedAt || 0).getTime();
            const rightTime = new Date(right?.updatedAt || 0).getTime();
            return leftTime - rightTime;
          });

        for (const message of allMessages) {
          if (!this.shouldDisplayPlannerMessage(message)) {
            continue;
          }
          const messageKey = this.buildPlannerMessageKey(message);
          if (!messageKey || seenKeys.has(messageKey)) {
            continue;
          }
          seenKeys.add(messageKey);
          if (!this.plannerHistoryReady) {
            visibleMessages.push(message);
            continue;
          }
          visibleMessages.push(message);
        }

        if (!this.plannerHistoryReady) {
          this.stopPlannerTyping({ complete: false });
          this.plannerHistory = [];
          this.plannerLiveTurn = null;
          this.plannerSeenTurnKeys = Object.create(null);

          const historicalMessages = visibleMessages.slice(0, -1);
          for (const message of historicalMessages) {
            this.pushPlannerHistory(this.buildPlannerEntry(message, { typing: false }));
          }

          const latestMessage = visibleMessages[visibleMessages.length - 1];
          if (latestMessage) {
            this.beginPlannerLiveTurn(latestMessage, {
              animate: this.shouldAnimatePlannerTurn(latestMessage),
            });
          }
        } else {
          for (const message of visibleMessages) {
            this.ingestPlannerMessage(message);
          }
        }
        this.plannerHistoryReady = true;
      },
      replyTextForItem(item) {
        if (!item) {
          return this.emptyMessage;
        }
        return item.displayReply || item.reply || this.emptyMessage;
      },
      replyIsTypingForItem(item) {
        return Boolean(item?.isTyping);
      },
      resolveAsset(path) {
        if (!path) {
          return "";
        }
        const cleanPath = String(path).replace(/\\/g, "/").replace(/^\/+/, "");
        const url = new URL(`../${cleanPath}`, window.location.href);
        url.searchParams.set("t", this.state?.updatedAt || String(Date.now()));
        return url.toString();
      },
      /** 冷启动仅看底图时用固定缓存键，避免每帧用 Date.now() 反复请求 */
      resolveMinimapImage(path) {
        if (!path) {
          return "";
        }
        const cleanPath = String(path).replace(/\\/g, "/").replace(/^\/+/, "");
        const url = new URL(`../${cleanPath}`, window.location.href);
        const tag = this.liveUiArmed ? this.state?.updatedAt || String(Date.now()) : "baseline";
        url.searchParams.set("t", tag);
        return url.toString();
      },
      async fetchState() {
        if (this.isFetchingState) {
          return;
        }
        this.isFetchingState = true;
        try {
          const previousFeatureStamp = this.lastFeatureStamp;
          const previousCommands = this.state?.commandSequence ?? [];
          const url = new URL(STATE_URL);
          url.searchParams.set("t", Date.now());

          const response = await fetch(url, {
            cache: "no-store",
          });

          if (!response.ok) {
            throw new Error(`state file unavailable (${response.status})`);
          }

          const nextState = await response.json();
          const fp = fingerprintDashboardState(nextState);
          if (!this.liveUiArmed) {
            if (this.stateBootstrapFingerprint === null) {
              this.stateBootstrapFingerprint = fp;
            } else if (fp !== this.stateBootstrapFingerprint) {
              this.liveUiArmed = true;
            }
          }

          const isRunning = nextState?.status === "running";
          if (!this.liveUiArmed || !isRunning) {
            this.plannerHistoryReady = false;
            this.plannerHistory = [];
            this.plannerLiveTurn = null;
            this.plannerSeenTurnKeys = Object.create(null);
            this.executedCommands = [];
            this.commandTrackingReady = false;
          }
          this.state = prepareDisplayState(nextState, this.liveUiArmed);
          this.updateEnemyMainTracker();
          this.updateCommandExecutionHistory(previousCommands, this.state?.commandSequence ?? []);
          this.trackNewCommands(this.state?.commandSequence ?? []);
          this.rememberPlannerTurnsFromState();
          this.fetchFailureCount = 0;
          this.fetchError = "";
          this.isLoading = false;

          const nextFeatures = this.state?.latestFeatureFrame?.features ?? [];
          const nextFeatureStamp =
            this.state?.latestFeatureFrame?.updatedAt ||
            String(this.state?.latestFeatureFrame?.game_loop || "");
          if (nextFeatureStamp && nextFeatureStamp !== previousFeatureStamp) {
            this.lastFeatureStamp = nextFeatureStamp;
            this.triggerFeatureFlash();
          }

          if (!nextFeatures.length) {
            this.activeFeatureKey = "";
          } else if (!nextFeatures.some((feature) => feature.name === this.activeFeatureKey)) {
            this.activeFeatureKey = nextFeatures[0].name || "";
          }

          this.activeChannel = "latest";
        } catch (error) {
          this.fetchFailureCount += 1;
          if (!this.state?.updatedAt || this.fetchFailureCount >= 3) {
            this.fetchError = "无法加载 dashboard_state.json";
          }
          this.isLoading = false;
          console.warn("dashboard_state poll failed", error);
        } finally {
          this.isFetchingState = false;
        }
      },
      startPolling() {
        this.fetchState();
        this.pollTimer = window.setInterval(() => {
          this.fetchState();
        }, this.refreshMs);
      },
      stopPolling() {
        if (this.pollTimer) {
          window.clearInterval(this.pollTimer);
          this.pollTimer = null;
        }
        if (this.featureFlashTimer) {
          window.clearTimeout(this.featureFlashTimer);
          this.featureFlashTimer = null;
        }
        this.stopPlannerTyping();
        this.stopFeatureAutoPlay();
      },
      teardownPanelFreeform() {
        if (!this.panelInteractionCleanups?.length) {
          return;
        }
        this.panelInteractionCleanups.forEach((fn) => {
          if (typeof fn === "function") {
            fn();
          }
        });
        this.panelInteractionCleanups = [];
      },

      persistPanelLayout() {
        const selectors = [
          ".panel-left",
          ".panel-force-deployment",
          ".panel-critical-targets",
          ".panel-killchain",
          ".panel-agent",
        ];
        const layout = {};
        for (const selector of selectors) {
          const panel = this.$el?.querySelector(selector);
          if (!panel || !panel.classList.contains("panel-freeform")) {
            continue;
          }
          const key = selector.replace(/^\./, "");
          layout[key] = {
            left: Math.round(parseFloat(panel.style.left) || 0),
            top: Math.round(parseFloat(panel.style.top) || 0),
            width: Math.round(panel.offsetWidth),
            height: Math.round(panel.offsetHeight),
            zIndex: Math.round(parseInt(panel.style.zIndex, 10) || 1000),
          };
        }
        if (Object.keys(layout).length) {
          writePanelLayout(layout);
        }
      },

      schedulePersistPanelLayout() {
        if (this.panelLayoutSaveTimer) {
          window.clearTimeout(this.panelLayoutSaveTimer);
        }
        this.panelLayoutSaveTimer = window.setTimeout(() => {
          this.panelLayoutSaveTimer = null;
          this.persistPanelLayout();
        }, 200);
      },

      setupPanelFreeform() {
        this.teardownPanelFreeform();
        const selectors = [
          ".panel-left",
          ".panel-force-deployment",
          ".panel-critical-targets",
          ".panel-killchain",
          ".panel-agent",
        ];
        const panels = selectors
          .map((selector) => this.$el?.querySelector(selector))
          .filter(Boolean);

        const savedLayout = readPanelLayout() || {};
        let maxZ = 1000;
        const VIEWPORT_PADDING = 10;
        const clampWithinViewport = (panel, left, top, width, height, minW, minH) => {
          const viewportW = Math.max(320, window.innerWidth || 0);
          const viewportH = Math.max(320, window.innerHeight || 0);
          const safeWidth = Math.min(
            Math.max(minW, Math.round(width || minW)),
            Math.max(minW, viewportW - VIEWPORT_PADDING * 2)
          );
          const safeHeight = Math.min(
            Math.max(minH, Math.round(height || minH)),
            Math.max(minH, viewportH - VIEWPORT_PADDING * 2)
          );
          const maxLeft = Math.max(VIEWPORT_PADDING, viewportW - safeWidth - VIEWPORT_PADDING);
          const maxTop = Math.max(VIEWPORT_PADDING, viewportH - safeHeight - VIEWPORT_PADDING);
          const safeLeft = Math.min(Math.max(Math.round(left || VIEWPORT_PADDING), VIEWPORT_PADDING), maxLeft);
          const safeTop = Math.min(Math.max(Math.round(top || VIEWPORT_PADDING), VIEWPORT_PADDING), maxTop);
          panel.style.left = `${safeLeft}px`;
          panel.style.top = `${safeTop}px`;
          panel.style.width = `${safeWidth}px`;
          panel.style.height = `${safeHeight}px`;
        };

        panels.forEach((panel, index) => {
          const rect = panel.getBoundingClientRect();
          const key = selectors[index].replace(/^\./, "");
          const saved = savedLayout[key];
          const panelMinSize = { minW: 320, minH: 220 };

          panel.classList.add("panel-freeform");
          if (
            saved &&
            Number.isFinite(saved.left) &&
            Number.isFinite(saved.top) &&
            Number.isFinite(saved.width) &&
            Number.isFinite(saved.height)
          ) {
            clampWithinViewport(
              panel,
              saved.left,
              saved.top,
              saved.width,
              saved.height,
              panelMinSize.minW,
              panelMinSize.minH
            );
            const z = Math.round(saved.zIndex) || 1000;
            panel.style.zIndex = String(z);
            maxZ = Math.max(maxZ, z);
          } else {
            clampWithinViewport(
              panel,
              rect.left,
              rect.top,
              rect.width,
              rect.height,
              panelMinSize.minW,
              panelMinSize.minH
            );
            panel.style.zIndex = String(++this.panelZCounter);
            maxZ = Math.max(maxZ, this.panelZCounter);
          }

          const bringToFront = () => {
            panel.style.zIndex = String(++this.panelZCounter);
            maxZ = Math.max(maxZ, this.panelZCounter);
            this.schedulePersistPanelLayout();
          };
          panel.addEventListener("mousedown", bringToFront);

          const header = panel.querySelector(".panel-head");
          let dragMove = null;
          let dragUp = null;
          const onDragStart = (event) => {
            if (event.button !== 0) return;
            const tag = (event.target?.tagName || "").toUpperCase();
            if (["BUTTON", "INPUT", "TEXTAREA", "SELECT"].includes(tag)) {
              return;
            }
            event.preventDefault();
            bringToFront();
            const startX = event.clientX;
            const startY = event.clientY;
            const startLeft = parseFloat(panel.style.left) || 0;
            const startTop = parseFloat(panel.style.top) || 0;
            dragMove = (moveEvent) => {
              const dx = moveEvent.clientX - startX;
              const dy = moveEvent.clientY - startY;
              const width = panel.offsetWidth || panelMinSize.minW;
              const height = panel.offsetHeight || panelMinSize.minH;
              clampWithinViewport(
                panel,
                startLeft + dx,
                startTop + dy,
                width,
                height,
                panelMinSize.minW,
                panelMinSize.minH
              );
            };
            dragUp = () => {
              window.removeEventListener("mousemove", dragMove);
              window.removeEventListener("mouseup", dragUp);
              dragMove = null;
              dragUp = null;
              this.persistPanelLayout();
            };
            window.addEventListener("mousemove", dragMove);
            window.addEventListener("mouseup", dragUp);
          };
          if (header) {
            header.addEventListener("mousedown", onDragStart);
          }

          const resizeHandle = document.createElement("button");
          resizeHandle.type = "button";
          resizeHandle.className = "panel-free-resize";
          resizeHandle.title = "拖拽调整容器大小";
          panel.appendChild(resizeHandle);

          let resizeMove = null;
          let resizeUp = null;
          const onResizeStart = (event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            event.stopPropagation();
            bringToFront();
            const startX = event.clientX;
            const startY = event.clientY;
            const startW = panel.offsetWidth;
            const startH = panel.offsetHeight;
            const minW = panelMinSize.minW;
            const minH = panelMinSize.minH;
            resizeMove = (moveEvent) => {
              const dx = moveEvent.clientX - startX;
              const dy = moveEvent.clientY - startY;
              const nextW = Math.max(minW, Math.round(startW + dx));
              const nextH = Math.max(minH, Math.round(startH + dy));
              const left = parseFloat(panel.style.left) || 0;
              const top = parseFloat(panel.style.top) || 0;
              clampWithinViewport(
                panel,
                left,
                top,
                nextW,
                nextH,
                minW,
                minH
              );
            };
            resizeUp = () => {
              window.removeEventListener("mousemove", resizeMove);
              window.removeEventListener("mouseup", resizeUp);
              resizeMove = null;
              resizeUp = null;
              this.persistPanelLayout();
            };
            window.addEventListener("mousemove", resizeMove);
            window.addEventListener("mouseup", resizeUp);
          };
          resizeHandle.addEventListener("mousedown", onResizeStart);

          this.panelInteractionCleanups.push(() => {
            panel.removeEventListener("mousedown", bringToFront);
            if (header) header.removeEventListener("mousedown", onDragStart);
            resizeHandle.removeEventListener("mousedown", onResizeStart);
            resizeHandle.remove();
            panel.classList.remove("panel-freeform");
            panel.style.left = "";
            panel.style.top = "";
            panel.style.width = "";
            panel.style.height = "";
            panel.style.zIndex = "";
            if (dragMove) window.removeEventListener("mousemove", dragMove);
            if (dragUp) window.removeEventListener("mouseup", dragUp);
            if (resizeMove) window.removeEventListener("mousemove", resizeMove);
            if (resizeUp) window.removeEventListener("mouseup", resizeUp);
          });
        });

        this.panelZCounter = Math.max(this.panelZCounter, maxZ);
      },
    },
    mounted() {
      this.startPolling();
      this.startFeatureAutoPlay();
      this.$nextTick(() => {
        if (this.activeRightLayer === "force") {
          this.attachForceCapHeightObserver();
        }
        if (this.activeRightLayer === "overview") {
          this.attachSysArchKbMacroLinkObserver();
          this.scheduleSysArchKbMacroLinkUpdate();
        }
      });
    },
    beforeUnmount() {
      this.detachSysArchKbMacroLinkObserver();
      this.detachForceCapHeightObserver();
      if (this.panelLayoutSaveTimer) {
        window.clearTimeout(this.panelLayoutSaveTimer);
        this.panelLayoutSaveTimer = null;
      }
      this.stopPolling();
      this.stopFeatureAutoPlay();
    },
  }).mount("#app");
}
