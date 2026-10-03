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
updateClock();
