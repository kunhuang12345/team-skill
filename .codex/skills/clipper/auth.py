import argparse
import getpass
import os
import pathlib
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_AUTH_FILE = BASE_DIR / "auth_headers.txt"
DOTENV_FILE = BASE_DIR / ".env"

WIKI_BASE_URL = "http://wiki.feifanuniv.com"
QC_BASE_URL = "http://qc.feifanuniv.com"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class LoginResult:
    cookies: Dict[str, str]
    final_url: str


def _new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": DEFAULT_UA})
    return session


def _extract_form_fields(form) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        fields[name] = inp.get("value", "")
    return fields


def _find_first_form(soup: BeautifulSoup, *, form_id: Optional[str] = None, action_contains: Optional[str] = None):
    if form_id:
        form = soup.find("form", id=form_id)
        if form:
            return form
    if action_contains:
        for form in soup.find_all("form"):
            action = (form.get("action") or "").lower()
            if action_contains.lower() in action:
                return form
    return soup.find("form")


def _cookies_for_domain(session: requests.Session, domain: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for c in session.cookies:
        cookie_domain = (c.domain or "").lstrip(".")
        if cookie_domain == domain or cookie_domain.endswith("." + domain):
            out[c.name] = c.value
    return out


def login_wiki(username: str, password: str, *, remember: bool = True) -> LoginResult:
    session = _new_session()
    login_url = f"{WIKI_BASE_URL}/login.action?os_destination=%2Findex.action"
    resp = session.get(login_url, timeout=30, proxies={})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    form = _find_first_form(soup, action_contains="dologin.action")
    if not form:
        raise RuntimeError("未找到 Wiki 登录表单（dologin.action），可能页面结构已变更或被跳转到 SSO")

    action = form.get("action") or "/dologin.action"
    post_url = urljoin(WIKI_BASE_URL, action)
    data = _extract_form_fields(form)
    data["os_username"] = username
    data["os_password"] = password
    if remember:
        data["os_cookie"] = "true"

    resp2 = session.post(post_url, data=data, timeout=30, allow_redirects=True, proxies={})
    resp2.raise_for_status()

    final_url = resp2.url or ""
    if "login.action" in final_url.lower() or "dologin.action" in final_url.lower():
        raise RuntimeError("Wiki 登录失败：仍停留在登录页（请检查用户名/密码是否正确）")

    cookies = _cookies_for_domain(session, "wiki.feifanuniv.com")
    if not cookies:
        raise RuntimeError("Wiki 登录失败：未获取到任何 Cookie（可能需要 SSO 或额外校验）")

    return LoginResult(cookies=cookies, final_url=final_url)


def login_qc(username: str, password: str, *, remember: bool = True) -> LoginResult:
    session = _new_session()
    login_url = f"{QC_BASE_URL}/login.jsp?os_destination=%2Fsecure%2FDashboard.jspa"
    resp = session.get(login_url, timeout=30, proxies={})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    form = _find_first_form(soup, form_id="login-form", action_contains="login.jsp")
    if not form:
        raise RuntimeError("未找到 QC 登录表单（login.jsp），可能页面结构已变更或被跳转到 SSO")

    action = form.get("action") or "/login.jsp"
    post_url = urljoin(QC_BASE_URL, action)
    data = _extract_form_fields(form)
    data["os_username"] = username
    data["os_password"] = password
    if remember:
        data["os_cookie"] = "true"

    resp2 = session.post(post_url, data=data, timeout=30, allow_redirects=True, proxies={})
    resp2.raise_for_status()

    final_url = resp2.url or ""
    if "login.jsp" in final_url.lower():
        raise RuntimeError("QC 登录失败：仍停留在登录页（请检查用户名/密码是否正确）")

    cookies = _cookies_for_domain(session, "qc.feifanuniv.com")
    if not cookies:
        raise RuntimeError("QC 登录失败：未获取到任何 Cookie（可能需要 SSO 或额外校验）")

    return LoginResult(cookies=cookies, final_url=final_url)


def _pick_cookie_keys(cookies: Dict[str, str], keys: Iterable[str]) -> Dict[str, str]:
    picked: Dict[str, str] = {}
    for k in keys:
        v = cookies.get(k)
        if v:
            picked[k] = v
    return picked


def write_auth_headers_file(
    path: pathlib.Path,
    *,
    wiki_cookies: Dict[str, str],
    qc_cookies: Dict[str, str],
    keep_all_cookies: bool = False,
) -> None:
    if keep_all_cookies:
        wiki_out = dict(sorted(wiki_cookies.items()))
        qc_out = dict(sorted(qc_cookies.items()))
    else:
        wiki_out = _pick_cookie_keys(wiki_cookies, ["seraph.confluence", "JSESSIONID"])
        qc_out = _pick_cookie_keys(qc_cookies, ["seraph.rememberme.cookie", "atlassian.xsrf.token", "JSESSIONID"])

    lines: list[str] = []
    lines.append("# Authentication headers")
    lines.append("# Sections split for readability. Only fill the dynamic cookie fragments shown.")
    lines.append("")
    lines.append("----- WIKI -----")
    lines.append("# confluence.list.pages.cookie is baked in code")
    for k, v in wiki_out.items():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("----- QC -----")
    lines.append("# AJS.conglomerate.cookie is baked in code")
    for k, v in qc_out.items():
        lines.append(f"{k}={v}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _get_env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    if v:
        v = v.strip()
        return v or None
    v = _DOTENV.get(name)
    if v:
        v = v.strip()
        return v or None
    return None


def _load_dotenv(path: pathlib.Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val and ((val[0] == val[-1]) and val[0] in ("'", '"')):
            val = val[1:-1]
        elif "#" in val:
            # Treat unquoted `#` as a comment delimiter.
            val = val.split("#", 1)[0].rstrip()
        out.setdefault(key, val)
    return out


_DOTENV = _load_dotenv(DOTENV_FILE)


def _resolve_credentials(kind: str) -> Tuple[str, str]:
    user = _get_env(f"CLIPPER_{kind}_USERNAME") or _get_env("CLIPPER_USERNAME")
    pwd = _get_env(f"CLIPPER_{kind}_PASSWORD") or _get_env("CLIPPER_PASSWORD")

    if not user:
        user = input(f"{kind} username: ").strip()
    if not pwd:
        pwd = getpass.getpass(f"{kind} password: ")

    if not user or not pwd:
        raise RuntimeError(f"{kind} 登录需要用户名和密码（可用环境变量 CLIPPER_USERNAME/CLIPPER_PASSWORD）")
    return user, pwd


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Clipper auth helper: auto login and refresh auth_headers.txt")
    parser.add_argument("--auth-file", default=str(DEFAULT_AUTH_FILE))
    parser.add_argument("--only", choices=["wiki", "qc", "both"], default="both")
    parser.add_argument("--all-cookies", action="store_true", help="写入该域名下的全部 cookie（更稳健，但更冗长）")
    parser.add_argument("--no-remember", action="store_true", help="不勾选 remember me（不推荐）")
    parser.add_argument("--dry-run", action="store_true", help="仅探测登录页表单，不提交用户名密码")
    args = parser.parse_args(argv)

    auth_path = pathlib.Path(args.auth_file).expanduser().resolve()
    remember = not args.no_remember

    wiki_cookies: Dict[str, str] = {}
    qc_cookies: Dict[str, str] = {}

    if args.only in ("wiki", "both"):
        if args.dry_run:
            session = _new_session()
            resp = session.get(f"{WIKI_BASE_URL}/login.action", timeout=30, proxies={})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            form = _find_first_form(soup, action_contains="dologin.action")
            if not form:
                raise RuntimeError("未找到 Wiki 登录表单（dologin.action）")
        else:
            user, pwd = _resolve_credentials("WIKI")
            wiki_cookies = login_wiki(user, pwd, remember=remember).cookies

    if args.only in ("qc", "both"):
        if args.dry_run:
            session = _new_session()
            resp = session.get(f"{QC_BASE_URL}/login.jsp", timeout=30, proxies={})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            form = _find_first_form(soup, form_id="login-form", action_contains="login.jsp")
            if not form:
                raise RuntimeError("未找到 QC 登录表单（login.jsp）")
        else:
            user, pwd = _resolve_credentials("QC")
            qc_cookies = login_qc(user, pwd, remember=remember).cookies

    if args.dry_run:
        print("[OK] dry-run: login forms detected")
        return 0

    existing = auth_path.read_text(encoding="utf-8") if auth_path.exists() else ""

    if args.only == "wiki":
        existing_qc = _read_existing_section_cookies(existing, "QC")
        qc_cookies = existing_qc
    elif args.only == "qc":
        existing_wiki = _read_existing_section_cookies(existing, "WIKI")
        wiki_cookies = existing_wiki

    write_auth_headers_file(
        auth_path,
        wiki_cookies=wiki_cookies,
        qc_cookies=qc_cookies,
        keep_all_cookies=args.all_cookies,
    )

    print(f"[OK] auth headers refreshed: {auth_path}")
    return 0


def _read_existing_section_cookies(text: str, section: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        up = line.upper()
        if "-----" in up and "WIKI" in up:
            current = "WIKI"
            continue
        if "-----" in up and "QC" in up:
            current = "QC"
            continue
        if current != section:
            continue
        if ":" in line:
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


if __name__ == "__main__":
    raise SystemExit(main())
