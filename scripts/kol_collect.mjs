import fs from "node:fs/promises";
import dns from "node:dns/promises";

const outputPath = "D:/mail/output/kol-find/kol_candidates.json";
const discoveryCachePath = "D:/mail/output/kol-find/discovery_cache.json";
const headers = {
  "accept-language": "en-US,en;q=0.9",
  "user-agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
};

const productTerms = {
  Dreamina: [
    "industrial design",
    "product design",
    "product visualization",
    "concept design",
    "3D modeling",
    "3D rendering",
    "Blender",
    "Blender AI",
    "Maya 3D",
    "Cinema 4D",
    "C4D motion design",
    "Houdini VFX",
    "ZBrush sculpting",
    "KeyShot rendering",
    "Fusion 360 design",
    "SolidWorks design",
    "Rhino 3D",
    "Unreal Engine art",
    "Unity game art",
    "game CG",
    "game cinematics",
    "game environment art",
    "character modeling",
    "environment modeling",
    "architectural visualization",
    "3D animation",
    "VFX artist",
    "CG artist",
    "digital art",
    "render challenge",
    "scientific illustration",
    "scientific visualization",
    "biomedical illustration",
    "engineering visualization",
    "text to 3D AI",
    "image to 3D AI",
    "generative 3D",
    "AI design tools",
    "AI image generation",
    "creative AI workflow",
  ],
  Pippit: [
    "AI filmmaking",
    "generative filmmaking",
    "AI short film",
    "cinematic AI video",
    "AI storyteller",
    "AI visual storytelling",
    "AI video workflow",
    "AI video tutorial",
    "AI film director",
    "commercial director",
    "filmmaking workflow",
    "video production tools",
    "Premiere Pro workflow",
    "Unreal Engine filmmaking",
    "Blender filmmaking",
    "virtual production",
    "AI creative workflow",
    "AI tool education",
    "generative AI education",
    "AI industry insights",
    "AI trends analysis",
    "AI case studies",
    "AI podcast",
    "generative AI podcast",
    "AI newsletter",
    "AI content creator",
    "AI advertising creative",
    "AI commercial filmmaking",
    "screenwriting AI",
    "AI animation filmmaking",
  ],
  Kimi: [
    "AI productivity",
    "office productivity",
    "AI search",
    "AI writing",
    "AI workflow",
    "AI for work",
    "AI product review",
    "AI news",
    "technology insights",
    "AI trends",
    "knowledge sharing",
    "study productivity",
    "career development",
    "educational content",
    "content creation workflow",
    "digital productivity",
    "future of work",
    "no code automation",
    "business automation",
    "research productivity",
    "note taking productivity",
    "remote work productivity",
    "creator productivity",
    "ChatGPT productivity",
  ],
  Dola: [
    "UK lifestyle creator",
    "UK healthy lifestyle",
    "UK fitness lifestyle",
    "UK healthy eating",
    "UK travel tips",
    "UK life hacks",
    "UK personal growth",
    "UK college life",
    "UK university study",
    "UK graduate student",
    "UK study tips",
    "UK study productivity",
    "UK academic research tips",
    "UK exam preparation",
    "UK student productivity",
    "UK work productivity",
    "UK office productivity",
    "UK corporate life",
    "UK career development",
    "UK remote work",
    "UK freelancing",
    "UK side hustle",
    "UK ecommerce creator",
    "UK digital marketing",
    "UK social media manager",
    "UK entrepreneurship",
    "UK content creator",
    "UK UGC creator",
    "UK short form video creator",
    "UK education creator",
    "UK career creator",
    "UK productivity creator",
    "UK storytelling creator",
    "UK product demo creator",
  ],
};

const genericModifiers = ["tutorial", "tips", "workflow", "creator"];
const regionModifiers = [
  "USA",
  "UK",
  "Canada",
  "Europe",
  "Germany",
  "France",
  "Netherlands",
  "Spain",
  "Italy",
  "Sweden",
  "Norway",
  "Denmark",
  "Finland",
  "Portugal",
  "Brazil",
  "Mexico",
  "Argentina",
  "Colombia",
  "Chile",
  "Japan",
  "Korea",
];

const allowedByProduct = {
  Dreamina: new Set([
    "United States",
    "United Kingdom",
    "Canada",
    "Netherlands",
    "Spain",
    "France",
    "Italy",
    "Switzerland",
    "Germany",
    "Denmark",
    "Norway",
    "Finland",
    "Sweden",
    "Portugal",
    "Brazil",
    "Mexico",
    "Argentina",
    "Colombia",
    "Chile",
  ]),
  Pippit: new Set([
    "United States",
    "United Kingdom",
    "Canada",
    "Netherlands",
    "Spain",
    "France",
    "Italy",
    "Switzerland",
    "Germany",
    "Denmark",
    "Norway",
    "Finland",
    "Sweden",
    "Portugal",
    "Brazil",
    "Mexico",
    "Argentina",
    "Colombia",
    "Chile",
    "Japan",
    "South Korea",
  ]),
  Kimi: new Set([
    "United States",
    "United Kingdom",
    "Canada",
    "Netherlands",
    "Spain",
    "France",
    "Italy",
    "Switzerland",
    "Germany",
    "Denmark",
    "Norway",
    "Finland",
    "Sweden",
    "Portugal",
  ]),
  Dola: new Set(["United Kingdom"]),
};

const countryAliases = [
  ["United Kingdom", /\b(united kingdom|uk|british|england|scotland|wales|london)\b/i],
  ["United States", /\b(united states|usa|u\.s\.|american|new york|los angeles|california)\b/i],
  ["Canada", /\b(canada|canadian|toronto|vancouver)\b/i],
  ["Netherlands", /\b(netherlands|dutch|amsterdam)\b/i],
  ["Spain", /\b(spain|spanish|madrid|barcelona)\b/i],
  ["France", /\b(france|french|paris)\b/i],
  ["Italy", /\b(italy|italian|milan|rome)\b/i],
  ["Switzerland", /\b(switzerland|swiss|zurich)\b/i],
  ["Germany", /\b(germany|german|berlin|munich)\b/i],
  ["Denmark", /\b(denmark|danish|copenhagen)\b/i],
  ["Norway", /\b(norway|norwegian|oslo)\b/i],
  ["Finland", /\b(finland|finnish|helsinki)\b/i],
  ["Sweden", /\b(sweden|swedish|stockholm)\b/i],
  ["Portugal", /\b(portugal|portuguese|lisbon)\b/i],
  ["Brazil", /\b(brazil|brazilian|sao paulo|rio de janeiro)\b/i],
  ["Mexico", /\b(mexico|mexican|mexico city)\b/i],
  ["Argentina", /\b(argentina|argentinian|buenos aires)\b/i],
  ["Colombia", /\b(colombia|colombian|bogota)\b/i],
  ["Chile", /\b(chile|chilean|santiago)\b/i],
  ["Japan", /\b(japan|japanese|tokyo)\b/i],
  ["South Korea", /\b(south korea|korean|seoul)\b/i],
];

const excludedHandles = new Set(
  [
    "anvi__lifestyle",
    "inteligencianapratica_",
    "ceylinvibes_",
    "ugc_with_meg",
    "ana_ai_finds",
    "scalewithimanshu",
    "fatihlyfe",
    "growithrobin",
    "gokceercan",
    "davidugc31",
    "nickintech1",
    "kilic_usastyle",
  ].map((value) => `/@${value.toLowerCase()}`),
);

const badEmailFragments = [
  "example.com",
  "email.com",
  "sentry",
  "wixpress",
  "youtube.com",
  "google.com",
  "schema.org",
  "domain.com",
  "yourname@",
];

function makeQueries() {
  const entries = [];
  for (const [product, terms] of Object.entries(productTerms)) {
    for (let index = 0; index < terms.length; index++) {
      const term = terms[index];
      entries.push({ product, query: term });
      if (product !== "Dola") {
        entries.push({
          product,
          query: `${term} ${genericModifiers[index % genericModifiers.length]}`,
        });
        entries.push({
          product,
          query: `${regionModifiers[index % regionModifiers.length]} ${term}`,
        });
      } else {
        entries.push({ product, query: `${term} tips` });
      }
    }
  }
  return entries;
}

async function getText(url, attempt = 0) {
  try {
    const response = await fetch(url, { headers });
    if (response.ok) return response.text();
    if ((response.status === 429 || response.status >= 500) && attempt < 3) {
      await new Promise((resolve) => setTimeout(resolve, 1200 * (attempt + 1)));
      return getText(url, attempt + 1);
    }
    throw new Error(`${response.status} ${url}`);
  } catch (error) {
    if (attempt < 3) {
      await new Promise((resolve) => setTimeout(resolve, 1200 * (attempt + 1)));
      return getText(url, attempt + 1);
    }
    throw error;
  }
}

function handlesFromSearch(html) {
  return [
    ...new Set(
      [...html.matchAll(/canonicalBaseUrl":"(\/@[^"]+)"/g)].map(
        (match) => match[1],
      ),
    ),
  ];
}

function decodeHtml(value = "") {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&#39;", "'")
    .replaceAll("&quot;", '"')
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("\\u0026", "&")
    .replaceAll("\\u0027", "'")
    .replaceAll("\\n", "\n")
    .replaceAll("\\/", "/");
}

function metaContent(html, name) {
  const patterns = [
    new RegExp(`<meta[^>]+name="${name}"[^>]+content="([^"]*)"`, "i"),
    new RegExp(`<meta[^>]+content="([^"]*)"[^>]+name="${name}"`, "i"),
  ];
  for (const pattern of patterns) {
    const match = html.match(pattern);
    if (match) return decodeHtml(match[1]);
  }
  return "";
}

function fullChannelDescription(html) {
  const matches = [
    ...html.matchAll(
      /"description":"((?:\\.|[^"\\])*)","descriptionLabel"/g,
    ),
  ];
  if (matches.length) {
    try {
      return decodeHtml(JSON.parse(`"${matches.at(-1)[1]}"`));
    } catch {
      return decodeHtml(matches.at(-1)[1]);
    }
  }
  return metaContent(html, "description");
}

function extractEmails(text) {
  const pattern =
    /[A-Z0-9][A-Z0-9._%+-]*@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+/gi;
  return [
    ...new Set(
      (text.match(pattern) || [])
        .map((email) => email.toLowerCase())
        .filter(
          (email) =>
            email.length <= 254 &&
            !email.includes("..") &&
            !badEmailFragments.some((fragment) => email.includes(fragment)),
        ),
    ),
  ];
}

function explicitCountry(html) {
  const matches = [...html.matchAll(/"country":"([^"]+)"/g)].map(
    (match) => decodeHtml(match[1]),
  );
  return matches.at(-1) || "";
}

function inferredCountry(description, title) {
  const haystack = `${title}\n${description}`;
  for (const [country, pattern] of countryAliases) {
    if (pattern.test(haystack)) return country;
  }
  return "";
}

function fieldFromHtml(html, field) {
  const matches = [...html.matchAll(new RegExp(`"${field}":"([^"]+)"`, "g"))];
  return matches.length ? decodeHtml(matches.at(-1)[1]) : "";
}

function businessSignal(description, email) {
  const at = description.toLowerCase().indexOf(email.toLowerCase());
  const context =
    at >= 0
      ? description.slice(Math.max(0, at - 100), at + email.length + 100)
      : description;
  return /\b(business|sponsor|collab|partnership|management|inquir|contact|work with|brand|booking|press)\b/i.test(
    context,
  );
}

function channelType(title, description) {
  const text = `${title} ${description}`.toLowerCase();
  if (/\b(official|corporation|company|university|school|institute|news network)\b/.test(text))
    return "可能为机构/官方";
  if (/\b(podcast|newsletter|media)\b/.test(text)) return "媒体/播客";
  return "个人创作者/工作室";
}

async function mxStatus(domain) {
  try {
    const records = await dns.resolveMx(domain);
    return records.length ? "MX有效" : "无MX记录";
  } catch {
    return "无MX记录";
  }
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
  await fs.mkdir("D:/mail/output/kol-find", { recursive: true });
  const queries = makeQueries();
  const candidateMap = new Map();
  let searched = 0;

  try {
    const cached = JSON.parse(await fs.readFile(discoveryCachePath, "utf8"));
    for (const item of cached) {
      candidateMap.set(item.handle.toLowerCase(), {
        handle: item.handle,
        products: new Set(item.products),
        discoveryQueries: new Set(item.discoveryQueries),
      });
    }
    console.log(`DISCOVERY_CACHE candidates=${candidateMap.size}`);
  } catch {
    await pool(queries, 3, async ({ product, query }) => {
      try {
        const html = await getText(
          `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`,
        );
        for (const handle of handlesFromSearch(html)) {
          const normalized = handle.toLowerCase();
          if (excludedHandles.has(normalized)) continue;
          if (!candidateMap.has(normalized)) {
            candidateMap.set(normalized, {
              handle,
              products: new Set(),
              discoveryQueries: new Set(),
            });
          }
          candidateMap.get(normalized).products.add(product);
          candidateMap.get(normalized).discoveryQueries.add(query);
        }
      } catch (error) {
        console.error(`SEARCH_ERROR ${query}: ${error.message}`);
      }
      searched++;
      if (searched % 50 === 0)
        console.log(`SEARCHED ${searched}/${queries.length} CANDIDATES ${candidateMap.size}`);
    });
    await fs.writeFile(
      discoveryCachePath,
      JSON.stringify(
        [...candidateMap.values()].map((item) => ({
          handle: item.handle,
          products: [...item.products],
          discoveryQueries: [...item.discoveryQueries],
        })),
        null,
        2,
      ),
      "utf8",
    );
  }

  const candidates = [...candidateMap.values()];
  console.log(`DISCOVERY_DONE queries=${queries.length} candidates=${candidates.length}`);

  const rows = [];
  let inspected = 0;
  await pool(candidates, 6, async (candidate) => {
    const sourceUrl = `https://www.youtube.com${candidate.handle}/about`;
    try {
      const html = await getText(sourceUrl);
      const title = metaContent(html, "title").replace(/ - YouTube$/i, "");
      const description = fullChannelDescription(html);
      const emails = extractEmails(description);
      if (!emails.length) return;

      const statedCountry = explicitCountry(html);
      const inferred = statedCountry ? "" : inferredCountry(description, title);
      const country = statedCountry || inferred || "Unknown";
      const countryConfidence = statedCountry
        ? "平台声明"
        : inferred
          ? "简介推断"
          : "未知";
      const products = [...candidate.products];
      const qualifiedProducts = products.filter(
        (product) =>
          country === "Unknown" || allowedByProduct[product].has(country),
      );
      if (!qualifiedProducts.length) return;

      for (const email of emails) {
        rows.push({
          platform: "YouTube",
          handle: candidate.handle.slice(2),
          display_name: title,
          profile_url: `https://www.youtube.com${candidate.handle}`,
          followers: fieldFromHtml(html, "subscriberCountText"),
          recent_average_views: "",
          country_region: country,
          country_confidence: countryConfidence,
          language: products.includes("Kimi") ? "English required / sampled by query" : "",
          content_niche: [...candidate.discoveryQueries].slice(0, 6).join("; "),
          fit_products: qualifiedProducts.join(", "),
          primary_recommendation: qualifiedProducts[0],
          account_type: channelType(title, description),
          contact_type: businessSignal(description, email)
            ? "公开商务邮箱"
            : "公开邮箱",
          contact: email,
          source_url: sourceUrl,
          source_excerpt: description
            .slice(
              Math.max(0, description.toLowerCase().indexOf(email) - 80),
              description.toLowerCase().indexOf(email) + email.length + 100,
            )
            .replace(/\s+/g, " ")
            .trim(),
          data_updated_at: new Date().toISOString().slice(0, 10),
        });
      }
    } catch (error) {
      console.error(`CHANNEL_ERROR ${candidate.handle}: ${error.message}`);
    } finally {
      inspected++;
      if (inspected % 100 === 0)
        console.log(`INSPECTED ${inspected}/${candidates.length} RAW_EMAIL_ROWS ${rows.length}`);
    }
  });

  const domains = [
    ...new Set(rows.map((row) => row.contact.split("@").at(-1))),
  ];
  const domainStatus = new Map();
  await pool(domains, 30, async (domain) => {
    domainStatus.set(domain, await mxStatus(domain));
  });
  for (const row of rows) row.email_validation = domainStatus.get(row.contact.split("@").at(-1));

  const uniqueByEmail = new Map();
  for (const row of rows) {
    if (row.email_validation !== "MX有效") continue;
    const existing = uniqueByEmail.get(row.contact);
    if (
      !existing ||
      (row.contact_type === "公开商务邮箱" &&
        existing.contact_type !== "公开商务邮箱")
    ) {
      uniqueByEmail.set(row.contact, row);
    }
  }

  const result = {
    generated_at: new Date().toISOString(),
    query_count: queries.length,
    candidate_count: candidates.length,
    raw_email_rows: rows.length,
    unique_mx_valid_emails: uniqueByEmail.size,
    rows: [...uniqueByEmail.values()],
  };
  await fs.writeFile(outputPath, JSON.stringify(result, null, 2), "utf8");
  console.log(
    `DONE raw=${rows.length} unique_mx_valid=${uniqueByEmail.size} output=${outputPath}`,
  );
}

await main();
