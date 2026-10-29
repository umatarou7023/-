/* IME入力中は触らず、確定後・フォーカス移動・送信時に時刻だけを整える。 */
window.ShiftInput = (() => {
  function clock(value) {
    let result = value.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/：/g, ":").trim();
    const match = result.match(/^([0-9]{1,2}):([0-9]{2})$/);
    if (match && Number(match[1]) < 24 && Number(match[2]) < 60) result = `${match[1].padStart(2, "0")}:${match[2]}`;
    return result;
  }
  function configureTimeInput(input) {
    input.type = "text";
    input.inputMode = "text";
    input.dataset.clock = "true";
    input.placeholder = "例 １７：００";
    input.pattern = "[0-9０-９]{1,2}[:：][0-9０-９]{2}";
    input.maxLength = 5;
    input.autocomplete = "off";
    input.spellcheck = false;
  }
  function normalizeTimeFields(root = document) {
    for (const input of root.querySelectorAll("input[data-clock]")) input.value = clock(input.value);
  }
  for (const event of ["blur", "change", "compositionend"]) document.addEventListener(event, (e) => {
    if (e.target.matches?.("input[data-clock]") && !e.isComposing) e.target.value = clock(e.target.value);
  }, true);
  document.querySelectorAll("input[data-clock]").forEach(configureTimeInput);
  return {clock, configureTimeInput, normalizeTimeFields};
})();
