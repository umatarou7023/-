const toggle = document.getElementById("repair-blank-formulas");
const saveButton = document.getElementById("save-excel-settings");
const state = document.getElementById("repair-state");
const status = document.getElementById("save-status");
let saved = null;

async function requestSettings(data) {
  const response = await fetch("/api/excel-settings", data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  if (response.status === 401) { location.href = "/login"; throw new Error("ログインしてください。"); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "設定を保存できませんでした。");
  return result.settings;
}
function showState() {
  state.textContent = toggle.checked ? "ON" : "OFF";
  saveButton.disabled = saved === null || toggle.checked === saved;
}
async function load() {
  try {
    const settings = await requestSettings();
    saved = settings.repair_blank_formulas;
    toggle.checked = saved; toggle.disabled = false; showState();
    status.textContent = `保存済み：${saved ? "ON" : "OFF"}。次回もこの設定を使います。`;
  } catch (e) { status.textContent = e.message + " 画面を更新して再度お試しください。"; }
}
toggle.addEventListener("change", () => {
  showState();
  status.textContent = toggle.checked === saved ? "保存済みの設定です。" : "変更は未保存です。「設定を保存」を押してください。";
});
document.getElementById("excel-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saved === null) return;
  saveButton.disabled = true; toggle.disabled = true;
  try {
    const settings = await requestSettings({repair_blank_formulas: toggle.checked});
    saved = settings.repair_blank_formulas; toggle.checked = saved;
    status.textContent = `${saved ? "ON" : "OFF"}で保存しました。日別管理画面で候補を読み込むと適用されます。次回もこの設定を使います。`;
  } catch (e) { status.textContent = e.message; }
  finally { toggle.disabled = false; showState(); }
});
load();

const dataNode = (id) => document.getElementById(id);
let retentionReview = null;
const deletionReviews = {submission: null, roster: null};

async function dataRequest(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  if (response.status === 401) { location.href = "/login"; throw new Error("ログインしてください。"); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "データを処理できませんでした。");
  return result;
}
function autoPayload() {
  return {retention_days: Number(dataNode("retention-days").value),
    auto_months: [...dataNode("retention-months").querySelectorAll("input:checked")].map((node) => node.value).sort()};
}
function invalidateRetention() {
  retentionReview = null; dataNode("retention-review").hidden = true; dataNode("retention-confirmed").checked = false;
}
function monthSwitch(month, enabled) {
  const label = document.createElement("label"); label.className = "check";
  const input = document.createElement("input"); input.type = "checkbox"; input.value = month; input.checked = enabled;
  const text = document.createElement("span");
  const update = () => { text.textContent = `${month} の希望・履歴を自動削除：${input.checked ? "ON" : "OFF"}`; };
  input.addEventListener("change", update); update(); label.append(input, text); return label;
}
async function loadRetention() {
  try {
    const info = await dataRequest("/api/retention-settings");
    dataNode("retention-days").value = info.settings.retention_days;
    dataNode("retention-months").replaceChildren(...info.months.map((item) => monthSwitch(item.month, item.enabled)));
    for (const id of ["retention-days", "retention-add-month", "retention-preview"]) dataNode(id).disabled = false;
    dataNode("submission-delete-month").value ||= info.today.slice(0, 7);
    dataNode("retention-add-value").value ||= info.today.slice(0, 7);
    dataNode("retention-status").textContent = `保存済み：半月の最終日から${info.settings.retention_days}日。自動削除ONの月：${info.settings.auto_months.join("、") || "なし（すべてOFF）"}。` +
      (info.last_run ? `\n最終確認：${info.last_run.checked_at} ／ 削除した希望：合計${info.last_run.deleted.person_days}日分。` : "");
    invalidateRetention();
  } catch (error) { dataNode("retention-status").textContent = error.message; }
}
dataNode("retention-form").addEventListener("input", invalidateRetention);
dataNode("retention-add-month").addEventListener("click", () => {
  const month = dataNode("retention-add-value").value;
  if (!month) { dataNode("retention-status").textContent = "追加する対象月を選んでください。"; return; }
  if (![...dataNode("retention-months").querySelectorAll("input")].some((node) => node.value === month)) {
    dataNode("retention-months").append(monthSwitch(month, false));
  }
  invalidateRetention();
});
function summaryText(summary) {
  return `${summary.people}人・希望は合計${summary.person_days}日分、過去の再提出を含む履歴は${summary.history_entries}日分`;
}
dataNode("retention-form").addEventListener("submit", async (event) => {
  event.preventDefault(); invalidateRetention();
  const payload = autoPayload(); dataNode("retention-preview").disabled = true;
  try {
    const result = await dataRequest("/api/retention-preview", payload);
    if (JSON.stringify(payload) !== JSON.stringify(autoPayload())) return;
    retentionReview = {payload, token: result.preview_token};
    dataNode("retention-summary").textContent = `半月の最終日から${payload.retention_days}日。ONの月：${payload.auto_months.join("、") || "なし"}。\n` +
      result.schedule.map((item) => `${item.month}：前半は${item.first}、後半は${item.second}に削除対象`).join("\n") +
      `\n保存時に期限を過ぎているため削除する対象：${summaryText(result.summary)}。`;
    dataNode("retention-confirm-wrap").hidden = result.summary.history_entries === 0;
    dataNode("retention-save").disabled = result.summary.history_entries > 0;
    dataNode("retention-review").hidden = false;
  } catch (error) { dataNode("retention-status").textContent = error.message; }
  finally { dataNode("retention-preview").disabled = false; }
});
dataNode("retention-confirmed").addEventListener("change", () => { dataNode("retention-save").disabled = !dataNode("retention-confirmed").checked; });
dataNode("retention-save").addEventListener("click", async () => {
  if (!retentionReview || JSON.stringify(retentionReview.payload) !== JSON.stringify(autoPayload())) return;
  const review = retentionReview; dataNode("retention-save").disabled = true;
  const controls = [...dataNode("retention-form").elements, dataNode("retention-confirmed")];
  for (const node of controls) node.disabled = true;
  try {
    const result = await dataRequest("/api/retention-settings", {...review.payload, preview_token: review.token, confirmed: dataNode("retention-confirmed").checked});
    await loadRetention();
    dataNode("retention-status").textContent += `\n設定を保存しました。今回削除した対象：${summaryText(result.deleted)}。`;
  } catch (error) { invalidateRetention(); dataNode("retention-status").textContent = error.message; }
  finally { for (const node of controls) if (node.isConnected) node.disabled = false; }
});
async function loadDeletionRoster() {
  try {
    const result = await dataRequest("/api/roster"); const labels = [];
    for (const [role, names] of Object.entries(result.roster)) for (const name of names) {
      const label = document.createElement("label"); label.className = "check";
      const input = document.createElement("input"); input.type = "checkbox"; input.value = name;
      const text = document.createElement("span"); text.textContent = `${role === "kitchen" ? "キッチン" : "ホール"}：${name}`;
      label.append(input, text); labels.push(label);
    }
    dataNode("roster-delete-list").replaceChildren(...labels);
    if (!labels.length) dataNode("roster-delete-list").textContent = "登録されているスタッフはいません。";
    dataNode("roster-delete-all").checked = false;
    dataNode("roster-delete-all").disabled = dataNode("roster-delete-preview").disabled = !labels.length;
  } catch (error) { dataNode("roster-delete-status").textContent = error.message; }
}
function deletionPayload(kind) {
  return kind === "submission" ? {kind: "submissions", month: dataNode("submission-delete-month").value, part: dataNode("submission-delete-part").value} :
    {kind: "roster", names: [...dataNode("roster-delete-list").querySelectorAll("input:checked")].map((node) => node.value)};
}
function invalidateDeletion(kind) {
  deletionReviews[kind] = null; dataNode(`${kind}-delete-review`).hidden = true;
  dataNode(`${kind}-delete-confirmed`).checked = false; dataNode(`${kind}-delete-execute`).disabled = true;
}
dataNode("roster-delete-all").addEventListener("change", () => {
  for (const input of dataNode("roster-delete-list").querySelectorAll("input")) input.checked = dataNode("roster-delete-all").checked;
  invalidateDeletion("roster");
});
for (const kind of ["submission", "roster"]) {
  const form = dataNode(`${kind}-delete-form`);
  form.addEventListener("input", () => invalidateDeletion(kind));
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); invalidateDeletion(kind);
    const payload = deletionPayload(kind), button = form.querySelector('button[type="submit"]'); button.disabled = true;
    try {
      const result = await dataRequest("/api/data-deletion-preview", payload);
      if (JSON.stringify(payload) !== JSON.stringify(deletionPayload(kind))) return;
      deletionReviews[kind] = {payload, token: result.preview_token};
      const text = kind === "submission" ? `${result.target.start}〜${result.target.end}：${summaryText(result.summary)}。この期間の変更履歴も削除します。` :
        `${payload.names.join("、")}：${result.summary.people}人をスタッフ一覧から削除します。提出された希望・履歴は残します。`;
      dataNode(`${kind}-delete-summary`).textContent = text;
      dataNode(`${kind}-delete-review`).hidden = false;
      if (!result.summary.people) { invalidateDeletion(kind); dataNode(`${kind}-delete-status`).textContent = "削除するデータはありません。"; }
    } catch (error) { dataNode(`${kind}-delete-status`).textContent = error.message; }
    finally { button.disabled = false; }
  });
  dataNode(`${kind}-delete-confirmed`).addEventListener("change", () => {
    dataNode(`${kind}-delete-execute`).disabled = !deletionReviews[kind] || !dataNode(`${kind}-delete-confirmed`).checked;
  });
  dataNode(`${kind}-delete-execute`).addEventListener("click", async () => {
    const review = deletionReviews[kind];
    if (!review || JSON.stringify(review.payload) !== JSON.stringify(deletionPayload(kind))) return;
    dataNode(`${kind}-delete-execute`).disabled = true;
    try {
      const result = await dataRequest("/api/data-delete", {...review.payload, preview_token: review.token, confirmed: dataNode(`${kind}-delete-confirmed`).checked});
      invalidateDeletion(kind);
      dataNode(`${kind}-delete-status`).textContent = kind === "submission" ? `削除しました：${summaryText(result.deleted)}。` : `スタッフ一覧から${result.deleted.people}人を削除しました。`;
      if (kind === "roster") await loadDeletionRoster();
    } catch (error) { invalidateDeletion(kind); dataNode(`${kind}-delete-status`).textContent = error.message; }
  });
}
loadRetention(); loadDeletionRoster();
