"""
CLI script: batch fetch QC/Wiki pages to Markdown.
Supports optional QC编号（逗号分隔）；如未提供，则读取同目录 urls.txt。
Also supports passing Wiki URLs directly, and customizing output directory / urls file path.
"""

import argparse
import io
import re
import sys
import pathlib
import hashlib
import mimetypes
from typing import Dict, Tuple, List, Optional
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from bs4 import BeautifulSoup, Tag
import html2text


BASE_DIR = pathlib.Path(__file__).resolve().parent
AUTH_FILE = BASE_DIR / "auth_headers.txt"
URLS_FILE = BASE_DIR / "urls.txt"

# Defaults baked in (do not change unless necessary)
DEFAULT_WIKI_COOKIES = {
    "confluence.list.pages.cookie": "list-content-tree",
}
DEFAULT_QC_COOKIES = {
    # keep default UI preference; other tokens are provided via auth file
    "AJS.conglomerate.cookie": "[streams.view.10003=list-view]",
}
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)

WIKI_DOMAIN = "wiki.feifanuniv.com"
QC_DOMAIN = "qc.feifanuniv.com"

# JIRA issue key pattern (e.g. SPK-12345 / QSC-82081).
# Keep conservative: <PROJECTKEY>-<number>, normalized to uppercase.
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def build_headers_from_lines(lines: List[str], defaults: Dict[str, str]) -> Dict[str, str]:
    """
    Accepts lines from a section:
    - Supports `Cookie: ...` or bare cookie fragments (JSESSIONID=...).
    - Merges defaults into Cookie header.
    - Allows other headers via `Key: value`.
    """
    headers: Dict[str, str] = {}
    cookie_parts = [f"{k}={v}" for k, v in defaults.items()]

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key, val = key.strip(), val.strip()
            if key.lower() == "cookie":
                if val:
                    cookie_parts.append(val)
            else:
                headers[key] = val
        else:
            # only accept cookie fragments like `k=v`
            if "=" in line:
                cookie_parts.append(line)

    if cookie_parts:
        headers["Cookie"] = "; ".join(cookie_parts)
    if "User-Agent" not in headers:
        headers["User-Agent"] = DEFAULT_UA
    return headers


def load_headers(path: pathlib.Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Parse headers file with sections, separated by lines containing '----- WIKI -----' or '----- QC -----'.
    Returns (wiki_headers, qc_headers).
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing headers file: {path}")

    sections = {"wiki": [], "qc": []}
    current: Optional[str] = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        if "-----" in upper and "WIKI" in upper:
            current = "wiki"
            continue
        if "-----" in upper and "QC" in upper:
            current = "qc"
            continue
        if current:
            sections[current].append(line)

    wiki_headers = build_headers_from_lines(sections["wiki"], DEFAULT_WIKI_COOKIES)
    qc_headers = build_headers_from_lines(sections["qc"], DEFAULT_QC_COOKIES)
    return wiki_headers, qc_headers


def parse_issue_from_qc_value(val: str) -> Optional[str]:
    """
    Accepts either:
    - issue key like SPK-12345 / QSC-82081
    - plain numeric like 82081 (auto-prefixed with QSC- for backward compatibility)
    - QC browse URL
    Returns normalized issue key like SPK-12345, or None if not derivable.
    """
    val = val.strip()
    if not val:
        return None
    if val.isdigit():
        return f"QSC-{val}"
    upper = val.upper()
    if ISSUE_KEY_RE.match(upper):
        return upper
    try:
        parsed = urlparse(val)
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            last = path_parts[-1]
            upper_last = last.upper()
            if ISSUE_KEY_RE.match(upper_last):
                return upper_last
    except Exception:
        return None
    return None


def parse_qc_inputs(raw: Optional[str | List[str]]) -> List[str]:
    """Split/normalize qc inputs (string with commas or list)."""
    if not raw:
        return []
    if isinstance(raw, str):
        items = raw.split(",")
    else:
        items = raw
    seen = set()
    out = []
    for item in items:
        norm = parse_issue_from_qc_value(item.strip())
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def parse_wiki_urls(raw: Optional[str | List[str]]) -> List[str]:
    """
    Parse wiki url(s) from:
    - comma-separated string
    - whitespace/newline-separated string
    - list of strings
    Returns unique urls preserving order.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        candidates = raw
    else:
        # split by comma or whitespace/newlines
        candidates = [p for p in re.split(r"[,\s]+", raw.strip()) if p]

    seen: set[str] = set()
    out: list[str] = []
    for u in candidates:
        u = u.strip()
        if not u:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_url_blocks(path: pathlib.Path) -> List[Tuple[Optional[str], List[str]]]:
    """
    Parse urls.txt with grouped QC markers.
    Syntax:
      qc=<issueKey or QC URL>
      <url1>
      <url2>
    Blocks separated by another qc=... or EOF.
    URLs before any qc marker go to default group (issue=None).
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing URLs file: {path}")

    blocks: List[Tuple[Optional[str], List[str]]] = []
    current_issue: Optional[str] = None
    current_urls: List[str] = []

    def flush(force: bool = False):
        nonlocal current_issue, current_urls
        if current_urls or force:
            blocks.append((current_issue, current_urls))
            current_urls = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("qc="):
            flush(force=True)  # flush previous block even if empty urls (to allow QC-only)
            val = line.split("=", 1)[1].strip()
            current_issue = parse_issue_from_qc_value(val)
            continue
        current_urls.append(line)

    flush(force=True)

    if not blocks:
        raise ValueError(f"No URLs found in {path}")
    return blocks


def build_blocks(url_file: pathlib.Path, extra_qc: List[str], use_urls_file: bool = True) -> List[Tuple[Optional[str], List[str]]]:
    """
    Merge blocks from url file with extra qc issues (dedup by issue key).
    When use_urls_file is False, only use extra_qc.
    """
    blocks: List[Tuple[Optional[str], List[str]]] = []
    if use_urls_file:
        blocks = parse_url_blocks(url_file)

    seen_issues = {issue for issue, _ in blocks if issue}

    for issue in extra_qc:
        if issue and issue not in seen_issues:
            blocks.append((issue, []))
            seen_issues.add(issue)

    if not blocks:
        raise ValueError(f"No URLs or QC issues provided (checked {url_file})")
    return blocks


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    return name or "untitled"


def unique_path(base: pathlib.Path) -> pathlib.Path:
    if not base.exists():
        return base
    stem, suffix = base.stem, base.suffix
    counter = 1
    while True:
        candidate = base.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def html_to_md(html: str) -> str:
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False  # keep images; src has been rewritten to local paths
    h.body_width = 0  # disable wrapping
    return h.handle(html)


def trim_md_tail(md: str) -> str:
    """
    Trim footer/comment blocks by locating the last line containing '添加头像'
    (avatar block) and dropping that line and everything after it.
    This avoids regex over-match in正文.
    """
    lines = md.splitlines()
    cut_idx = -1
    for i, line in enumerate(lines):
        if "添加头像" in line:
            cut_idx = i
    if cut_idx != -1:
        lines = lines[:cut_idx]
        md = "\n".join(lines).rstrip() + "\n"
    return md


def fallback_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts: List[str] = []
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts:
        parts.append(path_parts[-1])
    qs = parse_qs(parsed.query)
    if "pageId" in qs and qs["pageId"]:
        parts.append(f"page-{qs['pageId'][0]}")
    if not parts:
        parts.append(parsed.netloc or "untitled")
    return "_".join(parts)


def ensure_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _filename_from_url(url: str, content_type: Optional[str], idx: int) -> str:
    parsed = urlparse(url)
    name = pathlib.Path(parsed.path).name
    if name and "." in name:
        return name
    ext = None
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
    if not ext:
        ext = ".bin"
    return f"img_{idx}{ext}"


def _download_image(img_url: str, session: requests.Session, headers: Dict[str, str], img_dir: pathlib.Path, idx: int) -> Optional[str]:
    try:
        ensure_dir(img_dir)
        resp = session.get(img_url, headers=headers, timeout=30, proxies={})
        resp.raise_for_status()
        fname = _filename_from_url(img_url, resp.headers.get("Content-Type"), idx)
        out_path = img_dir / fname
        out_path.write_bytes(resp.content)
        return fname
    except Exception:
        return None


def clean_html_and_download(html: bytes, encoding: str | None, base_url: str, headers: Dict[str, str], img_dir: pathlib.Path) -> Tuple[str, str]:
    """
    Remove scripts/styles, download images to img_dir, rewrite src to local paths,
    extract title, return (title, cleaned_html).
    """
    soup = BeautifulSoup(html, "html.parser", from_encoding=encoding)
    title = soup.title.string.strip() if soup.title and soup.title.string else "untitled"

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    body = soup.body or soup

    def has_heading(node) -> bool:
        if isinstance(node, Tag):
            if node.name in ("h1", "h2", "h3"):
                return True
            return bool(node.find(["h1", "h2", "h3"]))
        return False

    # Trim leading boilerplate: keep content from first heading with text,
    # but only if that heading sits in the body; otherwise keep body as-is.
    def first_heading_in_body(b: Tag) -> Optional[Tag]:
        for h in b.find_all(["h1", "h2", "h3"]):
            if h.get_text(strip=True):
                return h
        return None

    fh = first_heading_in_body(body)
    if fh:
        # remove all previous siblings up the ancestor chain until body
        cur = fh
        while cur and cur is not body:
            for sib in list(cur.previous_siblings):
                try:
                    sib.decompose()
                except Exception:
                    pass
            cur = cur.parent

    # Trim trailing boilerplate: cut at common footer/comment markers
    cut_patterns = re.compile(r"(无标签|编辑标签|写评论|添加评论|内容工具|serverDuration|添加头像)", re.I)
    cutoff = None
    for node in body.find_all(string=cut_patterns):
        cutoff = node.parent
        break
    if cutoff:
        for sib in list(cutoff.next_siblings):
            try:
                sib.decompose()
            except Exception:
                pass
        try:
            cutoff.decompose()
        except Exception:
            pass

    # Only download/rewire images inside the trimmed body
    session = requests.Session()
    session.trust_env = False
    img_tags = body.find_all("img")
    skip_img_patterns = re.compile(r"(logo|favicon|avatar|useravatar|site\.logo|banner|add_profile_pic|profile_pic)", re.I)
    for idx, tag in enumerate(img_tags, 1):
        src = tag.get("src")
        if not src:
            tag.decompose()
            continue
        if skip_img_patterns.search(src):
            tag.decompose()
            continue
        full_url = urljoin(base_url, src)
        local_name = _download_image(full_url, session, headers, img_dir, idx)
        if local_name:
            tag["src"] = f"images/{local_name}"
        else:
            tag["src"] = src

    return title, str(body)


def fetch_and_convert(url: str, headers: Dict[str, str], out_dir: pathlib.Path) -> Tuple[str, str]:
    # Disable environment proxies to avoid unintended 127.0.0.1 proxy failures
    session = requests.Session()
    session.trust_env = False
    resp = session.get(url, headers=headers, timeout=30, proxies={})
    resp.raise_for_status()
    # Confluence unauth typically redirects to login.action (still 200 after redirect).
    lower_final_url = (resp.url or "").lower()
    if "login.action" in lower_final_url or "dologin.action" in lower_final_url:
        raise Exception("Wiki Token已过期，请更新 auth_headers.txt 中的WIKI认证信息")
    preferred = resp.apparent_encoding or resp.encoding or "utf-8"
    encodings_to_try = [preferred, "utf-8", "gbk", "gb2312"]
    seen = set()

    def parse_with(enc: str) -> Tuple[str, str]:
        return clean_html_and_download(resp.content, enc, url, headers, out_dir / "images")

    title, cleaned_html = "", ""
    for enc in encodings_to_try:
        if not enc or enc in seen:
            continue
        seen.add(enc)
        t, html = parse_with(enc)
        title, cleaned_html = t, html
        # If title decoded cleanly (no replacement char), accept
        if t and "\ufffd" not in t:
            break
    if (not title) or ("\ufffd" in title):
        title = fallback_title_from_url(url)
    md = html_to_md(cleaned_html)
    md = trim_md_tail(md)
    return title, md


def extract_issue_links(issue_key: str, qc_headers: Dict[str, str]) -> List[str]:
    """
    Fetch JIRA issue page and extract wiki links (mentioned in).
    """
    issue = parse_issue_from_qc_value(issue_key)
    if not issue:
        raise ValueError(f"Invalid QC issue key: {issue_key}")
    url = f"http://{QC_DOMAIN}/browse/{issue}"
    session = requests.Session()
    session.trust_env = False
    resp = session.get(url, headers=qc_headers, timeout=30, proxies={})
    resp.raise_for_status()

    # 检测是否被重定向到登录页（token过期）
    if "login" in resp.url.lower() or "用户登录" in resp.text or "请登录" in resp.text:
        raise Exception("QC Token已过期，请更新 auth_headers.txt 中的QC认证信息")

    soup = BeautifulSoup(resp.content, "html.parser")
    links: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if WIKI_DOMAIN in href:
            links.append(href)
    # deduplicate preserving order
    seen = set()
    uniq = []
    for h in links:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq


def ensure_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_block(
    issue: Optional[str],
    urls: List[str],
    wiki_headers: Dict[str, str],
    qc_headers: Dict[str, str],
    base_out_dir: pathlib.Path,
    log=print,
) -> None:
    # Determine output dir
    out_dir = base_out_dir / (issue if issue else "default")
    ensure_dir(out_dir)

    combined_urls: List[str] = []
    if issue:
        try:
            combined_urls.extend(extract_issue_links(issue, qc_headers))
        except Exception as exc:
            log(f"[WARN] Fetch issue {issue} links failed: {exc}")
    combined_urls.extend(urls)

    # dedupe while preserving order
    seen = set()
    uniq_urls = []
    for u in combined_urls:
        if u not in seen:
            seen.add(u)
            uniq_urls.append(u)

    if not uniq_urls:
        log(f"[WARN] Issue {issue}: 未找到任何关联的Wiki链接")
        return

    for url in uniq_urls:
        try:
            title, md = fetch_and_convert(url, wiki_headers, out_dir)
            # strip trailing " - 项目-青书学堂 - 青颖飞帆WIKI系统" if present
            clean_title = re.sub(r"\s*-\s*项目-青书学堂\s*-\s*青颖飞帆WIKI系统\s*$", "", title)
            filename = sanitize_filename(clean_title) + ".md"
            out_path = out_dir / filename  # overwrite if exists
            out_path.write_text(md, encoding="utf-8")
            log(f"[OK] {url} -> {out_path}")
        except Exception as exc:  # keep batch going
            log(f"[ERR] {url}: {exc}")


def run(
    qc_numbers: Optional[str | List[str]] = None,
    use_urls_file: Optional[bool] = None,
    wiki_urls: Optional[str | List[str]] = None,
    urls_file: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """
    Entry for external callers (CLI/MCP):
    - qc_numbers: comma-separated string or list; merged with urls.txt when provided.
    - If qc_numbers is None/empty, only urls.txt is used.
    - wiki_urls: optional wiki URL(s); when no QC issue key is associated, files/images go under <output_dir>/default.
    - urls_file: optional path to urls file (same syntax as urls.txt).
    - output_dir: optional base output directory; default is <script_dir>/output.
    Returns aggregated log output as string.
    """
    wiki_headers, qc_headers = load_headers(AUTH_FILE)
    extra_qc = parse_qc_inputs(qc_numbers)
    wiki_url_list = parse_wiki_urls(wiki_urls)

    base_out_dir = pathlib.Path(output_dir).expanduser().resolve() if output_dir else (BASE_DIR / "output")

    url_file_path = pathlib.Path(urls_file).expanduser().resolve() if urls_file else URLS_FILE
    blocks: List[Tuple[Optional[str], List[str]]] = []

    resolved_use_urls_file: bool
    if use_urls_file is not None:
        resolved_use_urls_file = use_urls_file
    elif urls_file:
        resolved_use_urls_file = True
    elif extra_qc or wiki_url_list:
        # Default: "pure QC/pure wiki" unless explicitly enabled.
        resolved_use_urls_file = False
    else:
        # No inputs -> keep original behavior: read default urls file.
        resolved_use_urls_file = True

    if resolved_use_urls_file or extra_qc:
        blocks = build_blocks(url_file_path, extra_qc, use_urls_file=resolved_use_urls_file)

    if wiki_url_list:
        # Merge into the default block if it exists, otherwise add one.
        merged = False
        for idx, (issue, urls) in enumerate(blocks):
            if issue is None:
                blocks[idx] = (issue, urls + wiki_url_list)
                merged = True
                break
        if not merged:
            blocks.append((None, wiki_url_list))

    if not blocks:
        raise ValueError("No inputs provided: please specify qc_numbers and/or wiki_urls, or enable/use urls file.")

    buf = io.StringIO()
    def log(msg: str) -> None:
        print(msg, file=buf)

    for issue, urls in blocks:
        run_block(issue, urls, wiki_headers, qc_headers, base_out_dir=base_out_dir, log=log)
    return buf.getvalue()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Batch fetch QC/Wiki pages to Markdown.")
    parser.add_argument(
        "qc_numbers",
        nargs="?",
        default=None,
        help="QC编号（多个以英文逗号分隔，可选）；不提供则使用 urls 文件或 wiki_urls",
    )
    parser.add_argument(
        "--wiki-urls",
        default=None,
        help="直接传入要抓取的 Wiki URL（可用逗号或空格分隔多个）；不依赖 urls 文件",
    )
    parser.add_argument(
        "--urls-file",
        default=None,
        help="指定 urls 文件路径（默认同目录 urls.txt；语法支持 qc=... 分组）",
    )
    parser.add_argument(
        "--use-urls-file",
        action="store_true",
        help="同时读取 urls 文件（默认：传了 qc_numbers/wiki_urls 时不读取；都不传时会读取默认 urls.txt）",
    )
    parser.add_argument(
        "--no-urls-file",
        action="store_true",
        help="不读取 urls 文件（仅使用 qc_numbers/wiki_urls）",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="指定下载输出根目录（默认脚本目录下 output/）；QC 仍按问题编号建目录，纯 wiki 放 default 目录",
    )

    args = parser.parse_args(argv)
    try:
        resolved_use_urls_file: Optional[bool] = None
        if args.use_urls_file:
            resolved_use_urls_file = True
        if args.no_urls_file:
            resolved_use_urls_file = False
        log_output = run(
            qc_numbers=args.qc_numbers,
            wiki_urls=args.wiki_urls,
            urls_file=args.urls_file,
            output_dir=args.output_dir,
            use_urls_file=resolved_use_urls_file,
        )
        sys.stdout.write(log_output)
    except Exception as exc:
        print(f"[FATAL] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
