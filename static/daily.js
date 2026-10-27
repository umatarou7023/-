const $ = (id) => document.getElementById(id);
const roles = {kitchen: {label: "キッチン", limit: 8}, hall: {label: "ホール", limit: 10}};
const now = new Date();
$("day").value = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
let version = "", loadedDay = "", fileData = "", reviewed = null;
let loadedRoster = "";
let loadedBreakSettings = "";

function message(text) { $("status").textContent = text; $("status").hidden = !text; }
function invalidate() { reviewed = null; $("result").hidden = true; $("overwrite").checked = false; }
function resetCandidates() { version = ""; loadedDay = ""; $("candidates").replaceChildren(); $("candidate-area").hidden = true; invalidate(); }
function rosterPayload() {
  const names = (id) => $(id).value.split(/\r?\n/).map((n) => n.trim()).filter(Boolean);
  return {kitchen: names("kitchen"), hall: names("hall")};
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  if (response.status === 401) { location.href = "/login"; throw new Error("ログインしてください。"); }
  if (!response.ok) { const error = await response.json(); throw new Error(error.error || "処理できませんでした。"); }
  return response;
}
async function loadRoster() {
  try {
    const {roster} = await (await api("/api/roster")).json();
    $("kitchen").value = roster.kitchen.join("\n"); $("hall").value = roster.hall.join("\n");
    loadedRoster = JSON.stringify(rosterPayload());
  } catch (e) { message(e.message); }
}
function breakSettingsPayload() {
  const result = {mode: document.querySelector('input[name="workflow-mode"]:checked').value};
  for (const key of ["weekday", "weekend"]) {
    const mode = $(`${key}-rule`).value;
    result[key] = mode === "time" ? {mode, start: $(`${key}-start`).value, end: $(`${key}-end`).value} : {mode};
  }
  return result;
}
function syncRuleInputs() {
  for (const key of ["weekday", "weekend"]) for (const edge of ["start", "end"]) $(`${key}-${edge}`).disabled = $(`${key}-rule`).value !== "time";
}
function displayBreakSettings(settings) {
  document.querySelector(`input[name="workflow-mode"][value="${settings.mode}"]`).checked = true;
  for (const key of ["weekday", "weekend"]) {
    $(`${key}-rule`).value = settings[key].mode;
    $(`${key}-start`).value = settings[key].start || "";
    $(`${key}-end`).value = settings[key].end || "";
  }
  syncRuleInputs(); loadedBreakSettings = JSON.stringify(breakSettingsPayload());
  $("break-settings-note").textContent = `保存済み：${settings.mode === "auto" ? "固定休憩を自動入力" : "管理者が確認して指定"}。次回もこの設定を使います。`;
}
async function loadBreakSettings() {
  try { displayBreakSettings((await (await api("/api/break-settings")).json()).settings); }
  catch (e) { message(e.message); $("break-settings-note").textContent = "設定を読み込めませんでした。画面を更新してください。"; }
}
Promise.all([loadRoster(), loadBreakSettings()]).then(() => {
  if (loadedBreakSettings && breakSettingsPayload().mode === "auto") loadCandidates(false);
});
for (const node of document.querySelectorAll('input[name="workflow-mode"], #weekday-rule, #weekday-start, #weekday-end, #weekend-rule, #weekend-start, #weekend-end')) node.addEventListener("input", () => {
  syncRuleInputs(); resetCandidates();
  $("break-settings-note").textContent = "変更は未保存です。「モード・休憩設定を保存」を押してください。";
});
$("save-break-settings").addEventListener("click", async () => {
  $("save-break-settings").disabled = true;
  try {
    const data = breakSettingsPayload();
    const result = await (await api("/api/break-settings", data)).json();
    resetCandidates();
    if (JSON.stringify(data) !== JSON.stringify(breakSettingsPayload())) {
      loadedBreakSettings = JSON.stringify(result.settings); message("保存後に設定が変わりました。変更分も保存してください。"); return;
    }
    displayBreakSettings(result.settings);
    if (result.settings.mode === "auto") await loadCandidates(false);
    else message("手動モードを保存しました。候補を読み込んで、休憩を指定してください。");
  } catch (e) { message(e.message); }
  finally { $("save-break-settings").disabled = false; }
});
for (const id of ["kitchen", "hall"]) $(id).addEventListener("input", () => {
  resetCandidates(); $("roster-note").textContent = "一覧が未保存です。保存してから候補を読み込んでください。";
});
$("save-roster").addEventListener("click", async () => {
  $("save-roster").disabled = true;
  try {
    const payload = rosterPayload();
    await api("/api/roster", payload);
    loadedRoster = JSON.stringify(payload); resetCandidates();
    $("roster-note").textContent = "保存しました。この順を同時刻の並び順に使います。";
    message("スタッフ一覧を保存しました。対象日の候補を読み込んでください。");
  } catch (e) { message(e.message); }
  finally { $("save-roster").disabled = false; }
});
$("day").addEventListener("change", () => {
  resetCandidates(); $("date-confirmed").checked = false;
  if (JSON.stringify(breakSettingsPayload()) === loadedBreakSettings && breakSettingsPayload().mode === "auto") loadCandidates(false);
});
function selectedRows() { return [...$("candidates").children].filter((row) => row.querySelector(".pick").checked); }
function updateCapacity() {
  const selected = selectedRows();
  const counts = Object.entries(roles).map(([role, info]) => {
    const count = selected.filter((r) => r.dataset.role === role).length;
    return `${info.label} ${count}/${info.limit}人${count > info.limit ? `（${count-info.limit}人超過：反映できません）` : ""}`;
  });
  const unknown = [...$("candidates").children].filter((r) => !r.dataset.role).length;
  $("capacity").textContent = counts.join(" ／ ") + (unknown ? ` ／ 未登録 ${unknown}人：一覧に登録してから読み込み直してください` : "");
}
async function loadCandidates(confirmReset=true) {
  if (JSON.stringify(rosterPayload()) !== loadedRoster) { message("スタッフ一覧の変更を保存してください。"); return; }
  if (JSON.stringify(breakSettingsPayload()) !== loadedBreakSettings) { message("モード・休憩設定を保存してから候補を読み込んでください。"); return; }
  if (confirmReset && version && !window.confirm("候補を読み込み直すと、選択と休憩の指定がリセットされます。続けますか？")) return;
  const day = $("day").value;
  $("load").disabled = true;
  try {
    const result = await (await api(`/api/daily-candidates?day=${encodeURIComponent(day)}`)).json();
    if (day !== $("day").value || JSON.stringify(rosterPayload()) !== loadedRoster || JSON.stringify(breakSettingsPayload()) !== loadedBreakSettings) return;
    displayBreakSettings(result.settings);
    resetCandidates(); version = result.version; loadedDay = day;
    const auto = result.settings.mode === "auto";
    $("day-type").textContent = `${day}：${result.day_type.label}${result.day_type.holiday ? `（${result.day_type.holiday}）` : ""} ／ ${auto ? "保存した固定休憩を自動入力" : "休憩は管理者が指定"}`;
    for (const person of result.rows) {
      const row = document.createElement("tr"); row.dataset.id = person.id; row.dataset.role = person.role;
      const pick = document.createElement("input"); pick.type = "checkbox"; pick.className = "pick";
      pick.checked = !!person.role; pick.disabled = !person.role; pick.setAttribute("aria-label", `${person.name}を反映する`);
      const pickCell = document.createElement("td"); pickCell.append(pick); row.append(pickCell);
      for (const text of [roles[person.role]?.label || "未登録", person.name, person.shift]) { const cell = document.createElement("td"); cell.textContent = text; row.append(cell); }
      const mode = document.createElement("select"); mode.className = "rest-mode";
      mode.setAttribute("aria-label", `${person.name}の休憩`);
      mode.append(new Option(person.break_issue ? "要確認" : "未確認", ""), new Option(auto ? "休憩なし（自動）" : "休憩なし（確認済み）", "none"), new Option("時刻を指定", "time"));
      if (auto) { mode.value = person.automatic_break.mode || ""; mode.disabled = true; }
      const modeCell = document.createElement("td"); modeCell.append(mode); row.append(modeCell);
      if (auto && (person.break_issue || person.break_note)) {
        const note = document.createElement("p"); note.className = "small";
        note.textContent = person.break_issue || person.break_note; modeCell.append(note);
      }
      for (const [kind, label] of [["rest-start", "休憩開始"], ["rest-end", "休憩終了"]]) {
        const input = document.createElement("input"); input.type = "time"; input.step = "900"; input.className = kind; input.disabled = true;
        if (auto) input.value = person.automatic_break[kind === "rest-start" ? "start" : "end"] || "";
        input.setAttribute("aria-label", `${person.name}の${label}`);
        const cell = document.createElement("td"); cell.append(input); row.append(cell);
      }
      mode.addEventListener("change", () => { for (const input of row.querySelectorAll("input[type=time]")) input.disabled = mode.value !== "time"; });
      $("candidates").append(row);
    }
    $("candidate-area").hidden = false; updateCapacity();
    message(result.rows.length ? `${result.rows.length}人の出勤希望を読み込みました。${auto ? "固定休憩を適用しました。人数と内容を確認してください。" : "対象者と休憩を確認してください。"}` : "対象日の出勤希望はありません。");
  } catch (e) { resetCandidates(); message(e.message); }
  finally { $("load").disabled = false; }
}
$("load").addEventListener("click", () => loadCandidates());
$("candidates").addEventListener("input", () => { invalidate(); updateCapacity(); });
$("file").addEventListener("change", async () => {
  fileData = ""; invalidate(); $("date-confirmed").checked = false;
  $("sheet").replaceChildren(new Option("読み込み中…", ""));
  const file = $("file").files[0];
  if (!file) return;
  try {
    if (!file.name.toLowerCase().endsWith(".xlsx") || file.size > 8*1024*1024) throw new Error("8MB以下の.xlsxファイルを選んでください。");
    const encoded = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = () => reject(new Error("ファイルを読めませんでした。")); reader.readAsDataURL(file); });
    const result = await (await api("/api/inspect", {file: encoded})).json();
    if ($("file").files[0] !== file) return;
    fileData = encoded; $("sheet").replaceChildren(...result.sheets.map((n) => new Option(n, n))); message("");
  } catch (e) { message(e.message); }
});
for (const id of ["sheet", "date-confirmed"]) $(id).addEventListener("change", invalidate);
$("sample").addEventListener("click", async () => {
  try {
    const response = await api(`/api/daily-sample?day=${encodeURIComponent($("day").value)}`);
    const url = URL.createObjectURL(await response.blob()), link = document.createElement("a");
    link.href = url; link.download = `日別お試し_${$("day").value}.xlsx`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  } catch (e) { message(e.message); }
});
function payload() {
  const selected = selectedRows(), breaks = {};
  for (const row of selected) breaks[row.dataset.id] = {mode: row.querySelector(".rest-mode").value, start: row.querySelector(".rest-start").value, end: row.querySelector(".rest-end").value};
  return {file: fileData, sheet: $("sheet").value, day: $("day").value, date_confirmed: $("date-confirmed").checked, selected_ids: selected.map((r) => Number(r.dataset.id)), breaks, version};
}
function appendRow(target, values, conflict=false) {
  const row = document.createElement("tr"); if (conflict) row.className = "conflict";
  for (const value of values) { const td = document.createElement("td"); td.textContent = value; row.append(td); }
  $(target).append(row);
}
$("preview").addEventListener("click", async () => {
  invalidate();
  if (!version || loadedDay !== $("day").value) { message("対象日の候補を読み込んでください。"); return; }
  if (!fileData) { message("Excelの読み込みが終わってから操作してください。"); return; }
  const data = payload(); $("preview").disabled = true;
  try {
    const result = await (await api("/api/daily-preview", data)).json();
    if (JSON.stringify(data) !== JSON.stringify(payload())) { message("入力が変わりました。確認し直してください。"); return; }
    $("placements").replaceChildren(); $("updates").replaceChildren();
    for (const p of result.placements) appendRow("placements", [p.role, p.name, p.shift, p.break, p.cell]);
    for (const u of result.updates) appendRow("updates", [u.name, u.cell, u.previous || "（空欄）", u.next || "（空欄に戻す）"], u.conflict);
    $("excluded").textContent = `今回は反映しない人（提出は保存済み）：${result.excluded.join("、") || "なし"}`;
    $("warnings").textContent = (result.date_missing ? "注意：Excelの日付は未設定です。対象日をExcel側で設定してください。\n" : "") + result.warnings.join("\n");
    reviewed = {data, token: result.plan_token}; $("result").hidden = false; message("");
    $("result").scrollIntoView({behavior:"smooth"});
  } catch (e) { message(e.message); }
  finally { $("preview").disabled = false; }
});
$("download").addEventListener("click", async () => {
  if (!reviewed || JSON.stringify(reviewed.data) !== JSON.stringify(payload())) { invalidate(); message("もう一度プレビューしてください。"); return; }
  $("download").disabled = true;
  try {
    const response = await api("/api/daily-export", {...reviewed.data, plan_token: reviewed.token, overwrite: $("overwrite").checked});
    const url = URL.createObjectURL(await response.blob()), link = document.createElement("a");
    link.href = url; link.download = `日別シフト_ベータ_${reviewed.data.day}.xlsx`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    message("コピーを出力しました。Excelで再計算して、日付・勤務帯・合計・プルダウンを確認してください。");
  } catch (e) { message(e.message); }
  finally { $("download").disabled = false; }
});
