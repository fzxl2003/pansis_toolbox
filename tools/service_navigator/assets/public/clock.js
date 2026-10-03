export function updateClock() {
  const now = new Date();
  const clockTime = document.querySelector("[data-clock-time]");
  const clockDate = document.querySelector("[data-clock-date]");
  if (clockTime)
    clockTime.textContent = new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(now);
  if (clockDate)
    clockDate.textContent = new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
      weekday: "long",
    }).format(now);
}
function scheduleClockUpdate() {
  updateClock();
  // Align updates to the next real second so the display never drifts.
  window.setTimeout(scheduleClockUpdate, 1000 - (Date.now() % 1000) + 10);
}

scheduleClockUpdate();
