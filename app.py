import io
import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Business Email Extractor", page_icon="📧")
st.title("📧 Public Business Email Extractor")
st.caption("Extracts publicly displayed business emails from websites you provide.")

USER_AGENT = "PublicBusinessEmailExtractor/1.0"
MAX_PAGES = 15
DELAY_SECONDS = 1.5
TIMEOUT = 15

EMAIL_RE = re.compile(
    r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\b"
)

SKIP_EXTENSIONS = (".jpg",".jpeg",".png",".gif",".webp",".svg",".pdf",".zip",".rar",".mp4",".mp3",".avi",".mov",".css",".js")
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

def normalize_url(url):
    url = url.strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")

def robots_allowed(url, cache):
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in cache:
        rp = RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
        except Exception:
            return False
        cache[origin] = rp
    return cache[origin].can_fetch(USER_AGENT, url)

def emails_from_page(soup):
    text = soup.get_text(" ", strip=True)
    found = set(e.lower() for e in EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):
        e = a.get("href","")[7:].split("?",1)[0].strip().lower()
        if EMAIL_RE.fullmatch(e):
            found.add(e)
    return found

def crawl(start):
    start = normalize_url(start)
    if not start:
        return []
    domain = urlparse(start).netloc.lower()
    q, visited, rows, robots = deque([start]), set(), [], {}
    while q and len(visited) < MAX_PAGES:
        url = q.popleft()
        if url in visited:
            continue
        visited.add(url)
        p = urlparse(url)
        if p.netloc.lower() != domain or p.path.lower().endswith(SKIP_EXTENSIONS):
            continue
        if not robots_allowed(url, robots):
            continue
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type","").lower():
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.get_text(" ", strip=True) if soup.title else domain
            for email in emails_from_page(soup):
                rows.append({"Company": title[:150], "Website": start, "Email": email, "Source URL": r.url})
            links = []
            for a in soup.find_all("a", href=True):
                href = urljoin(r.url, a["href"].split("#")[0])
                hp = urlparse(href)
                if hp.scheme in ("http","https") and hp.netloc.lower() == domain and href not in visited and not hp.path.lower().endswith(SKIP_EXTENSIONS):
                    label = (a.get_text(" ",strip=True) + " " + href).lower()
                    priority = 0 if any(x in label for x in ("contact","お問い合わせ","about","support","email")) else 1
                    links.append((priority, href))
            for _, href in sorted(links)[:80]:
                q.append(href)
            time.sleep(DELAY_SECONDS)
        except requests.RequestException:
            continue
    unique = {}
    for row in rows:
        unique[(row["Email"], row["Source URL"])] = row
    return list(unique.values())

st.subheader("1. Add company websites")
text = st.text_area("Paste one website per line", placeholder="https://example.co.kr\nhttps://example.com")

uploaded = st.file_uploader("Or upload a TXT/CSV file containing website URLs", type=["txt","csv"])

urls = []
if text:
    urls.extend([normalize_url(x) for x in text.splitlines() if x.strip()])

if uploaded:
    raw = uploaded.read().decode("utf-8-sig", errors="ignore")
    if uploaded.name.lower().endswith(".csv"):
        try:
            df = pd.read_csv(io.StringIO(raw))
            col = next((c for c in df.columns if "url" in str(c).lower() or "website" in str(c).lower()), df.columns[0])
            urls.extend([normalize_url(str(x)) for x in df[col].dropna().tolist()])
        except Exception:
            urls.extend([normalize_url(x) for x in raw.splitlines() if x.strip()])
    else:
        urls.extend([normalize_url(x) for x in raw.splitlines() if x.strip()])

urls = list(dict.fromkeys([u for u in urls if u]))

st.write(f"**Websites ready:** {len(urls)}")

if st.button("🔎 Extract Public Emails", type="primary", disabled=not urls):
    results = []
    progress = st.progress(0)
    status = st.empty()
    for i, url in enumerate(urls):
        status.write(f"Crawling {i+1}/{len(urls)}: {url}")
        results.extend(crawl(url))
        progress.progress((i + 1) / len(urls))

    result_df = pd.DataFrame(results, columns=["Company","Website","Email","Source URL"]).drop_duplicates()
    status.success(f"Finished. Found {len(result_df)} email/source records.")
    st.dataframe(result_df, use_container_width=True)
    st.download_button(
        "📥 Download CSV",
        result_df.to_csv(index=False).encode("utf-8-sig"),
        "emails.csv",
        "text/csv",
    )
    st.info("Use only publicly displayed business contact information and comply with website terms, robots.txt, privacy, and anti-spam laws.")
