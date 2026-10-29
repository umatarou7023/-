const $ = (id) => document.getElementById(id);
const key = new URLSearchParams(location.search).get("key") || "";
const today = new Date();
$("month").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
function message(text) { $("status").textContent = text; $("status").hidden = !text; }
function renderDays() {
  const [year, month] = $("month").value.split("-").map(Number);
  $("days").replaceChildren();
  $("confirm").checked = false;
  if (!year || !month) return;
  for (let day = 1; day <= new Date(year, month, 0).getDate(); day++) {
    const row = document.createElement("div"); row.className = "day-row";
    row.dataset.date = `${$("month").value}-${String(day).padStart(2, "0")}`;
    const title = document.createElement("strong");
    title.textContent = `${month}/${day}（${"日月火水木金土"[new Date(year, month - 1, day).getDay()]}）`;
    const mode = document.createElement("select");
    mode.setAttribute("aria-label", `${month}月${day}日の希望`);
    mode.append(new Option("未入力", ""), new Option("出勤可", "work"), new Option("休み", "off"));
    const start = document.createElement("input"); ShiftInput.configureTimeInput(start); start.value = "17:00"; start.disabled = true;
    start.setAttribute("aria-label", `${month}月${day}日の開始時刻`);
    const end = document.createElement("input"); ShiftInput.configureTimeInput(end); end.value = "22:00"; end.disabled = true;
    end.setAttribute("aria-label", `${month}月${day}日の終了時刻`);
    mode.addEventListener("change", () => { start.disabled = end.disabled = mode.value !== "work"; start.required = end.required = mode.value === "work"; $("confirm").checked = false; });
    row.append(title, mode, start, end); $("days").append(row);
  }
}
$("month").addEventListener("change", () => {
  // 月を変えた際に入力を黙って捨てない。
  if ([...$("days").querySelectorAll("select")].some((s) => s.value) && !window.confirm("対象月を変えると、入力中の日別の希望が消えます。変更しますか？")) {
    $("month").value = $("days").firstElementChild.dataset.date.slice(0, 7); return;
  }
  renderDays();
});
renderDays();
if (!key) { message("グループLINEで案内された提出リンクから開いてください。"); $("send").disabled = true; }
$("submit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  ShiftInput.normalizeTimeFields();
  const days = [...$("days").children].flatMap((row) => {
    const mode = row.querySelector("select").value;
    if (!mode) return [];
    const times = row.querySelectorAll("input");
    return [{date: row.dataset.date, shift: mode === "off" ? "休み" : `${times[0].value}-${times[1].value}`}];
  });
  if (!days.length) { message("1日以上のシフトを選んでください。"); return; }
  if (!window.confirm(`${$("name").value}さんの ${$("month").value} の希望 ${days.length}日分を提出します。よろしいですか？`)) return;
  $("send").disabled = true;
  try {
    const response = await fetch("/api/submit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({key, name: $("name").value, month: $("month").value, days})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    message(`提出できました！ 受付番号：${result.receipt} ／ ${result.count}日分。この画面を閉じて大丈夫です。修正する場合は、全日分の希望をもう一度提出してください。`);
    $("confirm").checked = false;
  } catch (error) { message(error.message || "通信できませんでした。接続を確認してください。"); }
  finally { $("send").disabled = false; $("status").scrollIntoView({behavior: "smooth", block: "center"}); }
});
