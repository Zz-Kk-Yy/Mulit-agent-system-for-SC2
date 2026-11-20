const STATE_URLS = {
  red: new URL("../dashboard_state_red.json", window.location.href),
  blue: new URL("../dashboard_state_blue.json", window.location.href),
};
/** 与 launch_support_stack.py 默认 --ask-port（SWARMBRAIN_ASK_PORT，默认 8011）一致 */
const ASK_API_BASE = "http://127.0.0.1:8011";
const DEERFLOW_RISK_API_URL = `${ASK_API_BASE}/api/risk/refresh`;
const ASK_API_URL = `${ASK_API_BASE}/api/ask`;
const FORCE_LLM_ADVICE_API_URL = `${ASK_API_BASE}/api/force/llm-advice`;
const STRIKE_TARGETS_ADVICE_API_URL = `${ASK_API_BASE}/api/advice/strike-targets`;
const KILLCHAIN_PLANS_API_URL = `${ASK_API_BASE}/api/killchain/plans`;
const ORIENT_QA_API_URL = `${ASK_API_BASE}/api/orient/understand`;
const ORIENT_QA_REFRESH_MS = 30000;
const COT_SELFPLAY_API_URL = `${ASK_API_BASE}/api/cot/selfplay`;
const COT_ENV_SELFPLAY_API_URL = `${ASK_API_BASE}/api/cot/env-selfplay`;
const COT_KILLNET_API_URL = `${ASK_API_BASE}/api/cot/killnet`;
const ASK_API_HEALTH_URL = `${ASK_API_BASE}/health`;
/** 各模块大模型意见尽可能每 12s 刷新一次（若上轮仍在生成则跳过） */
const LLM_ADVICE_REFRESH_MS = 12000;
const COT_ROUND_CAP = 10;
const COT_AUTO_GAP_MS = 30000;
/** 对局状态盘文件超过此时长不再更新，视为后端已停，转入快照 */
const COT_STATE_STALE_MS = 12000;

function isAskApiUnreachableError(err) {
  const msg = String(err?.message || err || "").toLowerCase();
  return (
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("network error") ||
    msg.includes("err_connection") ||
    msg.includes("econnrefused") ||
    msg.includes("load failed") ||
    msg.includes("network request failed") ||
    msg.includes("abort")
  );
}

function cmpDash(value, fallback = "暂无数据") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function cmpNum(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function cmpPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function cmpFmtList(items, limit = 3) {
  const list = (Array.isArray(items) ? items : []).map((x) => String(x || "").trim()).filter(Boolean);
  if (!list.length) return "暂无";
  return list.slice(0, limit).join(" · ");
}

function cmpTopLocations(rows, side, limit = 3) {
  const map = Object.create(null);
  for (const row of rows || []) {
    if (!row || (side && row.side !== side)) continue;
    const loc = String(row.location || "").trim().toUpperCase();
    if (!loc || loc === "--") continue;
    map[loc] = (map[loc] || 0) + (cmpNum(row.count) || 1);
  }
  return Object.entries(map)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([loc, count]) => `${loc}×${count}`);
}

function cmpCapabilityClusters(rows, side) {
  const buckets = Object.fromEntries(CAPABILITY_ORDER.map((k) => [k, 0]));
  for (const row of rows || []) {
    if (!row || (side && row.side !== side)) continue;
    const key = CAPABILITY_META[row.capability] ? row.capability : "strike";
    buckets[key] += cmpNum(row.count) || 1;
  }
  return CAPABILITY_ORDER
    .filter((k) => buckets[k] > 0)
    .map((k) => `${CAPABILITY_META[k].label}${buckets[k]}`)
    .join(" · ") || "暂无可见集群";
}

function cmpRowsByCapability(rows, side, capability, limit = 2) {
  return (rows || [])
    .filter((row) => row && (!side || row.side === side) && row.capability === capability)
    .sort((a, b) => cmpNum(b.count) - cmpNum(a.count))
    .slice(0, limit)
    .map((row) => `${row.name}×${row.count}@${row.location || "--"}`);
}

function cmpConcentration(rows, side) {
  const map = Object.create(null);
  let total = 0;
  for (const row of rows || []) {
    if (!row || (side && row.side !== side)) continue;
    const loc = String(row.location || "").trim().toUpperCase() || "--";
    const n = cmpNum(row.count) || 1;
    map[loc] = (map[loc] || 0) + n;
    total += n;
  }
  if (!total) return { score: 0, hotspot: "--", total: 0 };
  const [hotspot, count] = Object.entries(map).sort((a, b) => b[1] - a[1])[0] || ["--", 0];
  return { score: count / total, hotspot, total, count };
}

function cmpControlPrefixes(rows, side) {
  const own = (rows || []).filter((row) => row && (!side || row.side === side));
  const a = own.filter((r) => String(r.location || "").toUpperCase().startsWith("A")).reduce((s, r) => s + (cmpNum(r.count) || 1), 0);
  const b = own.filter((r) => String(r.location || "").toUpperCase().startsWith("B")).reduce((s, r) => s + (cmpNum(r.count) || 1), 0);
  if (!a && !b) return "控制区未形成明确分区";
  if (a >= b * 1.25) return `偏 A 区（A:${a} / B:${b}）`;
  if (b >= a * 1.25) return `偏 B 区（A:${a} / B:${b}）`;
  return `A/B 交织（A:${a} / B:${b}）`;
}

function cmpContactHotspots(redOwnLocs, blueOwnLocs) {
  const blueSet = new Set(blueOwnLocs.map((x) => String(x).split("×")[0]));
  const hits = redOwnLocs
    .map((x) => String(x).split("×")[0])
    .filter((loc) => blueSet.has(loc));
  return hits;
}

function cmpExtractForceRows(state) {
  const game = state?.game || {};
  const ownFromDetails = normalizeForceRows(game.ownForceDetails || [], "own");
  const ownRows = ownFromDetails.length
    ? ownFromDetails
    : normalizeForceRows(compositionToForceRows(game.ownUnitComposition, "own"), "own");
  const enemyFromDetails = normalizeForceRows(game.enemyVisibleDetails || [], "enemy");
  const enemyRows = enemyFromDetails.length
    ? enemyFromDetails
    : normalizeForceRows(compositionToForceRows(game.enemyVisibleComposition, "enemy"), "enemy");
  return [...ownRows, ...enemyRows];
}

function cmpBuildCriticalFromRows(forceRows) {
  const ownRows = (forceRows || []).filter((row) => row.side === "own");
  return (forceRows || [])
    .filter((row) => row.side === "enemy")
    .map((row, index) => {
      const category = CRITICAL_TARGET_CATEGORY_META[row.capability] ? row.capability : "strike";
      const count = cmpNum(row.count) || 1;
      const capabilityValue = { strike: 30, detect: 28, defense: 24, support: 18 }[category] || 20;
      const structureBonus = row.kind === "structure" ? 14 : 0;
      const pressureBonus = /进攻|前压|压制|打击|骚扰|attack|harass/i.test(String(row.action || "")) ? 18 : 0;
      const valueScore = Math.min(100, Math.round(capabilityValue + structureBonus + count * 7));
      const threatScore = Math.min(
        100,
        Math.round(22 + count * 8 + pressureBonus + (category === "strike" ? 18 : 0) + (category === "detect" ? 10 : 0)),
      );
      const priorityScore = Math.round(valueScore * 0.52 + threatScore * 0.48);
      const guards = ownRows
        .filter((own) => own.location === row.location || own.capability === "defense" || own.capability === "support")
        .filter((own) => own.location === row.location)
        .reduce((s, own) => s + (cmpNum(own.count) || 1), 0);
      return {
        name: row.name,
        count,
        location: row.location || "--",
        category,
        categoryLabel: CRITICAL_TARGET_CATEGORY_META[category]?.label || category,
        priorityScore,
        guardNearby: guards,
      };
    })
    .sort((a, b) => b.priorityScore - a.priorityScore || b.count - a.count);
}

function cmpRecentCommandRoute(state, limit = 4) {
  const seq = Array.isArray(state?.commandSequence) ? state.commandSequence : [];
  const locs = [];
  for (let i = seq.length - 1; i >= 0 && locs.length < limit; i -= 1) {
    const cmd = seq[i] || {};
    const loc = String(cmd.locationLabel || cmd.location || cmd.target || "").trim().toUpperCase();
    if (!loc || loc === "--") continue;
    if (locs[locs.length - 1] === loc) continue;
    locs.push(loc);
  }
  return locs.reverse();
}

function cmpSideSnapshot(state, sideKey) {
  const game = state?.game || {};
  const wb = state?.agentWorkbench || {};
  const forceRows = cmpExtractForceRows(state);
  const ownRows = forceRows.filter((r) => r.side === "own");
  const enemyRows = forceRows.filter((r) => r.side === "enemy");
  const ownUnitCount = ownRows.filter((r) => r.kind !== "structure").reduce((s, r) => s + (cmpNum(r.count) || 1), 0);
  const ownStructCount = ownRows.filter((r) => r.kind === "structure").reduce((s, r) => s + (cmpNum(r.count) || 1), 0);
  const enemyVisibleCount = enemyRows.reduce((s, r) => s + (cmpNum(r.count) || 1), 0);
  const ownLocs = cmpTopLocations(ownRows, null, 4);
  const enemyLocs = cmpTopLocations(enemyRows, null, 3);
  const concentration = cmpConcentration(ownRows, null);
  const vision = state?.latestFeatureFrame?.vision || {};
  const visiblePct = cmpPct(vision.visible_tiles_pct);
  const fogPct = cmpPct(vision.fog_of_war_pct);
  const intents = Array.isArray(wb.intentRisk?.behaviorIntent) ? wb.intentRisk.behaviorIntent : [];
  const risks = Array.isArray(wb.intentRisk?.riskRecognition) ? wb.intentRisk.riskRecognition : [];
  const chains = Array.isArray(wb.killchainDiscovery?.enemyChains) ? wb.killchainDiscovery.enemyChains : [];
  const plans = Array.isArray(wb.combatPlanning?.plans) ? wb.combatPlanning.plans : [];
  const criticals = cmpBuildCriticalFromRows(forceRows);
  const topCritical = criticals[0] || null;
  const topIntent = intents[0] || null;
  const topRisk = risks[0] || null;
  const topChain = [...chains].sort((a, b) => cmpNum(b.confidence) - cmpNum(a.confidence))[0] || null;
  const topPlan = plans[0] || null;
  const latestMsg = state?.messages?.latest || state?.lastMessage || {};
  const collab = game.forceCollaborationSuggestion || {};
  const formation = wb.formationAdvisor || {};
  const route = cmpRecentCommandRoute(state);
  const strikeFront = cmpRowsByCapability(ownRows, null, "strike", 2);
  const defenseFront = cmpRowsByCapability(ownRows, null, "defense", 2);
  const scoutFront = cmpRowsByCapability(ownRows, null, "detect", 2);
  const ready = Boolean(state?.updatedAt) || cmpNum(game.armyCount) > 0 || ownRows.length > 0;

  return {
    sideKey,
    ready,
    status: state?.status || "idle",
    updatedAt: state?.updatedAt || "",
    time: game.timeFormatted || state?.latestFeatureFrame?.time_formatted || "--:--",
    stage: STAGE_LABELS[game.gameStage] || game.gameStage || "--",
    agentName: game.agentName || (sideKey === "blue" ? "蓝方 SwarmBrain" : "红方 SwarmBrain"),
    army: cmpNum(game.armyCount),
    workers: cmpNum(game.workerCount),
    supplyUsed: cmpNum(game.supplyUsed),
    supplyCap: cmpNum(game.supplyCap),
    minerals: cmpNum(game.minerals),
    vespene: cmpNum(game.vespene),
    ownHpTotal: cmpNum(game.ownHpTotal),
    startLocation: String(game.startLocation || ""),
    ownUnitCount,
    ownStructCount,
    enemyVisibleCount,
    enemyUnitsVisible: cmpNum(game.enemyUnitsVisible),
    enemyStructuresVisible: cmpNum(game.enemyStructuresVisible),
    ownLocs,
    enemyLocs,
    clusters: cmpCapabilityClusters(ownRows, null),
    concentration,
    control: cmpControlPrefixes(ownRows, null),
    visiblePct,
    fogPct,
    strikeFront,
    defenseFront,
    scoutFront,
    route,
    intents,
    risks,
    chains,
    plans,
    criticals,
    topCritical,
    topIntent,
    topRisk,
    topChain,
    topPlan,
    latestMsgTitle: String(latestMsg.title || latestMsg.channel || "").trim(),
    latestMsgSummary: String(latestMsg.summary || latestMsg.content || latestMsg.text || "").trim().slice(0, 120),
    strikeTarget: String(collab.strikeTarget || "").trim(),
    protectionTarget: String(collab.protectionTarget || "").trim(),
    strategyCommand: String(collab.currentStrategyCommand || "").trim(),
    formationConclusion: String(formation.situationConclusion || "").trim(),
    awarenessSummary: String(wb.situationAwareness?.summary || "").trim(),
    intentSummary: String(wb.intentRisk?.summary || "").trim(),
  };
}

function cmpNoteByNumber(label, redVal, blueVal, higherWins = true) {
  const r = cmpNum(redVal);
  const b = cmpNum(blueVal);
  if (r === b) return `${label}双方持平（${r}）`;
  const redLeads = higherWins ? r > b : r < b;
  return redLeads ? `${label}红方领先（${r} vs ${b}）` : `${label}蓝方领先（${b} vs ${r}）`;
}

function cmpBoard(title, subtitle, verdict, rows, bullets) {
  return {
    title,
    subtitle,
    verdict: cmpDash(verdict, "等待双侧态势同步后生成结论"),
    rows: (rows || []).map((row) => ({
      dimension: row.dimension,
      red: cmpDash(row.red),
      blue: cmpDash(row.blue),
      note: row.note || "",
    })),
    bullets: (bullets || []).filter(Boolean).slice(0, 6),
    live: true,
  };
}

function buildLiveDuelCompareBoard(layer, redState, blueState) {
  const red = cmpSideSnapshot(redState, "red");
  const blue = cmpSideSnapshot(blueState, "blue");
  const bothReady = red.ready || blue.ready;
  const contact = cmpContactHotspots(red.ownLocs, blue.ownLocs);

  const awareness = () => {
    const verdictParts = [];
    if (bothReady) {
      verdictParts.push(cmpNoteByNumber("作战单元", red.army || red.ownUnitCount, blue.army || blue.ownUnitCount));
      if (red.visiblePct != null || blue.visiblePct != null) {
        verdictParts.push(cmpNoteByNumber("视野覆盖", red.visiblePct || 0, blue.visiblePct || 0));
      }
      if (contact.length) verdictParts.push(`接触热点：${contact.join("、")}`);
    }
    return cmpBoard(
      "态势感知 · 红蓝总体对比",
      "基于双侧 dashboard 实时盘面：实体、资源、分布、控制与侦察",
      verdictParts.join("； ") || "等待红蓝态势写盘",
      [
        {
          dimension: "双方实体（单位/建筑）",
          red: `作战 ${red.army || red.ownUnitCount} · 建筑 ${red.ownStructCount} · 补给 ${red.supplyUsed}/${red.supplyCap}`,
          blue: `作战 ${blue.army || blue.ownUnitCount} · 建筑 ${blue.ownStructCount} · 补给 ${blue.supplyUsed}/${blue.supplyCap}`,
          note: cmpNoteByNumber("实体规模", red.army || red.ownUnitCount, blue.army || blue.ownUnitCount),
        },
        {
          dimension: "建筑 / 资源 / 地形",
          red: `矿 ${red.minerals} / 气 ${red.vespene} · 工蜂 ${red.workers} · 开局 ${red.startLocation || "--"} · 阶段 ${red.stage}`,
          blue: `矿 ${blue.minerals} / 气 ${blue.vespene} · 工蜂 ${blue.workers} · 开局 ${blue.startLocation || "--"} · 阶段 ${blue.stage}`,
          note: cmpNoteByNumber("矿物", red.minerals, blue.minerals),
        },
        {
          dimension: "当前兵力分布",
          red: cmpFmtList(red.ownLocs, 4),
          blue: cmpFmtList(blue.ownLocs, 4),
          note: contact.length ? `重叠接触点：${contact.join("、")}` : "双方主力区位暂未重叠",
        },
        {
          dimension: "交战区域",
          red: contact.length ? `接触点附近：${cmpFmtList(red.ownLocs.filter((x) => contact.includes(String(x).split("×")[0])), 3)}` : "暂无与蓝方同位接触",
          blue: contact.length ? `接触点附近：${cmpFmtList(blue.ownLocs.filter((x) => contact.includes(String(x).split("×")[0])), 3)}` : "暂无与红方同位接触",
          note: contact.length ? `当前交战热点 ${contact.length} 处` : "尚未形成同位交战热点",
        },
        {
          dimension: "控制区域",
          red: red.control,
          blue: blue.control,
          note: "按双方己方单位区位 A/B 前缀统计",
        },
        {
          dimension: "攻击 / 侦察范围",
          red: `视野 ${red.visiblePct != null ? `${red.visiblePct}%` : "--"} · 迷雾 ${red.fogPct != null ? `${red.fogPct}%` : "--"} · 可见敌 ${red.enemyVisibleCount || red.enemyUnitsVisible}`,
          blue: `视野 ${blue.visiblePct != null ? `${blue.visiblePct}%` : "--"} · 迷雾 ${blue.fogPct != null ? `${blue.fogPct}%` : "--"} · 可见敌 ${blue.enemyVisibleCount || blue.enemyUnitsVisible}`,
          note: cmpNoteByNumber("可见敌情", red.enemyVisibleCount || red.enemyUnitsVisible, blue.enemyVisibleCount || blue.enemyUnitsVisible),
        },
        {
          dimension: "行动路线 / 关键目标",
          red: `路线 ${cmpFmtList(red.route, 4)} · 打击目标 ${cmpDash(red.strikeTarget, "未指定")} · 保护 ${cmpDash(red.protectionTarget, "未指定")}`,
          blue: `路线 ${cmpFmtList(blue.route, 4)} · 打击目标 ${cmpDash(blue.strikeTarget, "未指定")} · 保护 ${cmpDash(blue.protectionTarget, "未指定")}`,
          note: "路线来自近期指令序列，目标来自兵力协同建议",
        },
      ],
      [
        `红方 ${red.time} · 蓝方 ${blue.time}`,
        cmpNoteByNumber("补给占用", red.supplyUsed, blue.supplyUsed),
        contact.length ? `接触热点：${contact.join("、")}` : "当前无同位接触热点",
      ],
    );
  };

  const force = () => {
    const verdict = bothReady
      ? `${cmpNoteByNumber("集中度", red.concentration.score, blue.concentration.score)}；红热点 ${red.concentration.hotspot}，蓝热点 ${blue.concentration.hotspot}`
      : "等待兵力明细同步";
    return cmpBoard(
      "兵力布势 · 红蓝总体对比",
      "基于双侧 ownForceDetails 实时统计集群、区位与集中度",
      verdict,
      [
        {
          dimension: "兵力集群划分",
          red: red.clusters,
          blue: blue.clusters,
          note: "按打击/探测/防御/保障能力归类",
        },
        {
          dimension: "主力部队位置",
          red: cmpFmtList(red.strikeFront, 2),
          blue: cmpFmtList(blue.strikeFront, 2),
          note: "取打击类单位前两处",
        },
        {
          dimension: "防守部队位置",
          red: cmpFmtList(red.defenseFront, 2),
          blue: cmpFmtList(blue.defenseFront, 2),
          note: "取防御类单位前两处",
        },
        {
          dimension: "侦察部队位置",
          red: cmpFmtList(red.scoutFront, 2),
          blue: cmpFmtList(blue.scoutFront, 2),
          note: "取探测类单位前两处",
        },
        {
          dimension: "当前阵型",
          red: cmpDash(red.formationConclusion, `集中度 ${(red.concentration.score * 100).toFixed(0)}% @ ${red.concentration.hotspot}`),
          blue: cmpDash(blue.formationConclusion, `集中度 ${(blue.concentration.score * 100).toFixed(0)}% @ ${blue.concentration.hotspot}`),
          note: "优先阵型顾问结论，否则用区位集中度启发式",
        },
        {
          dimension: "兵力调动路线",
          red: cmpFmtList(red.route, 4),
          blue: cmpFmtList(blue.route, 4),
          note: "来自 commandSequence 近期区位变化",
        },
        {
          dimension: "局部兵力集中度",
          red: `${(red.concentration.score * 100).toFixed(0)}% @ ${red.concentration.hotspot}（己方 ${red.concentration.total}）`,
          blue: `${(blue.concentration.score * 100).toFixed(0)}% @ ${blue.concentration.hotspot}（己方 ${blue.concentration.total}）`,
          note: cmpNoteByNumber("最高格集中度", red.concentration.score, blue.concentration.score),
        },
      ],
      [
        `红主力热点 ${red.concentration.hotspot}`,
        `蓝主力热点 ${blue.concentration.hotspot}`,
        cmpNoteByNumber("己方实体", red.concentration.total, blue.concentration.total),
      ],
    );
  };

  const intent = () => {
    const fmtIntent = (s) => {
      if (s.topIntent) {
        const title = s.topIntent.title || s.topIntent.name || s.topIntent.intent || "意图条目";
        const level = s.topIntent.level || s.topIntent.confidence || s.topIntent.score || "";
        const summary = s.topIntent.summary || s.topIntent.evidence || s.topIntent.detail || "";
        return `${title}${level ? ` · ${level}` : ""}${summary ? ` · ${String(summary).slice(0, 60)}` : ""}`;
      }
      if (s.intentSummary) return s.intentSummary.slice(0, 100);
      if (s.latestMsgTitle) return `决策侧写：${s.latestMsgTitle}`;
      return "暂无意图条目";
    };
    const evidence = (s) => {
      const extras = (s.intents || []).slice(0, 3).map((it) => it.title || it.name || it.intent).filter(Boolean);
      return extras.length ? `条目 ${s.intents.length}：${cmpFmtList(extras, 3)}` : "工作台意图列表为空";
    };
    return cmpBoard(
      "意图识别 · 红蓝总体对比",
      "基于双侧 agentWorkbench.intentRisk / 最新决策输出（可观测项，非对抗真值裁判）",
      bothReady
        ? `红意图条目 ${red.intents.length} · 蓝意图条目 ${blue.intents.length}；${cmpNoteByNumber("意图条目数", red.intents.length, blue.intents.length)}`
        : "等待意图工作台同步",
      [
        {
          dimension: "当前主意图判定",
          red: fmtIntent(red),
          blue: fmtIntent(blue),
          note: "取行为意图列表首条或摘要",
        },
        {
          dimension: "意图条目覆盖",
          red: evidence(red),
          blue: evidence(blue),
          note: cmpNoteByNumber("意图条目数", red.intents.length, blue.intents.length),
        },
        {
          dimension: "决策侧写（方案消息）",
          red: cmpDash(red.latestMsgTitle, "暂无最新规划消息"),
          blue: cmpDash(blue.latestMsgTitle, "暂无最新规划消息"),
          note: "来自 messages.latest / lastMessage",
        },
        {
          dimension: "策略指令关联",
          red: cmpDash(red.strategyCommand, "暂无协同策略指令"),
          blue: cmpDash(blue.strategyCommand, "暂无协同策略指令"),
          note: "来自 forceCollaborationSuggestion",
        },
      ],
      [
        `红：${cmpDash(red.topIntent?.title || red.latestMsgTitle, "无主意图")}`,
        `蓝：${cmpDash(blue.topIntent?.title || blue.latestMsgTitle, "无主意图")}`,
        "说明：正确性/提前量需对抗真值，当前展示可观测识别结果",
      ],
    );
  };

  const risk = () => {
    const fmtRisk = (s) => {
      if (s.topRisk) {
        const title = s.topRisk.title || s.topRisk.name || s.topRisk.risk || "风险条目";
        const level = s.topRisk.level || s.topRisk.severity || s.topRisk.score || "";
        const summary = s.topRisk.summary || s.topRisk.response || s.topRisk.detail || "";
        return `${title}${level ? ` · ${level}` : ""}${summary ? ` · ${String(summary).slice(0, 60)}` : ""}`;
      }
      return "暂无风险条目";
    };
    const listRisk = (s) => {
      const titles = (s.risks || []).slice(0, 3).map((r) => r.title || r.name || r.risk).filter(Boolean);
      return titles.length ? `共 ${s.risks.length} 条：${cmpFmtList(titles, 3)}` : "风险列表为空";
    };
    return cmpBoard(
      "风险分析 · 红蓝总体对比",
      "基于双侧 agentWorkbench.intentRisk.riskRecognition 实时条目",
      bothReady
        ? `${cmpNoteByNumber("风险条目数", red.risks.length, blue.risks.length)}；红顶风险「${cmpDash(red.topRisk?.title || red.topRisk?.name, "无")}」，蓝顶风险「${cmpDash(blue.topRisk?.title || blue.topRisk?.name, "无")}」`
        : "等待风险工作台同步",
      [
        {
          dimension: "最高优先级风险",
          red: fmtRisk(red),
          blue: fmtRisk(blue),
          note: "取风险识别列表首条",
        },
        {
          dimension: "风险清单覆盖",
          red: listRisk(red),
          blue: listRisk(blue),
          note: cmpNoteByNumber("风险条目数", red.risks.length, blue.risks.length),
        },
        {
          dimension: "关联保护 / 打击目标",
          red: `保护 ${cmpDash(red.protectionTarget, "未指定")} · 打击 ${cmpDash(red.strikeTarget, "未指定")}`,
          blue: `保护 ${cmpDash(blue.protectionTarget, "未指定")} · 打击 ${cmpDash(blue.strikeTarget, "未指定")}`,
          note: "来自兵力协同建议，反映当前风险应对指向",
        },
      ],
      [
        `红风险 ${red.risks.length} 条`,
        `蓝风险 ${blue.risks.length} 条`,
        "即时性秒级指标暂无埋点，以上为盘面可观测风险结果",
      ],
    );
  };

  const killchain = () => {
    const fmtChain = (s) => {
      if (!s.topChain) return "暂无发现蓝方杀伤链";
      const conf = cmpNum(s.topChain.confidence);
      return `${cmpDash(s.topChain.title, "敌链")} · ${cmpDash(s.topChain.status, "状态未知")} · 置信 ${conf || "--"} · @${cmpDash(s.topChain.targetLocation, "--")}`;
    };
    const listChains = (s) => {
      if (!s.chains.length) return "杀伤链列表为空";
      const titles = s.chains.slice(0, 3).map((c) => c.title || c.key).filter(Boolean);
      return `共 ${s.chains.length} 条：${cmpFmtList(titles, 3)}`;
    };
    return cmpBoard(
      "杀伤链发现 · 红蓝总体对比",
      "基于双侧 agentWorkbench.killchainDiscovery.enemyChains",
      bothReady
        ? `${cmpNoteByNumber("发现链数", red.chains.length, blue.chains.length)}；红最高置信 ${cmpNum(red.topChain?.confidence)} · 蓝最高置信 ${cmpNum(blue.topChain?.confidence)}`
        : "等待杀伤链工作台同步",
      [
        {
          dimension: "当前最高置信敌链",
          red: fmtChain(red),
          blue: fmtChain(blue),
          note: cmpNoteByNumber("最高置信", cmpNum(red.topChain?.confidence), cmpNum(blue.topChain?.confidence)),
        },
        {
          dimension: "杀伤链清单",
          red: listChains(red),
          blue: listChains(blue),
          note: cmpNoteByNumber("发现链数", red.chains.length, blue.chains.length),
        },
        {
          dimension: "链目标区位",
          red: cmpDash(red.topChain?.targetLocation, "无"),
          blue: cmpDash(blue.topChain?.targetLocation, "无"),
          note: "取最高置信链的目标位置",
        },
      ],
      [
        `红链 ${red.chains.length} · 蓝链 ${blue.chains.length}`,
        `红顶链：${cmpDash(red.topChain?.title, "无")}`,
        `蓝顶链：${cmpDash(blue.topChain?.title, "无")}`,
      ],
    );
  };

  const critical = () => {
    const fmtCrit = (s) => {
      if (!s.topCritical) return "暂无可见蓝方关键目标";
      return `${s.topCritical.name}×${s.topCritical.count}@${s.topCritical.location} · ${s.topCritical.categoryLabel} · 优先级 ${s.topCritical.priorityScore}`;
    };
    const listCrit = (s) => {
      if (!s.criticals.length) return "关键目标列表为空";
      return `共 ${s.criticals.length} 个：${cmpFmtList(
        s.criticals.slice(0, 3).map((c) => `${c.name}@${c.location}`),
        3,
      )}`;
    };
    return cmpBoard(
      "关键打击节点 · 红蓝总体对比",
      "基于双侧可见敌情明细实时计算优先级",
      bothReady
        ? `${cmpNoteByNumber("可见关键目标数", red.criticals.length, blue.criticals.length)}；红顶节点「${cmpDash(red.topCritical?.name, "无")}」，蓝顶节点「${cmpDash(blue.topCritical?.name, "无")}」`
        : "等待敌情明细同步",
      [
        {
          dimension: "最高优先级打击节点",
          red: fmtCrit(red),
          blue: fmtCrit(blue),
          note: cmpNoteByNumber("顶节点优先级", red.topCritical?.priorityScore || 0, blue.topCritical?.priorityScore || 0),
        },
        {
          dimension: "关键节点清单",
          red: listCrit(red),
          blue: listCrit(blue),
          note: cmpNoteByNumber("节点数量", red.criticals.length, blue.criticals.length),
        },
        {
          dimension: "节点附近己方护卫强度",
          red: red.topCritical ? `同位护卫约 ${red.topCritical.guardNearby}` : "无节点",
          blue: blue.topCritical ? `同位护卫约 ${blue.topCritical.guardNearby}` : "无节点",
          note: "统计与顶节点同区位的己方防御/保障单位",
        },
      ],
      [
        `红目标 ${red.criticals.length} · 蓝目标 ${blue.criticals.length}`,
        `红顶：${cmpDash(red.topCritical?.name, "无")}`,
        `蓝顶：${cmpDash(blue.topCritical?.name, "无")}`,
      ],
    );
  };

  const plan = () => {
    const fmtPlan = (s) => {
      if (s.topPlan) {
        return `${cmpDash(s.topPlan.title || s.topPlan.name, "方案")} · ${String(s.topPlan.summary || s.topPlan.reason || "").slice(0, 80) || "无摘要"}`;
      }
      if (s.latestMsgTitle) return `${s.latestMsgTitle}${s.latestMsgSummary ? ` · ${s.latestMsgSummary}` : ""}`;
      return "暂无方案输出";
    };
    return cmpBoard(
      "方案生成 · 红蓝总体对比",
      "基于双侧 combatPlanning / 最新规划消息与协同建议",
      bothReady
        ? `红方案「${cmpDash(red.topPlan?.title || red.latestMsgTitle, "无")}」· 蓝方案「${cmpDash(blue.topPlan?.title || blue.latestMsgTitle, "无")}」`
        : "等待方案输出同步",
      [
        {
          dimension: "当前态势快照",
          red: `${red.stage} · 作战 ${red.army || red.ownUnitCount} · 补给 ${red.supplyUsed}/${red.supplyCap} · ${red.time}`,
          blue: `${blue.stage} · 作战 ${blue.army || blue.ownUnitCount} · 补给 ${blue.supplyUsed}/${blue.supplyCap} · ${blue.time}`,
          note: "双侧 gameStage / 兵力 / 补给实时对照",
        },
        {
          dimension: "识别到的对手压力点",
          red: cmpDash(red.topCritical ? `${red.topCritical.name}@${red.topCritical.location}` : red.strikeTarget, "暂无"),
          blue: cmpDash(blue.topCritical ? `${blue.topCritical.name}@${blue.topCritical.location}` : blue.strikeTarget, "暂无"),
          note: "优先关键目标，其次协同打击目标",
        },
        {
          dimension: "当前主要风险",
          red: cmpDash(red.topRisk?.title || red.topRisk?.name, "暂无风险条目"),
          blue: cmpDash(blue.topRisk?.title || blue.topRisk?.name, "暂无风险条目"),
          note: cmpNoteByNumber("风险条目数", red.risks.length, blue.risks.length),
        },
        {
          dimension: "关键目标指向",
          red: `打击 ${cmpDash(red.strikeTarget, "未指定")} · 保护 ${cmpDash(red.protectionTarget, "未指定")}`,
          blue: `打击 ${cmpDash(blue.strikeTarget, "未指定")} · 保护 ${cmpDash(blue.protectionTarget, "未指定")}`,
          note: "来自 forceCollaborationSuggestion",
        },
        {
          dimension: "当前采用方案",
          red: fmtPlan(red),
          blue: fmtPlan(blue),
          note: "优先 combatPlanning.plans[0]，否则 latest 规划消息",
        },
        {
          dimension: "策略指令",
          red: cmpDash(red.strategyCommand, "暂无"),
          blue: cmpDash(blue.strategyCommand, "暂无"),
          note: "currentStrategyCommand 字段",
        },
      ],
      [
        `红：${cmpDash(red.topPlan?.title || red.latestMsgTitle, "无方案")}`,
        `蓝：${cmpDash(blue.topPlan?.title || blue.latestMsgTitle, "无方案")}`,
        cmpNoteByNumber("规划方案数", red.plans.length, blue.plans.length),
      ],
    );
  };

  const overview = () => cmpBoard(
    "总体对比总览",
    "红蓝双侧实时盘面摘要；进入各态势模块查看细项",
    bothReady
      ? `综合：${cmpNoteByNumber("兵力", red.army || red.ownUnitCount, blue.army || blue.ownUnitCount)}；${cmpNoteByNumber("风险条目", red.risks.length, blue.risks.length)}；${cmpNoteByNumber("杀伤链", red.chains.length, blue.chains.length)}`
      : "等待红蓝 dashboard_state 同步",
    [
      {
        dimension: "态势感知",
        red: `作战 ${red.army || red.ownUnitCount} · 矿 ${red.minerals} · 视野 ${red.visiblePct != null ? `${red.visiblePct}%` : "--"}`,
        blue: `作战 ${blue.army || blue.ownUnitCount} · 矿 ${blue.minerals} · 视野 ${blue.visiblePct != null ? `${blue.visiblePct}%` : "--"}`,
        note: "切换「态势感知」查看完整对照",
      },
      {
        dimension: "兵力布势",
        red: `热点 ${red.concentration.hotspot} · 集中 ${(red.concentration.score * 100).toFixed(0)}% · ${red.clusters}`,
        blue: `热点 ${blue.concentration.hotspot} · 集中 ${(blue.concentration.score * 100).toFixed(0)}% · ${blue.clusters}`,
        note: "切换「兵力布势」查看完整对照",
      },
      {
        dimension: "意图 / 风险 / 杀伤链 / 关键节点 / 方案 / 战法评估",
        red: `意图 ${red.intents.length} · 风险 ${red.risks.length} · 链 ${red.chains.length} · 节点 ${red.criticals.length} · 方案 ${cmpDash(red.topPlan?.title || red.latestMsgTitle, "无")}`,
        blue: `意图 ${blue.intents.length} · 风险 ${blue.risks.length} · 链 ${blue.chains.length} · 节点 ${blue.criticals.length} · 方案 ${cmpDash(blue.topPlan?.title || blue.latestMsgTitle, "无")}`,
        note: "请切换右侧各分析模块查看分项对比",
      },
    ],
    [
      `红方 ${red.status} · ${red.time}`,
      `蓝方 ${blue.status} · ${blue.time}`,
      contact.length ? `接触热点：${contact.join("、")}` : "当前无同位接触热点",
    ],
  );

  const tactics = () => {
    const redBoard = buildTacticsEvaluationBoard("red");
    const blueBoard = buildTacticsEvaluationBoard("blue");
    const pick = (board, kind, layer) => {
      const item = board[kind]?.[layer]?.[0];
      if (!item) return "暂无条目";
      return `${item.title} · ${item.focus}`;
    };
    return cmpBoard(
      "战术战法评估 · 红蓝总体对比",
      "对照双方成功与失败的战术决策、战役决策",
      `红成功 ${redBoard.successCount}/失败 ${redBoard.failureCount}；蓝成功 ${blueBoard.successCount}/失败 ${blueBoard.failureCount}`,
      [
        {
          dimension: "成功 · 战术决策",
          red: pick(redBoard, "success", "tactical"),
          blue: pick(blueBoard, "success", "tactical"),
          note: "如探测对象、进攻地点等局部行动",
        },
        {
          dimension: "成功 · 战役决策",
          red: pick(redBoard, "success", "campaign"),
          blue: pick(blueBoard, "success", "campaign"),
          note: "如科技节奏、兵力扩充等阶段决策",
        },
        {
          dimension: "失败 · 战术决策",
          red: pick(redBoard, "failure", "tactical"),
          blue: pick(blueBoard, "failure", "tactical"),
          note: "局部误判或执行偏差",
        },
        {
          dimension: "失败 · 战役决策",
          red: pick(redBoard, "failure", "campaign"),
          blue: pick(blueBoard, "failure", "campaign"),
          note: "阶段节奏或体系建设偏差",
        },
      ],
      [
        cmpDash(redBoard.verdict, "红方评估生成中"),
        cmpDash(blueBoard.verdict, "蓝方评估生成中"),
      ],
    );
  };


  const knowledge = () => {
    const redBoard = buildKnowledgeExperienceBoard("red");
    const blueBoard = buildKnowledgeExperienceBoard("blue");
    const pickRound = (board, idx) => board.rounds[idx] || board.rounds[0] || null;
    const dimLabel = (key) => (KNOWLEDGE_CHAIN_DIMS.find((d) => d.key === key) || {}).label || key;
    const chainBrief = (sideBlock, dim) => {
      const chain = getKnowledgeSideChain(sideBlock, dim);
      if (!chain) return "暂无";
      return `${dimLabel(dim)}：${chain.conclusion || "—"}`;
    };
    const r0 = pickRound(redBoard, 0);
    const b0 = pickRound(blueBoard, 0);
    return cmpBoard(
      "知识与经验库更新 · 红蓝总体对比",
      "四维博弈思维链（打/探/防/保）与经验沉淀更新对照",
      `红 ${redBoard.roundCount} 轮 / 经验 ${redBoard.memoryCount}；蓝 ${blueBoard.roundCount} 轮 / 经验 ${blueBoard.memoryCount}`,
      [
        {
          dimension: "推演主轴",
          red: redBoard.summary,
          blue: blueBoard.summary,
          note: "各观察阵营的自博弈主线摘要",
        },
        {
          dimension: "首轮 · 打击维",
          red: chainBrief(r0?.ourAction, "strike"),
          blue: chainBrief(b0?.ourAction, "strike"),
          note: "第 1 轮我方打击子链结论",
        },
        {
          dimension: "首轮 · 探测维",
          red: chainBrief(r0?.ourAction, "detect"),
          blue: chainBrief(b0?.ourAction, "detect"),
          note: "第 1 轮我方探测子链结论",
        },
        {
          dimension: "经验沉淀与更新",
          red: `成功 ${redBoard.successMemoryCount} / 失败 ${redBoard.failureMemoryCount} · 共 ${redBoard.memoryCount}`,
          blue: `成功 ${blueBoard.successMemoryCount} / 失败 ${blueBoard.failureMemoryCount} · 共 ${blueBoard.memoryCount}`,
          note: "入库与规则修订条目计数",
        },
      ],
      [
        `红轮次 ${redBoard.roundCount} · 蓝轮次 ${blueBoard.roundCount}`,
        `红经验 ${redBoard.memoryCount} · 蓝经验 ${blueBoard.memoryCount}`,
      ],
    );
  };

  const builders = {
    awareness,
    force,
    intent,
    risk,
    killchain,
    critical,
    plan,
    tactics,
    knowledge,
    overview,
  };
  const build = builders[layer] || overview;
  return build();
}

/** 战术战法评估：按阵营整理的成败案例（对外不标注来源） */
const TACTICS_EVALUATION_CASES = {
  red: {
    horizon: "本局推演窗",
    verdict: "战术层侦察与峡谷防守闭环较好；战役层扩矿节奏一度偏慢，科技切换及时挽回。成功条目多指向对敌 OODA 的有效破环。",
    success: {
      tactical: [
        {
          title: "对中路峡谷实施 Overlord 持续探测",
          detail: "以双王虫交错覆盖峡谷南北口，提前标出蓝方蟑螂前压队形。",
          focus: "探测对象：峡谷通道 / 蓝方突击群",
          outcome: "接触前 20s 完成敌情更新，防守部署未失位",
          oodaBreak: {
            code: "O",
            ring: "观察",
            text: "破坏对方观察环：先手开雾使其队形与补给采信滞后，后续判断建立在过期信息上。",
          },
        },
        {
          title: "对 B2 高地口实施毒爆侧翼打击",
          detail: "主力刺蛇正面牵制，毒爆自侧翼切入蓝方前排，打散一字推进。",
          focus: "进攻地点：B2→峡谷南坡",
          outcome: "蓝方前排损失上升，压迫节奏被打断",
          oodaBreak: {
            code: "A",
            ring: "行动",
            text: "破坏对方行动环：侧翼引爆使其既定推进脚本落空，被迫中断行动并回流重组。",
          },
        },
      ],
      campaign: [
        {
          title: "适时转入刺蛇穴科技线",
          detail: "在确认蓝方蟑螂比重升高后，优先完成刺蛇穴与进化腔升级。",
          focus: "科技节点：远程防空/地面输出",
          outcome: "后续峡谷拉扯中具备对位输出能力",
          oodaBreak: {
            code: "D",
            ring: "决策",
            text: "破坏对方决策环：科技对位完成后，对方「继续硬压」的决策收益骤降，拍板窗口被压缩。",
          },
        },
        {
          title: "主矿稳定后再抽调扩编毒爆集群",
          detail: "工蜂与补给安全阈值内，再分批转化毒爆，避免经济塌陷。",
          focus: "兵力扩充：打击集群规模",
          outcome: "打击密度提升且矿线未中断",
          oodaBreak: {
            code: "O",
            ring: "判断",
            text: "破坏对方判断环：可持续打击密度改变其对「红方无力反击」的假设，判断被迫改写。",
          },
        },
      ],
    },
    failure: {
      tactical: [
        {
          title: "对东侧绕行侦察覆盖不足",
          detail: "侧翼小狗渗透时未及时补侦察，分矿口信息滞后。",
          focus: "探测对象：东翼走廊 / 分矿外围",
          outcome: "一度丢失侧翼预警，被迫临时回调防守",
          oodaBreak: {
            code: "O",
            ring: "观察",
            failed: true,
            text: "未能破坏对方观察环：己方侧翼采信缺口反被利用，对方观察反而更完整。",
          },
        },
        {
          title: "对蓝方第三矿方向进攻过早",
          detail: "关键节点刚出现即强推，缺少刺蛇护航，推进受阻。",
          focus: "进攻地点：蓝三矿脚手架",
          outcome: "突击受挫，需回撤重整",
          oodaBreak: {
            code: "A",
            ring: "行动",
            failed: true,
            text: "未能破坏对方行动环：过早突击未打断其扩编节奏，己方行动反被其防守闭环消化。",
          },
        },
      ],
      campaign: [
        {
          title: "三矿扩建启动偏晚",
          detail: "中期仍长时间双矿运转，瓦斯优势未能尽快转化为数量优势。",
          focus: "兵力扩充：经济底座",
          outcome: "前线持续承压，补给弹性下降",
          oodaBreak: {
            code: "D",
            ring: "决策",
            failed: true,
            text: "未能破坏对方决策环：经济决策偏慢，对方仍可按原决策持续加压。",
          },
        },
        {
          title: "菌毯升级节奏与前线需求错位",
          detail: "蜂巢过渡偏快但地面兵营产能未同步拉高。",
          focus: "科技节点：阶段转换",
          outcome: "科技点亮后仍短暂缺少可执行兵力",
          oodaBreak: {
            code: "O",
            ring: "判断",
            failed: true,
            text: "未能破坏对方判断环：阶段转换空窗让对方确认「红方暂无增量」，判断更敢下压。",
          },
        },
      ],
    },
  },
  blue: {
    horizon: "本局推演窗",
    verdict: "战役层蟑螂体系成型快、前线集中度高；战术层侧翼欺骗有效，但对红方防守反击识别偏慢。成功条目对应不同 OODA 破环点。",
    success: {
      tactical: [
        {
          title: "对红方分矿走廊实施小狗持续侦察",
          detail: "东侧绕行确认红方未真正快扩，修正错误意图假设。",
          focus: "探测对象：红分矿 / 侧翼通道",
          outcome: "意图修正后兵力重新对准毒爆群",
          oodaBreak: {
            code: "O",
            ring: "观察",
            text: "破坏对方观察环：侧翼证伪使其「防快扩」抽调暴露，采信与兵力配置不同步。",
          },
        },
        {
          title: "对峡谷北口实施蟑螂正面压制",
          detail: "一字推进压缩红方活动空间，迫使毒爆过早交战。",
          focus: "进攻地点：峡谷北坡",
          outcome: "前线压力占优，短暂掌握主动",
          oodaBreak: {
            code: "A",
            ring: "行动",
            text: "破坏对方行动环：压缩空间迫使其毒爆提前交战，打乱既定「后置开火」行动脚本。",
          },
        },
      ],
      campaign: [
        {
          title: "适时完成蟑螂巢与进化腔",
          detail: "双矿运转稳定后优先点亮蟑螂线，形成中期地面主干。",
          focus: "科技节点：地面突击体系",
          outcome: "中路集中度与瞬时伤害显著提高",
          oodaBreak: {
            code: "D",
            ring: "决策",
            text: "破坏对方决策环：体系成型后对方「继续纯防守」决策收益下降，被迫重新取舍。",
          },
        },
        {
          title: "在补给阈值内持续扩充突击群",
          detail: "工蜂与补给同步抬升，维持前线抽调不断档。",
          focus: "兵力扩充：突击集群",
          outcome: "前线流量持续，压制窗口拉长",
          oodaBreak: {
            code: "O",
            ring: "判断",
            text: "破坏对方判断环：持续流量否定其「蓝方无法维持高压」假设，判断被迫上调威胁等级。",
          },
        },
      ],
    },
    failure: {
      tactical: [
        {
          title: "对红方假扩动作误判过久",
          detail: "一度抽调前线去防并不存在的快扩，峡谷压力回落。",
          focus: "探测/研判对象：红方开扩意图",
          outcome: "出现错误响应窗口，主动权短暂丢失",
          oodaBreak: {
            code: "O",
            ring: "判断",
            failed: true,
            text: "未能破坏对方判断环：己方判断被假扩带走，反让对方获得行动窗口。",
          },
        },
        {
          title: "对毒爆落点规避不及时",
          detail: "发现红链后仍沿原路线推进，侧翼拆网启动偏慢。",
          focus: "进攻路线：峡谷正面",
          outcome: "前排承伤升高，需重组队形",
          oodaBreak: {
            code: "A",
            ring: "行动",
            failed: true,
            text: "未能破坏对方行动环：规避过慢，对方侧翼打击行动顺利闭环。",
          },
        },
      ],
      campaign: [
        {
          title: "家门口防御冗余不足",
          detail: "主力前压时 A1 口刺蛇线偏薄，回防成本高。",
          focus: "兵力扩充：防守冗余",
          outcome: "侧翼告警时缺少就近拦截面",
          oodaBreak: {
            code: "D",
            ring: "决策",
            failed: true,
            text: "未能破坏对方决策环：后院偏薄让对方「牵制抽调」决策更易成立。",
          },
        },
        {
          title: "三矿护卫未与关键节点同步加强",
          detail: "经济点扩展后护卫未跟上，节点暴露风险上升。",
          focus: "科技/扩展节点：三矿安全",
          outcome: "关键经济节点防护偏弱",
          oodaBreak: {
            code: "O",
            ring: "观察",
            failed: true,
            text: "未能破坏对方观察环：节点暴露给对方清晰观察目标，反而强化其采信。",
          },
        },
      ],
    },
  },
};

function buildTacticsEvaluationBoard(sideKey) {
  const side = sideKey === "blue" ? "blue" : "red";
  const raw = TACTICS_EVALUATION_CASES[side] || TACTICS_EVALUATION_CASES.red;
  const successTactical = raw.success?.tactical || [];
  const successCampaign = raw.success?.campaign || [];
  const failureTactical = raw.failure?.tactical || [];
  const failureCampaign = raw.failure?.campaign || [];
  return {
    side,
    horizon: raw.horizon || "本局推演窗",
    verdict: raw.verdict || "",
    success: {
      tactical: successTactical,
      campaign: successCampaign,
    },
    failure: {
      tactical: failureTactical,
      campaign: failureCampaign,
    },
    successCount: successTactical.length + successCampaign.length,
    failureCount: failureTactical.length + failureCampaign.length,
  };
}


/** 知识与经验库：多轮自博弈思维链 + 经验沉淀（按观察阵营组织） */
const KNOWLEDGE_CHAIN_DIMS = [
  { key: "strike", label: "打击" },
  { key: "detect", label: "探测" },
  { key: "defense", label: "防御" },
  { key: "support", label: "保障" },
];

/** 作战方案：流水线页签（方案 → 任务 → 指令 → 执行；思维链与杀伤网已独立到决策环） */
const PLAN_PIPELINE_TABS = [
  { key: "bus", label: "方案生成智能体", idx: "01", hint: "写出方案" },
  { key: "tasksplit", label: "任务生成智能体", idx: "02", hint: "拆成任务" },
  { key: "unitcomp", label: "指令生成智能体", idx: "03", hint: "编成指令" },
  { key: "parser", label: "指令执行智能体", idx: "04", hint: "盯住队列" },
];

const COT_ROLE_AGENTS = {
  ours: "红方方案规划智能体",
  enemy: "蓝方行为预测智能体",
  reflect: "方案反思纠正智能体",
  final: "红方方案总结智能体",
};

const COT_DIM_LABELS = {
  strike: "打击",
  detect: "探测",
  defense: "防御",
  support: "保障",
  environment: "作战环境",
};

function cotDimLabelOf(dimKey) {
  return COT_DIM_LABELS[dimKey] || "打击";
}

/** 自博弈席位按当前维、红/蓝对象命名 */
function cotRoleAgentName(role, dimKey) {
  const dim = dimKey === "environment" ? "兵力布势" : cotDimLabelOf(dimKey);
  if (role === "ours") return `红方${dim}方案规划智能体`;
  if (role === "enemy") return `蓝方${dim}行为预测智能体`;
  if (role === "reflect") return `红方${dim}方案反思纠正智能体`;
  if (role === "final") return `红方${dim}方案总结智能体`;
  return "";
}

/**
 * 自博弈思维链（写死演示）：
 * 红方方案规划 → 蓝方行为预测 → 方案反思纠正 → 再循环 → 由红方方案总结收敛
 */
const PLAN_SELFPLAY_LOOP = {
  title: "峡谷窗口 · 自博弈收敛",
  horizon: "本轮多步推演",
  thesis: "以红方方案开局，循环预测蓝方反应并反思纠正，最终收敛为可执行的「我方应对」。",
  situationDigest: "决策方案的目标：双矿运转、峡谷口刚开雾、对方蟑螂比重上升。",
  turns: [
    {
      key: "ours",
      role: "ours",
      title: "红方方案规划智能体",
      subtitle: "本轮 · 多步方案规划",
      dims: {
        strike: {
          summary: "高地口牵制为主，侧翼短打可开火，禁止深推与追残。",
          thoughts: [
            "第一步：确认 B2 高地俯射面仍在控，正面只做宽度压制不抢先开火。",
            "第二步：把毒爆从「等待」改成「可立即切入」，侧翼待命升级为可开火档。",
            "第三步：开火门槛写成「队形一字过线 + 补给抬升同时成立」。",
            "第四步：侧翼只打露出的前排，打完立刻回到复位点，不追残、不跟开阔走廊。",
            "第五步：若门槛未成立，改高地口牵制续压，等待下一拍复探回报。",
          ],
        },
        detect: {
          summary: "南北口与东翼双通道交错开雾，交战后优先复探绕后。",
          thoughts: [
            "第一步：双王虫钉住峡谷南北口，先拿蟑螂队形与补给节奏。",
            "第二步：东翼走廊与峡谷口同优先级，补第二只轻侦防滞后。",
            "第三步：把「队形是否一字过线、东翼有无渗透」写成打击门槛字段。",
            "第四步：交火一结束立刻复探绕后与补给抬升，不把王虫送进交火面。",
            "第五步：若东翼先被开雾，立即改挂一只王虫到东翼走廊校正判断。",
          ],
        },
        defense: {
          summary: "B2 刺蛇骨架不撤，回撤通道可视，家门口最低警戒不抽空。",
          thoughts: [
            "第一步：B2 高地留刺蛇骨架，接敌窗只覆盖侧翼开火那一段。",
            "第二步：回撤折线全程可视，窗一关就复位，不跟打击一起冲。",
            "第三步：家门口保留最低警戒，防止前线过抽造成真空。",
            "第四步：高地口再加一层阻挡，吸收对手流量换塌风险。",
            "第五步：守住矿线后才允许保障评估三矿条件开启。",
          ],
        },
        support: {
          summary: "产能服务待命与补损，瓦斯优先刺蛇穴，三矿条件化。",
          thoughts: [
            "第一步：暂缓无条件三矿，瓦斯优先刺蛇穴完成度。",
            "第二步：毒爆按待命规模生产，不按立刻交战规模堆。",
            "第三步：工蜂曲线保住，补损队列与侦察续航同权。",
            "第四步：损失速率一旦过线，停扩转补，产能跟损失走。",
            "第五步：仅当东翼无渗透且矿线仍在防御覆盖时，才评估开启三矿。",
          ],
        },
      },
      conclusion: "红方本轮方案：信息先手 + 可打可收的有限侧翼打击 + 矿线护航，三矿只作条件动作。",
    },
    {
      key: "enemy",
      role: "enemy",
      title: "蓝方行为预测智能体",
      subtitle: "本轮 · 多步对手预测",
      dims: {
        strike: {
          summary: "北口持续正面施压，得手前排后更可能转西翼打经济。",
          thoughts: [
            "第一步：沿峡谷北坡维持正面压力，追求瞬时伤害差。",
            "第二步：见毒爆则风筝拆网，流量不从前线轻易撤回。",
            "第三步：若前排被拆，改绕西翼/东翼打经济，而不是二次硬推高地。",
            "第四步：把剩余蟑螂转成渗透，避开红方俯射面。",
            "第五步：一旦发现红方家门口真空，立即改目标直压矿线。",
          ],
        },
        detect: {
          summary: "侧翼小狗绕行证伪快扩，用回报修正正面意图。",
          thoughts: [
            "第一步：小狗东侧/西侧绕行确认分矿与回防厚度。",
            "第二步：用侧翼回报修正正面意图，不全凭中路观察。",
            "第三步：限时抽调侦察，避免长期掉线导致改线滞后。",
            "第四步：交火后优先确认红方是否抽空家门口。",
            "第五步：若红方东翼先亮，蓝方会加快西翼走廊出口的渗透节奏。",
          ],
        },
        defense: {
          summary: "家门口薄线换前线流量，正面改折线避爆。",
          thoughts: [
            "第一步：家门口只留薄线，接受回防成本换前线流量。",
            "第二步：行军预留避爆折线，降低被侧翼惩罚。",
            "第三步：前排受损后正面改防守姿态，收到峡谷南坡阴影。",
            "第四步：北口可封但不攻坚，主压从已开走廊出去。",
            "第五步：用侧翼得手对冲正面脆，缓冲短是其固有弱点。",
          ],
        },
        support: {
          summary: "补给优先突击群维持蟑螂比重，失败则回头补工与三矿。",
          thoughts: [
            "第一步：补给增量优先突击群，维持蟑螂比重。",
            "第二步：侦察按限时任务抽调，避免经济线长期失血。",
            "第三步：突击失败后回头补工蜂与三矿，用经济翻盘。",
            "第四步：侧翼得手前继续双矿吸血，不提前散开产能。",
            "第五步：若红方稳住矿线，蓝方会把资源从硬推改到渗透编制。",
          ],
        },
      },
      conclusion: "蓝方脚本：正面持续压线 + 侧翼活修正意图；前排被拆后更可能转经济打击。",
    },
    {
      key: "reflect",
      role: "reflect",
      title: "方案反思纠正智能体",
      subtitle: "本轮 · 多步对照纠正",
      dims: {
        strike: {
          summary: "后置开火会亏窗；一波后追击会走进绕后脚本。",
          thoughts: [
            "第一步：对照张力——对方持续压线，我方后置开火会亏时间窗。",
            "第二步：纠正开火档位：毒爆可立即切入，但仍禁止深推。",
            "第三步：纠正追击欲：打完即收，任务改为「清前排 + 复位」。",
            "第四步：交战触发条件写成「仍在高地俯射面内」。",
            "第五步：二次脉冲仅在补给抬升或队形再露空档时启动。",
          ],
        },
        detect: {
          summary: "东翼必须先亮，否则判断滞后会被侧翼撕口。",
          thoughts: [
            "第一步：东翼若被对手先开雾，我方判断会滞后。",
            "第二步：纠正优先级：东翼走廊与峡谷口同权。",
            "第三步：把一只王虫从峡谷口改挂东翼走廊。",
            "第四步：交战后复探绕后必须先于二次开火决策。",
            "第五步：渗透告警一旦触发，打击门槛立即降级为牵制。",
          ],
        },
        defense: {
          summary: "薄回防会被流量换塌，家门口真空是转经济打击条件。",
          thoughts: [
            "第一步：薄回防会被对手流量换塌，必须加厚高地阻挡。",
            "第二步：回撤通道必须可视，接敌窗与复位折线绑定。",
            "第三步：复位后立刻回填最低防守骨架，杜绝家门口真空。",
            "第四步：矿线护航优先级不低于前线接敌。",
            "第五步：防线未塌才允许保障把暂缓三矿改成条件开启。",
          ],
        },
        support: {
          summary: "产能过低会被耗干；三矿只能条件开，不能情绪开。",
          thoughts: [
            "第一步：暂缓三矿正确，但产能过低会被耗干。",
            "第二步：纠正：工蜂曲线保住，毒爆产能上调一档待命。",
            "第三步：损失速率过线则停扩转补，产能跟损失走。",
            "第四步：三矿仅当东翼无渗透且矿线安全时启动。",
            "第五步：侦察续航与补损同权，避免二次脉冲断档。",
          ],
        },
      },
      conclusion: "拧正后的闭环：可打可收 → 打完即收 → 东翼先亮 → 矿线回填 → 三矿条件化。",
    },
  ],
  finalPlan: {
    title: "红方方案总结智能体",
    subtitle: "最终行动方案 · 由多步自博弈收敛",
    dims: {
      strike: "侧翼可开火但禁止深推；打完前排立即收网复位；二次脉冲看门槛。",
      detect: "峡谷口与东翼双通道常亮，交战后优先复探绕后与补给抬升。",
      defense: "高地骨架不撤，回撤通道可视，家门口保持最低警戒并回填。",
      support: "产能服务待命与补损；三矿仅在东翼安全且矿线在控时开启。",
    },
    steps: [
      "第一步：南北口与东翼交错开雾，确认队形宽度与补给节奏。",
      "第二步：B2 高地占位 + 刺蛇骨架就位，接敌窗只覆盖侧翼开火段。",
      "第三步：门槛成立则侧翼打前排，不追残，打完立刻复位。",
      "第四步：交战后复探绕后，回填家门口最低防守，禁止追击。",
      "第五步：损失过线停扩转补；东翼无渗透且矿线安全才开三矿。",
      "第六步：约束总轴——信息不先手不开火，开火不深推，深推不追残。",
    ],
    conclusion: "最终方案是一条可执行闭环：信息先手 → 有限打击 → 窗口保护 → 资源再决策。",
  },
};


/**
 * 兵力布势自博弈思维链（写死演示）：
 * 对齐打击维结构——只出红方方案 / 蓝方预测 / 反思纠正 / 方案总结，
 * 按双方编制落点过招，不做当前窗/下一窗切换。
 */
const ENV_SELFPLAY_LOOP = {
  title: "峡谷窗口 · 兵力布势自博弈",
  horizon: "本轮窗口",
  thesis: "正对双方可见编制与落点过招：红方先给出本轮兵力布势，预测蓝方布势反应并反思纠正，收敛编成落位。",
  situationDigest: "决策方案的目标：红方主力在南口/B2，蓝方蟑螂压北口、西翼有虫群，双矿工蜂未抽空。",
  turns: [
    {
      key: "ours",
      role: "ours",
      title: "红方兵力布势方案规划智能体",
      subtitle: "本轮 · 布势多步方案",
      summary: "打击钉南口前排，探测占南北口与东翼，防御留 B2 骨架，保障工蜂不离矿。",
      thoughts: [
        "第一步：刺蛇/毒爆主力落在南口—B2 交战线，不把前排抽空到西翼。",
        "第二步：王虫钉南北口，另留一只轻侦看东翼，开雾点不进交火面。",
        "第三步：B2 高地留刺蛇骨架，家门口最低警戒不撤。",
        "第四步：工蜂与女王留双矿，瓦斯优先补损，不抽矿堆前线。",
        "第五步：若北口压力抬升，只从前排抽一小队回填，不整体改线。",
      ],
      detail: "打击钉南口—B2，探测双通道加东翼，防御留高地骨架，保障工蜂不离矿。北口加压时只抽一小队回填，不整体改线。",
      conclusion: "本轮布势：前排南口、开雾双通道、骨架留 B2、经济不抽空。",
    },
    {
      key: "enemy",
      role: "enemy",
      title: "蓝方兵力布势行为预测智能体",
      subtitle: "本轮 · 对手布势反应",
      summary: "蟑螂压北口做正面厚度，虫群走西翼走廊，家门口只留薄线。",
      thoughts: [
        "第一步：北口集结蟑螂维持正面流量，不把主力拆去守家。",
        "第二步：西翼虫群试探改线，准备在前排受阻时转压经济。",
        "第三步：王虫限时抽调开雾，不会长期钉在同一通道。",
        "第四步：二矿工蜂继续吸血，突击群补给优先于扩编。",
        "第五步：若正面失血，会把侧翼编成收拢回填北口，而不是深推。",
      ],
      detail: "蓝方布势是北口厚、西翼活、家里薄：蟑螂顶正面，虫群走走廊，侦察限时抽调，经济继续供突击。",
      conclusion: "蓝方脚本：正面压线 + 侧翼活修正，家门口接受真空。",
    },
    {
      key: "reflect",
      role: "reflect",
      title: "红方兵力布势方案反思纠正智能体",
      subtitle: "本轮 · 布势纠正",
      summary: "若全军压南口，西翼会空；探测进交火面会断雾；工蜂一抽矿线先塌。",
      thoughts: [
        "第一步：对照张力——蓝方西翼活，红方不能把打击全堆南口。",
        "第二步：纠正：东翼与家门口各留最低编制，前排只保持可开火厚度。",
        "第三步：王虫禁止进交火面，开雾点与打击点错开。",
        "第四步：保障不抽矿，补损跟损失走，不为追击堆毒爆。",
        "第五步：若北口被撕，回填用防御骨架而不是把探测拉去挡线。",
      ],
      detail: "拧正布势：前排有厚度但不抽空侧翼与家门口；侦察与交火错位；工蜂不离矿。",
      conclusion: "拧正：南口可开火、西翼不空、开雾不进火、矿线不抽。",
    },
  ],
  finalPlan: {
    title: "红方兵力布势方案总结智能体",
    subtitle: "兵力布势方案 · 由自博弈收敛",
    summary: "南口保持可开火前排，探测双通道错位开雾，B2 骨架不撤，工蜂留矿；防蓝方西翼改线。",
    steps: [
      "第一步：打击主力落南口—B2，禁止整队跟西翼。",
      "第二步：王虫钉南北口，东翼留一只轻侦，均不进交火面。",
      "第三步：高地骨架与家门口最低警戒保持。",
      "第四步：工蜂不抽矿，补损按损失回填前排。",
      "第五步：总约束——侧翼不空、开雾不挡线、矿线不塌。",
    ],
    conclusion: "本轮布势：南口可开火、双通道开雾、B2 骨架在、工蜂留矿，不跟蓝方西翼整队改线。",
  },
};


function makeDemoEnvCotRound() {
  return {
    id: "env-cot-demo",
    gameTime: "00:00",
    gameSeconds: 0,
    generatedAt: "00:00:08",
    source: "demo",
    loading: false,
    error: "",
    loop: {
      ...ENV_SELFPLAY_LOOP,
      title: "00:00 开局 · 环境自博弈",
      horizon: "本轮窗口",
      situationDigest: ENV_SELFPLAY_LOOP.situationDigest,
    },
  };
}

function makeDemoEnvCotHistory() {
  const stamps = [
    { id: "env-cot-demo-1", sec: 0, title: "00:00 开局勘察 · 南口高地先判", thesis: "先判南口高地与东翼岩石，不急着开岩。" },
    { id: "env-cot-demo-2", sec: 40, title: "00:40 开雾启动 · 塔权优先", thesis: "塔权高于开岩，南口半封先于东翼动作。" },
    { id: "env-cot-demo-3", sec: 80, title: "01:20 西翼走廊预警 · 贴高地", thesis: "蓝方西翼已开，交战必须仍在俯射面内。" },
    { id: "env-cot-demo-4", sec: 120, title: "02:00 隘口半封 · 东翼只卡", thesis: "东翼窄廊设卡不开岩，收网回两层缓冲。" },
    { id: "env-cot-demo-5", sec: 160, title: "02:40 条件开岩门槛 · 收敛", thesis: "开岩仅无渗透且塔在控；不跟开阔走廊。" },
  ];
  return stamps.map((row) => {
    const clock = formatGameClockFromSeconds(row.sec);
    const item = makeDemoEnvCotRound();
    item.id = row.id;
    item.gameTime = clock;
    item.gameSeconds = row.sec;
    item.generatedAt = `${clock}:${String(8 + (row.sec % 50)).padStart(2, "0")}`;
    item.loop = {
      ...ENV_SELFPLAY_LOOP,
      title: row.title,
      thesis: row.thesis,
      situationDigest: ENV_SELFPLAY_LOOP.situationDigest,
    };
    return item;
  });
}

function envCotPack(turn) {
  if (!turn || typeof turn !== "object") {
    return { summary: "—", detail: "", thoughts: [] };
  }
  const summary = String(turn.summary || turn.conclusion || "").trim() || "—";
  const detail = String(turn.detail || "").trim();
  const thoughts = Array.isArray(turn.thoughts) && turn.thoughts.length
    ? turn.thoughts.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 8)
    : splitCotThoughts(detail);
  return { summary, detail, thoughts };
}

function snapshotEnvCotLoop(loop) {
  const src = loop && typeof loop === "object" ? loop : ENV_SELFPLAY_LOOP;
  try {
    return JSON.parse(JSON.stringify(src));
  } catch {
    return ENV_SELFPLAY_LOOP;
  }
}

function normalizeEnvCotLoop(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const turnsIn = Array.isArray(src.turns) ? src.turns : [];
  const byRole = Object.create(null);
  for (const turn of turnsIn) {
    if (!turn || typeof turn !== "object") continue;
    const role = String(turn.role || turn.key || "").toLowerCase();
    if (role === "ours" || role === "our" || role === "self" || role === "red") byRole.ours = turn;
    else if (role === "enemy" || role === "opp" || role === "opponent" || role === "blue") byRole.enemy = turn;
    else if (role === "reflect" || role === "revise" || role === "correct") byRole.reflect = turn;
  }
  const specs = [
    ["ours", cotRoleAgentName("ours", "environment"), "本轮 · 布势方案"],
    ["enemy", cotRoleAgentName("enemy", "environment"), "本轮 · 对手布势反应"],
    ["reflect", cotRoleAgentName("reflect", "environment"), "本轮 · 对照张力"],
  ];
  const turns = specs.map(([role, title, subtitle]) => {
    const item = byRole[role] || {};
    const pack = envCotPack(item);
    return {
      key: item.key || role,
      role,
      title: String(item.title || title).trim() || title,
      subtitle: String(item.subtitle || subtitle).trim() || subtitle,
      summary: pack.summary === "—" ? "" : pack.summary,
      detail: pack.detail || pack.thoughts.join("；"),
      thoughts: pack.thoughts,
      conclusion: String(item.conclusion || "").trim(),
    };
  });
  const finalRaw = src.finalPlan && typeof src.finalPlan === "object"
    ? src.finalPlan
    : (src.final_plan && typeof src.final_plan === "object" ? src.final_plan : {});
  const finalSummary = String(finalRaw.summary || finalRaw.text || finalRaw.detail || "").trim();
  const finalSteps = Array.isArray(finalRaw.steps)
    ? finalRaw.steps.map((step) => String(step || "").trim()).filter(Boolean).slice(0, 6)
    : [];
  const finalConclusion = String(finalRaw.conclusion || "").trim();
  return {
    title: String(src.title || "本轮布势推演").trim() || "本轮布势推演",
    horizon: String(src.horizon || "本轮窗口").trim() || "本轮窗口",
    thesis: String(src.thesis || "").trim(),
    situationDigest: String(src.situationDigest || src.situation_digest || "").trim(),
    turns,
    finalPlan: {
      title: String(finalRaw.title || cotRoleAgentName("final", "environment")).trim() || cotRoleAgentName("final", "environment"),
      subtitle: String(finalRaw.subtitle || "").trim(),
      summary: finalSummary || finalConclusion,
      steps: finalSteps.length
        ? finalSteps
        : turns.map((turn) => turn.conclusion || turn.summary).filter(Boolean).slice(0, 3),
      conclusion: finalConclusion || finalSummary || (turns[turns.length - 1]?.conclusion || ""),
    },
  };
}

/** 作战筹划 / 红方指挥控制智能体：永久展示的样例方案卷宗 */
const PLANNER_DEMO_CASE = {
  key: "plan-demo-permanent",
  signature: "plan-demo-permanent",
  channel: "latest",
  title: "作战筹划样例 · 峡谷窗口闭环",
  prompt: "基于当前自博弈收敛结论，生成可执行作战方案。",
  reply: [
    "1. Current Stage of the Match;",
    "游戏时间02:40，处于早中期交火门槛确认窗。双矿运转、峡谷口刚开雾，对方蟑螂比重上升；红方需在信息不完整条件下完成「可打可收」的闭环，而不是深推追残。",
    "",
    "2. Our Zerg Units and Buildings;",
    "红方当前以蟑螂/刺蛇/毒爆混编驻守 B2 高地口，双王虫覆盖南北口与东翼，家门口保留刺蛇骨架与最低警戒。主矿与二矿运转，瓦斯优先刺蛇穴完成度，工蜂曲线不得因前线抽空塌陷。",
    "",
    "3. Our current Zerg Operational Strategy;",
    "执行「探测先手 + 有限侧翼打击 + 窗口保护」策略。打击在队形一字过线且补给抬升时侧翼突击前排，打完立即收网复位；探测把队形宽度与东翼渗透写成开火门槛；防御钉住接敌窗与回撤折线；保障产能服务待命与补损，三矿仅条件开启。",
    "",
    "4. Opponent's Units and Buildings;",
    "蓝方北口集结蟑螂维持正面压力，西翼虫群沿已开走廊试探改线；双矿在控，北口到主矿缓冲偏短。尚未观察到其厚栈深防，更像「正面压线 + 侧翼活修正」脚本。",
    "",
    "5. Opponent's Tactical Plan and our Potential Risks;",
    "蓝方更可能在前排受阻后转西翼/东翼打经济，诱红方脱高地到开阔面。主要风险：深推丢俯射、家门口被抽空、东翼被先开雾导致判断滞后。若追残，正好走进对手绕后脚本。",
    "",
    "6. Scouting Intel;",
    "必须保持南北口与东翼双通道开雾，交战后优先复探绕后与补给抬升。王虫不进交火面；东翼走廊与峡谷口同优先级，防止侧翼改线信息滞后。",
    "",
    "7. Whether to attack;",
    "True。门槛成立（队形过线 + 补给抬升 + 仍在俯射面内）则侧翼短打前排；否则高地口牵制续压。打完即收，禁止深推追残。",
    "",
    "8. Analyze based on self-play convergence;",
    "打击任务：在 B2 高地口实施牵制打击，队形一字过线且补给抬升后侧翼突击前排，打完立即收网复位，禁止深推追残。",
    "探测任务：双王虫交错开雾覆盖峡谷南北口与东翼，持续回报队形宽度、补给节奏与渗透告警。",
    "防御任务：B2 高地保留刺蛇骨架驻守，接敌窗短开短关，回撤通道可视，家门口最低警戒不得抽空。",
    "保障任务：瓦斯优先刺蛇穴，产能服务待命与补损；三矿仅东翼无渗透且矿线在控时条件开启。",
    "",
    "```json",
    "{",
    '  "strategic_plan": {',
    '    "primary_intent": "fast_expand",',
    '    "secondary_intents": ["contain", "harass"],',
    '    "intent_confidence": 0.82,',
    '    "intent_rationale_short": "先信息后有限侧翼打击，打完收网护矿线；三矿条件化，避免情绪开扩。",',
    '    "horizon_sec": 120',
    "  }",
    "}",
    "```",
  ].join("\n"),
  displayReply: "",
  promptTime: "02:40",
  updatedAt: "02:40:30",
  isTyping: false,
  source: "demo",
};
PLANNER_DEMO_CASE.displayReply = PLANNER_DEMO_CASE.reply;

const SAMPLE_COMMAND_SEQUENCE = [
  { order: 1, unit: "Overlord, A7", action: "Move", target: "B2", category: "scout", locationLabel: "B2", summary: "Overlord, A7 / Move / B2", updatedAt: "2026-09-17T00:02:10" },
  { order: 2, unit: "Overlord, B7", action: "Move", target: "B3", category: "scout", locationLabel: "B3", summary: "Overlord, B7 / Move / B3", updatedAt: "2026-09-17T00:02:10" },
  { order: 3, unit: "Roach, A2", action: "Move", target: "B2", category: "attack", locationLabel: "B2", summary: "Roach, A2 / Move / B2", updatedAt: "2026-09-17T00:02:10" },
  { order: 4, unit: "Hydralisk, A2", action: "Attack", target: "B2", category: "attack", locationLabel: "B2", summary: "Hydralisk, A2 / Attack / B2", updatedAt: "2026-09-17T00:02:10" },
  { order: 5, unit: "Baneling, B2", action: "Attack", target: "B1", category: "attack", locationLabel: "B1", summary: "Baneling, B2 / Attack / B1", updatedAt: "2026-09-17T00:02:10" },
  { order: 6, unit: "Spine Crawler, A2", action: "Hold", target: "A2", category: "defend", locationLabel: "A2", summary: "Spine Crawler, A2 / Hold / A2", updatedAt: "2026-09-17T00:02:10" },
  { order: 7, unit: "Drone, A1", action: "Build", target: "Spine Crawler, B2", category: "build", locationLabel: "B2", summary: "Drone, A1 / Build / Spine Crawler, B2", updatedAt: "2026-09-17T00:02:10" },
  { order: 8, unit: "Larva, A1", action: "Hatch", target: "Hydralisk", category: "train", locationLabel: "A1", summary: "Larva, A1 / Hatch / Hydralisk", updatedAt: "2026-09-17T00:02:10" },
  { order: 9, unit: "Larva, A1", action: "Hatch", target: "Roach", category: "train", locationLabel: "A1", summary: "Larva, A1 / Hatch / Roach", updatedAt: "2026-09-17T00:02:10" },
  { order: 10, unit: "Drone, A1", action: "Gather gas", target: "Extractor, A1", category: "gather", locationLabel: "A1", summary: "Drone, A1 / Gather gas / Extractor, A1", updatedAt: "2026-09-17T00:02:10" },
];

const SAMPLE_EXECUTED_COMMANDS = [
  { order: 101, unit: "Overlord, A1", action: "Move", target: "A7", category: "scout", locationLabel: "A7", summary: "Overlord, A1 / Move / A7", updatedAt: "2026-09-17T00:00:40", executedAt: "2026-09-17T00:00:48" },
  { order: 102, unit: "Drone, A1", action: "Build", target: "Extractor, A1", category: "build", locationLabel: "A1", summary: "Drone, A1 / Build / Extractor, A1", updatedAt: "2026-09-17T00:01:00", executedAt: "2026-09-17T00:01:10" },
  { order: 103, unit: "Queen, A1", action: "Inject", target: "Hatchery, A1", category: "other", locationLabel: "A1", summary: "Queen, A1 / Inject / Hatchery, A1", updatedAt: "2026-09-17T00:01:20", executedAt: "2026-09-17T00:01:28" },
  { order: 104, unit: "Roach, A1", action: "Move", target: "A2", category: "attack", locationLabel: "A2", summary: "Roach, A1 / Move / A2", updatedAt: "2026-09-17T00:01:40", executedAt: "2026-09-17T00:01:50" },
  { order: 105, unit: "Spine Crawler, A1", action: "Hold", target: "A2", category: "defend", locationLabel: "A2", summary: "Spine Crawler, A1 / Hold / A2", updatedAt: "2026-09-17T00:02:00", executedAt: "2026-09-17T00:02:08" },
  { order: 106, unit: "Larva, A1", action: "Hatch", target: "Overlord", category: "train", locationLabel: "A1", summary: "Larva, A1 / Hatch / Overlord", updatedAt: "2026-09-17T00:02:10", executedAt: "2026-09-17T00:02:18" },
];

/** 样例模式固化展示：五维 LLM / 认知问答文案（兵力行见 getSampleForceRows，须在 normalizeForceRows 之后） */
const SAMPLE_FORCE_LLM_SECTIONS = [
  {
    key: "strike",
    title: "打击类建议",
    summary: "B2 高地口牵制为主轴，队形一字过线且补给抬升前不开火；侧翼短打前排后必须立即收网复位，禁止深推与开阔面追残。",
    detail: "因为峡谷南口刚开雾、对方蟑螂比重上升，所以先确认队形宽度与补给门槛，而不是抢先交火。若对方过中线且俯射面仍在控，则侧翼打前排兑现伤害差；则打完不追残，回 B2 复位等待复探。二次脉冲只在补给再抬升或队形再露空档时启动，否则改高地口牵制续压。",
  },
  {
    key: "detect",
    title: "侦察探测建议",
    summary: "双王虫钉住南北口与东翼双通道，交战后优先复探绕后；开雾单位绝不送进交火面，把「队形是否过线、东翼有无渗透」写成打击门槛。",
    detail: "南北口交错开雾先拿蟑螂队形与补给节奏；东翼与峡谷口同优先级，防止侧翼改线信息滞后。交火一结束立刻复探绕后与补给抬升。若东翼被对手先开雾，立即改挂一只王虫到东翼走廊校正判断，并把打击门槛临时降级为牵制。",
  },
  {
    key: "defense",
    title: "防御类建议",
    summary: "B2 留刺蛇骨架，回撤通道可视，家门口最低警戒不被前线抽空；接敌窗只覆盖侧翼开火段，窗关即复位回填。",
    detail: "接敌窗与复位折线必须钉死，避免跟打击一起冲垮防线。东翼半控只卡不开岩，矿线护航优先级不低于前线接敌。复位后立刻回填最低防守骨架，杜绝家门口真空成为蓝方转经济打击的条件。",
  },
  {
    key: "support",
    title: "保障类建议",
    summary: "产能服务待命与补损，瓦斯优先刺蛇穴；三矿仅东翼安全且矿线在控时开启，损失过线则停扩转补。",
    detail: "矿 920 / 气 240 / 工约 24 / 双矿运转。先吊住侦察续航与二次脉冲产能，毒爆按待命规模生产而不是按立刻交战规模堆。工蜂曲线不得因前线抽空塌陷；三矿写成条件动作，情绪开扩一律禁止。",
  },
  {
    key: "environment",
    title: "作战环境建议",
    summary: "交战贴南口高地与隘口，稳塔优先于开岩，不跟到开阔走廊；利用蓝方正面脆，避开其侧翼活。",
    detail: "续控瞭望塔并钉住南坡/B2 俯射面。东翼窄廊设卡不开岩，开岩门槛写成「无渗透且塔在控」。收网后回到两层缓冲，禁止追残拉长战线。脱高地则改牵制，绝不被诱到开阔面打伤害差。",
  },
];

const SAMPLE_ORIENT_QA_BY_DIM = {
  strike: {
    loadedAt: "02:39:10",
    source: "sample-showcase",
    dimension: "strike",
    conclusion: "蓝方打击意图偏北口硬顶叠加西翼绕行：正面用蟑螂换瞬时伤害差，侧翼用虫群试探改线。红方在 B2 高地接战更赚，开火必须仍在俯射面内；一旦脱高地追残，俯射归零且补给与复位成本会快速上升。建议侧翼只打前排、打完即收，把二次脉冲留给复探确认后的下一拍。",
    steps: [
      { key: "intent", title: "蓝方打击意图识别", text: "北口集结蟑螂维持正面压力，追求一字过线后的瞬时伤害差；西翼虫群同步试探改线，准备在前排受阻时转打经济。其脚本不是单一硬推，而是「正面压线 + 侧翼活修正」。", evidence: "北口蟑螂×16；西翼虫群×22；北坡持续施压" },
      { key: "risk", title: "我方打击风险识别", text: "深推会丢俯射优势，开阔走廊对红方不利；若追残，王虫与收网编队容易脱节，补给与复位成本上升。东翼岩石未拆时走廊选择少，脱高地后更难重整队形。", evidence: "东翼岩石未拆；开阔走廊对红不利；复位折线依赖高地" },
      { key: "vuln", title: "蓝方打击脆弱性分析", text: "正面对高地展开宽度不足，缺塔控时侧翼虽活但正面偏脆；北坡缓冲短，一旦前排被拆，其硬推脚本会迅速失血并被迫改线。", evidence: "北坡宽度不足；缺塔控；正面脆、侧翼活" },
      { key: "nodes", title: "对蓝方关键节点分析", text: "北口集结骨架与西翼走廊出口是其施压与改线支点；压制这两个节点，可同时削弱其正面流量与侧翼渗透选项。", evidence: "B1 集结；B3 走廊出口；北口隘口" },
    ],
    messages: [],
  },
  detect: {
    loadedAt: "02:39:12",
    source: "sample-showcase",
    dimension: "detect",
    conclusion: "蓝方探测依赖北口开雾支撑集结，缺中路塔时侧翼回报易迟滞，改线决策会慢半拍。红方应用双王虫先钉南北口与东翼，把队形宽度与渗透告警写成打击门槛；交火后立刻复探绕后，严禁把开雾单位送进交火面。",
    steps: [
      { key: "intent", title: "蓝方探测意图识别", text: "北口开雾支撑蟑螂集结与正面施压；侧翼轻侦负责驱动改线，用有限视野换「是否继续硬推」的判断。侦察是限时任务，不会长期掉线在同一通道。", evidence: "北口王虫；西翼虫群探路；限时抽调痕迹" },
      { key: "risk", title: "我方探测风险识别", text: "王虫若进交火面会断雾，南北口信息会同时塌；东翼若被对手先开雾，红方对改线的判断会滞后，打击门槛也会失真。", evidence: "南口刚开雾；交火阈值临近；东翼优先级不足则滞后" },
      { key: "vuln", title: "蓝方探测脆弱性分析", text: "缺塔控时侧翼改线信息滞后，正面观察无法独立支撑决策；一旦北口开雾被压制，其集结与改线会同时失明。", evidence: "中路塔未稳控；侧翼回报链路脆弱" },
      { key: "nodes", title: "对蓝方关键节点分析", text: "北口开雾位与西翼走廊出口是其信息支点；打断这两点可迫使其在视野不足时继续硬推或过早散开。", evidence: "B1 开雾；B3 走廊；中路塔争议区" },
    ],
    messages: [],
  },
  defense: {
    loadedAt: "02:39:14",
    source: "sample-showcase",
    dimension: "defense",
    conclusion: "红方防线应钉住 B2 高地与家门口双点，接敌窗短开短关，避免被诱抽空。蓝方北口到主矿缓冲短，是其防守脆弱点；红方打完前排后若不清空追击欲，反而会把自己的回撤通道让出去。",
    steps: [
      { key: "intent", title: "蓝方防御意图识别", text: "北口封线保主矿，用西翼已开走廊换正面压力；家门口只留薄线，接受回防成本换前线流量。其防守是「用空间换时间」而不是厚栈深防。", evidence: "北口集结；西翼已开；家门口薄线" },
      { key: "risk", title: "我方防御风险识别", text: "前线过抽会导致家门口真空，蓝方侧翼得手后可直压矿线；回撤通道若不可视，接敌窗关闭后会溃成无序后撤。", evidence: "刺蛇骨架需留守；复位折线必须可视" },
      { key: "vuln", title: "蓝方防御脆弱性分析", text: "北口到主矿纵深短，一破即压矿；正面缺展开宽度时，防守弹性主要靠侧翼搅局，而不是本地纵深。", evidence: "缓冲不足；正面脆" },
      { key: "nodes", title: "对蓝方关键节点分析", text: "北口隘口骨架是其防守枢纽；同步盯住西翼走廊出口，防止其用改线掩盖正面失血。", evidence: "B1 隘口；B3 走廊出口" },
    ],
    messages: [],
  },
  support: {
    loadedAt: "02:39:16",
    source: "sample-showcase",
    dimension: "support",
    conclusion: "双方双矿运转。红方瓦斯应优先刺蛇穴与侦察续航，产能先服务待命与补损；三矿只能条件开启。蓝方侧翼得手前经济面仍脆，二矿与气矿是可压节点，但红方不能用情绪开扩去换短时编制。",
    steps: [
      { key: "intent", title: "蓝方保障意图识别", text: "继续双矿吸血维持蟑螂比重，侧翼活则把资源导向渗透与压经济；突击失败后会回头补工与三矿，用经济翻盘对冲本轮伤害差。", evidence: "双矿在控；突击群补给优先" },
      { key: "risk", title: "我方保障风险识别", text: "补损与侦察若掉线，二次脉冲会断档；瓦斯若被建筑科技抽干，毒爆待命档会掉到不可开火。工蜂曲线因前线抽空塌陷是最大隐患。", evidence: "瓦斯约 240；产能紧张；工蜂需保住" },
      { key: "vuln", title: "蓝方保障脆弱性分析", text: "侧翼得手前经济面仍脆，二矿暴露；一旦正面失血又转扩失败，其补给会同时服务不了突击与回血。", evidence: "二矿暴露；转扩窗口窄" },
      { key: "nodes", title: "对蓝方关键节点分析", text: "蓝方二矿与气矿是可压节点；在不脱高地的前提下，用有限打击兑现后的空间差去威胁这些节点，比深推基地更划算。", evidence: "B2 二矿；气矿点位" },
    ],
    messages: [],
  },
  environment: {
    loadedAt: "02:39:18",
    source: "sample-showcase",
    dimension: "environment",
    conclusion: "交战应贴南口高地与隘口，稳塔优先于开岩；利用蓝方正面脆，避开其侧翼活。红方东翼未开岩时走廊选择少，脱高地则俯射失效。开岩只能写成条件动作：无渗透且塔在控。",
    steps: [
      { key: "intent", title: "蓝方地形利用意图预测", text: "拆岩开走廊、侧翼改线，不硬顶南口高地；诱红方脱离俯射面到开阔走廊再打伤害差。中路塔争夺用于逼红方轻侦补位、分散开雾。", evidence: "西翼岩石已拆；北口可封但不攻坚；诱敌脱高地" },
      { key: "risk", title: "红方作战环境风险研判", text: "东翼未开岩走廊偏少，脱高地则俯射失效；丢塔会放大侧翼风险，使打击门槛与复探节奏同时失真。", evidence: "东翼岩石仍在；南坡依托；塔权敏感" },
      { key: "vuln", title: "蓝方作战环境脆弱性预测", text: "北口缓冲短、缺塔、正面难展开；侧翼活建立在红方愿意跟出开阔面的前提上，一旦红方钉口不跟，其脚本收益下降。", evidence: "北口纵深短；塔未控；正面脆" },
      { key: "nodes", title: "蓝方关键地形利用预测", text: "西翼走廊出口、北口隘口、中路瞭望塔是其地形支点；红方应续控塔、半封南口、东翼只卡不开。", evidence: "西翼出口；北口；中路塔" },
    ],
    messages: [],
  },
};

function hydrateSampleOrientMessages(byDim) {
  const out = {};
  for (const [dim, row] of Object.entries(byDim || {})) {
    const conclusion = row.conclusion || "";
    const label = dim === "environment" ? "作战环境" : dim;
    out[dim] = {
      ...row,
      messages: [
        {
          role: "assistant",
          content: `对蓝方${label}态势分析\n\n${conclusion}`,
          conclusion,
          time: row.loadedAt || "08:41:10",
          source: row.source || "sample-showcase",
          kind: "briefing",
        },
      ],
    };
  }
  return out;
}

function snapshotCotLoop(loop) {
  const src = loop && typeof loop === "object" ? loop : PLAN_SELFPLAY_LOOP;
  try {
    return JSON.parse(JSON.stringify(src));
  } catch {
    return PLAN_SELFPLAY_LOOP;
  }
}

function emptyCotDim() {
  return { summary: "—", detail: "", thoughts: [] };
}

function splitCotThoughts(text) {
  return String(text || "")
    .split(/[\n；;]+|(?<=[。！？!?])/)
    .map((part) => part.trim())
    .filter(Boolean)
    .slice(0, 8);
}

function normalizeCotDim(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const summary = String(src.summary || src.title || src.text || "").trim() || "—";
  let thoughts = [];
  if (Array.isArray(src.thoughts)) {
    thoughts = src.thoughts.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 8);
  } else if (typeof src.thoughts === "string" && src.thoughts.trim()) {
    thoughts = splitCotThoughts(src.thoughts);
  }
  if (!thoughts.length) thoughts = splitCotThoughts(src.detail || src.reason || src.note || "");
  const detail = thoughts.join("；") || String(src.detail || "").trim();
  return { summary, detail, thoughts };
}

function formatGameClockFromSeconds(sec) {
  const n = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(n / 60);
  const s = n % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function normalizeCotLoop(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const turnsIn = Array.isArray(src.turns) ? src.turns : [];
  const byRole = Object.create(null);
  for (const turn of turnsIn) {
    if (!turn || typeof turn !== "object") continue;
    const role = String(turn.role || turn.key || "").toLowerCase();
    if (role === "ours" || role === "our" || role === "self") byRole.ours = turn;
    else if (role === "enemy" || role === "opp" || role === "opponent") byRole.enemy = turn;
    else if (role === "reflect" || role === "revise") byRole.reflect = turn;
  }
  const specs = [
    ["ours", COT_ROLE_AGENTS.ours, "当前窗 · 红方先手"],
    ["enemy", COT_ROLE_AGENTS.enemy, "蓝方下一手"],
    ["reflect", COT_ROLE_AGENTS.reflect, "对照张力 · 收敛"],
  ];
  const turns = specs.map(([role, title, subtitle]) => {
    const item = byRole[role] || {};
    const dimsIn = item.dims && typeof item.dims === "object" ? item.dims : {};
    return {
      key: item.key || role,
      role,
      title,
      subtitle: String(item.subtitle || subtitle),
      dims: {
        strike: normalizeCotDim(dimsIn.strike),
        detect: normalizeCotDim(dimsIn.detect),
        defense: normalizeCotDim(dimsIn.defense),
        support: normalizeCotDim(dimsIn.support),
      },
      conclusion: String(item.conclusion || "").trim(),
    };
  });
  const finalRaw = src.finalPlan && typeof src.finalPlan === "object" ? src.finalPlan : {};
  const finalDims = finalRaw.dims && typeof finalRaw.dims === "object" ? finalRaw.dims : {};
  return {
    title: String(src.title || "本轮推演"),
    horizon: String(src.horizon || "当前窗 + 下一窗"),
    thesis: String(src.thesis || ""),
    situationDigest: String(src.situationDigest || src.situation_digest || ""),
    turns,
    finalPlan: {
      title: COT_ROLE_AGENTS.final,
      subtitle: String(finalRaw.subtitle || ""),
      dims: {
        strike: String(finalDims.strike || ""),
        detect: String(finalDims.detect || ""),
        defense: String(finalDims.defense || ""),
        support: String(finalDims.support || ""),
      },
      steps: Array.isArray(finalRaw.steps) ? finalRaw.steps.map((step) => String(step || "")).filter(Boolean) : [],
      conclusion: String(finalRaw.conclusion || "").trim(),
    },
  };
}

function isMojibakePrompt(text) {
  const s = String(text || "");
  if (!s.trim()) return true;
  const q = (s.match(/\?/g) || []).length;
  const bad = (s.match(/\uFFFD/g) || []).length;
  return bad > 0 || (q >= 6 && q / s.length >= 0.35);
}

function parseGameClockToSeconds(text) {
  const hit = String(text || "").trim().match(/(\d+):(\d{2})/);
  if (!hit) return 0;
  return Number(hit[1]) * 60 + Number(hit[2]);
}

function usableGameClock(text) {
  const s = String(text || "").trim();
  if (!s || s === "--:--" || s === "样例窗") return "";
  return /(\d+):(\d{2})/.test(s) ? s : "";
}

function looksLikeJsonBlob(text) {
  const s = String(text || "").trim();
  return s.startsWith("{") || s.startsWith("[");
}

function pickOrientConclusion(payload) {
  const conclusion = String(payload?.conclusion || "").trim();
  if (conclusion && !looksLikeJsonBlob(conclusion)) return conclusion;
  const answer = String(payload?.answer || "").trim();
  if (answer && !looksLikeJsonBlob(answer)) return answer;
  const steps = Array.isArray(payload?.steps) ? payload.steps : [];
  return steps.map((step) => String(step?.text || "").trim()).filter(Boolean).join("\n");
}

function splitKillnetClauses(text, limit = 5) {
  return String(text || "")
    .split(/[，,；;。.!？?\n]/)
    .map((part) => part.trim())
    .filter((part) => part.length >= 2)
    .slice(0, limit);
}

const KILLNET_KIND_LABELS = {
  causal: "因果",
  window: "窗口",
  resource: "资源",
  space: "空间",
};

const KILLNET_DIM_HINTS = {
  detect: { cue: "开雾 / 定位 / 预警", question: "看见什么、何时更新" },
  strike: { cue: "开火 / 切入 / 收网", question: "打谁、打到哪停" },
  defense: { cue: "接敌 / 通道 / 警戒", question: "守住哪、让谁通过" },
  support: { cue: "产能 / 补给 / 扩建", question: "养什么、何时加码" },
  environment: { cue: "地形 / 窗口 / 约束", question: "环境如何约束四维" },
};

const KILLNET_ENVIRONMENT_NODE = {
  key: "environment",
  label: "作战环境",
  role: "约束四维行动",
  fromPlan: ENV_SELFPLAY_LOOP.finalPlan.conclusion,
  outputs: Array.isArray(ENV_SELFPLAY_LOOP.finalPlan.steps)
    ? ENV_SELFPLAY_LOOP.finalPlan.steps.slice(0, 4)
    : ["内容待定"],
};

function decorateKillnetBoard(nodes, edges, finalPlan, taskTurns) {
  const edgeList = Array.isArray(edges) ? edges : [];
  const dimLabel = (key) => ({
    strike: "打击",
    detect: "探测",
    defense: "防御",
    support: "保障",
    environment: "作战环境",
  }[key] || key);
  const tasks = [];
  for (const turn of taskTurns || []) {
    for (const task of turn.tasks || []) tasks.push(task);
  }
  const steps = Array.isArray(finalPlan?.steps)
    ? finalPlan.steps.map((step) => String(step || "").trim()).filter(Boolean)
    : [];
  return (Array.isArray(nodes) ? nodes : []).map((node) => {
    const text = String(node.fromPlan || "").trim();
    const clauses = splitKillnetClauses(text, 8);
    const rawOut = Array.isArray(node.outputs)
      ? node.outputs.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const outputs = (rawOut.length ? rawOut : clauses.slice(1)).slice(0, 6);
    const inbound = edgeList
      .filter((edge) => edge.to === node.key)
      .map((edge) => ({
        peer: edge.from,
        peerLabel: dimLabel(edge.from),
        kind: edge.kind,
        kindLabel: KILLNET_KIND_LABELS[edge.kind] || edge.kind,
        label: String(edge.label || "").trim(),
      }));
    const outbound = edgeList
      .filter((edge) => edge.from === node.key)
      .map((edge) => ({
        peer: edge.to,
        peerLabel: dimLabel(edge.to),
        kind: edge.kind,
        kindLabel: KILLNET_KIND_LABELS[edge.kind] || edge.kind,
        label: String(edge.label || "").trim(),
      }));
    const relatedTasks = tasks.filter((task) => task.type === node.key).slice(0, 3);
    const relatedSteps = steps.filter((step) => {
      const hay = step.toLowerCase();
      return hay.includes(String(node.label || "")) || hay.includes(String(node.key || ""));
    }).slice(0, 2);
    const hint = KILLNET_DIM_HINTS[node.key] || { cue: "", question: "" };
    return {
      ...node,
      cue: hint.cue,
      question: hint.question,
      summary: text || clauses.join("，") || `本轮尚未给出${node.label || ""}要点。`,
      details: clauses.slice(0, 6),
      inText: inbound.map((row) => `${row.peerLabel}·${row.label || row.kindLabel}`).filter(Boolean).join("；"),
      outText: outbound.map((row) => `${row.peerLabel}·${row.label || row.kindLabel}`).filter(Boolean).join("；"),
      outputs,
      inbound,
      outbound,
      relatedTasks,
      relatedSteps,
    };
  });
}

function killnetFromFinalPlan(finalPlan, gameTime) {
  const plan = finalPlan && typeof finalPlan === "object" ? finalPlan : {};
  const dims = plan.dims && typeof plan.dims === "object" ? plan.dims : {};
  const meta = {
    detect: { label: "探测", role: "提供目标信息" },
    strike: { label: "打击", role: "形成任务效果" },
    defense: { label: "防御", role: "保护行动窗口" },
    support: { label: "保障", role: "维持资源与持续性" },
  };
  const conclusion = String(plan.conclusion || "").trim();
  const planSteps = Array.isArray(plan.steps)
    ? plan.steps.map((step) => String(step || "").trim()).filter(Boolean)
    : [];
  const edgeNotes = {
    detect: { in: "承接保障续航", out: "向打击送开火条件，向防御送预警方向" },
    strike: { in: "承接探测门槛与防御接敌窗", out: "向防御交付收网窗，向保障回报损失" },
    defense: { in: "承接探测预警", out: "向打击护窗口，向保障守矿线" },
    support: { in: "承接防御矿线安全", out: "向探测续航，向打击补编" },
  };
  const nodes = Object.keys(meta).map((key) => {
    const text = String(dims[key] || "").trim();
    const related = planSteps.filter((step) => step.includes(meta[key].label) || step.toLowerCase().includes(key)).slice(0, 2);
    const note = edgeNotes[key] || { in: "", out: "" };
    const parts = [
      text || `本轮红方方案规划尚未写明${meta[key].label}动作，先按「${meta[key].role}」占位。`,
      related.length ? `方案步骤：${related.join("；")}` : "",
      note.in,
      note.out,
      conclusion && !text.includes(conclusion) ? `服从总轴：${conclusion}` : "",
    ].filter(Boolean);
    const fromPlan = parts.join(" ");
    const clauses = splitKillnetClauses(fromPlan, 8);
    return {
      key,
      label: meta[key].label,
      role: meta[key].role,
      fromPlan,
      outputs: clauses.slice(0, 5),
    };
  });
  nodes.push({ ...KILLNET_ENVIRONMENT_NODE });
  return {
    thesis: String(plan.conclusion || "").trim()
      || `${gameTime || "--:--"} 红方方案规划已按打/探/防/保落网：探测开门、打击吃窗口、防御护收网、保障供产能，作战环境居中约束。`,
    nodes,
    edges: (PLAN_KILLNET.edges || []).map((edge) => ({ ...edge })),
  };
}

/** 样例时序打击思维链：每个时间 tab 独立正文，目标随 title/thesis 变化 */
const DEMO_COT_LOOP_BY_STAMP = {
  "cot-demo-1": {
    situationDigest: "决策方案的目标：00:00 开局，双矿刚起，信息为零，禁止贸然开火。",
    ours: {
      strike: { summary: "开局不开火，只占 B2 高地口牵制位，等待开雾回报。", thoughts: ["第一步：开局目标是信息先手，不设侧翼开火档。", "第二步：毒爆与蟑螂只做集结，不越过峡谷口接敌线。", "第三步：开火门槛整轮关闭，任何前排露头都不打。", "第四步：把「南北口开雾完成」写成下一拍才能解除牵制。", "第五步：若蓝方先压线，只做宽度压制，绝不追残。"] },
      detect: { summary: "双王虫优先钉南北口，东翼先挂一只轻侦防开局盲区。", thoughts: ["第一步：00:00 起双王虫分赴峡谷南北口，先拿对方出生朝向。", "第二步：东翼补一只轻侦，防止开局绕行漏看。", "第三步：本轮只输出「队形是否出现」「补给是否抬升」字段，不触发打击。", "第四步：王虫禁止进交火面，开局损失不可接受。", "第五步：任一口开雾滞后，立刻改挂优先级到滞后侧。"] },
      defense: { summary: "主矿最低警戒先立，高地骨架后补，不抽空家门口。", thoughts: ["第一步：开局家门口留最低工蜂/脊刺警戒，防小狗偷袭。", "第二步：B2 刺蛇骨架随产兵进度逐步补，不抢先抽空。", "第三步：回撤通道先画虚线，本轮不开接敌窗。", "第四步：矿线护航优先于前线任何对射。", "第五步：信息未齐前不做防线前推。"] },
      support: { summary: "经济先保双矿与工蜂曲线，产兵服务开雾与占位，不开三矿。", thoughts: ["第一步：瓦斯优先刺蛇穴与王虫，不开无条件三矿。", "第二步：毒爆只按侦察掩护规模排产，不堆交战编制。", "第三步：工蜂曲线必须连续，开局掉工不可逆。", "第四步：产能全部服务信息先手，不为侧翼短打预留过量待命。", "第五步：三矿评估整轮关闭。"] },
      conclusion: "红方 00:00 方案：信息先手——开雾与占位，不开火、不开三矿。",
    },
    enemy: {
      strike: { summary: "开局更可能用轻量试探压线，尚未形成北口硬推。", thoughts: ["第一步：蓝方开局优先探红方出生，而非立刻攻坚。", "第二步：少量单位试探峡谷口宽度。", "第三步：未见红方开雾前，不敢把主队送进俯射面。", "第四步：若红方高地空，可能提前抢口；否则改侧翼轻探。", "第五步：开局经济打击概率低，主目标仍是信息对称。"] },
      detect: { summary: "小狗绕行确认分矿与墙体，修正正面意图。", thoughts: ["第一步：侧翼小狗绕行证伪快扩。", "第二步：用回报决定是否正面加压。", "第三步：侦察任务限时，避免长期掉线。", "第四步：开局更关注红方二矿时点。", "第五步：若红方双王虫先亮南北口，蓝方会加快己方扫描节奏。"] },
      defense: { summary: "家门口薄线，前线几乎未成型，缓冲依赖出生点。", thoughts: ["第一步：开局防线未成型，家门口只留薄线。", "第二步：不会在 00:00 前推墙体到峡谷口。", "第三步：接受信息落后换出生安全。", "第四步：正面脆是开局常态。", "第五步：侧翼活尚未建立。"] },
      support: { summary: "双矿吸血为主，突击群尚未成型。", thoughts: ["第一步：补给优先工蜂与基础编制。", "第二步：突击比重尚未抬到交战档。", "第三步：三矿更晚于红方评估。", "第四步：开局资源不散到渗透编制。", "第五步：失败剧本尚未触发。"] },
      conclusion: "蓝方 00:00 脚本：轻探对称信息，尚未进入北口硬压。",
    },
    reflect: {
      strike: { summary: "开局若抢开火会亏信息窗，必须钉死「先雾后打」。", thoughts: ["第一步：对照——蓝方只是试探，我方抢打会暴露编制。", "第二步：纠正：开火档整轮关闭。", "第三步：任务改成「占位 + 开雾」，不是清前排。", "第四步：任何追击欲在 00:00 一律否决。", "第五步：下一拍才允许讨论侧翼待命升级。"] },
      detect: { summary: "信息先手是本轮唯一硬指标，东翼不能留盲。", thoughts: ["第一步：南北口任一侧滞后即算失败。", "第二步：东翼轻侦与峡谷口同权。", "第三步：开雾完成前不向打击交任何开火条件。", "第四步：王虫损失会直接毁掉先手优势。", "第五步：回报字段先写「有无队形」，再写「能否打」。"] },
      defense: { summary: "开局真空比前线更致命，家门口不能抽空。", thoughts: ["第一步：纠正抽前线欲望，家门口最低警戒先立。", "第二步：接敌窗本轮不开。", "第三步：高地骨架可缓，矿线不可缓。", "第四步：信息未齐不做前推。", "第五步：回撤通道只预标，不实战启用。"] },
      support: { summary: "开局产能服务侦察，三矿与交战编制都暂缓。", thoughts: ["第一步：纠正堆毒爆冲动。", "第二步：工蜂曲线高于一切待命规模。", "第三步：三矿整轮否决。", "第四步：瓦斯给王虫与刺蛇穴。", "第五步：损失阈值尚未启用，先防开局掉工。"] },
      conclusion: "拧正：00:00 只做信息先手，开火与三矿全部后置。",
    },
    finalPlan: {
      dims: {
        strike: "开局不开火，只占高地牵制位；任何前排露头都不打。",
        detect: "南北口与东翼先亮，开雾完成前不向打击交开火条件。",
        defense: "家门口最低警戒先立，接敌窗关闭，矿线优先。",
        support: "产能服务开雾与占位；三矿与交战编制整轮暂缓。",
      },
      steps: [
        "第一步：双王虫钉南北口，东翼挂轻侦，完成开局开雾。",
        "第二步：B2 高地只占位牵制，不设侧翼开火档。",
        "第三步：家门口留最低警戒，禁止抽空换前线。",
        "第四步：工蜂曲线与刺蛇穴优先，不开三矿。",
        "第五步：总约束——信息不齐不开火，开火议题留到下一拍。",
      ],
      conclusion: "00:00 最终方案：信息先手闭环——开雾 → 占位 → 护矿，不开火。",
    },
  },
  "cot-demo-2": {
    situationDigest: "决策方案的目标：00:40 双通道刚开雾，高地口牵制成型，毒爆可待命升级。",
    ours: {
      strike: { summary: "高地口牵制为主，毒爆升为可开火待命，仍禁止深推。", thoughts: ["第一步：确认南北口开雾已回报队形轮廓。", "第二步：毒爆从等待升为可立即切入待命档。", "第三步：开火门槛写成「队形过线 + 补给抬升」，本拍可观察未强制打。", "第四步：正面只做宽度压制，侧翼待命不抢先开火。", "第五步：门槛未成立则继续高地牵制，不浪费第一波。"] },
      detect: { summary: "双王虫交错刷新南北口，东翼与峡谷口同权。", thoughts: ["第一步：双通道交错开雾，跟踪蟑螂比重变化。", "第二步：东翼走廊补第二只轻侦防滞后。", "第三步：把队形宽度与东翼渗透写成门槛字段。", "第四步：王虫不进交火面，只外围刷新。", "第五步：任一口雾滞，立即改挂。"] },
      defense: { summary: "B2 刺蛇骨架就位，接敌窗仍收窄，只护牵制段。", thoughts: ["第一步：高地骨架到位，吸收试探流量。", "第二步：接敌窗只覆盖可能的侧翼切入段，默认关闭。", "第三步：回撤折线可视预演。", "第四步：家门口警戒不抽。", "第五步：矿线护航与高地同权。"] },
      support: { summary: "产能服务待命毒爆与刺蛇穴，三矿仍条件关闭。", thoughts: ["第一步：瓦斯优先刺蛇穴完成度。", "第二步：毒爆按待命规模上调一档。", "第三步：工蜂曲线保住。", "第四步：不为立刻交战堆编制。", "第五步：三矿仍否决，直到东翼安全。"] },
      conclusion: "红方 00:40 方案：双通道开雾 + 高地牵制 + 毒爆待命，仍不深推。",
    },
    enemy: {
      strike: { summary: "开雾后更可能沿北口试压，测红方是否敢接。", thoughts: ["第一步：用小股流量测高地口硬度。", "第二步：见毒爆待命则改风筝，不硬吃第一波。", "第三步：若红方只牵制不开火，蓝方会加长压线时间。", "第四步：尚未必然转西翼经济打。", "第五步：目标是逼红方提前开火或抽空家门口。"] },
      detect: { summary: "侧翼小狗确认二矿与回防厚度，修正正面意图。", thoughts: ["第一步：绕行确认红方二矿时点。", "第二步：用侧翼回报决定是否加北口流量。", "第三步：限时侦察，避免掉线。", "第四步：关注红方东翼是否先亮。", "第五步：若双通道常亮，蓝方渗透窗口变窄。"] },
      defense: { summary: "家门口仍薄，前线开始留避爆折线。", thoughts: ["第一步：薄回防换前线试压流量。", "第二步：行军预留避爆折线。", "第三步：未准备攻坚高地。", "第四步：北口可封不攻坚。", "第五步：侧翼活仍在铺垫。"] },
      support: { summary: "补给开始向突击群倾斜，维持蟑螂比重。", thoughts: ["第一步：补给增量给突击群。", "第二步：侦察限时抽调。", "第三步：双矿吸血继续。", "第四步：三矿仍早。", "第五步：渗透编制尚未全开。"] },
      conclusion: "蓝方 00:40 脚本：北口试压测硬度，侧翼回报修正意图。",
    },
    reflect: {
      strike: { summary: "待命可升，但牵制拍不能提前清前排。", thoughts: ["第一步：对照——蓝方在测硬度，我方抢打会亏窗。", "第二步：纠正：可待命，不可强制开火。", "第三步：深推与追残仍禁止。", "第四步：牵制续压优先于第一波伤害。", "第五步：二次脉冲议题后置。"] },
      detect: { summary: "双通道必须常亮，东翼滞后会毁掉牵制判断。", thoughts: ["第一步：东翼与峡谷口同权不可退让。", "第二步：交错刷新节奏钉死。", "第三步：门槛字段只观察，不催打。", "第四步：王虫损失不可接受。", "第五步：雾滞立即改挂。"] },
      defense: { summary: "骨架要厚，但不能为牵制抽空家门口。", thoughts: ["第一步：高地阻挡加厚。", "第二步：接敌窗默认关。", "第三步：家门口警戒保留。", "第四步：回撤通道只预演。", "第五步：矿线护航不降权。"] },
      support: { summary: "待命上调一档，三矿仍关，防情绪扩编。", thoughts: ["第一步：毒爆待命可上调。", "第二步：不按交战规模堆。", "第三步：工蜂优先。", "第四步：三矿否决。", "第五步：侦察续航与补损同权。"] },
      conclusion: "拧正：00:40 牵制拍——待命升级、开火后置、双通道常亮。",
    },
    finalPlan: {
      dims: {
        strike: "高地牵制为主，毒爆可待命；门槛未齐不开火，禁止深推。",
        detect: "双通道交错开雾常亮，东翼与峡谷口同权。",
        defense: "刺蛇骨架就位，接敌窗默认关，家门口不抽空。",
        support: "待命产能上调一档；三矿仍关闭。",
      },
      steps: [
        "第一步：维持南北口与东翼交错开雾。",
        "第二步：B2 高地牵制占位，毒爆升待命档。",
        "第三步：观察队形宽度与补给抬升，不强制开火。",
        "第四步：家门口警戒与矿线护航保持。",
        "第五步：总约束——牵制先于打击，打击先于追击。",
      ],
      conclusion: "00:40 最终方案：双通道开雾 + 高地牵制 + 待命可打，仍不深推。",
    },
  },
  "cot-demo-3": {
    situationDigest: "决策方案的目标：01:20 北口蟑螂加压，侧翼可打可收，门槛未成立则继续牵制。",
    ours: {
      strike: { summary: "北口加压时只侧翼打前排，可打可收，禁止深推追残。", thoughts: ["第一步：确认北口加压与队形是否一字过线。", "第二步：门槛成立则侧翼切入打前排。", "第三步：打完立刻复位，不跟开阔走廊。", "第四步：门槛未成立则高地牵制续压。", "第五步：二次脉冲本拍不做，先看收网质量。"] },
      detect: { summary: "盯北口队形与补给节奏，东翼防渗透撕口。", thoughts: ["第一步：北口王虫加密刷新队形宽度。", "第二步：东翼保持同权，防加压掩护下的绕行。", "第三步：开火条件字段实时更新。", "第四步：交火一开始就准备复探绕后。", "第五步：渗透告警则打击降级牵制。"] },
      defense: { summary: "接敌窗只开侧翼开火段，窗关即复位并护矿。", thoughts: ["第一步：接敌窗覆盖侧翼开火那一段。", "第二步：窗关立刻复位，不跟打击冲。", "第三步：高地阻挡吸收北口流量。", "第四步：家门口回填防真空。", "第五步：矿线护航升级。"] },
      support: { summary: "补损与待命并行，损失过线停扩，三矿仍条件化。", thoughts: ["第一步：补损队列与侦察续航同权。", "第二步：毒爆按损失补，不按交战规模扩。", "第三步：损失过线停扩转补。", "第四步：工蜂曲线保住。", "第五步：三矿仅东翼安全时评估。"] },
      conclusion: "红方 01:20 方案：可打可收——门槛成立侧翼清前排，否则牵制。",
    },
    enemy: {
      strike: { summary: "北口持续正面施压，追求瞬时伤害差。", thoughts: ["第一步：沿北坡维持正面压力。", "第二步：见毒爆则风筝拆网。", "第三步：前排被拆后可能转侧翼。", "第四步：流量不轻易从前线撤。", "第五步：逼红方过早接或抽空家门口。"] },
      detect: { summary: "侧翼回报修正正面意图，确认红方是否敢收网。", thoughts: ["第一步：小狗确认回防厚度。", "第二步：用回报决定加不加流量。", "第三步：交火后看红方是否复位。", "第四步：东翼渗透窗口仍在找。", "第五步：红方双通道常亮会压缩其改线。"] },
      defense: { summary: "薄回防换前线流量，正面改折线避爆。", thoughts: ["第一步：家门口薄线换流量。", "第二步：避爆折线预留。", "第三步：前排受损后收到南坡阴影。", "第四步：北口封而不攻坚。", "第五步：侧翼活对冲正面脆。"] },
      support: { summary: "补给优先突击群，维持蟑螂比重。", thoughts: ["第一步：补给给突击群。", "第二步：侦察限时。", "第三步：失败则回头补工。", "第四步：双矿继续吸血。", "第五步：渗透编制待命。"] },
      conclusion: "蓝方 01:20 脚本：北口硬压测收网，前排掉了再改线。",
    },
    reflect: {
      strike: { summary: "可打但不能恋战；追残会走进绕后脚本。", thoughts: ["第一步：对照——持续压线逼我恋战。", "第二步：纠正：清前排即收。", "第三步：禁止深推。", "第四步：仍在俯射面内才允许切入。", "第五步：二次脉冲后置到复探后。"] },
      detect: { summary: "加压时东翼更易被偷开，必须先亮。", thoughts: ["第一步：东翼同权不可松。", "第二步：北口加密不能牺牲东翼。", "第三步：复探绕后先于二次开火。", "第四步：渗透告警降级打击。", "第五步：王虫不进交火面。"] },
      defense: { summary: "窗开就要能关，复位与回填绑定。", thoughts: ["第一步：接敌窗与复位折线绑定。", "第二步：打完回填家门口。", "第三步：高地阻挡加厚。", "第四步：矿线护航升级。", "第五步：薄回防不可接受。"] },
      support: { summary: "交战拍产能跟损失走，三矿仍不情绪开。", thoughts: ["第一步：补损优先。", "第二步：过线停扩。", "第三步：待命不断档。", "第四步：三矿条件化。", "第五步：侦察续航同权。"] },
      conclusion: "拧正：01:20 可打可收——打前排、即复位、东翼先亮。",
    },
    finalPlan: {
      dims: {
        strike: "门槛成立则侧翼打前排；打完即收；未成立则牵制。",
        detect: "北口加密 + 东翼常亮；交火后复探绕后。",
        defense: "接敌窗只覆盖开火段，复位回填，矿线升级护航。",
        support: "补损与待命并行；损失过线停扩；三矿条件化。",
      },
      steps: [
        "第一步：确认北口队形宽度与东翼无渗透。",
        "第二步：门槛成立则侧翼清前排，否则高地牵制。",
        "第三步：打完立刻复位，不开二次脉冲。",
        "第四步：复探绕后并回填家门口。",
        "第五步：总约束——可打可收，收优于打。",
      ],
      conclusion: "01:20 最终方案：可打可收闭环——门槛打 → 即收 → 复探。",
    },
  },
  "cot-demo-4": {
    situationDigest: "决策方案的目标：02:00 西翼有改线迹象，复探优先于二次脉冲，打完即收。",
    ours: {
      strike: { summary: "西翼改线阶段先收网，二次脉冲必须等复探，不追残。", thoughts: ["第一步：上一拍若已打过前排，本拍默认收网。", "第二步：西翼改线时禁止跟出开阔走廊。", "第三步：二次脉冲门槛改为「复探完成 + 补给再抬升」。", "第四步：未复探前任何追击欲否决。", "第五步：高地牵制续压，等待东/西翼校正。"] },
      detect: { summary: "优先复探西翼与绕后，校正改线后再谈开火。", thoughts: ["第一步：交火或加压结束后立刻复探绕后。", "第二步：西翼走廊与东翼同优先级更新。", "第三步：确认是否渗透与补给抬升。", "第四步：王虫不进交火面。", "第五步：复探未完成不向打击交二次开火条件。"] },
      defense: { summary: "复位折线钉死，防西翼渗透撕家门口。", thoughts: ["第一步：全员回到复位点。", "第二步：家门口回填最低骨架。", "第三步：设防朝向兼顾西翼预警。", "第四步：接敌窗保持关闭直至复探完成。", "第五步：矿线护航高于前线对射。"] },
      support: { summary: "按损失补编，停无效扩编，三矿仍看东翼与矿线。", thoughts: ["第一步：产能跟损失走。", "第二步：不为追击堆毒爆。", "第三步：侦察续航优先保障复探。", "第四步：工蜂曲线保住。", "第五步：三矿条件仍要东翼安全。"] },
      conclusion: "红方 02:00 方案：西翼改线期——复探优先，二次脉冲后置，打完即收。",
    },
    enemy: {
      strike: { summary: "前排受损后更可能转西翼打经济，避开俯射面。", thoughts: ["第一步：正面压力转渗透。", "第二步：西翼走廊出口成为主压。", "第三步：不跟红方钉口硬刚。", "第四步：诱红方脱高地到开阔面。", "第五步：得手则直压矿线。"] },
      detect: { summary: "加快西翼开雾与绕后确认，压红方复探节奏。", thoughts: ["第一步：西翼轻侦加密。", "第二步：确认红方是否抽空家门口。", "第三步：用回报驱动改线。", "第四步：正面观察降权。", "第五步：红方复探慢则加大渗透。"] },
      defense: { summary: "正面改防守，侧翼活换空间。", thoughts: ["第一步：北口封而不攻坚。", "第二步：接受正面脆。", "第三步：侧翼得手对冲。", "第四步：家门口仍薄。", "第五步：缓冲短是固有弱点。"] },
      support: { summary: "资源从硬推改到渗透编制与经济线。", thoughts: ["第一步：突击失败则补工与三矿。", "第二步：渗透编制抬升。", "第三步：补给不再堆正面。", "第四步：侦察服务改线。", "第五步：经济翻盘优先。"] },
      conclusion: "蓝方 02:00 脚本：西翼改线打经济，诱红方脱高地。",
    },
    reflect: {
      strike: { summary: "追残正中改线脚本；二次脉冲必须复探后。", thoughts: ["第一步：对照——追击=脱高地。", "第二步：纠正：收网优先。", "第三步：二次脉冲绑定复探。", "第四步：禁止开阔面接敌。", "第五步：牵制续压等校正。"] },
      detect: { summary: "复探是本拍主任务，慢一拍就会被撕口。", thoughts: ["第一步：复探绕后绝对优先。", "第二步：西翼与东翼同刷。", "第三步：未完成不交开火条件。", "第四步：渗透告警降级一切打击。", "第五步：王虫保全。"] },
      defense: { summary: "复位与回填是防经济打击的前提。", thoughts: ["第一步：复位点全员到达。", "第二步：家门口回填。", "第三步：朝向兼顾西翼。", "第四步：窗保持关。", "第五步：矿线护航最高权。"] },
      support: { summary: "不为追击扩编；复探续航优先。", thoughts: ["第一步：停无效扩。", "第二步：补损跟损失。", "第三步：侦察续航给复探。", "第四步：三矿仍条件化。", "第五步：工蜂保住。"] },
      conclusion: "拧正：02:00 复探优先——收网、校正、再谈二次脉冲。",
    },
    finalPlan: {
      dims: {
        strike: "默认收网；二次脉冲仅复探完成后；禁止追残与开阔面接敌。",
        detect: "西翼与绕后复探绝对优先，未完成不交开火条件。",
        defense: "全员复位回填，朝向兼顾西翼，矿线最高护航。",
        support: "按损失补编，复探续航优先，三矿仍条件化。",
      },
      steps: [
        "第一步：全员收网复位，不开追击。",
        "第二步：复探西翼与绕后，确认渗透与补给。",
        "第三步：家门口回填，矿线护航升级。",
        "第四步：仅复探完成且门槛再成立才允许二次脉冲。",
        "第五步：总约束——改线期复探大于开火。",
      ],
      conclusion: "02:00 最终方案：复探优先闭环——收网 → 校正 → 条件再打。",
    },
  },
  // cot-demo-5：沿用 PLAN_SELFPLAY_LOOP 全文（收网复位 · 三矿条件化）
};

function buildDemoCotLoopForStamp(stamp) {
  const base = PLAN_SELFPLAY_LOOP;
  const variant = DEMO_COT_LOOP_BY_STAMP[stamp.id];
  const loop = {
    ...base,
    title: stamp.title,
    thesis: stamp.thesis,
    horizon: "本轮多步推演",
    situationDigest: (variant && variant.situationDigest) || base.situationDigest,
  };
  if (!variant || !variant.ours) {
    return normalizeCotLoop(loop);
  }
  const patchRole = (turn, patch) => {
    if (!patch) return { ...turn };
    const dims = { ...(turn.dims || {}) };
    for (const key of ["strike", "detect", "defense", "support"]) {
      if (patch[key]) dims[key] = { ...(dims[key] || {}), ...patch[key] };
    }
    return {
      ...turn,
      dims,
      conclusion: patch.conclusion || turn.conclusion,
    };
  };
  loop.turns = (base.turns || []).map((turn) => {
    const role = String(turn.role || turn.key || "");
    if (role === "ours") return patchRole(turn, variant.ours);
    if (role === "enemy") return patchRole(turn, variant.enemy);
    if (role === "reflect") return patchRole(turn, variant.reflect);
    return { ...turn };
  });
  if (variant.finalPlan) {
    loop.finalPlan = {
      ...(base.finalPlan || {}),
      ...variant.finalPlan,
      dims: { ...(base.finalPlan?.dims || {}), ...(variant.finalPlan.dims || {}) },
      steps: variant.finalPlan.steps || base.finalPlan?.steps || [],
      conclusion: variant.finalPlan.conclusion || base.finalPlan?.conclusion || "",
    };
  }
  return normalizeCotLoop(loop);
}

function makeDemoCotRound() {
  return {
    id: "cot-demo-5",
    gameTime: "02:40",
    gameSeconds: 160,
    generatedAt: "02:40:18",
    source: "demo",
    loading: false,
    error: "",
    loop: buildDemoCotLoopForStamp({
      id: "cot-demo-5",
      title: "02:40 收网复位 · 三矿条件化",
      thesis: "收网复位并回填家门口，三矿仅东翼安全时开启。",
    }),
    killnet: PLAN_KILLNET,
    killnetLoading: false,
    killnetError: "",
  };
}

function makeDemoCotHistory() {
  const stamps = [
    { id: "cot-demo-1", sec: 0, title: "00:00 开局 · 信息先手", thesis: "从 0 时刻起先拿南北口开雾与高地占位，不开火。" },
    { id: "cot-demo-2", sec: 40, title: "00:40 双通道开雾 · 高地牵制", thesis: "双王虫交错开雾，B2 高地口牵制，毒爆可开火待命。" },
    { id: "cot-demo-3", sec: 80, title: "01:20 北口加压预警 · 可打可收", thesis: "北口蟑螂加压时只侧翼打前排，门槛未成立则继续牵制。" },
    { id: "cot-demo-4", sec: 120, title: "02:00 西翼改线 · 复探优先", thesis: "西翼改线先复探再决定二次脉冲，打完即收不追残。" },
    { id: "cot-demo-5", sec: 160, title: "02:40 收网复位 · 三矿条件化", thesis: "收网复位并回填家门口，三矿仅东翼安全时开启。" },
  ];
  return stamps.map((row) => {
    const clock = formatGameClockFromSeconds(row.sec);
    const item = makeDemoCotRound();
    item.id = row.id;
    item.gameTime = clock;
    item.gameSeconds = row.sec;
    item.generatedAt = `${clock}:${String(10 + (row.sec % 40)).padStart(2, "0")}`;
    item.loop = buildDemoCotLoopForStamp(row);
    return item;
  });
}

/** 杀伤网（写死演示）：把最终行动方案按打/探/防/保连成网 */
const PLAN_KILLNET = {
  thesis: "本轮先由探测把峡谷南北口与东翼同时点亮，打击只在队形宽度和补给门槛成立时侧翼短打；防御把接敌窗和复位折线钉住，保障用瓦斯与补损把侦察和二次脉冲吊住。作战环境居中约束四维。边既有开火因果，也有窗口、资源和矿线空间依赖，收网后必须复探再决定下一拍。",
  nodes: [
    {
      key: "detect",
      label: "探测",
      role: "提供目标信息",
      fromPlan: "峡谷南北口与东翼双通道保持交错开雾，王虫不进交火面。交战后优先复探绕后和补给抬升，把「队形是否一字过线、东翼有无渗透」写成打击门槛。本维承接保障的侦察不掉线，向打击送开火条件，向防御送预警方向。",
      outputs: ["南北口队形", "东翼渗透告警", "补给节奏门槛", "交战后复探", "绕后校正"],
    },
    {
      key: "strike",
      label: "打击",
      role: "形成任务效果",
      fromPlan: "侧翼只打露出的前排，禁止深推和追残，打完立即回到复位点。开火必须同时拿到探测的队形宽度和防御的接敌窗，否则改高地口牵制。本维向防御交付「需要护航的收网窗」，向保障回报损失速率，不把王虫送进交火面。",
      outputs: ["前排拆解", "短暂空间差", "收网复位点", "禁止深追", "二次脉冲门槛"],
    },
    {
      key: "defense",
      label: "防御",
      role: "保护行动窗口",
      fromPlan: "高地骨架不撤，回撤通道全程可视，家门口留最低警戒。接敌窗只覆盖侧翼开火那一段，窗一关就复位，不跟打击一起冲。承接探测预警改设防朝向，守住矿线后才允许保障加码经济。",
      outputs: ["接敌窗口", "复位通道", "高地骨架", "家门口警戒", "矿线护航"],
    },
    {
      key: "support",
      label: "保障",
      role: "维持资源与持续性",
      fromPlan: "产能先服务待命和补损，瓦斯优先刺蛇穴完成度，工蜂曲线不因前线抽空。三矿只在东翼无渗透且矿线仍在防御覆盖时开启。本维给探测续航、给打击补编，损失速率一旦过线就停扩转补，不按立刻交战规模堆毒爆。",
      outputs: ["毒爆待命产能", "补损队列", "条件三矿", "刺蛇穴优先", "侦察续航"],
    },
    { ...KILLNET_ENVIRONMENT_NODE },
  ],
  edges: [
    { from: "detect", to: "strike", kind: "causal", label: "目标信息 → 开火条件" },
    { from: "strike", to: "defense", kind: "causal", label: "任务效果 → 需要窗口" },
    { from: "defense", to: "strike", kind: "window", label: "保护接敌与收网窗" },
    { from: "support", to: "strike", kind: "resource", label: "产能维持打击可持续" },
    { from: "support", to: "detect", kind: "resource", label: "保障侦察不掉线" },
    { from: "detect", to: "defense", kind: "space", label: "预警驱动设防" },
    { from: "defense", to: "support", kind: "space", label: "守住矿线才有经济" },
  ],
  patternLabel: "侦察开门 · 侧翼短打收网",
  sequence: [
    { dim: "support", order: 1, phase: "T0", title: "产能卡到门槛", action: "瓦斯与补损先服务待命规模，刺蛇穴优先。", gate: "刺蛇穴/补损队列到位" },
    { dim: "detect", order: 2, phase: "T0", title: "双通道交错开雾", action: "南北口与东翼同时更新，跟踪补给抬升。", sync: "与保障并行" },
    { dim: "defense", order: 3, phase: "T1", title: "骨架就位只开接敌窗", action: "B2 高地骨架不撤，回撤通道可视。", gate: "探测回报队形宽度" },
    { dim: "environment", order: 4, phase: "T1", title: "贴南口高地约束交战面", action: "稳塔优先于开岩，不跟到开阔走廊。" },
    { dim: "strike", order: 5, phase: "T2", title: "侧翼短打前排", action: "可开火但不深推，打完立刻收网。", gate: "东翼无渗透告警" },
    { dim: "detect", order: 6, phase: "T3", title: "交战后复探绕后", action: "优先复探绕后与补给节奏，再决定二次开火。", gate: "打击已收网" },
    { dim: "support", order: 7, phase: "T3", title: "按损失补毒爆", action: "产能跟损失走，不按交战规模扩。", sync: "与复探并行" },
    { dim: "strike", order: 8, phase: "T5", title: "门槛满足才二次脉冲", action: "仅当补给抬升或队形露出空档再打。", gate: "探测门槛成立，否则停手" },
  ],
};

const KILLNET_DIM_LABELS = {
  detect: "探测",
  strike: "打击",
  defense: "防御",
  support: "保障",
  environment: "作战环境",
};

function killnetHashSeed(text) {
  let hash = 2166136261;
  const src = String(text || "");
  for (let i = 0; i < src.length; i += 1) {
    hash ^= src.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function killnetClip(text, max) {
  const src = String(text || "").trim().replace(/\s+/g, " ");
  if (!src) return "";
  if (src.length <= max) return src;
  return `${src.slice(0, max).replace(/[，。；、]+$/, "")}…`;
}

function killnetNodeText(killnet, key, finalPlan) {
  const node = (Array.isArray(killnet?.nodes) ? killnet.nodes : []).find((row) => row.key === key);
  const fromNode = String(node?.fromPlan || node?.summary || "").trim();
  const fromPlan = String(finalPlan?.dims?.[key] || "").trim();
  return fromNode || fromPlan;
}

function killnetLeadDim(killnet, finalPlan) {
  const keys = ["detect", "strike", "defense", "support"];
  let best = "detect";
  let bestLen = -1;
  for (const key of keys) {
    const len = killnetNodeText(killnet, key, finalPlan).length;
    if (len > bestLen) {
      best = key;
      bestLen = len;
    }
  }
  return best;
}

function normalizeKillnetSequence(raw) {
  if (!Array.isArray(raw) || raw.length < 4) return null;
  const steps = [];
  raw.forEach((item, idx) => {
    if (!item || typeof item !== "object") return;
    const dim = String(item.dim || item.key || "").trim().toLowerCase();
    if (!KILLNET_DIM_LABELS[dim]) return;
    steps.push({
      id: String(item.id || `llm-${idx + 1}`),
      dim,
      dimLabel: KILLNET_DIM_LABELS[dim],
      orderLabel: String(item.order || idx + 1).padStart(2, "0"),
      phase: String(item.phase || `T${idx}`).trim() || `T${idx}`,
      title: String(item.title || item.label || `${KILLNET_DIM_LABELS[dim]}动作`).trim(),
      action: String(item.action || item.text || item.fromPlan || "").trim(),
      gate: String(item.gate || item.condition || "").trim(),
      sync: String(item.sync || item.parallel || "").trim(),
      also: Array.isArray(item.also) ? item.also.map((key) => String(key || "").toLowerCase()) : [],
    });
  });
  return steps.length >= 4 ? steps : null;
}

function composeKillnetSteps(spec, ctx) {
  return spec.map((row, idx) => {
    const dim = row.dim;
    const fallback = row.fallback || `${KILLNET_DIM_LABELS[dim]}按本轮方案执行`;
    const live = killnetClip(killnetNodeText(ctx.killnet, dim, ctx.finalPlan), 140);
    return {
      id: `${ctx.patternId}-${idx + 1}`,
      dim,
      dimLabel: KILLNET_DIM_LABELS[dim],
      orderLabel: String(idx + 1).padStart(2, "0"),
      phase: row.phase,
      title: row.title,
      action: live || fallback,
      gate: row.gate || "",
      sync: row.sync || "",
      also: row.also || [],
    };
  });
}

function buildKillnetSequence(killnet, finalPlan, gameTime) {
  const llmSteps = normalizeKillnetSequence(killnet?.sequence);
  if (llmSteps) {
    return {
      source: "llm",
      sourceLabel: "大模型编排",
      patternId: "llm",
      patternLabel: String(killnet?.patternLabel || "大模型编排"),
      steps: llmSteps,
    };
  }
  const thesis = String(killnet?.thesis || finalPlan?.conclusion || "");
  const clock = String(gameTime || "");
  const lead = killnetLeadDim(killnet, finalPlan);
  const seed = killnetHashSeed(`${thesis}|${clock}|${lead}|${killnetNodeText(killnet, "strike", finalPlan)}`);
  const ctx = { killnet, finalPlan, thesis, clock, lead, seed };
  const packs = [
    {
      id: "recon-gate",
      label: "侦察开门",
      spec: [
        { dim: "support", phase: "T0", title: "先把产能卡到门槛", fallback: "瓦斯与补损先服务待命规模，不提前扩编。", gate: "刺蛇穴/补损队列到位" },
        { dim: "detect", phase: "T0", title: "双通道交错开雾", fallback: "南北口与东翼同时更新，跟踪补给抬升。", sync: "与保障并行", also: ["support"] },
        { dim: "defense", phase: "T1", title: "骨架就位只开接敌窗", fallback: "高地骨架不撤，回撤通道可视。", gate: "探测回报队形宽度" },
        { dim: "strike", phase: "T2", title: "侧翼短打前排", fallback: "可开火但不深推，打完立刻收网。", gate: "东翼无渗透告警" },
        { dim: "detect", phase: "T3", title: "交战后复探绕后", fallback: "优先复探绕后与补给节奏，再决定二次开火。", gate: "打击已收网" },
        { dim: "support", phase: "T3", title: "按损失补毒爆", fallback: "产能跟损失走，不按交战规模扩。", sync: "与复探并行", also: ["detect"] },
        { dim: "defense", phase: "T4", title: "复位通道保持可视", fallback: "家门口最低警戒，不因前线抽空。", gate: "复探未改威胁方向" },
        { dim: "strike", phase: "T5", title: "门槛满足才二次脉冲", fallback: "仅当补给抬升或队形露出空档再打。", gate: "探测门槛成立，否则停手" },
      ],
    },
    {
      id: "hold-probe",
      label: "先守后探",
      spec: [
        { dim: "defense", phase: "T0", title: "先把防线钉住", fallback: "高地骨架与家门口警戒同时就位。", gate: "回撤通道未断" },
        { dim: "support", phase: "T0", title: "科技服务防守", fallback: "暂缓三矿，瓦斯优先完成防守科技。", sync: "与防御并行", also: ["defense"] },
        { dim: "detect", phase: "T1", title: "用侧翼证伪快扩", fallback: "东翼走廊与中路开雾同权，先验证假守。", gate: "防线未塌" },
        { dim: "detect", phase: "T2", title: "跟踪补给再改权重", fallback: "补给抬升则提高交战权重，否则继续等。", gate: "侧翼回报已回" },
        { dim: "strike", phase: "T3", title: "只打露出的前排", fallback: "禁止深追，王虫不进交火面。", gate: "探测确认一字过线" },
        { dim: "defense", phase: "T3", title: "接敌窗只护这一段", fallback: "开火窗结束立刻收网复位。", sync: "与短打并行", also: ["strike"] },
        { dim: "support", phase: "T4", title: "补损优先于扩建", fallback: "损失速率绑定补给，不开空窗三矿。", gate: "东翼仍安全" },
        { dim: "strike", phase: "T5", title: "不满足则改牵制", fallback: "若队形未散，改高地口牵制不再脉冲。", gate: "复探未给出空档" },
        { dim: "detect", phase: "T5", title: "持续校正下一窗", fallback: "交战后复探绕后，准备下一轮门槛。", sync: "与改牵制并行", also: ["strike"] },
      ],
    },
    {
      id: "support-gate",
      label: "保障卡点",
      spec: [
        { dim: "support", phase: "T0", title: "经济与科技先闭环", fallback: "工蜂曲线维持，刺蛇穴完成度优先。", gate: "瓦斯与补损队列就绪" },
        { dim: "detect", phase: "T1", title: "开雾服务扩建判断", fallback: "先确认东翼安全，再谈三矿。", gate: "保障门槛已过" },
        { dim: "defense", phase: "T1", title: "矿线与通道同时守", fallback: "守住矿线才允许经济加码。", sync: "与开雾并行", also: ["detect"] },
        { dim: "support", phase: "T2", title: "条件满足才开三矿", fallback: "东翼不安全则继续双矿运转。", gate: "东翼无渗透" },
        { dim: "strike", phase: "T3", title: "产能到位再短打", fallback: "毒爆按待命规模配置，不提前交战。", gate: "补损与科技完成" },
        { dim: "detect", phase: "T3", title: "开火同时盯补给", fallback: "补给抬升才允许转入交战。", sync: "与短打并行", also: ["strike"] },
        { dim: "defense", phase: "T4", title: "保护收网与新经济", fallback: "新节点必须有护航，禁止单独开扩。", gate: "打击已复位" },
        { dim: "strike", phase: "T5", title: "资源不够就停手", fallback: "若补损跟不上，改为牵制不二次脉冲。", gate: "保障未告急" },
      ],
    },
    {
      id: "interleave",
      label: "交织脉冲",
      spec: [
        { dim: "detect", phase: "T0", title: "先拿信息主动权", fallback: "双 Overlord 交错覆盖南北口。", gate: "中路视野刚开" },
        { dim: "support", phase: "T0", title: "待命产能同步铺", fallback: "毒爆后置待命，避免过早暴露。", sync: "与开雾并行", also: ["detect"] },
        { dim: "strike", phase: "T1", title: "高地口牵制面", fallback: "暂不深推，等队形宽度确认。", gate: "探测未报绕后" },
        { dim: "defense", phase: "T1", title: "让牵制面有骨架", fallback: "B2 高地口预留防守骨架。", sync: "与牵制并行", also: ["strike"] },
        { dim: "detect", phase: "T2", title: "侧翼修正正面意图", fallback: "不全凭中路观察，用东翼回报改权重。", gate: "牵制已打出空间" },
        { dim: "strike", phase: "T3", title: "空档出现再切入", fallback: "蓝方一字过中线才侧翼打前排。", gate: "侧翼证伪快扩" },
        { dim: "defense", phase: "T3", title: "护航切入角", fallback: "凹字护航，毒爆居中、刺蛇两翼。", sync: "与切入并行", also: ["strike"] },
        { dim: "support", phase: "T4", title: "按拆网速率补编", fallback: "对方拆网意识强则加护航，不加无护航前排。", gate: "切入已收网" },
        { dim: "detect", phase: "T4", title: "复探决定下一拍", fallback: "绕后与补给同时核验，决定停或再打。", also: ["support"] },
      ],
    },
    {
      id: "reset-loop",
      label: "收网回环",
      spec: [
        { dim: "detect", phase: "T0", title: "先标定交火面", fallback: "确认峡谷南北口队形与补给。", gate: "双通道回报" },
        { dim: "defense", phase: "T1", title: "预留复位折线", fallback: "行军预留避爆折线，降低被侧翼惩罚。", gate: "交火面已标定" },
        { dim: "strike", phase: "T2", title: "打完前排立刻收", fallback: "不追残，不把王虫送进交火面。", gate: "复位通道可视" },
        { dim: "support", phase: "T2", title: "收网同时补损", fallback: "补给绑定前线损失速率。", sync: "与收网并行", also: ["strike"] },
        { dim: "detect", phase: "T3", title: "复探对方拆网", fallback: "见毒爆被风筝则改权重，不硬打。", gate: "第一波已复位" },
        { dim: "defense", phase: "T3", title: "家门口不抽空", fallback: "前线压力再大也留最低警戒。", also: ["detect"] },
        { dim: "strike", phase: "T4", title: "只在复位点再出手", fallback: "未回到复位点禁止二次开火。", gate: "复探未改威胁方向" },
        { dim: "support", phase: "T5", title: "下一环产能重配", fallback: "按待命规模而不是立刻交战规模配置。", gate: "二次出手已结束或取消" },
        { dim: "detect", phase: "T5", title: "把门槛写给下一轮", fallback: "补给与东翼安全作为下一窗开门条件。", sync: "与产能重配并行", also: ["support"] },
      ],
    },
  ];
  let index = seed % packs.length;
  if (lead === "defense") index = 1;
  else if (lead === "support") index = 2;
  else if (lead === "strike") index = seed % 2 === 0 ? 3 : 4;
  else index = seed % 2 === 0 ? 0 : 3;
  if (/三矿|开扩|经济/.test(`${thesis}${killnetNodeText(killnet, "support", finalPlan)}`)) index = 2;
  if (/防守|骨架|高地/.test(`${thesis}${killnetNodeText(killnet, "defense", finalPlan)}`) && seed % 3 === 1) index = 1;
  const pack = packs[index] || packs[0];
  return {
    source: "local",
    sourceLabel: "态势推演",
    patternId: pack.id,
    patternLabel: pack.label,
    steps: composeKillnetSteps(pack.spec, { ...ctx, patternId: pack.id }),
  };
}

const KNOWLEDGE_LIVE_VARIANT_COUNT = 5;
const KNOWLEDGE_LIVE_TICK_MS = 10000; // 每 10 秒切换；5 片 = 50 秒一轮
const KNOWLEDGE_EXPERIENCE_VARIANTS = [
  {
    "id": "slice-1",
    "label": "切片① · 峡谷对峙",
    "stamp": "帧窗 A",
    "cases": {
      "red": {
        "summary": "红方以峡谷防守反击为主轴；本切片强调东翼预警与毒爆待命，经验库持续回写双通道探测规则。",
        "focus": "实时切片① · 红方观察 · 峡谷南北口对峙",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 前线试探与意图对撞",
            "constraint": "【实时切片·峡谷】约束前提：双矿运转、中路视野刚开、蓝方蟑螂比重上升。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 峡谷切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【峡谷】暂不主动深推，打击重心放在高地口牵制面，等待队形宽度确认。",
                    "毒爆集群后置待命，避免过早暴露落点被风筝拆网。",
                    "若蓝方一字过峡谷中线，再以侧翼切入打前排。"
                  ],
                  "conclusion": "[峡谷/打击] 打击结论：不深推，侧翼窗口待确认后开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【峡谷】双 Overlord 交错覆盖峡谷南北口，优先获取蟑螂队形与补给节奏。",
                    "东翼分矿走廊与中路开雾同优先级，防小狗先完成意图修正。",
                    "跟踪蓝方补给抬升，作为是否转入交战的信息门槛。"
                  ],
                  "conclusion": "[峡谷/探测] 探测结论：双通道开雾，信息主动权优先于交战。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【峡谷】B2 高地口预留刺蛇防守骨架，守住活动空间不被压缩。",
                    "主矿与前线之间保留回撤通道，防止被贴脸拆网。",
                    "家门口维持最低警戒，不因前线压力抽空。"
                  ],
                  "conclusion": "[峡谷/防御] 防御结论：高地骨架 + 回撤通道，守而不塌。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【峡谷】工蜂曲线维持，暂缓三矿，瓦斯优先刺蛇穴完成度。",
                    "补给与科技节点绑定前线损失速率，不盲目扩编。",
                    "毒爆产能按「待命规模」而非「立刻交战规模」配置。"
                  ],
                  "conclusion": "[峡谷/保障] 保障结论：科技与产能服务防守反击，不开三矿。"
                }
              },
              "conclusion": "[峡谷] 总判：侦察开雾 + 高地防守姿态，不主动深推。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 峡谷切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【峡谷】主力沿峡谷北坡持续正面施压，追求瞬时伤害差。",
                    "若红方毒爆现身，改蟑螂风筝拆网并绕开落点。",
                    "把流量集中前线，不在本轮分散打经济。"
                  ],
                  "conclusion": "[峡谷/打击] 打击预测：正面不停压，见毒爆则拆网绕行。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【峡谷】小狗东侧绕行确认分矿，验证「快扩/假守」假设。",
                    "用侧翼回报修正正面意图，不全凭中路观察。",
                    "对红方未扩三矿提高「防守反击」权重。"
                  ],
                  "conclusion": "[峡谷/探测] 探测预测：侧翼证伪快扩，再决定是否改目标。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【峡谷】家门口只留薄刺蛇线，接受回防成本换前线流量。",
                    "行军预留避爆折线，降低被侧翼惩罚概率。",
                    "不主动加厚后院，把风险留到下一轮评估。"
                  ],
                  "conclusion": "[峡谷/防御] 防御预测：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【峡谷】补给增量优先投入突击群，而非工蜂或三矿。",
                    "维持蟑螂比重，保证正面可持续施压。",
                    "侦察小狗按「限时任务」抽调，避免长期掉线。"
                  ],
                  "conclusion": "[峡谷/保障] 保障预测：流量堆前线，侦察限时抽调。"
                }
              },
              "conclusion": "[峡谷] 总判：蓝方会用侧翼侦察修正意图，正面不轻易撤压。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·峡谷 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[峡谷] 我方后置开火，对方持续正面施压——时间窗错位，硬接会亏。",
                  "oursRead": "侧翼待命、确认宽度后再打。",
                  "enemyRead": "不停压 + 见毒爆拆网绕行。",
                  "revise": "【峡谷修正】打击维保持待命，不抢先交战；窗口条件写成「队形宽度确认」。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[峡谷] 我方偏中路开雾，对方侧翼小狗可能先完成意图修正。",
                  "oursRead": "双通道开雾，信息优先于交战。",
                  "enemyRead": "东侧绕行证伪快扩。",
                  "revise": "【峡谷修正】东翼与峡谷同优先级，探测维提前于打击维闭环。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[峡谷] 高地骨架可用，但对方薄家门口暴露出对称机会，我方尚未利用。",
                  "oursRead": "高地骨架 + 回撤通道。",
                  "enemyRead": "薄家门口换前线流量。",
                  "revise": "【峡谷修正】守骨架不塌；下一拍可用小股牵制压对方回防成本。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[峡谷] 我方锁三矿喂科技，对方堆前线补给——经济节奏不同步。",
                  "oursRead": "科技优先，不开三矿。",
                  "enemyRead": "补给堆突击，侦察限时抽调。",
                  "revise": "【峡谷修正】保障维继续锁扩，产能按待命规模配置，避免被拖进消耗战。"
                }
              },
              "synthesis": [
                "[峡谷] 跨维结论：信息主动权（探测）决定打击窗口是否成立。",
                "[峡谷] 优先序：探测补东翼 → 防御守骨架 → 打击待命 → 保障锁扩。"
              ],
              "verdict": "[峡谷内省] 内省结论：保持防守骨架，必须补东翼预警，否则下一轮丢信息主动权。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【峡谷应对】探测维：立即补一只 Overlord 偏东悬停，专盯分矿走廊。",
                "防御/打击维：峡谷南口刺蛇线不变，毒爆后置到确认宽度后再侧翼切入。",
                "保障维：瓦斯继续点刺蛇穴，三矿仍锁，等待前线损失速率稳定。"
              ],
              "conclusion": "[峡谷·C] 本轮产出约束：东翼已覆盖 · 不深推 · 毒爆待命。",
              "libraryUpdate": "实时回写（峡谷）：经验库更新：写入「双通道探测并行」成功条；标注东翼后置为待规避失败模式。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 假扩识破与拆网博弈",
            "constraint": "【实时切片·峡谷】约束继承自 C₁：东翼已覆盖、不深推、毒爆待命；蓝方侧翼侦察回报「红未真扩」。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 峡谷切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【峡谷】真实意图：峡谷南坡完成毒爆护航阵，待对方过中线侧翼引爆。",
                    "假扩仅作弱诱导，不抽调前线真实打击资源。",
                    "若诱导失败，立刻放弃欺骗，转纯防守反击脚本。"
                  ],
                  "conclusion": "[峡谷/打击] 打击结论：弱诱导换窗口，主打侧翼引爆。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【峡谷】监视蓝方是否提高对假扩的怀疑权重。",
                    "跟踪对方规避落点的行军折线，预判队形拉长点。",
                    "东翼保持覆盖，防止对方改打分矿走廊。"
                  ],
                  "conclusion": "[峡谷/探测] 探测结论：盯怀疑权重与避爆折线，不丢东翼。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【峡谷】毒爆居中、刺蛇两翼形成凹字护航，防风筝拆网。",
                    "脚手架姿态不抽空护航，防止被象征性盯梢反制。",
                    "交战中保持凹字完整，优先保核心而非追击。"
                  ],
                  "conclusion": "[峡谷/防御] 防御结论：凹字护航优先于欺骗动作。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【峡谷】瓦斯与补给继续喂护航密度，假扩不加建筑投资。",
                    "交战后用损失速率评估是否开启三矿。",
                    "预备下一轮节点保护所需的刺蛇存量。"
                  ],
                  "conclusion": "[峡谷/保障] 保障结论：资源绑护航；开扩开关=损失评估。"
                }
              },
              "conclusion": "[峡谷] 总判：用弱诱导 + 强护航换取一次侧翼交战窗口。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 峡谷切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【峡谷】继续正面压制，不全线回调防假扩。",
                    "对毒爆落点提前写入规避路线，降低引爆效率。",
                    "寻找护航薄弱翼，准备定点拆网。"
                  ],
                  "conclusion": "[峡谷/打击] 打击预测：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【峡谷】已吃过意图误判，对假扩提高怀疑，只派象征性盯梢。",
                    "重点确认毒爆护航阵是否成型。",
                    "记录交战损失，为是否转打节点做信息准备。"
                  ],
                  "conclusion": "[峡谷/探测] 探测预测：弱盯梢 + 确认真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【峡谷】避爆折线写入默认行军，接受队形短暂拉长。",
                    "家门口仍偏薄，但本轮不主动加厚。",
                    "拆网单位前置，降低被凹字咬死的概率。"
                  ],
                  "conclusion": "[峡谷/防御] 防御预测：避爆优先，后院风险暂挂起。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【峡谷】补给维持突击可持续性，不因假扩信号大挪资源。",
                    "盯梢单位按最低成本配置。",
                    "为脉冲压迫预留重组间隔的补给余量。"
                  ],
                  "conclusion": "[峡谷/保障] 保障预测：成本可控的持续施压。"
                }
              },
              "conclusion": "[峡谷] 总判：蓝方不会完全上当，正面压力仍在，拆网意识增强。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·峡谷 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[峡谷] 强欺骗收益下降；对方点名拆网，硬打窗口变短。",
                  "oursRead": "弱诱导 + 侧翼引爆。",
                  "enemyRead": "压体系 + 点名拆网。",
                  "revise": "【峡谷修正】弃强欺骗，主资源留给侧翼窗口，不抽调护航去演戏。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[峡谷] 对方已提高怀疑权重，假扩信号易被廉价化解。",
                  "oursRead": "盯怀疑权重与避爆折线。",
                  "enemyRead": "弱盯梢 + 确认护航体系。",
                  "revise": "【峡谷修正】把「怀疑权重」写入欺骗降级开关；东翼覆盖不撤。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[峡谷] 对方避爆折线会拉长队形，恰给凹字护航侧翼角。",
                  "oursRead": "凹字护航优先。",
                  "enemyRead": "避爆优先，拆网前置。",
                  "revise": "【峡谷修正】护航完整度 > 追击；用阵型换切入角，而不是用骗术。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[峡谷] 开扩开关依赖交战评估，对方也在记损失准备转节点。",
                  "oursRead": "资源绑护航；开扩=损失评估。",
                  "enemyRead": "可持续脉冲 + 可抽调余量。",
                  "revise": "【峡谷修正】保障维只做弱诱导投资；评估环写进开扩门槛。"
                }
              },
              "synthesis": [
                "[峡谷] 跨维结论：欺骗维降权，护航/打击维升权。",
                "[峡谷] 优先序：防御凹字 → 打击侧翼 → 探测盯折线 → 保障弱诱导。"
              ],
              "verdict": "[峡谷内省] 内省结论：弃强欺骗，改「弱诱导 + 强护航」交战脚本。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【峡谷应对】保障维：脚手架仅作弱信号，不抽调前线。",
                "防御/打击维：毒爆居中、刺蛇两翼凹字，等待对方过峡谷中线。",
                "评估环：交战后立刻看损失速率，决定是否开启三矿。"
              ],
              "conclusion": "[峡谷·C] 本轮产出约束：凹字护航就绪 · 弱诱导 · 交战后评估开扩。",
              "libraryUpdate": "实时回写（峡谷）：经验库更新：降级「强欺骗」为失败教训；入库「凹字护航克一字」成功条。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 开扩阈值与节点保护",
            "constraint": "【实时切片·峡谷】约束继承自 C₂：凹字护航、弱诱导、交战后评估；前线损失速率已回落。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 峡谷切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【峡谷】若蓝方抽调打三矿，放弃峡谷压迫，优先拦截穿插。",
                    "节点护卫圈内可对穿插队实施短打击，不恋战。",
                    "小股牵制对方家门口，降低其抽调自由度。"
                  ],
                  "conclusion": "[峡谷/打击] 打击结论：护节点反击为主，辅以后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【峡谷】峡谷保留 Overlord + 少量刺蛇预警抽调迹象。",
                    "监视三矿热点是否已被对方 Overlord 标记。",
                    "跟踪穿插走廊，预设拦截点触发条件。"
                  ],
                  "conclusion": "[峡谷/探测] 探测结论：预警抽调与标记热点，服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【峡谷】刺蛇环伺 + 毒爆核心随三矿迁移，形成节点护卫圈。",
                    "峡谷只留轻量防守，避免护航留在旧前线空窗。",
                    "开扩与护航同迁，节点存活绑扩编开关。"
                  ],
                  "conclusion": "[峡谷/防御] 防御结论：护航随经济节点迁移。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【峡谷】损失速率低于阈值后开启三矿。",
                    "三矿选址避开已被标记的热点。",
                    "产能节奏服务护卫圈密度，而不是继续堆峡谷。"
                  ],
                  "conclusion": "[峡谷/保障] 保障结论：开扩阈值明确，选址避热点。"
                }
              },
              "conclusion": "[峡谷] 总判：经济扩编与节点保护并行。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 峡谷切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【峡谷】抽一队穿插威胁脚手架，逼红方护航离场。",
                    "正面维持脉冲压力，防止空场完成开扩。",
                    "穿插受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[峡谷/打击] 打击预测：节点威胁 + 脉冲牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【峡谷】确认红方开扩迹象与脚手架位置。",
                    "跟踪护航是否随三矿后撤。",
                    "评估后院被牵制的风险信号。"
                  ],
                  "conclusion": "[峡谷/探测] 探测预测：盯开扩与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【峡谷】穿插规模可控，预留回防弹性。",
                    "家门口偏薄被写入风险，避免无回防豪赌。",
                    "正面脉冲缩短暴露，降低被凹字反噬。"
                  ],
                  "conclusion": "[峡谷/防御] 防御预测：可控穿插，保留回防。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【峡谷】把流量在「正面脉冲 / 穿插队 / 回防」间再分配。",
                    "不因单次破点抽空补给。",
                    "成功标准可以是「迫使护航离场」而非拆掉节点。"
                  ],
                  "conclusion": "[峡谷/保障] 保障预测：以机会成本为目标配置兵力。"
                }
              },
              "conclusion": "[峡谷] 总判：蓝方会把压力从正面部分转移到节点。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·峡谷 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[峡谷] 对方转打节点穿插；我方若仍压峡谷，护航会空窗。",
                  "oursRead": "护节点短打 + 后院牵制。",
                  "enemyRead": "穿插威胁脚手架 + 脉冲牵制。",
                  "revise": "【峡谷修正】打击重心随节点迁移；穿插路径预设拦截，不恋战。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[峡谷] 开扩与标记热点同期，预警抽调比盯峡谷更值钱。",
                  "oursRead": "预警抽调与热点标记。",
                  "enemyRead": "盯开扩与护航迁移。",
                  "revise": "【峡谷修正】峡谷降为轻量预警，探测主盯穿插走廊与脚手架。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[峡谷] 开扩与节点暴露双目标压力；护航若留旧前线必空窗。",
                  "oursRead": "护航随经济节点迁移。",
                  "enemyRead": "可控穿插 + 回防弹性。",
                  "revise": "【峡谷修正】护卫圈跟着三矿走；峡谷只留薄防。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[峡谷] 损失回落给出扩编窗口，但选址若落热点会被点名。",
                  "oursRead": "开扩阈值明确，选址避热点。",
                  "enemyRead": "按机会成本配置穿插。",
                  "revise": "【峡谷修正】开扩绑定护航迁移校验；产能喂护卫圈而非峡谷。"
                }
              },
              "synthesis": [
                "[峡谷] 跨维结论：扩编可以开，保护必须同迁。",
                "[峡谷] 优先序：防御同迁 → 保障开扩 → 探测预警 → 打击拦截。"
              ],
              "verdict": "[峡谷内省] 内省结论：扩编可开，但护航必须跟着经济节点走。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【峡谷应对】保障/防御维：开启三矿，刺蛇与毒爆核心后撤形成节点护卫圈。",
                "探测维：峡谷保留 Overlord + 少量刺蛇预警。",
                "打击维：对蓝方抽调路径预设拦截点，避免被穿插。"
              ],
              "conclusion": "[峡谷·C] 本轮沉淀：开扩阈值明确 · 护航随节点迁移 · 形成可入库经验。",
              "libraryUpdate": "实时回写（峡谷）：经验库更新：入库「开扩与护航同迁」「后院牵制换抽调自由度」两条成功经验。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[峡谷] 东翼预警必须与峡谷开雾同优先级",
            "outcome": "success",
            "tags": [
              "峡谷",
              "实时",
              "探测",
              "第1轮",
              "已入库"
            ],
            "summary": "切片峡谷：仅开中路雾会让对方侧翼先完成意图修正；东翼与峡谷同步覆盖才能保住信息主动权。",
            "learned": "探测任务要双通道并行，不能串行后置。",
            "updateNote": "实时更新·峡谷 · 更新动作：覆盖旧「先中路后侧翼」条目，写入双通道并行规则。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[峡谷] 强欺骗在对手已误判一次后收益骤降",
            "outcome": "failure",
            "tags": [
              "峡谷",
              "实时",
              "打击",
              "意图",
              "第2轮"
            ],
            "summary": "切片峡谷：对方吃过快扩误判后，假扩只能廉价化解；执着强欺骗会浪费前线资源。",
            "learned": "欺骗等级随对手怀疑权重下调，改为弱诱导。",
            "updateNote": "实时更新·峡谷 · 更新动作：将强欺骗从推荐脚本降级为慎用，并挂失败标签。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[峡谷] 凹字护航克一字推进",
            "outcome": "success",
            "tags": [
              "峡谷",
              "实时",
              "防御",
              "打击",
              "第2轮"
            ],
            "summary": "切片峡谷：毒爆居中、刺蛇两翼，可利用对方规避落点导致的队形拉长打出侧翼窗口。",
            "learned": "对方拆网意识越强，越要用护航阵型换切入角。",
            "updateNote": "实时更新·峡谷 · 更新动作：新增护航阵型模板，关联打击维侧翼窗口条件。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[峡谷] 开扩与护航必须同迁",
            "outcome": "success",
            "tags": [
              "峡谷",
              "实时",
              "保障",
              "防御",
              "第3轮"
            ],
            "summary": "切片峡谷：损失速率回落后可开三矿，但护航若留在旧前线，新经济节点会空窗。",
            "learned": "扩编决策绑定节点保护迁移，不单独开扩。",
            "updateNote": "实时更新·峡谷 · 更新动作：战役开扩门槛增加「护航迁移」校验项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[峡谷] 家门口偏薄是可利用的对称弱点",
            "outcome": "success",
            "tags": [
              "峡谷",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片峡谷：对方把流量堆前线时，小股牵制可降低其抽调打节点的自由度。",
            "learned": "节点保护不一定加兵，也可通过对手后院制造机会成本。",
            "updateNote": "实时更新·峡谷 · 更新动作：经验库增加「机会成本牵制」条目，供保障/打击维调用。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[峡谷] 损失速率评估写入开扩门槛",
            "outcome": "success",
            "tags": [
              "峡谷",
              "实时",
              "保障",
              "评估",
              "已入库"
            ],
            "summary": "切片峡谷：交战后用损失速率评估，再决定是否开扩，避免凭感觉扩矿。",
            "learned": "把「评估」作为战役开关，而不是事后总结。",
            "updateNote": "实时更新·峡谷 · 更新动作：保障维开扩规则绑定评估环，替换旧凭感觉阈值。",
            "linkedRoundId": "r2"
          }
        ]
      },
      "blue": {
        "summary": "蓝方以中路集中压制为主轴；本切片强调快扩限时证伪与脉冲压迫，经验库回写意图超时规则。",
        "focus": "实时切片① · 蓝方观察 · 北口高压窗口",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 正面施压与意图假设",
            "constraint": "【实时切片·高压】约束前提：双矿稳定、蟑螂线成型、计划以峡谷北口建立压力。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 高压切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【高压】一字推进压缩红方空间，强迫其毒爆过早交战。",
                    "追求前线瞬时伤害差，不在本轮转打经济。",
                    "见毒爆则准备拆网绕行，而非硬接。"
                  ],
                  "conclusion": "[高压/打击] 打击结论：正面高压，逼毒爆提前。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【高压】小狗东侧绕行，验证是否存在快扩。",
                    "把「红方未开三矿」读作防守或反击，设短超时证实。",
                    "侧翼回报用于修正正面目标，不全凭中路。"
                  ],
                  "conclusion": "[高压/探测] 探测结论：侧翼证伪快扩，假设带超时。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【高压】家门口只留薄防御，接受回防成本。",
                    "行军预留毒爆规避折线。",
                    "本轮不加厚后院，标记为下一轮风险。"
                  ],
                  "conclusion": "[高压/防御] 防御结论：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【高压】补给增量投入突击群。",
                    "维持蟑螂比重以支撑持续施压。",
                    "侦察抽调限时，避免前线掉线过久。"
                  ],
                  "conclusion": "[高压/保障] 保障结论：流量堆前线，侦察限时。"
                }
              },
              "conclusion": "[高压] 总判：正面高压 + 侧翼证伪快扩。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 高压切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【高压】可能用毒爆侧翼惩罚一字推进。",
                    "不轻易深推，打击窗口偏后置。",
                    "确认队形宽度前毒爆不交战。"
                  ],
                  "conclusion": "[高压/打击] 打击预测：侧翼待命，后置开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【高压】优先开雾并覆盖峡谷南北口。",
                    "若发现东翼侦察，会补 Overlord 盯分矿走廊。",
                    "跟踪蟑螂队形与补给节奏。"
                  ],
                  "conclusion": "[高压/探测] 探测预测：双通道开雾，防意图被抢先修正。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【高压】高地口布防，守活动空间。",
                    "保持回撤通道，防贴脸拆网。",
                    "家门口维持最低警戒。"
                  ],
                  "conclusion": "[高压/防御] 防御预测：高地骨架防守反击。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【高压】继续点刺蛇科技，暂不开三矿。",
                    "产能服务防守反击而非扩矿。",
                    "瓦斯优先科技完成度。"
                  ],
                  "conclusion": "[高压/保障] 保障预测：科技优先，锁三矿。"
                }
              },
              "conclusion": "[高压] 总判：红方偏防守反击，关键变量是毒爆时机。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·高压 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[高压] 高压有效的前提是毒爆未成型；若假设错误，损失差会反转。",
                  "oursRead": "正面高压逼毒爆提前。",
                  "enemyRead": "侧翼待命、后置开火。",
                  "revise": "【高压修正】打击维保持压力，但把「改打毒爆群」写成超时后的默认切换。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[高压] 快扩假设若无过期，会把兵力锁在错误目标。",
                  "oursRead": "侧翼证伪，假设带超时。",
                  "enemyRead": "双通道开雾，防意图被抢先修正。",
                  "revise": "【高压修正】探测维强制「假设+超时+超时动作」；侧翼要快。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[高压] 薄家门口换流量，被反击时回防成本高。",
                  "oursRead": "薄家门口 + 避爆折线。",
                  "enemyRead": "高地骨架防守反击。",
                  "revise": "【高压修正】本轮可薄，但标记为下一轮风险，不无限加注前线。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[高压] 流量堆前线与侦察抽调争补给。",
                  "oursRead": "补给堆突击，侦察限时。",
                  "enemyRead": "科技优先，锁三矿。",
                  "revise": "【高压修正】侦察抽调限时归队，保障维禁止长期掉线。"
                }
              },
              "synthesis": [
                "[高压] 跨维结论：意图假设必须可证伪，否则打击目标会漂。",
                "[高压] 优先序：探测限时证伪 → 打击可切换目标 → 防御记风险 → 保障限时抽调。"
              ],
              "verdict": "[高压内省] 内省结论：快扩假设设短超时，超时未证实则立刻改打毒爆群。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【高压应对】探测维：小狗侦察限时完成；超时未见到脚手架，则撤销防扩抽调。",
                "打击/防御维：正面保持压力，行军预留毒爆规避折线。",
                "保障维：本轮不加厚家门口，但标记为下一轮风险。"
              ],
              "conclusion": "[高压·C] 本轮产出约束：快扩假设限时 · 正面不停压 · 规避落点。",
              "libraryUpdate": "实时回写（高压）：经验库更新：挂起「无超时意图假设」失败风险；写入限时证伪规则。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 意图修正后的持续施压",
            "constraint": "【实时切片·高压】约束继承自 C₁：快扩假设已超时证伪；红方转为凹字护航。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 高压切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【高压】确认主威胁是毒爆护航阵，持续正面施压。",
                    "风筝拆网降低引爆效率，点名薄弱翼。",
                    "对假扩只派象征性盯梢，不全线回调。"
                  ],
                  "conclusion": "[高压/打击] 打击结论：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【高压】快扩已证伪，转向确认护航阵型与薄弱翼。",
                    "记录交战损失，为转节点做信息准备。",
                    "弱盯梢足够抑制强欺骗。"
                  ],
                  "conclusion": "[高压/探测] 探测结论：目标切换到真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【高压】改脉冲压迫，避免一字队形长时间拉伸。",
                    "拆网单位前置。",
                    "缩短暴露窗口，降低被凹字咬死概率。"
                  ],
                  "conclusion": "[高压/防御] 防御结论：脉冲式降低暴露。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【高压】补给支持短突击—后撤重组节奏。",
                    "盯梢最低成本。",
                    "为是否转打经济节点预留可抽调余量。"
                  ],
                  "conclusion": "[高压/保障] 保障结论：可持续脉冲 + 可抽调余量。"
                }
              },
              "conclusion": "[高压] 总判：对准毒爆体系持续施压。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 高压切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【高压】降级假扩为弱诱导，资源留给侧翼引爆。",
                    "峡谷中线等待过线后开火。",
                    "刺蛇两翼防风筝拆网。"
                  ],
                  "conclusion": "[高压/打击] 打击预测：高质量侧翼窗口。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【高压】监视我方怀疑权重与避爆折线。",
                    "东翼保持覆盖。",
                    "用损失速率评估开扩。"
                  ],
                  "conclusion": "[高压/探测] 探测预测：盯折线与损失评估。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【高压】凹字护航成型，保毒爆核心。",
                    "弱诱导不抽空护航。",
                    "交战中优先保阵型。"
                  ],
                  "conclusion": "[高压/防御] 防御预测：强护航优先。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【高压】资源绑护航密度。",
                    "交战后评估是否开三矿。",
                    "假扩不加建筑投资。"
                  ],
                  "conclusion": "[高压/保障] 保障预测：护航优先于欺骗投资。"
                }
              },
              "conclusion": "[高压] 总判：红方准备打一次高质量侧翼窗口。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·高压 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[高压] 连续一字 + 避爆折线拉长队形，正好喂给凹字侧翼。",
                  "oursRead": "压体系 + 点名拆网。",
                  "enemyRead": "高质量侧翼窗口。",
                  "revise": "【高压修正】改脉冲压迫，缩短暴露；拆网前置点名薄弱翼。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[高压] 快扩已证伪，继续防扩是浪费。",
                  "oursRead": "目标切换到真实体系。",
                  "enemyRead": "盯折线与损失评估。",
                  "revise": "【高压修正】探测结论硬绑定打击关键目标：对准护航体系。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[高压] 过线过深会被凹字最大化惩罚。",
                  "oursRead": "脉冲式降低暴露。",
                  "enemyRead": "强护航优先。",
                  "revise": "【高压修正】短突击—后撤重组，不在中线长时间拉伸。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[高压] 要为转节点预留抽调余量，又要撑住脉冲节奏。",
                  "oursRead": "可持续脉冲 + 可抽调余量。",
                  "enemyRead": "护航优先于欺骗投资。",
                  "revise": "【高压修正】补给服务脉冲间隔；记下损失，准备转节点。"
                }
              },
              "synthesis": [
                "[高压] 跨维结论：证伪后必须改打真实体系，阵型从连续改为脉冲。",
                "[高压] 优先序：探测改目标 → 打击脉冲拆网 → 防御控暴露 → 保障留余量。"
              ],
              "verdict": "[高压内省] 内省结论：改脉冲压迫，缩短队形暴露时间。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【高压应对】打击维：以短突击—后撤重组代替连续一字推进。",
                "打击维：拆网单位前置，护航薄弱处优先点名。",
                "保障/探测维：记录交战损失，为是否转打经济节点做准备。"
              ],
              "conclusion": "[高压·C] 本轮产出约束：脉冲压迫 · 点名拆网 · 准备转节点。",
              "libraryUpdate": "实时回写（高压）：经验库更新：入库「证伪后改打真实体系」；将连续一字推进标为失败模式。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 压力从正面转到节点",
            "constraint": "【实时切片·高压】约束继承自 C₂：脉冲压迫与点名拆网；观察到红方损失回落并有开扩迹象。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 高压切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【高压】抽一队穿插威胁脚手架，逼护航离场。",
                    "正面脉冲不停，防止空场开扩。",
                    "受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[高压/打击] 打击结论：节点威胁制造机会成本。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【高压】确认开扩迹象与脚手架位置。",
                    "跟踪护航是否后撤。",
                    "评估后院牵制风险。"
                  ],
                  "conclusion": "[高压/探测] 探测结论：盯节点与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【高压】穿插规模可控，预留回防。",
                    "家门口薄弱写入风险清单。",
                    "脉冲缩短暴露。"
                  ],
                  "conclusion": "[高压/防御] 防御结论：可控穿插 + 回防弹性。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【高压】流量在脉冲/穿插/回防间再分配。",
                    "成功标准可以是迫使护航离场。",
                    "把节点转移结果写入经验库。"
                  ],
                  "conclusion": "[高压/保障] 保障结论：按机会成本配置，不赌单次破点。"
                }
              },
              "conclusion": "[高压] 总判：节点转移压力，扩大对方决策负担。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 高压切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【高压】对穿插设拦截点，短打不恋战。",
                    "小股牵制我方家门口。",
                    "放弃峡谷压迫以保节点时也可接受。"
                  ],
                  "conclusion": "[高压/打击] 打击预测：拦截 + 后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【高压】峡谷留预警抽调。",
                    "监视脚手架是否被标记。",
                    "跟踪穿插走廊。"
                  ],
                  "conclusion": "[高压/探测] 探测预测：预警服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【高压】护航随三矿迁移。",
                    "峡谷变薄但节点护卫圈加厚。",
                    "开扩与节点存活绑定。"
                  ],
                  "conclusion": "[高压/防御] 防御预测：护航同迁。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【高压】损失回落后开三矿。",
                    "选址避热点。",
                    "产能服务护卫圈。"
                  ],
                  "conclusion": "[高压/保障] 保障预测：扩编绑保护。"
                }
              },
              "conclusion": "[高压] 总判：红方会用护航迁移 + 后院牵制对冲节点压力。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·高压 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[高压] 节点威胁能逼护航离场，但硬闯可能得不偿失。",
                  "oursRead": "节点威胁制造机会成本。",
                  "enemyRead": "拦截 + 后院牵制。",
                  "revise": "【高压修正】可控穿插威胁脚手架；受阻退回拆网，不赌单次破点。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[高压] 要同时看开扩、护航迁移与后院牵制风险。",
                  "oursRead": "盯节点与护航迁移。",
                  "enemyRead": "预警服务开扩安全。",
                  "revise": "【高压修正】探测清单三件套：脚手架、护航位移、后院威胁。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[高压] 抽调打节点暴露家门口薄弱，对手可能牵制反噬。",
                  "oursRead": "可控穿插 + 回防弹性。",
                  "enemyRead": "护航同迁。",
                  "revise": "【高压修正】穿插规模设上限，回防余量必留。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[高压] 成功标准应是改对方资源配置，而非拆掉节点。",
                  "oursRead": "按机会成本配置。",
                  "enemyRead": "扩编绑保护。",
                  "revise": "【高压修正】流量在脉冲/穿插/回防间再分配；结果写入经验库。"
                }
              },
              "synthesis": [
                "[高压] 跨维结论：节点战目标是机会成本，不是豪赌破点。",
                "[高压] 优先序：保障控规模 → 防御留回防 → 打击威胁节点 → 探测盯三信号。"
              ],
              "verdict": "[高压内省] 内省结论：小规模穿插制造机会成本，而不是赌单次破点。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【高压应对】打击/防御维：派出可控穿插队威胁脚手架，同时预留回防。",
                "打击维：正面脉冲不停，阻止红方空场发育。",
                "保障维：把本轮「节点转移」成败写入经验库。"
              ],
              "conclusion": "[高压·C] 本轮沉淀：压力可从正面迁移到节点 · 抽调必须保留回防弹性。",
              "libraryUpdate": "实时回写（高压）：经验库更新：入库「节点压力逼护航离场」「抽调保留回防」两条。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[高压] 意图假设必须带超时",
            "outcome": "failure",
            "tags": [
              "高压",
              "实时",
              "探测",
              "意图",
              "第1轮"
            ],
            "summary": "切片高压：快扩假设若无过期时间，会把前线兵力锁在错误目标上。",
            "learned": "每个意图假设写明证实条件与超时动作。",
            "updateNote": "实时更新·高压 · 更新动作：探测维规则强制「假设+超时+超时动作」三元组。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[高压] 证伪后立刻改打真实体系",
            "outcome": "success",
            "tags": [
              "高压",
              "实时",
              "打击",
              "目标切换",
              "第2轮"
            ],
            "summary": "切片高压：快扩证伪后，压力应转向毒爆护航体系，而不是继续防扩。",
            "learned": "意图修正要同步改关键目标，不能只改口头判断。",
            "updateNote": "实时更新·高压 · 更新动作：打击维关键目标与探测维结论硬绑定。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[高压] 连续一字推进会被凹字护航惩罚",
            "outcome": "failure",
            "tags": [
              "高压",
              "实时",
              "打击",
              "防御",
              "已修订"
            ],
            "summary": "切片高压：避爆折线拉长队形，恰好给侧翼引爆角度。",
            "learned": "改脉冲压迫，控制队形暴露时长。",
            "updateNote": "实时更新·高压 · 更新动作：将连续一字推进标为禁用模板，替换为脉冲脚本。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[高压] 节点压力可逼护航离场",
            "outcome": "success",
            "tags": [
              "高压",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片高压：威胁脚手架足以改变对方护航重心，即使未必拆掉节点。",
            "learned": "节点战的目标可以是「改对方资源配置」。",
            "updateNote": "实时更新·高压 · 更新动作：战役目标库增加「机会成本」成功判据。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[高压] 抽调必须保留回防弹性",
            "outcome": "success",
            "tags": [
              "高压",
              "实时",
              "防御",
              "保障",
              "第3轮"
            ],
            "summary": "切片高压：家门口偏薄时，无回防的穿插会被后院牵制反噬。",
            "learned": "穿插规模可控，先制造机会成本。",
            "updateNote": "实时更新·高压 · 更新动作：保障维抽调清单增加「回防余量」必填项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[高压] 象征性盯梢足以抑制强欺骗",
            "outcome": "success",
            "tags": [
              "高压",
              "实时",
              "探测",
              "信息",
              "已入库"
            ],
            "summary": "切片高压：对手降级假扩后，少量盯梢即可，不必全线回调。",
            "learned": "信息对抗也要算成本，防骗不等于重兵。",
            "updateNote": "实时更新·高压 · 更新动作：探测维防骗任务默认最低成本配置。",
            "linkedRoundId": "r2"
          }
        ]
      }
    }
  },
  {
    "id": "slice-2",
    "label": "切片② · 东翼渗透",
    "stamp": "帧窗 B",
    "cases": {
      "red": {
        "summary": "红方转向东翼渗透反制：小狗走廊与分矿口成为主探测轴，打击维改为定点拦截而非峡谷硬接。",
        "focus": "实时切片② · 红方观察 · 东翼走廊渗透",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 东翼开雾抢先手",
            "constraint": "【实时切片·东翼】约束前提：双矿运转、中路视野刚开、蓝方蟑螂比重上升。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 东翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【东翼】暂不主动深推，打击重心放在高地口牵制面，等待队形宽度确认。",
                    "毒爆集群后置待命，避免过早暴露落点被风筝拆网。",
                    "若蓝方一字过峡谷中线，再以侧翼切入打前排。"
                  ],
                  "conclusion": "[东翼/打击] 打击结论：不深推，侧翼窗口待确认后开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【东翼】双 Overlord 交错覆盖峡谷南北口，优先获取蟑螂队形与补给节奏。",
                    "东翼分矿走廊与中路开雾同优先级，防小狗先完成意图修正。",
                    "跟踪蓝方补给抬升，作为是否转入交战的信息门槛。"
                  ],
                  "conclusion": "[东翼/探测] 探测结论：双通道开雾，信息主动权优先于交战。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【东翼】B2 高地口预留刺蛇防守骨架，守住活动空间不被压缩。",
                    "主矿与前线之间保留回撤通道，防止被贴脸拆网。",
                    "家门口维持最低警戒，不因前线压力抽空。"
                  ],
                  "conclusion": "[东翼/防御] 防御结论：高地骨架 + 回撤通道，守而不塌。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【东翼】工蜂曲线维持，暂缓三矿，瓦斯优先刺蛇穴完成度。",
                    "补给与科技节点绑定前线损失速率，不盲目扩编。",
                    "毒爆产能按「待命规模」而非「立刻交战规模」配置。"
                  ],
                  "conclusion": "[东翼/保障] 保障结论：科技与产能服务防守反击，不开三矿。"
                }
              },
              "conclusion": "[东翼] 总判：侦察开雾 + 高地防守姿态，不主动深推。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 东翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【东翼】主力沿峡谷北坡持续正面施压，追求瞬时伤害差。",
                    "若红方毒爆现身，改蟑螂风筝拆网并绕开落点。",
                    "把流量集中前线，不在本轮分散打经济。"
                  ],
                  "conclusion": "[东翼/打击] 打击预测：正面不停压，见毒爆则拆网绕行。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【东翼】小狗东侧绕行确认分矿，验证「快扩/假守」假设。",
                    "用侧翼回报修正正面意图，不全凭中路观察。",
                    "对红方未扩三矿提高「防守反击」权重。"
                  ],
                  "conclusion": "[东翼/探测] 探测预测：侧翼证伪快扩，再决定是否改目标。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【东翼】家门口只留薄刺蛇线，接受回防成本换前线流量。",
                    "行军预留避爆折线，降低被侧翼惩罚概率。",
                    "不主动加厚后院，把风险留到下一轮评估。"
                  ],
                  "conclusion": "[东翼/防御] 防御预测：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【东翼】补给增量优先投入突击群，而非工蜂或三矿。",
                    "维持蟑螂比重，保证正面可持续施压。",
                    "侦察小狗按「限时任务」抽调，避免长期掉线。"
                  ],
                  "conclusion": "[东翼/保障] 保障预测：流量堆前线，侦察限时抽调。"
                }
              },
              "conclusion": "[东翼] 总判：蓝方会用侧翼侦察修正意图，正面不轻易撤压。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·东翼 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[东翼] 我方后置开火，对方持续正面施压——时间窗错位，硬接会亏。",
                  "oursRead": "侧翼待命、确认宽度后再打。",
                  "enemyRead": "不停压 + 见毒爆拆网绕行。",
                  "revise": "【东翼修正】打击维保持待命，不抢先交战；窗口条件写成「队形宽度确认」。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[东翼] 我方偏中路开雾，对方侧翼小狗可能先完成意图修正。",
                  "oursRead": "双通道开雾，信息优先于交战。",
                  "enemyRead": "东侧绕行证伪快扩。",
                  "revise": "【东翼修正】东翼与峡谷同优先级，探测维提前于打击维闭环。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[东翼] 高地骨架可用，但对方薄家门口暴露出对称机会，我方尚未利用。",
                  "oursRead": "高地骨架 + 回撤通道。",
                  "enemyRead": "薄家门口换前线流量。",
                  "revise": "【东翼修正】守骨架不塌；下一拍可用小股牵制压对方回防成本。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[东翼] 我方锁三矿喂科技，对方堆前线补给——经济节奏不同步。",
                  "oursRead": "科技优先，不开三矿。",
                  "enemyRead": "补给堆突击，侦察限时抽调。",
                  "revise": "【东翼修正】保障维继续锁扩，产能按待命规模配置，避免被拖进消耗战。"
                }
              },
              "synthesis": [
                "[东翼] 跨维结论：信息主动权（探测）决定打击窗口是否成立。",
                "[东翼] 优先序：探测补东翼 → 防御守骨架 → 打击待命 → 保障锁扩。"
              ],
              "verdict": "[东翼内省] 内省结论：保持防守骨架，必须补东翼预警，否则下一轮丢信息主动权。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【东翼应对】探测维：立即补一只 Overlord 偏东悬停，专盯分矿走廊。",
                "防御/打击维：峡谷南口刺蛇线不变，毒爆后置到确认宽度后再侧翼切入。",
                "保障维：瓦斯继续点刺蛇穴，三矿仍锁，等待前线损失速率稳定。"
              ],
              "conclusion": "[东翼·C] 本轮产出约束：东翼已覆盖 · 不深推 · 毒爆待命。",
              "libraryUpdate": "实时回写（东翼）：经验库更新：写入「双通道探测并行」成功条；标注东翼后置为待规避失败模式。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 渗透队与护航换防",
            "constraint": "【实时切片·东翼】约束继承自 C₁：东翼已覆盖、不深推、毒爆待命；蓝方侧翼侦察回报「红未真扩」。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 东翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【东翼】真实意图：峡谷南坡完成毒爆护航阵，待对方过中线侧翼引爆。",
                    "假扩仅作弱诱导，不抽调前线真实打击资源。",
                    "若诱导失败，立刻放弃欺骗，转纯防守反击脚本。"
                  ],
                  "conclusion": "[东翼/打击] 打击结论：弱诱导换窗口，主打侧翼引爆。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【东翼】监视蓝方是否提高对假扩的怀疑权重。",
                    "跟踪对方规避落点的行军折线，预判队形拉长点。",
                    "东翼保持覆盖，防止对方改打分矿走廊。"
                  ],
                  "conclusion": "[东翼/探测] 探测结论：盯怀疑权重与避爆折线，不丢东翼。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【东翼】毒爆居中、刺蛇两翼形成凹字护航，防风筝拆网。",
                    "脚手架姿态不抽空护航，防止被象征性盯梢反制。",
                    "交战中保持凹字完整，优先保核心而非追击。"
                  ],
                  "conclusion": "[东翼/防御] 防御结论：凹字护航优先于欺骗动作。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【东翼】瓦斯与补给继续喂护航密度，假扩不加建筑投资。",
                    "交战后用损失速率评估是否开启三矿。",
                    "预备下一轮节点保护所需的刺蛇存量。"
                  ],
                  "conclusion": "[东翼/保障] 保障结论：资源绑护航；开扩开关=损失评估。"
                }
              },
              "conclusion": "[东翼] 总判：用弱诱导 + 强护航换取一次侧翼交战窗口。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 东翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【东翼】继续正面压制，不全线回调防假扩。",
                    "对毒爆落点提前写入规避路线，降低引爆效率。",
                    "寻找护航薄弱翼，准备定点拆网。"
                  ],
                  "conclusion": "[东翼/打击] 打击预测：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【东翼】已吃过意图误判，对假扩提高怀疑，只派象征性盯梢。",
                    "重点确认毒爆护航阵是否成型。",
                    "记录交战损失，为是否转打节点做信息准备。"
                  ],
                  "conclusion": "[东翼/探测] 探测预测：弱盯梢 + 确认真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【东翼】避爆折线写入默认行军，接受队形短暂拉长。",
                    "家门口仍偏薄，但本轮不主动加厚。",
                    "拆网单位前置，降低被凹字咬死的概率。"
                  ],
                  "conclusion": "[东翼/防御] 防御预测：避爆优先，后院风险暂挂起。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【东翼】补给维持突击可持续性，不因假扩信号大挪资源。",
                    "盯梢单位按最低成本配置。",
                    "为脉冲压迫预留重组间隔的补给余量。"
                  ],
                  "conclusion": "[东翼/保障] 保障预测：成本可控的持续施压。"
                }
              },
              "conclusion": "[东翼] 总判：蓝方不会完全上当，正面压力仍在，拆网意识增强。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·东翼 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[东翼] 强欺骗收益下降；对方点名拆网，硬打窗口变短。",
                  "oursRead": "弱诱导 + 侧翼引爆。",
                  "enemyRead": "压体系 + 点名拆网。",
                  "revise": "【东翼修正】弃强欺骗，主资源留给侧翼窗口，不抽调护航去演戏。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[东翼] 对方已提高怀疑权重，假扩信号易被廉价化解。",
                  "oursRead": "盯怀疑权重与避爆折线。",
                  "enemyRead": "弱盯梢 + 确认护航体系。",
                  "revise": "【东翼修正】把「怀疑权重」写入欺骗降级开关；东翼覆盖不撤。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[东翼] 对方避爆折线会拉长队形，恰给凹字护航侧翼角。",
                  "oursRead": "凹字护航优先。",
                  "enemyRead": "避爆优先，拆网前置。",
                  "revise": "【东翼修正】护航完整度 > 追击；用阵型换切入角，而不是用骗术。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[东翼] 开扩开关依赖交战评估，对方也在记损失准备转节点。",
                  "oursRead": "资源绑护航；开扩=损失评估。",
                  "enemyRead": "可持续脉冲 + 可抽调余量。",
                  "revise": "【东翼修正】保障维只做弱诱导投资；评估环写进开扩门槛。"
                }
              },
              "synthesis": [
                "[东翼] 跨维结论：欺骗维降权，护航/打击维升权。",
                "[东翼] 优先序：防御凹字 → 打击侧翼 → 探测盯折线 → 保障弱诱导。"
              ],
              "verdict": "[东翼内省] 内省结论：弃强欺骗，改「弱诱导 + 强护航」交战脚本。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【东翼应对】保障维：脚手架仅作弱信号，不抽调前线。",
                "防御/打击维：毒爆居中、刺蛇两翼凹字，等待对方过峡谷中线。",
                "评估环：交战后立刻看损失速率，决定是否开启三矿。"
              ],
              "conclusion": "[东翼·C] 本轮产出约束：凹字护航就绪 · 弱诱导 · 交战后评估开扩。",
              "libraryUpdate": "实时回写（东翼）：经验库更新：降级「强欺骗」为失败教训；入库「凹字护航克一字」成功条。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 分矿口拦截定型",
            "constraint": "【实时切片·东翼】约束继承自 C₂：凹字护航、弱诱导、交战后评估；前线损失速率已回落。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 东翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【东翼】若蓝方抽调打三矿，放弃峡谷压迫，优先拦截穿插。",
                    "节点护卫圈内可对穿插队实施短打击，不恋战。",
                    "小股牵制对方家门口，降低其抽调自由度。"
                  ],
                  "conclusion": "[东翼/打击] 打击结论：护节点反击为主，辅以后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【东翼】峡谷保留 Overlord + 少量刺蛇预警抽调迹象。",
                    "监视三矿热点是否已被对方 Overlord 标记。",
                    "跟踪穿插走廊，预设拦截点触发条件。"
                  ],
                  "conclusion": "[东翼/探测] 探测结论：预警抽调与标记热点，服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【东翼】刺蛇环伺 + 毒爆核心随三矿迁移，形成节点护卫圈。",
                    "峡谷只留轻量防守，避免护航留在旧前线空窗。",
                    "开扩与护航同迁，节点存活绑扩编开关。"
                  ],
                  "conclusion": "[东翼/防御] 防御结论：护航随经济节点迁移。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【东翼】损失速率低于阈值后开启三矿。",
                    "三矿选址避开已被标记的热点。",
                    "产能节奏服务护卫圈密度，而不是继续堆峡谷。"
                  ],
                  "conclusion": "[东翼/保障] 保障结论：开扩阈值明确，选址避热点。"
                }
              },
              "conclusion": "[东翼] 总判：经济扩编与节点保护并行。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 东翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【东翼】抽一队穿插威胁脚手架，逼红方护航离场。",
                    "正面维持脉冲压力，防止空场完成开扩。",
                    "穿插受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[东翼/打击] 打击预测：节点威胁 + 脉冲牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【东翼】确认红方开扩迹象与脚手架位置。",
                    "跟踪护航是否随三矿后撤。",
                    "评估后院被牵制的风险信号。"
                  ],
                  "conclusion": "[东翼/探测] 探测预测：盯开扩与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【东翼】穿插规模可控，预留回防弹性。",
                    "家门口偏薄被写入风险，避免无回防豪赌。",
                    "正面脉冲缩短暴露，降低被凹字反噬。"
                  ],
                  "conclusion": "[东翼/防御] 防御预测：可控穿插，保留回防。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【东翼】把流量在「正面脉冲 / 穿插队 / 回防」间再分配。",
                    "不因单次破点抽空补给。",
                    "成功标准可以是「迫使护航离场」而非拆掉节点。"
                  ],
                  "conclusion": "[东翼/保障] 保障预测：以机会成本为目标配置兵力。"
                }
              },
              "conclusion": "[东翼] 总判：蓝方会把压力从正面部分转移到节点。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·东翼 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[东翼] 对方转打节点穿插；我方若仍压峡谷，护航会空窗。",
                  "oursRead": "护节点短打 + 后院牵制。",
                  "enemyRead": "穿插威胁脚手架 + 脉冲牵制。",
                  "revise": "【东翼修正】打击重心随节点迁移；穿插路径预设拦截，不恋战。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[东翼] 开扩与标记热点同期，预警抽调比盯峡谷更值钱。",
                  "oursRead": "预警抽调与热点标记。",
                  "enemyRead": "盯开扩与护航迁移。",
                  "revise": "【东翼修正】峡谷降为轻量预警，探测主盯穿插走廊与脚手架。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[东翼] 开扩与节点暴露双目标压力；护航若留旧前线必空窗。",
                  "oursRead": "护航随经济节点迁移。",
                  "enemyRead": "可控穿插 + 回防弹性。",
                  "revise": "【东翼修正】护卫圈跟着三矿走；峡谷只留薄防。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[东翼] 损失回落给出扩编窗口，但选址若落热点会被点名。",
                  "oursRead": "开扩阈值明确，选址避热点。",
                  "enemyRead": "按机会成本配置穿插。",
                  "revise": "【东翼修正】开扩绑定护航迁移校验；产能喂护卫圈而非峡谷。"
                }
              },
              "synthesis": [
                "[东翼] 跨维结论：扩编可以开，保护必须同迁。",
                "[东翼] 优先序：防御同迁 → 保障开扩 → 探测预警 → 打击拦截。"
              ],
              "verdict": "[东翼内省] 内省结论：扩编可开，但护航必须跟着经济节点走。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【东翼应对】保障/防御维：开启三矿，刺蛇与毒爆核心后撤形成节点护卫圈。",
                "探测维：峡谷保留 Overlord + 少量刺蛇预警。",
                "打击维：对蓝方抽调路径预设拦截点，避免被穿插。"
              ],
              "conclusion": "[东翼·C] 本轮沉淀：开扩阈值明确 · 护航随节点迁移 · 形成可入库经验。",
              "libraryUpdate": "实时回写（东翼）：经验库更新：入库「开扩与护航同迁」「后院牵制换抽调自由度」两条成功经验。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[东翼] 东翼预警必须与峡谷开雾同优先级",
            "outcome": "success",
            "tags": [
              "东翼",
              "实时",
              "探测",
              "第1轮",
              "已入库"
            ],
            "summary": "切片东翼：仅开中路雾会让对方侧翼先完成意图修正；东翼与峡谷同步覆盖才能保住信息主动权。",
            "learned": "探测任务要双通道并行，不能串行后置。",
            "updateNote": "实时更新·东翼 · 更新动作：覆盖旧「先中路后侧翼」条目，写入双通道并行规则。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[东翼] 强欺骗在对手已误判一次后收益骤降",
            "outcome": "failure",
            "tags": [
              "东翼",
              "实时",
              "打击",
              "意图",
              "第2轮"
            ],
            "summary": "切片东翼：对方吃过快扩误判后，假扩只能廉价化解；执着强欺骗会浪费前线资源。",
            "learned": "欺骗等级随对手怀疑权重下调，改为弱诱导。",
            "updateNote": "实时更新·东翼 · 更新动作：将强欺骗从推荐脚本降级为慎用，并挂失败标签。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[东翼] 凹字护航克一字推进",
            "outcome": "success",
            "tags": [
              "东翼",
              "实时",
              "防御",
              "打击",
              "第2轮"
            ],
            "summary": "切片东翼：毒爆居中、刺蛇两翼，可利用对方规避落点导致的队形拉长打出侧翼窗口。",
            "learned": "对方拆网意识越强，越要用护航阵型换切入角。",
            "updateNote": "实时更新·东翼 · 更新动作：新增护航阵型模板，关联打击维侧翼窗口条件。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[东翼] 开扩与护航必须同迁",
            "outcome": "success",
            "tags": [
              "东翼",
              "实时",
              "保障",
              "防御",
              "第3轮"
            ],
            "summary": "切片东翼：损失速率回落后可开三矿，但护航若留在旧前线，新经济节点会空窗。",
            "learned": "扩编决策绑定节点保护迁移，不单独开扩。",
            "updateNote": "实时更新·东翼 · 更新动作：战役开扩门槛增加「护航迁移」校验项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[东翼] 家门口偏薄是可利用的对称弱点",
            "outcome": "success",
            "tags": [
              "东翼",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片东翼：对方把流量堆前线时，小股牵制可降低其抽调打节点的自由度。",
            "learned": "节点保护不一定加兵，也可通过对手后院制造机会成本。",
            "updateNote": "实时更新·东翼 · 更新动作：经验库增加「机会成本牵制」条目，供保障/打击维调用。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[东翼] 损失速率评估写入开扩门槛",
            "outcome": "success",
            "tags": [
              "东翼",
              "实时",
              "保障",
              "评估",
              "已入库"
            ],
            "summary": "切片东翼：交战后用损失速率评估，再决定是否开扩，避免凭感觉扩矿。",
            "learned": "把「评估」作为战役开关，而不是事后总结。",
            "updateNote": "实时更新·东翼 · 更新动作：保障维开扩规则绑定评估环，替换旧凭感觉阈值。",
            "linkedRoundId": "r2"
          }
        ]
      },
      "blue": {
        "summary": "蓝方发现东翼被抢先开雾，改为象征性盯梢 + 中路脉冲，避免把流量锁死在错误侧翼。",
        "focus": "实时切片② · 蓝方观察 · 侧翼信息战",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 侧翼回报延迟",
            "constraint": "【实时切片·侧翼】约束前提：双矿稳定、蟑螂线成型、计划以峡谷北口建立压力。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 侧翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【侧翼】一字推进压缩红方空间，强迫其毒爆过早交战。",
                    "追求前线瞬时伤害差，不在本轮转打经济。",
                    "见毒爆则准备拆网绕行，而非硬接。"
                  ],
                  "conclusion": "[侧翼/打击] 打击结论：正面高压，逼毒爆提前。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【侧翼】小狗东侧绕行，验证是否存在快扩。",
                    "把「红方未开三矿」读作防守或反击，设短超时证实。",
                    "侧翼回报用于修正正面目标，不全凭中路。"
                  ],
                  "conclusion": "[侧翼/探测] 探测结论：侧翼证伪快扩，假设带超时。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【侧翼】家门口只留薄防御，接受回防成本。",
                    "行军预留毒爆规避折线。",
                    "本轮不加厚后院，标记为下一轮风险。"
                  ],
                  "conclusion": "[侧翼/防御] 防御结论：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【侧翼】补给增量投入突击群。",
                    "维持蟑螂比重以支撑持续施压。",
                    "侦察抽调限时，避免前线掉线过久。"
                  ],
                  "conclusion": "[侧翼/保障] 保障结论：流量堆前线，侦察限时。"
                }
              },
              "conclusion": "[侧翼] 总判：正面高压 + 侧翼证伪快扩。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 侧翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【侧翼】可能用毒爆侧翼惩罚一字推进。",
                    "不轻易深推，打击窗口偏后置。",
                    "确认队形宽度前毒爆不交战。"
                  ],
                  "conclusion": "[侧翼/打击] 打击预测：侧翼待命，后置开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【侧翼】优先开雾并覆盖峡谷南北口。",
                    "若发现东翼侦察，会补 Overlord 盯分矿走廊。",
                    "跟踪蟑螂队形与补给节奏。"
                  ],
                  "conclusion": "[侧翼/探测] 探测预测：双通道开雾，防意图被抢先修正。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【侧翼】高地口布防，守活动空间。",
                    "保持回撤通道，防贴脸拆网。",
                    "家门口维持最低警戒。"
                  ],
                  "conclusion": "[侧翼/防御] 防御预测：高地骨架防守反击。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【侧翼】继续点刺蛇科技，暂不开三矿。",
                    "产能服务防守反击而非扩矿。",
                    "瓦斯优先科技完成度。"
                  ],
                  "conclusion": "[侧翼/保障] 保障预测：科技优先，锁三矿。"
                }
              },
              "conclusion": "[侧翼] 总判：红方偏防守反击，关键变量是毒爆时机。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·侧翼 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[侧翼] 高压有效的前提是毒爆未成型；若假设错误，损失差会反转。",
                  "oursRead": "正面高压逼毒爆提前。",
                  "enemyRead": "侧翼待命、后置开火。",
                  "revise": "【侧翼修正】打击维保持压力，但把「改打毒爆群」写成超时后的默认切换。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[侧翼] 快扩假设若无过期，会把兵力锁在错误目标。",
                  "oursRead": "侧翼证伪，假设带超时。",
                  "enemyRead": "双通道开雾，防意图被抢先修正。",
                  "revise": "【侧翼修正】探测维强制「假设+超时+超时动作」；侧翼要快。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[侧翼] 薄家门口换流量，被反击时回防成本高。",
                  "oursRead": "薄家门口 + 避爆折线。",
                  "enemyRead": "高地骨架防守反击。",
                  "revise": "【侧翼修正】本轮可薄，但标记为下一轮风险，不无限加注前线。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[侧翼] 流量堆前线与侦察抽调争补给。",
                  "oursRead": "补给堆突击，侦察限时。",
                  "enemyRead": "科技优先，锁三矿。",
                  "revise": "【侧翼修正】侦察抽调限时归队，保障维禁止长期掉线。"
                }
              },
              "synthesis": [
                "[侧翼] 跨维结论：意图假设必须可证伪，否则打击目标会漂。",
                "[侧翼] 优先序：探测限时证伪 → 打击可切换目标 → 防御记风险 → 保障限时抽调。"
              ],
              "verdict": "[侧翼内省] 内省结论：快扩假设设短超时，超时未证实则立刻改打毒爆群。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【侧翼应对】探测维：小狗侦察限时完成；超时未见到脚手架，则撤销防扩抽调。",
                "打击/防御维：正面保持压力，行军预留毒爆规避折线。",
                "保障维：本轮不加厚家门口，但标记为下一轮风险。"
              ],
              "conclusion": "[侧翼·C] 本轮产出约束：快扩假设限时 · 正面不停压 · 规避落点。",
              "libraryUpdate": "实时回写（侧翼）：经验库更新：挂起「无超时意图假设」失败风险；写入限时证伪规则。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 降本盯梢修正",
            "constraint": "【实时切片·侧翼】约束继承自 C₁：快扩假设已超时证伪；红方转为凹字护航。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 侧翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【侧翼】确认主威胁是毒爆护航阵，持续正面施压。",
                    "风筝拆网降低引爆效率，点名薄弱翼。",
                    "对假扩只派象征性盯梢，不全线回调。"
                  ],
                  "conclusion": "[侧翼/打击] 打击结论：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【侧翼】快扩已证伪，转向确认护航阵型与薄弱翼。",
                    "记录交战损失，为转节点做信息准备。",
                    "弱盯梢足够抑制强欺骗。"
                  ],
                  "conclusion": "[侧翼/探测] 探测结论：目标切换到真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【侧翼】改脉冲压迫，避免一字队形长时间拉伸。",
                    "拆网单位前置。",
                    "缩短暴露窗口，降低被凹字咬死概率。"
                  ],
                  "conclusion": "[侧翼/防御] 防御结论：脉冲式降低暴露。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【侧翼】补给支持短突击—后撤重组节奏。",
                    "盯梢最低成本。",
                    "为是否转打经济节点预留可抽调余量。"
                  ],
                  "conclusion": "[侧翼/保障] 保障结论：可持续脉冲 + 可抽调余量。"
                }
              },
              "conclusion": "[侧翼] 总判：对准毒爆体系持续施压。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 侧翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【侧翼】降级假扩为弱诱导，资源留给侧翼引爆。",
                    "峡谷中线等待过线后开火。",
                    "刺蛇两翼防风筝拆网。"
                  ],
                  "conclusion": "[侧翼/打击] 打击预测：高质量侧翼窗口。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【侧翼】监视我方怀疑权重与避爆折线。",
                    "东翼保持覆盖。",
                    "用损失速率评估开扩。"
                  ],
                  "conclusion": "[侧翼/探测] 探测预测：盯折线与损失评估。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【侧翼】凹字护航成型，保毒爆核心。",
                    "弱诱导不抽空护航。",
                    "交战中优先保阵型。"
                  ],
                  "conclusion": "[侧翼/防御] 防御预测：强护航优先。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【侧翼】资源绑护航密度。",
                    "交战后评估是否开三矿。",
                    "假扩不加建筑投资。"
                  ],
                  "conclusion": "[侧翼/保障] 保障预测：护航优先于欺骗投资。"
                }
              },
              "conclusion": "[侧翼] 总判：红方准备打一次高质量侧翼窗口。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·侧翼 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[侧翼] 连续一字 + 避爆折线拉长队形，正好喂给凹字侧翼。",
                  "oursRead": "压体系 + 点名拆网。",
                  "enemyRead": "高质量侧翼窗口。",
                  "revise": "【侧翼修正】改脉冲压迫，缩短暴露；拆网前置点名薄弱翼。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[侧翼] 快扩已证伪，继续防扩是浪费。",
                  "oursRead": "目标切换到真实体系。",
                  "enemyRead": "盯折线与损失评估。",
                  "revise": "【侧翼修正】探测结论硬绑定打击关键目标：对准护航体系。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[侧翼] 过线过深会被凹字最大化惩罚。",
                  "oursRead": "脉冲式降低暴露。",
                  "enemyRead": "强护航优先。",
                  "revise": "【侧翼修正】短突击—后撤重组，不在中线长时间拉伸。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[侧翼] 要为转节点预留抽调余量，又要撑住脉冲节奏。",
                  "oursRead": "可持续脉冲 + 可抽调余量。",
                  "enemyRead": "护航优先于欺骗投资。",
                  "revise": "【侧翼修正】补给服务脉冲间隔；记下损失，准备转节点。"
                }
              },
              "synthesis": [
                "[侧翼] 跨维结论：证伪后必须改打真实体系，阵型从连续改为脉冲。",
                "[侧翼] 优先序：探测改目标 → 打击脉冲拆网 → 防御控暴露 → 保障留余量。"
              ],
              "verdict": "[侧翼内省] 内省结论：改脉冲压迫，缩短队形暴露时间。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【侧翼应对】打击维：以短突击—后撤重组代替连续一字推进。",
                "打击维：拆网单位前置，护航薄弱处优先点名。",
                "保障/探测维：记录交战损失，为是否转打经济节点做准备。"
              ],
              "conclusion": "[侧翼·C] 本轮产出约束：脉冲压迫 · 点名拆网 · 准备转节点。",
              "libraryUpdate": "实时回写（侧翼）：经验库更新：入库「证伪后改打真实体系」；将连续一字推进标为失败模式。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 中路回收流量",
            "constraint": "【实时切片·侧翼】约束继承自 C₂：脉冲压迫与点名拆网；观察到红方损失回落并有开扩迹象。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 侧翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【侧翼】抽一队穿插威胁脚手架，逼护航离场。",
                    "正面脉冲不停，防止空场开扩。",
                    "受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[侧翼/打击] 打击结论：节点威胁制造机会成本。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【侧翼】确认开扩迹象与脚手架位置。",
                    "跟踪护航是否后撤。",
                    "评估后院牵制风险。"
                  ],
                  "conclusion": "[侧翼/探测] 探测结论：盯节点与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【侧翼】穿插规模可控，预留回防。",
                    "家门口薄弱写入风险清单。",
                    "脉冲缩短暴露。"
                  ],
                  "conclusion": "[侧翼/防御] 防御结论：可控穿插 + 回防弹性。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【侧翼】流量在脉冲/穿插/回防间再分配。",
                    "成功标准可以是迫使护航离场。",
                    "把节点转移结果写入经验库。"
                  ],
                  "conclusion": "[侧翼/保障] 保障结论：按机会成本配置，不赌单次破点。"
                }
              },
              "conclusion": "[侧翼] 总判：节点转移压力，扩大对方决策负担。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 侧翼切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【侧翼】对穿插设拦截点，短打不恋战。",
                    "小股牵制我方家门口。",
                    "放弃峡谷压迫以保节点时也可接受。"
                  ],
                  "conclusion": "[侧翼/打击] 打击预测：拦截 + 后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【侧翼】峡谷留预警抽调。",
                    "监视脚手架是否被标记。",
                    "跟踪穿插走廊。"
                  ],
                  "conclusion": "[侧翼/探测] 探测预测：预警服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【侧翼】护航随三矿迁移。",
                    "峡谷变薄但节点护卫圈加厚。",
                    "开扩与节点存活绑定。"
                  ],
                  "conclusion": "[侧翼/防御] 防御预测：护航同迁。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【侧翼】损失回落后开三矿。",
                    "选址避热点。",
                    "产能服务护卫圈。"
                  ],
                  "conclusion": "[侧翼/保障] 保障预测：扩编绑保护。"
                }
              },
              "conclusion": "[侧翼] 总判：红方会用护航迁移 + 后院牵制对冲节点压力。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·侧翼 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[侧翼] 节点威胁能逼护航离场，但硬闯可能得不偿失。",
                  "oursRead": "节点威胁制造机会成本。",
                  "enemyRead": "拦截 + 后院牵制。",
                  "revise": "【侧翼修正】可控穿插威胁脚手架；受阻退回拆网，不赌单次破点。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[侧翼] 要同时看开扩、护航迁移与后院牵制风险。",
                  "oursRead": "盯节点与护航迁移。",
                  "enemyRead": "预警服务开扩安全。",
                  "revise": "【侧翼修正】探测清单三件套：脚手架、护航位移、后院威胁。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[侧翼] 抽调打节点暴露家门口薄弱，对手可能牵制反噬。",
                  "oursRead": "可控穿插 + 回防弹性。",
                  "enemyRead": "护航同迁。",
                  "revise": "【侧翼修正】穿插规模设上限，回防余量必留。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[侧翼] 成功标准应是改对方资源配置，而非拆掉节点。",
                  "oursRead": "按机会成本配置。",
                  "enemyRead": "扩编绑保护。",
                  "revise": "【侧翼修正】流量在脉冲/穿插/回防间再分配；结果写入经验库。"
                }
              },
              "synthesis": [
                "[侧翼] 跨维结论：节点战目标是机会成本，不是豪赌破点。",
                "[侧翼] 优先序：保障控规模 → 防御留回防 → 打击威胁节点 → 探测盯三信号。"
              ],
              "verdict": "[侧翼内省] 内省结论：小规模穿插制造机会成本，而不是赌单次破点。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【侧翼应对】打击/防御维：派出可控穿插队威胁脚手架，同时预留回防。",
                "打击维：正面脉冲不停，阻止红方空场发育。",
                "保障维：把本轮「节点转移」成败写入经验库。"
              ],
              "conclusion": "[侧翼·C] 本轮沉淀：压力可从正面迁移到节点 · 抽调必须保留回防弹性。",
              "libraryUpdate": "实时回写（侧翼）：经验库更新：入库「节点压力逼护航离场」「抽调保留回防」两条。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[侧翼] 意图假设必须带超时",
            "outcome": "failure",
            "tags": [
              "侧翼",
              "实时",
              "探测",
              "意图",
              "第1轮"
            ],
            "summary": "切片侧翼：快扩假设若无过期时间，会把前线兵力锁在错误目标上。",
            "learned": "每个意图假设写明证实条件与超时动作。",
            "updateNote": "实时更新·侧翼 · 更新动作：探测维规则强制「假设+超时+超时动作」三元组。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[侧翼] 证伪后立刻改打真实体系",
            "outcome": "success",
            "tags": [
              "侧翼",
              "实时",
              "打击",
              "目标切换",
              "第2轮"
            ],
            "summary": "切片侧翼：快扩证伪后，压力应转向毒爆护航体系，而不是继续防扩。",
            "learned": "意图修正要同步改关键目标，不能只改口头判断。",
            "updateNote": "实时更新·侧翼 · 更新动作：打击维关键目标与探测维结论硬绑定。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[侧翼] 连续一字推进会被凹字护航惩罚",
            "outcome": "failure",
            "tags": [
              "侧翼",
              "实时",
              "打击",
              "防御",
              "已修订"
            ],
            "summary": "切片侧翼：避爆折线拉长队形，恰好给侧翼引爆角度。",
            "learned": "改脉冲压迫，控制队形暴露时长。",
            "updateNote": "实时更新·侧翼 · 更新动作：将连续一字推进标为禁用模板，替换为脉冲脚本。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[侧翼] 节点压力可逼护航离场",
            "outcome": "success",
            "tags": [
              "侧翼",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片侧翼：威胁脚手架足以改变对方护航重心，即使未必拆掉节点。",
            "learned": "节点战的目标可以是「改对方资源配置」。",
            "updateNote": "实时更新·侧翼 · 更新动作：战役目标库增加「机会成本」成功判据。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[侧翼] 抽调必须保留回防弹性",
            "outcome": "success",
            "tags": [
              "侧翼",
              "实时",
              "防御",
              "保障",
              "第3轮"
            ],
            "summary": "切片侧翼：家门口偏薄时，无回防的穿插会被后院牵制反噬。",
            "learned": "穿插规模可控，先制造机会成本。",
            "updateNote": "实时更新·侧翼 · 更新动作：保障维抽调清单增加「回防余量」必填项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[侧翼] 象征性盯梢足以抑制强欺骗",
            "outcome": "success",
            "tags": [
              "侧翼",
              "实时",
              "探测",
              "信息",
              "已入库"
            ],
            "summary": "切片侧翼：对手降级假扩后，少量盯梢即可，不必全线回调。",
            "learned": "信息对抗也要算成本，防骗不等于重兵。",
            "updateNote": "实时更新·侧翼 · 更新动作：探测维防骗任务默认最低成本配置。",
            "linkedRoundId": "r2"
          }
        ]
      }
    }
  },
  {
    "id": "slice-3",
    "label": "切片③ · 空中骚扰",
    "stamp": "帧窗 C",
    "cases": {
      "red": {
        "summary": "红方引入空中骚扰博弈：Overlord 与突变体路径干扰对方补给抬升，地面凹字护航降为次主轴。",
        "focus": "实时切片③ · 红方观察 · 空中路径干扰",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 空中开雾与补给扰动",
            "constraint": "【实时切片·空中】约束前提：双矿运转、中路视野刚开、蓝方蟑螂比重上升。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 空中切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【空中】暂不主动深推，打击重心放在高地口牵制面，等待队形宽度确认。",
                    "毒爆集群后置待命，避免过早暴露落点被风筝拆网。",
                    "若蓝方一字过峡谷中线，再以侧翼切入打前排。"
                  ],
                  "conclusion": "[空中/打击] 打击结论：不深推，侧翼窗口待确认后开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【空中】双 Overlord 交错覆盖峡谷南北口，优先获取蟑螂队形与补给节奏。",
                    "东翼分矿走廊与中路开雾同优先级，防小狗先完成意图修正。",
                    "跟踪蓝方补给抬升，作为是否转入交战的信息门槛。"
                  ],
                  "conclusion": "[空中/探测] 探测结论：双通道开雾，信息主动权优先于交战。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【空中】B2 高地口预留刺蛇防守骨架，守住活动空间不被压缩。",
                    "主矿与前线之间保留回撤通道，防止被贴脸拆网。",
                    "家门口维持最低警戒，不因前线压力抽空。"
                  ],
                  "conclusion": "[空中/防御] 防御结论：高地骨架 + 回撤通道，守而不塌。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【空中】工蜂曲线维持，暂缓三矿，瓦斯优先刺蛇穴完成度。",
                    "补给与科技节点绑定前线损失速率，不盲目扩编。",
                    "毒爆产能按「待命规模」而非「立刻交战规模」配置。"
                  ],
                  "conclusion": "[空中/保障] 保障结论：科技与产能服务防守反击，不开三矿。"
                }
              },
              "conclusion": "[空中] 总判：侦察开雾 + 高地防守姿态，不主动深推。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 空中切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【空中】主力沿峡谷北坡持续正面施压，追求瞬时伤害差。",
                    "若红方毒爆现身，改蟑螂风筝拆网并绕开落点。",
                    "把流量集中前线，不在本轮分散打经济。"
                  ],
                  "conclusion": "[空中/打击] 打击预测：正面不停压，见毒爆则拆网绕行。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【空中】小狗东侧绕行确认分矿，验证「快扩/假守」假设。",
                    "用侧翼回报修正正面意图，不全凭中路观察。",
                    "对红方未扩三矿提高「防守反击」权重。"
                  ],
                  "conclusion": "[空中/探测] 探测预测：侧翼证伪快扩，再决定是否改目标。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【空中】家门口只留薄刺蛇线，接受回防成本换前线流量。",
                    "行军预留避爆折线，降低被侧翼惩罚概率。",
                    "不主动加厚后院，把风险留到下一轮评估。"
                  ],
                  "conclusion": "[空中/防御] 防御预测：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【空中】补给增量优先投入突击群，而非工蜂或三矿。",
                    "维持蟑螂比重，保证正面可持续施压。",
                    "侦察小狗按「限时任务」抽调，避免长期掉线。"
                  ],
                  "conclusion": "[空中/保障] 保障预测：流量堆前线，侦察限时抽调。"
                }
              },
              "conclusion": "[空中] 总判：蓝方会用侧翼侦察修正意图，正面不轻易撤压。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·空中 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[空中] 我方后置开火，对方持续正面施压——时间窗错位，硬接会亏。",
                  "oursRead": "侧翼待命、确认宽度后再打。",
                  "enemyRead": "不停压 + 见毒爆拆网绕行。",
                  "revise": "【空中修正】打击维保持待命，不抢先交战；窗口条件写成「队形宽度确认」。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[空中] 我方偏中路开雾，对方侧翼小狗可能先完成意图修正。",
                  "oursRead": "双通道开雾，信息优先于交战。",
                  "enemyRead": "东侧绕行证伪快扩。",
                  "revise": "【空中修正】东翼与峡谷同优先级，探测维提前于打击维闭环。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[空中] 高地骨架可用，但对方薄家门口暴露出对称机会，我方尚未利用。",
                  "oursRead": "高地骨架 + 回撤通道。",
                  "enemyRead": "薄家门口换前线流量。",
                  "revise": "【空中修正】守骨架不塌；下一拍可用小股牵制压对方回防成本。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[空中] 我方锁三矿喂科技，对方堆前线补给——经济节奏不同步。",
                  "oursRead": "科技优先，不开三矿。",
                  "enemyRead": "补给堆突击，侦察限时抽调。",
                  "revise": "【空中修正】保障维继续锁扩，产能按待命规模配置，避免被拖进消耗战。"
                }
              },
              "synthesis": [
                "[空中] 跨维结论：信息主动权（探测）决定打击窗口是否成立。",
                "[空中] 优先序：探测补东翼 → 防御守骨架 → 打击待命 → 保障锁扩。"
              ],
              "verdict": "[空中内省] 内省结论：保持防守骨架，必须补东翼预警，否则下一轮丢信息主动权。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【空中应对】探测维：立即补一只 Overlord 偏东悬停，专盯分矿走廊。",
                "防御/打击维：峡谷南口刺蛇线不变，毒爆后置到确认宽度后再侧翼切入。",
                "保障维：瓦斯继续点刺蛇穴，三矿仍锁，等待前线损失速率稳定。"
              ],
              "conclusion": "[空中·C] 本轮产出约束：东翼已覆盖 · 不深推 · 毒爆待命。",
              "libraryUpdate": "实时回写（空中）：经验库更新：写入「双通道探测并行」成功条；标注东翼后置为待规避失败模式。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 骚扰落地窗口",
            "constraint": "【实时切片·空中】约束继承自 C₁：东翼已覆盖、不深推、毒爆待命；蓝方侧翼侦察回报「红未真扩」。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 空中切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【空中】真实意图：峡谷南坡完成毒爆护航阵，待对方过中线侧翼引爆。",
                    "假扩仅作弱诱导，不抽调前线真实打击资源。",
                    "若诱导失败，立刻放弃欺骗，转纯防守反击脚本。"
                  ],
                  "conclusion": "[空中/打击] 打击结论：弱诱导换窗口，主打侧翼引爆。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【空中】监视蓝方是否提高对假扩的怀疑权重。",
                    "跟踪对方规避落点的行军折线，预判队形拉长点。",
                    "东翼保持覆盖，防止对方改打分矿走廊。"
                  ],
                  "conclusion": "[空中/探测] 探测结论：盯怀疑权重与避爆折线，不丢东翼。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【空中】毒爆居中、刺蛇两翼形成凹字护航，防风筝拆网。",
                    "脚手架姿态不抽空护航，防止被象征性盯梢反制。",
                    "交战中保持凹字完整，优先保核心而非追击。"
                  ],
                  "conclusion": "[空中/防御] 防御结论：凹字护航优先于欺骗动作。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【空中】瓦斯与补给继续喂护航密度，假扩不加建筑投资。",
                    "交战后用损失速率评估是否开启三矿。",
                    "预备下一轮节点保护所需的刺蛇存量。"
                  ],
                  "conclusion": "[空中/保障] 保障结论：资源绑护航；开扩开关=损失评估。"
                }
              },
              "conclusion": "[空中] 总判：用弱诱导 + 强护航换取一次侧翼交战窗口。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 空中切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【空中】继续正面压制，不全线回调防假扩。",
                    "对毒爆落点提前写入规避路线，降低引爆效率。",
                    "寻找护航薄弱翼，准备定点拆网。"
                  ],
                  "conclusion": "[空中/打击] 打击预测：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【空中】已吃过意图误判，对假扩提高怀疑，只派象征性盯梢。",
                    "重点确认毒爆护航阵是否成型。",
                    "记录交战损失，为是否转打节点做信息准备。"
                  ],
                  "conclusion": "[空中/探测] 探测预测：弱盯梢 + 确认真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【空中】避爆折线写入默认行军，接受队形短暂拉长。",
                    "家门口仍偏薄，但本轮不主动加厚。",
                    "拆网单位前置，降低被凹字咬死的概率。"
                  ],
                  "conclusion": "[空中/防御] 防御预测：避爆优先，后院风险暂挂起。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【空中】补给维持突击可持续性，不因假扩信号大挪资源。",
                    "盯梢单位按最低成本配置。",
                    "为脉冲压迫预留重组间隔的补给余量。"
                  ],
                  "conclusion": "[空中/保障] 保障预测：成本可控的持续施压。"
                }
              },
              "conclusion": "[空中] 总判：蓝方不会完全上当，正面压力仍在，拆网意识增强。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·空中 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[空中] 强欺骗收益下降；对方点名拆网，硬打窗口变短。",
                  "oursRead": "弱诱导 + 侧翼引爆。",
                  "enemyRead": "压体系 + 点名拆网。",
                  "revise": "【空中修正】弃强欺骗，主资源留给侧翼窗口，不抽调护航去演戏。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[空中] 对方已提高怀疑权重，假扩信号易被廉价化解。",
                  "oursRead": "盯怀疑权重与避爆折线。",
                  "enemyRead": "弱盯梢 + 确认护航体系。",
                  "revise": "【空中修正】把「怀疑权重」写入欺骗降级开关；东翼覆盖不撤。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[空中] 对方避爆折线会拉长队形，恰给凹字护航侧翼角。",
                  "oursRead": "凹字护航优先。",
                  "enemyRead": "避爆优先，拆网前置。",
                  "revise": "【空中修正】护航完整度 > 追击；用阵型换切入角，而不是用骗术。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[空中] 开扩开关依赖交战评估，对方也在记损失准备转节点。",
                  "oursRead": "资源绑护航；开扩=损失评估。",
                  "enemyRead": "可持续脉冲 + 可抽调余量。",
                  "revise": "【空中修正】保障维只做弱诱导投资；评估环写进开扩门槛。"
                }
              },
              "synthesis": [
                "[空中] 跨维结论：欺骗维降权，护航/打击维升权。",
                "[空中] 优先序：防御凹字 → 打击侧翼 → 探测盯折线 → 保障弱诱导。"
              ],
              "verdict": "[空中内省] 内省结论：弃强欺骗，改「弱诱导 + 强护航」交战脚本。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【空中应对】保障维：脚手架仅作弱信号，不抽调前线。",
                "防御/打击维：毒爆居中、刺蛇两翼凹字，等待对方过峡谷中线。",
                "评估环：交战后立刻看损失速率，决定是否开启三矿。"
              ],
              "conclusion": "[空中·C] 本轮产出约束：凹字护航就绪 · 弱诱导 · 交战后评估开扩。",
              "libraryUpdate": "实时回写（空中）：经验库更新：降级「强欺骗」为失败教训；入库「凹字护航克一字」成功条。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 地面回收护航",
            "constraint": "【实时切片·空中】约束继承自 C₂：凹字护航、弱诱导、交战后评估；前线损失速率已回落。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 空中切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【空中】若蓝方抽调打三矿，放弃峡谷压迫，优先拦截穿插。",
                    "节点护卫圈内可对穿插队实施短打击，不恋战。",
                    "小股牵制对方家门口，降低其抽调自由度。"
                  ],
                  "conclusion": "[空中/打击] 打击结论：护节点反击为主，辅以后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【空中】峡谷保留 Overlord + 少量刺蛇预警抽调迹象。",
                    "监视三矿热点是否已被对方 Overlord 标记。",
                    "跟踪穿插走廊，预设拦截点触发条件。"
                  ],
                  "conclusion": "[空中/探测] 探测结论：预警抽调与标记热点，服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【空中】刺蛇环伺 + 毒爆核心随三矿迁移，形成节点护卫圈。",
                    "峡谷只留轻量防守，避免护航留在旧前线空窗。",
                    "开扩与护航同迁，节点存活绑扩编开关。"
                  ],
                  "conclusion": "[空中/防御] 防御结论：护航随经济节点迁移。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【空中】损失速率低于阈值后开启三矿。",
                    "三矿选址避开已被标记的热点。",
                    "产能节奏服务护卫圈密度，而不是继续堆峡谷。"
                  ],
                  "conclusion": "[空中/保障] 保障结论：开扩阈值明确，选址避热点。"
                }
              },
              "conclusion": "[空中] 总判：经济扩编与节点保护并行。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 空中切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【空中】抽一队穿插威胁脚手架，逼红方护航离场。",
                    "正面维持脉冲压力，防止空场完成开扩。",
                    "穿插受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[空中/打击] 打击预测：节点威胁 + 脉冲牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【空中】确认红方开扩迹象与脚手架位置。",
                    "跟踪护航是否随三矿后撤。",
                    "评估后院被牵制的风险信号。"
                  ],
                  "conclusion": "[空中/探测] 探测预测：盯开扩与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【空中】穿插规模可控，预留回防弹性。",
                    "家门口偏薄被写入风险，避免无回防豪赌。",
                    "正面脉冲缩短暴露，降低被凹字反噬。"
                  ],
                  "conclusion": "[空中/防御] 防御预测：可控穿插，保留回防。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【空中】把流量在「正面脉冲 / 穿插队 / 回防」间再分配。",
                    "不因单次破点抽空补给。",
                    "成功标准可以是「迫使护航离场」而非拆掉节点。"
                  ],
                  "conclusion": "[空中/保障] 保障预测：以机会成本为目标配置兵力。"
                }
              },
              "conclusion": "[空中] 总判：蓝方会把压力从正面部分转移到节点。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·空中 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[空中] 对方转打节点穿插；我方若仍压峡谷，护航会空窗。",
                  "oursRead": "护节点短打 + 后院牵制。",
                  "enemyRead": "穿插威胁脚手架 + 脉冲牵制。",
                  "revise": "【空中修正】打击重心随节点迁移；穿插路径预设拦截，不恋战。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[空中] 开扩与标记热点同期，预警抽调比盯峡谷更值钱。",
                  "oursRead": "预警抽调与热点标记。",
                  "enemyRead": "盯开扩与护航迁移。",
                  "revise": "【空中修正】峡谷降为轻量预警，探测主盯穿插走廊与脚手架。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[空中] 开扩与节点暴露双目标压力；护航若留旧前线必空窗。",
                  "oursRead": "护航随经济节点迁移。",
                  "enemyRead": "可控穿插 + 回防弹性。",
                  "revise": "【空中修正】护卫圈跟着三矿走；峡谷只留薄防。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[空中] 损失回落给出扩编窗口，但选址若落热点会被点名。",
                  "oursRead": "开扩阈值明确，选址避热点。",
                  "enemyRead": "按机会成本配置穿插。",
                  "revise": "【空中修正】开扩绑定护航迁移校验；产能喂护卫圈而非峡谷。"
                }
              },
              "synthesis": [
                "[空中] 跨维结论：扩编可以开，保护必须同迁。",
                "[空中] 优先序：防御同迁 → 保障开扩 → 探测预警 → 打击拦截。"
              ],
              "verdict": "[空中内省] 内省结论：扩编可开，但护航必须跟着经济节点走。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【空中应对】保障/防御维：开启三矿，刺蛇与毒爆核心后撤形成节点护卫圈。",
                "探测维：峡谷保留 Overlord + 少量刺蛇预警。",
                "打击维：对蓝方抽调路径预设拦截点，避免被穿插。"
              ],
              "conclusion": "[空中·C] 本轮沉淀：开扩阈值明确 · 护航随节点迁移 · 形成可入库经验。",
              "libraryUpdate": "实时回写（空中）：经验库更新：入库「开扩与护航同迁」「后院牵制换抽调自由度」两条成功经验。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[空中] 东翼预警必须与峡谷开雾同优先级",
            "outcome": "success",
            "tags": [
              "空中",
              "实时",
              "探测",
              "第1轮",
              "已入库"
            ],
            "summary": "切片空中：仅开中路雾会让对方侧翼先完成意图修正；东翼与峡谷同步覆盖才能保住信息主动权。",
            "learned": "探测任务要双通道并行，不能串行后置。",
            "updateNote": "实时更新·空中 · 更新动作：覆盖旧「先中路后侧翼」条目，写入双通道并行规则。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[空中] 强欺骗在对手已误判一次后收益骤降",
            "outcome": "failure",
            "tags": [
              "空中",
              "实时",
              "打击",
              "意图",
              "第2轮"
            ],
            "summary": "切片空中：对方吃过快扩误判后，假扩只能廉价化解；执着强欺骗会浪费前线资源。",
            "learned": "欺骗等级随对手怀疑权重下调，改为弱诱导。",
            "updateNote": "实时更新·空中 · 更新动作：将强欺骗从推荐脚本降级为慎用，并挂失败标签。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[空中] 凹字护航克一字推进",
            "outcome": "success",
            "tags": [
              "空中",
              "实时",
              "防御",
              "打击",
              "第2轮"
            ],
            "summary": "切片空中：毒爆居中、刺蛇两翼，可利用对方规避落点导致的队形拉长打出侧翼窗口。",
            "learned": "对方拆网意识越强，越要用护航阵型换切入角。",
            "updateNote": "实时更新·空中 · 更新动作：新增护航阵型模板，关联打击维侧翼窗口条件。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[空中] 开扩与护航必须同迁",
            "outcome": "success",
            "tags": [
              "空中",
              "实时",
              "保障",
              "防御",
              "第3轮"
            ],
            "summary": "切片空中：损失速率回落后可开三矿，但护航若留在旧前线，新经济节点会空窗。",
            "learned": "扩编决策绑定节点保护迁移，不单独开扩。",
            "updateNote": "实时更新·空中 · 更新动作：战役开扩门槛增加「护航迁移」校验项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[空中] 家门口偏薄是可利用的对称弱点",
            "outcome": "success",
            "tags": [
              "空中",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片空中：对方把流量堆前线时，小股牵制可降低其抽调打节点的自由度。",
            "learned": "节点保护不一定加兵，也可通过对手后院制造机会成本。",
            "updateNote": "实时更新·空中 · 更新动作：经验库增加「机会成本牵制」条目，供保障/打击维调用。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[空中] 损失速率评估写入开扩门槛",
            "outcome": "success",
            "tags": [
              "空中",
              "实时",
              "保障",
              "评估",
              "已入库"
            ],
            "summary": "切片空中：交战后用损失速率评估，再决定是否开扩，避免凭感觉扩矿。",
            "learned": "把「评估」作为战役开关，而不是事后总结。",
            "updateNote": "实时更新·空中 · 更新动作：保障维开扩规则绑定评估环，替换旧凭感觉阈值。",
            "linkedRoundId": "r2"
          }
        ]
      },
      "blue": {
        "summary": "蓝方对空中骚扰提高防空权重，前线施压节奏被迫脉冲化，经验库写入防空余量门槛。",
        "focus": "实时切片③ · 蓝方观察 · 防空余量校准",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 防空缺口暴露",
            "constraint": "【实时切片·防空】约束前提：双矿稳定、蟑螂线成型、计划以峡谷北口建立压力。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 防空切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【防空】一字推进压缩红方空间，强迫其毒爆过早交战。",
                    "追求前线瞬时伤害差，不在本轮转打经济。",
                    "见毒爆则准备拆网绕行，而非硬接。"
                  ],
                  "conclusion": "[防空/打击] 打击结论：正面高压，逼毒爆提前。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【防空】小狗东侧绕行，验证是否存在快扩。",
                    "把「红方未开三矿」读作防守或反击，设短超时证实。",
                    "侧翼回报用于修正正面目标，不全凭中路。"
                  ],
                  "conclusion": "[防空/探测] 探测结论：侧翼证伪快扩，假设带超时。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【防空】家门口只留薄防御，接受回防成本。",
                    "行军预留毒爆规避折线。",
                    "本轮不加厚后院，标记为下一轮风险。"
                  ],
                  "conclusion": "[防空/防御] 防御结论：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【防空】补给增量投入突击群。",
                    "维持蟑螂比重以支撑持续施压。",
                    "侦察抽调限时，避免前线掉线过久。"
                  ],
                  "conclusion": "[防空/保障] 保障结论：流量堆前线，侦察限时。"
                }
              },
              "conclusion": "[防空] 总判：正面高压 + 侧翼证伪快扩。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 防空切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【防空】可能用毒爆侧翼惩罚一字推进。",
                    "不轻易深推，打击窗口偏后置。",
                    "确认队形宽度前毒爆不交战。"
                  ],
                  "conclusion": "[防空/打击] 打击预测：侧翼待命，后置开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【防空】优先开雾并覆盖峡谷南北口。",
                    "若发现东翼侦察，会补 Overlord 盯分矿走廊。",
                    "跟踪蟑螂队形与补给节奏。"
                  ],
                  "conclusion": "[防空/探测] 探测预测：双通道开雾，防意图被抢先修正。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【防空】高地口布防，守活动空间。",
                    "保持回撤通道，防贴脸拆网。",
                    "家门口维持最低警戒。"
                  ],
                  "conclusion": "[防空/防御] 防御预测：高地骨架防守反击。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【防空】继续点刺蛇科技，暂不开三矿。",
                    "产能服务防守反击而非扩矿。",
                    "瓦斯优先科技完成度。"
                  ],
                  "conclusion": "[防空/保障] 保障预测：科技优先，锁三矿。"
                }
              },
              "conclusion": "[防空] 总判：红方偏防守反击，关键变量是毒爆时机。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·防空 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[防空] 高压有效的前提是毒爆未成型；若假设错误，损失差会反转。",
                  "oursRead": "正面高压逼毒爆提前。",
                  "enemyRead": "侧翼待命、后置开火。",
                  "revise": "【防空修正】打击维保持压力，但把「改打毒爆群」写成超时后的默认切换。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[防空] 快扩假设若无过期，会把兵力锁在错误目标。",
                  "oursRead": "侧翼证伪，假设带超时。",
                  "enemyRead": "双通道开雾，防意图被抢先修正。",
                  "revise": "【防空修正】探测维强制「假设+超时+超时动作」；侧翼要快。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[防空] 薄家门口换流量，被反击时回防成本高。",
                  "oursRead": "薄家门口 + 避爆折线。",
                  "enemyRead": "高地骨架防守反击。",
                  "revise": "【防空修正】本轮可薄，但标记为下一轮风险，不无限加注前线。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[防空] 流量堆前线与侦察抽调争补给。",
                  "oursRead": "补给堆突击，侦察限时。",
                  "enemyRead": "科技优先，锁三矿。",
                  "revise": "【防空修正】侦察抽调限时归队，保障维禁止长期掉线。"
                }
              },
              "synthesis": [
                "[防空] 跨维结论：意图假设必须可证伪，否则打击目标会漂。",
                "[防空] 优先序：探测限时证伪 → 打击可切换目标 → 防御记风险 → 保障限时抽调。"
              ],
              "verdict": "[防空内省] 内省结论：快扩假设设短超时，超时未证实则立刻改打毒爆群。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【防空应对】探测维：小狗侦察限时完成；超时未见到脚手架，则撤销防扩抽调。",
                "打击/防御维：正面保持压力，行军预留毒爆规避折线。",
                "保障维：本轮不加厚家门口，但标记为下一轮风险。"
              ],
              "conclusion": "[防空·C] 本轮产出约束：快扩假设限时 · 正面不停压 · 规避落点。",
              "libraryUpdate": "实时回写（防空）：经验库更新：挂起「无超时意图假设」失败风险；写入限时证伪规则。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 脉冲压缩暴露",
            "constraint": "【实时切片·防空】约束继承自 C₁：快扩假设已超时证伪；红方转为凹字护航。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 防空切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【防空】确认主威胁是毒爆护航阵，持续正面施压。",
                    "风筝拆网降低引爆效率，点名薄弱翼。",
                    "对假扩只派象征性盯梢，不全线回调。"
                  ],
                  "conclusion": "[防空/打击] 打击结论：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【防空】快扩已证伪，转向确认护航阵型与薄弱翼。",
                    "记录交战损失，为转节点做信息准备。",
                    "弱盯梢足够抑制强欺骗。"
                  ],
                  "conclusion": "[防空/探测] 探测结论：目标切换到真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【防空】改脉冲压迫，避免一字队形长时间拉伸。",
                    "拆网单位前置。",
                    "缩短暴露窗口，降低被凹字咬死概率。"
                  ],
                  "conclusion": "[防空/防御] 防御结论：脉冲式降低暴露。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【防空】补给支持短突击—后撤重组节奏。",
                    "盯梢最低成本。",
                    "为是否转打经济节点预留可抽调余量。"
                  ],
                  "conclusion": "[防空/保障] 保障结论：可持续脉冲 + 可抽调余量。"
                }
              },
              "conclusion": "[防空] 总判：对准毒爆体系持续施压。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 防空切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【防空】降级假扩为弱诱导，资源留给侧翼引爆。",
                    "峡谷中线等待过线后开火。",
                    "刺蛇两翼防风筝拆网。"
                  ],
                  "conclusion": "[防空/打击] 打击预测：高质量侧翼窗口。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【防空】监视我方怀疑权重与避爆折线。",
                    "东翼保持覆盖。",
                    "用损失速率评估开扩。"
                  ],
                  "conclusion": "[防空/探测] 探测预测：盯折线与损失评估。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【防空】凹字护航成型，保毒爆核心。",
                    "弱诱导不抽空护航。",
                    "交战中优先保阵型。"
                  ],
                  "conclusion": "[防空/防御] 防御预测：强护航优先。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【防空】资源绑护航密度。",
                    "交战后评估是否开三矿。",
                    "假扩不加建筑投资。"
                  ],
                  "conclusion": "[防空/保障] 保障预测：护航优先于欺骗投资。"
                }
              },
              "conclusion": "[防空] 总判：红方准备打一次高质量侧翼窗口。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·防空 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[防空] 连续一字 + 避爆折线拉长队形，正好喂给凹字侧翼。",
                  "oursRead": "压体系 + 点名拆网。",
                  "enemyRead": "高质量侧翼窗口。",
                  "revise": "【防空修正】改脉冲压迫，缩短暴露；拆网前置点名薄弱翼。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[防空] 快扩已证伪，继续防扩是浪费。",
                  "oursRead": "目标切换到真实体系。",
                  "enemyRead": "盯折线与损失评估。",
                  "revise": "【防空修正】探测结论硬绑定打击关键目标：对准护航体系。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[防空] 过线过深会被凹字最大化惩罚。",
                  "oursRead": "脉冲式降低暴露。",
                  "enemyRead": "强护航优先。",
                  "revise": "【防空修正】短突击—后撤重组，不在中线长时间拉伸。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[防空] 要为转节点预留抽调余量，又要撑住脉冲节奏。",
                  "oursRead": "可持续脉冲 + 可抽调余量。",
                  "enemyRead": "护航优先于欺骗投资。",
                  "revise": "【防空修正】补给服务脉冲间隔；记下损失，准备转节点。"
                }
              },
              "synthesis": [
                "[防空] 跨维结论：证伪后必须改打真实体系，阵型从连续改为脉冲。",
                "[防空] 优先序：探测改目标 → 打击脉冲拆网 → 防御控暴露 → 保障留余量。"
              ],
              "verdict": "[防空内省] 内省结论：改脉冲压迫，缩短队形暴露时间。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【防空应对】打击维：以短突击—后撤重组代替连续一字推进。",
                "打击维：拆网单位前置，护航薄弱处优先点名。",
                "保障/探测维：记录交战损失，为是否转打经济节点做准备。"
              ],
              "conclusion": "[防空·C] 本轮产出约束：脉冲压迫 · 点名拆网 · 准备转节点。",
              "libraryUpdate": "实时回写（防空）：经验库更新：入库「证伪后改打真实体系」；将连续一字推进标为失败模式。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 防空与穿插并行",
            "constraint": "【实时切片·防空】约束继承自 C₂：脉冲压迫与点名拆网；观察到红方损失回落并有开扩迹象。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 防空切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【防空】抽一队穿插威胁脚手架，逼护航离场。",
                    "正面脉冲不停，防止空场开扩。",
                    "受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[防空/打击] 打击结论：节点威胁制造机会成本。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【防空】确认开扩迹象与脚手架位置。",
                    "跟踪护航是否后撤。",
                    "评估后院牵制风险。"
                  ],
                  "conclusion": "[防空/探测] 探测结论：盯节点与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【防空】穿插规模可控，预留回防。",
                    "家门口薄弱写入风险清单。",
                    "脉冲缩短暴露。"
                  ],
                  "conclusion": "[防空/防御] 防御结论：可控穿插 + 回防弹性。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【防空】流量在脉冲/穿插/回防间再分配。",
                    "成功标准可以是迫使护航离场。",
                    "把节点转移结果写入经验库。"
                  ],
                  "conclusion": "[防空/保障] 保障结论：按机会成本配置，不赌单次破点。"
                }
              },
              "conclusion": "[防空] 总判：节点转移压力，扩大对方决策负担。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 防空切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【防空】对穿插设拦截点，短打不恋战。",
                    "小股牵制我方家门口。",
                    "放弃峡谷压迫以保节点时也可接受。"
                  ],
                  "conclusion": "[防空/打击] 打击预测：拦截 + 后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【防空】峡谷留预警抽调。",
                    "监视脚手架是否被标记。",
                    "跟踪穿插走廊。"
                  ],
                  "conclusion": "[防空/探测] 探测预测：预警服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【防空】护航随三矿迁移。",
                    "峡谷变薄但节点护卫圈加厚。",
                    "开扩与节点存活绑定。"
                  ],
                  "conclusion": "[防空/防御] 防御预测：护航同迁。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【防空】损失回落后开三矿。",
                    "选址避热点。",
                    "产能服务护卫圈。"
                  ],
                  "conclusion": "[防空/保障] 保障预测：扩编绑保护。"
                }
              },
              "conclusion": "[防空] 总判：红方会用护航迁移 + 后院牵制对冲节点压力。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·防空 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[防空] 节点威胁能逼护航离场，但硬闯可能得不偿失。",
                  "oursRead": "节点威胁制造机会成本。",
                  "enemyRead": "拦截 + 后院牵制。",
                  "revise": "【防空修正】可控穿插威胁脚手架；受阻退回拆网，不赌单次破点。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[防空] 要同时看开扩、护航迁移与后院牵制风险。",
                  "oursRead": "盯节点与护航迁移。",
                  "enemyRead": "预警服务开扩安全。",
                  "revise": "【防空修正】探测清单三件套：脚手架、护航位移、后院威胁。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[防空] 抽调打节点暴露家门口薄弱，对手可能牵制反噬。",
                  "oursRead": "可控穿插 + 回防弹性。",
                  "enemyRead": "护航同迁。",
                  "revise": "【防空修正】穿插规模设上限，回防余量必留。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[防空] 成功标准应是改对方资源配置，而非拆掉节点。",
                  "oursRead": "按机会成本配置。",
                  "enemyRead": "扩编绑保护。",
                  "revise": "【防空修正】流量在脉冲/穿插/回防间再分配；结果写入经验库。"
                }
              },
              "synthesis": [
                "[防空] 跨维结论：节点战目标是机会成本，不是豪赌破点。",
                "[防空] 优先序：保障控规模 → 防御留回防 → 打击威胁节点 → 探测盯三信号。"
              ],
              "verdict": "[防空内省] 内省结论：小规模穿插制造机会成本，而不是赌单次破点。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【防空应对】打击/防御维：派出可控穿插队威胁脚手架，同时预留回防。",
                "打击维：正面脉冲不停，阻止红方空场发育。",
                "保障维：把本轮「节点转移」成败写入经验库。"
              ],
              "conclusion": "[防空·C] 本轮沉淀：压力可从正面迁移到节点 · 抽调必须保留回防弹性。",
              "libraryUpdate": "实时回写（防空）：经验库更新：入库「节点压力逼护航离场」「抽调保留回防」两条。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[防空] 意图假设必须带超时",
            "outcome": "failure",
            "tags": [
              "防空",
              "实时",
              "探测",
              "意图",
              "第1轮"
            ],
            "summary": "切片防空：快扩假设若无过期时间，会把前线兵力锁在错误目标上。",
            "learned": "每个意图假设写明证实条件与超时动作。",
            "updateNote": "实时更新·防空 · 更新动作：探测维规则强制「假设+超时+超时动作」三元组。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[防空] 证伪后立刻改打真实体系",
            "outcome": "success",
            "tags": [
              "防空",
              "实时",
              "打击",
              "目标切换",
              "第2轮"
            ],
            "summary": "切片防空：快扩证伪后，压力应转向毒爆护航体系，而不是继续防扩。",
            "learned": "意图修正要同步改关键目标，不能只改口头判断。",
            "updateNote": "实时更新·防空 · 更新动作：打击维关键目标与探测维结论硬绑定。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[防空] 连续一字推进会被凹字护航惩罚",
            "outcome": "failure",
            "tags": [
              "防空",
              "实时",
              "打击",
              "防御",
              "已修订"
            ],
            "summary": "切片防空：避爆折线拉长队形，恰好给侧翼引爆角度。",
            "learned": "改脉冲压迫，控制队形暴露时长。",
            "updateNote": "实时更新·防空 · 更新动作：将连续一字推进标为禁用模板，替换为脉冲脚本。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[防空] 节点压力可逼护航离场",
            "outcome": "success",
            "tags": [
              "防空",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片防空：威胁脚手架足以改变对方护航重心，即使未必拆掉节点。",
            "learned": "节点战的目标可以是「改对方资源配置」。",
            "updateNote": "实时更新·防空 · 更新动作：战役目标库增加「机会成本」成功判据。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[防空] 抽调必须保留回防弹性",
            "outcome": "success",
            "tags": [
              "防空",
              "实时",
              "防御",
              "保障",
              "第3轮"
            ],
            "summary": "切片防空：家门口偏薄时，无回防的穿插会被后院牵制反噬。",
            "learned": "穿插规模可控，先制造机会成本。",
            "updateNote": "实时更新·防空 · 更新动作：保障维抽调清单增加「回防余量」必填项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[防空] 象征性盯梢足以抑制强欺骗",
            "outcome": "success",
            "tags": [
              "防空",
              "实时",
              "探测",
              "信息",
              "已入库"
            ],
            "summary": "切片防空：对手降级假扩后，少量盯梢即可，不必全线回调。",
            "learned": "信息对抗也要算成本，防骗不等于重兵。",
            "updateNote": "实时更新·防空 · 更新动作：探测维防骗任务默认最低成本配置。",
            "linkedRoundId": "r2"
          }
        ]
      }
    }
  },
  {
    "id": "slice-4",
    "label": "切片④ · 经济竞赛",
    "stamp": "帧窗 D",
    "cases": {
      "red": {
        "summary": "红方把保障维抬到主决策：损失速率评估直接绑定三矿开关，护航必须与经济节点同迁。",
        "focus": "实时切片④ · 红方观察 · 开扩阈值竞赛",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 锁扩换科技",
            "constraint": "【实时切片·经济】约束前提：双矿运转、中路视野刚开、蓝方蟑螂比重上升。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 经济切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【经济】暂不主动深推，打击重心放在高地口牵制面，等待队形宽度确认。",
                    "毒爆集群后置待命，避免过早暴露落点被风筝拆网。",
                    "若蓝方一字过峡谷中线，再以侧翼切入打前排。"
                  ],
                  "conclusion": "[经济/打击] 打击结论：不深推，侧翼窗口待确认后开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【经济】双 Overlord 交错覆盖峡谷南北口，优先获取蟑螂队形与补给节奏。",
                    "东翼分矿走廊与中路开雾同优先级，防小狗先完成意图修正。",
                    "跟踪蓝方补给抬升，作为是否转入交战的信息门槛。"
                  ],
                  "conclusion": "[经济/探测] 探测结论：双通道开雾，信息主动权优先于交战。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【经济】B2 高地口预留刺蛇防守骨架，守住活动空间不被压缩。",
                    "主矿与前线之间保留回撤通道，防止被贴脸拆网。",
                    "家门口维持最低警戒，不因前线压力抽空。"
                  ],
                  "conclusion": "[经济/防御] 防御结论：高地骨架 + 回撤通道，守而不塌。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【经济】工蜂曲线维持，暂缓三矿，瓦斯优先刺蛇穴完成度。",
                    "补给与科技节点绑定前线损失速率，不盲目扩编。",
                    "毒爆产能按「待命规模」而非「立刻交战规模」配置。"
                  ],
                  "conclusion": "[经济/保障] 保障结论：科技与产能服务防守反击，不开三矿。"
                }
              },
              "conclusion": "[经济] 总判：侦察开雾 + 高地防守姿态，不主动深推。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 经济切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【经济】主力沿峡谷北坡持续正面施压，追求瞬时伤害差。",
                    "若红方毒爆现身，改蟑螂风筝拆网并绕开落点。",
                    "把流量集中前线，不在本轮分散打经济。"
                  ],
                  "conclusion": "[经济/打击] 打击预测：正面不停压，见毒爆则拆网绕行。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【经济】小狗东侧绕行确认分矿，验证「快扩/假守」假设。",
                    "用侧翼回报修正正面意图，不全凭中路观察。",
                    "对红方未扩三矿提高「防守反击」权重。"
                  ],
                  "conclusion": "[经济/探测] 探测预测：侧翼证伪快扩，再决定是否改目标。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【经济】家门口只留薄刺蛇线，接受回防成本换前线流量。",
                    "行军预留避爆折线，降低被侧翼惩罚概率。",
                    "不主动加厚后院，把风险留到下一轮评估。"
                  ],
                  "conclusion": "[经济/防御] 防御预测：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【经济】补给增量优先投入突击群，而非工蜂或三矿。",
                    "维持蟑螂比重，保证正面可持续施压。",
                    "侦察小狗按「限时任务」抽调，避免长期掉线。"
                  ],
                  "conclusion": "[经济/保障] 保障预测：流量堆前线，侦察限时抽调。"
                }
              },
              "conclusion": "[经济] 总判：蓝方会用侧翼侦察修正意图，正面不轻易撤压。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·经济 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[经济] 我方后置开火，对方持续正面施压——时间窗错位，硬接会亏。",
                  "oursRead": "侧翼待命、确认宽度后再打。",
                  "enemyRead": "不停压 + 见毒爆拆网绕行。",
                  "revise": "【经济修正】打击维保持待命，不抢先交战；窗口条件写成「队形宽度确认」。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[经济] 我方偏中路开雾，对方侧翼小狗可能先完成意图修正。",
                  "oursRead": "双通道开雾，信息优先于交战。",
                  "enemyRead": "东侧绕行证伪快扩。",
                  "revise": "【经济修正】东翼与峡谷同优先级，探测维提前于打击维闭环。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[经济] 高地骨架可用，但对方薄家门口暴露出对称机会，我方尚未利用。",
                  "oursRead": "高地骨架 + 回撤通道。",
                  "enemyRead": "薄家门口换前线流量。",
                  "revise": "【经济修正】守骨架不塌；下一拍可用小股牵制压对方回防成本。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[经济] 我方锁三矿喂科技，对方堆前线补给——经济节奏不同步。",
                  "oursRead": "科技优先，不开三矿。",
                  "enemyRead": "补给堆突击，侦察限时抽调。",
                  "revise": "【经济修正】保障维继续锁扩，产能按待命规模配置，避免被拖进消耗战。"
                }
              },
              "synthesis": [
                "[经济] 跨维结论：信息主动权（探测）决定打击窗口是否成立。",
                "[经济] 优先序：探测补东翼 → 防御守骨架 → 打击待命 → 保障锁扩。"
              ],
              "verdict": "[经济内省] 内省结论：保持防守骨架，必须补东翼预警，否则下一轮丢信息主动权。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【经济应对】探测维：立即补一只 Overlord 偏东悬停，专盯分矿走廊。",
                "防御/打击维：峡谷南口刺蛇线不变，毒爆后置到确认宽度后再侧翼切入。",
                "保障维：瓦斯继续点刺蛇穴，三矿仍锁，等待前线损失速率稳定。"
              ],
              "conclusion": "[经济·C] 本轮产出约束：东翼已覆盖 · 不深推 · 毒爆待命。",
              "libraryUpdate": "实时回写（经济）：经验库更新：写入「双通道探测并行」成功条；标注东翼后置为待规避失败模式。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 评估环写入门槛",
            "constraint": "【实时切片·经济】约束继承自 C₁：东翼已覆盖、不深推、毒爆待命；蓝方侧翼侦察回报「红未真扩」。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 经济切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【经济】真实意图：峡谷南坡完成毒爆护航阵，待对方过中线侧翼引爆。",
                    "假扩仅作弱诱导，不抽调前线真实打击资源。",
                    "若诱导失败，立刻放弃欺骗，转纯防守反击脚本。"
                  ],
                  "conclusion": "[经济/打击] 打击结论：弱诱导换窗口，主打侧翼引爆。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【经济】监视蓝方是否提高对假扩的怀疑权重。",
                    "跟踪对方规避落点的行军折线，预判队形拉长点。",
                    "东翼保持覆盖，防止对方改打分矿走廊。"
                  ],
                  "conclusion": "[经济/探测] 探测结论：盯怀疑权重与避爆折线，不丢东翼。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【经济】毒爆居中、刺蛇两翼形成凹字护航，防风筝拆网。",
                    "脚手架姿态不抽空护航，防止被象征性盯梢反制。",
                    "交战中保持凹字完整，优先保核心而非追击。"
                  ],
                  "conclusion": "[经济/防御] 防御结论：凹字护航优先于欺骗动作。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【经济】瓦斯与补给继续喂护航密度，假扩不加建筑投资。",
                    "交战后用损失速率评估是否开启三矿。",
                    "预备下一轮节点保护所需的刺蛇存量。"
                  ],
                  "conclusion": "[经济/保障] 保障结论：资源绑护航；开扩开关=损失评估。"
                }
              },
              "conclusion": "[经济] 总判：用弱诱导 + 强护航换取一次侧翼交战窗口。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 经济切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【经济】继续正面压制，不全线回调防假扩。",
                    "对毒爆落点提前写入规避路线，降低引爆效率。",
                    "寻找护航薄弱翼，准备定点拆网。"
                  ],
                  "conclusion": "[经济/打击] 打击预测：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【经济】已吃过意图误判，对假扩提高怀疑，只派象征性盯梢。",
                    "重点确认毒爆护航阵是否成型。",
                    "记录交战损失，为是否转打节点做信息准备。"
                  ],
                  "conclusion": "[经济/探测] 探测预测：弱盯梢 + 确认真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【经济】避爆折线写入默认行军，接受队形短暂拉长。",
                    "家门口仍偏薄，但本轮不主动加厚。",
                    "拆网单位前置，降低被凹字咬死的概率。"
                  ],
                  "conclusion": "[经济/防御] 防御预测：避爆优先，后院风险暂挂起。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【经济】补给维持突击可持续性，不因假扩信号大挪资源。",
                    "盯梢单位按最低成本配置。",
                    "为脉冲压迫预留重组间隔的补给余量。"
                  ],
                  "conclusion": "[经济/保障] 保障预测：成本可控的持续施压。"
                }
              },
              "conclusion": "[经济] 总判：蓝方不会完全上当，正面压力仍在，拆网意识增强。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·经济 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[经济] 强欺骗收益下降；对方点名拆网，硬打窗口变短。",
                  "oursRead": "弱诱导 + 侧翼引爆。",
                  "enemyRead": "压体系 + 点名拆网。",
                  "revise": "【经济修正】弃强欺骗，主资源留给侧翼窗口，不抽调护航去演戏。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[经济] 对方已提高怀疑权重，假扩信号易被廉价化解。",
                  "oursRead": "盯怀疑权重与避爆折线。",
                  "enemyRead": "弱盯梢 + 确认护航体系。",
                  "revise": "【经济修正】把「怀疑权重」写入欺骗降级开关；东翼覆盖不撤。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[经济] 对方避爆折线会拉长队形，恰给凹字护航侧翼角。",
                  "oursRead": "凹字护航优先。",
                  "enemyRead": "避爆优先，拆网前置。",
                  "revise": "【经济修正】护航完整度 > 追击；用阵型换切入角，而不是用骗术。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[经济] 开扩开关依赖交战评估，对方也在记损失准备转节点。",
                  "oursRead": "资源绑护航；开扩=损失评估。",
                  "enemyRead": "可持续脉冲 + 可抽调余量。",
                  "revise": "【经济修正】保障维只做弱诱导投资；评估环写进开扩门槛。"
                }
              },
              "synthesis": [
                "[经济] 跨维结论：欺骗维降权，护航/打击维升权。",
                "[经济] 优先序：防御凹字 → 打击侧翼 → 探测盯折线 → 保障弱诱导。"
              ],
              "verdict": "[经济内省] 内省结论：弃强欺骗，改「弱诱导 + 强护航」交战脚本。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【经济应对】保障维：脚手架仅作弱信号，不抽调前线。",
                "防御/打击维：毒爆居中、刺蛇两翼凹字，等待对方过峡谷中线。",
                "评估环：交战后立刻看损失速率，决定是否开启三矿。"
              ],
              "conclusion": "[经济·C] 本轮产出约束：凹字护航就绪 · 弱诱导 · 交战后评估开扩。",
              "libraryUpdate": "实时回写（经济）：经验库更新：降级「强欺骗」为失败教训；入库「凹字护航克一字」成功条。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 三矿与护航同迁",
            "constraint": "【实时切片·经济】约束继承自 C₂：凹字护航、弱诱导、交战后评估；前线损失速率已回落。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 经济切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【经济】若蓝方抽调打三矿，放弃峡谷压迫，优先拦截穿插。",
                    "节点护卫圈内可对穿插队实施短打击，不恋战。",
                    "小股牵制对方家门口，降低其抽调自由度。"
                  ],
                  "conclusion": "[经济/打击] 打击结论：护节点反击为主，辅以后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【经济】峡谷保留 Overlord + 少量刺蛇预警抽调迹象。",
                    "监视三矿热点是否已被对方 Overlord 标记。",
                    "跟踪穿插走廊，预设拦截点触发条件。"
                  ],
                  "conclusion": "[经济/探测] 探测结论：预警抽调与标记热点，服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【经济】刺蛇环伺 + 毒爆核心随三矿迁移，形成节点护卫圈。",
                    "峡谷只留轻量防守，避免护航留在旧前线空窗。",
                    "开扩与护航同迁，节点存活绑扩编开关。"
                  ],
                  "conclusion": "[经济/防御] 防御结论：护航随经济节点迁移。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【经济】损失速率低于阈值后开启三矿。",
                    "三矿选址避开已被标记的热点。",
                    "产能节奏服务护卫圈密度，而不是继续堆峡谷。"
                  ],
                  "conclusion": "[经济/保障] 保障结论：开扩阈值明确，选址避热点。"
                }
              },
              "conclusion": "[经济] 总判：经济扩编与节点保护并行。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 经济切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【经济】抽一队穿插威胁脚手架，逼红方护航离场。",
                    "正面维持脉冲压力，防止空场完成开扩。",
                    "穿插受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[经济/打击] 打击预测：节点威胁 + 脉冲牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【经济】确认红方开扩迹象与脚手架位置。",
                    "跟踪护航是否随三矿后撤。",
                    "评估后院被牵制的风险信号。"
                  ],
                  "conclusion": "[经济/探测] 探测预测：盯开扩与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【经济】穿插规模可控，预留回防弹性。",
                    "家门口偏薄被写入风险，避免无回防豪赌。",
                    "正面脉冲缩短暴露，降低被凹字反噬。"
                  ],
                  "conclusion": "[经济/防御] 防御预测：可控穿插，保留回防。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【经济】把流量在「正面脉冲 / 穿插队 / 回防」间再分配。",
                    "不因单次破点抽空补给。",
                    "成功标准可以是「迫使护航离场」而非拆掉节点。"
                  ],
                  "conclusion": "[经济/保障] 保障预测：以机会成本为目标配置兵力。"
                }
              },
              "conclusion": "[经济] 总判：蓝方会把压力从正面部分转移到节点。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·经济 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[经济] 对方转打节点穿插；我方若仍压峡谷，护航会空窗。",
                  "oursRead": "护节点短打 + 后院牵制。",
                  "enemyRead": "穿插威胁脚手架 + 脉冲牵制。",
                  "revise": "【经济修正】打击重心随节点迁移；穿插路径预设拦截，不恋战。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[经济] 开扩与标记热点同期，预警抽调比盯峡谷更值钱。",
                  "oursRead": "预警抽调与热点标记。",
                  "enemyRead": "盯开扩与护航迁移。",
                  "revise": "【经济修正】峡谷降为轻量预警，探测主盯穿插走廊与脚手架。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[经济] 开扩与节点暴露双目标压力；护航若留旧前线必空窗。",
                  "oursRead": "护航随经济节点迁移。",
                  "enemyRead": "可控穿插 + 回防弹性。",
                  "revise": "【经济修正】护卫圈跟着三矿走；峡谷只留薄防。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[经济] 损失回落给出扩编窗口，但选址若落热点会被点名。",
                  "oursRead": "开扩阈值明确，选址避热点。",
                  "enemyRead": "按机会成本配置穿插。",
                  "revise": "【经济修正】开扩绑定护航迁移校验；产能喂护卫圈而非峡谷。"
                }
              },
              "synthesis": [
                "[经济] 跨维结论：扩编可以开，保护必须同迁。",
                "[经济] 优先序：防御同迁 → 保障开扩 → 探测预警 → 打击拦截。"
              ],
              "verdict": "[经济内省] 内省结论：扩编可开，但护航必须跟着经济节点走。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【经济应对】保障/防御维：开启三矿，刺蛇与毒爆核心后撤形成节点护卫圈。",
                "探测维：峡谷保留 Overlord + 少量刺蛇预警。",
                "打击维：对蓝方抽调路径预设拦截点，避免被穿插。"
              ],
              "conclusion": "[经济·C] 本轮沉淀：开扩阈值明确 · 护航随节点迁移 · 形成可入库经验。",
              "libraryUpdate": "实时回写（经济）：经验库更新：入库「开扩与护航同迁」「后院牵制换抽调自由度」两条成功经验。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[经济] 东翼预警必须与峡谷开雾同优先级",
            "outcome": "success",
            "tags": [
              "经济",
              "实时",
              "探测",
              "第1轮",
              "已入库"
            ],
            "summary": "切片经济：仅开中路雾会让对方侧翼先完成意图修正；东翼与峡谷同步覆盖才能保住信息主动权。",
            "learned": "探测任务要双通道并行，不能串行后置。",
            "updateNote": "实时更新·经济 · 更新动作：覆盖旧「先中路后侧翼」条目，写入双通道并行规则。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[经济] 强欺骗在对手已误判一次后收益骤降",
            "outcome": "failure",
            "tags": [
              "经济",
              "实时",
              "打击",
              "意图",
              "第2轮"
            ],
            "summary": "切片经济：对方吃过快扩误判后，假扩只能廉价化解；执着强欺骗会浪费前线资源。",
            "learned": "欺骗等级随对手怀疑权重下调，改为弱诱导。",
            "updateNote": "实时更新·经济 · 更新动作：将强欺骗从推荐脚本降级为慎用，并挂失败标签。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[经济] 凹字护航克一字推进",
            "outcome": "success",
            "tags": [
              "经济",
              "实时",
              "防御",
              "打击",
              "第2轮"
            ],
            "summary": "切片经济：毒爆居中、刺蛇两翼，可利用对方规避落点导致的队形拉长打出侧翼窗口。",
            "learned": "对方拆网意识越强，越要用护航阵型换切入角。",
            "updateNote": "实时更新·经济 · 更新动作：新增护航阵型模板，关联打击维侧翼窗口条件。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[经济] 开扩与护航必须同迁",
            "outcome": "success",
            "tags": [
              "经济",
              "实时",
              "保障",
              "防御",
              "第3轮"
            ],
            "summary": "切片经济：损失速率回落后可开三矿，但护航若留在旧前线，新经济节点会空窗。",
            "learned": "扩编决策绑定节点保护迁移，不单独开扩。",
            "updateNote": "实时更新·经济 · 更新动作：战役开扩门槛增加「护航迁移」校验项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[经济] 家门口偏薄是可利用的对称弱点",
            "outcome": "success",
            "tags": [
              "经济",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片经济：对方把流量堆前线时，小股牵制可降低其抽调打节点的自由度。",
            "learned": "节点保护不一定加兵，也可通过对手后院制造机会成本。",
            "updateNote": "实时更新·经济 · 更新动作：经验库增加「机会成本牵制」条目，供保障/打击维调用。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[经济] 损失速率评估写入开扩门槛",
            "outcome": "success",
            "tags": [
              "经济",
              "实时",
              "保障",
              "评估",
              "已入库"
            ],
            "summary": "切片经济：交战后用损失速率评估，再决定是否开扩，避免凭感觉扩矿。",
            "learned": "把「评估」作为战役开关，而不是事后总结。",
            "updateNote": "实时更新·经济 · 更新动作：保障维开扩规则绑定评估环，替换旧凭感觉阈值。",
            "linkedRoundId": "r2"
          }
        ]
      },
      "blue": {
        "summary": "蓝方试图用节点威胁打断红方开扩曲线，穿插规模受回防弹性约束，经验库强调机会成本目标。",
        "focus": "实时切片④ · 蓝方观察 · 打断开扩曲线",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 侦测开扩信号",
            "constraint": "【实时切片·打断】约束前提：双矿稳定、蟑螂线成型、计划以峡谷北口建立压力。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 打断切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【打断】一字推进压缩红方空间，强迫其毒爆过早交战。",
                    "追求前线瞬时伤害差，不在本轮转打经济。",
                    "见毒爆则准备拆网绕行，而非硬接。"
                  ],
                  "conclusion": "[打断/打击] 打击结论：正面高压，逼毒爆提前。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【打断】小狗东侧绕行，验证是否存在快扩。",
                    "把「红方未开三矿」读作防守或反击，设短超时证实。",
                    "侧翼回报用于修正正面目标，不全凭中路。"
                  ],
                  "conclusion": "[打断/探测] 探测结论：侧翼证伪快扩，假设带超时。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【打断】家门口只留薄防御，接受回防成本。",
                    "行军预留毒爆规避折线。",
                    "本轮不加厚后院，标记为下一轮风险。"
                  ],
                  "conclusion": "[打断/防御] 防御结论：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【打断】补给增量投入突击群。",
                    "维持蟑螂比重以支撑持续施压。",
                    "侦察抽调限时，避免前线掉线过久。"
                  ],
                  "conclusion": "[打断/保障] 保障结论：流量堆前线，侦察限时。"
                }
              },
              "conclusion": "[打断] 总判：正面高压 + 侧翼证伪快扩。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 打断切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【打断】可能用毒爆侧翼惩罚一字推进。",
                    "不轻易深推，打击窗口偏后置。",
                    "确认队形宽度前毒爆不交战。"
                  ],
                  "conclusion": "[打断/打击] 打击预测：侧翼待命，后置开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【打断】优先开雾并覆盖峡谷南北口。",
                    "若发现东翼侦察，会补 Overlord 盯分矿走廊。",
                    "跟踪蟑螂队形与补给节奏。"
                  ],
                  "conclusion": "[打断/探测] 探测预测：双通道开雾，防意图被抢先修正。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【打断】高地口布防，守活动空间。",
                    "保持回撤通道，防贴脸拆网。",
                    "家门口维持最低警戒。"
                  ],
                  "conclusion": "[打断/防御] 防御预测：高地骨架防守反击。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【打断】继续点刺蛇科技，暂不开三矿。",
                    "产能服务防守反击而非扩矿。",
                    "瓦斯优先科技完成度。"
                  ],
                  "conclusion": "[打断/保障] 保障预测：科技优先，锁三矿。"
                }
              },
              "conclusion": "[打断] 总判：红方偏防守反击，关键变量是毒爆时机。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·打断 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[打断] 高压有效的前提是毒爆未成型；若假设错误，损失差会反转。",
                  "oursRead": "正面高压逼毒爆提前。",
                  "enemyRead": "侧翼待命、后置开火。",
                  "revise": "【打断修正】打击维保持压力，但把「改打毒爆群」写成超时后的默认切换。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[打断] 快扩假设若无过期，会把兵力锁在错误目标。",
                  "oursRead": "侧翼证伪，假设带超时。",
                  "enemyRead": "双通道开雾，防意图被抢先修正。",
                  "revise": "【打断修正】探测维强制「假设+超时+超时动作」；侧翼要快。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[打断] 薄家门口换流量，被反击时回防成本高。",
                  "oursRead": "薄家门口 + 避爆折线。",
                  "enemyRead": "高地骨架防守反击。",
                  "revise": "【打断修正】本轮可薄，但标记为下一轮风险，不无限加注前线。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[打断] 流量堆前线与侦察抽调争补给。",
                  "oursRead": "补给堆突击，侦察限时。",
                  "enemyRead": "科技优先，锁三矿。",
                  "revise": "【打断修正】侦察抽调限时归队，保障维禁止长期掉线。"
                }
              },
              "synthesis": [
                "[打断] 跨维结论：意图假设必须可证伪，否则打击目标会漂。",
                "[打断] 优先序：探测限时证伪 → 打击可切换目标 → 防御记风险 → 保障限时抽调。"
              ],
              "verdict": "[打断内省] 内省结论：快扩假设设短超时，超时未证实则立刻改打毒爆群。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【打断应对】探测维：小狗侦察限时完成；超时未见到脚手架，则撤销防扩抽调。",
                "打击/防御维：正面保持压力，行军预留毒爆规避折线。",
                "保障维：本轮不加厚家门口，但标记为下一轮风险。"
              ],
              "conclusion": "[打断·C] 本轮产出约束：快扩假设限时 · 正面不停压 · 规避落点。",
              "libraryUpdate": "实时回写（打断）：经验库更新：挂起「无超时意图假设」失败风险；写入限时证伪规则。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 脉冲制造窗口",
            "constraint": "【实时切片·打断】约束继承自 C₁：快扩假设已超时证伪；红方转为凹字护航。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 打断切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【打断】确认主威胁是毒爆护航阵，持续正面施压。",
                    "风筝拆网降低引爆效率，点名薄弱翼。",
                    "对假扩只派象征性盯梢，不全线回调。"
                  ],
                  "conclusion": "[打断/打击] 打击结论：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【打断】快扩已证伪，转向确认护航阵型与薄弱翼。",
                    "记录交战损失，为转节点做信息准备。",
                    "弱盯梢足够抑制强欺骗。"
                  ],
                  "conclusion": "[打断/探测] 探测结论：目标切换到真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【打断】改脉冲压迫，避免一字队形长时间拉伸。",
                    "拆网单位前置。",
                    "缩短暴露窗口，降低被凹字咬死概率。"
                  ],
                  "conclusion": "[打断/防御] 防御结论：脉冲式降低暴露。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【打断】补给支持短突击—后撤重组节奏。",
                    "盯梢最低成本。",
                    "为是否转打经济节点预留可抽调余量。"
                  ],
                  "conclusion": "[打断/保障] 保障结论：可持续脉冲 + 可抽调余量。"
                }
              },
              "conclusion": "[打断] 总判：对准毒爆体系持续施压。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 打断切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【打断】降级假扩为弱诱导，资源留给侧翼引爆。",
                    "峡谷中线等待过线后开火。",
                    "刺蛇两翼防风筝拆网。"
                  ],
                  "conclusion": "[打断/打击] 打击预测：高质量侧翼窗口。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【打断】监视我方怀疑权重与避爆折线。",
                    "东翼保持覆盖。",
                    "用损失速率评估开扩。"
                  ],
                  "conclusion": "[打断/探测] 探测预测：盯折线与损失评估。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【打断】凹字护航成型，保毒爆核心。",
                    "弱诱导不抽空护航。",
                    "交战中优先保阵型。"
                  ],
                  "conclusion": "[打断/防御] 防御预测：强护航优先。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【打断】资源绑护航密度。",
                    "交战后评估是否开三矿。",
                    "假扩不加建筑投资。"
                  ],
                  "conclusion": "[打断/保障] 保障预测：护航优先于欺骗投资。"
                }
              },
              "conclusion": "[打断] 总判：红方准备打一次高质量侧翼窗口。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·打断 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[打断] 连续一字 + 避爆折线拉长队形，正好喂给凹字侧翼。",
                  "oursRead": "压体系 + 点名拆网。",
                  "enemyRead": "高质量侧翼窗口。",
                  "revise": "【打断修正】改脉冲压迫，缩短暴露；拆网前置点名薄弱翼。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[打断] 快扩已证伪，继续防扩是浪费。",
                  "oursRead": "目标切换到真实体系。",
                  "enemyRead": "盯折线与损失评估。",
                  "revise": "【打断修正】探测结论硬绑定打击关键目标：对准护航体系。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[打断] 过线过深会被凹字最大化惩罚。",
                  "oursRead": "脉冲式降低暴露。",
                  "enemyRead": "强护航优先。",
                  "revise": "【打断修正】短突击—后撤重组，不在中线长时间拉伸。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[打断] 要为转节点预留抽调余量，又要撑住脉冲节奏。",
                  "oursRead": "可持续脉冲 + 可抽调余量。",
                  "enemyRead": "护航优先于欺骗投资。",
                  "revise": "【打断修正】补给服务脉冲间隔；记下损失，准备转节点。"
                }
              },
              "synthesis": [
                "[打断] 跨维结论：证伪后必须改打真实体系，阵型从连续改为脉冲。",
                "[打断] 优先序：探测改目标 → 打击脉冲拆网 → 防御控暴露 → 保障留余量。"
              ],
              "verdict": "[打断内省] 内省结论：改脉冲压迫，缩短队形暴露时间。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【打断应对】打击维：以短突击—后撤重组代替连续一字推进。",
                "打击维：拆网单位前置，护航薄弱处优先点名。",
                "保障/探测维：记录交战损失，为是否转打经济节点做准备。"
              ],
              "conclusion": "[打断·C] 本轮产出约束：脉冲压迫 · 点名拆网 · 准备转节点。",
              "libraryUpdate": "实时回写（打断）：经验库更新：入库「证伪后改打真实体系」；将连续一字推进标为失败模式。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 可控穿插施压",
            "constraint": "【实时切片·打断】约束继承自 C₂：脉冲压迫与点名拆网；观察到红方损失回落并有开扩迹象。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 打断切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【打断】抽一队穿插威胁脚手架，逼护航离场。",
                    "正面脉冲不停，防止空场开扩。",
                    "受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[打断/打击] 打击结论：节点威胁制造机会成本。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【打断】确认开扩迹象与脚手架位置。",
                    "跟踪护航是否后撤。",
                    "评估后院牵制风险。"
                  ],
                  "conclusion": "[打断/探测] 探测结论：盯节点与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【打断】穿插规模可控，预留回防。",
                    "家门口薄弱写入风险清单。",
                    "脉冲缩短暴露。"
                  ],
                  "conclusion": "[打断/防御] 防御结论：可控穿插 + 回防弹性。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【打断】流量在脉冲/穿插/回防间再分配。",
                    "成功标准可以是迫使护航离场。",
                    "把节点转移结果写入经验库。"
                  ],
                  "conclusion": "[打断/保障] 保障结论：按机会成本配置，不赌单次破点。"
                }
              },
              "conclusion": "[打断] 总判：节点转移压力，扩大对方决策负担。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 打断切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【打断】对穿插设拦截点，短打不恋战。",
                    "小股牵制我方家门口。",
                    "放弃峡谷压迫以保节点时也可接受。"
                  ],
                  "conclusion": "[打断/打击] 打击预测：拦截 + 后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【打断】峡谷留预警抽调。",
                    "监视脚手架是否被标记。",
                    "跟踪穿插走廊。"
                  ],
                  "conclusion": "[打断/探测] 探测预测：预警服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【打断】护航随三矿迁移。",
                    "峡谷变薄但节点护卫圈加厚。",
                    "开扩与节点存活绑定。"
                  ],
                  "conclusion": "[打断/防御] 防御预测：护航同迁。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【打断】损失回落后开三矿。",
                    "选址避热点。",
                    "产能服务护卫圈。"
                  ],
                  "conclusion": "[打断/保障] 保障预测：扩编绑保护。"
                }
              },
              "conclusion": "[打断] 总判：红方会用护航迁移 + 后院牵制对冲节点压力。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·打断 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[打断] 节点威胁能逼护航离场，但硬闯可能得不偿失。",
                  "oursRead": "节点威胁制造机会成本。",
                  "enemyRead": "拦截 + 后院牵制。",
                  "revise": "【打断修正】可控穿插威胁脚手架；受阻退回拆网，不赌单次破点。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[打断] 要同时看开扩、护航迁移与后院牵制风险。",
                  "oursRead": "盯节点与护航迁移。",
                  "enemyRead": "预警服务开扩安全。",
                  "revise": "【打断修正】探测清单三件套：脚手架、护航位移、后院威胁。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[打断] 抽调打节点暴露家门口薄弱，对手可能牵制反噬。",
                  "oursRead": "可控穿插 + 回防弹性。",
                  "enemyRead": "护航同迁。",
                  "revise": "【打断修正】穿插规模设上限，回防余量必留。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[打断] 成功标准应是改对方资源配置，而非拆掉节点。",
                  "oursRead": "按机会成本配置。",
                  "enemyRead": "扩编绑保护。",
                  "revise": "【打断修正】流量在脉冲/穿插/回防间再分配；结果写入经验库。"
                }
              },
              "synthesis": [
                "[打断] 跨维结论：节点战目标是机会成本，不是豪赌破点。",
                "[打断] 优先序：保障控规模 → 防御留回防 → 打击威胁节点 → 探测盯三信号。"
              ],
              "verdict": "[打断内省] 内省结论：小规模穿插制造机会成本，而不是赌单次破点。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【打断应对】打击/防御维：派出可控穿插队威胁脚手架，同时预留回防。",
                "打击维：正面脉冲不停，阻止红方空场发育。",
                "保障维：把本轮「节点转移」成败写入经验库。"
              ],
              "conclusion": "[打断·C] 本轮沉淀：压力可从正面迁移到节点 · 抽调必须保留回防弹性。",
              "libraryUpdate": "实时回写（打断）：经验库更新：入库「节点压力逼护航离场」「抽调保留回防」两条。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[打断] 意图假设必须带超时",
            "outcome": "failure",
            "tags": [
              "打断",
              "实时",
              "探测",
              "意图",
              "第1轮"
            ],
            "summary": "切片打断：快扩假设若无过期时间，会把前线兵力锁在错误目标上。",
            "learned": "每个意图假设写明证实条件与超时动作。",
            "updateNote": "实时更新·打断 · 更新动作：探测维规则强制「假设+超时+超时动作」三元组。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[打断] 证伪后立刻改打真实体系",
            "outcome": "success",
            "tags": [
              "打断",
              "实时",
              "打击",
              "目标切换",
              "第2轮"
            ],
            "summary": "切片打断：快扩证伪后，压力应转向毒爆护航体系，而不是继续防扩。",
            "learned": "意图修正要同步改关键目标，不能只改口头判断。",
            "updateNote": "实时更新·打断 · 更新动作：打击维关键目标与探测维结论硬绑定。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[打断] 连续一字推进会被凹字护航惩罚",
            "outcome": "failure",
            "tags": [
              "打断",
              "实时",
              "打击",
              "防御",
              "已修订"
            ],
            "summary": "切片打断：避爆折线拉长队形，恰好给侧翼引爆角度。",
            "learned": "改脉冲压迫，控制队形暴露时长。",
            "updateNote": "实时更新·打断 · 更新动作：将连续一字推进标为禁用模板，替换为脉冲脚本。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[打断] 节点压力可逼护航离场",
            "outcome": "success",
            "tags": [
              "打断",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片打断：威胁脚手架足以改变对方护航重心，即使未必拆掉节点。",
            "learned": "节点战的目标可以是「改对方资源配置」。",
            "updateNote": "实时更新·打断 · 更新动作：战役目标库增加「机会成本」成功判据。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[打断] 抽调必须保留回防弹性",
            "outcome": "success",
            "tags": [
              "打断",
              "实时",
              "防御",
              "保障",
              "第3轮"
            ],
            "summary": "切片打断：家门口偏薄时，无回防的穿插会被后院牵制反噬。",
            "learned": "穿插规模可控，先制造机会成本。",
            "updateNote": "实时更新·打断 · 更新动作：保障维抽调清单增加「回防余量」必填项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[打断] 象征性盯梢足以抑制强欺骗",
            "outcome": "success",
            "tags": [
              "打断",
              "实时",
              "探测",
              "信息",
              "已入库"
            ],
            "summary": "切片打断：对手降级假扩后，少量盯梢即可，不必全线回调。",
            "learned": "信息对抗也要算成本，防骗不等于重兵。",
            "updateNote": "实时更新·打断 · 更新动作：探测维防骗任务默认最低成本配置。",
            "linkedRoundId": "r2"
          }
        ]
      }
    }
  },
  {
    "id": "slice-5",
    "label": "切片⑤ · 节点决胜",
    "stamp": "帧窗 E",
    "cases": {
      "red": {
        "summary": "红方进入节点决胜切片：拦截点、后院牵制与护卫圈密度成为胜负手，经验库固化同迁校验。",
        "focus": "实时切片⑤ · 红方观察 · 关键节点护卫",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 热点避选",
            "constraint": "【实时切片·节点】约束前提：双矿运转、中路视野刚开、蓝方蟑螂比重上升。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 节点切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【节点】暂不主动深推，打击重心放在高地口牵制面，等待队形宽度确认。",
                    "毒爆集群后置待命，避免过早暴露落点被风筝拆网。",
                    "若蓝方一字过峡谷中线，再以侧翼切入打前排。"
                  ],
                  "conclusion": "[节点/打击] 打击结论：不深推，侧翼窗口待确认后开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【节点】双 Overlord 交错覆盖峡谷南北口，优先获取蟑螂队形与补给节奏。",
                    "东翼分矿走廊与中路开雾同优先级，防小狗先完成意图修正。",
                    "跟踪蓝方补给抬升，作为是否转入交战的信息门槛。"
                  ],
                  "conclusion": "[节点/探测] 探测结论：双通道开雾，信息主动权优先于交战。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【节点】B2 高地口预留刺蛇防守骨架，守住活动空间不被压缩。",
                    "主矿与前线之间保留回撤通道，防止被贴脸拆网。",
                    "家门口维持最低警戒，不因前线压力抽空。"
                  ],
                  "conclusion": "[节点/防御] 防御结论：高地骨架 + 回撤通道，守而不塌。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【节点】工蜂曲线维持，暂缓三矿，瓦斯优先刺蛇穴完成度。",
                    "补给与科技节点绑定前线损失速率，不盲目扩编。",
                    "毒爆产能按「待命规模」而非「立刻交战规模」配置。"
                  ],
                  "conclusion": "[节点/保障] 保障结论：科技与产能服务防守反击，不开三矿。"
                }
              },
              "conclusion": "[节点] 总判：侦察开雾 + 高地防守姿态，不主动深推。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 节点切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【节点】主力沿峡谷北坡持续正面施压，追求瞬时伤害差。",
                    "若红方毒爆现身，改蟑螂风筝拆网并绕开落点。",
                    "把流量集中前线，不在本轮分散打经济。"
                  ],
                  "conclusion": "[节点/打击] 打击预测：正面不停压，见毒爆则拆网绕行。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【节点】小狗东侧绕行确认分矿，验证「快扩/假守」假设。",
                    "用侧翼回报修正正面意图，不全凭中路观察。",
                    "对红方未扩三矿提高「防守反击」权重。"
                  ],
                  "conclusion": "[节点/探测] 探测预测：侧翼证伪快扩，再决定是否改目标。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【节点】家门口只留薄刺蛇线，接受回防成本换前线流量。",
                    "行军预留避爆折线，降低被侧翼惩罚概率。",
                    "不主动加厚后院，把风险留到下一轮评估。"
                  ],
                  "conclusion": "[节点/防御] 防御预测：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【节点】补给增量优先投入突击群，而非工蜂或三矿。",
                    "维持蟑螂比重，保证正面可持续施压。",
                    "侦察小狗按「限时任务」抽调，避免长期掉线。"
                  ],
                  "conclusion": "[节点/保障] 保障预测：流量堆前线，侦察限时抽调。"
                }
              },
              "conclusion": "[节点] 总判：蓝方会用侧翼侦察修正意图，正面不轻易撤压。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·节点 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[节点] 我方后置开火，对方持续正面施压——时间窗错位，硬接会亏。",
                  "oursRead": "侧翼待命、确认宽度后再打。",
                  "enemyRead": "不停压 + 见毒爆拆网绕行。",
                  "revise": "【节点修正】打击维保持待命，不抢先交战；窗口条件写成「队形宽度确认」。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[节点] 我方偏中路开雾，对方侧翼小狗可能先完成意图修正。",
                  "oursRead": "双通道开雾，信息优先于交战。",
                  "enemyRead": "东侧绕行证伪快扩。",
                  "revise": "【节点修正】东翼与峡谷同优先级，探测维提前于打击维闭环。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[节点] 高地骨架可用，但对方薄家门口暴露出对称机会，我方尚未利用。",
                  "oursRead": "高地骨架 + 回撤通道。",
                  "enemyRead": "薄家门口换前线流量。",
                  "revise": "【节点修正】守骨架不塌；下一拍可用小股牵制压对方回防成本。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[节点] 我方锁三矿喂科技，对方堆前线补给——经济节奏不同步。",
                  "oursRead": "科技优先，不开三矿。",
                  "enemyRead": "补给堆突击，侦察限时抽调。",
                  "revise": "【节点修正】保障维继续锁扩，产能按待命规模配置，避免被拖进消耗战。"
                }
              },
              "synthesis": [
                "[节点] 跨维结论：信息主动权（探测）决定打击窗口是否成立。",
                "[节点] 优先序：探测补东翼 → 防御守骨架 → 打击待命 → 保障锁扩。"
              ],
              "verdict": "[节点内省] 内省结论：保持防守骨架，必须补东翼预警，否则下一轮丢信息主动权。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【节点应对】探测维：立即补一只 Overlord 偏东悬停，专盯分矿走廊。",
                "防御/打击维：峡谷南口刺蛇线不变，毒爆后置到确认宽度后再侧翼切入。",
                "保障维：瓦斯继续点刺蛇穴，三矿仍锁，等待前线损失速率稳定。"
              ],
              "conclusion": "[节点·C] 本轮产出约束：东翼已覆盖 · 不深推 · 毒爆待命。",
              "libraryUpdate": "实时回写（节点）：经验库更新：写入「双通道探测并行」成功条；标注东翼后置为待规避失败模式。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 护卫圈成型",
            "constraint": "【实时切片·节点】约束继承自 C₁：东翼已覆盖、不深推、毒爆待命；蓝方侧翼侦察回报「红未真扩」。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 节点切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【节点】真实意图：峡谷南坡完成毒爆护航阵，待对方过中线侧翼引爆。",
                    "假扩仅作弱诱导，不抽调前线真实打击资源。",
                    "若诱导失败，立刻放弃欺骗，转纯防守反击脚本。"
                  ],
                  "conclusion": "[节点/打击] 打击结论：弱诱导换窗口，主打侧翼引爆。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【节点】监视蓝方是否提高对假扩的怀疑权重。",
                    "跟踪对方规避落点的行军折线，预判队形拉长点。",
                    "东翼保持覆盖，防止对方改打分矿走廊。"
                  ],
                  "conclusion": "[节点/探测] 探测结论：盯怀疑权重与避爆折线，不丢东翼。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【节点】毒爆居中、刺蛇两翼形成凹字护航，防风筝拆网。",
                    "脚手架姿态不抽空护航，防止被象征性盯梢反制。",
                    "交战中保持凹字完整，优先保核心而非追击。"
                  ],
                  "conclusion": "[节点/防御] 防御结论：凹字护航优先于欺骗动作。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【节点】瓦斯与补给继续喂护航密度，假扩不加建筑投资。",
                    "交战后用损失速率评估是否开启三矿。",
                    "预备下一轮节点保护所需的刺蛇存量。"
                  ],
                  "conclusion": "[节点/保障] 保障结论：资源绑护航；开扩开关=损失评估。"
                }
              },
              "conclusion": "[节点] 总判：用弱诱导 + 强护航换取一次侧翼交战窗口。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 节点切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【节点】继续正面压制，不全线回调防假扩。",
                    "对毒爆落点提前写入规避路线，降低引爆效率。",
                    "寻找护航薄弱翼，准备定点拆网。"
                  ],
                  "conclusion": "[节点/打击] 打击预测：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【节点】已吃过意图误判，对假扩提高怀疑，只派象征性盯梢。",
                    "重点确认毒爆护航阵是否成型。",
                    "记录交战损失，为是否转打节点做信息准备。"
                  ],
                  "conclusion": "[节点/探测] 探测预测：弱盯梢 + 确认真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【节点】避爆折线写入默认行军，接受队形短暂拉长。",
                    "家门口仍偏薄，但本轮不主动加厚。",
                    "拆网单位前置，降低被凹字咬死的概率。"
                  ],
                  "conclusion": "[节点/防御] 防御预测：避爆优先，后院风险暂挂起。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【节点】补给维持突击可持续性，不因假扩信号大挪资源。",
                    "盯梢单位按最低成本配置。",
                    "为脉冲压迫预留重组间隔的补给余量。"
                  ],
                  "conclusion": "[节点/保障] 保障预测：成本可控的持续施压。"
                }
              },
              "conclusion": "[节点] 总判：蓝方不会完全上当，正面压力仍在，拆网意识增强。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·节点 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[节点] 强欺骗收益下降；对方点名拆网，硬打窗口变短。",
                  "oursRead": "弱诱导 + 侧翼引爆。",
                  "enemyRead": "压体系 + 点名拆网。",
                  "revise": "【节点修正】弃强欺骗，主资源留给侧翼窗口，不抽调护航去演戏。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[节点] 对方已提高怀疑权重，假扩信号易被廉价化解。",
                  "oursRead": "盯怀疑权重与避爆折线。",
                  "enemyRead": "弱盯梢 + 确认护航体系。",
                  "revise": "【节点修正】把「怀疑权重」写入欺骗降级开关；东翼覆盖不撤。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[节点] 对方避爆折线会拉长队形，恰给凹字护航侧翼角。",
                  "oursRead": "凹字护航优先。",
                  "enemyRead": "避爆优先，拆网前置。",
                  "revise": "【节点修正】护航完整度 > 追击；用阵型换切入角，而不是用骗术。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[节点] 开扩开关依赖交战评估，对方也在记损失准备转节点。",
                  "oursRead": "资源绑护航；开扩=损失评估。",
                  "enemyRead": "可持续脉冲 + 可抽调余量。",
                  "revise": "【节点修正】保障维只做弱诱导投资；评估环写进开扩门槛。"
                }
              },
              "synthesis": [
                "[节点] 跨维结论：欺骗维降权，护航/打击维升权。",
                "[节点] 优先序：防御凹字 → 打击侧翼 → 探测盯折线 → 保障弱诱导。"
              ],
              "verdict": "[节点内省] 内省结论：弃强欺骗，改「弱诱导 + 强护航」交战脚本。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【节点应对】保障维：脚手架仅作弱信号，不抽调前线。",
                "防御/打击维：毒爆居中、刺蛇两翼凹字，等待对方过峡谷中线。",
                "评估环：交战后立刻看损失速率，决定是否开启三矿。"
              ],
              "conclusion": "[节点·C] 本轮产出约束：凹字护航就绪 · 弱诱导 · 交战后评估开扩。",
              "libraryUpdate": "实时回写（节点）：经验库更新：降级「强欺骗」为失败教训；入库「凹字护航克一字」成功条。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 拦截与牵制闭环",
            "constraint": "【实时切片·节点】约束继承自 C₂：凹字护航、弱诱导、交战后评估；前线损失速率已回落。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "红方 · 本轮杀伤链（打/探/防/保） · 节点切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【节点】若蓝方抽调打三矿，放弃峡谷压迫，优先拦截穿插。",
                    "节点护卫圈内可对穿插队实施短打击，不恋战。",
                    "小股牵制对方家门口，降低其抽调自由度。"
                  ],
                  "conclusion": "[节点/打击] 打击结论：护节点反击为主，辅以后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【节点】峡谷保留 Overlord + 少量刺蛇预警抽调迹象。",
                    "监视三矿热点是否已被对方 Overlord 标记。",
                    "跟踪穿插走廊，预设拦截点触发条件。"
                  ],
                  "conclusion": "[节点/探测] 探测结论：预警抽调与标记热点，服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【节点】刺蛇环伺 + 毒爆核心随三矿迁移，形成节点护卫圈。",
                    "峡谷只留轻量防守，避免护航留在旧前线空窗。",
                    "开扩与护航同迁，节点存活绑扩编开关。"
                  ],
                  "conclusion": "[节点/防御] 防御结论：护航随经济节点迁移。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【节点】损失速率低于阈值后开启三矿。",
                    "三矿选址避开已被标记的热点。",
                    "产能节奏服务护卫圈密度，而不是继续堆峡谷。"
                  ],
                  "conclusion": "[节点/保障] 保障结论：开扩阈值明确，选址避热点。"
                }
              },
              "conclusion": "[节点] 总判：经济扩编与节点保护并行。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测蓝方 · 本轮杀伤链（打/探/防/保） · 节点切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【节点】抽一队穿插威胁脚手架，逼红方护航离场。",
                    "正面维持脉冲压力，防止空场完成开扩。",
                    "穿插受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[节点/打击] 打击预测：节点威胁 + 脉冲牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【节点】确认红方开扩迹象与脚手架位置。",
                    "跟踪护航是否随三矿后撤。",
                    "评估后院被牵制的风险信号。"
                  ],
                  "conclusion": "[节点/探测] 探测预测：盯开扩与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【节点】穿插规模可控，预留回防弹性。",
                    "家门口偏薄被写入风险，避免无回防豪赌。",
                    "正面脉冲缩短暴露，降低被凹字反噬。"
                  ],
                  "conclusion": "[节点/防御] 防御预测：可控穿插，保留回防。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【节点】把流量在「正面脉冲 / 穿插队 / 回防」间再分配。",
                    "不因单次破点抽空补给。",
                    "成功标准可以是「迫使护航离场」而非拆掉节点。"
                  ],
                  "conclusion": "[节点/保障] 保障预测：以机会成本为目标配置兵力。"
                }
              },
              "conclusion": "[节点] 总判：蓝方会把压力从正面部分转移到节点。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·节点 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[节点] 对方转打节点穿插；我方若仍压峡谷，护航会空窗。",
                  "oursRead": "护节点短打 + 后院牵制。",
                  "enemyRead": "穿插威胁脚手架 + 脉冲牵制。",
                  "revise": "【节点修正】打击重心随节点迁移；穿插路径预设拦截，不恋战。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[节点] 开扩与标记热点同期，预警抽调比盯峡谷更值钱。",
                  "oursRead": "预警抽调与热点标记。",
                  "enemyRead": "盯开扩与护航迁移。",
                  "revise": "【节点修正】峡谷降为轻量预警，探测主盯穿插走廊与脚手架。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[节点] 开扩与节点暴露双目标压力；护航若留旧前线必空窗。",
                  "oursRead": "护航随经济节点迁移。",
                  "enemyRead": "可控穿插 + 回防弹性。",
                  "revise": "【节点修正】护卫圈跟着三矿走；峡谷只留薄防。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[节点] 损失回落给出扩编窗口，但选址若落热点会被点名。",
                  "oursRead": "开扩阈值明确，选址避热点。",
                  "enemyRead": "按机会成本配置穿插。",
                  "revise": "【节点修正】开扩绑定护航迁移校验；产能喂护卫圈而非峡谷。"
                }
              },
              "synthesis": [
                "[节点] 跨维结论：扩编可以开，保护必须同迁。",
                "[节点] 优先序：防御同迁 → 保障开扩 → 探测预警 → 打击拦截。"
              ],
              "verdict": "[节点内省] 内省结论：扩编可开，但护航必须跟着经济节点走。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【节点应对】保障/防御维：开启三矿，刺蛇与毒爆核心后撤形成节点护卫圈。",
                "探测维：峡谷保留 Overlord + 少量刺蛇预警。",
                "打击维：对蓝方抽调路径预设拦截点，避免被穿插。"
              ],
              "conclusion": "[节点·C] 本轮沉淀：开扩阈值明确 · 护航随节点迁移 · 形成可入库经验。",
              "libraryUpdate": "实时回写（节点）：经验库更新：入库「开扩与护航同迁」「后院牵制换抽调自由度」两条成功经验。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[节点] 东翼预警必须与峡谷开雾同优先级",
            "outcome": "success",
            "tags": [
              "节点",
              "实时",
              "探测",
              "第1轮",
              "已入库"
            ],
            "summary": "切片节点：仅开中路雾会让对方侧翼先完成意图修正；东翼与峡谷同步覆盖才能保住信息主动权。",
            "learned": "探测任务要双通道并行，不能串行后置。",
            "updateNote": "实时更新·节点 · 更新动作：覆盖旧「先中路后侧翼」条目，写入双通道并行规则。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[节点] 强欺骗在对手已误判一次后收益骤降",
            "outcome": "failure",
            "tags": [
              "节点",
              "实时",
              "打击",
              "意图",
              "第2轮"
            ],
            "summary": "切片节点：对方吃过快扩误判后，假扩只能廉价化解；执着强欺骗会浪费前线资源。",
            "learned": "欺骗等级随对手怀疑权重下调，改为弱诱导。",
            "updateNote": "实时更新·节点 · 更新动作：将强欺骗从推荐脚本降级为慎用，并挂失败标签。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[节点] 凹字护航克一字推进",
            "outcome": "success",
            "tags": [
              "节点",
              "实时",
              "防御",
              "打击",
              "第2轮"
            ],
            "summary": "切片节点：毒爆居中、刺蛇两翼，可利用对方规避落点导致的队形拉长打出侧翼窗口。",
            "learned": "对方拆网意识越强，越要用护航阵型换切入角。",
            "updateNote": "实时更新·节点 · 更新动作：新增护航阵型模板，关联打击维侧翼窗口条件。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[节点] 开扩与护航必须同迁",
            "outcome": "success",
            "tags": [
              "节点",
              "实时",
              "保障",
              "防御",
              "第3轮"
            ],
            "summary": "切片节点：损失速率回落后可开三矿，但护航若留在旧前线，新经济节点会空窗。",
            "learned": "扩编决策绑定节点保护迁移，不单独开扩。",
            "updateNote": "实时更新·节点 · 更新动作：战役开扩门槛增加「护航迁移」校验项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[节点] 家门口偏薄是可利用的对称弱点",
            "outcome": "success",
            "tags": [
              "节点",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片节点：对方把流量堆前线时，小股牵制可降低其抽调打节点的自由度。",
            "learned": "节点保护不一定加兵，也可通过对手后院制造机会成本。",
            "updateNote": "实时更新·节点 · 更新动作：经验库增加「机会成本牵制」条目，供保障/打击维调用。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[节点] 损失速率评估写入开扩门槛",
            "outcome": "success",
            "tags": [
              "节点",
              "实时",
              "保障",
              "评估",
              "已入库"
            ],
            "summary": "切片节点：交战后用损失速率评估，再决定是否开扩，避免凭感觉扩矿。",
            "learned": "把「评估」作为战役开关，而不是事后总结。",
            "updateNote": "实时更新·节点 · 更新动作：保障维开扩规则绑定评估环，替换旧凭感觉阈值。",
            "linkedRoundId": "r2"
          }
        ]
      },
      "blue": {
        "summary": "蓝方以威胁脚手架为成功判据，不强求拆点；回防余量与脉冲前线并行，写入节点战经验。",
        "focus": "实时切片⑤ · 蓝方观察 · 机会成本破局",
        "rounds": [
          {
            "id": "r1",
            "title": "第 1 轮 · 标定脚手架",
            "constraint": "【实时切片·破局】约束前提：双矿稳定、蟑螂线成型、计划以峡谷北口建立压力。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 破局切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【破局】一字推进压缩红方空间，强迫其毒爆过早交战。",
                    "追求前线瞬时伤害差，不在本轮转打经济。",
                    "见毒爆则准备拆网绕行，而非硬接。"
                  ],
                  "conclusion": "[破局/打击] 打击结论：正面高压，逼毒爆提前。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【破局】小狗东侧绕行，验证是否存在快扩。",
                    "把「红方未开三矿」读作防守或反击，设短超时证实。",
                    "侧翼回报用于修正正面目标，不全凭中路。"
                  ],
                  "conclusion": "[破局/探测] 探测结论：侧翼证伪快扩，假设带超时。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【破局】家门口只留薄防御，接受回防成本。",
                    "行军预留毒爆规避折线。",
                    "本轮不加厚后院，标记为下一轮风险。"
                  ],
                  "conclusion": "[破局/防御] 防御结论：薄家门口 + 避爆折线。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【破局】补给增量投入突击群。",
                    "维持蟑螂比重以支撑持续施压。",
                    "侦察抽调限时，避免前线掉线过久。"
                  ],
                  "conclusion": "[破局/保障] 保障结论：流量堆前线，侦察限时。"
                }
              },
              "conclusion": "[破局] 总判：正面高压 + 侧翼证伪快扩。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 破局切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【破局】可能用毒爆侧翼惩罚一字推进。",
                    "不轻易深推，打击窗口偏后置。",
                    "确认队形宽度前毒爆不交战。"
                  ],
                  "conclusion": "[破局/打击] 打击预测：侧翼待命，后置开火。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【破局】优先开雾并覆盖峡谷南北口。",
                    "若发现东翼侦察，会补 Overlord 盯分矿走廊。",
                    "跟踪蟑螂队形与补给节奏。"
                  ],
                  "conclusion": "[破局/探测] 探测预测：双通道开雾，防意图被抢先修正。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【破局】高地口布防，守活动空间。",
                    "保持回撤通道，防贴脸拆网。",
                    "家门口维持最低警戒。"
                  ],
                  "conclusion": "[破局/防御] 防御预测：高地骨架防守反击。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【破局】继续点刺蛇科技，暂不开三矿。",
                    "产能服务防守反击而非扩矿。",
                    "瓦斯优先科技完成度。"
                  ],
                  "conclusion": "[破局/保障] 保障预测：科技优先，锁三矿。"
                }
              },
              "conclusion": "[破局] 总判：红方偏防守反击，关键变量是毒爆时机。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·破局 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[破局] 高压有效的前提是毒爆未成型；若假设错误，损失差会反转。",
                  "oursRead": "正面高压逼毒爆提前。",
                  "enemyRead": "侧翼待命、后置开火。",
                  "revise": "【破局修正】打击维保持压力，但把「改打毒爆群」写成超时后的默认切换。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[破局] 快扩假设若无过期，会把兵力锁在错误目标。",
                  "oursRead": "侧翼证伪，假设带超时。",
                  "enemyRead": "双通道开雾，防意图被抢先修正。",
                  "revise": "【破局修正】探测维强制「假设+超时+超时动作」；侧翼要快。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[破局] 薄家门口换流量，被反击时回防成本高。",
                  "oursRead": "薄家门口 + 避爆折线。",
                  "enemyRead": "高地骨架防守反击。",
                  "revise": "【破局修正】本轮可薄，但标记为下一轮风险，不无限加注前线。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[破局] 流量堆前线与侦察抽调争补给。",
                  "oursRead": "补给堆突击，侦察限时。",
                  "enemyRead": "科技优先，锁三矿。",
                  "revise": "【破局修正】侦察抽调限时归队，保障维禁止长期掉线。"
                }
              },
              "synthesis": [
                "[破局] 跨维结论：意图假设必须可证伪，否则打击目标会漂。",
                "[破局] 优先序：探测限时证伪 → 打击可切换目标 → 防御记风险 → 保障限时抽调。"
              ],
              "verdict": "[破局内省] 内省结论：快扩假设设短超时，超时未证实则立刻改打毒爆群。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【破局应对】探测维：小狗侦察限时完成；超时未见到脚手架，则撤销防扩抽调。",
                "打击/防御维：正面保持压力，行军预留毒爆规避折线。",
                "保障维：本轮不加厚家门口，但标记为下一轮风险。"
              ],
              "conclusion": "[破局·C] 本轮产出约束：快扩假设限时 · 正面不停压 · 规避落点。",
              "libraryUpdate": "实时回写（破局）：经验库更新：挂起「无超时意图假设」失败风险；写入限时证伪规则。"
            }
          },
          {
            "id": "r2",
            "title": "第 2 轮 · 护航迁移跟踪",
            "constraint": "【实时切片·破局】约束继承自 C₁：快扩假设已超时证伪；红方转为凹字护航。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 破局切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【破局】确认主威胁是毒爆护航阵，持续正面施压。",
                    "风筝拆网降低引爆效率，点名薄弱翼。",
                    "对假扩只派象征性盯梢，不全线回调。"
                  ],
                  "conclusion": "[破局/打击] 打击结论：压体系 + 点名拆网。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【破局】快扩已证伪，转向确认护航阵型与薄弱翼。",
                    "记录交战损失，为转节点做信息准备。",
                    "弱盯梢足够抑制强欺骗。"
                  ],
                  "conclusion": "[破局/探测] 探测结论：目标切换到真实体系。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【破局】改脉冲压迫，避免一字队形长时间拉伸。",
                    "拆网单位前置。",
                    "缩短暴露窗口，降低被凹字咬死概率。"
                  ],
                  "conclusion": "[破局/防御] 防御结论：脉冲式降低暴露。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【破局】补给支持短突击—后撤重组节奏。",
                    "盯梢最低成本。",
                    "为是否转打经济节点预留可抽调余量。"
                  ],
                  "conclusion": "[破局/保障] 保障结论：可持续脉冲 + 可抽调余量。"
                }
              },
              "conclusion": "[破局] 总判：对准毒爆体系持续施压。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 破局切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【破局】降级假扩为弱诱导，资源留给侧翼引爆。",
                    "峡谷中线等待过线后开火。",
                    "刺蛇两翼防风筝拆网。"
                  ],
                  "conclusion": "[破局/打击] 打击预测：高质量侧翼窗口。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【破局】监视我方怀疑权重与避爆折线。",
                    "东翼保持覆盖。",
                    "用损失速率评估开扩。"
                  ],
                  "conclusion": "[破局/探测] 探测预测：盯折线与损失评估。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【破局】凹字护航成型，保毒爆核心。",
                    "弱诱导不抽空护航。",
                    "交战中优先保阵型。"
                  ],
                  "conclusion": "[破局/防御] 防御预测：强护航优先。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【破局】资源绑护航密度。",
                    "交战后评估是否开三矿。",
                    "假扩不加建筑投资。"
                  ],
                  "conclusion": "[破局/保障] 保障预测：护航优先于欺骗投资。"
                }
              },
              "conclusion": "[破局] 总判：红方准备打一次高质量侧翼窗口。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·破局 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[破局] 连续一字 + 避爆折线拉长队形，正好喂给凹字侧翼。",
                  "oursRead": "压体系 + 点名拆网。",
                  "enemyRead": "高质量侧翼窗口。",
                  "revise": "【破局修正】改脉冲压迫，缩短暴露；拆网前置点名薄弱翼。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[破局] 快扩已证伪，继续防扩是浪费。",
                  "oursRead": "目标切换到真实体系。",
                  "enemyRead": "盯折线与损失评估。",
                  "revise": "【破局修正】探测结论硬绑定打击关键目标：对准护航体系。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[破局] 过线过深会被凹字最大化惩罚。",
                  "oursRead": "脉冲式降低暴露。",
                  "enemyRead": "强护航优先。",
                  "revise": "【破局修正】短突击—后撤重组，不在中线长时间拉伸。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[破局] 要为转节点预留抽调余量，又要撑住脉冲节奏。",
                  "oursRead": "可持续脉冲 + 可抽调余量。",
                  "enemyRead": "护航优先于欺骗投资。",
                  "revise": "【破局修正】补给服务脉冲间隔；记下损失，准备转节点。"
                }
              },
              "synthesis": [
                "[破局] 跨维结论：证伪后必须改打真实体系，阵型从连续改为脉冲。",
                "[破局] 优先序：探测改目标 → 打击脉冲拆网 → 防御控暴露 → 保障留余量。"
              ],
              "verdict": "[破局内省] 内省结论：改脉冲压迫，缩短队形暴露时间。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入下一轮约束",
              "steps": [
                "【破局应对】打击维：以短突击—后撤重组代替连续一字推进。",
                "打击维：拆网单位前置，护航薄弱处优先点名。",
                "保障/探测维：记录交战损失，为是否转打经济节点做准备。"
              ],
              "conclusion": "[破局·C] 本轮产出约束：脉冲压迫 · 点名拆网 · 准备转节点。",
              "libraryUpdate": "实时回写（破局）：经验库更新：入库「证伪后改打真实体系」；将连续一字推进标为失败模式。"
            }
          },
          {
            "id": "r3",
            "title": "第 3 轮 · 威胁即得分",
            "constraint": "【实时切片·破局】约束继承自 C₂：脉冲压迫与点名拆网；观察到红方损失回落并有开扩迹象。",
            "ourAction": {
              "title": "红方博弈思维链",
              "subtitle": "蓝方 · 本轮杀伤链（打/探/防/保） · 破局切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【破局】抽一队穿插威胁脚手架，逼护航离场。",
                    "正面脉冲不停，防止空场开扩。",
                    "受阻则退回拆网，不硬闯。"
                  ],
                  "conclusion": "[破局/打击] 打击结论：节点威胁制造机会成本。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【破局】确认开扩迹象与脚手架位置。",
                    "跟踪护航是否后撤。",
                    "评估后院牵制风险。"
                  ],
                  "conclusion": "[破局/探测] 探测结论：盯节点与护航迁移。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【破局】穿插规模可控，预留回防。",
                    "家门口薄弱写入风险清单。",
                    "脉冲缩短暴露。"
                  ],
                  "conclusion": "[破局/防御] 防御结论：可控穿插 + 回防弹性。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【破局】流量在脉冲/穿插/回防间再分配。",
                    "成功标准可以是迫使护航离场。",
                    "把节点转移结果写入经验库。"
                  ],
                  "conclusion": "[破局/保障] 保障结论：按机会成本配置，不赌单次破点。"
                }
              },
              "conclusion": "[破局] 总判：节点转移压力，扩大对方决策负担。"
            },
            "opponentCounter": {
              "title": "对方博弈思维链",
              "subtitle": "预测红方 · 本轮杀伤链（打/探/防/保） · 破局切片",
              "chains": {
                "strike": {
                  "key": "strike",
                  "label": "打击",
                  "steps": [
                    "【破局】对穿插设拦截点，短打不恋战。",
                    "小股牵制我方家门口。",
                    "放弃峡谷压迫以保节点时也可接受。"
                  ],
                  "conclusion": "[破局/打击] 打击预测：拦截 + 后院牵制。"
                },
                "detect": {
                  "key": "detect",
                  "label": "探测",
                  "steps": [
                    "【破局】峡谷留预警抽调。",
                    "监视脚手架是否被标记。",
                    "跟踪穿插走廊。"
                  ],
                  "conclusion": "[破局/探测] 探测预测：预警服务开扩安全。"
                },
                "defense": {
                  "key": "defense",
                  "label": "防御",
                  "steps": [
                    "【破局】护航随三矿迁移。",
                    "峡谷变薄但节点护卫圈加厚。",
                    "开扩与节点存活绑定。"
                  ],
                  "conclusion": "[破局/防御] 防御预测：护航同迁。"
                },
                "support": {
                  "key": "support",
                  "label": "保障",
                  "steps": [
                    "【破局】损失回落后开三矿。",
                    "选址避热点。",
                    "产能服务护卫圈。"
                  ],
                  "conclusion": "[破局/保障] 保障预测：扩编绑保护。"
                }
              },
              "conclusion": "[破局] 总判：红方会用护航迁移 + 后院牵制对冲节点压力。"
            },
            "introspection": {
              "title": "内省反馈",
              "subtitle": "实时切片·破局 · 逐维对照 A / B，校准可执行修正",
              "dims": {
                "strike": {
                  "label": "打击",
                  "tension": "[破局] 节点威胁能逼护航离场，但硬闯可能得不偿失。",
                  "oursRead": "节点威胁制造机会成本。",
                  "enemyRead": "拦截 + 后院牵制。",
                  "revise": "【破局修正】可控穿插威胁脚手架；受阻退回拆网，不赌单次破点。"
                },
                "detect": {
                  "label": "探测",
                  "tension": "[破局] 要同时看开扩、护航迁移与后院牵制风险。",
                  "oursRead": "盯节点与护航迁移。",
                  "enemyRead": "预警服务开扩安全。",
                  "revise": "【破局修正】探测清单三件套：脚手架、护航位移、后院威胁。"
                },
                "defense": {
                  "label": "防御",
                  "tension": "[破局] 抽调打节点暴露家门口薄弱，对手可能牵制反噬。",
                  "oursRead": "可控穿插 + 回防弹性。",
                  "enemyRead": "护航同迁。",
                  "revise": "【破局修正】穿插规模设上限，回防余量必留。"
                },
                "support": {
                  "label": "保障",
                  "tension": "[破局] 成功标准应是改对方资源配置，而非拆掉节点。",
                  "oursRead": "按机会成本配置。",
                  "enemyRead": "扩编绑保护。",
                  "revise": "【破局修正】流量在脉冲/穿插/回防间再分配；结果写入经验库。"
                }
              },
              "synthesis": [
                "[破局] 跨维结论：节点战目标是机会成本，不是豪赌破点。",
                "[破局] 优先序：保障控规模 → 防御留回防 → 打击威胁节点 → 探测盯三信号。"
              ],
              "verdict": "[破局内省] 内省结论：小规模穿插制造机会成本，而不是赌单次破点。"
            },
            "ourResponse": {
              "title": "红方应对 C",
              "subtitle": "综合修订 · 写入经验库",
              "steps": [
                "【破局应对】打击/防御维：派出可控穿插队威胁脚手架，同时预留回防。",
                "打击维：正面脉冲不停，阻止红方空场发育。",
                "保障维：把本轮「节点转移」成败写入经验库。"
              ],
              "conclusion": "[破局·C] 本轮沉淀：压力可从正面迁移到节点 · 抽调必须保留回防弹性。",
              "libraryUpdate": "实时回写（破局）：经验库更新：入库「节点压力逼护航离场」「抽调保留回防」两条。"
            }
          }
        ],
        "memories": [
          {
            "id": "m1",
            "title": "[破局] 意图假设必须带超时",
            "outcome": "failure",
            "tags": [
              "破局",
              "实时",
              "探测",
              "意图",
              "第1轮"
            ],
            "summary": "切片破局：快扩假设若无过期时间，会把前线兵力锁在错误目标上。",
            "learned": "每个意图假设写明证实条件与超时动作。",
            "updateNote": "实时更新·破局 · 更新动作：探测维规则强制「假设+超时+超时动作」三元组。",
            "linkedRoundId": "r1"
          },
          {
            "id": "m2",
            "title": "[破局] 证伪后立刻改打真实体系",
            "outcome": "success",
            "tags": [
              "破局",
              "实时",
              "打击",
              "目标切换",
              "第2轮"
            ],
            "summary": "切片破局：快扩证伪后，压力应转向毒爆护航体系，而不是继续防扩。",
            "learned": "意图修正要同步改关键目标，不能只改口头判断。",
            "updateNote": "实时更新·破局 · 更新动作：打击维关键目标与探测维结论硬绑定。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m3",
            "title": "[破局] 连续一字推进会被凹字护航惩罚",
            "outcome": "failure",
            "tags": [
              "破局",
              "实时",
              "打击",
              "防御",
              "已修订"
            ],
            "summary": "切片破局：避爆折线拉长队形，恰好给侧翼引爆角度。",
            "learned": "改脉冲压迫，控制队形暴露时长。",
            "updateNote": "实时更新·破局 · 更新动作：将连续一字推进标为禁用模板，替换为脉冲脚本。",
            "linkedRoundId": "r2"
          },
          {
            "id": "m4",
            "title": "[破局] 节点压力可逼护航离场",
            "outcome": "success",
            "tags": [
              "破局",
              "实时",
              "打击",
              "保障",
              "第3轮"
            ],
            "summary": "切片破局：威胁脚手架足以改变对方护航重心，即使未必拆掉节点。",
            "learned": "节点战的目标可以是「改对方资源配置」。",
            "updateNote": "实时更新·破局 · 更新动作：战役目标库增加「机会成本」成功判据。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m5",
            "title": "[破局] 抽调必须保留回防弹性",
            "outcome": "success",
            "tags": [
              "破局",
              "实时",
              "防御",
              "保障",
              "第3轮"
            ],
            "summary": "切片破局：家门口偏薄时，无回防的穿插会被后院牵制反噬。",
            "learned": "穿插规模可控，先制造机会成本。",
            "updateNote": "实时更新·破局 · 更新动作：保障维抽调清单增加「回防余量」必填项。",
            "linkedRoundId": "r3"
          },
          {
            "id": "m6",
            "title": "[破局] 象征性盯梢足以抑制强欺骗",
            "outcome": "success",
            "tags": [
              "破局",
              "实时",
              "探测",
              "信息",
              "已入库"
            ],
            "summary": "切片破局：对手降级假扩后，少量盯梢即可，不必全线回调。",
            "learned": "信息对抗也要算成本，防骗不等于重兵。",
            "updateNote": "实时更新·破局 · 更新动作：探测维防骗任务默认最低成本配置。",
            "linkedRoundId": "r2"
          }
        ]
      }
    }
  }
];

let knowledgeLiveVariantIndex = 0;

function getKnowledgeLiveVariant() {
  const list = KNOWLEDGE_EXPERIENCE_VARIANTS;
  if (!Array.isArray(list) || !list.length) return null;
  const idx = ((knowledgeLiveVariantIndex % list.length) + list.length) % list.length;
  return list[idx] || list[0];
}

function buildKnowledgeExperienceBoard(sideKey) {
  const side = sideKey === "blue" ? "blue" : "red";
  const pack = getKnowledgeLiveVariant();
  const cases = pack?.cases || {};
  const raw = cases[side] || cases.red || {};
  const rounds = Array.isArray(raw.rounds) ? raw.rounds : [];
  const memories = Array.isArray(raw.memories) ? raw.memories : [];
  return {
    side,
    summary: raw.summary || "",
    focus: raw.focus || "",
    rounds,
    memories,
    chainDims: KNOWLEDGE_CHAIN_DIMS,
    roundCount: rounds.length,
    memoryCount: memories.length,
    successMemoryCount: memories.filter((m) => m.outcome === "success").length,
    failureMemoryCount: memories.filter((m) => m.outcome === "failure").length,
    liveVariantIndex: ((knowledgeLiveVariantIndex % KNOWLEDGE_LIVE_VARIANT_COUNT) + KNOWLEDGE_LIVE_VARIANT_COUNT) % KNOWLEDGE_LIVE_VARIANT_COUNT,
    liveVariantLabel: pack?.label || "",
    liveVariantStamp: pack?.stamp || "",
    liveVariantId: pack?.id || "",
    liveTickMs: KNOWLEDGE_LIVE_TICK_MS,
    liveCycleMs: KNOWLEDGE_LIVE_TICK_MS * KNOWLEDGE_LIVE_VARIANT_COUNT,
  };
}

function getKnowledgeSideChain(sideBlock, dimKey) {
  if (!sideBlock || !sideBlock.chains) return null;
  const key = dimKey || "strike";
  return sideBlock.chains[key] || sideBlock.chains.strike || null;
}



const CHANNELS = [
  { key: "latest", label: "方案生成智能体" },
  { key: "parser", label: "指令解析智能体" },
];

const AGENT_VIEWS = [
  { key: "planner", label: "方案生成" },
];

/** OODA 四环：点击进入对应初始界面，环内再用 Tab 切换 */
function stripAgentSuffix(name) {
  const text = String(name || "").trim();
  return text.endsWith("智能体") ? text.slice(0, -3) : text;
}

function withAgentShort(agent) {
  if (!agent || typeof agent !== "object") return { name: "体系值班台", short: "体系值班台", duty: "" };
  return {
    ...agent,
    short: agent.short || stripAgentSuffix(agent.name),
  };
}

const MODULE_AGENTS = {
  overview: { name: "体系值班台", duty: "总览全席" },
  observe: { name: "红方态势感知智能体", short: "红方态势感知", duty: "先把场上看清楚" },
  orient: { name: "红方态势认知智能体", short: "红方态势认知", duty: "把看见的事做成判断" },
  oriStrike: { name: "红方打击态势认知智能体", short: "红方打击态势认知", duty: "判断蓝方打击意图与薄弱点" },
  oriDetect: { name: "红方探测态势认知智能体", short: "红方探测态势认知", duty: "判断蓝方侦察意图与视野缺口" },
  oriDefense: { name: "红方防御态势认知智能体", short: "红方防御态势认知", duty: "判断蓝方防守意图与防线缺口" },
  oriSupport: { name: "红方保障态势认知智能体", short: "红方保障态势认知", duty: "判断蓝方后勤意图与补给缺口" },
  oriEnvironment: { name: "红方作战环境认知智能体", short: "红方作战环境认知", duty: "把控点事实收成地形利用判断与关键节点" },
  awareness: { name: "红方态势事实与推演智能体", short: "红方态势事实与推演智能体", duty: "把场上事实摊开" },
  awarenessBlue: { name: "蓝方状态推演智能体", duty: "从红方观测反推对面" },
  force: { name: "兵力布势对比智能体", duty: "看双方力量怎么摆" },
  obsStrike: { name: "红方打击态势感知智能体", short: "红方打击态势感知", duty: "评估有效杀伤能力" },
  obsDetect: { name: "红方探测态势感知智能体", short: "红方探测态势感知", duty: "评估侦察覆盖与信息时效" },
  obsDefense: { name: "红方防御态势感知智能体", short: "红方防御态势感知", duty: "评估抗击与阻滞能力" },
  obsSupport: { name: "红方保障态势感知智能体", short: "红方保障态势感知", duty: "评估持续作战能力" },
  obsEnvironment: { name: "红方作战环境感知智能体", short: "红方作战环境感知", duty: "摊开双方对关键地形与环境要点的控制" },
  intent: { name: "意图识别智能体", duty: "读对手下一步想干什么" },
  risk: { name: "风险分析智能体", duty: "把马上要出事的点喊出来" },
  killchain: { name: "杀伤链发现智能体", duty: "把对方能打到我们的链路理清" },
  critical: { name: "关键打击节点识别智能体", duty: "标出最值得下手的节点" },
  cot: { name: "自博弈思维链生成智能体", duty: "红蓝过招，收敛成红方方案" },
  cotStrike: { name: "红方打击决策自博弈思维链生成智能体", short: "红方打击决策自博弈", duty: "该维红蓝过招，收敛打击应对" },
  cotDetect: { name: "红方探测决策自博弈思维链生成智能体", short: "红方探测决策自博弈", duty: "该维红蓝过招，收敛探测应对" },
  cotDefense: { name: "红方防御决策自博弈思维链生成智能体", short: "红方防御决策自博弈", duty: "该维红蓝过招，收敛防御应对" },
  cotSupport: { name: "红方保障决策自博弈思维链生成智能体", short: "红方保障决策自博弈", duty: "该维红蓝过招，收敛保障应对" },
  cotEnvironment: { name: "红方作战环境思维链生成智能体", short: "红方作战环境思维链", duty: "红蓝围绕地形利用过招，收敛本轮环境约束" },
  cotOurs: { name: COT_ROLE_AGENTS.ours, duty: "写出红方这一手" },
  cotEnemy: { name: COT_ROLE_AGENTS.enemy, duty: "预估对面下一手" },
  cotReflect: { name: COT_ROLE_AGENTS.reflect, duty: "对照预测把方案拧正" },
  killnet: { name: "红方杀伤网编排与方案规划智能体", duty: "按打探防保把应对落成网，并给出本轮方案" },
  killnetNet: { name: "红方杀伤网编排智能体", duty: "把应对落成打探防保杀伤网" },
  killnetSeq: { name: "红方方案规划智能体", duty: "按杀伤网给出可执行步骤" },
  plan: { name: "红方指挥控制智能体", duty: "把应对写成可执行方案并指挥控制" },
  bus: { name: "方案生成智能体", duty: "写出这一轮作战方案" },
  tasksplit: { name: "任务生成智能体", duty: "把方案拆成四类任务" },
  unitcomp: { name: "指令生成智能体", duty: "把任务编成可下发指令" },
  parser: { name: "指令执行智能体", duty: "盯已下发和待下发" },
  eval: { name: "红方策略评估知识沉淀智能体", duty: "看这一仗哪对哪错" },
  tactics: { name: "策略评估智能体", duty: "对照成败，收紧打法" },
  knowledge: { name: "知识沉淀智能体", duty: "把改过的规矩写回库里" },
};

function moduleAgentOf(layerKey, extras = {}) {
  let agent = MODULE_AGENTS[layerKey] || MODULE_AGENTS.overview;
  if (layerKey === "awareness" && extras.observeSide === "blue") {
    agent = MODULE_AGENTS.awarenessBlue;
  } else if (layerKey === "plan") {
    const sub = extras.plannerTab;
    if (sub && MODULE_AGENTS[sub]) agent = MODULE_AGENTS[sub];
  } else if (layerKey === "eval") {
    if (extras.evalTab === "knowledge") agent = MODULE_AGENTS.knowledge;
    else if (extras.evalTab === "tactics") agent = MODULE_AGENTS.tactics;
  }
  return withAgentShort(agent);
}

const OODA_RINGS = [
  {
    key: "observe",
    english: "Observe",
    label: "观察",
    agent: "红方态势感知智能体",
    duty: "先把场上看清楚",
    defaultLayer: "observe",
    tabs: [
      { key: "obsStrike", label: "红方打击态势感知", agent: "红方打击态势感知智能体" },
      { key: "obsDetect", label: "红方探测态势感知", agent: "红方探测态势感知智能体" },
      { key: "obsDefense", label: "红方防御态势感知", agent: "红方防御态势感知智能体" },
      { key: "obsSupport", label: "红方保障态势感知", agent: "红方保障态势感知智能体" },
      { key: "obsEnvironment", label: "红方作战环境感知", agent: "红方作战环境感知智能体" },
    ],
  },
  {
    key: "orient",
    english: "Orient",
    label: "判断",
    agent: "红方态势认知智能体",
    duty: "把看见的事说成判断",
    defaultLayer: "orient",
    tabs: [
      { key: "oriStrike", label: "红方打击态势认知", agent: "红方打击态势认知智能体" },
      { key: "oriDetect", label: "红方探测态势认知", agent: "红方探测态势认知智能体" },
      { key: "oriDefense", label: "红方防御态势认知", agent: "红方防御态势认知智能体" },
      { key: "oriSupport", label: "红方保障态势认知", agent: "红方保障态势认知智能体" },
      { key: "oriEnvironment", label: "红方作战环境认知", agent: "红方作战环境认知智能体" },
    ],
  },
  {
    key: "decide",
    english: "Decide",
    label: "决策",
    agent: "红方决策方案生成智能体",
    duty: "两边过招，定下这一手",
    defaultLayer: "cotStrike",
    tabs: [
      { key: "cotStrike", label: "红方打击决策自博弈", agent: "红方打击决策自博弈思维链生成智能体" },
      { key: "cotDetect", label: "红方探测决策自博弈", agent: "红方探测决策自博弈思维链生成智能体" },
      { key: "cotDefense", label: "红方防御决策自博弈", agent: "红方防御决策自博弈思维链生成智能体" },
      { key: "cotSupport", label: "红方保障决策自博弈", agent: "红方保障决策自博弈思维链生成智能体" },
      { key: "cotEnvironment", label: "红方作战环境思维链", agent: "红方作战环境思维链生成智能体" },
      { key: "killnet", label: "红方杀伤网编排与方案规划", agent: "红方杀伤网编排与方案规划智能体" },
    ],
  },
  {
    key: "act",
    english: "Act",
    label: "行动",
    agent: "红方指挥控制与策略评估智能体",
    duty: "写成方案，拆到指令",
    defaultLayer: "plan",
    tabs: [
      { key: "plan", label: "红方指挥控制", agent: "红方指挥控制智能体" },
      { key: "eval", label: "红方策略评估知识沉淀", agent: "红方策略评估知识沉淀智能体" },
    ],
  },
];

/** 业务架构：按 OODA 四环展示本系统智能体（参考 Agent Harness） */
const OODA_ARCH_CAPS = [
  "任务拆解",
  "任务调度",
  "动态 Agent 路由",
  "MCP 工具调用",
  "追踪与记忆更新",
];

const OODA_ARCH_MEMORY = [
  "当前信念态势",
  "任务与规则约束",
  "候选思维链",
  "工具与推演结果",
  "执行轨迹",
];

const OODA_ARCH_COLUMNS = [
  {
    key: "observe",
    code: "O1",
    english: "Observe",
    label: "观察",
    hub: "大模型感知中枢",
    duty: "把战场事实、量化对照与兵力结构摊开",
    agents: [
      { name: "红方打击态势感知智能体", duty: "该维单位位置、对照与建议", action: { layer: "obsStrike" }, tone: "strike" },
      { name: "红方探测态势感知智能体", duty: "该维单位位置、对照与建议", action: { layer: "obsDetect" }, tone: "detect" },
      { name: "红方防御态势感知智能体", duty: "该维单位位置、对照与建议", action: { layer: "obsDefense" }, tone: "defense" },
      { name: "红方保障态势感知智能体", duty: "该维单位位置、对照与建议", action: { layer: "obsSupport" }, tone: "support" },
      { name: "红方作战环境感知智能体", duty: "摊开双方对关键地形与环境要点的控制", action: { layer: "obsEnvironment" }, tone: "env" },
    ],
  },
  {
    key: "orient",
    code: "O2",
    english: "Orient",
    label: "判断",
    hub: "态势认知与对手建模",
    duty: "把看见的事融成判断、意图与威胁假设",
    agents: [
      { name: "红方打击态势认知智能体", duty: "蓝方打击意图、脆弱点与关键节点", action: { layer: "oriStrike" }, tone: "strike" },
      { name: "红方探测态势认知智能体", duty: "蓝方侦察意图、视野缺口与协同", action: { layer: "oriDetect" }, tone: "detect" },
      { name: "红方防御态势认知智能体", duty: "蓝方防守意图、防线缺口与节点", action: { layer: "oriDefense" }, tone: "defense" },
      { name: "红方保障态势认知智能体", duty: "蓝方后勤意图、补给缺口与节点", action: { layer: "oriSupport" }, tone: "support" },
      { name: "红方作战环境认知智能体", duty: "把控点事实收成地形利用判断与关键节点", action: { layer: "oriEnvironment" }, tone: "env" },
    ],
  },
  {
    key: "decide",
    code: "D",
    english: "Decide",
    label: "决策",
    hub: "自博弈思维链与杀伤网",
    duty: "红蓝过招，收敛成红方方案并落成杀伤网",
    agents: [
      { name: "红方打击决策自博弈思维链生成智能体", duty: "该维红蓝过招，收敛打击应对", action: { layer: "cotStrike" }, tone: "strike" },
      { name: "红方探测决策自博弈思维链生成智能体", duty: "该维红蓝过招，收敛探测应对", action: { layer: "cotDetect" }, tone: "detect" },
      { name: "红方防御决策自博弈思维链生成智能体", duty: "该维红蓝过招，收敛防御应对", action: { layer: "cotDefense" }, tone: "defense" },
      { name: "红方保障决策自博弈思维链生成智能体", duty: "该维红蓝过招，收敛保障应对", action: { layer: "cotSupport" }, tone: "support" },
      { name: "红方作战环境思维链生成智能体", duty: "红蓝围绕地形利用过招，收敛本轮环境约束", action: { layer: "cotEnvironment" }, tone: "env" },
      { name: "红方打击方案规划智能体", duty: "写出红方打击这一手", action: { layer: "cotStrike" }, tone: "cot" },
      { name: "蓝方打击行为预测智能体", duty: "预估对面打击下一手", action: { layer: "cotStrike" }, tone: "cot" },
      { name: "红方打击方案反思纠正智能体", duty: "对照打击预测把方案拧正", action: { layer: "cotStrike" }, tone: "cot" },
      { name: "红方探测方案规划智能体", duty: "写出红方探测这一手", action: { layer: "cotDetect" }, tone: "cot" },
      { name: "蓝方探测行为预测智能体", duty: "预估对面探测下一手", action: { layer: "cotDetect" }, tone: "cot" },
      { name: "红方探测方案反思纠正智能体", duty: "对照探测预测把方案拧正", action: { layer: "cotDetect" }, tone: "cot" },
      { name: "红方防御方案规划智能体", duty: "写出红方防御这一手", action: { layer: "cotDefense" }, tone: "cot" },
      { name: "蓝方防御行为预测智能体", duty: "预估对面防御下一手", action: { layer: "cotDefense" }, tone: "cot" },
      { name: "红方防御方案反思纠正智能体", duty: "对照防御预测把方案拧正", action: { layer: "cotDefense" }, tone: "cot" },
      { name: "红方保障方案规划智能体", duty: "写出红方保障这一手", action: { layer: "cotSupport" }, tone: "cot" },
      { name: "蓝方保障行为预测智能体", duty: "预估对面保障下一手", action: { layer: "cotSupport" }, tone: "cot" },
      { name: "红方保障方案反思纠正智能体", duty: "对照保障预测把方案拧正", action: { layer: "cotSupport" }, tone: "cot" },
      { name: "红方兵力布势方案规划智能体", duty: "写出红方本轮编成落位", action: { layer: "cotEnvironment" }, tone: "cot" },
      { name: "蓝方兵力布势行为预测智能体", duty: "预估对面兵力如何摆", action: { layer: "cotEnvironment" }, tone: "cot" },
      { name: "红方兵力布势方案反思纠正智能体", duty: "对照蓝方布势把落位拧正", action: { layer: "cotEnvironment" }, tone: "cot" },
      { name: "红方方案总结智能体", duty: "收敛本轮最终结论", action: { layer: "killnet" }, tone: "cot" },
      { name: "红方杀伤网编排与方案规划智能体", duty: "按打探防保把应对落成网，并给出本轮方案", action: { layer: "killnet" }, tone: "killnet" },
      { name: "红方杀伤网编排智能体", duty: "把应对落成打探防保杀伤网", action: { layer: "killnet" }, tone: "killnet" },
    ],
  },
  {
    key: "act",
    code: "A",
    english: "Act",
    label: "行动",
    hub: "指挥控制与评估沉淀",
    duty: "写成方案、拆到指令，并对照成败写回经验库",
    agents: [
      { name: "红方指挥控制智能体", duty: "把应对写成可执行方案并指挥控制", action: { layer: "plan", plannerTab: "bus" }, tone: "pipe" },
      { name: "方案生成智能体", duty: "写出这一轮作战方案", action: { layer: "plan", plannerTab: "bus" }, tone: "pipe" },
      { name: "任务生成智能体", duty: "把方案拆成打探防保四类任务", action: { layer: "plan", plannerTab: "tasksplit" }, tone: "pipe" },
      { name: "指令生成智能体", duty: "把任务编成可下发指令", action: { layer: "plan", plannerTab: "unitcomp" }, tone: "pipe" },
      { name: "指令执行智能体", duty: "盯已下发与待下发", action: { layer: "plan", plannerTab: "parser" }, tone: "pipe" },
      { name: "红方策略评估知识沉淀智能体", duty: "看这一仗哪对哪错", action: { layer: "eval", evalTab: "tactics" }, tone: "eval" },
      { name: "策略评估智能体", duty: "对照成败，收紧打法", action: { layer: "eval", evalTab: "tactics" }, tone: "eval" },
      { name: "知识沉淀智能体", duty: "把改过的规矩写回库里", action: { layer: "eval", evalTab: "knowledge" }, tone: "eval" },
      { name: "历史知识修订智能体", duty: "对本轮既有记忆做修正或补充", action: { layer: "eval", evalTab: "knowledge" }, tone: "eval" },
      { name: "经验库沉淀智能体", duty: "沉淀条目与规则修订合一展示", action: { layer: "eval", evalTab: "knowledge" }, tone: "eval" },
    ],
  },
];

/** 知识沉淀：对历史记忆的修正 / 补充（写死演示 4 条） */
const KNOWLEDGE_REVISION_ITEMS = [
  {
    key: "rev-1",
    kind: "revise",
    kindLabel: "历史记忆修正",
    target: "先中路开雾、侧翼后置",
    change: "改为峡谷口与东翼双通道并行开雾",
    reason: "只开中路会让对手侧翼先完成意图修正，信息主动权丢失。",
  },
  {
    key: "rev-2",
    kind: "revise",
    kindLabel: "历史记忆修正",
    target: "强欺骗可重复用于打乱对方判断",
    change: "对手已误判一次后，强欺骗降级为弱诱导",
    reason: "二次强欺骗收益骤降，会把前线资源耗在无效脚本上。",
  },
  {
    key: "rev-3",
    kind: "append",
    kindLabel: "历史记忆补充",
    target: "正面防守骨架",
    change: "补充「凹字护航克一字推进」：毒爆居中、刺蛇两翼",
    reason: "对方拆网意识越强，越需要用护航阵型换侧翼切入角。",
  },
  {
    key: "rev-4",
    kind: "append",
    kindLabel: "历史记忆补充",
    target: "三矿开启门槛",
    change: "补充「开扩与护航必须同迁」，禁止单独开扩",
    reason: "护航留在旧前线时，新经济节点会形成空窗。",
  },
];

const OODA_STEPS = [
  {
    key: "observe",
    code: "O",
    label: "红方态势感知智能体",
    hint: "打/探/防/保/环境",
    layers: ["observe", "obsStrike", "obsDetect", "obsDefense", "obsSupport", "obsEnvironment", "awareness", "force"],
  },
  {
    key: "orient",
    code: "O",
    label: "红方态势认知智能体",
    hint: "打/探/防/保/环境",
    layers: ["orient", "oriStrike", "oriDetect", "oriDefense", "oriSupport", "oriEnvironment", "intent", "risk", "killchain", "critical"],
  },
  {
    key: "decide",
    code: "D",
    label: "红方决策方案生成智能体",
    hint: "思维链/杀伤网",
    layers: ["cot", "cotStrike", "cotDetect", "cotDefense", "cotSupport", "cotEnvironment", "killnet"],
  },
  {
    key: "act",
    code: "A",
    label: "红方指挥控制与策略评估智能体",
    hint: "筹划/评估",
    layers: ["plan", "eval"],
  },
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
  environment: "作战环境",
};

/** 任务/指令分列：全部时按打探防保四维；作战环境与保障一样走单列横栏 */
const TASK_LANE_KEYS = ["strike", "detect", "defense", "support"];

/** 与作战环境（地形/控点/走廊等）关联的任务文案 */
const ENVIRONMENT_TASK_RE =
  /作战环境|地形|高地|斜坡|隘口|峡谷|走廊|岩石|开岩|瞭望|塔权|控点|南口|北口|东翼|西翼|南坡|北坡|开阔|俯射|折线|通道|窗口|地形利用|脱高地|贴口|封口|卡口/;

function isEnvironmentRelatedTask(textInput) {
  return ENVIRONMENT_TASK_RE.test(String(textInput || ""));
}

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
const SC2_CN_TO_EN = Object.create(null);
for (const [k, v] of Object.entries(SC2_UNIT_CN)) {
  const n = normalizeSc2Token(k);
  if (n) SC2_UNIT_LOOKUP[n] = v;
  const zh = normalizeSc2Token(v);
  if (zh && n) SC2_CN_TO_EN[zh] = n;
}

function sc2EnglishToken(name) {
  const n = normalizeSc2Token(name);
  if (!n) return "";
  return SC2_CN_TO_EN[n] || n;
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

const PLANNER_TASK_MIN_WORDS = 10;

const PLANNER_TASK_HEADING_RE = /^(current stage of the match|our zerg units(?: and buildings)?|enemy'?s? units|enemy'?s? buildings|whether to attack|our current zerg operational strategy|opponent'?s? units and buildings|opponent'?s? tactical plan(?: and our potential risks)?|scouting intel|analyze based on|based on above analysis|response format|true|false|对局阶段|红方(?:兵力|编制)?|蓝方(?:兵力|设施|编制)?|作战决心|作战策略|侦察情报|校验备忘|对手意图与风险)[:：.]?$/i;

function countPlannerTaskWords(text) {
  const s = String(text || "");
  const latin = s.match(/[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?/g) || [];
  const cjk = s.match(/[\u4e00-\u9fff]/g) || [];
  return latin.length + Math.ceil(cjk.length / 2);
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
  if (
    /support|economy|expand|macro|mineral|vespene|drone|queen|inject|larva|hatchery|extractor|upgrade|research|train|build|produce|supply|creep|保障|补给|生产|训练|建造|扩张|采矿|采气|工蜂|经济|三矿|二矿|升级|科技|菌毯|幼虫|注射|产能|开矿|补兵|补员/.test(text)
  ) {
    return "support";
  }
  return "";
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
    if (PLANNER_TASK_HEADING_RE.test(modelLine)) continue;
    if (countPlannerTaskWords(modelLine) < PLANNER_TASK_MIN_WORDS) continue;
    const type = inferTaskPartitionType(modelLine);
    if (!type) continue;
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

const PLAN_INTENT_LABELS = {
  mid_timing_attack: "中期时机进攻",
  early_all_in: "前期全力一击",
  all_in: "全力一击",
  fast_expand: "快速扩张",
  tech_climb: "科技爬升",
  defend: "转入防守",
  hold: "按兵待机",
  harass: "牵制袭扰",
  contain: "封锁压制",
  counter: "反打窗口",
  ambiguous: "灵活待定",
};

const PLAN_SECTION_KIND_RULES = [
  { test: /whether to attack|是否进攻|进攻决策|交战/i, kind: "verdict", label: "交战裁决" },
  { test: /our (zerg )?units|我方.*(单位|兵力|部队)|红方.*(单位|兵力|部队)/i, kind: "ours", label: "红方兵力" },
  { test: /enemy'?s? units|opponent'?s? units|敌方.*(单位|兵力|部队)|蓝方.*(单位|兵力|部队)/i, kind: "enemy", label: "蓝方兵力" },
  { test: /enemy'?s? buildings|opponent'?s? buildings|建筑/i, kind: "infra", label: "设施态势" },
  { test: /current stage|对局阶段|阶段/i, kind: "stage", label: "对局阶段" },
  { test: /operational strategy|作战决心|作战策略|策略|方案/i, kind: "plan", label: "作战决心" },
  { test: /risk|风险/i, kind: "risk", label: "风险研判" },
  { test: /scout|侦察|intel|情报/i, kind: "detect", label: "侦察情报" },
  { test: /memory|self-verification|校验|备忘/i, kind: "meta", label: "校验备忘" },
];

const PLAN_TITLE_ZH = {
  "Our Zerg Units": "红方兵力",
  "Enemy's Units": "蓝方兵力",
  "Enemy's Buildings": "蓝方设施",
  "Current Stage of the Match": "对局阶段",
  "Whether to attack": "是否交战",
  "Our Zerg Units and Buildings": "红方编制",
  "Our current Zerg Operational Strategy": "作战决心",
  "Opponent's Units and Buildings": "敌情编制",
  "Opponent's Tactical Plan and our Potential Risks": "对手意图与风险",
  "Scouting Intel": "侦察情报",
};

function planIntentLabel(key) {
  const raw = String(key || "").trim();
  if (!raw) return "待定";
  if (PLAN_INTENT_LABELS[raw]) return PLAN_INTENT_LABELS[raw];
  return raw.replace(/_/g, " ");
}

function classifyPlanSection(title, preview) {
  const hay = `${title} ${preview || ""}`;
  const hit = PLAN_SECTION_KIND_RULES.find((rule) => rule.test.test(hay));
  return hit || { kind: "note", label: "研判" };
}

function extractPlanVerdictFlag(body) {
  const s = String(body || "").trim();
  if (/^(yes|true|是|进攻|攻击|打)\b/i.test(s)) return "go";
  if (/^(no|false|否|不|防守|等)\b/i.test(s)) return "hold";
  return "";
}

function stripPlannerFences(text) {
  const jsonBlocks = [];
  const cleaned = String(text || "").replace(/```json\s*([\s\S]*?)```/gi, (_, raw) => {
    jsonBlocks.push(String(raw || "").trim());
    return "\n";
  });
  let intent = null;
  for (const raw of jsonBlocks) {
    try {
      const obj = JSON.parse(raw);
      const sp = obj?.strategic_plan && typeof obj.strategic_plan === "object" ? obj.strategic_plan : obj;
      if (sp && (sp.primary_intent || sp.intent_confidence || sp.intent_rationale_short)) {
        intent = {
          primary: String(sp.primary_intent || ""),
          secondary: Array.isArray(sp.secondary_intents) ? sp.secondary_intents.map((x) => String(x || "")).filter(Boolean) : [],
          confidence: Number(sp.intent_confidence),
          rationale: String(sp.intent_rationale_short || "").trim(),
          horizon: Number(sp.horizon_sec) || 0,
        };
      }
    } catch {
      /* ignore malformed fence */
    }
  }
  return { text: cleaned, intent };
}

function composePlannerDossier(text) {
  const { text: cleaned, intent } = stripPlannerFences(text);
  let body = cleaned
    .replace(/Based on above analysis[\s\S]*$/i, "")
    .replace(/\{\{\s*[\s\S]*?\}\}/g, "")
    .replace(/\n-{3,}\s*$/g, "")
    .trim();
  const split = body.split(/(?:^|\n)\s*(\d+)\.\s+/);
  const sections = [];
  if (split.length >= 3) {
    for (let i = 1; i < split.length; i += 2) {
      const index = String(split[i] || "").padStart(2, "0");
      const chunk = String(split[i + 1] || "").trim();
      if (!chunk || /^(true|false)$/i.test(chunk) || chunk.length < 8) continue;
      let title = "";
      let rest = chunk;
      const md = chunk.match(/^\*\*(.+?)\*\*\s*:?\s*([\s\S]*)$/);
      const col = chunk.match(/^([^:\n]{2,64}):\s*([\s\S]*)$/);
      if (md) {
        title = md[1].replace(/\*+/g, "").trim();
        rest = String(md[2] || "").trim();
      } else if (col) {
        title = col[1].replace(/\*+/g, "").trim();
        rest = String(col[2] || "").trim();
      }
      const classified = classifyPlanSection(title, chunk.slice(0, 80));
      const zhTitle = PLAN_TITLE_ZH[title] || title || classified.label;
      sections.push({
        index,
        title: zhTitle,
        body: rest || chunk,
        kind: classified.kind,
        kindLabel: classified.label,
        flag: classified.kind === "verdict" ? extractPlanVerdictFlag(rest || chunk) : "",
      });
    }
  }
  return {
    structured: sections.length >= 2,
    sections,
    intent,
    leftover: sections.length >= 2 ? "" : body,
  };
}

function deriveUnitMappingForTask(task, ownPoolByType) {
  const type = TASK_LANE_KEYS.includes(task?.type) ? task.type : "support";
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

const OBSERVE_SENSE_LAYER_CAP = {
  obsStrike: "strike",
  obsDetect: "detect",
  obsDefense: "defense",
  obsSupport: "support",
};

/**
 * 作战环境感知 · 控制地形对比（模板键；数值由战局启发式填充，开局无证据则为 0）
 */
const OBSERVE_ENVIRONMENT_TERRAIN_KEYS = [
  { key: "highground", label: "高地", ownHints: ["高地", "b2", "南坡", "北坡", "high"], enemyHints: ["高地", "b2", "南坡", "北坡", "high"] },
  { key: "ramp", label: "斜坡", ownHints: ["斜坡", "坡", "ramp", "出口"], enemyHints: ["斜坡", "坡", "ramp", "出口"] },
  { key: "choke", label: "隘口", ownHints: ["隘口", "峡谷", "窄廊", "choke", "口"], enemyHints: ["隘口", "峡谷", "窄廊", "choke", "口"] },
  { key: "rock", label: "可破坏岩石", ownHints: ["岩石", "rock", "开岩"], enemyHints: ["岩石", "rock", "开岩"] },
  { key: "tower", label: "瞭望塔", ownHints: ["瞭望", "塔", "xel", "watch"], enemyHints: ["瞭望", "塔", "xel", "watch"] },
  { key: "minerals", label: "矿区", ownHints: ["矿", "mineral", "主矿", "二矿", "三矿"], enemyHints: ["矿", "mineral", "主矿", "二矿", "三矿"] },
  { key: "gas", label: "气矿点", ownHints: ["气", "瓦斯", "vespene", "gas"], enemyHints: ["气", "瓦斯", "vespene", "gas"] },
];

function _envHitCount(labels, hints) {
  const rows = Array.isArray(labels) ? labels : [];
  const needles = Array.isArray(hints) ? hints : [];
  let n = 0;
  for (const raw of rows) {
    const text = String(raw || "").toLowerCase();
    if (!text) continue;
    if (needles.some((h) => text.includes(String(h).toLowerCase()))) n += 1;
  }
  return n;
}

function buildObserveEnvironmentBoard(ownLabel, enemyLabel, ctx = {}) {
  const live = Boolean(ctx.live);
  const ownLocs = Array.isArray(ctx.ownLocations) ? ctx.ownLocations : [];
  const enemyLocs = Array.isArray(ctx.enemyLocations) ? ctx.enemyLocations : [];
  const ownBases = Math.max(0, Number(ctx.ownBases) || 0);
  const enemyBases = Math.max(0, Number(ctx.enemyBasesSeen) || 0);
  const ownMinerals = Math.max(0, Number(ctx.ownMinerals) || 0);
  const ownVespene = Math.max(0, Number(ctx.ownVespene) || 0);
  const enemyVisible = Math.max(0, Number(ctx.enemyVisibleSupply) || 0);
  const visionPct = ctx.visiblePct != null ? Number(ctx.visiblePct) : null;

  const terrains = OBSERVE_ENVIRONMENT_TERRAIN_KEYS.map((meta) => {
    let own = 0;
    let enemy = 0;
    let ownNote = "未见控点证据";
    let enemyNote = "未见控点证据";
    if (live) {
      if (meta.key === "minerals") {
        own = ownBases || (ownMinerals > 0 ? 1 : 0);
        enemy = enemyBases;
        ownNote = own
          ? `基地 ${ownBases || "—"} · 晶矿 ${ownMinerals || "—"}`
          : "尚未形成稳控矿区证据";
        enemyNote = enemy
          ? `可见基地迹象 ${enemyBases}`
          : "未见敌方矿区控点";
      } else if (meta.key === "gas") {
        own = ownVespene > 0 || ownBases > 0 ? Math.min(2, Math.max(ownBases, ownVespene > 100 ? 1 : 0)) : 0;
        enemy = enemyBases > 0 ? Math.min(2, enemyBases) : 0;
        ownNote = own ? `瓦斯 ${ownVespene || "—"} · 基地 ${ownBases || "—"}` : "未见气矿运转证据";
        enemyNote = enemy ? `可见基地 ${enemyBases}（气矿未直接确认）` : "未见敌方气矿证据";
      } else {
        own = _envHitCount(ownLocs, meta.ownHints);
        enemy = _envHitCount(enemyLocs, meta.enemyHints);
        const ownHits = ownLocs.filter((loc) => meta.ownHints.some((h) => String(loc).toLowerCase().includes(String(h).toLowerCase())));
        const enemyHits = enemyLocs.filter((loc) => meta.enemyHints.some((h) => String(loc).toLowerCase().includes(String(h).toLowerCase())));
        ownNote = ownHits.length ? ownHits.slice(0, 3).join("、") : "部署标签中未见该地形证据";
        enemyNote = enemyHits.length ? enemyHits.slice(0, 3).join("、") : "可见敌情中未见该地形证据";
      }
    }
    const lead = own > enemy ? "own" : enemy > own ? "enemy" : "even";
    return {
      key: meta.key,
      label: meta.label,
      own,
      enemy,
      ownNote,
      enemyNote,
      lead,
      contested: own > 0 && enemy > 0,
      statusLabel: lead === "own" ? `${ownLabel}控` : lead === "enemy" ? `${enemyLabel}控` : own || enemy ? "争夺中" : "未控",
    };
  });

  const ownCount = terrains.reduce((sum, row) => sum + row.own, 0);
  const enemyCount = terrains.reduce((sum, row) => sum + row.enemy, 0);
  const diff = ownCount - enemyCount;
  const diffLabel = !live
    ? "等待态势"
    : diff > 0
      ? `${ownLabel} +${diff}`
      : diff < 0
        ? `${enemyLabel} +${Math.abs(diff)}`
        : ownCount || enemyCount
          ? "势均力敌"
          : "暂无控点";
  const ownHeld = terrains.filter((row) => row.own > 0).map((row) => `${row.label}×${row.own}`);
  const enemyHeld = terrains.filter((row) => row.enemy > 0).map((row) => `${row.label}×${row.enemy}`);
  const ownHot = terrains.filter((row) => row.lead === "own").map((row) => row.label);
  const enemyHot = terrains.filter((row) => row.lead === "enemy").map((row) => row.label);
  const contested = terrains.filter((row) => row.contested);

  const scoreFrom = (own, enemy) => {
    if (!live) return { ownScore: 0, enemyScore: 0 };
    const o = Math.max(0, Number(own) || 0);
    const e = Math.max(0, Number(enemy) || 0);
    if (!o && !e) return { ownScore: 0, enemyScore: 0 };
    const base = 42;
    return {
      ownScore: Math.min(92, base + o * 18),
      enemyScore: Math.min(92, base + e * 18),
    };
  };
  const deepSpecs = [
    {
      key: "fireseat",
      label: "高地火力位",
      ...scoreFrom(
        terrains.find((r) => r.key === "highground")?.own,
        terrains.find((r) => r.key === "highground")?.enemy,
      ),
      ownNote: live ? (terrains.find((r) => r.key === "highground")?.ownNote || "—") : "等待态势",
      enemyNote: live ? (terrains.find((r) => r.key === "highground")?.enemyNote || "—") : "等待态势",
    },
    {
      key: "chokelock",
      label: "隘口封锁度",
      ...scoreFrom(
        terrains.find((r) => r.key === "choke")?.own,
        terrains.find((r) => r.key === "choke")?.enemy,
      ),
      ownNote: live ? (terrains.find((r) => r.key === "choke")?.ownNote || "—") : "等待态势",
      enemyNote: live ? (terrains.find((r) => r.key === "choke")?.enemyNote || "—") : "等待态势",
    },
    {
      key: "vision",
      label: "视野制高点",
      ownScore: live ? Math.min(90, Math.round((visionPct != null ? visionPct : 0) * 0.7 + (terrains.find((r) => r.key === "tower")?.own || 0) * 18)) : 0,
      enemyScore: live ? Math.min(90, (terrains.find((r) => r.key === "tower")?.enemy || 0) * 22 + (enemyVisible > 0 ? 20 : 0)) : 0,
      ownNote: live
        ? `视野 ${visionPct != null ? `${Math.round(visionPct)}%` : "—"} · 塔控 ${terrains.find((r) => r.key === "tower")?.own || 0}`
        : "等待态势",
      enemyNote: live
        ? `敌可见编制迹象 ${enemyVisible || 0} · 塔控 ${terrains.find((r) => r.key === "tower")?.enemy || 0}`
        : "等待态势",
    },
    {
      key: "corridor",
      label: "机动走廊",
      ...scoreFrom(
        terrains.find((r) => r.key === "rock")?.enemy, // 对方拆岩≈对方走廊更活；我方岩石仍在则走廊偏少
        terrains.find((r) => r.key === "rock")?.own,
      ),
      ownNote: live ? (terrains.find((r) => r.key === "rock")?.ownNote || "未见开岩证据") : "等待态势",
      enemyNote: live ? (terrains.find((r) => r.key === "rock")?.enemyNote || "未见开岩证据") : "等待态势",
    },
    {
      key: "depth",
      label: "接敌缓冲纵深",
      ...scoreFrom(ownBases, enemyBases),
      ownNote: live ? `基地纵深 ${ownBases || 0}` : "等待态势",
      enemyNote: live ? `可见敌基地 ${enemyBases || 0}` : "等待态势",
    },
  ];
  const deepRows = deepSpecs.map((row) => {
    const pair = observeSenseScorePair(row.ownScore, row.enemyScore);
    return {
      label: row.label,
      ...pair,
      ownNote: row.ownNote,
      enemyNote: row.enemyNote,
      leadLabel: !live
        ? "等待态势"
        : pair.lead === "own"
          ? `${ownLabel}占优`
          : pair.lead === "enemy"
            ? `${enemyLabel}占优`
            : "基本相当",
    };
  });
  const ownLead = deepRows.filter((row) => row.lead === "own").length;
  const enemyLead = deepRows.filter((row) => row.lead === "enemy").length;
  const deepLead = !live
    ? "等待态势接入"
    : ownLead > enemyLead
      ? ownLabel
      : enemyLead > ownLead
        ? enemyLabel
        : "双方基本相当";
  const leadBits = deepRows.filter((row) => row.lead !== "even").map((row) => `${row.label}·${row.leadLabel}`);
  const question = "作战环境优劣对比";
  const summary = !live
    ? `${question}：对局态势尚未接入，控制地形对比暂不展示样例控点。`
    : `${question}：已控地形要点 ${ownLabel} ${ownCount} / ${enemyLabel} ${enemyCount}。深层对比中我方领先 ${ownLead} 项、对方领先 ${enemyLead} 项，综合判断为${deepLead}。我方控点偏 ${ownHot.join("、") || "暂无"}；敌方控点偏 ${enemyHot.join("、") || "暂无"}。${contested.length ? `争夺中：${contested.map((row) => row.label).join("、")}。` : "当前无同点争夺。"}`;
  const suggestionSummary = !live
    ? "接入对局后，将按可见部署与资源证据刷新控点。"
    : leadBits.length
      ? `建议优先巩固已领先项（${leadBits.slice(0, 2).join("、")}），对短板项先补控点再谈交战。`
      : ownCount || enemyCount
        ? "双方环境优劣尚未拉开，宜先稳控隘口与高地，再评估开岩石与扩矿。"
        : "当前未见明确地形控点证据，先补侦察与矿线视野，再谈抢点。";
  const suggestionDetail = !live
    ? ""
    : enemyLead > ownLead
      ? "对方环境项领先更多，不宜在开阔面硬接；可先抢信息与高地火力位换主动。"
      : "可将交战窗压在有地形依托的方向，避免被诱到开阔走廊。";
  return {
    key: "environment",
    tone: "environment",
    title: "红方作战环境感知智能体",
    duty: "摊开双方对关键地形与环境要点的控制",
    label: "作战环境",
    question,
    ownCount,
    enemyCount,
    diffLabel,
    ownHeld: ownHeld.length ? ownHeld : ["暂无已控要点"],
    enemyHeld: enemyHeld.length ? enemyHeld : ["暂无已控要点"],
    ownHotText: ownHot.join("、") || "未形成稳控",
    enemyHotText: enemyHot.join("、") || "未形成稳控",
    terrains,
    deepRows,
    deepLead,
    ownLead,
    enemyLead,
    summary,
    suggestionSummary,
    suggestionDetail,
    suggestionTitle: "作战环境建议",
    llmFromApi: false,
    live,
  };
}

/**
 * 作战环境认知：把感知层的控点事实收成判断链
 * 对齐四维认知：意图预测 → 风险研判 → 脆弱性预测 → 关键节点预测 → 分析
 * 风险取红方视角，脆弱/节点取蓝方视角，证据口径成对对齐
 */
function buildOrientEnvironmentFlow(ownLabel, enemyLabel) {
  const stages = [
    {
      key: "intent",
      title: "蓝方地形利用意图预测",
      text: "预测蓝方优先「拆岩开走廊 + 侧翼改线」，避开峡谷南口高地正面对抗，把交战拉到开阔机动面。",
      evidenceItems: [
        `${enemyLabel}西翼岩石已拆，具备绕行条件`,
        "北口可封但宽度不足，硬顶南口高地成本高",
        "缺瞭望塔时，更依赖侧翼轻侦回报驱动改线",
      ],
    },
    {
      key: "risk",
      title: "红方作战环境风险研判",
      text: "红方主要风险：东翼未开岩导致走廊偏少，一旦被诱出高地到开阔面交战，俯射优势会被摊薄，侧翼易被拉开。",
      evidenceItems: [
        "机动走廊：东翼岩石未拆，红方绕行选项少于蓝方",
        "交战面：脱南坡/B2 高地后，红方火力位优势难保持",
        "侧翼：东翼窄廊仅半控，被渗透后回填成本高",
      ],
    },
    {
      key: "vuln",
      title: "蓝方作战环境脆弱性预测",
      text: "蓝方主要脆弱：北口到主矿缓冲短、缺塔控信息、正面对高地难展开——侧翼活、正面脆。",
      evidenceItems: [
        "接敌缓冲：北口到主矿纵深短，一破即压矿",
        "视野制高：未稳控瞭望塔，侧翼改线依赖轻侦，易迟滞",
        "高地对抗：北坡宽度不足，难同时压制两向，正面对南口吃亏",
      ],
    },
    {
      key: "nodes",
      title: "蓝方关键地形利用预测",
      text: "蓝方关键节点优先序：西翼已开走廊出口 → 北口隘口骨架 → 中路瞭望塔争夺点（信息差）。",
      evidenceItems: [
        "西翼走廊出口：蓝方侧翼施压的主通道",
        "峡谷北口：蓝方封线与回撤依托",
        "中路瞭望塔：蓝方若丢失，侧翼改线会更盲目",
      ],
    },
  ];
  const conclusion = "红方应对：交战贴南口高地与隘口，不跟到开阔走廊；续控瞭望塔压蓝方信息；东翼开岩仅作条件动作。利用蓝方「正面脆、缓冲短」，避免掉进其「侧翼活」脚本。";
  const briefing = [
    "蓝方意图预测：拆岩开走廊、侧翼改线，不硬顶南口高地。",
    "红方风险：走廊少、脱高地则俯射失效，东翼半控易被撕。",
    "蓝方脆弱：北口缓冲短、缺塔、正面难展开。",
    "蓝方关键节点：西翼走廊出口、北口、中路瞭望塔。",
    conclusion,
  ].join(" ");
  return {
    title: "红方作战环境认知智能体",
    duty: "把控点事实收成地形利用判断与关键节点",
    pipelineLabel: "蓝方地形利用意图预测 → 红方作战环境风险研判 → 蓝方作战环境脆弱性预测 → 蓝方关键地形利用预测 → 红方作战环境分析",
    stages,
    conclusionTitle: "红方作战环境分析",
    conclusion,
    briefing,
  };
}

const ORIENT_SENSE_LAYER_CAP = {
  oriStrike: "strike",
  oriDetect: "detect",
  oriDefense: "defense",
  oriSupport: "support",
};

const COT_SENSE_LAYER_CAP = {
  cot: "strike",
  cotStrike: "strike",
  cotDetect: "detect",
  cotDefense: "defense",
  cotSupport: "support",
};

const ORIENT_CAP_TO_INTENT = {
  strike: "attack",
  detect: "detect",
  defense: "defense",
  support: "support",
};

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

function observeSenseUnitToken(name) {
  return sc2EnglishToken(name);
}

function observeSenseFirepowerWeight(name) {
  const n = observeSenseUnitToken(name);
  if (/siegetank|colossus|battlecruiser|carrier|thor|lurker|liberator|tempest|broodlord|ultralisk|disruptor/.test(n)) return 8;
  if (/ravager|baneling|banshee|voidray|immortal|marauder|hydralisk|mutalisk|ghost|cyclone|infestor/.test(n)) return 5;
  if (/marine|roach|stalker|zealot|adept|zergling|hellion|reaper|phoenix|queen|viking/.test(n)) return 3;
  return 2;
}

function observeSenseCanHitAir(name) {
  const n = observeSenseUnitToken(name);
  return /marine|hydralisk|queen|thor|viking|corruptor|phoenix|voidray|tempest|carrier|mutalisk|battlecruiser|spore|missileturret|photoncannon|stalker|archon|liberator|raven/.test(n);
}

function observeSenseCanHitGround(name) {
  const n = observeSenseUnitToken(name);
  return !/corruptor|phoenix|vikingfighter/.test(n) || /viking|thor|marine|liberator/.test(n);
}

function observeSenseAntiBuilding(name) {
  const n = observeSenseUnitToken(name);
  return /siegetank|ravager|battlecruiser|lurker|colossus|tank|baneling|tempest|liberator/.test(n);
}

function observeSenseIsDetector(name) {
  const n = observeSenseUnitToken(name);
  return /overseer|observer|raven|oracle|spore|missileturret|photoncannon|sensortower|ghost/.test(n);
}

function observeSenseIsCloakThreat(name) {
  const n = observeSenseUnitToken(name);
  return /banshee|darktemplar|lurker|ghost|widowmine/.test(n);
}

function observeSenseIsStaticDefense(name, kind) {
  const n = observeSenseUnitToken(name);
  return kind === "structure" || /spine|spore|bunker|turret|cannon|battery|planetary|sensortower/.test(n);
}

function observeSenseIsProduction(name) {
  const n = observeSenseUnitToken(name);
  return /hatchery|lair|hive|barracks|factory|starport|gateway|warpgate|roboticsfacility|stargate|commandcenter|orbital|nexus/.test(n);
}

function observeSenseIsTransportOrHeal(name) {
  const n = observeSenseUnitToken(name);
  return /medivac|warpprism|nydus|overlord|queen|drop/.test(n);
}

function observeSenseScorePair(own, enemy) {
  const ownVal = Math.max(0, Number(own) || 0);
  const enemyVal = Math.max(0, Number(enemy) || 0);
  const maxSide = Math.max(ownVal, enemyVal, 1);
  let lead = "even";
  if (ownVal > enemyVal * 1.08 && ownVal - enemyVal >= 1) lead = "own";
  if (enemyVal > ownVal * 1.08 && enemyVal - ownVal >= 1) lead = "enemy";
  return {
    own: ownVal,
    enemy: enemyVal,
    ownBar: Math.round((ownVal / maxSide) * 100),
    enemyBar: Math.round((enemyVal / maxSide) * 100),
    lead,
  };
}

function buildObserveSenseDeepRows(key, ctx) {
  const ownItems = ctx.ownItems || [];
  const enemyItems = ctx.enemyItems || [];
  const countOf = (rows, pred) => rows.reduce((sum, row) => sum + (pred(row) ? Number(row.count) || 0 : 0), 0);
  const locSet = (rows) => new Set(rows.map((row) => row.location).filter((loc) => loc && loc !== "--" && loc !== "未知区域"));
  const rangeScore = (rows, role) =>
    rows.reduce((sum, row) => sum + getUnitRangePct(row.name, role) * (Number(row.count) || 0), 0);
  const namePreview = (rows, pred, limit = 3) => {
    const counter = Object.create(null);
    for (const row of rows) {
      if (pred && !pred(row)) continue;
      counter[row.name] = (counter[row.name] || 0) + (Number(row.count) || 0);
    }
    return Object.entries(counter)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, limit)
      .map(([name, count]) => `${name}×${count}`)
      .join("、") || "—";
  };
  const pack = (label, ownScore, enemyScore, ownNote, enemyNote) => {
    const pair = observeSenseScorePair(ownScore, enemyScore);
    return {
      label,
      ...pair,
      ownNote,
      enemyNote,
      leadLabel: pair.lead === "own" ? `${ctx.ownLabel}占优` : pair.lead === "enemy" ? `${ctx.enemyLabel}占优` : "基本相当",
    };
  };

  if (key === "strike") {
    const ownFire = ownItems.reduce((sum, row) => sum + observeSenseFirepowerWeight(row.name) * (Number(row.count) || 0), 0);
    const enemyFire = enemyItems.reduce((sum, row) => sum + observeSenseFirepowerWeight(row.name) * (Number(row.count) || 0), 0);
    const ownTargets = (countOf(ownItems, (row) => observeSenseCanHitGround(row.name)) > 0 ? 1 : 0)
      + (countOf(ownItems, (row) => observeSenseCanHitAir(row.name)) > 0 ? 1 : 0)
      + (countOf(ownItems, (row) => observeSenseAntiBuilding(row.name)) > 0 ? 1 : 0);
    const enemyTargets = (countOf(enemyItems, (row) => observeSenseCanHitGround(row.name)) > 0 ? 1 : 0)
      + (countOf(enemyItems, (row) => observeSenseCanHitAir(row.name)) > 0 ? 1 : 0)
      + (countOf(enemyItems, (row) => observeSenseAntiBuilding(row.name)) > 0 ? 1 : 0);
    const ownEngage = ownFire + rangeScore(ownItems, "strike") + ctx.contestedOwn * 4;
    const enemyEngage = enemyFire + rangeScore(enemyItems, "strike") + ctx.contestedEnemy * 4;
    const ownCover = locSet(ownItems).size * 8 + rangeScore(ownItems, "strike");
    const enemyCover = locSet(enemyItems).size * 8 + rangeScore(enemyItems, "strike");
    return [
      pack("火力", ownFire, enemyFire,
        namePreview(ownItems, (row) => observeSenseFirepowerWeight(row.name) >= 5) || `可见打击 ${ctx.ownCount}`,
        namePreview(enemyItems, (row) => observeSenseFirepowerWeight(row.name) >= 5) || `可见打击 ${ctx.enemyCount}`),
      pack("可攻击目标", ownTargets * 20, enemyTargets * 20,
        `对地 ${countOf(ownItems, (row) => observeSenseCanHitGround(row.name))} · 对空 ${countOf(ownItems, (row) => observeSenseCanHitAir(row.name))} · 对建筑 ${countOf(ownItems, (row) => observeSenseAntiBuilding(row.name))}`,
        `对地 ${countOf(enemyItems, (row) => observeSenseCanHitGround(row.name))} · 对空 ${countOf(enemyItems, (row) => observeSenseCanHitAir(row.name))} · 对建筑 ${countOf(enemyItems, (row) => observeSenseAntiBuilding(row.name))}`),
      pack("交战优势", ownEngage, enemyEngage,
        `接触点 ${ctx.contestedOwn} · 射程加权 ${rangeScore(ownItems, "strike").toFixed(0)}`,
        `接触点 ${ctx.contestedEnemy} · 射程加权 ${rangeScore(enemyItems, "strike").toFixed(0)}`),
      pack("火力覆盖", ownCover, enemyCover,
        `覆盖 ${locSet(ownItems).size} 个区域`,
        `可见覆盖 ${locSet(enemyItems).size} 个区域`),
    ];
  }

  if (key === "detect") {
    const ownVision = (Number(ctx.visiblePct) || 0) + countOf(ownItems, () => true) * 6;
    const enemyVision = countOf(enemyItems, () => true) * 8 + (ctx.enemyBaseVisible ? 12 : 0);
    const ownCover = locSet(ownItems).size * 12 + ctx.keyRegionOwn * 10;
    const enemyCover = locSet(enemyItems).size * 12 + ctx.keyRegionEnemy * 10;
    const ownDet = countOf(ownItems, (row) => observeSenseIsDetector(row.name));
    const enemyCloak = countOf(enemyItems, (row) => observeSenseIsCloakThreat(row.name)) + countOf(ctx.allEnemyItems || [], (row) => observeSenseIsCloakThreat(row.name));
    const ownStealth = ownDet * 16 + (ownDet > 0 ? 12 : 0);
    const enemyStealth = countOf(enemyItems, (row) => observeSenseIsDetector(row.name)) * 16;
    const freshnessOwn = Math.max(8, 90 - Math.min(80, ctx.staleSeconds || 0)) + (ctx.liveContacts || 0) * 4;
    const freshnessEnemy = ctx.enemyVisibleNow ? 70 : Math.max(10, 40 - Math.min(30, (ctx.staleSeconds || 0) / 4));
    return [
      pack("视野", ownVision, enemyVision,
        `可见地块约 ${ctx.visiblePct != null ? `${ctx.visiblePct}%` : "—"} · 探测单元 ${ctx.ownCount}`,
        `已见基地 ${ctx.enemyBaseVisible ? "是" : "否"} · 可见探测 ${ctx.enemyCount}`),
      pack("侦察覆盖", ownCover, enemyCover,
        `侦察点 ${[...locSet(ownItems)].slice(0, 3).join("、") || "无"} · 关键区域 ${ctx.keyRegionOwn}`,
        `可见侦察点 ${[...locSet(enemyItems)].slice(0, 3).join("、") || "无"}`),
      pack("隐身探测", ownStealth, Math.max(enemyStealth, enemyCloak * 8),
        ownDet > 0 ? `反隐单位 ${namePreview(ownItems, (row) => observeSenseIsDetector(row.name))}` : "尚无可靠反隐手段",
        enemyCloak > 0 ? `可见隐身威胁 ${enemyCloak}` : "暂未见隐身编制"),
      pack("信息时效", freshnessOwn, freshnessEnemy,
        `即时接触 ${ctx.liveContacts ?? "—"} · 已知接触 ${ctx.knownContacts ?? "—"}`,
        ctx.enemyVisibleNow ? "敌方单位仍在当前视野内" : `上次主力记录 ${ctx.lastSeenText || "尚无"}`),
    ];
  }

  if (key === "defense") {
    const ownStatic = countOf(ownItems, (row) => observeSenseIsStaticDefense(row.name, row.kind));
    const enemyStatic = countOf(enemyItems, (row) => observeSenseIsStaticDefense(row.name, row.kind));
    const ownMobile = ctx.ownCount - ownStatic;
    const enemyMobile = ctx.enemyCount - enemyStatic;
    const ownKey = ctx.keyDefenseOwn * 18 + ownStatic * 8 + ctx.homeDefenseOwn * 14;
    const enemyKey = ctx.keyDefenseEnemy * 18 + enemyStatic * 8 + ctx.homeDefenseEnemy * 14;
    return [
      pack("防守兵力", Math.max(ownMobile, 0) * 6 + ctx.ownCount * 2, Math.max(enemyMobile, 0) * 6 + ctx.enemyCount * 2,
        `机动驻守 ${Math.max(ownMobile, 0)} · 本维合计 ${ctx.ownCount}`,
        `可见机动 ${Math.max(enemyMobile, 0)} · 本维合计 ${ctx.enemyCount}`),
      pack("静态防御", ownStatic * 14 + (ownStatic > 0 ? 10 : 0), enemyStatic * 14,
        ownStatic > 0 ? namePreview(ownItems, (row) => observeSenseIsStaticDefense(row.name, row.kind)) : "尚未部署可见静态防御",
        enemyStatic > 0 ? namePreview(enemyItems, (row) => observeSenseIsStaticDefense(row.name, row.kind)) : "敌方静态防御尚未暴露"),
      pack("关键区域强度", ownKey, enemyKey,
        `主矿防守 ${ctx.homeDefenseOwn} · 关键区域 ${ctx.keyDefenseOwn}`,
        `可见敌方关键防守 ${ctx.keyDefenseEnemy}`),
    ];
  }

  const ownEco = (Number(ctx.minerals) || 0) / 40 + (Number(ctx.vespene) || 0) / 25 + (Number(ctx.workers) || 0) * 2 + (Number(ctx.bases) || 0) * 12;
  const enemyEco = (Number(ctx.enemyBasesSeen) || 0) * 14 + (Number(ctx.enemyVisibleSupply) || 0) * 1.2;
  const ownProd = countOf(ownItems, (row) => observeSenseIsProduction(row.name)) * 16 + (Number(ctx.bases) || 0) * 8;
  const enemyProd = countOf(enemyItems, (row) => observeSenseIsProduction(row.name)) * 16 + (Number(ctx.enemyBasesSeen) || 0) * 8;
  const ownSupplyLeft = Math.max(0, (Number(ctx.supplyCap) || 0) - (Number(ctx.supplyUsed) || 0));
  const ownReinforce = ownSupplyLeft * 3 + countOf(ownItems, (row) => /train|生产|训练|morph|注射/.test(String(row.action || ""))) * 10 + ctx.ownCount;
  const enemyReinforce = (Number(ctx.enemyVisibleSupply) || 0) + countOf(enemyItems, (row) => observeSenseIsProduction(row.name)) * 8;
  const ownLift = countOf(ownItems, (row) => observeSenseIsTransportOrHeal(row.name));
  const enemyLift = countOf(enemyItems, (row) => observeSenseIsTransportOrHeal(row.name));
  const ownSustain = ownEco + ownSupplyLeft * 2 + ownLift * 8 + (Number(ctx.workers) || 0);
  const enemySustain = enemyEco + enemyLift * 8 + (Number(ctx.enemyBasesSeen) || 0) * 10;
  return [
    pack("经济", ownEco, Math.max(enemyEco, 1),
      `晶矿 ${ctx.minerals ?? "—"} · 瓦斯 ${ctx.vespene ?? "—"} · 工人 ${ctx.workers ?? "—"} · 基地 ${ctx.bases ?? "—"}`,
      `已见基地 ${ctx.enemyBasesSeen ?? "—"} · 可见供给 ${ctx.enemyVisibleSupply ?? "—"}`),
    pack("产能", ownProd, enemyProd,
      namePreview(ownItems, (row) => observeSenseIsProduction(row.name)) || "生产建筑未单独列出",
      namePreview(enemyItems, (row) => observeSenseIsProduction(row.name)) || "敌方生产建筑仍在视野外"),
    pack("补员", ownReinforce, enemyReinforce,
      `补给余量 ${ownSupplyLeft} · 本维保障 ${ctx.ownCount}`,
      `可见供给 ${ctx.enemyVisibleSupply ?? "—"}`),
    pack("运输 / 恢复", ownLift * 18 + (ownLift > 0 ? 10 : 0), enemyLift * 18,
      ownLift > 0 ? namePreview(ownItems, (row) => observeSenseIsTransportOrHeal(row.name)) : "运输与恢复编制不足",
      enemyLift > 0 ? namePreview(enemyItems, (row) => observeSenseIsTransportOrHeal(row.name)) : "未见敌方投送力量"),
    pack("持续作战", ownSustain, enemySustain,
      `工人 ${ctx.workers ?? "—"} · 补给 ${ctx.supplyUsed ?? "—"}/${ctx.supplyCap ?? "—"}`,
      "依据可见经济建筑与供给估算，视野外误差较大"),
  ];
}

function classifyCapability(name, kind, action) {
  const n = sc2EnglishToken(name);
  // Broodlord 英文 token 含子串 "overlord"，不能与人口王虫 Overlord 共用同一子串规则。
  if (n === "broodlord" || n.endsWith("broodlord")) {
    return "strike";
  }
  const actionText = String(action || "").toLowerCase();
  const detectTokens = ["observer", "overseer", "raven", "oracle", "sensor", "scout", "changeling"];
  const defenseTokens = ["spine", "spore", "bunker", "turret", "cannon", "battery", "fortress"];
  const supportTokens = [
    "drone", "scv", "probe", "queen", "medivac", "overlord", "warpprism", "larva", "egg",
    "hatchery", "lair", "hive", "extractor", "refinery", "assimilator", "commandcenter", "nexus",
    "pylon", "supplydepot", "nydus", "barracks", "factory", "starport", "gateway", "warpgate",
    "spawningpool", "roachwarren", "hydraliskden", "banelingnest", "spire", "infestationpit",
    "robotics", "stargate", "engineeringbay", "armory", "fusioncore", "cybernetics",
    "twilight", "darkshrine", "fleetbeacon", "ghostacademy", "lurkerden", "evolutionchamber",
    "orbitalcommand", "creeptumor",
  ];
  if (detectTokens.some((token) => n.includes(token)) || /侦察|扫描|探测|scout/.test(actionText)) {
    return "detect";
  }
  if ((kind === "structure" && defenseTokens.some((token) => n.includes(token))) || actionText.includes("驻防")) {
    return "defense";
  }
  if (supportTokens.some((token) => n.includes(token)) || /采集|生产|补给|gather|supply/.test(actionText)) {
    return "support";
  }
  return "strike";
}

function collectEvidenceBits(parts, limit = 8, itemMax = 96) {
  const seen = new Set();
  const out = [];
  for (const part of parts || []) {
    const chunks = Array.isArray(part)
      ? part
      : String(part || "").split(/[；;|\n]+/);
    for (const chunk of chunks) {
      const s = String(chunk || "").replace(/\s+/g, " ").trim();
      if (!s || seen.has(s)) continue;
      seen.add(s);
      out.push(s.length > itemMax ? `${s.slice(0, itemMax)}…` : s);
      if (out.length >= limit) return out;
    }
  }
  return out;
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
      const rawName = String(row.code || row.name || "UNKNOWN");
      const kind = String(row.kind || "").toLowerCase() === "structure" ? "structure" : "unit";
      const location = String(row.location || "--").toUpperCase();
      const action = String(row.action || "待命/集结");
      const count = Math.max(1, Number(row.count) || 0);
      const capability = classifyCapability(rawName, kind, action);
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

/** 样例沙盘兵力：须在 normalizeForceRows / SC2 词典就绪后生成 */
function getSampleForceRows() {
  return normalizeForceRows(
    [
      { name: "Roach", kind: "unit", location: "B2", action: "高地占位", count: 16, xPercent: 58, yPercent: 42 },
      { name: "Roach", kind: "unit", location: "B2", action: "隘口侧翼", count: 8, xPercent: 55, yPercent: 48 },
      { name: "Roach", kind: "unit", location: "A7", action: "南口前压", count: 6, xPercent: 52, yPercent: 56 },
      { name: "Roach", kind: "unit", location: "B7", action: "东翼掩护", count: 4, xPercent: 68, yPercent: 42 },
      { name: "Hydralisk", kind: "unit", location: "B2", action: "侧翼待命", count: 12, xPercent: 62, yPercent: 46 },
      { name: "Hydralisk", kind: "unit", location: "A7", action: "南口支援", count: 6, xPercent: 50, yPercent: 58 },
      { name: "Hydralisk", kind: "unit", location: "B7", action: "东翼火力", count: 4, xPercent: 70, yPercent: 40 },
      { name: "Baneling", kind: "unit", location: "B2", action: "可开火待命", count: 10, xPercent: 60, yPercent: 44 },
      { name: "Baneling", kind: "unit", location: "A7", action: "南口伏击", count: 6, xPercent: 47, yPercent: 57 },
      { name: "Zergling", kind: "unit", location: "A7", action: "南口警戒", count: 20, xPercent: 46, yPercent: 60 },
      { name: "Zergling", kind: "unit", location: "B2", action: "高地游走", count: 12, xPercent: 56, yPercent: 45 },
      { name: "Zergling", kind: "unit", location: "B7", action: "东翼探路", count: 8, xPercent: 74, yPercent: 36 },
      { name: "Queen", kind: "unit", location: "A2", action: "注射与菌毯", count: 3, xPercent: 32, yPercent: 66 },
      { name: "Queen", kind: "unit", location: "A1", action: "主矿注射", count: 2, xPercent: 26, yPercent: 72 },
      { name: "Overlord", kind: "unit", location: "A7", action: "峡谷南口开雾", count: 2, xPercent: 48, yPercent: 62 },
      { name: "Overlord", kind: "unit", location: "B7", action: "东翼开雾", count: 2, xPercent: 72, yPercent: 38 },
      { name: "Overlord", kind: "unit", location: "B2", action: "高地开雾", count: 2, xPercent: 57, yPercent: 40 },
      { name: "Overlord", kind: "unit", location: "A2", action: "二矿开雾", count: 1, xPercent: 34, yPercent: 64 },
      { name: "Spine Crawler", kind: "structure", location: "A2", action: "家门口警戒", count: 4, xPercent: 28, yPercent: 68 },
      { name: "Spine Crawler", kind: "structure", location: "B2", action: "高地骨架", count: 4, xPercent: 54, yPercent: 50 },
      { name: "Spine Crawler", kind: "structure", location: "A7", action: "南口阻击", count: 2, xPercent: 44, yPercent: 64 },
      { name: "Spore Colony", kind: "structure", location: "A2", action: "防空警戒", count: 2, xPercent: 34, yPercent: 70 },
      { name: "Spore Colony", kind: "structure", location: "A1", action: "主矿防空", count: 2, xPercent: 24, yPercent: 76 },
      { name: "Hatchery", kind: "structure", location: "A1", action: "主矿运转", count: 1, xPercent: 22, yPercent: 74 },
      { name: "Hatchery", kind: "structure", location: "A2", action: "二矿运转", count: 1, xPercent: 30, yPercent: 70 },
      { name: "Extractor", kind: "structure", location: "A1", action: "采气", count: 2, xPercent: 20, yPercent: 72 },
      { name: "Extractor", kind: "structure", location: "A2", action: "二矿采气", count: 2, xPercent: 28, yPercent: 72 },
      { name: "Drone", kind: "unit", location: "A1", action: "采矿", count: 28, xPercent: 24, yPercent: 72 },
      { name: "Drone", kind: "unit", location: "A2", action: "采矿", count: 20, xPercent: 28, yPercent: 68 },
      { name: "Drone", kind: "unit", location: "A1", action: "采气", count: 6, xPercent: 21, yPercent: 70 },
    ],
    "own",
  ).concat(
    normalizeForceRows(
      [
        { name: "Roach", kind: "unit", location: "B1", action: "北口集结", count: 20, xPercent: 54, yPercent: 22 },
        { name: "Roach", kind: "unit", location: "B1", action: "北坡施压", count: 12, xPercent: 52, yPercent: 26 },
        { name: "Roach", kind: "unit", location: "B3", action: "西翼跟进", count: 8, xPercent: 42, yPercent: 26 },
        { name: "Zergling", kind: "unit", location: "B3", action: "西翼走廊", count: 28, xPercent: 38, yPercent: 28 },
        { name: "Zergling", kind: "unit", location: "B2", action: "侧翼渗透", count: 16, xPercent: 44, yPercent: 32 },
        { name: "Zergling", kind: "unit", location: "B1", action: "北口游走", count: 12, xPercent: 50, yPercent: 24 },
        { name: "Baneling", kind: "unit", location: "B1", action: "突击待命", count: 8, xPercent: 56, yPercent: 24 },
        { name: "Baneling", kind: "unit", location: "B3", action: "走廊伏击", count: 6, xPercent: 40, yPercent: 30 },
        { name: "Hydralisk", kind: "unit", location: "B1", action: "北口火力", count: 6, xPercent: 58, yPercent: 20 },
        { name: "Hydralisk", kind: "unit", location: "B2", action: "二矿掩护", count: 4, xPercent: 66, yPercent: 22 },
        { name: "Overlord", kind: "unit", location: "B1", action: "北口开雾", count: 2, xPercent: 50, yPercent: 18 },
        { name: "Overlord", kind: "unit", location: "B3", action: "西翼开雾", count: 2, xPercent: 36, yPercent: 24 },
        { name: "Overlord", kind: "unit", location: "B2", action: "中路开雾", count: 1, xPercent: 60, yPercent: 28 },
        { name: "Queen", kind: "unit", location: "B2", action: "注射", count: 2, xPercent: 62, yPercent: 18 },
        { name: "Queen", kind: "unit", location: "B1", action: "主矿注射", count: 1, xPercent: 57, yPercent: 15 },
        { name: "Spine Crawler", kind: "structure", location: "B1", action: "北口骨架", count: 4, xPercent: 55, yPercent: 16 },
        { name: "Spine Crawler", kind: "structure", location: "B2", action: "二矿警戒", count: 2, xPercent: 63, yPercent: 22 },
        { name: "Spore Colony", kind: "structure", location: "B1", action: "防空", count: 2, xPercent: 53, yPercent: 14 },
        { name: "Hatchery", kind: "structure", location: "B1", action: "主矿", count: 1, xPercent: 56, yPercent: 14 },
        { name: "Hatchery", kind: "structure", location: "B2", action: "二矿", count: 1, xPercent: 64, yPercent: 20 },
        { name: "Extractor", kind: "structure", location: "B1", action: "采气", count: 2, xPercent: 58, yPercent: 12 },
        { name: "Extractor", kind: "structure", location: "B2", action: "二矿采气", count: 2, xPercent: 66, yPercent: 18 },
        { name: "Drone", kind: "unit", location: "B1", action: "采矿", count: 24, xPercent: 60, yPercent: 14 },
        { name: "Drone", kind: "unit", location: "B2", action: "采矿", count: 16, xPercent: 65, yPercent: 19 },
        { name: "Drone", kind: "unit", location: "B1", action: "采气", count: 4, xPercent: 59, yPercent: 13 },
      ],
      "enemy",
    ),
  );
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
        title: "红方态势感知智能体",
        agentName: "红方态势感知智能体",
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

let fogCanvasCache = { key: "", url: null };

function applyDashboardAppearance() {
  document.documentElement.dataset.appearance = "light";
  document.documentElement.style.colorScheme = "light";
  if (document.body) {
    document.body.dataset.appearance = "light";
  }
  try {
    window.localStorage.setItem("mass.dashboard.appearance", "light");
  } catch (err) {}
}

function showDashboardBootError(message) {
  const root = document.getElementById("app");
  if (!root) {
    return;
  }
  root.removeAttribute("v-cloak");
  root.innerHTML =
    '<div class="page-shell"><section class="panel"><div class="empty-state"><h3>页面未能启动</h3><p>' +
    String(message || "请检查网络连接后刷新页面。") +
    "</p></div></section></div>";
}

if (!window.Vue) {
  showDashboardBootError("Vue 加载失败，请检查网络连接后刷新页面。");
} else {
  try {
  const { createApp } = window.Vue;

  createApp({
    data() {
      return {
        state: (() => {
          const boot = createEmptyState();
          boot.status = "running";
          boot.updatedAt = "2026-09-17T00:02:40";
          boot.commandSequence = SAMPLE_COMMAND_SEQUENCE.map((row) => ({ ...row }));
          boot.game = {
            ...(boot.game || {}),
            timeFormatted: "02:40",
            gameSeconds: 160,
            supplyUsed: 54,
            supplyCap: 66,
            minerals: 920,
            vespene: 240,
            workers: 24,
            bases: 2,
            gameStage: "mid",
            startLocation: "A1",
            armyCount: 38,
            workerCount: 24,
          };
          return boot;
        })(),
        duelStates: {
          red: createEmptyState(),
          blue: createEmptyState(),
        },
        activeDuelSide: "red",
        compareMode: false,
        agentViews: AGENT_VIEWS,
        activeAgentView: "planner",
        activeRightLayer: "overview",
        overviewDeskTab: "scenario",
        activeKnowledgeRoundId: "r1",
        knowledgeChainTab: "strike",
        knowledgeMemoryFilter: "all",
        selectedKnowledgeMemoryId: "",
        knowledgeChainDims: KNOWLEDGE_CHAIN_DIMS,
        knowledgeLiveVariantIndex: 0,
        knowledgeLiveTimer: null,
        knowledgeLiveCountdown: 10,
        knowledgeLiveFlash: false,
        knowledgeLiveFlashTimer: null,

        activePlannerTab: "bus",
        planPipelineTabs: PLAN_PIPELINE_TABS,
        planCotDimTab: "strike",
        planSelfplayLoop: PLAN_SELFPLAY_LOOP,
        planKillnet: PLAN_KILLNET,
        cotRounds: makeDemoCotHistory(),
        activeCotRoundId: "cot-demo-5",
        envCotRounds: makeDemoEnvCotHistory(),
        activeEnvCotRoundId: "env-cot-demo-5",
        envCotRoundSeq: 1,
        envCotSelfplayLoading: false,
        envCotSelfplayError: "",
        envCotAutoTimer: null,
        envCotAutoStarted: false,
        cotSelfplayLoading: false,
        cotSelfplayError: "",
        cotAutoTimer: null,
        lastCotRequestAtMs: 0,
        cotAutoStarted: false,
        cotSnapshotReason: "",
        askApiReachable: null,
        askApiProbeAtMs: 0,
        cotLiveUpdatedAt: "",
        cotLiveUpdatedAtMs: 0,
        cotRoundSeq: 1,
        cotKillnetLoading: false,
        cotKillnetError: "",
        selectedKillnetDim: "",
        evalInnerTab: "tactics",
        evalDrafts: {},
        evalAmendDialog: { open: false, scope: "", id: "", title: "", note: "" },
        observeSide: "red",
        knowledgeRevisionItems: KNOWLEDGE_REVISION_ITEMS,
        oodaRings: OODA_RINGS,
        oodaArchCaps: OODA_ARCH_CAPS,
        oodaArchMemory: OODA_ARCH_MEMORY,
        oodaArchColumns: OODA_ARCH_COLUMNS,
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
          observe:    { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          obsStrike:  { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          obsDetect:  { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          obsDefense: { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          obsSupport: { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          obsEnvironment: { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          orient:     { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          oriStrike:  { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          oriDetect:  { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          oriDefense: { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          oriSupport: { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          oriEnvironment: { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          awareness:  { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          force:      { showFog: true, showResources: true, showRangeCircles: true, showUnits: true },
          intent:     { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          risk:       { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          killchain:  { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          critical:   { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          plan:       { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          cotStrike:  { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          cotDetect:  { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          cotDefense: { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          cotSupport: { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          cotEnvironment: { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          killnet:    { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
          eval:       { showFog: false, showResources: false, showRangeCircles: false, showUnits: false },
        },
        // 主页（home-clone / overview）沙盘独立配置
        homeMinimapConfig:    { showFog: true, showResources: true, showRangeCircles: false, showUnits: true },
        featureAutoPlay: true,
        featureAutoPlayTimer: null,
        plannerLiveTurn: null,
        plannerAutoFollow: true,
        plannerSessionStartedAt: Date.now(),
        plannerSeenTurnKeys: Object.create(null),
        plannerTypingTimer: null,
        plannerTypingKey: "",
        plannerTypingIndex: 0,
        plannerExpandedReplies: Object.create(null),
        plannerPinnedKey: PLANNER_DEMO_CASE.key,
        plannerHistory: [{
          ...PLANNER_DEMO_CASE,
          key: PLANNER_DEMO_CASE.key,
          displayReply: PLANNER_DEMO_CASE.reply,
        }],
        plannerHistoryReady: true,
        executedCommands: SAMPLE_EXECUTED_COMMANDS.map((row) => ({ ...row })),
        commandTrackingReady: true,
        isLoading: true,
        fetchError: "",
        fetchFailureCount: 0,
        isFetchingState: false,
        refreshMs: 1200,
        llmAdviceRefreshMs: LLM_ADVICE_REFRESH_MS,
        llmAdviceTimer: null,
        llmAdviceLastAt: "",
        llmAdviceTick: 0,
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
        orientQaTimer: null,
        orientQaReqSeq: 0,
        orientQaByDim: hydrateSampleOrientMessages(SAMPLE_ORIENT_QA_BY_DIM),
        orientQa: (() => {
          const pack = SAMPLE_ORIENT_QA_BY_DIM.strike;
          const hydrated = hydrateSampleOrientMessages({ strike: pack }).strike;
          return {
            question: "",
            loading: false,
            errorText: "",
            loadedAt: hydrated.loadedAt || "08:41:10",
            source: hydrated.source || "sample-showcase",
            dimension: "strike",
            messages: Array.isArray(hydrated.messages) ? hydrated.messages.map((m) => ({ ...m })) : [],
            steps: Array.isArray(hydrated.steps) ? hydrated.steps.map((s) => ({ ...s })) : [],
            conclusion: hydrated.conclusion || "",
          };
        })(),
        observeSenseDraft: {
          strike: "",
          detect: "",
          defense: "",
          support: "",
          environment: "",
          envOrient: "",
        },
        observeSenseNotes: {
          strike: [],
          detect: [],
          defense: [],
          support: [],
          environment: [],
          envOrient: [],
        },
        amendToastText: "",
        amendToastTimer: null,
        orientHubFold: {
          intent: false,
          vuln: false,
          chain: false,
          critical: false,
        },
        /** 为 true 后才信任 dashboard_state.json 中的对局数据（避免仅重启静态服务后仍显示旧内容） */
        liveUiArmed: false,
        /** sample=固化演示内容；live=跟后端对局（无后端时为空页） */
        uiMode: "sample",
        hideSampleArtifacts: false,
        /** 首次成功拉取后的指纹；后续任一轮指纹变化则视为对局在写盘，解除冷启动 */
        stateBootstrapFingerprint: null,
        /** 兵力布势：智能体协同建议（ask_api_server /api/force/llm-advice） */
        forceLlmAdviceLoading: false,
        forceLlmAdvice: {
          ok: true,
          sections: SAMPLE_FORCE_LLM_SECTIONS.map((row) => ({ ...row })),
          errorText: "",
          answerRaw: "",
          traces: [],
          loadedAt: "08:41:05",
          source: "样例固化包",
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
        /** 对齐「兵力部署统计」卡片外框高度与「双方兵力数量对比」（ResizeObserver） */
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
      activeDuelLabel() {
        return "红方";
      },
      /** 观察环切到蓝方视角时改用蓝色主题，其余保持红方 */
      uiThemeMode() {
        if (
          this.currentOodaRing &&
          this.currentOodaRing.key === "observe" &&
          this.observeSide === "blue"
        ) {
          return "blue";
        }
        return "red";
      },
      duelCompareBoard() {
        knowledgeLiveVariantIndex = this.knowledgeLiveVariantIndex || 0;
        const key = this.activeRightLayer;
        return buildLiveDuelCompareBoard(
          key,
          this.duelStates?.red || createEmptyState(),
          this.duelStates?.blue || createEmptyState(),
        );
      },
      tacticsEvaluationBoard() {
        return buildTacticsEvaluationBoard(this.activeDuelSide);
      },

      knowledgeExperienceBoard() {
        knowledgeLiveVariantIndex = this.knowledgeLiveVariantIndex || 0;
        return buildKnowledgeExperienceBoard(this.activeDuelSide);
      },
      activeKnowledgeRound() {
        const board = this.knowledgeExperienceBoard;
        const rounds = board.rounds || [];
        const hit = rounds.find((r) => r.id === this.activeKnowledgeRoundId);
        return hit || rounds[0] || null;
      },
      activeKnowledgeOurChain() {
        return getKnowledgeSideChain(this.activeKnowledgeRound?.ourAction, this.knowledgeChainTab);
      },
      activeKnowledgeEnemyChain() {
        return getKnowledgeSideChain(
          this.activeKnowledgeRound?.opponentCounter,
          this.knowledgeChainTab,
        );
      },
      knowledgeRevisionView() {
        return (this.knowledgeRevisionItems || []).map((item) => this.withEvalDraft("revision", item.key, item));
      },
      filteredKnowledgeMemories() {
        const memories = this.knowledgeExperienceBoard.memories || [];
        const filter = this.knowledgeMemoryFilter;
        const rows = memories.map((mem) => this.withEvalDraft("memory", mem.id, mem));
        if (filter === "success" || filter === "failure") {
          return rows.filter((m) => m.outcome === filter);
        }
        return rows;
      },
      knowledgeChainStats() {
        const board = this.knowledgeExperienceBoard;
        const round = this.activeKnowledgeRound;
        return {
          rounds: board.roundCount,
          memories: board.memoryCount,
          success: board.successMemoryCount,
          failure: board.failureMemoryCount,
          constraint: round?.constraint || "",
          ourDim: this.knowledgeChainTab,
          enemyDim: this.knowledgeChainTab,
          libraryUpdate: round?.ourResponse?.libraryUpdate || "",
          liveLabel: board.liveVariantLabel || "",
          liveStamp: board.liveVariantStamp || "",
          liveIndex: (board.liveVariantIndex || 0) + 1,
          liveTotal: KNOWLEDGE_LIVE_VARIANT_COUNT,
          liveCountdown: this.knowledgeLiveCountdown,
        };
      },

      tacticsEvalSections() {
        const board = this.tacticsEvaluationBoard;
        const wrap = (section, group, items) => (items || []).map((item, idx) => {
          const uid = `${section}-${group}-${idx}`;
          return this.withEvalDraft("tactics", uid, { ...item, uid });
        });
        return [
          {
            key: "success",
            label: "成功评估",
            tone: "success",
            total: board.successCount,
            groups: [
              {
                key: "tactical",
                label: "成功战术决策",
                hint: "探测对象、进攻地点等局部行动",
                items: wrap("success", "tactical", board.success.tactical),
              },
              {
                key: "campaign",
                label: "成功战役决策",
                hint: "科技节奏、兵力扩充等阶段安排",
                items: wrap("success", "campaign", board.success.campaign),
              },
            ],
          },
          {
            key: "failure",
            label: "失败评估",
            tone: "failure",
            total: board.failureCount,
            groups: [
              {
                key: "tactical",
                label: "失败战术决策",
                hint: "侦察遗漏、进攻时机/地点偏差",
                items: wrap("failure", "tactical", board.failure.tactical),
              },
              {
                key: "campaign",
                label: "失败战役决策",
                hint: "科技切换、扩编节奏等阶段偏差",
                items: wrap("failure", "campaign", board.failure.campaign),
              },
            ],
          },
        ];
      },
      activeDuelAgentName() {
        const game = this.duelStates[this.activeDuelSide]?.game || this.game || {};
        return game.agentName || (this.activeDuelSide === "blue" ? "蓝方 SwarmBrain" : "红方 SwarmBrain");
      },
      refreshSeconds() {
        return this.refreshMs / 1000;
      },
      observePerspectiveOptions() {
        return [
          { key: "red", title: "红方", subtitle: "红方智能体" },
        ];
      },
      observeState() {
        return this.duelStates?.red || this.state || createEmptyState();
      },
      observeGame() {
        return this.observeState?.game || {};
      },
      observeOwnLabel() {
        return "红方";
      },
      observeEnemyLabel() {
        return "蓝方";
      },
      observeFogNote() {
        return "红方智能体 · 人机对战";
      },
      game() {
        return this.state?.game ?? createEmptyState().game;
      },
      /**
       * 沙盘锚定侧：优先红方态势（双方都在跑时保持固定），不随页眉红/蓝切换翻转。
       */
      sandboxMapSide() {
        const red = this.duelStates?.red;
        const blue = this.duelStates?.blue;
        const redReady =
          Boolean(red?.latestFeatureFrame?.minimap?.imagePath) ||
          red?.status === "running" ||
          red?.game?.armyCount != null;
        const blueReady =
          Boolean(blue?.latestFeatureFrame?.minimap?.imagePath) ||
          blue?.status === "running" ||
          blue?.game?.armyCount != null;
        if (redReady) return "red";
        if (blueReady) return "blue";
        return "red";
      },
      sandboxState() {
        return this.duelStates?.[this.sandboxMapSide] || this.duelStates?.red || createEmptyState();
      },
      /** 沙盘兵力：双方各自「己方」单位，绝对红/蓝，不随观察阵营翻转 */
      sandboxForceDeploymentItems() {
        if (this.isSampleShowcase) {
          return getSampleForceRows().map((row) => ({
            ...row,
            side: row.side === "enemy" ? "blue" : "red",
          }));
        }
        if (!this.liveUiArmed) {
          return [];
        }
        const collectOwn = (side) => {
          const game = this.duelStates?.[side]?.game || {};
          const fromDetails = normalizeForceRows(game.ownForceDetails || [], side);
          if (fromDetails.length) {
            return fromDetails;
          }
          return normalizeForceRows(compositionToForceRows(game.ownUnitComposition, "own"), side);
        };
        return [...collectOwn("red"), ...collectOwn("blue")];
      },
      stageLabel() {
        return STAGE_LABELS[this.game.gameStage] || STAGE_LABELS.early;
      },
      minimapFrame() {
        const sandbox = this.sandboxState;
        const mini = sandbox?.latestFeatureFrame?.minimap ?? null;
        if (mini && (mini.imagePath || mini.visibilityGrid || mini.visibilityShape)) {
          return mini;
        }
        // 样例模式：始终给沙盘壳，方便叠固化兵力
        if (this.isSampleShowcase) {
          return {
            imagePath: mini?.imagePath || "",
            visibilityGrid: null,
            visibilityShape: null,
            shellOnly: true,
          };
        }
        // 沙盘固定锚定红/蓝态势源；无特征帧时仍展示底图壳
        const game = sandbox?.game || {};
        if (this.liveUiArmed && (sandbox?.status === "running" || game?.armyCount != null)) {
          return {
            imagePath: "",
            visibilityGrid: null,
            visibilityShape: null,
            shellOnly: true,
          };
        }
        return mini;
      },
      minimapMetaLine() {
        if (!this.minimapFrame) {
          return "等待小地图同步";
        }
        if (this.isSampleShowcase) {
          return "样例场景 · 峡谷中盘 08:42 · 沙盘兵力固化展示";
        }
        if (!this.liveUiArmed) {
          return "底图预览 · 等待对局写盘同步";
        }
        if (this.minimapFrame.shellOnly || !this.minimapFrame.imagePath) {
          return "红蓝固定沙盘 · 底图+双方兵力（等待特征图导出）";
        }
        const lf = this.sandboxState?.latestFeatureFrame;
        if (!lf) {
          return "红蓝固定沙盘 · 等待小地图同步";
        }
        return `红蓝固定沙盘 · ${lf.time_formatted || "--"} / 回合 ${lf.game_loop ?? "--"}`;
      },
      minimapLegend() {
        return [
          { label: "蓝方单位", note: "蓝色：蓝方单位与建筑体力标识", className: "blue" },
          { label: "红方单位", note: "红色：红方单位与建筑体力标识", className: "red" },
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
        const lf = this.sandboxState?.latestFeatureFrame || null;
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
        const sandboxGame = this.sandboxState?.game || {};
        const liveResources = Array.isArray(sandboxGame?.resourceMarkers)
          ? sandboxGame.resourceMarkers
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
        const layout = this.sandboxState?.mapLayout && typeof this.sandboxState.mapLayout === "object"
          ? this.sandboxState.mapLayout
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
        if (!this.liveUiArmed && !this.isSampleShowcase) return [];
        const pointByLabel = this.minimapResourceMarkers.reduce((acc, point) => {
          acc[point.label] = point;
          return acc;
        }, Object.create(null));
        const stackCounts = Object.create(null);
        const activeSide = this.activeDuelSide === "blue" ? "blue" : "red";
        const rows = this.sandboxForceDeploymentItems
          .filter((row) => row && (this.hasExactMapPoint(row) || (row.location && pointByLabel[row.location])))
          .filter((row) => Number(row.count || 0) > 0);

        return rows.map((row, index) => {
          const absoluteSide = row.side === "blue" ? "blue" : "red";
          const relativeSide = absoluteSide === activeSide ? "own" : "enemy";
          const point = this.hasExactMapPoint(row)
            ? { xPercent: row.xPercent, yPercent: row.yPercent }
            : pointByLabel[row.location];
          const role = this.unitMarkerRole({ ...row, side: absoluteSide });
          const stackKey = `${Math.round(Number(point.xPercent) * 10) / 10}:${Math.round(Number(point.yPercent) * 10) / 10}:${absoluteSide}:${role}`;
          const stackIndex = stackCounts[stackKey] || 0;
          stackCounts[stackKey] = stackIndex + 1;
          return {
            key: `${absoluteSide}-${role}-${row.location}-${row.name}-${index}`,
            side: absoluteSide,
            relativeSide,
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
        if (!this.liveUiArmed && !this.isSampleShowcase) return [];
        const roleFilter = this.observeSenseRole || this.orientSenseRole || this.minimapHighlightRole;
        return this.minimapUnitMarkers
          .filter((m) => !roleFilter || this.markerMatchesObserveRole(m, roleFilter))
          .filter((m) => m.role === "scout" || m.role === "strike" || m.role === "defend")
          .map((m) => ({ ...m, rangePct: getUnitRangePct(m.name, m.role) }))
          .filter((m) => m.rangePct > 0);
      },
      // 按能力类型高亮或进入四维观察页时，只显示该角色单元
      minimapUnitMarkersFiltered() {
        const roleFilter = this.observeSenseRole || this.orientSenseRole || this.minimapHighlightRole;
        if (!roleFilter) return this.minimapUnitMarkers;
        return this.minimapUnitMarkers.filter((m) => this.markerMatchesObserveRole(m, roleFilter));
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
        // 无精准匹配时：按角色/阵营回退（相对当前观察阵营）
        if (!matched.length) {
          const roleMap = { attack: "strike", defense: "defend", detect: "scout", support: "support" };
          const sideMap = { attack: "enemy", defense: "own", detect: "enemy", support: "enemy" };
          const role = roleMap[key];
          const side = sideMap[key];
          matched = markers.filter((m) => m.relativeSide === side && m.role === role);
          if (!matched.length && key === "attack") {
            matched = markers.filter((m) => m.relativeSide === "enemy");
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
        let fingerprint = 0;
        const step = Math.max(1, Math.floor(grid.length / 64));
        for (let i = 0; i < grid.length; i += step) {
          fingerprint = (fingerprint * 33 + (grid[i] | 0)) | 0;
        }
        const cacheKey = `${H}x${W}:${grid.length}:${fingerprint}`;
        if (fogCanvasCache.key === cacheKey) {
          return fogCanvasCache.url;
        }
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
          const url = canvas.toDataURL("image/png");
          fogCanvasCache = { key: cacheKey, url };
          return url;
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
        if (this.isSampleShowcase) {
          return getSampleForceRows();
        }
        if (!this.liveUiArmed) {
          return [];
        }
        const ownFromDetails = normalizeForceRows(this.observeGame?.ownForceDetails || [], "own");
        const ownRows = ownFromDetails.length
          ? ownFromDetails
          : normalizeForceRows(compositionToForceRows(this.observeGame?.ownUnitComposition, "own"), "own");
        const enemyFromDetails = normalizeForceRows(this.observeGame?.enemyVisibleDetails || [], "enemy");
        const enemyRows = enemyFromDetails.length
          ? enemyFromDetails
          : normalizeForceRows(compositionToForceRows(this.observeGame?.enemyVisibleComposition, "enemy"), "enemy");
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
          return "尚未发现蓝方关键目标";
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
          if (diff > 0) diffLabel = `红方 +${diff}`;
          if (diff < 0) diffLabel = `蓝方 +${Math.abs(diff)}`;
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
        return `红方 ${this.forceSideSummary.ownTotal} · 蓝方 ${this.forceSideSummary.enemyTotal} · 能力域 ${CAPABILITY_ORDER.length}`;
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
          {
            key: "environment",
            title: "作战环境建议",
            summary: hotspot
              ? `围绕 ${hotspot.location} 一带的地形控点与走廊再确认，未见证据不编造。`
              : "先补齐隘口/高地/矿区可见证据，再谈抢点与开岩。",
            detail: "作战环境建议应扣住控点、走廊与视野制高，避免与打击编制建议重复。",
          },
        ];
      },
      /** 仅展示大模型 API 返回；不再回退本地启发式 */
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
        return [];
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
        if (!this.liveUiArmed && !this.isSampleShowcase) {
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
              title: `蓝方${meta.label}链路-${targetHint}`,
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
        if (this.killChainLlmPlansLoading) {
          return `敌链 ${chainTotal} 条 · 大模型方案生成中`;
        }
        if (this.killChainLlmPlans?.ok) {
          return `敌链 ${chainTotal} 条 · 大模型方案 ${planTotal} 个 · ${this.killChainLlmPlans.loadedAt || ""}`;
        }
        return `敌链 ${chainTotal} 条 · 等待大模型方案`;
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
        // 不再使用 workbench / 本地启发式兜底，只展示大模型实时方案
        return [];
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
        if (this.activeRightLayer !== "killchain" && !this.isOrientHubLayer) return [];
        const dimFilter = this.orientSenseCapabilityKey;
        const steps = (this.killChainEnemyStrikeFlow?.steps || []).filter((step) => !dimFilter || step.dim === dimFilter);
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
      plannerFocusItem() {
        const pin = String(this.plannerPinnedKey || "");
        if (pin) {
          if (pin === PLANNER_DEMO_CASE.key) {
            return this.hideSampleArtifacts ? null : PLANNER_DEMO_CASE;
          }
          const hist = this.plannerHistoryItems.find((item) => item.key === pin);
          if (hist) return hist;
          if (this.plannerLiveItem && this.plannerLiveItem.key === pin) return this.plannerLiveItem;
        }
        const liveOrHist = this.plannerLiveItem || this.plannerHistoryItems[this.plannerHistoryItems.length - 1] || null;
        if (liveOrHist) return liveOrHist;
        return this.hideSampleArtifacts ? null : PLANNER_DEMO_CASE;
      },
      plannerArchiveItems() {
        if (this.hideSampleArtifacts) {
          const focusKey = this.plannerFocusItem?.key;
          return this.plannerHistoryItems.filter((item) => item.key !== focusKey).slice(-6);
        }
        const focusKey = this.plannerFocusItem?.key;
        const hist = this.plannerHistoryItems.filter((item) => item.key !== focusKey).slice(-6);
        // 永久样例始终可回看；有实时/历史方案时放在归档首位。
        if (focusKey !== PLANNER_DEMO_CASE.key) {
          return [PLANNER_DEMO_CASE, ...hist].slice(0, 7);
        }
        return hist;
      },
      plannerFocusDossier() {
        return this.plannerDossierOf(this.plannerFocusItem);
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
        const seen = new Set();
        const pushItem = (item, isLive = false) => {
          if (!item) return;
          const key = String(item.key || "");
          if (!key || seen.has(key)) return;
          const tasks = extractPlannerTasksFromReply(this.replyTextForItem(item));
          if (!tasks.length) return;
          seen.add(key);
          turns.push({
            key,
            title: item.title || (isLive ? "当前规划" : "历史规划"),
            time: item.promptTime || "--:--",
            isLive: Boolean(isLive),
            tasks,
          });
        };
        // 样例：确保方案卷宗进入任务生成页
        if (this.isSampleShowcase) {
          pushItem(this.plannerFocusItem || PLANNER_DEMO_CASE, false);
        }
        for (const item of this.plannerHistoryItems) {
          pushItem(item, false);
        }
        if (this.plannerLiveItem) {
          pushItem(this.plannerLiveItem, true);
        }
        return turns;
      },
      plannerTaskTurnsFiltered() {
        const filter = this.activePlannerTaskFilter;
        return this.plannerTaskTurns
          .map((turn) => ({
            ...turn,
            tasks: filter === "all"
              ? turn.tasks
              : filter === "environment"
                ? turn.tasks.filter((task) => isEnvironmentRelatedTask(`${task.summaryCn || ""} ${task.detailCn || ""}`))
                : turn.tasks.filter((task) => task.type === filter),
          }))
          .filter((turn) => turn.tasks.length > 0);
      },
      plannerTaskFilterCounts() {
        const counts = { all: 0, strike: 0, detect: 0, defense: 0, support: 0, environment: 0 };
        for (const turn of this.plannerTaskTurns) {
          for (const task of turn.tasks) {
            counts.all += 1;
            if (counts[task.type] !== undefined) {
              counts[task.type] += 1;
            }
            if (isEnvironmentRelatedTask(`${task.summaryCn || ""} ${task.detailCn || ""}`)) {
              counts.environment += 1;
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
      plannerTaskLaneTasks() {
        const turns = this.plannerTaskShowAllHistory
          ? this.plannerTaskTurnsFiltered
          : this.plannerTaskVisibleTurns;
        const rows = [];
        for (const turn of turns) {
          for (const task of turn.tasks || []) {
            rows.push({
              ...task,
              turnTitle: turn.title,
              turnTime: turn.time,
              isLive: turn.isLive,
            });
          }
        }
        return rows;
      },
      plannerTaskLanes() {
        const filter = this.activePlannerTaskFilter;
        const keys = filter === "all" ? TASK_LANE_KEYS : [filter];
        const tasks = this.plannerTaskLaneTasks;
        return keys.map((key) => ({
          key,
          label: TASK_PARTITION_LABELS[key] || key,
          // 作战环境为单列横栏：条目已按环境关联筛过，不再按打探防保拆开
          tasks: key === "environment" ? tasks : tasks.filter((task) => task.type === key),
        }));
      },
      plannerUnitLanes() {
        const filter = this.activePlannerUnitFilter;
        const keys = filter === "all" ? TASK_LANE_KEYS : [filter];
        const rows = this.plannerUnitAssignRowsFiltered;
        return keys.map((key) => ({
          key,
          label: TASK_PARTITION_LABELS[key] || key,
          rows: key === "environment" ? rows : rows.filter((row) => row.type === key),
        }));
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
          const type = TASK_LANE_KEYS.includes(row.capability) ? row.capability : "strike";
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
        const counts = { all: 0, strike: 0, detect: 0, defense: 0, support: 0, environment: 0 };
        for (const row of this.plannerUnitAssignRows) {
          counts.all += 1;
          if (counts[row.type] !== undefined) {
            counts[row.type] += 1;
          }
          if (isEnvironmentRelatedTask(row.taskSummary || "")) {
            counts.environment += 1;
          }
        }
        return counts;
      },
      plannerUnitAssignRowsFiltered() {
        const filter = this.activePlannerUnitFilter;
        if (filter === "all") {
          return this.plannerUnitAssignRows;
        }
        if (filter === "environment") {
          return this.plannerUnitAssignRows.filter((row) => isEnvironmentRelatedTask(row.taskSummary || ""));
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
        if (this.isSampleShowcase) {
          return this.activeCotRound?.gameTime
            || this.activeEnvCotRound?.gameTime
            || "02:40";
        }
        return this.game.timeFormatted || "--:--";
      },
      formattedUpdatedAt() {
        return formatClock(this.state?.updatedAt);
      },
      statusLabel() {
        if (this.uiMode === "sample") {
          return "样例展示";
        }
        if (this.fetchError) {
          return "连接中断";
        }
        const redRunning = this.duelStates.red?.status === "running";
        const blueRunning = this.duelStates.blue?.status === "running";
        if (this.liveUiArmed && (redRunning || blueRunning || this.state?.status === "running")) {
          return "对局进行中";
        }
        if (this.liveUiArmed) {
          return "实时待命";
        }
        return "等待后端";
      },
      statusClass() {
        if (this.uiMode === "sample") {
          return "idle";
        }
        if (this.fetchError) {
          return "danger";
        }
        const redRunning = this.duelStates.red?.status === "running";
        const blueRunning = this.duelStates.blue?.status === "running";
        if (this.liveUiArmed && (redRunning || blueRunning || this.state?.status === "running")) {
          return "live";
        }
        return this.liveUiArmed ? "idle" : "danger";
      },
      isSampleUiMode() {
        return this.uiMode === "sample";
      },
      /** 样例固化展示：保留时钟/沙盘/多轮思维链/LLM 文案，不走空页门控 */
      isSampleShowcase() {
        return this.uiMode === "sample" && !this.hideSampleArtifacts;
      },
      isLiveUiMode() {
        return this.uiMode === "live";
      },
      rightPanelMeta() {
        if (!this.plannerTailItem) {
          return MODULE_AGENTS.plan.name;
        }
        return `${this.activeModuleAgent.name} / ${this.plannerTailItem.title}`;
      },
      rightLayerOptions() {
        return [
          { key: "overview", label: MODULE_AGENTS.overview.name },
          { key: "observe", label: MODULE_AGENTS.observe.name },
          { key: "obsStrike", label: MODULE_AGENTS.obsStrike.name },
          { key: "obsDetect", label: MODULE_AGENTS.obsDetect.name },
          { key: "obsDefense", label: MODULE_AGENTS.obsDefense.name },
          { key: "obsSupport", label: MODULE_AGENTS.obsSupport.name },
          { key: "obsEnvironment", label: MODULE_AGENTS.obsEnvironment.name },
          { key: "orient", label: MODULE_AGENTS.orient.name },
          { key: "oriStrike", label: MODULE_AGENTS.oriStrike.name },
          { key: "oriDetect", label: MODULE_AGENTS.oriDetect.name },
          { key: "oriDefense", label: MODULE_AGENTS.oriDefense.name },
          { key: "oriSupport", label: MODULE_AGENTS.oriSupport.name },
          { key: "oriEnvironment", label: MODULE_AGENTS.oriEnvironment.name },
          { key: "intent", label: MODULE_AGENTS.intent.name },
          { key: "risk", label: MODULE_AGENTS.risk.name },
          { key: "killchain", label: MODULE_AGENTS.killchain.name },
          { key: "critical", label: MODULE_AGENTS.critical.name },
          { key: "cotStrike", label: MODULE_AGENTS.cotStrike.name },
          { key: "cotDetect", label: MODULE_AGENTS.cotDetect.name },
          { key: "cotDefense", label: MODULE_AGENTS.cotDefense.name },
          { key: "cotSupport", label: MODULE_AGENTS.cotSupport.name },
          { key: "cotEnvironment", label: MODULE_AGENTS.cotEnvironment.name },
          { key: "killnet", label: MODULE_AGENTS.killnet.name },
          { key: "plan", label: MODULE_AGENTS.plan.name },
          { key: "eval", label: MODULE_AGENTS.eval.name },
        ];
      },
      /** 顶栏单独展示「系统总览」，此处仅渲染其余页签 */
      rightLayerTabOptions() {
        return this.currentOodaRing?.tabs || [];
      },
      currentOodaRing() {
        const layer = this.activeRightLayer === "cot" ? "cotStrike" : this.activeRightLayer;
        return (
          OODA_RINGS.find(
            (ring) =>
              ring.key === layer ||
              ring.defaultLayer === layer ||
              (ring.tabs || []).some((tab) => tab.key === layer),
          ) || null
        );
      },
      isObserveSenseLayer() {
        return Boolean(OBSERVE_SENSE_LAYER_CAP[this.activeRightLayer]);
      },
      observeSenseCapabilityKey() {
        return OBSERVE_SENSE_LAYER_CAP[this.activeRightLayer] || "";
      },
      observeSenseRole() {
        const key = this.observeSenseCapabilityKey;
        // 作战环境感知不按打探防保过滤兵力，避免沙盘被清空
        if (!key || key === "environment") return "";
        return this.capabilityToRole(key);
      },
      observeSenseView() {
        const key = this.observeSenseCapabilityKey;
        if (!key) return null;
        const meta = CAPABILITY_META[key] || { key, label: "该维", icon: "◎" };
        const allItems = this.forceDeploymentItems || [];
        const items = allItems.filter((row) => (CAPABILITY_META[row.capability] ? row.capability : "strike") === key);
        const ownItems = items.filter((row) => row.side !== "enemy");
        const enemyItems = items.filter((row) => row.side === "enemy");
        const emptySide = () => ({ total: 0, counter: Object.create(null), actions: new Set(), structures: 0 });
        const grouped = new Map();
        const sideCounter = { own: Object.create(null), enemy: Object.create(null) };
        let ownCount = 0;
        let enemyCount = 0;
        let ownStructures = 0;
        let enemyStructures = 0;
        for (const row of items) {
          const side = row.side === "enemy" ? "enemy" : "own";
          const count = Number(row.count) || 0;
          if (side === "enemy") enemyCount += count;
          else ownCount += count;
          if (row.kind === "structure") {
            if (side === "enemy") enemyStructures += count;
            else ownStructures += count;
          }
          sideCounter[side][row.name] = (sideCounter[side][row.name] || 0) + count;
          const location = row.location && row.location !== "--" ? row.location : "未知区域";
          if (!grouped.has(location)) {
            grouped.set(location, { location, own: emptySide(), enemy: emptySide() });
          }
          const bucket = grouped.get(location)[side];
          bucket.total += count;
          bucket.counter[row.name] = (bucket.counter[row.name] || 0) + count;
          if (row.kind === "structure") bucket.structures += count;
          if (row.action) bucket.actions.add(row.action);
        }
        const toText = (counter) =>
          Object.entries(counter)
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .slice(0, 10)
            .map(([name, count]) => `${name}×${count}`);
        const toActionText = (bucket) => {
          const actions = Array.from(bucket.actions).filter(Boolean).slice(0, 3);
          return actions.length ? actions.join(" / ") : "待命/集结";
        };
        const locations = Array.from(grouped.values())
          .map((row) => ({
            location: row.location,
            ownTotal: row.own.total,
            enemyTotal: row.enemy.total,
            ownUnits: toText(row.own.counter).join("、") || "暂无",
            enemyUnits: toText(row.enemy.counter).join("、") || "暂无",
            ownActions: toActionText(row.own),
            enemyActions: toActionText(row.enemy),
          }))
          .sort((a, b) => (b.ownTotal + b.enemyTotal) - (a.ownTotal + a.enemyTotal) || a.location.localeCompare(b.location))
          .slice(0, 12);
        const ownUnits = toText(sideCounter.own);
        const enemyUnits = toText(sideCounter.enemy);
        const ownHot = locations.filter((row) => row.ownTotal > 0).slice(0, 4).map((row) => row.location);
        const enemyHot = locations.filter((row) => row.enemyTotal > 0).slice(0, 4).map((row) => row.location);
        const contested = locations.filter((row) => row.ownTotal > 0 && row.enemyTotal > 0);
        const diff = ownCount - enemyCount;
        const ownLabel = this.observeOwnLabel;
        const enemyLabel = this.observeEnemyLabel;
        const diffLabel = diff > 0 ? `${ownLabel} +${diff}` : diff < 0 ? `${enemyLabel} +${Math.abs(diff)}` : "势均力敌";
        const sensing = this.deerflowAwarenessSensing || {};
        const own = sensing.own || {};
        const en = sensing.enemy || {};
        const vision = sensing.vision || {};
        const start = String(this.observeGame?.startLocation || "A1");
        const ownPrefix = start.startsWith("B") ? "B" : "A";
        const enemyPrefix = ownPrefix === "A" ? "B" : "A";
        const keyLabels = [`${ownPrefix}1`, `${ownPrefix}2`, `${ownPrefix}7`, `${enemyPrefix}1`, `${enemyPrefix}2`, `${enemyPrefix}7`];
        const hasSideAt = (side, labels) =>
          labels.filter((label) => locations.some((row) => row.location === label && (side === "own" ? row.ownTotal : row.enemyTotal) > 0)).length;
        const tracker = this.enemyMainTracker || {};
        const staleSeconds = tracker.lastSeenAtMs ? Math.max(0, Math.round((Date.now() - tracker.lastSeenAtMs) / 1000)) : 0;
        const questions = {
          strike: "有效杀伤能力对比",
          detect: "侦察覆盖、时效与精度对比",
          defense: "抗击与阻滞能力对比",
          support: "持续作战能力对比",
        };
        const deepRows = buildObserveSenseDeepRows(key, {
          ownItems,
          enemyItems,
          allEnemyItems: allItems.filter((row) => row.side === "enemy"),
          ownCount,
          enemyCount,
          ownLabel,
          enemyLabel,
          contestedOwn: contested.reduce((sum, row) => sum + row.ownTotal, 0),
          contestedEnemy: contested.reduce((sum, row) => sum + row.enemyTotal, 0),
          visiblePct: vision.visible_tiles_pct != null ? Math.round(Number(vision.visible_tiles_pct) * 100) : null,
          enemyBaseVisible: Boolean(vision.enemy_base_visible),
          keyRegionOwn: hasSideAt("own", keyLabels),
          keyRegionEnemy: hasSideAt("enemy", keyLabels),
          liveContacts: vision.live_contacts,
          knownContacts: vision.known_contacts,
          staleSeconds,
          enemyVisibleNow: enemyCount > 0,
          lastSeenText: tracker.lastSeenGameTime || (staleSeconds ? `${staleSeconds} 秒前` : ""),
          homeDefenseOwn: hasSideAt("own", [`${ownPrefix}1`, `${ownPrefix}2`]),
          homeDefenseEnemy: hasSideAt("enemy", [`${enemyPrefix}1`, `${enemyPrefix}2`]),
          keyDefenseOwn: hasSideAt("own", keyLabels),
          keyDefenseEnemy: hasSideAt("enemy", keyLabels),
          minerals: own.minerals,
          vespene: own.vespene,
          workers: own.workers,
          bases: own.bases,
          supplyUsed: own.supply_used,
          supplyCap: own.supply_cap,
          enemyBasesSeen: en.bases_seen,
          enemyVisibleSupply: en.visible_supply,
        });
        const ownLead = deepRows.filter((row) => row.lead === "own").length;
        const enemyLead = deepRows.filter((row) => row.lead === "enemy").length;
        const deepLead = ownLead > enemyLead ? ownLabel : enemyLead > ownLead ? enemyLabel : "双方基本相当";
        const leadBits = deepRows.filter((row) => row.lead !== "even").map((row) => `${row.label}·${row.leadLabel}`);
        const question = questions[key];
        const copies = {
          strike: {
            summary: `${question}：可见打击 ${ownLabel} ${ownCount} / ${enemyLabel} ${enemyCount}。深层对比中我方领先 ${ownLead} 项、对方领先 ${enemyLead} 项，综合判断为${deepLead}。我方部署于 ${ownHot.join("、") || "未标位置"}，敌方可见于 ${enemyHot.join("、") || "视野外"}。${contested.length ? `交火区域：${contested.map((row) => row.location).join("、")}。` : "当前无同区域交火。"}`,
            feedback: (deepRows.find((row) => row.label === "可攻击目标" && row.lead === "enemy")
              ? "敌方可攻击目标类型更完整，正面对抗易受克制，宜规避短板后再交战。"
              : "") +
              (deepRows.find((row) => row.label === "火力" && row.lead === "own")
                ? "火力占优，可在覆盖充分的方向寻求交战窗口；覆盖不足时不宜分兵。"
                : deepRows.find((row) => row.label === "火力" && row.lead === "enemy")
                  ? "可见火力对方占优，本维度不宜主动接战，宜侧翼机动或等待补员。"
                  : "火力基本相当，差距主要在部署位置与交战时机。"),
          },
          detect: {
            summary: `${question}：我方侦察点 ${ownHot.join("、") || "无"}，敌方可见侦察 ${enemyHot.join("、") || "尚未发现"}。视野约 ${vision.visible_tiles_pct != null ? `${Math.round(Number(vision.visible_tiles_pct) * 100)}%` : "—"}，即时接触 ${vision.live_contacts ?? "—"}。综合判断为${deepLead}。`,
            feedback: (deepRows.find((row) => row.label === "隐身探测" && row.lead !== "own")
              ? "反隐能力不足，一旦出现隐身或潜地单位，情报链路容易中断。"
              : "反隐手段基本具备。") +
              (ownCount <= 0
                ? " 我方探测力量缺位，高价值区域与敌主力消失点的掌握将滞后，应优先补充侦察。"
                : " 应优先覆盖仍处于视野外的关键区域，而不是跟随前线部队移动。"),
          },
          defense: {
            summary: `${question}：本维 ${ownLabel} ${ownCount}（建筑 ${ownStructures}）/ ${enemyLabel} ${enemyCount}。主矿与二矿防守点 ${hasSideAt("own", [`${ownPrefix}1`, `${ownPrefix}2`])} 处有部署。综合判断为${deepLead}。`,
            feedback: (deepRows.find((row) => row.label === "关键区域强度" && row.lead === "enemy")
              ? "关键矿区与关口防守偏弱，应先巩固基地前沿，再考虑前线投入。"
              : "") +
              (ownCount <= 0
                ? " 基地前沿缺少驻守，采集线易被渗透。"
                : contested.length
                  ? " 已出现接触点，应避免继续抽调基地防守。"
                  : " 防线尚在，需确认回撤通道是否被前线抽调削弱。"),
          },
          support: {
            summary: `${question}：补给 ${own.supply_used ?? "—"}/${own.supply_cap ?? "—"}，工人 ${own.workers ?? "—"}，基地 ${own.bases ?? "—"}，晶矿 ${own.minerals ?? "—"} / 瓦斯 ${own.vespene ?? "—"}。保障部署于 ${ownHot.join("、") || "未标位置"}。综合判断为${deepLead}。`,
            feedback: Number(own.supply_used) >= Number(own.supply_cap) && Number(own.supply_cap) > 0
              ? "补给已达上限，继续交战将导致补员中断，应优先扩大人口与补给建筑。"
              : (deepRows.find((row) => row.label === "运输 / 恢复" && row.lead !== "own")
                ? "投送与恢复能力偏弱，前线损耗难以及时回补，应补充运输或医疗编制。"
                : "后勤尚可维持，需关注扩张点安全与瓦斯供给，避免被前线节奏过度抽调。"),
          },
        };
        const copy = copies[key] || copies.strike;
        const llmApi = (this.forceDispositionAdviceSections || []).find((section) => section.key === key);
        const llmLocal = (this.forceLlmSuggestionSections || []).find((section) => section.key === key);
        const llm = llmApi || llmLocal || {};
        const localAdvice = leadBits.length
          ? `建议优先巩固已领先项（${leadBits.slice(0, 2).join("、")}），短板项暂不主动交战。`
          : "本维度尚未形成明显差距，宜先补齐可见编制与部署信息后再作判断。";
        return {
          key,
          tone: key,
          title: this.activeModuleAgent.name,
          duty: this.activeModuleAgent.duty,
          icon: meta.icon,
          label: meta.label.replace("类", ""),
          question,
          ownCount,
          enemyCount,
          ownStructures,
          enemyStructures,
          ownMobile: Math.max(0, ownCount - ownStructures),
          enemyMobile: Math.max(0, enemyCount - enemyStructures),
          diffLabel,
          ownUnits: ownUnits.length ? ownUnits : ["暂无可见单位"],
          enemyUnits: enemyUnits.length ? enemyUnits : ["暂无可见单位"],
          locations,
          deepRows,
          deepLead,
          ownLead,
          enemyLead,
          summary: copy.summary,
          feedback: String(copy.feedback || "").replace(/\s+/g, " ").trim(),
          suggestionTitle: llm.title || `${meta.label}建议`,
          suggestionSummary: llm.summary || localAdvice,
          suggestionDetail: llm.detail || (leadBits.length ? `对照项：${leadBits.join("；")}。` : ""),
          llmFromApi: Boolean(llmApi),
          locationCount: locations.length,
          ownHotText: ownHot.join("、") || "未标位置",
          enemyHotText: enemyHot.join("、") || "迷雾中",
        };
      },
      isOrientSenseLayer() {
        return Boolean(ORIENT_SENSE_LAYER_CAP[this.activeRightLayer]);
      },
      isCotSenseLayer() {
        return Boolean(COT_SENSE_LAYER_CAP[this.activeRightLayer]);
      },
      isEnvironmentPendingLayer() {
        return false;
      },
      isObserveEnvironmentLayer() {
        return this.activeRightLayer === "obsEnvironment";
      },
      isOrientEnvironmentLayer() {
        return this.activeRightLayer === "oriEnvironment";
      },
      isCotEnvironmentLayer() {
        return this.activeRightLayer === "cotEnvironment";
      },
      observeEnvironmentView() {
        if (!this.isObserveEnvironmentLayer) return null;
        const sensing = this.deerflowAwarenessSensing || {};
        const own = sensing.own || {};
        const en = sensing.enemy || {};
        const vision = sensing.vision || {};
        const allItems = this.forceDeploymentItems || [];
        const ownLocs = allItems
          .filter((row) => row.side === "own")
          .map((row) => `${row.location || ""} ${row.name || ""}`.trim())
          .filter(Boolean);
        const enemyLocs = allItems
          .filter((row) => row.side === "enemy")
          .map((row) => `${row.location || ""} ${row.name || ""}`.trim())
          .filter(Boolean);
        const sampleCtx = {
          live: true,
          ownBases: 2,
          ownMinerals: 920,
          ownVespene: 240,
          enemyBasesSeen: 2,
          enemyVisibleSupply: 18,
          visiblePct: 28,
          ownLocations: ["B2 高地口", "峡谷南口隘口", "中路瞭望塔", "主矿", "二矿", "双气"],
          enemyLocations: ["北坡高地", "峡谷北口", "西翼岩石已拆走廊", "主矿", "二矿"],
        };
        const liveCtx = {
          live: Boolean(this.liveUiArmed && this.uiMode === "live"),
          ownBases: own.bases,
          ownMinerals: own.minerals,
          ownVespene: own.vespene,
          enemyBasesSeen: en.bases_seen,
          enemyVisibleSupply: en.visible_supply,
          visiblePct: vision.visible_tiles_pct != null
            ? Math.round(Number(vision.visible_tiles_pct) * 100)
            : null,
          ownLocations: ownLocs,
          enemyLocations: enemyLocs,
        };
        const board = buildObserveEnvironmentBoard(
          this.observeOwnLabel,
          this.observeEnemyLabel,
          this.uiMode === "sample" && !this.hideSampleArtifacts ? sampleCtx : liveCtx,
        );
        const llmApi = (this.forceDispositionAdviceSections || []).find((section) => section.key === "environment");
        const llmLocal = (this.forceLlmSuggestionSections || []).find((section) => section.key === "environment");
        const llm = llmApi || llmLocal || {};
        return {
          ...board,
          title: this.activeModuleAgent?.name || board.title,
          duty: this.activeModuleAgent?.duty || board.duty,
          suggestionTitle: llm.title || board.suggestionTitle,
          suggestionSummary: llm.summary || board.suggestionSummary,
          suggestionDetail: llm.detail || board.suggestionDetail,
          llmFromApi: Boolean(llmApi) || this.isSampleShowcase,
        };
      },
      orientEnvironmentFlow() {
        if (!this.isOrientEnvironmentLayer) return null;
        const flow = buildOrientEnvironmentFlow(this.observeOwnLabel, this.observeEnemyLabel);
        const apiSteps = (this.orientQa.dimension === "environment")
          ? (this.orientQa.steps || [])
          : [];
        const apiMap = Object.fromEntries(apiSteps.map((step) => [step.key, step]));
        const stages = flow.stages.map((stage) => {
          const api = apiMap[stage.key];
          if (!api) return stage;
          const evidence = String(api.evidence || "").trim();
          return {
            ...stage,
            text: String(api.text || stage.text || "").trim() || stage.text,
            evidenceItems: evidence
              ? evidence.split(/[；;]+/).map((s) => s.trim()).filter(Boolean)
              : stage.evidenceItems,
          };
        });
        const conclusion = String(this.orientQa.conclusion || flow.conclusion || "").trim() || flow.conclusion;
        const briefing = this.orientQaBriefingText && this.orientQa.dimension === "environment" && this.orientQa.loadedAt
          ? this.orientQaBriefingText
          : flow.briefing;
        return {
          ...flow,
          title: this.activeModuleAgent?.name || flow.title,
          duty: this.activeModuleAgent?.duty || flow.duty,
          stages,
          conclusion,
          briefing,
          llmFromApi: Boolean(this.orientQa.loadedAt && this.orientQa.dimension === "environment"),
        };
      },
      activeEnvCotRound() {
        const rows = Array.isArray(this.envCotRounds) ? this.envCotRounds : [];
        return rows.find((row) => row.id === this.activeEnvCotRoundId) || rows[0] || null;
      },
      envCotLoop() {
        if (this.activeEnvCotRound?.loop) return this.activeEnvCotRound.loop;
        if (this.uiMode === "sample" && !this.hideSampleArtifacts) return ENV_SELFPLAY_LOOP;
        return null;
      },
      envCotTurns() {
        return Array.isArray(this.envCotLoop?.turns) ? this.envCotLoop.turns : [];
      },
      envCotTurnOurs() {
        return this.envCotTurns.find((turn) => turn.role === "ours") || null;
      },
      envCotTurnEnemy() {
        return this.envCotTurns.find((turn) => turn.role === "enemy") || null;
      },
      envCotTurnReflect() {
        return this.envCotTurns.find((turn) => turn.role === "reflect") || null;
      },
      envCotFinal() {
        return this.envCotLoop?.finalPlan || null;
      },
      envCotFinalThoughtList() {
        const plan = this.envCotFinal;
        if (!plan) return [];
        if (Array.isArray(plan.steps) && plan.steps.length) {
          return plan.steps.map((step) => String(step || "").trim()).filter(Boolean);
        }
        return splitCotThoughts(plan.summary || plan.conclusion || "");
      },
      environmentPendingCopy() {
        const agent = this.activeModuleAgent || {};
        return {
          title: agent.name || "作战环境智能体",
          duty: agent.duty || "内容待定",
          note: "本席位已挂载，详细内容待定。",
        };
      },
      cotSenseDimKey() {
        return COT_SENSE_LAYER_CAP[this.activeRightLayer] || "";
      },
      cotSenseDimLabel() {
        const meta = CAPABILITY_META[this.cotSenseDimKey];
        return meta ? String(meta.label || "").replace("类", "") : "打击";
      },
      cotSenseTitle() {
        return this.activeModuleAgent?.name || `决策自博弈思维链生成智能体`;
      },
      orientSenseCapabilityKey() {
        if (this.activeRightLayer === "oriEnvironment") return "environment";
        return ORIENT_SENSE_LAYER_CAP[this.activeRightLayer] || "";
      },
      orientSenseTitle() {
        const label = this.orientBoard?.dimLabel || "打击";
        return `红方${label}态势认知智能体`;
      },
      orientQaTitle() {
        if (this.isOrientEnvironmentLayer || this.orientSenseCapabilityKey === "environment") {
          return "红方作战环境认知分析智能体";
        }
        const label = this.orientBoard?.dimLabel || "打击";
        return `红方${label}态势认知分析智能体`;
      },
      orientQaBriefingText() {
        const msgs = this.orientQa.messages || [];
        const last = [...msgs].reverse().find((m) => m.role === "assistant");
        const text = String(last?.content || this.orientQa.conclusion || this.orientSenseFlow?.conclusion || "").trim();
        return text || "等待大模型总结。点击「刷新大模型意见」后写入本栏。";
      },
      orientQaUserNotes() {
        return (this.orientQa.messages || []).filter((m) => m.role === "user").slice(-8);
      },
      orientSenseFlow() {
        const label = this.orientBoard?.dimLabel || "打击";
        const board = this.orientBoard || {};
        const dimKey = board.dimKey || "strike";
        const clip = (text, max = 72) => {
          const s = String(text || "").replace(/\s+/g, " ").trim();
          return s.length > max ? `${s.slice(0, max)}…` : s;
        };
        const apiSteps = (this.orientQa.dimension === dimKey)
          ? (this.orientQa.steps || [])
          : [];
        const apiMap = Object.fromEntries(apiSteps.map((step) => [step.key, step]));
        const intent = (board.intents || [])[0];
        const vuln = (board.highVulns || [])[0];
        const chain = (board.chains || [])[0];
        const node = (board.criticalList || [])[0];
        const capRow = (this.forceCapabilityRows || []).find((row) => row.key === dimKey);
        const ownForce = Number(capRow?.own ?? vuln?.own ?? 0);
        const enemyForce = Number(capRow?.enemy ?? vuln?.enemy ?? 0);
        const forceEvidence = `编制对照 ${this.observeOwnLabel} ${ownForce} / ${this.observeEnemyLabel} ${enemyForce}${capRow?.diffLabel ? `（${capRow.diffLabel}）` : ""}`;
        const dimItems = (this.forceDeploymentItems || []).filter(
          (row) => (CAPABILITY_META[row.capability] ? row.capability : "strike") === dimKey,
        );
        const sidePack = (side) => {
          const counter = Object.create(null);
          const locs = [];
          for (const row of dimItems) {
            if ((row.side === "enemy" ? "enemy" : "own") !== side) continue;
            const count = Number(row.count) || 0;
            counter[row.name] = (counter[row.name] || 0) + count;
            if (row.location && row.location !== "--") locs.push(`${row.location} ${row.name}×${count}`);
          }
          const units = Object.entries(counter)
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .slice(0, 6)
            .map(([name, count]) => `${name}×${count}`)
            .join("、");
          return {
            units: units || "未见编制",
            locs: [...new Set(locs)].slice(0, 5).join("、") || "落点未标",
          };
        };
        const ownPack = sidePack("own");
        const enemyPack = sidePack("enemy");
        let riskText = `我方${label}风险待观察`;
        if (capRow || vuln) {
          if (ownForce + enemyForce === 0) {
            riskText = `该维未见交战条件，我方风险不明`;
          } else if (ownForce < enemyForce) {
            riskText = `我方${label}兵力落后，强打风险高`;
          } else if (ownForce === enemyForce) {
            riskText = `双方${label}胶着，贸然推进有损失`;
          } else {
            riskText = `我方${label}占优，风险可控但仍需控损`;
          }
        }
        const nodeChain = (chain?.nodes || [])
          .slice(0, 4)
          .map((item) => item?.name || item?.title || item?.label || item)
          .filter(Boolean)
          .join(" → ");
        const local = {
          intent: {
            text: intent ? `${intent.label}·${intent.levelCn}` : `暂无中等以上${label}意图`,
            evidenceItems: collectEvidenceBits([
              intent?.evidence,
              intent?.description,
              `${this.observeOwnLabel}可见：${ownPack.units}；${ownPack.locs}`,
              `${this.observeEnemyLabel}可见：${enemyPack.units}；${enemyPack.locs}`,
              forceEvidence,
            ]),
          },
          risk: {
            text: riskText,
            evidenceItems: collectEvidenceBits([
              forceEvidence,
              `${this.observeOwnLabel}部署 ${ownPack.units} @ ${ownPack.locs}`,
              `${this.observeEnemyLabel}部署 ${enemyPack.units} @ ${enemyPack.locs}`,
              vuln?.reason,
            ]),
          },
          vuln: {
            text: vuln ? clip(vuln.reason, 64) : `尚未形成${label}脆弱判断`,
            evidenceItems: collectEvidenceBits([
              vuln?.reason,
              forceEvidence,
              vuln?.topTarget ? `可打节点 ${vuln.topTarget}` : "",
              vuln?.topReason,
              enemyPack.units !== "未见编制" ? `蓝方该维暴露 ${enemyPack.units}` : "蓝方该维几乎未见部署",
            ]),
          },
          nodes: {
            text: node ? `${node.name} · 执行度 ${node.executabilityLevel}` : "暂无高执行度节点",
            evidenceItems: collectEvidenceBits([
              node ? `${node.name} @ ${node.location || "未标"}，执行度 ${node.executabilityLevel}，价值 ${node.valueScore ?? "—"}` : "",
              node?.reason,
              chain?.summary,
              nodeChain ? `链路 ${chain?.title || label}：${nodeChain}` : "",
              (board.criticalList || []).slice(0, 3).map((item) => `${item.name}@${item.location} 执行度${item.executabilityLevel}`),
            ]),
          },
        };
        const keys = [
          { key: "intent", title: `蓝方${label}意图预测` },
          { key: "risk", title: `红方受${label}风险研判` },
          { key: "vuln", title: `蓝方${label}脆弱性分析` },
          { key: "nodes", title: `蓝方${label}关键节点预测` },
        ];
        const stages = keys.map((item) => {
          const api = apiMap[item.key] || {};
          const fallback = local[item.key];
          const apiItems = collectEvidenceBits([api.evidence], 6, 120);
          const evidenceItems = collectEvidenceBits([apiItems, fallback.evidenceItems], 8, 120);
          return {
            ...item,
            text: clip(api.text || fallback.text, 72),
            evidence: evidenceItems.join("；") || "等待可见证据",
            evidenceItems: evidenceItems.length ? evidenceItems : ["等待可见证据"],
          };
        });
        const lastBrief = [...(this.orientQa.messages || [])].reverse().find((m) => m.role === "assistant" && m.kind === "briefing");
        const fromQa = String(this.orientQa.conclusion || lastBrief?.conclusion || "").trim();
        const fromStages = stages
          .map((row) => String(row.text || "").trim())
          .filter((text) => text && !/待观察|暂无|尚未形成|等待/.test(text))
          .join(" ");
        const conclusion = fromQa || fromStages || `等待「红方${label}态势认知分析智能体」每 30 秒落卷后写入本栏。`;
        return {
          stages,
          conclusion,
          conclusionHtml: fromQa ? this.renderMarkdown(fromQa) : "",
          conclusionTitle: `对蓝方${label}态势分析`,
        };
      },
      orientQaContextText() {
        const board = this.orientBoard;
        if (!board) return "";
        const bits = [];
        const dimKey = board.dimKey || "strike";
        for (const row of board.intents || []) {
          bits.push(`意图 ${row.label}（${row.levelCn}）：${row.description}`);
          if (row.evidence?.length) bits.push(`意图证据：${row.evidence.join("；")}`);
        }
        const capRow = (this.forceCapabilityRows || []).find((row) => row.key === dimKey);
        if (capRow) {
          bits.push(`我方${board.dimLabel || "打击"}风险对照：${this.observeOwnLabel} ${capRow.own} / ${this.observeEnemyLabel} ${capRow.enemy}（${capRow.diffLabel}）`);
        }
        const dimItems = (this.forceDeploymentItems || []).filter(
          (row) => (CAPABILITY_META[row.capability] ? row.capability : "strike") === dimKey,
        );
        const pack = (side) => dimItems
          .filter((row) => (row.side === "enemy" ? "enemy" : "own") === side)
          .map((row) => `${row.name}×${row.count}@${row.location || "未标"}`)
          .slice(0, 8)
          .join("、");
        const ownText = pack("own");
        const enemyText = pack("enemy");
        if (ownText) bits.push(`${this.observeOwnLabel}该维部署：${ownText}`);
        if (enemyText) bits.push(`${this.observeEnemyLabel}该维部署：${enemyText}`);
        for (const row of board.highVulns || []) {
          bits.push(`脆弱 ${row.label}（${row.levelCn}）：${row.reason}；节点 ${row.topTarget}`);
        }
        for (const chain of (board.chains || []).slice(0, 3)) {
          bits.push(`链路 ${chain.title}：${chain.summary}`);
        }
        for (const target of (board.criticalList || []).slice(0, 4)) {
          bits.push(`节点 ${target.name}@${target.location} 执行度 ${target.executabilityLevel || target.executability} 价值 ${target.valueScore ?? "—"}`);
        }
        return bits.join("\n");
      },
      orientSenseRole() {
        const key = this.orientSenseCapabilityKey;
        // 作战环境认知不按打探防保过滤兵力，避免沙盘被清空
        if (!key || key === "environment") return "";
        return this.capabilityToRole(key);
      },
      orientMapHighlightRoles() {
        const key = this.orientSenseCapabilityKey;
        if (!key || key === "environment") {
          return new Set();
        }
        const role = this.capabilityToRole(key);
        if (role) {
          const next = new Set([role]);
          if (role === "support") next.add("base");
          return next;
        }
        const dims = this.deerflowRisk?.intent?.intent_dimensions || {};
        const dimToRole = { attack: "strike", defense: "defend", detect: "scout", support: "support" };
        return new Set(
          Object.entries(dims)
            .filter(([, d]) => {
              const level = String(d?.level || "").toLowerCase();
              return level === "high" || level === "medium";
            })
            .map(([k]) => dimToRole[k])
            .filter(Boolean),
        );
      },
      isOrientHubLayer() {
        return this.activeRightLayer === "orient" || this.isOrientSenseLayer;
      },
      orientBoard() {
        const dimFilter = this.orientSenseCapabilityKey;
        const evidenceGroups = this.deerflowIntentEvidenceGroups || [];
        const capRows = this.forceCapabilityRows || [];
        const ownBdAll = this.forceCapabilityBreakdown?.own || {};
        const enemyBdAll = this.forceCapabilityBreakdown?.enemy || {};
        const intents = (this.deerflowIntentDimensionRows || [])
          .filter((row) => row.level === "high" || row.level === "medium")
          .filter((row) => !dimFilter || ORIENT_CAP_TO_INTENT[dimFilter] === row.key)
          .map((row) => {
            const capKey = row.key === "attack" ? "strike" : row.key;
            const ev = (evidenceGroups.find((group) => group.key === row.key)?.items || [])
              .map((item) => String(item || "").trim())
              .filter(Boolean);
            const cap = capRows.find((item) => item.key === capKey);
            const ownUnits = ownBdAll[capKey]?.units || [];
            const enemyUnits = enemyBdAll[capKey]?.units || [];
            const locBits = (this.forceDeploymentItems || [])
              .filter((item) => item.side === "enemy" && (CAPABILITY_META[item.capability] ? item.capability : "strike") === capKey)
              .map((item) => `${item.name}×${item.count}@${item.location || "未标"}`)
              .slice(0, 5);
            return {
              ...row,
              capKey,
              evidence: collectEvidenceBits([
                ev,
                row.description,
                cap ? `编制对照 ${this.observeOwnLabel} ${cap.own} / ${this.observeEnemyLabel} ${cap.enemy}（${cap.diffLabel}）` : "",
                ownUnits.length ? `${this.observeOwnLabel}该维可见：${ownUnits.slice(0, 5).join("、")}` : "",
                enemyUnits.length ? `${this.observeEnemyLabel}该维可见：${enemyUnits.slice(0, 5).join("、")}` : "蓝方该维暂无可见编制",
                locBits.length ? `蓝方落点 ${locBits.join("、")}` : "",
              ], 8, 110),
            };
          });
        const criticalItems = this.criticalTargetItems || [];
        const dimOrder = [
          { key: "strike", label: "打击", icon: "⚔" },
          { key: "detect", label: "探测", icon: "◉" },
          { key: "defense", label: "防御", icon: "🛡" },
          { key: "support", label: "保障", icon: "✚" },
        ];
        const vulnerabilities = dimOrder.map((dim) => {
          const cap = capRows.find((row) => row.key === dim.key) || { own: 0, enemy: 0, diff: 0 };
          const crits = criticalItems.filter((item) => item.category === dim.key);
          const topCrit = [...crits].sort((a, b) => (Number(b.priorityScore) || 0) - (Number(a.priorityScore) || 0))[0];
          const lead = Number(cap.diff) || 0;
          const critScore = Number(topCrit?.priorityScore) || 0;
          const emptyEnemy = Number(cap.enemy) === 0 && Number(cap.own) > 0 ? 12 : 0;
          const thinEnemy = Number(cap.own) > 0 && Number(cap.enemy) > 0 && Number(cap.enemy) <= Number(cap.own) ? 8 : 0;
          const score = lead * 8 + critScore * 0.45 + emptyEnemy + thinEnemy;
          let level = score >= 16 ? "high" : score >= 5 ? "medium" : "low";
          if ((emptyEnemy || lead >= 1 || topCrit) && level === "low") level = "medium";
          let reason = "可见编制尚未拉开差距，蓝方该维缺口还不稳。";
          if (emptyEnemy) reason = `蓝方该维几乎未见部署，相对 ${this.observeOwnLabel} 存在暴露窗口，可优先试探。`;
          else if (lead >= 2) reason = `${this.observeOwnLabel} 在该维数量领先 ${lead}，蓝方承压偏弱，具备切入条件。`;
          else if (thinEnemy) reason = `双方该维都可见，但蓝方数量不占优，防线厚度不足。`;
          else if (topCrit) reason = `已识别可打节点 ${topCrit.name}（${topCrit.location}），该维存在可撕开的点。`;
          return {
            ...dim,
            level,
            levelCn: level === "high" ? "高" : level === "medium" ? "中" : "低",
            own: cap.own || 0,
            enemy: cap.enemy || 0,
            lead,
            critCount: crits.length,
            topTarget: topCrit ? `${topCrit.name} · ${topCrit.location}` : "暂无高价值节点",
            topReason: topCrit?.reason || "",
            reason,
            score,
          };
        });
        const shownVulns = vulnerabilities
          .filter((row) => row.level === "high" || row.level === "medium")
          .filter((row) => !dimFilter || row.key === dimFilter);
        const enemyItems = (this.forceDeploymentItems || []).filter((row) => row.side === "enemy");
        const byLoc = new Map();
        for (const row of enemyItems) {
          const loc = row.location && row.location !== "--" ? row.location : "";
          if (!loc) continue;
          if (dimFilter && (CAPABILITY_META[row.capability] ? row.capability : "strike") !== dimFilter) continue;
          if (!byLoc.has(loc)) byLoc.set(loc, []);
          byLoc.get(loc).push(row);
        }
        const collabs = [];
        for (const [location, rows] of byLoc.entries()) {
          const names = [...new Set(rows.map((row) => row.name))].slice(0, 4);
          const caps = [...new Set(rows.map((row) => (CAPABILITY_META[row.capability] ? row.capability : "strike")))];
          if (names.length < 2) continue;
          const capLabels = caps.map((key) => String(CAPABILITY_META[key]?.label || key).replace("类", "")).join(" / ");
          collabs.push({
            key: location,
            location,
            title: `${names[0]} 协同 ${names[1]}`,
            detail: `${location}：${names.join("、")}。能力组合 ${capLabels || "未分类"}。`,
            caps,
          });
        }
        const chainGroups = (this.killChainDimensionGroups || []).map((group) => ({
          ...group,
          chains: (group.chains || []).map((chain) => ({
            ...chain,
            nodes: Array.isArray(chain.nodes) ? chain.nodes : [],
          })),
        }));
        const visibleChainGroups = dimFilter
          ? chainGroups.filter((group) => group.key === dimFilter)
          : chainGroups;
        const allChains = chainGroups.flatMap((group) =>
          (group.chains || []).map((chain) => ({
            ...chain,
            dim: group.key,
            dimLabel: group.label,
            dimIcon: group.icon,
          })),
        );
        const filteredChains = dimFilter ? allChains.filter((chain) => chain.dim === dimFilter) : allChains;
        const flowSteps = (this.killChainEnemyStrikeFlow?.steps || []).filter((step) => !dimFilter || step.dim === dimFilter);
        const flowSummary = this.killChainEnemyStrikeFlow?.summary || "等待作战模式步骤";
        const selectedPlan = this.killChainSelectedPlan;
        const criticalFiltered = dimFilter
          ? criticalItems.filter((item) => item.category === dimFilter)
          : criticalItems;
        const ownRows = (this.forceDeploymentItems || []).filter((row) => row.side === "own");
        const taskPool = this.killChainTaskPool || [];
        const criticalRanked = criticalFiltered
          .map((item) => {
            const matched = taskPool.find((task) => task.targetLocation === item.location && task.dimension === item.category)
              || taskPool.find((task) => task.targetLocation === item.location);
            const ownNear = ownRows
              .filter((row) => row.location === item.location)
              .reduce((sum, row) => sum + (Number(row.count) || 1), 0);
            const locKnown = item.location && item.location !== "--";
            const heuristic = clamp(Math.round(
              (locKnown ? 26 : 8)
              + Math.min(34, ownNear * 7)
              + (Number(item.valueScore) || 0) * 0.2
              + (item.category === "support" ? 8 : 0)
              + (item.category === "detect" ? 6 : 0),
            ));
            const executability = Number(matched?.executability) || heuristic;
            return {
              ...item,
              executability,
              executabilityLevel: normalizeLevelWord(scoreToLevel(executability)),
            };
          })
          .sort((a, b) => (b.executability - a.executability) || (b.priorityScore - a.priorityScore));
        const selected =
          criticalRanked.find((item) => item.key === this.selectedCriticalTargetKey) ||
          criticalRanked[0] ||
          null;
        return {
          isHub: !dimFilter,
          dimKey: dimFilter,
          dimLabel: dimFilter ? String(CAPABILITY_META[dimFilter]?.label || dimFilter).replace("类", "") : "",
          title: this.activeModuleAgent.name,
          duty: this.activeModuleAgent.duty,
          strategyText: String(this.deerflowRisk?.intent?.primary_strategy_cn || this.deerflowRisk?.intent?.primary_strategy || "").trim(),
          intentConfidence: Number(this.deerflowRisk?.intent?.confidence || 0),
          intents,
          intentEmpty: !intents.length,
          highVulns: shownVulns,
          vulnEmpty: !shownVulns.length,
          vulnCompare: vulnerabilities,
          collabs: collabs.slice(0, 6),
          chainGroups: visibleChainGroups,
          chainReady: visibleChainGroups.some((group) => (group.chains || []).length > 0),
          chains: filteredChains.slice(0, 8),
          flowSteps,
          flowSummary,
          planTitle: selectedPlan?.title || "蓝方作战链路",
          planDesc: selectedPlan?.description || "按打击 / 探测 / 防御 / 保障列出蓝方当前可见作战链路。",
          selectedCritical: selected,
          criticalList: criticalRanked.slice(0, 12),
          criticalReady: criticalRanked.length > 0,
        };
      },
      activeModuleAgent() {
        return moduleAgentOf(this.activeRightLayer, {
          observeSide: this.observeSide,
          plannerTab: this.activePlannerTab,
          evalTab: this.evalInnerTab,
        });
      },
      /** 当前激活模块的沙盘独立配置；模块不在映射表中时回退为全开 */
      activeModuleConfig() {
        const fallback = { showFog: true, showResources: true, showRangeCircles: false, showUnits: true };
        return this.moduleMinimapConfigs[this.activeRightLayer] || fallback;
      },
      deerflowAwarenessSensing() {
        // 本地实时读取（dashboard_state.json）——不依赖 智能体 流水线产物。
        const game = this.observeGame || {};
        const frame = this.observeState?.latestFeatureFrame || {};
        const frameVision = frame.vision && typeof frame.vision === "object" ? frame.vision : {};
        return {
          game_time: game.timeFormatted || "",
          game_loop: frame.game_loop ?? game.iteration ?? "",
          player_id: this.observeSide === "blue" ? 2 : 1,
          perspective: this.observeSide === "blue" ? "人机" : "红方智能体",
          perspectiveHint: game.perspectiveHint || "",
          map: {
            name: game.mapName || "",
            start_location: game.startLocation ? [game.startLocation] : [],
            size: null,
          },
          vision: {
            visible_tiles_pct: frameVision.visible_tiles_pct ?? null,
            fog_of_war_pct: frameVision.fog_of_war_pct ?? null,
            enemy_base_visible: Boolean(game.enemyStructuresVisible),
            known_contacts: game.knownContactCount ?? null,
            live_contacts: game.liveContactCount ?? null,
          },
          scouting: {
            stale_after_seconds: 150,
          },
          own: {
            minerals: game.economyKnown === false ? null : game.minerals ?? null,
            vespene: game.economyKnown === false ? null : game.vespene ?? null,
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
        const game = this.observeGame || {};
        const pipelineCompute = this.deerflowRisk?.stages?.compute;
        if (this.observeSide !== "blue" && pipelineCompute && typeof pipelineCompute === "object" && Object.keys(pipelineCompute).length > 0) {
          return pipelineCompute;
        }
        const ownSupply = Number(game.supplyUsed ?? 0);
        const enemySupplyProxy = Number(game.enemyUnitsVisible ?? 0);
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
          trend_30s_note: this.observeSide === "blue"
            ? "蓝方为人机视角：由我方可见单位反演其观察与兵力。"
            : "本面板已切为本地实时读取；敌方为可见代理指标。",
        };
      },
      deerflowAwarenessMeta() {
        if (this.deerflowRiskLoading) return "大模型快速分析中";
        if (!this.deerflowRisk.thread_id) return "等待分析结果";
        const mode = String(this.deerflowRisk.mode || "").trim();
        if (mode === "fast") return "快速分析已更新 · 本地感知层同步";
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
        if (o.workers != null && o.workers !== "") parts.push(`红方工蜂 ${o.workers}`);
        if (o.bases != null && o.bases !== "") parts.push(`红方基地 ${o.bases}`);
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
              ? "红方优势"
              : ratioOk && ratioN <= 0.88
                ? "蓝方优势"
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
      deerflowAwarenessSensingRows() {
        const s = this.deerflowAwarenessSensing || {};
        if (!this.deerflowAwarenessSensingHasPayload) return { ownPanels: [], enemyPanels: [] };
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
          const start = String(this.observeGame?.startLocation || "A1");
          const ownPrefix = start.startsWith("B") ? "B" : "A";
          const enemyPrefix = ownPrefix === "A" ? "B" : "A";
          const markers = Array.isArray(this.observeState?.minimapOverlay) ? this.observeState.minimapOverlay : [];
          const ownDetails = Array.isArray(this.observeGame?.ownForceDetails) ? this.observeGame.ownForceDetails : [];
          const enemyDetails = Array.isArray(this.observeGame?.enemyVisibleDetails) ? this.observeGame.enemyVisibleDetails : [];
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
            { name: "关键矿区（红方二矿）", label: `${ownPrefix}2` },
            { name: "关键矿区（蓝方主矿）", label: `${enemyPrefix}1` },
            { name: "关键矿区（蓝方二矿）", label: `${enemyPrefix}2` },
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
        const buildEnemyChangeLines = () => {
          const game = this.observeGame || {};
          const frame = this.observeState?.latestFeatureFrame || {};
          const compute = this.deerflowAwarenessCompute || {};
          const trend = compute.trend_30s && typeof compute.trend_30s === "object" ? compute.trend_30s : {};
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

          let economyLine = `经济变化：按对局约 ${minute.toFixed(1)} 分钟推估，敌方约 ${inferredBases} 基地、工人 ${workerLow}-${workerHigh}。`;
          if (visibleStructures >= 2) {
            economyLine = `经济变化：已见建筑 ${visibleStructures}，更像在扩点，估计 ${inferredBases} 基地同时抽矿，工人约 ${workerLow}-${workerHigh}。`;
          } else if (visibleStructures <= 0 && minute >= 4) {
            economyLine = `经济变化：经济建筑仍在雾里。按时间粗算可能已开到 ${inferredBases} 矿，工人约 ${workerLow}-${workerHigh}，误差会偏大。`;
          } else if (minute < 3) {
            economyLine = `经济变化：开局阶段，敌方多半还在单矿爬工人，暂按 ${workerLow}-${workerHigh} 工、1 基地估算。`;
          }

          let race = "种族未确认";
          if (hasAny("marine", "marauder", "reaper", "hellion", "siegetank", "barracks", "factory", "starport", "commandcenter", "scv")) {
            race = "人族";
          } else if (hasAny("zealot", "stalker", "adept", "gateway", "nexus", "stargate", "robotics", "cyberneticscore", "probe")) {
            race = "星灵";
          } else if (hasAny("zergling", "roach", "hydralisk", "hatchery", "lair", "spire", "spawningpool", "drone")) {
            race = "异虫";
          }

          let techShift = "还没看到新的科技节点，树仍偏早期";
          if (hasAny("battlecruiser", "fusioncore")) techShift = "出现大和/融合核心迹象，科技突然拔高";
          else if (hasAny("starport", "medivac", "viking", "liberator", "banshee", "raven")) techShift = "星港已经出来，空投或空军压力在加重";
          else if (hasAny("factory", "hellion", "siegetank", "cyclone", "thor")) techShift = "重工线在成型，机械战力开始抬头";
          else if (hasAny("barracks", "marine", "marauder", "reaper")) techShift = "仍停在兵营层，生化或死神开局还没转线";
          else if (hasAny("carrier", "fleetbeacon", "tempest", "voidray")) techShift = "舰队航标方向，空中科技在跳档";
          else if (hasAny("stargate", "oracle", "phoenix")) techShift = "星门已开，先知/凤凰骚扰窗口打开";
          else if (hasAny("robotics", "immortal", "observer", "colossus")) techShift = "机械台落地，地面质量在变厚";
          else if (hasAny("gateway", "stalker", "adept", "zealot", "cyberneticscore")) techShift = "折跃门地面科技在铺，还没看到高阶跳点";
          else if (hasAny("spire", "mutalisk", "corruptor")) techShift = "飞龙科技露头，转场能力在变";
          else if (hasAny("roachwarren", "roach", "ravager")) techShift = "蟑螂巢已起，地面线从轻转沉";
          else if (hasAny("spawningpool", "zergling", "banelingnest", "baneling")) techShift = "虫池/毒爆刚起，仍是轻量科技";
          const techLine = `科技变化：${race} · ${techShift}。`;

          const ownArmy = Number(game.armyCount || 0);
          const armyDelta = Number(trend.enemy_army_value_delta);
          let forceLine = `战力变化：敌可见战斗单位 ${visibleUnits}，我方军力 ${ownArmy}。`;
          if (Number.isFinite(armyDelta) && armyDelta !== 0) {
            const dir = armyDelta > 0 ? "近 30 秒估值在抬" : "近 30 秒估值在掉";
            forceLine = `战力变化：${dir}（${armyDelta > 0 ? "+" : ""}${Math.round(armyDelta)}），可见单位 ${visibleUnits}，对照我军 ${ownArmy}。`;
          } else if (visibleUnits <= 0) {
            forceLine = `战力变化：主力不在视野里，可见单位 0；我军 ${ownArmy}，真实落差只能按消失前印象估。`;
          } else if (visibleUnits >= ownArmy + 6) {
            forceLine = `战力变化：眼前这股比我军更厚（可见 ${visibleUnits} / 我 ${ownArmy}），短窗口里对方更像在加压。`;
          } else if (ownArmy >= visibleUnits + 6) {
            forceLine = `战力变化：可见敌群偏瘦（${visibleUnits}），我军 ${ownArmy}；也可能只是主力绕雾，不能当成真弱。`;
          }

          const start = String(this.observeGame?.startLocation || "A1");
          const ownPrefix = start.startsWith("B") ? "B" : "A";
          const enemyPrefix = ownPrefix === "A" ? "B" : "A";
          const markers = Array.isArray(this.observeState?.minimapOverlay) ? this.observeState.minimapOverlay : [];
          const ownDetails = Array.isArray(this.observeGame?.ownForceDetails) ? this.observeGame.ownForceDetails : [];
          const hasMarker = (label) => markers.some((m) => String(m.locationLabel || "") === label);
          const hasOwn = (label) => ownDetails.some((row) => String(row.location || "") === label) || hasMarker(label);
          const hasEnemy = (label) => enemyDetails.some((row) => String(row.location || "") === label);
          const watchLabels = [
            `${ownPrefix}7`,
            `${enemyPrefix}7`,
            `${ownPrefix}2`,
            `${enemyPrefix}1`,
            `${enemyPrefix}2`,
          ];
          let held = 0;
          let lost = 0;
          let fog = 0;
          for (const label of watchLabels) {
            if (label === `${enemyPrefix}1` && s.vision?.enemy_base_visible) held += 1;
            else if (hasOwn(label)) held += 1;
            else if (hasEnemy(label)) lost += 1;
            else fog += 1;
          }
          let areaLine = `占领区域：关键点里约 ${held} 处在控、${lost} 处见敌、${fog} 处仍在雾中。`;
          if (s.vision?.enemy_base_visible) {
            areaLine = `占领区域：敌主矿已露，关键点约 ${held} 在控 / ${lost} 见敌 / ${fog} 迷雾；对面经济区正在被看见。`;
          } else if (lost >= 2) {
            areaLine = `占领区域：至少 ${lost} 个关键点见到敌迹，控区在被咬；另有 ${fog} 处还没探清。`;
          } else if (held >= 3 && lost === 0) {
            areaLine = `占领区域：眼前关键点多数仍在我方或空置（在控 ${held}），敌占区变化还不明显。`;
          }

          return [
            { text: economyLine, className: visibleStructures <= 0 && minute >= 4 ? "deerflow-sensing-line--fog" : "" },
            techLine,
            {
              text: forceLine,
              className: visibleUnits <= 0 && this.enemyMainTracker.lastSeenAtMs ? "deerflow-sensing-line--stale" : "",
            },
            areaLine,
          ].slice(0, 5);
        };
        const own = s.own || {};
        const ownLines = [
          `晶矿 ${own.minerals ?? "—"} · 瓦斯 ${own.vespene ?? "—"}`,
          `补给 ${own.supply_used ?? "—"}/${own.supply_cap ?? "—"}`,
          `工人 ${own.workers ?? "—"} · 基地 ${own.bases ?? "—"}`,
          `作战单位：${fmtUnitMap(own.units)}`,
          `建筑：${fmtBld(own.buildings)}`,
        ];
        const en = s.enemy || {};
        const enLines = [
          `可见供给 ${en.visible_supply ?? "—"}`,
          `已见基地 ${en.bases_seen ?? "—"}`,
          `可见作战单位：${fmtUnitMap(en.units)}`,
          `可见建筑：${fmtBld(en.buildings)}`,
        ];
        return {
          ownPanels: [
            { title: "红方区域监控智能体", tone: "region", lines: buildKeyRegionLines().slice(0, 5) },
            { title: "红方事实观察智能体", tone: "own", lines: ownLines },
          ],
          enemyPanels: [
            { title: "蓝方事实可见智能体", tone: "blue", lines: enLines },
            { title: "红方敌情统计智能体", tone: "forecast", lines: buildEnemyChangeLines() },
          ],
        };
      },
      deerflowAwarenessSensingBlocks() {
        const rows = this.deerflowAwarenessSensingRows || {};
        return [...(rows.ownPanels || []), ...(rows.enemyPanels || [])];
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
          tips.push("系统会按约 12 秒节奏用大模型快速刷新意图与风险；也可手动点「刷新分析」。需 Ask API 与 LLM 可用。");
          return tips;
        }
        if (this.deerflowRisk.ok) {
          tips.push("本轮意图/风险已更新：可到「四维风险分析」细看，并结合「兵力布势」协同建议执行。");
        } else if (String(this.deerflowRisk.errorText || "").trim()) {
          tips.push("若快速分析失败：请核对本机问答栈是否在 8011 端口监听、dashboard_state/Redis 是否有态势，以及 LLM Key 是否正常。");
        }
        const mode = String(this.deerflowRisk.mode || "").trim();
        if (mode === "fast") {
          tips.push("当前为单次大模型快路径（不再跑完整六阶段流水线），结果通常十数秒内回写。");
        } else {
          const stages = this.deerflowRiskPipelineStages || [];
          const missShort = stages.filter((s) => !s.filled).map((s) => s.label);
          if (missShort.length && this.deerflowRisk.thread_id) {
            tips.push(`尚未从 Redis 读到的阶段：${missShort.join("、")}。`);
          }
        }
        return tips.slice(0, 4);
      },
      deerflowIntentMeta() {
        if (this.deerflowRiskLoading) return "大模型快速分析中";
        if (!this.deerflowRisk.thread_id) return "等待分析结果";
        return "最新一轮分析结果";
      },
      deerflowIntentHistoryRecent() {
        // 去除本地化生成记忆，只保留当前一轮大模型结果
        return [];
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
          label: `${label}证据`,
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
        if (this.deerflowRiskLoading) return "大模型快速分析中";
        if (this.deerflowRisk.errorText) return "执行失败";
        if (!this.deerflowRisk.thread_id) return "等待分析结果";
        const mode = String(this.deerflowRisk.mode || "").trim();
        const tid = this.deerflowRisk.thread_id.slice(0, 8);
        return mode === "fast" ? `快速分析 · ${tid}...` : `线程 ${tid}...`;
      },
      deerflowRiskHistoryRecent() {
        // 去除本地化生成记忆，只保留当前一轮大模型结果
        return [];
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
          case "observe":
          case "obsStrike":
          case "obsDetect":
          case "obsDefense":
          case "obsSupport":
          case "obsEnvironment":
          case "orient":
          case "oriStrike":
          case "oriDetect":
          case "oriDefense":
          case "oriSupport":
          case "oriEnvironment":
          case "awareness":
          case "force":
          case "intent":
          case "risk":
          case "killchain":
          case "critical":
          case "cot":
          case "cotStrike":
          case "cotDetect":
          case "cotDefense":
          case "cotSupport":
          case "cotEnvironment":
          case "killnet":
          case "plan":
          case "eval":
            return true;
          default:
            return false;
        }
      },
      situationModuleOverlayTitle() {
        if (this.currentOodaRing) return this.currentOodaRing.agent || this.currentOodaRing.label;
        return this.activeModuleAgent?.name || "";
      },
      moduleUsesFulltextLayout() {
        return (
          this.isCotSenseLayer ||
          this.isCotEnvironmentLayer ||
          this.isEnvironmentPendingLayer ||
          this.activeRightLayer === "killnet" ||
          this.activeRightLayer === "plan" ||
          this.activeRightLayer === "eval"
        );
      },
      planCotTurns() {
        const loop = this.activeCotRound?.loop
          || (this.uiMode === "sample" && !this.hideSampleArtifacts
            ? (this.planSelfplayLoop || PLAN_SELFPLAY_LOOP)
            : null);
        return Array.isArray(loop?.turns) ? loop.turns : [];
      },
      planCotFinal() {
        const loop = this.activeCotRound?.loop
          || (this.uiMode === "sample" && !this.hideSampleArtifacts
            ? (this.planSelfplayLoop || PLAN_SELFPLAY_LOOP)
            : null);
        return loop?.finalPlan || null;
      },
      activeCotRound() {
        const rows = Array.isArray(this.cotRounds) ? this.cotRounds : [];
        return rows.find((row) => row.id === this.activeCotRoundId) || rows[rows.length - 1] || null;
      },
      cotTurnOurs() {
        return this.planCotTurns.find((turn) => turn.role === "ours") || null;
      },
      cotTurnEnemy() {
        return this.planCotTurns.find((turn) => turn.role === "enemy") || null;
      },
      cotTurnReflect() {
        return this.planCotTurns.find((turn) => turn.role === "reflect") || null;
      },
      cotFinalThoughts() {
        const plan = this.planCotFinal;
        if (!plan) return [];
        const steps = Array.isArray(plan.steps)
          ? plan.steps.map((step) => String(step || "").trim()).filter(Boolean)
          : [];
        if (steps.length) return steps;
        const dimLine = String(plan.dims?.[this.cotSenseDimKey] || "").trim();
        if (dimLine) return splitCotThoughts(dimLine);
        const conclusion = String(plan.conclusion || "").trim();
        return conclusion ? [conclusion] : [];
      },
      cotLiveRoundCount() {
        return (this.cotRounds || []).filter((row) => row.source === "llm").length;
      },
      activeKillnet() {
        return this.activeCotRound?.killnet || this.planKillnet || PLAN_KILLNET;
      },
      planKillnetNodes() {
        return Array.isArray(this.activeKillnet?.nodes) ? this.activeKillnet.nodes : [];
      },
      planKillnetEdges() {
        return Array.isArray(this.activeKillnet?.edges) ? this.activeKillnet.edges : [];
      },
      planKillnetDiagramLinks() {
        const slotOf = (from, to) => {
          const pair = [from, to].slice().sort().join("-");
          if (pair === "detect-strike") return "h-top";
          if (pair === "defense-support") return "h-bottom";
          if (pair === "defense-detect") return "v-left";
          if (pair === "strike-support") return "v-right";
          return "diag";
        };
        const axisOf = (slot) => (slot.startsWith("v-") ? "vertical" : slot.startsWith("h-") ? "horizontal" : "diag");
        return (this.planKillnetEdges || []).map((edge, idx) => {
          const slot = slotOf(edge.from, edge.to);
          return {
            key: `${edge.from}-${edge.to}-${idx}`,
            kind: edge.kind || "causal",
            slot,
            axis: axisOf(slot),
            fromLabel: this.planDimLabel(edge.from),
            toLabel: this.planDimLabel(edge.to),
            hint: String(edge.label || "").trim(),
          };
        });
      },
      planKillnetBridgeSlots() {
        const order = ["h-top", "h-bottom", "v-left", "v-right"];
        const grouped = Object.create(null);
        for (const key of order) grouped[key] = [];
        for (const link of this.planKillnetDiagramLinks || []) {
          const slot = grouped[link.slot] ? link.slot : "";
          if (!slot) continue;
          grouped[slot].push(link);
        }
        return order
          .filter((key) => grouped[key].length)
          .map((key) => ({ key, links: grouped[key] }));
      },
      planKillnetBoardNodes() {
        const rows = decorateKillnetBoard(
          this.planKillnetNodes,
          this.planKillnetEdges,
          this.activeCotFinalPlan,
          this.plannerTaskTurns,
        );
        const envPlan = this.envCotFinal
          || (this.uiMode === "sample" && !this.hideSampleArtifacts ? ENV_SELFPLAY_LOOP.finalPlan : null);
        if (!envPlan) {
          return rows.filter((node) => node.key !== "environment");
        }
        const envNode = {
          ...KILLNET_ENVIRONMENT_NODE,
          fromPlan: String(envPlan?.summary || envPlan?.conclusion || KILLNET_ENVIRONMENT_NODE.fromPlan || "").trim(),
          outputs: Array.isArray(envPlan?.steps) && envPlan.steps.length
            ? envPlan.steps.map((s) => String(s || "").trim()).filter(Boolean).slice(0, 3)
            : (KILLNET_ENVIRONMENT_NODE.outputs || []).slice(0, 3),
        };
        const decoratedEnv = decorateKillnetBoard(
          [envNode],
          this.planKillnetEdges,
          this.activeCotFinalPlan,
          this.plannerTaskTurns,
        )[0];
        const withoutEnv = rows.filter((node) => node.key !== "environment");
        return decoratedEnv ? [...withoutEnv, decoratedEnv] : withoutEnv;
      },
      killnetSequenceView() {
        return buildKillnetSequence(
          this.activeKillnet,
          this.activeCotFinalPlan,
          this.activeCotRound?.gameTime || this.displayGameTime,
        );
      },
      activeCotFinalPlan() {
        return this.activeCotRound?.loop?.finalPlan || this.planCotFinal || null;
      },
      plannerCotBriefing() {
        // 样例模式：跟随当前时间 tab 的自博弈收敛；实时模式：仅在有 live 收敛时展示。
        if (this.uiMode === "sample" && !this.hideSampleArtifacts) {
          const loop = this.activeCotRound?.loop || PLAN_SELFPLAY_LOOP;
          const plan = loop.finalPlan || {};
          const dims = plan.dims && typeof plan.dims === "object" ? plan.dims : {};
          const digest = String(loop.situationDigest || "").trim()
            || "时间：02:40；出生点：A1；阶段：中期；双矿运转；峡谷口刚开雾；对方蟑螂比重上升。";
          return {
            gameTime: this.activeCotRound?.gameTime || "02:40",
            title: plan.title || COT_ROLE_AGENTS.final,
            subtitle: plan.subtitle || "最终行动方案 · 由多步自博弈收敛",
            dims: {
              strike: String(dims.strike || "").trim()
                || "侧翼可开火但禁止深推；打完前排立即收网复位；二次脉冲看门槛。",
              detect: String(dims.detect || "").trim()
                || "峡谷口与东翼双通道常亮，交战后优先复探绕后与补给抬升。",
              defense: String(dims.defense || "").trim()
                || "高地骨架不撤，回撤通道可视，家门口保持最低警戒并回填。",
              support: String(dims.support || "").trim()
                || "产能服务待命与补损；三矿仅在东翼安全且矿线在控时开启。",
            },
            steps: Array.isArray(plan.steps) && plan.steps.length
              ? plan.steps.map((s) => String(s || "").trim()).filter(Boolean)
              : [
                "南北口与东翼交错开雾，确认队形宽度与补给节奏。",
                "B2 高地占位 + 刺蛇骨架就位，接敌窗只覆盖侧翼开火段。",
                "门槛成立则侧翼打前排，不追残，打完立刻复位。",
                "交战后复探绕后，回填家门口最低防守。",
                "损失过线停扩转补；东翼无渗透且矿线安全才开三矿。",
              ],
            conclusion: String(plan.conclusion || "").trim()
              || "最终方案是一条可执行闭环：信息先手 → 有限打击 → 窗口保护 → 资源再决策。",
            digest,
          };
        }
        const rows = Array.isArray(this.cotRounds) ? this.cotRounds : [];
        const live = [...rows].reverse().find((row) => (
          row
          && row.source === "llm"
          && !row.loading
          && !row.error
          && row.loop?.finalPlan
        ));
        if (!live) return null;
        const plan = live.loop.finalPlan;
        const dims = plan.dims && typeof plan.dims === "object" ? plan.dims : {};
        const filled = Object.values(dims).some((text) => String(text || "").trim());
        if (!filled && !String(plan.conclusion || "").trim()) return null;
        const digest = String(live.loop?.situationDigest || "").trim()
          || String(plan.conclusion || "").trim();
        return {
          gameTime: live.gameTime || this.displayGameTime || "--:--",
          title: plan.title || COT_ROLE_AGENTS.final,
          subtitle: plan.subtitle || "",
          dims,
          steps: Array.isArray(plan.steps) ? plan.steps : [],
          conclusion: String(plan.conclusion || "").trim(),
          digest: digest.startsWith("决策方案的目标")
            ? digest
            : (digest ? `决策方案的目标：${digest}` : ""),
        };
      },
      oodaBoard() {
        const layer = this.activeRightLayer || "overview";
        const steps = OODA_STEPS.map((step) => ({
          key: step.key,
          code: step.code,
          label: step.label,
          hint: step.hint,
        }));
        const order = ["observe", "orient", "decide", "act"];

        let activeKey = "";
        const matched = OODA_STEPS.find((step) => step.layers.includes(layer));
        if (matched) {
          activeKey = matched.key;
        }

        const live = this.liveUiArmed;
        const riskLoading = this.deerflowRiskLoading;
        const game = this.state?.game || {};
        const hasArmy = Number(game.armyCount ?? 0) > 0;

        let blockText = "点选席位，OODA 环跟随点亮。";
        if (!live) {
          blockText = "红方态势感知智能体：对局态势未接入。";
        } else if (riskLoading) {
          blockText = "红方态势认知智能体：意图与风险还在过一遍。";
        } else if (activeKey === "observe") {
          blockText = hasArmy ? "红方态势感知智能体在岗：态势和兵力已刷新。" : "红方态势感知智能体在岗：可见兵力偏少。";
        } else if (activeKey === "orient") {
          blockText = "红方态势认知智能体在岗：意图、风险、杀伤链正在对齐。";
        } else if (activeKey === "decide") {
          blockText = "红方决策方案生成智能体在岗：自博弈思维链和杀伤网正在落卷。";
        } else if (activeKey === "act") {
          blockText =
            layer === "eval"
              ? "红方指挥控制与策略评估智能体在岗：策略评估正在对照这一仗。"
              : "红方指挥控制与策略评估智能体在岗：方案正在往任务和指令拆。";
        } else if (layer === "overview") {
          blockText = "系统总览：点选模块后对应环点亮。";
        }

        const activeIdx = order.indexOf(activeKey);
        const doneKeys = activeIdx > 0 ? order.slice(0, activeIdx) : [];

        return {
          steps,
          activeKey,
          blockText,
          doneKeys,
        };
      },
      rightLayerMeta() {
        if (this.compareMode) {
          return "红蓝总体对比 · 实时盘面";
        }
        if (this.activeRightLayer === "overview") {
          return MODULE_AGENTS.overview.name;
        }
        if (this.activeRightLayer === "observe" || this.activeRightLayer === "awareness") {
          const side = this.observeSide === "blue" ? "蓝方人机" : "红方智能体";
          return `${MODULE_AGENTS.observe.name} · ${side} · ${this.deerflowAwarenessMeta}`;
        }
        if (this.isObserveSenseLayer) {
          const side = this.observeSide === "blue" ? "蓝方人机" : "红方智能体";
          const view = this.observeSenseView;
          const counts = view ? `红 ${view.ownCount} · 蓝 ${view.enemyCount}` : "该维对照";
          return `${this.activeModuleAgent.name} · ${side} · ${counts}`;
        }
        if (this.isObserveEnvironmentLayer) {
          const view = this.observeEnvironmentView;
          const counts = view ? `控点 红 ${view.ownCount} · 蓝 ${view.enemyCount}` : "地形对照";
          return `${this.activeModuleAgent.name} · ${counts}`;
        }
        if (this.isOrientEnvironmentLayer) {
          const flow = this.orientEnvironmentFlow;
          return `${this.activeModuleAgent.name} · ${flow ? "地形利用判断链" : "环境认知"}`;
        }
        if (this.isCotEnvironmentLayer) {
          const round = this.activeEnvCotRound;
          const name = this.activeModuleAgent?.name || MODULE_AGENTS.cotEnvironment.name;
          if (this.envCotSelfplayLoading) return `${name} · 大模型推演中`;
          if (round?.source === "llm") return `${name} · ${round.gameTime || "--:--"} · 实况`;
          if (round?.gameTime) return `${name} · ${round.gameTime} · 环境自博弈`;
          return `${name} · 环境自博弈`;
        }
        if (this.isOrientHubLayer) {
          const board = this.orientBoard;
          return `${this.activeModuleAgent.name} · 意图 ${board.intents.length} · 脆弱 ${board.highVulns.length}`;
        }
        if (this.activeRightLayer === "force") {
          const side = this.observeSide === "blue" ? "蓝方人机" : "红方智能体";
          return `${this.activeModuleAgent.name} · ${side} · ${this.forceDeploymentMeta}`;
        }
        if (this.activeRightLayer === "intent") {
          return `${MODULE_AGENTS.intent.name} · ${this.deerflowIntentMeta}`;
        }
        if (this.activeRightLayer === "risk") {
          return `${MODULE_AGENTS.risk.name} · ${this.deerflowRiskMeta}`;
        }
        if (this.activeRightLayer === "killchain") {
          return `${MODULE_AGENTS.killchain.name} · ${this.killChainMeta}`;
        }
        if (this.activeRightLayer === "critical") {
          return `${MODULE_AGENTS.critical.name} · ${this.criticalTargetLastSeenText} · 显示 ${this.filteredCriticalTargetItems.length}/${this.criticalTargetItems.length} 个`;
        }
        if (this.isCotSenseLayer) {
          const round = this.activeCotRound;
          const name = this.activeModuleAgent?.name || MODULE_AGENTS.cot.name;
          if (this.cotSelfplayLoading) return `${name} · 大模型推演中`;
          if (round?.source === "llm") return `${name} · ${round.gameTime || "--:--"} · 实况`;
          if (round?.gameTime) return `${name} · ${round.gameTime}`;
          return name;
        }
        if (this.activeRightLayer === "killnet") {
          const round = this.activeCotRound;
          if (round?.killnetLoading || this.cotKillnetLoading) return `${MODULE_AGENTS.killnet.name} · 落网生成中`;
          if (round?.gameTime) return `${MODULE_AGENTS.killnet.name} · ${round.gameTime}`;
          return MODULE_AGENTS.killnet.name;
        }
        if (this.activeRightLayer === "plan") {
          return this.rightPanelMeta;
        }
        if (this.activeRightLayer === "eval") {
          if (this.evalInnerTab === "knowledge") {
            const board = this.knowledgeExperienceBoard;
            return `${MODULE_AGENTS.knowledge.name} · ${this.knowledgeRevisionItems.length} 条修订 · 记忆 ${board.memoryCount}`;
          }
          const board = this.tacticsEvaluationBoard;
          return `${MODULE_AGENTS.tactics.name} · 成功 ${board.successCount} · 失败 ${board.failureCount}`;
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
        const side = this.activeDuelLabel;
        const llmAt = this.llmAdviceLastAt ? ` · 大模型意见 ${this.llmAdviceLastAt}` : "";
        if (this.state?.game?.featureSession) {
          return `当前阵营：${side} · 态势会话：${this.state.game.featureSession}${llmAt}`;
        }
        return `当前阵营：${side} · 等待运行时数据${llmAt}`;
      },
    },
    watch: {
      liveUiArmed(next) {
        if (next) {
          if (this.uiMode !== "live") {
            this.onBackendLiveArmed();
          }
          this.refreshAllLlmAdvice({ reason: "live-armed" });
          if (this.isCotSenseLayer || this.activeRightLayer === "killnet") {
            this.startCotAutoCapture();
            if (this.canAutoCot()) {
              this.maybeCaptureCotRound({ reason: "enter", forceIfEmpty: true });
            }
          }
          if (this.isCotEnvironmentLayer) {
            this.startEnvCotAutoCapture();
          }
        } else {
          this.enterCotSnapshot("backend");
          this.stopEnvCotAutoCapture();
        }
      },
      uiThemeMode: {
        immediate: true,
        handler(mode) {
          const theme = mode || "red";
          document.documentElement.dataset.theme = theme;
          if (document.body) {
            document.body.dataset.theme = theme;
          }
        },
      },
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
        if (
          (next === "risk" || next === "intent" || next === "orient" || ORIENT_SENSE_LAYER_CAP[next])
          && !this.isSampleShowcase
          && !this.deerflowRisk.thread_id
          && !this.deerflowRiskLoading
        ) {
          this.refreshDeerflowRisk();
        }
        if (COT_SENSE_LAYER_CAP[next] || next === "killnet") {
          if (this.canAutoCot()) this.startCotAutoCapture();
        }
        if (COT_SENSE_LAYER_CAP[next]) {
          this.planCotDimTab = COT_SENSE_LAYER_CAP[next];
        }
        if (next === "cotEnvironment") {
          this.$nextTick(() => this.startEnvCotAutoCapture());
        } else {
          this.stopEnvCotAutoCapture();
        }
        if ((next === "killchain" || next === "orient" || ORIENT_SENSE_LAYER_CAP[next]) && !this.isSampleShowcase) {
          this.$nextTick(() => this.refreshKillChainLlmPlans());
        }
        if (next === "killnet") {
          this.selectedKillnetDim = "";
          this.$nextTick(() => {
            const round = this.activeCotRound;
            if (round?.loop?.finalPlan && !round.killnet && !this.cotKillnetLoading) {
              this.refreshCotKillnet(round.id);
            }
          });
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
        if ((OBSERVE_SENSE_LAYER_CAP[next] || next === "obsEnvironment") && !this.isSampleShowcase && !this.forceDispositionAdviceIsApi && !this.forceLlmAdviceLoading) {
          this.$nextTick(() => this.refreshForceLlmAdvice());
        }
        if ((ORIENT_SENSE_LAYER_CAP[next] || next === "oriEnvironment") && !this.isSampleShowcase) {
          this.$nextTick(() => this.startOrientQaPolling());
        } else if (!(ORIENT_SENSE_LAYER_CAP[next] || next === "oriEnvironment")) {
          this.stopOrientQaPolling();
        } else if (this.isSampleShowcase) {
          // 样例：切认知维时只切换固化包，不拉 API
          const dim = ORIENT_SENSE_LAYER_CAP[next] || (next === "oriEnvironment" ? "environment" : "strike");
          const pack = this.orientQaByDim?.[dim];
          if (pack) {
            this.orientQa = {
              question: "",
              loading: false,
              errorText: "",
              loadedAt: pack.loadedAt || "08:41:10",
              source: pack.source || "sample-showcase",
              dimension: pack.dimension || dim,
              messages: Array.isArray(pack.messages) ? pack.messages.map((m) => ({ ...m })) : [],
              steps: Array.isArray(pack.steps) ? pack.steps.map((s) => ({ ...s })) : [],
              conclusion: pack.conclusion || "",
            };
          }
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
      selectDuelSide(side) {
        if (!STATE_URLS[side]) {
          return;
        }
        this.compareMode = false;
        this.activeKnowledgeRoundId = "r1";
        this.selectedKnowledgeMemoryId = "";
        this.knowledgeMemoryFilter = "all";
        this.knowledgeChainTab = "strike";
        if (side === this.activeDuelSide) {
          return;
        }
        this.activeDuelSide = side;
        const selectedState = this.duelStates[side];
        if (selectedState?.updatedAt) {
          this.state = prepareDisplayState(selectedState, this.liveUiArmed);
        }
        // 切换阵营时重置会话态，避免红蓝方案/指令串台
        this.plannerHistoryReady = false;
        this.plannerHistory = [];
        this.plannerLiveTurn = null;
        this.plannerSeenTurnKeys = Object.create(null);
        this.executedCommands = [];
        this.commandTrackingReady = false;
        this.deerflowRisk = {
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
        };
        this.deerflowHistory = { intent: [], risk: [] };
        this.forceLlmAdvice = {
          ok: false,
          sections: [],
          errorText: "",
          answerRaw: "",
          traces: [],
          loadedAt: "",
          source: "",
        };
        this.strikeTargetAdvice = {
          ok: false,
          advice: "",
          errorText: "",
          loadedAt: "",
          source: "",
        };
        this.killChainLlmPlans = {
          ok: false,
          plans: [],
          errorText: "",
          answerRaw: "",
          traces: [],
          loadedAt: "",
          source: "",
          chainSignature: "",
        };
        // 自博弈问答基于红蓝双侧态势，切换阵营时保留对话记录
        this.rememberPlannerTurnsFromState();
        this.$nextTick(() => this.refreshAllLlmAdvice({ reason: "side-switch" }));
      },

      selectKnowledgeRound(roundId) {
        if (!roundId) return;
        this.activeKnowledgeRoundId = roundId;
      },
      openArchAgent(action) {
        if (!action) return;
        // 态势感知固定红方视角，不再切入蓝方人机
        if (action.observeSide) this.observeSide = "red";
        if (action.plannerTab) this.activePlannerTab = action.plannerTab;
        if (action.evalTab) this.evalInnerTab = action.evalTab;
        if (action.layer) this.selectOodaRingTab(action.layer);
        else if (action.ring) this.enterOodaRing(action.ring);
        this.$nextTick(() => this.scrollActAnchor(action));
      },
      scrollActAnchor(action) {
        const id = action?.evalTab === "knowledge"
          ? "act-eval-knowledge"
          : action?.evalTab === "tactics"
            ? "act-eval-tactics"
            : action?.plannerTab
              ? `act-plan-${action.plannerTab}`
              : "";
        if (!id) return;
        const node = document.getElementById(id);
        if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
      },
      enterOodaRing(ringKey) {
        const ring = OODA_RINGS.find((item) => item.key === ringKey);
        if (!ring) return;
        this.activeRightLayer = ring.defaultLayer;
        if (ring.defaultLayer === "eval") {
          this.evalInnerTab = "tactics";
        }
        if (ring.defaultLayer === "plan") {
          this.ensurePlanPipelineTab();
        }
      },
      selectOodaRingTab(layerKey) {
        if (!layerKey) return;
        if (layerKey === "cot") layerKey = "cotStrike";
        this.activeRightLayer = layerKey;
        if (layerKey === "eval") {
          this.evalInnerTab = this.evalInnerTab || "tactics";
        }
        if (layerKey === "plan") {
          this.ensurePlanPipelineTab();
        }
      },
      ensurePlanPipelineTab() {
        const keys = (this.planPipelineTabs || []).map((tab) => tab.key);
        if (!keys.includes(this.activePlannerTab)) {
          this.activePlannerTab = keys[0] || "bus";
        }
      },
      setEvalInnerTab(tabKey) {
        this.evalInnerTab = tabKey === "knowledge" ? "knowledge" : "tactics";
      },
      withEvalDraft(scope, id, item) {
        const draft = this.evalDrafts[`${scope}:${id}`] || {};
        return {
          ...item,
          uid: id,
          verdict: draft.verdict || "",
          amendNote: draft.note || "",
        };
      },
      acceptEvalItem(scope, id) {
        const key = `${scope}:${id}`;
        const cur = this.evalDrafts[key] || {};
        this.evalDrafts = {
          ...this.evalDrafts,
          [key]: { ...cur, verdict: "allow", note: "" },
        };
      },
      rejectEvalItem(scope, id) {
        const key = `${scope}:${id}`;
        const cur = this.evalDrafts[key] || {};
        this.evalDrafts = {
          ...this.evalDrafts,
          [key]: { ...cur, verdict: "reject", note: "" },
        };
      },
      openEvalAmendDialog(scope, id, title) {
        const cur = this.evalDrafts[`${scope}:${id}`] || {};
        this.evalAmendDialog = {
          open: true,
          scope,
          id,
          title: title || "",
          note: cur.note || "",
        };
      },
      confirmEvalAmend() {
        const dialog = this.evalAmendDialog;
        if (!dialog?.open || !dialog.id) return;
        const key = `${dialog.scope}:${dialog.id}`;
        const cur = this.evalDrafts[key] || {};
        this.evalDrafts = {
          ...this.evalDrafts,
          [key]: { ...cur, verdict: "amend", note: String(dialog.note || "").trim() },
        };
        this.evalAmendDialog = { open: false, scope: "", id: "", title: "", note: "" };
      },
      cancelEvalAmend() {
        this.evalAmendDialog = { open: false, scope: "", id: "", title: "", note: "" };
      },
      planDimLabel(key) {
        const hit = (this.knowledgeChainDims || []).find((d) => d.key === key);
        return hit ? hit.label : key;
      },
      setPlanCotDimTab(dimKey) {
        if (!dimKey) return;
        this.planCotDimTab = dimKey;
      },
      selectCotRound(roundId) {
        if (!roundId || roundId === this.activeCotRoundId) return;
        const row = (this.cotRounds || []).find((item) => item.id === roundId);
        if (!row) return;
        this.activeCotRoundId = roundId;
        this.selectedKillnetDim = "";
        if (row.killnet) this.planKillnet = row.killnet;
      },
      selectEnvCotRound(roundId) {
        if (!roundId || roundId === this.activeEnvCotRoundId) return;
        const row = (this.envCotRounds || []).find((item) => item.id === roundId);
        if (!row) return;
        this.activeEnvCotRoundId = roundId;
      },
      envCotPackOf(turn) {
        return envCotPack(turn);
      },
      patchEnvCotRound(roundId, patch) {
        if (!roundId) return;
        this.envCotRounds = (this.envCotRounds || []).map((row) => (
          row.id === roundId ? { ...row, ...patch } : row
        ));
      },
      lastLiveEnvCotRound() {
        const rows = Array.isArray(this.envCotRounds) ? this.envCotRounds : [];
        for (let i = rows.length - 1; i >= 0; i -= 1) {
          const row = rows[i];
          if (row && row.source === "llm" && !row.loading && row.loop) return row;
        }
        return null;
      },
      maybeCaptureEnvCotRound({ reason = "enter", forceIfEmpty = false, force = false } = {}) {
        if (this.envCotSelfplayLoading) return;
        if (!force && !this.canAutoCot() && reason === "timer") {
          this.stopEnvCotAutoCapture();
          return;
        }
        if (force) {
          this.refreshEnvCotSelfplay({ replaceLast: false });
          return;
        }
        const hasLive = Boolean(this.lastLiveEnvCotRound());
        if (!hasLive) {
          if (forceIfEmpty || reason === "enter" || reason === "manual") {
            this.refreshEnvCotSelfplay({ replaceLast: false });
          }
          return;
        }
        if (reason === "enter") {
          this.scheduleNextEnvCotCapture();
          return;
        }
        if (reason === "timer") {
          this.refreshEnvCotSelfplay({ replaceLast: false });
        }
      },
      startEnvCotAutoCapture() {
        if (!this.isCotEnvironmentLayer) return;
        if (this.uiMode !== "live") {
          this.stopEnvCotAutoCapture();
          return;
        }
        if (!this.canAutoCot()) {
          if (this.envCotAutoStarted || this.lastLiveEnvCotRound()) {
            this.stopEnvCotAutoCapture();
          }
          return;
        }
        if (this.envCotAutoStarted) {
          if (!this.envCotSelfplayLoading && !this.envCotAutoTimer) {
            this.scheduleNextEnvCotCapture();
          }
          return;
        }
        this.envCotAutoStarted = true;
        if (!this.envCotSelfplayLoading && !this.lastLiveEnvCotRound()) {
          this.maybeCaptureEnvCotRound({ reason: "enter", forceIfEmpty: true });
        }
        if (!this.envCotSelfplayLoading && !this.envCotAutoTimer) {
          this.scheduleNextEnvCotCapture();
        }
      },
      stopEnvCotAutoCapture() {
        this.envCotAutoStarted = false;
        if (this.envCotAutoTimer) {
          window.clearTimeout(this.envCotAutoTimer);
          this.envCotAutoTimer = null;
        }
      },
      scheduleNextEnvCotCapture() {
        if (this.envCotAutoTimer) {
          window.clearTimeout(this.envCotAutoTimer);
          this.envCotAutoTimer = null;
        }
        if (!this.envCotAutoStarted || !this.isCotEnvironmentLayer) return;
        this.envCotAutoTimer = window.setTimeout(async () => {
          this.envCotAutoTimer = null;
          if (!this.envCotAutoStarted || !this.isCotEnvironmentLayer) return;
          const healthy = await this.probeAskApiHealth();
          if (!healthy || !this.canAutoCot()) {
            this.stopEnvCotAutoCapture();
            return;
          }
          this.maybeCaptureEnvCotRound({ reason: "timer" });
          if (!this.envCotSelfplayLoading && this.canAutoCot()) {
            this.scheduleNextEnvCotCapture();
          }
        }, COT_AUTO_GAP_MS);
      },
      dropEmptyEnvCotRound(roundId) {
        const row = (this.envCotRounds || []).find((item) => item.id === roundId);
        if (!row || row.source === "demo") return;
        if (row.loop && (row.loop.thesis || row.loop.finalPlan?.conclusion)) return;
        this.envCotRounds = (this.envCotRounds || []).filter((item) => item.id !== roundId);
        if (this.activeEnvCotRoundId === roundId) {
          const last = this.envCotRounds[this.envCotRounds.length - 1];
          this.activeEnvCotRoundId = last ? last.id : "env-cot-demo";
        }
      },
      async refreshEnvCotSelfplay({ replaceLast = false } = {}) {
        if (this.envCotSelfplayLoading) return;
        const clock = usableGameClock(this.displayGameTime);
        const seconds = clock ? parseGameClockToSeconds(clock) : 0;
        const previous = this.lastLiveEnvCotRound();
        const previousConclusion = String(
          previous?.loop?.finalPlan?.conclusion || "",
        ).trim();
        this.envCotRoundSeq = Number(this.envCotRoundSeq || 1) + 1;
        const pendingId = `env-cot-${this.envCotRoundSeq}-${Date.now()}`;
        const boundId = replaceLast && previous && previous.id !== "env-cot-demo" ? previous.id : pendingId;
        const pending = {
          id: boundId,
          gameTime: clock || "--:--",
          gameSeconds: seconds,
          generatedAt: "",
          generatedAtMs: 0,
          source: "llm",
          loading: true,
          error: "",
          loop: snapshotEnvCotLoop(normalizeEnvCotLoop({})),
        };
        this.envCotSelfplayLoading = true;
        this.envCotSelfplayError = "";
        const currentId = this.activeEnvCotRoundId;
        const followLive = !currentId
          || currentId === "env-cot-demo"
          || currentId === boundId
          || Boolean(previous && currentId === previous.id);
        if (boundId !== pendingId) {
          this.patchEnvCotRound(boundId, pending);
        } else {
          const nextRows = (this.envCotRounds || []).filter((row) => row.id !== "env-cot-demo");
          nextRows.push(pending);
          this.envCotRounds = nextRows.slice(-COT_ROUND_CAP);
        }
        if (followLive) this.activeEnvCotRoundId = boundId;
        let unreachable = false;
        try {
          const resp = await fetch(COT_ENV_SELFPLAY_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              timeout_sec: 80,
              game_time: clock,
              previous_conclusion: previousConclusion,
              ...this.llmAdviceRequestBase(),
            }),
          });
          if (!resp.ok) {
            if (resp.status === 502 || resp.status === 503 || resp.status === 504) {
              unreachable = true;
            }
            throw new Error(`env cot api (${resp.status})`);
          }
          this.askApiReachable = true;
          const payload = await resp.json();
          const loop = payload?.loop && typeof payload.loop === "object"
            ? normalizeEnvCotLoop(payload.loop)
            : null;
          const ok = Boolean(payload?.ok) && Boolean(loop);
          const doneAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
          const liveClock = usableGameClock(payload?.game_time)
            || usableGameClock(this.displayGameTime)
            || clock
            || "--:--";
          this.patchEnvCotRound(boundId, {
            loading: false,
            error: ok ? "" : String(payload?.error || "生成失败"),
            source: "llm",
            gameTime: liveClock,
            gameSeconds: parseGameClockToSeconds(liveClock) || seconds,
            generatedAt: doneAt,
            generatedAtMs: Date.now(),
            loop: ok ? loop : pending.loop,
          });
          this.envCotSelfplayError = ok ? "" : String(payload?.error || "生成失败");
          if (ok) {
            const cotId = this.activeCotRoundId;
            this.$nextTick(() => {
              if (cotId && cotId !== "cot-demo") this.refreshCotKillnet(cotId);
            });
          }
        } catch (e) {
          unreachable = unreachable || isAskApiUnreachableError(e);
          const message = unreachable
            ? "后端已停下，当前保留最近一轮环境快照。"
            : `调用失败：${e?.message || e}`;
          this.envCotSelfplayError = message;
          this.patchEnvCotRound(boundId, { loading: false, error: message });
          if (unreachable) {
            this.askApiReachable = false;
            this.dropEmptyEnvCotRound(boundId);
          }
        } finally {
          this.envCotSelfplayLoading = false;
          if (this.envCotAutoStarted && this.isCotEnvironmentLayer && this.canAutoCot()) {
            this.scheduleNextEnvCotCapture();
          }
        }
      },
      selectKillnetDim(dimKey) {
        const key = String(dimKey || "").trim();
        if (!key) return;
        this.selectedKillnetDim = this.selectedKillnetDim === key ? "" : key;
      },
      isKillnetSeqLit(step) {
        const selected = this.selectedKillnetDim;
        if (!selected || !step) return false;
        if (step.dim === selected) return true;
        return Array.isArray(step.also) && step.also.includes(selected);
      },
      plannerScriptVisible(item) {
        if (this.plannerCotBriefing) return false;
        const text = String(item?.prompt || "").trim();
        return Boolean(text) && !isMojibakePrompt(text);
      },
      selectPlannerArchive(item) {
        if (!item?.key) return;
        this.plannerPinnedKey = this.plannerPinnedKey === item.key ? "" : item.key;
      },
      plannerDossierOf(item) {
        if (!item) {
          return { structured: false, sections: [], intent: null, leftover: "", clipped: false, typing: false };
        }
        const typing = this.replyIsTypingForItem(item);
        const raw = typing ? this.replyDisplayText(item) : this.replyTextForItem(item);
        const dossier = composePlannerDossier(raw);
        const expanded = this.isReplyExpanded(item);
        const cap = 5;
        if (!typing && !expanded && dossier.sections.length > cap) {
          return { ...dossier, sections: dossier.sections.slice(0, cap), clipped: true, typing };
        }
        return { ...dossier, clipped: false, typing };
      },
      planIntentLabel,
      planIntentPct(intent) {
        const n = Number(intent?.confidence);
        if (!Number.isFinite(n)) return 0;
        return Math.max(0, Math.min(100, Math.round(n <= 1 ? n * 100 : n)));
      },
      cotRoundTickTime(round) {
        if (!round) return "--:--";
        if (round.source === "demo") return round.gameTime || "08:42";
        const clock = usableGameClock(round.gameTime);
        if (clock) return clock;
        if (Number(round.gameSeconds) > 0) return formatGameClockFromSeconds(round.gameSeconds);
        return round.loading ? "生成中" : "--:--";
      },
      cotDimOf(turn, dimKey) {
        return turn?.dims?.[dimKey] || emptyCotDim();
      },
      cotThoughtsOf(turn, dimKey) {
        const dim = this.cotDimOf(turn, dimKey);
        if (Array.isArray(dim.thoughts) && dim.thoughts.length) return dim.thoughts;
        return splitCotThoughts(dim.detail);
      },
      syncLoadingCotRoundClock() {
        const clock = usableGameClock(this.displayGameTime);
        if (!clock) return;
        const sec = parseGameClockToSeconds(clock);
        let changed = false;
        const next = (this.cotRounds || []).map((row) => {
          if (!row?.loading) return row;
          const prev = Number(row.gameSeconds) || 0;
          if (sec < prev) return row;
          if (row.gameTime === clock && row.gameSeconds === sec) return row;
          changed = true;
          return { ...row, gameTime: clock, gameSeconds: sec };
        });
        if (changed) this.cotRounds = next;
      },
      lastLiveCotRound() {
        const rows = Array.isArray(this.cotRounds) ? this.cotRounds : [];
        for (let i = rows.length - 1; i >= 0; i -= 1) {
          if (rows[i]?.source === "llm" || rows[i]?.loading) return rows[i];
        }
        return null;
      },
      isLiveStateStale() {
        if (!this.cotLiveUpdatedAtMs) return false;
        return Date.now() - this.cotLiveUpdatedAtMs > COT_STATE_STALE_MS;
      },
      canAutoCot() {
        if (this.uiMode !== "live") return false;
        if (!this.liveUiArmed) return false;
        if (this.state?.status !== "running") return false;
        if (this.askApiReachable === false) return false;
        if (this.isLiveStateStale()) return false;
        return true;
      },
      enterCotSnapshot(reason = "backend") {
        this.cotSnapshotReason = reason || "backend";
        this.stopCotAutoCapture();
        this.stopEnvCotAutoCapture();
      },
      async probeAskApiHealth() {
        const now = Date.now();
        if (now - (this.askApiProbeAtMs || 0) < 1500 && this.askApiReachable != null) {
          return this.askApiReachable !== false;
        }
        this.askApiProbeAtMs = now;
        const ctrl = new AbortController();
        const timer = window.setTimeout(() => ctrl.abort(), 2000);
        try {
          const resp = await fetch(ASK_API_HEALTH_URL, { cache: "no-store", signal: ctrl.signal });
          this.askApiReachable = Boolean(resp.ok);
        } catch {
          this.askApiReachable = false;
        } finally {
          window.clearTimeout(timer);
        }
        return this.askApiReachable !== false;
      },
      dropEmptyCotRound(roundId) {
        if (!roundId) return;
        const row = (this.cotRounds || []).find((item) => item.id === roundId);
        const conclusion = String(row?.loop?.finalPlan?.conclusion || "").trim();
        const thesis = String(row?.loop?.thesis || "").trim();
        if (!row || conclusion || thesis) return;
        this.cotRounds = (this.cotRounds || []).filter((item) => item.id !== roundId);
        if (this.activeCotRoundId === roundId) {
          const last = this.cotRounds[this.cotRounds.length - 1];
          this.activeCotRoundId = last?.id || "cot-demo";
        }
      },
      startCotAutoCapture() {
        if (this.uiMode !== "live") {
          this.stopCotAutoCapture();
          return;
        }
        const onDecide = this.isCotSenseLayer || this.activeRightLayer === "killnet";
        if (!onDecide && !this.cotAutoStarted) return;
        if (!this.canAutoCot()) {
          if (this.cotAutoStarted || this.lastLiveCotRound()) {
            this.enterCotSnapshot("backend");
          } else {
            this.stopCotAutoCapture();
          }
          return;
        }
        this.cotSnapshotReason = "";
        if (this.cotAutoStarted) {
          if (!this.cotSelfplayLoading && !this.cotAutoTimer) {
            this.scheduleNextCotCapture();
          }
          return;
        }
        this.cotAutoStarted = true;
        if (!this.cotSelfplayLoading && !this.lastLiveCotRound()) {
          this.maybeCaptureCotRound({ reason: "enter", forceIfEmpty: true });
        }
        if (!this.cotSelfplayLoading && !this.cotAutoTimer) {
          this.scheduleNextCotCapture();
        }
      },
      syncCotAutoFromBackend(isRunning) {
        const liveNow = Boolean(this.uiMode === "live" && this.liveUiArmed && isRunning && !this.isLiveStateStale());
        if (!liveNow) {
          if (this.cotAutoStarted || this.lastLiveCotRound()) {
            this.enterCotSnapshot("backend");
          } else {
            this.stopCotAutoCapture();
          }
          if (this.envCotAutoStarted || this.lastLiveEnvCotRound()) {
            this.stopEnvCotAutoCapture();
          }
          return;
        }
        if (this.askApiReachable === false) {
          const now = Date.now();
          if (now - (this.askApiProbeAtMs || 0) >= 5000) {
            this.probeAskApiHealth().then((ok) => {
              if (ok) {
                this.startCotAutoCapture();
                if (this.isCotEnvironmentLayer) this.startEnvCotAutoCapture();
              }
            });
          }
          return;
        }
        this.startCotAutoCapture();
        if (this.isCotEnvironmentLayer) this.startEnvCotAutoCapture();
      },
      stopCotAutoCapture() {
        this.cotAutoStarted = false;
        if (this.cotAutoTimer) {
          window.clearTimeout(this.cotAutoTimer);
          this.cotAutoTimer = null;
        }
      },
      scheduleNextCotCapture() {
        if (this.cotAutoTimer) {
          window.clearTimeout(this.cotAutoTimer);
          this.cotAutoTimer = null;
        }
        if (!this.cotAutoStarted || !this.canAutoCot()) return;
        this.cotAutoTimer = window.setTimeout(async () => {
          this.cotAutoTimer = null;
          if (!this.cotAutoStarted) return;
          const healthy = await this.probeAskApiHealth();
          if (!healthy || !this.canAutoCot()) {
            this.enterCotSnapshot("backend");
            return;
          }
          this.maybeCaptureCotRound({ reason: "timer" });
          if (!this.cotSelfplayLoading && this.canAutoCot()) {
            this.scheduleNextCotCapture();
          }
        }, COT_AUTO_GAP_MS);
      },
      maybeCaptureCotRound({ reason = "timer", forceIfEmpty = false, force = false } = {}) {
        if (this.cotSelfplayLoading) return;
        if (!force && !this.canAutoCot()) {
          this.enterCotSnapshot("backend");
          return;
        }
        const last = this.lastLiveCotRound();
        const hasLive = Boolean(last);
        if (force) {
          this.refreshCotSelfplay({ replaceLast: false, manual: true });
          return;
        }
        if (!hasLive) {
          if (forceIfEmpty || reason === "enter") {
            this.refreshCotSelfplay({ replaceLast: false });
          }
          return;
        }
        if (reason === "enter") {
          this.scheduleNextCotCapture();
          return;
        }
        this.refreshCotSelfplay({ replaceLast: false });
      },
      patchCotRound(roundId, patch) {
        if (!roundId) return;
        this.cotRounds = (this.cotRounds || []).map((row) => (
          row.id === roundId ? { ...row, ...patch } : row
        ));
      },
      async refreshCotKillnet(roundId) {
        const targetId = roundId || this.activeCotRoundId;
        const round = (this.cotRounds || []).find((row) => row.id === targetId);
        const finalPlan = round?.loop?.finalPlan;
        if (!round || !finalPlan) return;
        const envPlan = this.envCotFinal || null;
        this.cotKillnetLoading = true;
        this.cotKillnetError = "";
        this.patchCotRound(targetId, { killnetLoading: true, killnetError: "" });
        try {
          const resp = await fetch(COT_KILLNET_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              timeout_sec: 70,
              game_time: round.gameTime || this.displayGameTime,
              situation_digest: String(round.loop?.situationDigest || ""),
              final_plan: finalPlan,
              environment_plan: envPlan || {},
              ...this.llmAdviceRequestBase(),
            }),
          });
          if (!resp.ok) {
            throw new Error(`killnet api (${resp.status})`);
          }
          const payload = await resp.json();
          const netRaw = payload?.killnet && typeof payload.killnet === "object" ? payload.killnet : null;
          const hasNodes = Array.isArray(netRaw?.nodes) && netRaw.nodes.length > 0;
          const net = hasNodes ? netRaw : killnetFromFinalPlan(finalPlan, round.gameTime);
          if (net) {
            this.planKillnet = net;
            this.patchCotRound(targetId, {
              killnet: net,
              killnetLoading: false,
              killnetError: String(payload?.error || ""),
            });
            this.cotKillnetError = String(payload?.error || "");
          } else {
            throw new Error(payload?.error || "未返回杀伤网");
          }
        } catch (e) {
          const message = `杀伤网生成失败：${e?.message || e}`;
          this.cotKillnetError = message;
          this.patchCotRound(targetId, { killnetLoading: false, killnetError: message });
        } finally {
          this.cotKillnetLoading = false;
        }
      },
      async refreshCotSelfplay({ replaceLast = false, manual = false } = {}) {
        if (this.cotSelfplayLoading) return;
        if (!manual && !this.canAutoCot()) {
          this.enterCotSnapshot("backend");
          return;
        }
        const clock = usableGameClock(this.displayGameTime);
        const seconds = clock ? parseGameClockToSeconds(clock) : 0;
        const previous = this.lastLiveCotRound();
        const previousConclusion = String(
          previous?.loop?.finalPlan?.conclusion || "",
        ).trim();
        this.cotRoundSeq = Number(this.cotRoundSeq || 1) + 1;
        const pendingId = `cot-${this.cotRoundSeq}-${Date.now()}`;
        const boundId = replaceLast && previous && previous.id !== "cot-demo" ? previous.id : pendingId;
        const pending = {
          id: boundId,
          gameTime: clock || "--:--",
          gameSeconds: seconds,
          generatedAt: "",
          generatedAtMs: 0,
          source: "llm",
          loading: true,
          error: "",
          loop: snapshotCotLoop(normalizeCotLoop({})),
          killnet: null,
          killnetLoading: false,
          killnetError: "",
        };
        this.lastCotRequestAtMs = Date.now();
        this.cotSelfplayLoading = true;
        this.cotSelfplayError = "";
        const currentId = this.activeCotRoundId;
        const followLive = !currentId
          || currentId === "cot-demo"
          || currentId === boundId
          || Boolean(previous && currentId === previous.id);
        if (boundId !== pendingId) {
          this.patchCotRound(boundId, pending);
        } else {
          const nextRows = (this.cotRounds || []).filter((row) => row.id !== "cot-demo");
          nextRows.push(pending);
          this.cotRounds = nextRows.slice(-COT_ROUND_CAP);
        }
        if (followLive) this.activeCotRoundId = boundId;
        let unreachable = false;
        try {
          const resp = await fetch(COT_SELFPLAY_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              timeout_sec: 80,
              game_time: clock,
              previous_conclusion: previousConclusion,
              ...this.llmAdviceRequestBase(),
            }),
          });
          if (!resp.ok) {
            if (resp.status === 502 || resp.status === 503 || resp.status === 504) {
              unreachable = true;
            }
            throw new Error(`cot api (${resp.status})`);
          }
          this.askApiReachable = true;
          const payload = await resp.json();
          const loop = payload?.loop && typeof payload.loop === "object"
            ? normalizeCotLoop(payload.loop)
            : null;
          const ok = Boolean(payload?.ok) && Boolean(loop);
          const doneAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
          const liveClock = usableGameClock(payload?.game_time)
            || usableGameClock(this.displayGameTime)
            || clock
            || "--:--";
          const seededNet = ok ? killnetFromFinalPlan(loop.finalPlan, liveClock) : null;
          this.patchCotRound(boundId, {
            loading: false,
            error: ok ? "" : String(payload?.error || "生成失败"),
            source: ok ? "llm" : "llm",
            gameTime: liveClock,
            gameSeconds: parseGameClockToSeconds(liveClock) || seconds,
            generatedAt: doneAt,
            generatedAtMs: Date.now(),
            loop: ok ? loop : pending.loop,
            killnet: seededNet || pending.killnet,
            killnetLoading: Boolean(ok),
            killnetError: "",
          });
          this.cotSelfplayError = ok ? "" : String(payload?.error || "生成失败");
          if (ok) {
            this.cotSnapshotReason = "";
            this.planKillnet = seededNet;
            this.$nextTick(() => this.refreshCotKillnet(boundId));
          }
        } catch (e) {
          unreachable = unreachable || isAskApiUnreachableError(e);
          const message = unreachable
            ? "后端已停下，当前保留最近一轮快照。"
            : `调用失败：${e?.message || e}`;
          this.cotSelfplayError = message;
          this.patchCotRound(boundId, { loading: false, error: message });
          if (unreachable) {
            this.askApiReachable = false;
            this.dropEmptyCotRound(boundId);
            this.enterCotSnapshot("backend");
          }
        } finally {
          this.cotSelfplayLoading = false;
          if (manual && this.canAutoCot()) this.startCotAutoCapture();
          if (this.canAutoCot()) this.scheduleNextCotCapture();
        }
      },
      setKnowledgeChainTab(dimKey) {
        if (!dimKey) return;
        this.knowledgeChainTab = dimKey;
        this.$nextTick(() => {
          const scrollPaneToDim = (paneEl, targetId) => {
            if (!paneEl) return;
            const target = document.getElementById(targetId);
            if (!target || !paneEl.contains(target)) return;
            const paneRect = paneEl.getBoundingClientRect();
            const targetRect = target.getBoundingClientRect();
            const nextTop = paneEl.scrollTop + (targetRect.top - paneRect.top) - 8;
            paneEl.scrollTo({ top: Math.max(0, nextTop), behavior: "smooth" });
          };
          scrollPaneToDim(this.$refs.kbPaneOurs, "kb-our-" + dimKey);
          scrollPaneToDim(this.$refs.kbPaneEnemy, "kb-enemy-" + dimKey);
          scrollPaneToDim(this.$refs.kbPaneReflect, "kb-reflect-" + dimKey);
        });
      },
      startKnowledgeLiveRotation() {
        this.stopKnowledgeLiveRotation();
        knowledgeLiveVariantIndex = this.knowledgeLiveVariantIndex || 0;
        this.knowledgeLiveCountdown = Math.round(KNOWLEDGE_LIVE_TICK_MS / 1000);
        this.knowledgeLiveTimer = window.setInterval(() => {
          const nextCountdown = this.knowledgeLiveCountdown - 1;
          if (nextCountdown > 0) {
            this.knowledgeLiveCountdown = nextCountdown;
            return;
          }
          const next =
            (this.knowledgeLiveVariantIndex + 1) % KNOWLEDGE_LIVE_VARIANT_COUNT;
          knowledgeLiveVariantIndex = next;
          this.knowledgeLiveVariantIndex = next;
          this.knowledgeLiveCountdown = Math.round(KNOWLEDGE_LIVE_TICK_MS / 1000);
          this.knowledgeLiveFlash = true;
          if (this.knowledgeLiveFlashTimer) {
            window.clearTimeout(this.knowledgeLiveFlashTimer);
          }
          this.knowledgeLiveFlashTimer = window.setTimeout(() => {
            this.knowledgeLiveFlash = false;
            this.knowledgeLiveFlashTimer = null;
          }, 650);
        }, 1000);
      },
      stopKnowledgeLiveRotation() {
        if (this.knowledgeLiveTimer) {
          window.clearInterval(this.knowledgeLiveTimer);
          this.knowledgeLiveTimer = null;
        }
        if (this.knowledgeLiveFlashTimer) {
          window.clearTimeout(this.knowledgeLiveFlashTimer);
          this.knowledgeLiveFlashTimer = null;
        }
        this.knowledgeLiveFlash = false;
      },
      selectKnowledgeMemory(memory) {
        if (!memory?.id) return;
        this.selectedKnowledgeMemoryId = memory.id;
        if (memory.linkedRoundId) {
          this.activeKnowledgeRoundId = memory.linkedRoundId;
        }
      },
      setKnowledgeMemoryFilter(filter) {
        this.knowledgeMemoryFilter = filter || "all";
      },

      enterCompareMode() {
        this.compareMode = true;
        if (this.activeRightLayer === "overview") {
          this.activeRightLayer = "observe";
        }
      },
      duelAgentSummary(side) {
        const duelState = this.duelStates[side] || createEmptyState();
        const game = duelState.game || {};
        const message = duelState.lastMessage || duelState.messages?.latest || {};
        return {
          name: game.agentName || (side === "blue" ? "蓝方 SwarmBrain" : "红方 SwarmBrain"),
          status: duelState.status || "idle",
          time: game.timeFormatted || "--:--",
          supply: `${Number(game.supplyUsed || 0)}/${Number(game.supplyCap || 0)}`,
          army: Number(game.armyCount || 0),
          workers: Number(game.workerCount || 0),
          message: message.title || "等待决策输出",
          channel: message.channel || "",
        };
      },
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
        // 按产品要求：不累积本地化生成记忆，仅保留最新一轮在 deerflowRisk 中
        this.deerflowHistory = { intent: [], risk: [] };
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
      llmAdviceRequestBase() {
        return { side: this.activeDuelSide || "red" };
      },
      async refreshAllLlmAdvice({ reason = "timer" } = {}) {
        // 样例模式：不刷实时大模型，避免把固化展示冲成空/失败态
        if (this.isSampleShowcase) {
          return;
        }
        // 末局/回放也可刷新意图风险；仅在完全未武装且是定时器时跳过其它意见模块
        const canRunHeavy = Boolean(this.liveUiArmed || this.state?.updatedAt);
        if (!canRunHeavy && reason === "timer") {
          return;
        }
        this.llmAdviceTick += 1;
        this.llmAdviceLastAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
        // 先刷新意图/风险，避免与其它大模型请求并发抢额度导致空回复
        await this.refreshDeerflowRisk();
        if (!this.liveUiArmed && reason === "timer") {
          return;
        }
        await Promise.allSettled([
          this.refreshForceLlmAdvice(),
          this.refreshStrikeTargetAdvice(),
          this.refreshKillChainLlmPlans({ force: true }),
        ]);
        // 感知五维已由 force/llm-advice 一次返回；认知五维在进入认知环时批量预取
        if (this.isOrientSenseLayer || this.isOrientEnvironmentLayer || this.activeRightLayer === "orient") {
          this.refreshOrientQaAllDimensions();
        }
      },
      startLlmAdvicePolling() {
        this.stopLlmAdvicePolling();
        this.refreshAllLlmAdvice({ reason: "boot" });
        this.llmAdviceTimer = window.setInterval(() => {
          this.refreshAllLlmAdvice({ reason: "timer" });
        }, this.llmAdviceRefreshMs || LLM_ADVICE_REFRESH_MS);
      },
      stopLlmAdvicePolling() {
        if (this.llmAdviceTimer) {
          window.clearInterval(this.llmAdviceTimer);
          this.llmAdviceTimer = null;
        }
      },
      startOrientQaPolling() {
        this.stopOrientQaPolling();
        const dim = this.orientSenseCapabilityKey || "strike";
        if (this.orientQa.dimension !== dim) {
          this.orientQaReqSeq += 1;
          const cached = this.orientQaByDim?.[dim];
          this.orientQa = cached
            ? {
                question: "",
                loading: false,
                errorText: "",
                loadedAt: cached.loadedAt || "",
                source: cached.source || "",
                dimension: dim,
                messages: Array.isArray(cached.messages) ? cached.messages.slice() : [],
                steps: Array.isArray(cached.steps) ? cached.steps.slice() : [],
                conclusion: cached.conclusion || "",
              }
            : {
                question: "",
                loading: false,
                errorText: "",
                loadedAt: "",
                source: "",
                dimension: dim,
                messages: [],
                steps: [],
                conclusion: "",
              };
        }
        this.refreshOrientQaBriefing({ force: true });
        this.orientQaTimer = window.setInterval(() => {
          this.refreshOrientQaBriefing({ force: false });
        }, ORIENT_QA_REFRESH_MS);
      },
      stopOrientQaPolling() {
        if (this.orientQaTimer) {
          window.clearInterval(this.orientQaTimer);
          this.orientQaTimer = null;
        }
      },
      scrollOrientQaToBottom() {
        const el = this.$refs?.orientQaDialog;
        if (!el || typeof el.scrollTo !== "function") return;
        el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      },
      async requestOrientUnderstand({ question = "", kind = "briefing", dimension = "" } = {}) {
        if (this.isSampleShowcase) return null;
        if (kind === "followup" && this.orientQa.loading) return null;
        const dim = String(dimension || this.orientSenseCapabilityKey || "strike").trim() || "strike";
        const currentDim = this.orientSenseCapabilityKey || "";
        const applyToUi = !dimension || dimension === currentDim;
        const seq = applyToUi ? ++this.orientQaReqSeq : this.orientQaReqSeq;
        if (applyToUi) {
          this.orientQa.loading = true;
          this.orientQa.errorText = "";
        }
        const history = (this.orientQa.messages || [])
          .slice(-6)
          .map((msg) => ({ role: msg.role, content: String(msg.content || "").slice(0, 500) }));
        try {
          const resp = await fetch(ORIENT_QA_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              timeout_sec: 45,
              dimension: dim,
              question: String(question || "").slice(0, 2000),
              history: applyToUi ? history : [],
              context: String(this.orientQaContextText || "").slice(0, 5900),
              ...this.llmAdviceRequestBase(),
            }),
          });
          if (!resp.ok) {
            throw new Error(`orient qa api (${resp.status})`);
          }
          const payload = await resp.json();
          if (seq !== this.orientQaReqSeq && applyToUi) return payload;
          const dimLabel = dim === "environment"
            ? "作战环境"
            : String(CAPABILITY_META[dim]?.label || "打击").replace("类", "");
          if (kind === "briefing" && applyToUi) {
            this.orientQa.steps = Array.isArray(payload?.steps) ? payload.steps : [];
            const conclusion = pickOrientConclusion(payload);
            this.orientQa.conclusion = conclusion;
            if (conclusion) {
              const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
              const entry = {
                role: "assistant",
                content: `对蓝方${dimLabel}态势分析\n\n${conclusion}`,
                conclusion,
                time,
                source: String(payload?.source || ""),
                kind: "briefing",
              };
              const msgs = this.orientQa.messages || [];
              this.orientQa.messages = [...msgs, entry].slice(-20);
              this.orientQa.loadedAt = time;
              this.orientQa.source = String(payload?.source || "");
              this.orientQa.dimension = dim;
              this.orientQaByDim = {
                ...this.orientQaByDim,
                [dim]: {
                  steps: this.orientQa.steps,
                  conclusion: this.orientQa.conclusion,
                  loadedAt: time,
                  source: this.orientQa.source,
                  messages: this.orientQa.messages.slice(),
                },
              };
              this.$nextTick(() => this.scrollOrientQaToBottom());
            }
          } else if (kind !== "briefing" && applyToUi) {
            const answer = String(payload?.answer || payload?.conclusion || "").trim();
            if (answer) {
              this.orientQa.messages.push({
                role: "assistant",
                content: answer,
                time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
                source: String(payload?.source || ""),
                kind: String(payload?.kind || kind),
              });
              this.orientQa.loadedAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
              this.orientQa.source = String(payload?.source || "");
              this.orientQa.dimension = dim;
              this.$nextTick(() => this.scrollOrientQaToBottom());
            }
          } else if (kind === "briefing" && !applyToUi) {
            const conclusion = pickOrientConclusion(payload);
            if (conclusion) {
              const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
              const steps = Array.isArray(payload?.steps) ? payload.steps : [];
              const entry = {
                role: "assistant",
                content: `对蓝方${dimLabel}态势分析\n\n${conclusion}`,
                conclusion,
                time,
                source: String(payload?.source || ""),
                kind: "briefing",
              };
              this.orientQaByDim = {
                ...this.orientQaByDim,
                [dim]: {
                  steps,
                  conclusion,
                  loadedAt: time,
                  source: String(payload?.source || ""),
                  messages: [entry],
                },
              };
            }
          }
          return payload;
        } catch (e) {
          if (applyToUi && seq === this.orientQaReqSeq) {
            this.orientQa.errorText = `认知分析失败：${e?.message || e}`;
          }
          return null;
        } finally {
          if (applyToUi && seq === this.orientQaReqSeq) {
            this.orientQa.loading = false;
          }
        }
      },
      async refreshOrientQaAllDimensions() {
        const dims = ["strike", "detect", "defense", "support", "environment"];
        const current = this.orientSenseCapabilityKey || "";
        await Promise.allSettled(
          dims.map((dim) => this.requestOrientUnderstand({
            kind: "briefing",
            dimension: dim === current ? "" : dim,
          })),
        );
      },
      async refreshOrientQaBriefing({ force = false } = {}) {
        if (!this.isOrientSenseLayer && !this.isOrientEnvironmentLayer) return;
        if (this.orientQa.loading && !force) return;
        await this.requestOrientUnderstand({ question: "", kind: "briefing" });
      },
      toggleOrientHubFold(key) {
        if (!this.orientHubFold || !(key in this.orientHubFold)) return;
        this.orientHubFold[key] = !this.orientHubFold[key];
      },
      async submitOrientQaFollowup() {
        const q = String(this.orientQa.question || "").trim();
        if (!q || this.orientQa.loading) return;
        this.orientQa.messages.push({
          role: "user",
          content: q,
          time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
          kind: "followup",
        });
        this.orientQa.question = "";
        this.showAmendSuccessToast();
        this.$nextTick(() => this.scrollOrientQaToBottom());
        await this.requestOrientUnderstand({ question: q, kind: "followup" });
      },
      submitObserveSenseFeedback() {
        const key = this.isObserveEnvironmentLayer
          ? "environment"
          : this.isOrientEnvironmentLayer
            ? "envOrient"
            : this.observeSenseCapabilityKey;
        if (!key) return;
        const text = String(this.observeSenseDraft?.[key] || "").trim();
        if (!text) return;
        const note = {
          text,
          time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        };
        this.observeSenseNotes[key] = [...(this.observeSenseNotes[key] || []), note].slice(-8);
        this.observeSenseDraft[key] = "";
        this.showAmendSuccessToast();
      },
      showAmendSuccessToast() {
        this.amendToastText = "修正成功";
        if (this.amendToastTimer) {
          window.clearTimeout(this.amendToastTimer);
        }
        this.amendToastTimer = window.setTimeout(() => {
          this.amendToastText = "";
          this.amendToastTimer = null;
        }, 1800);
      },
      showToast(text, ms = 3200) {
        const msg = String(text || "").trim();
        if (!msg) return;
        this.amendToastText = msg;
        if (this.amendToastTimer) {
          window.clearTimeout(this.amendToastTimer);
        }
        this.amendToastTimer = window.setTimeout(() => {
          this.amendToastText = "";
          this.amendToastTimer = null;
        }, ms);
      },
      setUiMode(mode) {
        const next = mode === "live" ? "live" : "sample";
        if (next === "sample") {
          this.enterSampleUiMode();
          return;
        }
        this.enterLiveUiMode();
      },
      enterSampleUiMode() {
        this.uiMode = "sample";
        this.hideSampleArtifacts = false;
        this.stopCotAutoCapture();
        this.stopEnvCotAutoCapture();
        this.cotSnapshotReason = "";
        this.hydrateSampleShowcase();
        this.showToast("已切换到样例模式（固化当前前端展示内容）");
      },
      hydrateSampleShowcase() {
        // 固化「当前前端页面生成内容」：多轮假时钟 CoT、沙盘兵力、LLM 样式分析，不清空
        this.cotRounds = makeDemoCotHistory();
        this.activeCotRoundId = "cot-demo-5";
        this.envCotRounds = makeDemoEnvCotHistory();
        this.activeEnvCotRoundId = "env-cot-demo-5";
        this.planKillnet = PLAN_KILLNET;
        this.plannerPinnedKey = PLANNER_DEMO_CASE.key;
        this.plannerHistory = [{
          ...PLANNER_DEMO_CASE,
          key: PLANNER_DEMO_CASE.key,
          displayReply: PLANNER_DEMO_CASE.reply,
        }];
        this.plannerHistoryReady = true;
        this.plannerLiveTurn = null;
        this.commandTrackingReady = true;
        this.executedCommands = SAMPLE_EXECUTED_COMMANDS.map((row) => ({ ...row }));
        this.state = {
          ...this.state,
          status: "running",
          updatedAt: "2026-09-17T00:02:40",
          commandSequence: SAMPLE_COMMAND_SEQUENCE.map((row) => ({ ...row })),
          game: {
            ...(this.state?.game || {}),
            timeFormatted: "02:40",
            gameSeconds: 160,
            supplyUsed: 54,
            supplyCap: 66,
            minerals: 920,
            vespene: 240,
            workers: 24,
            bases: 2,
          },
        };
        this.forceLlmAdviceLoading = false;
        this.forceLlmAdvice = {
          ok: true,
          sections: SAMPLE_FORCE_LLM_SECTIONS.map((row) => ({ ...row })),
          errorText: "",
          answerRaw: "",
          traces: [],
          loadedAt: "08:41:05",
          source: "样例固化包",
        };
        this.orientQaByDim = hydrateSampleOrientMessages(SAMPLE_ORIENT_QA_BY_DIM);
        const dim = ORIENT_SENSE_LAYER_CAP[this.activeRightLayer]
          || (this.activeRightLayer === "oriEnvironment" ? "environment" : "strike");
        const pack = this.orientQaByDim[dim] || this.orientQaByDim.strike;
        if (pack) {
          this.orientQa = {
            loading: false,
            errorText: "",
            dimension: pack.dimension || dim,
            conclusion: pack.conclusion || "",
            steps: Array.isArray(pack.steps) ? pack.steps.map((s) => ({ ...s })) : [],
            messages: Array.isArray(pack.messages) ? pack.messages.map((m) => ({ ...m })) : [],
            loadedAt: pack.loadedAt || "08:41:10",
            source: pack.source || "sample-showcase",
          };
        }
      },
      enterLiveUiMode() {
        const backendReady = Boolean(this.liveUiArmed);
        this.uiMode = "live";
        if (!backendReady) {
          this.clearLiveEmptyPage();
          this.showToast("请先启动后端（launch_support_stack / swarmbrain_launch）。当前为实时空页，可随时回到样例模式。", 4500);
          return;
        }
        this.hideSampleArtifacts = true;
        this.stripDemoCotRounds();
        this.showToast("已进入实时对局模式");
        if (this.canAutoCot()) {
          this.startCotAutoCapture();
          if (this.isCotEnvironmentLayer) this.startEnvCotAutoCapture();
        }
      },
      clearLiveEmptyPage() {
        this.hideSampleArtifacts = true;
        this.stopCotAutoCapture();
        this.stopEnvCotAutoCapture();
        this.cotRounds = [];
        this.activeCotRoundId = "";
        this.envCotRounds = [];
        this.activeEnvCotRoundId = "";
        this.cotSelfplayError = "";
        this.envCotSelfplayError = "";
        this.cotSnapshotReason = "";
        this.plannerPinnedKey = "";
        this.plannerHistory = [];
        this.plannerLiveTurn = null;
        this.plannerHistoryReady = false;
        this.planKillnet = {
          thesis: "",
          nodes: [],
          edges: [],
          sequence: [],
          patternLabel: "",
        };
        this.forceLlmAdvice = {
          ok: false,
          sections: [],
          errorText: "",
          answerRaw: "",
          traces: [],
          loadedAt: "",
          source: "",
        };
        this.orientQaByDim = {};
        this.orientQa = {
          loading: false,
          errorText: "",
          dimension: "",
          conclusion: "",
          steps: [],
          messages: [],
          loadedAt: "",
          source: "",
        };
        // 盘面走冷启动空态（仅可能保留底图缓存）
        this.state = prepareDisplayState(this.state, false);
      },
      stripDemoCotRounds() {
        const cotLive = (this.cotRounds || []).filter((row) => row && row.source !== "demo");
        this.cotRounds = cotLive;
        if (!cotLive.some((row) => row.id === this.activeCotRoundId)) {
          this.activeCotRoundId = cotLive[cotLive.length - 1]?.id || "";
        }
        const envLive = (this.envCotRounds || []).filter((row) => row && row.source !== "demo");
        this.envCotRounds = envLive;
        if (!envLive.some((row) => row.id === this.activeEnvCotRoundId)) {
          this.activeEnvCotRoundId = envLive[envLive.length - 1]?.id || "";
        }
      },
      onBackendLiveArmed() {
        // 后端开始写盘：仅提示；若用户仍在样例模式则不打断固化展示
        if (this.uiMode === "sample") {
          this.showToast("检测到后端对局写入；样例展示保持不变，可手动切到「实时对局」", 4200);
          return;
        }
        this.hideSampleArtifacts = true;
        this.stripDemoCotRounds();
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
            body: JSON.stringify({
              question: q,
              session_id: "dashboard-ask-duel",
              side: "duel",
              scope: "duel",
            }),
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
        if (this.isSampleShowcase) return;
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
            body: JSON.stringify({ timeout_sec: 55, ...this.llmAdviceRequestBase() }),
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
            body: JSON.stringify({ timeout_sec: 55, targets, ...this.llmAdviceRequestBase() }),
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
        // 实时刷新：默认强制重拉，去掉“同签名跳过”的本地记忆
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
            body: JSON.stringify({ timeout_sec: 70, chains, ...this.llmAdviceRequestBase() }),
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
        const previous = this.deerflowRisk || {};
        try {
          const resp = await fetch(DEERFLOW_RISK_API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              mode: "fast",
              timeout_sec: 55,
              ...this.llmAdviceRequestBase(),
            }),
          });
          if (!resp.ok) {
            throw new Error(`risk api unavailable (${resp.status})`);
          }
          const payload = await resp.json();
          const intentObj = payload?.intent && typeof payload.intent === "object" ? payload.intent : {};
          const riskObj = payload?.risk && typeof payload.risk === "object" ? payload.risk : {};
          const stagesObj =
            payload?.stages && typeof payload.stages === "object" ? payload.stages : {};
          const hasIntent =
            Boolean(intentObj.primary_strategy || intentObj.primary_strategy_cn) ||
            (intentObj.intent_dimensions && Object.keys(intentObj.intent_dimensions).length > 0);
          const hasRisk = Array.isArray(riskObj.risks) && riskObj.risks.length > 0;
          if (!payload?.ok && !hasIntent && !hasRisk && previous.thread_id) {
            // 新结果为空时保留上一轮有效内容，避免界面被刷成「无意图/无风险」
            this.deerflowRisk = {
              ...previous,
              errorText: String(payload?.summary || "本轮未返回有效结果，已保留上一轮"),
            };
            return;
          }
          this.deerflowRisk = {
            ok: Boolean(payload?.ok),
            thread_id: String(payload?.thread_id || ""),
            mode: String(payload?.mode || "fast"),
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
            ...previous,
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
      cotRoleTitle(role, dimKey) {
        return cotRoleAgentName(role, dimKey || this.cotSenseDimKey || "strike");
      },
      capabilityToRole(capKey) {
        if (!capKey || capKey === "environment") return "";
        const map = { strike: "strike", detect: "scout", defense: "defend", support: "support" };
        return map[capKey] || "";
      },
      markerMatchesObserveRole(marker, roleFilter) {
        if (!roleFilter || !marker) return true;
        if (marker.role === roleFilter) return true;
        if (roleFilter === "support" && marker.role === "base") return true;
        const cap = CAPABILITY_META[marker.capability] ? marker.capability : "";
        if (cap && this.capabilityToRole(cap) === roleFilter) return true;
        return false;
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
        if (row?.side === "enemy" || row?.relativeSide === "enemy") return "strike";
        if (row?.capability === "strike") return "strike";
        return "unit";
      },
      unitMarkerTitle(marker) {
        const side =
          marker.side === "blue" ? "蓝方" :
          marker.side === "red" ? "红方" :
          marker.relativeSide === "enemy" ? "蓝方" : "红方";
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
        const candidates = markers.filter((m) => m && m.relativeSide === "enemy");
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
        if (this.activeRightLayer !== "critical" && !this.isOrientHubLayer) return false;
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
      plannerScrollContainer() {
        return this.$refs.planModuleFill || this.$refs.plannerConversation;
      },
      handlePlannerScroll() {
        const container = this.plannerScrollContainer();
        this.plannerAutoFollow = this.isPlannerNearBottom(container);
      },
      scrollPlannerToBottom(force = false) {
        this.$nextTick(() => {
          const container = this.plannerScrollContainer();
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
        this.plannerPinnedKey = "";
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
          const entries = await Promise.all(
            Object.entries(STATE_URLS).map(async ([side, stateUrl]) => {
              const url = new URL(stateUrl);
              url.searchParams.set("t", Date.now());
              const response = await fetch(url, { cache: "no-store" });
              if (!response.ok) {
                return [side, null];
              }
              return [side, await response.json()];
            }),
          );
          const loadedStates = Object.fromEntries(entries);
          for (const side of Object.keys(STATE_URLS)) {
            if (loadedStates[side]) {
              this.duelStates[side] = loadedStates[side];
            }
          }
          const nextState =
            loadedStates[this.activeDuelSide] ||
            (this.duelStates[this.activeDuelSide]?.updatedAt
              ? this.duelStates[this.activeDuelSide]
              : null) ||
            loadedStates.red ||
            loadedStates.blue;

          if (!nextState?.updatedAt) {
            throw new Error("duel state files unavailable");
          }

          const fp = fingerprintDashboardState(nextState);
          if (!this.liveUiArmed) {
            if (this.stateBootstrapFingerprint === null) {
              this.stateBootstrapFingerprint = fp;
            } else if (fp !== this.stateBootstrapFingerprint) {
              this.liveUiArmed = true;
              this.onBackendLiveArmed();
            }
          }

          const isRunning = nextState?.status === "running";
          const rawUpdatedAt = String(nextState?.updatedAt || "");
          if (isRunning && rawUpdatedAt && rawUpdatedAt !== this.cotLiveUpdatedAt) {
            this.cotLiveUpdatedAt = rawUpdatedAt;
            this.cotLiveUpdatedAtMs = Date.now();
          }
          if (!this.liveUiArmed || !isRunning) {
            // 样例模式保留固化的方案卷宗 / 已执行指令，不被冷启动清空
            if (this.uiMode !== "sample") {
              this.plannerHistoryReady = false;
              this.plannerHistory = [];
              this.plannerLiveTurn = null;
              this.plannerSeenTurnKeys = Object.create(null);
              this.executedCommands = [];
              this.commandTrackingReady = false;
            }
          }

          // 样例模式：后台仍轮询以检测后端启动，但不覆盖当前固化展示盘面
          if (this.uiMode === "sample") {
            this.isLoading = false;
            this.fetchFailureCount = 0;
            this.fetchError = "";
            return;
          }

          this.state = prepareDisplayState(nextState, this.liveUiArmed);
          this.syncLoadingCotRoundClock();
          this.syncCotAutoFromBackend(isRunning);
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
            this.fetchError = "无法加载红蓝双方状态文件";
          }
          if (this.fetchFailureCount >= 3) {
            this.enterCotSnapshot("backend");
          }
          this.isLoading = false;
          console.warn("duel dashboard state poll failed", error);
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
      applyDashboardAppearance();
      if (this.isSampleShowcase) {
        this.hydrateSampleShowcase();
      }
      this.startPolling();
      this.startFeatureAutoPlay();
      this.startLlmAdvicePolling();
      this.startKnowledgeLiveRotation();
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
      this.stopLlmAdvicePolling();
      this.stopOrientQaPolling();
      this.stopKnowledgeLiveRotation();
      this.stopCotAutoCapture();
    },
  }).mount("#app");
  } catch (err) {
    showDashboardBootError(err && err.message ? err.message : "前端脚本执行失败。");
  }
}
