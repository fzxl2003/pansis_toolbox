export function serviceName(item) {
  return (
    item.name ||
    item.displayName ||
    item.httpTitle ||
    item.serviceName ||
    "未知服务"
  );
}

export function healthLabel(item) {
  if (!item.healthMonitored) return "";
  return item.healthStatus === "healthy"
    ? "健康"
    : item.healthStatus === "unhealthy"
      ? "异常"
      : "尚未检测";
}

export function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}
