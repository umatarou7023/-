document.getElementById("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const response = await fetch("/api/login", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({password: document.getElementById("password").value})});
    if (!response.ok) throw new Error((await response.json()).error);
    location.href = "/";
  } catch (error) { document.getElementById("error").textContent = error.message; }
});
