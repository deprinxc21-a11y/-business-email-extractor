import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(page_title="Korea Business Email Extractor", page_icon="🇰🇷", layout="wide")
st.title("🇰🇷 Korea Public Business Email Extractor")
st.caption("Finds publicly displayed business/contact emails on company websites.")

EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+)")
CONTACT_WORDS = (
    "contact", "contact-us", "contactus", "inquiry", "inquiries", "support",
    "about", "company", "business", "customer", "sales", "global", "overseas",
    "email", "고객", "문의", "연락", "회사", "사업", "지원", "상담", "오시는길"
)
SKIP_EXT = (".jpg",".jpeg",".png",".gif",".webp",".svg",".pdf",".zip",".rar",".mp4",".mp3",".avi",".mov",".css",".js")
USER_AGENT = "PublicBusinessEmailExtractor/1.1"

def norm(u):
    u = str(u or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return urldefrag(u)[0].rstrip("/")

def host(u):
    return (urlparse(u).hostname or "").lower().removeprefix("www.")

def same_domain(a, b):
    ha, hb = host(a), host(b)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)

def session():
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429,500,502,503,504],
                  allowed_methods=["GET", "HEAD"], raise_on_status=False)
    ad = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", ad); s.mount("http://", ad)
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.8"})
    return s

def robots_ok(url, cache, agent=USER_AGENT):
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in cache:
        rp = RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
            cache[origin] = rp
        except Exception:
            # If robots.txt is unavailable, do not treat that as permission to bypass it.
            cache[origin] = None
            return False
    return cache[origin] is not None and cache[origin].can_fetch(agent, url)

def fetch(s, u):
    try:
        r = s.get(u, timeout=15, allow_redirects=True)
        ct = (r.headers.get("content-type") or "").lower()
        if r.status_code >= 400 or ("text/html" not in ct and "application/xhtml" not in ct):
            return None, r.url, r.status_code
        return r.text, r.url, r.status_code
    except requests.RequestException:
        return None, u, 0

def emails_from_html(soup, raw, source):
    vals = set()
    for e in EMAIL_RE.findall(soup.get_text(" ", strip=True) + " " + raw):
        vals.add(e.lower().strip(".,;:)>]}"))
    for a in soup.select('a[href^="mailto:"]'):
        e = a.get("href","")[7:].split("?",1)[0].strip().lower()
        if EMAIL_RE.fullmatch(e):
            vals.add(e)
    return vals

def crawl(s, start, max_pages, delay):
    start = norm(start)
    if not start:
        return [], "Invalid URL"
    base = host(start)
    q = deque([start])
    seen = set()
    robots = {}
    found = []

    while q and len(seen) < max_pages:
        u = q.popleft()
        if u in seen or host(u) != base:
            continue
        p = urlparse(u)
        if p.path.lower().endswith(SKIP_EXT):
            continue
        if not robots_ok(u, robots):
            continue
        seen.add(u)

        raw, final, status = fetch(s, u)
        if not raw:
            time.sleep(delay)
            continue

        soup = BeautifulSoup(raw, "html.parser")

        # Extract from visible text, mailto links, and structured metadata.
        for e in emails_from_html(soup, raw, final):
            found.append((e, final))

        # Queue likely contact pages and same-site links.
        candidates = []
        for a in soup.find_all("a", href=True):
            target = norm(urljoin(final, a["href"].split("#")[0]))
            if not target or target in seen or host(target) != base:
                continue
            if urlparse(target).path.lower().endswith(SKIP_EXT):
                continue
            label = (a.get_text(" ", strip=True) + " " + target).lower()
            priority = 0 if any(w in label for w in CONTACT_WORDS) else 1
            candidates.append((priority, target))

        # Also try common contact paths on the actual redirected domain.
        actual = f"{urlparse(final).scheme}://{urlparse(final).netloc}"
        for path in ("/contact", "/contact-us", "/about/contact", "/inquiry", "/support", "/about-us", "/company"):
            target = norm(actual + path)
            if target not in seen and host(target) == base:
                candidates.append((0, target))

        for _, target in sorted(set(candidates), key=lambda x: x[0]):
            if target not in q and target not in seen:
                q.append(target)

        time.sleep(delay)

    unique = {}
    for e, source in found:
        unique.setdefault(e, source)
    return list(unique.items()), ""

def load_file(upload):
    if upload.name.lower().endswith(".csv"):
        df = pd.read_csv(upload)
    else:
        raw = upload.read().decode("utf-8-sig", errors="replace")
        df = pd.DataFrame({"website": [x.strip() for x in raw.splitlines() if x.strip()]})

    df.columns = [str(c).strip().lower() for c in df.columns]
    url_cols = [c for c in df.columns if c in ("website","url","website_url","homepage","domain","company_website")]
    url_col = url_cols[0] if url_cols else df.columns[0]

    company_col = next((c for c in ("company","company_name","name") if c in df.columns), None)
    out = pd.DataFrame({
        "company": df[company_col].fillna("").astype(str) if company_col else "",
        "website": df[url_col].fillna("").astype(str).map(norm)
    })
    return out[out.website != ""].drop_duplicates("website").reset_index(drop=True)

upload = st.file_uploader("Upload CSV or TXT containing company websites", type=["csv","txt"])

if upload:
    try:
        df = load_file(upload)
        st.success(f"Loaded {len(df):,} unique websites.")
        st.dataframe(df.head(20), use_container_width=True)

        c1,c2,c3 = st.columns(3)
        with c1:
            limit = st.number_input("Companies to process", 1, 10000, min(100, len(df)), 100)
        with c2:
            pages = st.number_input("Pages per company", 1, 12, 6, 1)
        with c3:
            delay = st.number_input("Delay per page (seconds)", 0.5, 10.0, 1.0, 0.1)

        if st.button("🚀 Start extraction", type="primary", use_container_width=True):
            df = df.head(int(limit))
            s = session()
            results = []
            progress = st.progress(0)
            status = st.empty()
            email_count = 0

            for i, row in df.iterrows():
                company = str(row["company"]).strip()
                website = row["website"]
                status.info(f"Processing {i+1:,}/{len(df):,}: {website}")
                found, error = crawl(s, website, int(pages), float(delay))

                if found:
                    for email, source in found:
                        results.append({
                            "company": company,
                            "website": website,
                            "email": email,
                            "source_page": source,
                            "status": "email found"
                        })
                        email_count += 1
                else:
                    results.append({
                        "company": company,
                        "website": website,
                        "email": "",
                        "source_page": "",
                        "status": "no public email found" if not error else error
                    })

                progress.progress((i+1)/len(df))
                status.write(f"Companies processed: {i+1:,} | Public business emails found: {email_count:,}")

            result = pd.DataFrame(results).drop_duplicates(["website","email"])
            st.success(f"Finished: {email_count:,} public business emails found.")
            st.dataframe(result, use_container_width=True)
            st.download_button(
                "⬇️ Download CSV",
                result.to_csv(index=False).encode("utf-8-sig"),
                "korea_business_emails.csv",
                "text/csv",
                use_container_width=True
            )
else:
    st.info("Upload a CSV or TXT file to begin.")
