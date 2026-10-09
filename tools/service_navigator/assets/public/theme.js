// Background analysis uses original pixels, independent of the page overlay.
export function analyzePixels(pixels) {
  const linear = (value) => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  };
  let count = 0;
  let luminance = 0;
  const buckets = new Map();
  for (let index = 0; index < pixels.length; index += 4) {
    if (pixels[index + 3] < 255) continue;
    const [r, g, b] = pixels.slice(index, index + 3);
    count += 1;
    const light = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
    luminance += light;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const saturation = max ? (max - min) / max : 0;
    if (light < 0.02 || light > 0.9 || saturation < 0.15) continue;
    const key = (r >> 4) * 256 + (g >> 4) * 16 + (b >> 4);
    const bucket = buckets.get(key) || { count: 0, r: 0, g: 0, b: 0, saturation: 0 };
    bucket.count += 1;
    bucket.r += r; bucket.g += g; bucket.b += b;
    bucket.saturation += saturation;
    buckets.set(key, bucket);
  }
  if (!count) return null;
  let best = null;
  let bestScore = -1;
  for (const bucket of buckets.values()) {
    const score = bucket.saturation / count;
    if (score > bestScore) { best = bucket; bestScore = score; }
  }
  const accent = best ? `#${[best.r, best.g, best.b].map((channel) => Math.round(channel / best.count).toString(16).padStart(2, "0")).join("")}` : null;
  return { theme: luminance / count >= 0.179 ? "light" : "dark", accent };
}

export function accentTextColor(color) {
  const hex = /^#[0-9a-f]{6}$/i.test(color || "") ? color.slice(1) : "4f7cff";
  const channels = [0, 2, 4].map((index) => parseInt(hex.slice(index, index + 2), 16));
  return analyzePixels(new Uint8ClampedArray([...channels, 255])).theme === "light" ? "#000000" : "#ffffff";
}

const analyses = new Map();
export function analyzeBackground(url) {
  if (!analyses.has(url)) {
    analyses.set(url, new Promise((resolve) => {
      const image = new Image();
      const timeout = setTimeout(() => finish(null), 15000);
      function finish(result) { clearTimeout(timeout); image.onload = null; image.onerror = null; resolve(result); }
      image.onerror = () => finish(null);
      image.onload = () => {
        try {
          const scale = Math.min(1, 64 / Math.max(image.naturalWidth, image.naturalHeight));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
          canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
          const context = canvas.getContext("2d", { willReadFrequently: true });
          context.drawImage(image, 0, 0, canvas.width, canvas.height);
          finish(analyzePixels(context.getImageData(0, 0, canvas.width, canvas.height).data));
        } catch { finish(null); }
      };
      image.src = url;
    }));
  }
  return analyses.get(url);
}

export function initializeTheme(appearance = {}) {
  const system = window.matchMedia("(prefers-color-scheme: light)");
  let background = null;
  function apply() {
    const theme = ["light", "dark"].includes(appearance.theme) ? appearance.theme
      : appearance.theme === "background" && background ? background.theme
      : system.matches ? "light" : "dark";
    document.body.dataset.snTheme = theme;
    const accent = appearance.accentColorMode === "background" && background?.accent
      ? background.accent : appearance.accentColor || "#4f7cff";
    document.body.style.setProperty("--sn-user-accent", accent);
    document.body.style.setProperty("--sn-on-accent", accentTextColor(accent));
  }
  apply();
  system.addEventListener("change", apply);
  if (appearance.theme !== "background" && appearance.accentColorMode !== "background") return;
  if (appearance.backgroundSource === "default") {
    background = { theme: "dark", accent: "#4f7cff" };
    apply();
  } else if (appearance.backgroundUrl) {
    analyzeBackground(appearance.backgroundUrl).then((result) => { background = result; apply(); });
  }
}

export function textIconColor(icon) {
  return icon?.iconColorMode === "theme" ? "var(--sn-accent)" : icon?.iconColor || "";
}
