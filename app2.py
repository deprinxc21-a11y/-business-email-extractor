import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------------------------------------------------
# PAGE SETTINGS
# ---------------------------------------------------------

st.set_page_config(
    page_title="Korea Business Email Extractor",
    page_icon="🇰🇷",
    layout="wide",
)

st.title("🇰🇷 Korea Public Business Email Extractor")
st.caption(
    "Extract publicly displayed business/contact emails from company websites."
)


# ---------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------

EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])"
    r"([a-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-z0-9-]+(?:\.[a-z0-9-]+)+)"
)

CONTACT_WORDS = (
    "contact",
    "contact-us",
    "contactus",
    "inquiry",
    "inquiries",
    "enquiry",
    "support",
    "about",
    "about-us",
    "company",
    "business",
    "customer",
    "sales",
    "global",
    "overseas",
    "email",
    "service",
    "고객",
    "문의",
    "연락",
    "회사",
    "사업",
    "지원",
    "상담",
)

SKIP_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".rar",
    ".mp4",
    ".mp3",
    ".avi",
    ".mov",
    ".css",
    ".js",
)

USER_AGENT = (
    "Mozilla/5.0 (compatible; "
    "PublicBusinessEmailExtractor/2.0)"
)


# ---------------------------------------------------------
# URL HELPERS
# ---------------------------------------------------------

def normalize_url(url):
    """Normalize a website URL."""
    url = str(url or "").strip()

    if not url:
        return ""

    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url

    url = urldefrag(url)[0]

    return url.rstrip("/")


def get_host(url):
    """Return normalized hostname."""
    return (
        urlparse(url)
        .hostname
        or ""
    ).lower().removeprefix("www.")


def same_domain(url1, url2):
    """Check whether two URLs belong to the same domain."""
    host1 = get_host(url1)
    host2 = get_host(url2)

    return (
        host1 == host2
        or host1.endswith("." + host2)
        or host2.endswith("." + host1)
    )


# ---------------------------------------------------------
# HTTP SESSION
# ---------------------------------------------------------

def create_session():
    """Create a requests session with retries."""

    session = requests.Session()

    retry = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=[
            "GET",
            "HEAD",
        ],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
    )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko,en;q=0.8",
        }
    )

    return session


# ---------------------------------------------------------
# FETCH WEBSITE
# ---------------------------------------------------------

def fetch_page(session, url):
    """Download an HTML page."""

    try:
        response = session.get(
            url,
            timeout=15,
            allow_redirects=True,
        )

        content_type = (
            response.headers.get("content-type")
            or ""
        ).lower()

        if response.status_code >= 400:
            return None, response.url, response.status_code

        if (
            "text/html" not in content_type
            and "application/xhtml" not in content_type
        ):
            return None, response.url, response.status_code

        return (
            response.text,
            response.url,
            response.status_code,
        )

    except requests.RequestException:
        return None, url, 0


# ---------------------------------------------------------
# EMAIL EXTRACTION
# ---------------------------------------------------------

def extract_emails(html, soup):
    """Extract visible and mailto email addresses."""

    emails = set()

    visible_text = soup.get_text(
        " ",
        strip=True,
    )

    combined_text = (
        visible_text
        + " "
        + html
    )

    matches = EMAIL_RE.findall(combined_text)

    for email in matches:
        email = email.lower().strip(
            ".,;:)>]}\"'"
        )

        if EMAIL_RE.fullmatch(email):
            emails.add(email)

    # Extract mailto links.
    for link in soup.select(
        'a[href^="mailto:"]'
    ):
        href = link.get("href", "")

        email = (
            href[7:]
            .split("?", 1)[0]
            .strip()
            .lower()
        )

        if EMAIL_RE.fullmatch(email):
            emails.add(email)

    return sorted(emails)


# ---------------------------------------------------------
# CONTACT LINK DETECTION
# ---------------------------------------------------------

def is_contact_link(text):
    """Determine whether a link looks like a contact page."""

    text = str(text or "").lower()

    return any(
        word in text
        for word in CONTACT_WORDS
    )


# ---------------------------------------------------------
# CRAWLER
# ---------------------------------------------------------

def crawl_website(
    session,
    website,
    max_pages=6,
    delay=1.0,
):
    """
    Crawl a company's website and find
    publicly displayed email addresses.
    """

    website = normalize_url(website)

    if not website:
        return [], "Invalid URL"

    original_host = get_host(website)

    if not original_host:
        return [], "Invalid domain"

    queue = deque()
    queue.append(website)

    visited = set()
    found = {}

    pages_attempted = 0

    while queue and pages_attempted < max_pages:

        current_url = queue.popleft()

        current_url = normalize_url(
            current_url
        )

        if not current_url:
            continue

        if current_url in visited:
            continue

        # Stay on the company's website.
        if get_host(current_url) != original_host:
            continue

        path = urlparse(
            current_url
        ).path.lower()

        if path.endswith(
            SKIP_EXTENSIONS
        ):
            continue

        visited.add(current_url)
        pages_attempted += 1

        html, final_url, status = fetch_page(
            session,
            current_url,
        )

        if html is None:
            time.sleep(delay)
            continue

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        # -------------------------------------------------
        # FIND EMAILS
        # -------------------------------------------------

        emails = extract_emails(
            html,
            soup,
        )

        for email in emails:
            if email not in found:
                found[email] = final_url

        # -------------------------------------------------
        # FIND MORE CONTACT PAGES
        # -------------------------------------------------

        links = []

        for anchor in soup.find_all(
            "a",
            href=True,
        ):

            href = anchor.get(
                "href",
                "",
            ).strip()

            if not href:
                continue

            if href.lower().startswith(
                (
                    "mailto:",
                    "tel:",
                    "javascript:",
                )
            ):
                continue

            target = normalize_url(
                urljoin(
                    final_url,
                    href.split("#")[0],
                )
            )

            if not target:
                continue

            if target in visited:
                continue

            if get_host(target) != original_host:
                continue

            target_path = urlparse(
                target
            ).path.lower()

            if target_path.endswith(
                SKIP_EXTENSIONS
            ):
                continue

            label = (
                anchor.get_text(
                    " ",
                    strip=True,
                )
                + " "
                + target
            ).lower()

            priority = (
                0
                if is_contact_link(label)
                else 1
            )

            links.append(
                (
                    priority,
                    target,
                )
            )

        # Prioritize contact pages.
        links.sort(
            key=lambda item: item[0]
        )

        for _, target in links:

            if target not in visited:
                if target not in queue:
                    queue.append(target)

        time.sleep(delay)

    if found:
        return list(found.items()), ""

    if pages_attempted == 0:
        return [], "Website could not be accessed"

    return [], "No public email found"


# ---------------------------------------------------------
# INPUT FILE
# ---------------------------------------------------------

def load_company_file(uploaded_file):

    filename = (
        uploaded_file.name.lower()
    )

    try:

        if filename.endswith(".csv"):

            dataframe = pd.read_csv(
                uploaded_file
            )

        else:

            raw = uploaded_file.read().decode(
                "utf-8-sig",
                errors="replace",
            )

            websites = [
                line.strip()
                for line in raw.splitlines()
                if line.strip()
            ]

            dataframe = pd.DataFrame(
                {
                    "website": websites
                }
            )

    except Exception as error:

        raise ValueError(
            f"Could not read file: {error}"
        )

    # Normalize column names.
    dataframe.columns = [
        str(column)
        .strip()
        .lower()
        for column in dataframe.columns
    ]

    # Find website column.
    possible_website_columns = [
        "website",
        "url",
        "website_url",
        "homepage",
        "domain",
        "company_website",
    ]

    website_column = None

    for column in possible_website_columns:

        if column in dataframe.columns:
            website_column = column
            break

    if website_column is None:

        website_column = dataframe.columns[0]

    # Find company column.
    company_column = None

    for column in (
        "company",
        "company_name",
        "name",
    ):

        if column in dataframe.columns:
            company_column = column
            break

    if company_column:

        companies = (
            dataframe[company_column]
            .fillna("")
            .astype(str)
        )

    else:

        companies = pd.Series(
            [""] * len(dataframe)
        )

    websites = (
        dataframe[website_column]
        .fillna("")
        .astype(str)
        .map(normalize_url)
    )

    result = pd.DataFrame(
        {
            "company": companies,
            "website": websites,
        }
    )

    result = result[
        result["website"] != ""
    ]

    result = result.drop_duplicates(
        subset=["website"]
    )

    return result.reset_index(
        drop=True
    )


# ---------------------------------------------------------
# STREAMLIT UI
# ---------------------------------------------------------

uploaded_file = st.file_uploader(
    "📁 Upload CSV or TXT",
    type=[
        "csv",
        "txt",
    ],
    help=(
        "CSV should contain company and website "
        "columns. TXT should contain one website "
        "per line."
    ),
)

if uploaded_file:

    try:

        companies = load_company_file(
            uploaded_file
        )

        st.success(
            f"Loaded {len(companies):,} unique websites."
        )

        st.dataframe(
            companies.head(20),
            use_container_width=True,
        )

        st.subheader(
            "⚙️ Extraction settings"
        )

        col1, col2, col3 = st.columns(3)

        with col1:

            default_limit = min(
                100,
                len(companies),
            )

            company_limit = st.number_input(
                "Companies to process",
                min_value=1,
                max_value=10000,
                value=max(
                    1,
                    default_limit,
                ),
                step=100,
            )

        with col2:

            pages_per_company = st.number_input(
                "Pages per company",
                min_value=1,
                max_value=12,
                value=6,
                step=1,
            )

        with col3:

            request_delay = st.number_input(
                "Delay between pages (seconds)",
                min_value=0.5,
                max_value=10.0,
                value=1.0,
                step=0.5,
            )

        st.info(
            "For your first test, process only 5 companies."
        )

        start = st.button(
            "🚀 Start extraction",
            type="primary",
            use_container_width=True,
        )

        if start:

            selected = companies.head(
                int(company_limit)
            )

            session = create_session()

            results = []

            progress = st.progress(0)

            status_box = st.empty()

            email_counter = 0

            for index, row in selected.iterrows():

                company = str(
                    row["company"]
                ).strip()

                website = str(
                    row["website"]
                ).strip()

                number = (
                    index + 1
                )

                status_box.info(
                    f"Processing "
                    f"{number:,}/{len(selected):,}: "
                    f"{website}"
                )

                found, error = crawl_website(
                    session=session,
                    website=website,
                    max_pages=int(
                        pages_per_company
                    ),
                    delay=float(
                        request_delay
                    ),
                )

                if found:

                    for email, source_page in found:

                        results.append(
                            {
                                "company": company,
                                "website": website,
                                "email": email,
                                "source_page": source_page,
                                "status": "email found",
                            }
                        )

                        email_counter += 1

                else:

                    results.append(
                        {
                            "company": company,
                            "website": website,
                            "email": "",
                            "source_page": "",
                            "status": error,
                        }
                    )

                progress.progress(
                    number / len(selected)
                )

                status_box.write(
                    f"Companies processed: "
                    f"{number:,} | "
                    f"Public emails found: "
                    f"{email_counter:,}"
                )

            result_dataframe = pd.DataFrame(
                results
            )

            if not result_dataframe.empty:

                result_dataframe = (
                    result_dataframe
                    .drop_duplicates(
                        subset=[
                            "website",
                            "email",
                        ]
                    )
                )

            st.success(
                f"Finished. "
                f"Public business emails found: "
                f"{email_counter:,}"
            )

            st.subheader(
                "📊 Results"
            )

            st.dataframe(
                result_dataframe,
                use_container_width=True,
            )

            csv_data = (
                result_dataframe
                .to_csv(
                    index=False,
                    encoding="utf-8-sig",
                )
                .encode("utf-8-sig")
            )

            st.download_button(
                "⬇️ Download results CSV",
                data=csv_data,
                file_name="korea_business_emails.csv",
                mime="text/csv",
                use_container_width=True,
            )

    except Exception as error:

        st.error(
            f"Error: {error}"
        )

else:

    st.info(
        "Upload a CSV or TXT file to begin."
    )

    st.markdown(
        """
### CSV format

Your CSV should look like this:

| company | website |
|---|---|
| Samsung Electronics | https://www.samsung.com |
| LG Electronics | https://www.lg.com |
| Hyundai Motor Company | https://www.hyundai.com |

For TXT files, put one website per line.

**Use only publicly displayed business/contact emails and respect website terms, access restrictions, privacy requirements, and applicable anti-spam laws.**
        """
    )
