const queries = [
  "AI filmmaking tutorial",
  "AI video workflow",
  "generative AI filmmaking",
  "Blender tutorial",
  "3D modeling tutorial",
  "industrial design tutorial",
  "AI productivity tools",
  "AI writing workflow",
  "UK study productivity",
  "UK career productivity",
];

const headers = {
  "accept-language": "en-US,en;q=0.9",
  "user-agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
};

const emailPattern =
  /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,24}/gi;

async function getText(url) {
  const response = await fetch(url, { headers });
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.text();
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

function cleanEmail(value) {
  return value
    .replaceAll("\\u0026", "&")
    .replace(/[.,;:!?)}\]]+$/g, "")
    .toLowerCase();
}

async function main() {
  const candidates = new Set();
  for (const query of queries) {
    const html = await getText(
      `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`,
    );
    for (const handle of handlesFromSearch(html)) candidates.add(handle);
  }

  const handles = [...candidates];
  const found = [];
  let cursor = 0;
  async function worker() {
    while (cursor < handles.length) {
      const handle = handles[cursor++];
      try {
        const html = await getText(`https://www.youtube.com${handle}/about`);
        const emails = [
          ...new Set(
            (html.match(emailPattern) || [])
              .map(cleanEmail)
              .filter(
                (email) =>
                  !email.endsWith("@example.com") &&
                  !email.endsWith("@email.com") &&
                  !email.includes("sentry"),
              ),
          ),
        ];
        if (emails.length) found.push({ handle, emails });
      } catch {
        // Probe only: skip transient failures.
      }
    }
  }

  await Promise.all(Array.from({ length: 10 }, () => worker()));
  console.log(
    JSON.stringify(
      {
        queryCount: queries.length,
        candidateCount: handles.length,
        channelsWithEmail: found.length,
        emailCount: found.reduce((sum, item) => sum + item.emails.length, 0),
        sample: found.slice(0, 20),
      },
      null,
      2,
    ),
  );
}

await main();
