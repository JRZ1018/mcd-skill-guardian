"use strict";

// The Python engine produces the verdicts. This page only presents its report.
const $ = selector => document.querySelector(selector);
const STATUS = {
  "regression": {label:"调用退化", hint:"前次有效，本次未通过输入契约校验。"},
  "pass": {label:"调用通过", hint:"这条具体调用在前后报告中均通过校验。"},
  "preexisting-failure": {label:"原有失败", hint:"这条调用在变化前已未通过校验。"},
  "improved": {label:"调用改善", hint:"前次未通过，本次具体调用通过校验。"},
  "unverifiable": {label:"无法验证", hint:"报告无法确定该调用是否满足契约。"}
};
let dataset = null;
let current = null;
let selection = {callIndex:null, tool:null};
let resizeFrame = 0;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = String(text);
  return element;
}
function array(value) { return Array.isArray(value) ? value : []; }
function display(value, fallback = "未提供") {
  return typeof value === "string" && value.trim() ? value : fallback;
}
function count(value) { return Number.isInteger(value) && value >= 0 ? String(value).padStart(2,"0") : "—"; }
function statusOf(call) { return STATUS[call?.status] || STATUS.unverifiable; }
function modeName(mode) {
  return ({live:"真实快照", synthetic:"合成快照", demo:"模拟快照", mixed:"混合来源"})[mode] || display(mode,"未声明来源");
}
function timeLabel(value) {
  if (typeof value !== "string") return "采集时间未提供";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "采集时间未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone:"Asia/Shanghai", year:"numeric",month:"2-digit",day:"2-digit",
    hour:"2-digit",minute:"2-digit",hour12:false
  }).format(timestamp) + " 北京时间";
}
function cleanStatusClass(status) { return Object.hasOwn(STATUS,status) ? "is-"+status : "is-unverifiable"; }
function setText(selector,text) { $(selector).textContent = text; }

function buildTabs() {
  const tabs = $("#scenario-tabs");
  tabs.replaceChildren();
  const options = [{id:"baseline",title:"真实基线"}, ...dataset.scenarios];
  options.forEach((option,index) => {
    const button = node("button", "", option.title);
    button.type = "button";
    button.id = "scenario-tab-"+index;
    button.setAttribute("role","tab");
    button.setAttribute("aria-selected",String(index === 0));
    button.setAttribute("aria-controls","report-panel");
    button.tabIndex = index === 0 ? 0 : -1;
    button.addEventListener("click",() => activate(index));
    button.addEventListener("keydown",event => {
      let next = index;
      if (event.key === "ArrowRight") next = (index+1)%options.length;
      else if (event.key === "ArrowLeft") next = (index+options.length-1)%options.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = options.length-1;
      else return;
      event.preventDefault();
      activate(next);
      tabs.children[next].focus();
    });
    tabs.append(button);
  });
}

function activate(index) {
  if (!dataset) return;
  const isBaseline = index === 0;
  const option = isBaseline ? dataset.baseline : dataset.scenarios[index-1];
  if (!option) return;
  current = {report:option.report, isBaseline, title:isBaseline?"真实基线：清单中的调用验证":option.title};
  const report = current.report;
  const calls = array(report.calls);
  const changes = array(report.changes);
  const regressionIndex = calls.findIndex(call => call.status === "regression");
  const used = new Set(calls.map(call => call.tool));
  const unusedChangedTool = changes.find(change => typeof change.tool === "string" && !used.has(change.tool));
  if (!isBaseline && regressionIndex < 0 && unusedChangedTool) {
    selection = {callIndex:null,tool:unusedChangedTool.tool};
  } else {
    selection.callIndex = regressionIndex >= 0 ? regressionIndex : calls.length ? 0 : null;
    selection.tool = selection.callIndex === null ? changes[0]?.tool || null : calls[selection.callIndex].tool;
  }
  [...$("#scenario-tabs").children].forEach((button,i) => {
    button.setAttribute("aria-selected",String(i === index));
    button.tabIndex = i === index ? 0 : -1;
  });
  $("#report-panel").setAttribute("aria-labelledby","scenario-tab-"+index);
  const label = $("#mode-label");
  label.textContent = isBaseline ? "真实基线" : "合成演练";
  label.className = "mode-label"+(isBaseline?"":" is-synthetic");
  setText("#scenario-title",current.title);
  setText("#scenario-description",isBaseline
    ? "展示真实采集契约对应的报告。有效范围仅限清单中已声明的具体调用，探测结果另见上方快照摘要。"
    : display(option.description,"该场景使用合成契约变化，不代表官方接口实际发生过对应变更。"));
  const summary = report.summary || {};
  setText("#metric-skills",count(summary.skills));
  setText("#metric-calls",count(summary.calls));
  setText("#metric-regressions",count(summary.regressions));
  setText("#metric-unverifiable",count(summary.unverifiable));
  setText("#metric-extra",`原有失败 ${count(summary.preexisting_failures)} · 改善 ${count(summary.improved)} · 契约变化记录 ${count(summary.schema_changes)}`);
  const limitations = $("#limitations-list"); limitations.replaceChildren();
  array(report.limitations).filter(item => typeof item === "string").forEach(item => limitations.append(node("li","",item)));
  if (!limitations.children.length) limitations.append(node("li","","该报告未提供额外限制说明；页面下方的具体调用范围说明始终适用。"));
  renderGraph();
  renderEvidence();
  setText("#live-status",`${isBaseline?"真实基线":"合成演练"}，${current.title}。报告记录 ${count(summary.calls)} 次具体调用，${count(summary.regressions)} 次退化。`);
}

function renderGraph() {
  const report = current.report;
  const calls = array(report.calls);
  const changes = array(report.changes);
  const tools = [...new Set([...calls.map(call => call.tool),...changes.map(change => change.tool)].filter(tool => typeof tool === "string"))];
  const toolNodes = $("#tool-nodes"), callNodes = $("#call-nodes");
  toolNodes.replaceChildren(); callNodes.replaceChildren();
  tools.forEach((tool,index) => {
    const button = node("button","tool-node"); button.type = "button";
    button.dataset.toolIndex = String(index);
    button.setAttribute("aria-pressed",String(selection.tool === tool));
    button.append(node("span","node-index",String(index+1).padStart(2,"0")),node("span","tool-name",tool));
    const dependents = calls.filter(call => call.tool === tool);
    button.append(node("div","node-note",dependents.length?`${dependents.length} 条声明调用` : "清单中没有依赖调用"));
    button.addEventListener("click",() => {
      const callIndex = calls.findIndex(call => call.tool === tool);
      selection = {callIndex:callIndex < 0 ? null : callIndex,tool};
      refreshSelection();
    });
    toolNodes.append(button);
  });
  if (!tools.length) toolNodes.append(node("p","graph-empty","报告没有工具记录。"));
  calls.forEach((call,index) => {
    const button = node("button","call-node "+cleanStatusClass(call.status)); button.type = "button";
    button.dataset.callIndex = String(index);
    button.setAttribute("aria-pressed",String(selection.callIndex === index));
    button.append(node("strong","",display(call.skill_name,display(call.skill_id))),
      node("span","call-id",display(call.id)),node("span","node-status",statusOf(call).label));
    button.addEventListener("click",() => { selection = {callIndex:index,tool:call.tool}; refreshSelection(); });
    callNodes.append(button);
  });
  if (!calls.length) callNodes.append(node("p","graph-empty","清单未声明具体调用。"));
  renderRule();
  scheduleWires();
}

function refreshSelection() {
  const calls = array(current.report.calls);
  const tools = [...new Set([...calls.map(call => call.tool),...array(current.report.changes).map(change => change.tool)].filter(tool => typeof tool === "string"))];
  [...$("#tool-nodes").querySelectorAll("button")].forEach((button,index) => button.setAttribute("aria-pressed",String(tools[index] === selection.tool)));
  [...$("#call-nodes").querySelectorAll("button")].forEach((button,index) => button.setAttribute("aria-pressed",String(index === selection.callIndex)));
  renderRule(); renderEvidence(); scheduleWires();
  setText("#live-status",selection.callIndex === null?`已选择工具 ${selection.tool}；清单中没有依赖调用。`:`已选择调用 ${display(calls[selection.callIndex].id)}，${statusOf(calls[selection.callIndex]).label}。`);
}

function renderRule() {
  const target = $("#rule-node"); target.replaceChildren();
  const call = selection.callIndex === null ? null : array(current.report.calls)[selection.callIndex];
  target.append(node("p","rule-kicker","VALIDATION EVIDENCE"));
  if (!call) {
    target.append(node("p","rule-title",selection.tool?"未声明依赖":"无调用证据"),
      node("p","rule-text",selection.tool?"该工具的变化没有对应到当前清单中的具体调用。结论只覆盖这份清单。":"请查看已载入报告的调用清单。"));
    return;
  }
  const issue = array(call.after?.issues)[0] || array(call.before?.issues)[0];
  target.append(node("p","rule-title",issue?display(issue.rule,"validation"):call.after?.valid === true?"valid":call.after?.valid === false?"invalid":"unverifiable"));
  target.append(node("p","rule-text",issue?display(issue.message):statusOf(call).hint));
  if (issue) target.append(node("p","rule-path","PATH "+display(issue.path,"/（根路径）")));
  else target.append(node("p","rule-path","本次结论来自 Python 报告；网页不重新校验。"));
}

function renderJson(target,value,placeholder) {
  target.replaceChildren();
  if (placeholder) { target.append(node("p","json-placeholder",placeholder)); return; }
  const source = JSON.stringify(value,null,2);
  if (source === undefined) { target.append(node("p","json-placeholder","该侧未提供证据片段。")); return; }
  source.split("\n").forEach((line,index) => {
    const row = node("div","json-line");
    const number = node("span","line-number",String(index+1)); number.setAttribute("aria-hidden","true");
    const text = node("span","line-content");
    const pattern = /("(?:\\.|[^"\\])*"\s*:?)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b|([{}\[\],:])/g;
    let last = 0;
    for (const match of line.matchAll(pattern)) {
      if (match.index > last) text.append(document.createTextNode(line.slice(last,match.index)));
      const kind = match[1] ? (match[1].trimEnd().endsWith(":")?"key":"string") : match[2] ? "number" : match[3] ? "literal" : "punctuation";
      text.append(node("span","json-"+kind,match[0]));
      last = match.index+match[0].length;
    }
    if (last < line.length) text.append(document.createTextNode(line.slice(last)));
    row.append(number,text); target.append(row);
  });
}

function renderEvidence() {
  const report = current.report;
  const call = selection.callIndex === null ? null : array(report.calls)[selection.callIndex];
  const toolChanges = array(report.changes).filter(change => change.tool === selection.tool);
  const changes = call && array(call.changes).length ? call.changes : toolChanges;
  const state = $("#call-status");
  state.className = "status-label"+(call?" "+cleanStatusClass(call.status):"");
  state.textContent = call?statusOf(call).label:selection.tool?"未声明依赖":"没有调用";
  setText("#evidence-title",call?`${display(call.skill_name,display(call.skill_id))} / ${display(call.id)}`:display(selection.tool,"报告中没有可展示调用"));
  setText("#evidence-description",call?`${display(call.tool)} · ${statusOf(call).hint}`:"当前展示工具层面的变化；清单中没有对应的具体调用。不能据此推断其他 Skill 的情况。");
  setText("#arguments-code",call && Object.hasOwn(call,"arguments")?JSON.stringify(call.arguments,null,2):call?"此报告未附实参；请以项目中的 Skill 依赖清单为准。":"没有对应的声明调用。");
  setText("#before-mode",modeName(report.before?.mode));
  setText("#after-mode",modeName(report.after?.mode));
  setText("#before-time",timeLabel(report.before?.captured_at));
  setText("#after-time",timeLabel(report.after?.captured_at));
  const hasBeforeSchema = call && Object.hasOwn(call,"before_schema");
  const hasAfterSchema = call && Object.hasOwn(call,"after_schema");
  if (hasBeforeSchema || hasAfterSchema) {
    renderJson($("#before-code"),call.before_schema,hasBeforeSchema?null:"报告未提供该侧 inputSchema。工具缺失与片段未记录需结合报告解释。");
    renderJson($("#after-code"),call.after_schema,hasAfterSchema?null:"报告未提供该侧 inputSchema。工具缺失与片段未记录需结合报告解释。");
    setText("#evidence-footnote","对照展示演示数据明确附带的 inputSchema。调用状态来自 Python 报告；网页不重新校验，也不以字段差异推断所有输入的兼容性。");
  } else if (changes.length) {
    for (const side of ["before","after"]) {
      const fragments = changes.map(change => {
        const item = {path:display(change.path,"/"),kind:display(change.kind)};
        if (Object.hasOwn(change,side)) item.fragment = change[side];
        else item.note = "报告未提供该侧片段";
        return item;
      });
      renderJson($("#"+side+"-code"),fragments);
    }
    setText("#evidence-footnote","对照仅展示报告记录的变化路径与 schema 片段，不补造完整契约。字段未记录不等于该字段值为 null。");
  } else {
    renderJson($("#before-code"),call?.before,call?null:"报告未提供与当前选择相关的变化前片段。");
    renderJson($("#after-code"),call?.after,call?null:"报告未提供与当前选择相关的变化后片段。");
    setText("#evidence-footnote","当前选择没有相关 schema 差异片段；上方显示报告中的前后调用校验结果。");
  }
  const impacts = $("#impact-list"); impacts.replaceChildren();
  function impact(text,warning=false) { impacts.append(node("li",warning?"is-warning":"",text)); }
  if (call) {
    impact(statusOf(call).hint,call.status === "regression" || call.status === "preexisting-failure");
    const issues = array(call.after?.issues);
    issues.slice(0,8).forEach(issue => impact(`${display(issue.path,"/（根路径）")} · ${display(issue.rule,"validation")}：${display(issue.message)}`,true));
    if (issues.length > 8) impact(`其余 ${issues.length-8} 条校验问题请查看下载的完整报告。`);
    if (changes.length) impact(`该工具有 ${changes.length} 条相关契约变化记录。差异本身不等于调用退化，状态以该具体输入的校验结果为准。`);
    else impact("此调用未记录相关 schema 变化；结论仅针对清单中这一组输入。");
  } else if (selection.tool) {
    impact(`当前清单中没有调用依赖 ${selection.tool}，因此不能将该工具的变化归因到某条已声明调用。`);
    impact("请补充清单，才能检查未声明的 Skill 或其他输入。");
  } else impact("报告没有具体调用，无法展示调用影响。");
}

function scheduleWires() {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(drawWires);
}
function drawWires() {
  const svg = $("#dependency-wires"); svg.replaceChildren();
  if (!current || window.matchMedia("(max-width:620px)").matches) return;
  const graph = $("#dependency-graph").getBoundingClientRect();
  svg.setAttribute("viewBox",`0 0 ${graph.width} ${graph.height}`);
  const tools = [...$("#tool-nodes").querySelectorAll("button")];
  const toolNames = tools.map(button => button.querySelector(".tool-name").textContent);
  const callNodes = [...$("#call-nodes").querySelectorAll("button")];
  const calls = array(current.report.calls);
  const rule = $("#rule-node");
  function connect(from,to,active) {
    const first = from.getBoundingClientRect(), second = to.getBoundingClientRect();
    const x1 = first.right-graph.left, y1 = first.top+first.height/2-graph.top;
    const x2 = second.left-graph.left, y2 = second.top+second.height/2-graph.top;
    const bend = Math.max(10,(x2-x1)/2);
    const path = document.createElementNS("http://www.w3.org/2000/svg","path");
    path.setAttribute("d",`M ${x1} ${y1} C ${x1+bend} ${y1}, ${x2-bend} ${y2}, ${x2} ${y2}`);
    if (active) path.setAttribute("class","is-active");
    svg.append(path);
  }
  calls.forEach((call,index) => {
    const toolIndex = toolNames.indexOf(call.tool);
    if (toolIndex >= 0 && callNodes[index]) connect(tools[toolIndex],callNodes[index],selection.callIndex === index);
  });
  if (selection.callIndex !== null && callNodes[selection.callIndex]) connect(callNodes[selection.callIndex],rule,true);
}

function validReport(value) {
  const validIssue = issue => issue && typeof issue === "object"
    && typeof issue.path === "string" && typeof issue.rule === "string" && typeof issue.message === "string";
  const validResult = result => result && typeof result === "object"
    && [true,false,null].includes(result.valid) && Array.isArray(result.issues)
    && result.issues.length <= 100 && result.issues.every(validIssue);
  const validChange = change => change && typeof change === "object"
    && typeof change.path === "string" && typeof change.kind === "string";
  return value && typeof value === "object" && !Array.isArray(value)
    && value.summary && typeof value.summary === "object" && Array.isArray(value.calls)
    && value.calls.length <= 500 && value.calls.every(call => call && typeof call === "object" && typeof call.tool === "string"
      && validResult(call.before) && validResult(call.after) && Array.isArray(call.changes)
      && call.changes.length <= 1000 && call.changes.every(validChange))
    && Array.isArray(value.changes) && value.changes.length <= 2000
    && value.changes.every(change => validChange(change) && typeof change.tool === "string");
}

async function loadData() {
  const load = $("#load-state");
  load.hidden = false; load.classList.remove("is-error");
  $("#retry").hidden = true; $("#report-panel").hidden = true;
  setText("#load-message","正在载入基线与演练报告…");
  try {
    const response = await fetch("demo-data.json",{cache:"no-cache"});
    if (!response.ok) throw new Error("unavailable");
    const content = await response.text();
    if (content.length > 4_000_000) throw new Error("oversized");
    const data = JSON.parse(content);
    if (!data || !data.baseline || !validReport(data.baseline.report)
      || data.baseline.report.before?.mode !== "live" || data.baseline.report.after?.mode !== "live"
      || !Array.isArray(data.scenarios) || data.scenarios.length > 12
      || !data.scenarios.every(item => item && typeof item.title === "string" && validReport(item.report)
        && item.report.after?.mode === "synthetic")) throw new Error("invalid");
    dataset = data;
    const snapshot = data.baseline.snapshot_summary || {};
    setText("#tool-count",count(snapshot.tool_count));
    setText("#captured-at",timeLabel(snapshot.captured_at));
    setText("#probe-summary",Number.isInteger(snapshot.probe_passed) && Number.isInteger(snapshot.probe_total)
      && snapshot.probe_passed >= 0 && snapshot.probe_passed <= snapshot.probe_total
      ? `${snapshot.probe_passed} / ${snapshot.probe_total} 条探测通过` : "摘要未提供探测计数");
    buildTabs();
    load.hidden = true; $("#report-panel").hidden = false;
    activate(0);
  } catch {
    dataset = null; current = null;
    load.classList.add("is-error"); $("#retry").hidden = false;
    setText("#load-message","报告暂时无法载入。请通过 HTTP 打开页面，并确认 demo-data.json 可读取。");
    setText("#tool-count","—");setText("#captured-at","未载入真实基线");setText("#probe-summary","未载入探测结果");
    setText("#live-status","报告载入失败，尚未显示任何验证结论。");
  }
}

$("#retry").addEventListener("click",loadData);
$("#export-report").addEventListener("click",() => {
  if (!current) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(current.report,null,2)+"\n"],{type:"application/json;charset=utf-8"}));
  const link = node("a"); link.href = url;
  link.download = current.isBaseline?"mcd-guardian-baseline.report.json":"mcd-guardian-synthetic.report.json";
  document.body.append(link);link.click();link.remove();
  setTimeout(() => URL.revokeObjectURL(url),1000);
});
window.addEventListener("resize",scheduleWires,{passive:true});
loadData();
