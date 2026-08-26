import io
import re
import time
import queue
import threading
from urllib.parse import urljoin, urlparse, urldefrag

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


st.set_page_config(
    page_title="Korea Business Email Extractor",
    page_icon="🇰🇷",
    layout="wide",
)

EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])([a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+)"
)

ROLE_WORDS = {
    "info", "contact", "hello", "sales", "support", "admin", "office",
    "marketing", "business", "inquiry", "inquiries", "enquiry", "service",
    "customer", "cs", "export", "trade", "overseas", "global", "hr",
    "career", "careers", "order", "orders", "help", "team"
}

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.kr", "hotmail.com",
    "outlook.com", "live.com", "naver.com", "daum.net", "hanmail.net",
    "icloud.com", "proton.me", "protonmail.com"
}

COMMON_CONTACT_PATHS = [
    "/contact", "/contact-us", "/contactus", "/about/contact",
    "/inquiry", "/inquiries", "/support", "/customer-service",
    "/company/contact", "/en/contact", "/ko/contact",
    "/about", "/about-us", "/company", "/business", "/support/contact"
]

USER_AGENT = (
    "Mozilla/5.0 (compatible; PublicBusinessEmailExtractor/1.0; "
    "+https://streamlit.io/) "
)

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return urldefrag(url)[0].rstrip("/")


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def same_site(a: str, b: str) -> bool:
    ha, hb = host_of(a), host_of(b)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def is_korean_domain(url: str) -> bool:
    host = host_of(url)
    return host.endswith(".kr") or ".kr." in host


def business_email(email: str, website: str) -> bool:
    email = email.strip().lower().rstrip(".,;:)")
    if "@" not in email:
        return False
    local, domain = email.rsplit("@", 1)
    site_host = host_of(website)
    if domain in FREE_EMAIL_DOMAINS:
        return False
    # Prefer addresses on the company's own domain.
    if site_host and not (domain == site_host or domain.endswith("." + site_host)):
        return False
    # Avoid obvious personal-looking local parts unless they are common business roles.
    role = local.replace(".", "").replace("-", "").replace("_", "")
    return (
        local in ROLE_WORDS
        or any(local.startswith(x + ".") or local.startswith(x + "-") or local.startswith(x + "_")
               for x in ROLE_WORDS)
        or role in ROLE_WORDS
    )


def extract_emails(text: str, website: str):
    found = set()
    for match in EMAIL_RE.findall(text or ""):
        e = match.lower().strip()
        if business_email(e, website):
            found.add(e)
    return sorted(found)


def make_session():
    s = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en,ko;q=0.8"})
    return s


def fetch(session, url, timeout=15):
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None, r.status_code, r.url
        ctype = (r.headers.get("content-type") or "").lower()
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            return None, r.status_code, r.url
        return r.text, r.status_code, r.url
    except requests.RequestException:
        return None, 0, url


def crawl_site(session, start_url, max_pages=5, delay=0.8):
    start_url = normalize_url(start_url)
    if not start_url:
        return [], "invalid URL"

    visited = set()
    candidates = [start_url]
    base_host = host_of(start_url)
    results = []

    # First try the homepage, then likely contact pages, then same-site links.
    for path in COMMON_CONTACT_PATHS:
        candidates.append(f"https://{base_host}{path}")

    while candidates and len(visited) < max_pages:
        url = candidates.pop(0)
        url = normalize_url(url)
        if not url or url in visited or host_of(url) != base_host:
            continue
        visited.add(url)

        html, status, final_url = fetch(session, url)
        if not html:
            time.sleep(delay)
            continue

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        for e in extract_emails(text + " " + html, final_url):
            results.append((e, final_url))

        # Look for mailto links and likely contact/about links.
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if href.lower().startswith("mailto:"):
                e = href[7:].split("?", 1)[0].strip()
                if business_email(e, final_url):
                    results.append((e.lower(), final_url))
                continue

            target = normalize_url(urljoin(final_url, href))
            if not target or host_of(target) != base_host:
                continue
            label = (a.get_text(" ", strip=True) + " " + target).lower()
            if any(k in label for k in [
                "contact", "inquiry", "inquiries", "support", "about",
                "company", "business", "고객", "문의", "회사", "연락"
            ]):
                if target not in visited and target not in candidates:
                    candidates.insert(0, target)

        time.sleep(delay)

    unique = {}
    for email, source in results:
        unique.setdefault(email, source)
    return [(e, src) for e, src in unique.items()], ""


def load_input(uploaded):
    if uploaded is None:
        return None, "No file"

    name = uploaded.name.lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(uploaded)
        else:
            raw = uploaded.read().decode("utf-8-sig", errors="replace")
            lines = [x.strip() for x in raw.splitlines() if x.strip()]
            df = pd.DataFrame({"website": lines})
    except Exception as exc:
        return None, f"Could not read file: {exc}"

    df.columns = [str(c).strip().lower() for c in df.columns]
    possible = [c for c in df.columns if c in {
        "website", "url", "website_url", "company_website", "homepage", "domain"
    }]
    if possible:
        url_col = possible[0]
    else:
        url_col = df.columns[0]

    out = pd.DataFrame()
    out["company"] = df["company"] if "company" in df.columns else (
        df["company_name"] if "company_name" in df.columns else ""
    )
    out["website"] = df[url_col].fillna("").astype(str).map(normalize_url)
    out = out[out["website"] != ""].drop_duplicates("website").reset_index(drop=True)
    return out, ""


st.title("🇰🇷 Korea Public Business Email Extractor")
st.caption(
    "Batch-extracts publicly displayed, company-domain business/contact emails "
    "from websites you provide."
)

with st.expander("Important usage notes", expanded=False):
    st.markdown(
        """
- This tool is for **publicly displayed business/contact email addresses**.
- It does not attempt to discover private/personal email addresses.
- It does not bypass CAPTCHAs, logins, robots controls, or access restrictions.
- Use only company lists/websites you are authorized to process and respect website terms,
  rate limits, privacy rules, and applicable anti-spam laws.
- Processing 10,000 websites can take a long time. The app saves a CSV result in your session
  and processes sites sequentially with a delay.
        """
    )

st.header("1. Upload your company website list")
uploaded = st.file_uploader(
    "Upload CSV or TXT",
    type=["csv", "txt"],
    help="CSV can contain company/company_name and website/url columns. TXT should contain one URL per line."
)

st.header("2. Crawl settings")
c1, c2, c3 = st.columns(3)
with c1:
    max_companies = st.number_input(
        "Maximum companies to process",
        min_value=1, max_value=10000, value=10000, step=100
    )
with c2:
    max_pages = st.number_input(
        "Pages per company",
        min_value=1, max_value=10, value=5, step=1
    )
with c3:
    delay = st.number_input(
        "Delay between page requests (seconds)",
        min_value=0.5, max_value=10.0, value=0.8, step=0.1
    )

if uploaded is not None:
    df, err = load_input(uploaded)
    if err:
        st.error(err)
    else:
        df = df.head(int(max_companies))
        st.success(f"Loaded {len(df):,} unique websites.")
        st.dataframe(df.head(20), use_container_width=True)

        if "run_id" not in st.session_state:
            st.session_state.run_id = 0

        start = st.button(
            f"🚀 Start extraction for {len(df):,} companies",
            type="primary",
            use_container_width=True
        )

        if start:
            st.session_state.run_id += 1
            session = make_session()
            rows = []
            progress = st.progress(0)
            status = st.empty()
            stats = st.empty()

            for i, rec in df.iterrows():
                company = str(rec.get("company", "") or "").strip()
                website = str(rec["website"])
                status.info(f"Processing {i+1:,} / {len(df):,}: {website}")

                emails, err = crawl_site(
                    session,
                    website,
                    max_pages=int(max_pages),
                    delay=float(delay)
                )

                if emails:
                    for email, source in emails:
                        rows.append({
                            "company": company,
                            "website": website,
                            "email": email,
                            "source_page": source,
                            "korea_domain": is_korean_domain(website),
                        })
                else:
                    rows.append({
                        "company": company,
                        "website": website,
                        "email": "",
                        "source_page": "",
                        "korea_domain": is_korean_domain(website),
                    })

                progress.progress((i + 1) / len(df))
                stats.write(
                    f"Companies processed: {i+1:,} | "
                    f"Business emails found: {sum(bool(r['email']) for r in rows):,}"
                )

            result = pd.DataFrame(rows).drop_duplicates(
                subset=["website", "email"], keep="first"
            )

            st.session_state["result"] = result
            st.success("✅ Extraction finished.")
            st.dataframe(result, use_container_width=True)

            csv = result.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "⬇️ Download results as CSV",
                data=csv,
                file_name="korea_business_emails.csv",
                mime="text/csv",
                use_container_width=True
            )

if "result" in st.session_state and uploaded is None:
    result = st.session_state["result"]
    st.subheader("Previous results")
    st.dataframe(result, use_container_width=True)
    st.download_button(
        "⬇️ Download previous CSV",
        data=result.to_csv(index=False).encode("utf-8-sig"),
        file_name="korea_business_emails.csv",
        mime="text/csv",
        use_container_width=True
    )
