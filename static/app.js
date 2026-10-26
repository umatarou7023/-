const $ = (id) => document.getElementById(id);
const today = new Date();
$("month").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
let fileData = "";
let reviewed = null;
let loading = false;
let inviteUrl = "";

async function loadSettings() {
  try {
    const response = await fetch("/api/settings");
    if (response.status === 401) { location.href = "/login"; return; }
    if (!response.ok) throw new Error("設定を読み込めませんでした。");
    const settings = await response.json();
    inviteUrl = settings.invite_url;
    $("invite-url").value = inviteUrl;
    $("mode-note").textContent = settings.public ? "公開モード：提出リンクをグループLINEに送れます。Excelはバイト先のPCから選んでください。" : "お試しモード：提出リンクはこのPCでだけ開けます。スマホに配るには、公開サーバーへの設置が必要です。READMEの公開手順を確認してください。";
    $("logout").hidden = !settings.public;
  } catch (error) { message(error.message); }
}
loadSettings();
$("copy-link").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(inviteUrl); message("提出リンクをコピーしました。"); }
  catch { $("invite-url").select(); message("リンク欄を選択しました。手動でコピーしてください。"); }
});
$("open-form").addEventListener("click", () => { if (inviteUrl) window.open(inviteUrl, "_blank", "noopener,noreferrer"); });
$("logout").addEventListener("click", async () => { await request("/api/logout", {}); location.href = "/login"; });
$("load-submissions").addEventListener("click", async () => {
  if ($("text").value.trim() && !window.confirm("入力欄の内容を、対象月の最新の提出内容に置き換えますか？")) return;
  const month = $("month").value;
  try {
    const response = await fetch(`/api/submissions?month=${encodeURIComponent(month)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (month !== $("month").value) return;
    if (!result.people.length) { message("この月の提出はまだありません。入力欄は変更していません。"); return; }
    $("text").value = result.text;
    $("submission-summary").textContent = result.people.map((p) => `${p.name}：${p.count}日分（${new Date(p.submitted_at).toLocaleString("ja-JP")}）`).join(" / ");
    invalidate(); message(`${result.people.length}人分の提出を読み込みました。`);
  } catch (error) { message(error.message); }
});

function message(text) {
  $("status").textContent = text;
  $("status").hidden = !text;
}
function invalidate() {
  reviewed = null;
  $("result").hidden = true;
  $("overwrite").checked = false;
}
function payload() {
  return { file: fileData, month: $("month").value, sheet: $("sheet").value,
    header_row: $("header-row").value, first_name_row: $("first-name-row").value,
    name_col: $("name-col").value, date_col: $("date-col").value, text: $("text").value };
}
async function request(path, data) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || "処理できませんでした。");
  }
  return response;
}
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("form").addEventListener("input", (event) => {
  if (event.target.id !== "overwrite") { invalidate(); message(""); }
});
$("file").addEventListener("change", async () => {
  const file = $("file").files[0];
  fileData = "";
  $("sheet").replaceChildren(new Option("Excelを選ぶと表示されます", ""));
  if (!file) return;
  try {
    if (!file.name.toLowerCase().endsWith(".xlsx") || file.size > 8 * 1024 * 1024) throw new Error("8MB以下の.xlsxファイルを選んでください。");
    loading = true;
    message("Excelを読み込んでいます…");
    const encoded = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = () => reject(new Error("ファイルを読み込めませんでした。"));
      reader.readAsDataURL(file);
    });
    const result = await (await request("/api/inspect", {file: encoded})).json();
    if ($("file").files[0] !== file) return;
    fileData = encoded;
    $("sheet").replaceChildren(...result.sheets.map((name) => new Option(name, name)));
    message("");
  } catch (error) { message(error.message); }
  finally { loading = false; }
});
$("example").addEventListener("click", () => {
  const month = Number($("month").value.split("-")[1]);
  if (!month) { message("対象月を選んでください。"); return; }
  $("text").value = `山田 太郎 ${month}/20 17:00-22:00\n佐藤 花子 ${month}/21 休み`;
  invalidate(); message("");
});
$("sample").addEventListener("click", async () => {
  try {
    const response = await fetch(`/api/sample?month=${encodeURIComponent($("month").value)}`);
    if (!response.ok) throw new Error((await response.json()).error);
    saveBlob(await response.blob(), `お試しシフト_${$("month").value}.xlsx`);
  } catch (error) { message(error.message); }
});
$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  invalidate();
  if (loading || !fileData) { message("Excelの読み込みが完了してから操作してください。"); return; }
  const data = payload();
  $("preview").disabled = true;
  message("反映先を確認しています…");
  try {
    const result = await (await request("/api/preview", data)).json();
    if (JSON.stringify(data) !== JSON.stringify(payload())) { message("入力が変わりました。もう一度確認してください。"); return; }
    $("rows").replaceChildren();
    for (const item of result.rows) {
      const row = document.createElement("tr");
      if (item.conflict) row.className = "conflict";
      for (const text of [item.name, item.date, item.cell, item.previous || "（空欄）", item.shift]) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
      }
      $("rows").append(row);
    }
    $("count").textContent = `${data.sheet} シートに ${result.rows.length} 件を反映します。氏名・日付・セルを確認してください。`;
    reviewed = data;
    $("result").hidden = false;
    message("");
    $("result").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) { message(error.message); }
  finally { $("preview").disabled = false; }
});
$("download").addEventListener("click", async () => {
  if (!reviewed || JSON.stringify(reviewed) !== JSON.stringify(payload())) { invalidate(); message("入力が変わりました。もう一度反映内容を確認してください。"); return; }
  $("download").disabled = true;
  try {
    const response = await request("/api/export", {...reviewed, overwrite: $("overwrite").checked});
    saveBlob(await response.blob(), `シフト反映済み_${reviewed.month}.xlsx`);
    message("反映済みExcelをダウンロードしました。Excelで内容を確認してください。");
  } catch (error) { message(error.message); }
  finally { $("download").disabled = false; }
});
