export async function ownerApi(path, method = "GET", body) {
  const init = { method, credentials: "include" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(`/api/tools/service-navigator${path}`, init);
  if (!response.ok)
    throw new Error(
      (await response.json().catch(() => ({}))).error?.message || "保存失败",
    );
  return response.json();
}
