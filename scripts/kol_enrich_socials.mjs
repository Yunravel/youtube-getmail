import fs from "node:fs/promises";

const sourcePath = "D:/mail/output/kol-find/discovery_cache.json";
const outputPath = "D:/mail/output/kol-find/multiplatform_candidates.json";
const progressPath = "D:/mail/output/kol-find/social_enrichment_progress.json";
const headers = {
  "accept-language": "en-US,en;q=0.9",
  "user-agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
};

const positiveSeeds = [
  { platform: "Instagram", handle: "paultrillo", product: "Pippit", note: "需求表正向种子：director" },
  { platform: "X", handle: "PJaccetturo", product: "Pippit", note: "需求表正向种子：filmmaker" },
  { platform: "Instagram", handle: "rourke", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "Instagram", handle: "sebintel", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "Instagram", handle: "jadheshvp", product: "Pippit", note: "需求表正向种子：AI影视/叙事" },
  { platform: "Instagram", handle: "fiqri_fox", product: "Pippit", note: "需求表正向种子：AI影视/叙事" },
  { platform: "Instagram", handle: "reels_mon01", product: "Pippit", note: "需求表正向种子：AI影视/叙事" },
  { platform: "Instagram", handle: "ryann.ananta", product: "Pippit", note: "需求表正向种子：AI影视/叙事" },
  { platform: "Instagram", handle: "theimagehs", product: "Pippit", note: "需求表正向种子：AI影视/叙事" },
  { platform: "Instagram", handle: "soegimitro", product: "Pippit", note: "需求表正向种子：AI影视/叙事" },
  { platform: "Instagram", handle: "kentdhani", product: "Pippit", note: "需求表正向种子：AI影视/叙事" },
  { platform: "YouTube", handle: "sankyverse", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "YouTube", handle: "kingy-ai", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "YouTube", handle: "IvanKv", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "YouTube", handle: "MikkelLassalle", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "YouTube", handle: "Web-3-World", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "YouTube", handle: "CryptoBrosVortex", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "YouTube", handle: "jonlawedu", product: "Pippit", note: "需求表正向种子：AI工具教育" },
  { platform: "YouTube", handle: "TaylorCutFilms", product: "Pippit", note: "需求表正向种子：AI影视" },
];

function profileUrl(platform, handle) {
  if (platform === "Instagram") return `https://www.instagram.com/${handle}/`;
  if (platform === "TikTok") return `https://www.tiktok.com/@${handle}`;
  if (platform === "X") return `https://x.com/${handle}`;
  return `https://www.youtube.com/@${handle}`;
}

async function getText(url, attempt = 0) {
  try {
    const response = await fetch(url, { headers });
    if (response.ok) return response.text();
    throw new Error(`${response.status}`);
  } catch (error) {
    if (attempt < 3) {
      await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)));
      return getText(url, attempt + 1);
    }
    throw error;
  }
}

function decodedVariants(html) {
  const variants = new Set([html]);
  let current = html;
  for (let index = 0; index < 2; index++) {
    try {
      current = decodeURIComponent(
        current
          .replaceAll("\\u0026", "&")
          .replaceAll("\\/", "/")
          .replaceAll("&amp;", "&"),
      );
      variants.add(current);
    } catch {
      break;
    }
  }
  return [...variants];
}

function ownChannelText(html) {
  const parts = [];
  const descriptionMatches = [
    ...html.matchAll(
      /"description":"((?:\\.|[^"\\])*)","descriptionLabel"/g,
    ),
  ];
  if (descriptionMatches.length) {
    try {
      parts.push(JSON.parse(`"${descriptionMatches.at(-1)[1]}"`));
    } catch {
      parts.push(descriptionMatches.at(-1)[1]);
    }
  }
  for (const match of html.matchAll(
    /"channelExternalLinkViewModel":\{"title":\{"content":"[^"]*"\},"link":\{"content":"([^"]+)"/g,
  )) {
    parts.push(match[1]);
  }
  return parts.join("\n");
}

function normalizeHandle(value) {
  return value.replace(/^@/, "").replace(/[/?#].*$/, "").trim();
}

function extractProfiles(html) {
  const profiles = new Map();
  function add(platform, handle) {
    const cleaned = normalizeHandle(handle);
    if (!cleaned || cleaned.length > 64) return;
    const excluded = new Set([
      "accounts",
      "explore",
      "p",
      "reel",
      "reels",
      "stories",
      "share",
      "intent",
      "home",
      "search",
      "i",
      "hashtag",
    ]);
    if (excluded.has(cleaned.toLowerCase())) return;
    profiles.set(`${platform}:${cleaned.toLowerCase()}`, {
      platform,
      handle: cleaned,
      profile_url: profileUrl(platform, cleaned),
    });
  }

  for (const text of decodedVariants(html)) {
    for (const match of text.matchAll(
      /(?:https?:\/\/)?(?:www\.)?instagram\.com\/([A-Za-z0-9._]+)/gi,
    )) add("Instagram", match[1]);
    for (const match of text.matchAll(
      /(?:https?:\/\/)?(?:www\.)?tiktok\.com\/@([A-Za-z0-9._-]+)/gi,
    )) add("TikTok", match[1]);
    for (const match of text.matchAll(
      /(?:https?:\/\/)?(?:www\.)?(?:x|twitter)\.com\/([A-Za-z0-9_]+)/gi,
    )) add("X", match[1]);
  }
  return [...profiles.values()];
}

async function pool(items, concurrency, handler) {
  let cursor = 0;
  async function worker() {
    while (cursor < items.length) {
      const index = cursor++;
      await handler(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
}

async function main() {
  const source = JSON.parse(await fs.readFile(sourcePath, "utf8"));
  let progress = {};
  try {
    progress = JSON.parse(await fs.readFile(progressPath, "utf8"));
  } catch {
    progress = {};
  }

  let completed = 0;
  await pool(source, 6, async (item) => {
    const key = item.handle.toLowerCase();
    if (progress[key]) {
      completed++;
      return;
    }
    try {
      const html = await getText(`https://www.youtube.com${item.handle}/about`);
      progress[key] = {
        youtube_handle: item.handle.replace(/^\/@/, ""),
        youtube_url: `https://www.youtube.com${item.handle}`,
        about_url: `https://www.youtube.com${item.handle}/about`,
        products: item.products,
        discovery_queries: item.discoveryQueries,
        social_profiles: extractProfiles(ownChannelText(html)),
      };
    } catch {
      progress[key] = {
        youtube_handle: item.handle.replace(/^\/@/, ""),
        youtube_url: `https://www.youtube.com${item.handle}`,
        about_url: `https://www.youtube.com${item.handle}/about`,
        products: item.products,
        discovery_queries: item.discoveryQueries,
        social_profiles: [],
        fetch_status: "failed",
      };
    }
    completed++;
    if (completed % 100 === 0) {
      await fs.writeFile(progressPath, JSON.stringify(progress), "utf8");
      const socialCount = Object.values(progress).reduce(
        (sum, row) => sum + row.social_profiles.length,
        0,
      );
      console.log(`COMPLETED ${completed}/${source.length} SOCIAL_LINKS ${socialCount}`);
    }
  });
  await fs.writeFile(progressPath, JSON.stringify(progress), "utf8");

  const records = [];
  const seen = new Set();
  function add(record) {
    const key = `${record.platform}:${record.handle.toLowerCase()}`;
    if (seen.has(key)) return;
    seen.add(key);
    records.push(record);
  }
  for (const item of Object.values(progress)) {
    add({
      platform: "YouTube",
      handle: item.youtube_handle,
      profile_url: item.youtube_url,
      source_about_url: item.about_url,
      products: item.products,
      discovery_queries: item.discovery_queries,
      discovery_method: "YouTube关键词发现",
      linked_youtube: item.youtube_handle,
    });
    for (const social of item.social_profiles) {
      add({
        ...social,
        source_about_url: item.about_url,
        products: item.products,
        discovery_queries: item.discovery_queries,
        discovery_method: "YouTube公开简介外链",
        linked_youtube: item.youtube_handle,
      });
    }
  }
  for (const seed of positiveSeeds) {
    add({
      platform: seed.platform,
      handle: seed.handle,
      profile_url: profileUrl(seed.platform, seed.handle),
      source_about_url: "",
      products: [seed.product],
      discovery_queries: [seed.note],
      discovery_method: "需求表正向种子",
      linked_youtube: seed.platform === "YouTube" ? seed.handle : "",
    });
  }

  const counts = records.reduce((acc, row) => {
    acc[row.platform] = (acc[row.platform] || 0) + 1;
    return acc;
  }, {});
  await fs.writeFile(
    outputPath,
    JSON.stringify(
      {
        generated_at: new Date().toISOString(),
        source_youtube_candidates: source.length,
        records: records.length,
        platform_counts: counts,
        rows: records,
      },
      null,
      2,
    ),
    "utf8",
  );
  console.log(JSON.stringify({ outputPath, records: records.length, counts }));
}

await main();
