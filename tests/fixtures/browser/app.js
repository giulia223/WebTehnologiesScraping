// Proves JavaScript ran: a cookie and an element that the static HTML does not contain.
document.cookie = "fixture_seen=1; path=/";
document.addEventListener("DOMContentLoaded", () => {
  const p = document.createElement("p");
  p.id = "added-by-js";
  p.textContent = "rendered by JavaScript";
  document.body.appendChild(p);
});
