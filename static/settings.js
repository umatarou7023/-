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
