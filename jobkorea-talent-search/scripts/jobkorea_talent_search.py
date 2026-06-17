#!/usr/bin/env python3
"""Search public JobKorea talent summaries.

This helper uses JobKorea's browser-visible corporate talent search page and its
same AJAX endpoint. It only reads public/obfuscated list summaries. Full resume
view, contact details, scraping at scale, scrap/bookmark, and position proposal
flows are intentionally out of scope because they require an employer account,
paid entitlements, or user confirmation.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any

BASE_URL = "https://www.jobkorea.co.kr"
FIND_PATH = "/corp/person/find"
AJAX_PATH = "/corp/person/detailsearchajax"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


@dataclass
class Candidate:
    rno: str
    url: str
    name: str = ""
    meta: str = ""
    career: str = ""
    education: str = ""
    locations: str = ""
    salary: str = ""
    skills: str = ""
    badges: str = ""
    raw_summary: str = ""


def fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> str:
    req_headers = {"User-Agent": DEFAULT_UA, "Referer": BASE_URL + FIND_PATH}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def extract_json_object(source: str, marker: str) -> dict[str, Any]:
    idx = source.find(marker)
    if idx < 0:
        raise RuntimeError(f"cannot find marker: {marker}")
    start = source.find("{", idx)
    if start < 0:
        raise RuntimeError("cannot find JSON object start")
    depth = 0
    in_string = False
    escape = False
    for pos in range(start, len(source)):
        ch = source[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(source[start : pos + 1])
    raise RuntimeError("unterminated JSON object")


def iter_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_nodes(item)


def mark_matching_nodes(sc: dict[str, Any], top_key: str, labels: list[str]) -> list[str]:
    if not labels:
        return []
    section = sc.get(top_key)
    if section is None:
        return []
    wanted = [x.strip().lower() for x in labels if x.strip()]
    matched: list[str] = []
    for node in iter_nodes(section):
        title = str(node.get("t", ""))
        code = str(node.get("v", ""))
        title_l = title.lower()
        code_l = code.lower()
        if any(w == title_l or w == code_l or w in title_l for w in wanted):
            for k in ("s", "c", "use"):
                if k in node:
                    node[k] = 1
            matched.append(title or code)
    return matched


def build_search_condition(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    first = fetch(BASE_URL + FIND_PATH)
    sc = extract_json_object(first, "var searchcondition =")

    sc["p"] = args.page
    sc["ps"] = args.limit
    sc["saveno"] = 0
    sc["ff"] = 0
    sc["sf"] = args.sort

    terms: list[dict[str, Any]] = []
    for kw in args.keyword:
        terms.append({"s": 1, "c": 1, "t": kw, "v": kw, "kwdtypecode": 1, "logictypecode": 0})
    for kw in args.and_keyword:
        terms.append({"s": 1, "c": 1, "t": kw, "v": kw, "kwdtypecode": 1, "logictypecode": 1})
    for kw in args.or_keyword:
        terms.append({"s": 1, "c": 1, "t": kw, "v": kw, "kwdtypecode": 1, "logictypecode": 3})
    for kw in args.exclude_keyword:
        terms.append({"s": 1, "c": 1, "t": kw, "v": kw, "kwdtypecode": 1, "logictypecode": 2})
    sc["totalkeywordlist"] = terms

    if terms:
        first_kw = terms[0]["t"]
        sc.setdefault("pfr", {}).setdefault("ck", {})["Keyword"] = first_kw
        sc["pfr"]["ck"]["KeywordType"] = 1
        sc["pfr"]["n"] = 1

    if args.career_min is not None:
        sc.setdefault("career", {})["s"] = str(args.career_min)
    if args.career_max is not None:
        sc.setdefault("career", {})["e"] = str(args.career_max)

    matched = {
        "job_category": mark_matching_nodes(sc, "jobtype", args.job_category),
        "work_area": mark_matching_nodes(sc, "workarea", args.work_area),
        "residential_area": mark_matching_nodes(sc, "residentialarea", args.residential_area),
    }
    return sc, matched


def post_search(sc: dict[str, Any]) -> str:
    body = urllib.parse.urlencode({"searchCondition": json.dumps(sc, ensure_ascii=False)}).encode()
    return fetch(
        BASE_URL + AJAX_PATH,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        },
    )


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


ACTION_CONTROL_RE = re.compile(
    r"^(?:스크랩\s*\d*|저장하기|닫기|포지션\s*제안|메모하기|프로필\s*확인|이력서\s*확인|펼쳐보기|접기|이전|다음)$"
)
ACTION_CONTROL_INLINE_RE = re.compile(
    r"(?:스크랩\s*\d+|저장하기|닫기|포지션\s*제안|메모하기|프로필\s*확인|이력서\s*확인|펼쳐보기|접기|이전|다음)"
)


def is_action_control_label(value: str) -> bool:
    label = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return bool(label and ACTION_CONTROL_RE.match(label))


def filter_action_control_text(value: str) -> str:
    lines = []
    for line in value.splitlines():
        label = line.strip()
        if not label or is_action_control_label(label):
            continue
        label = ACTION_CONTROL_INLINE_RE.sub(" ", label)
        label = re.sub(r"\s+", " ", label).strip()
        if label:
            lines.append(label)
    return "\n".join(lines).strip()


def row_contains_other_resume(candidate_markup: str, rno: str) -> bool:
    refs: list[str] = []
    for href_rno, data_rno in re.findall(r"rNo=(\d+)|data-rno=[\"'](\d+)[\"']", candidate_markup):
        refs.append(href_rno or data_rno)
    return any(ref != rno for ref in refs)


def extract_regex_candidate_markup(markup: str, match: re.Match[str], rno: str) -> str:
    row_start = markup.rfind("<tr", 0, match.start())
    if row_start >= 0:
        row_open_end = markup.find(">", row_start, match.start())
        row_end = markup.find("</tr>", match.end())
        row_open = markup[row_start : row_open_end + 1] if row_open_end >= 0 else ""
        if row_end >= 0 and f'data-rno="{rno}"' in row_open:
            return markup[row_start : row_end + len("</tr>")]

    booth_start = markup.rfind('<div class="booth"', 0, match.start())
    if booth_start >= 0:
        next_booth = markup.find('<div class="booth"', match.end())
        section_end = markup.find('</section>', match.end())
        end_candidates = [pos for pos in (next_booth, section_end) if pos >= 0]
        booth_end = min(end_candidates) if end_candidates else min(len(markup), match.end() + 2500)
        booth = markup[booth_start:booth_end]
        if not row_contains_other_resume(booth, rno):
            return booth

    start = max(0, match.start() - 300)
    end = min(len(markup), match.end() + 1200)
    return markup[start:end]


def parse_with_bs4(markup: str, limit: int) -> list[Candidate] | None:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return None

    soup = BeautifulSoup(markup, "html.parser")
    candidates: list[Candidate] = []
    seen: set[str] = set()

    for link in soup.select('a[href*="/corp/person/find/resume/view?rNo="]'):
        href = link.get("href", "")
        m = re.search(r"rNo=(\d+)", href)
        if not m:
            continue
        rno = m.group(1)
        if rno in seen:
            continue
        seen.add(rno)

        container = (
            link.find_parent("tr", attrs={"data-rno": rno})
            or link.find_parent(class_=re.compile(r"(^|\s)booth(\s|$)", re.I))
            or link.parent
        )
        if container and row_contains_other_resume(str(container), rno):
            # Broad ancestors such as tblSearchList/personList can contain several resumes.
            # Falling back to the link itself is safer than mixing candidate evidence.
            container = link.parent

        raw = clean_text(str(container)) if container else clean_text(str(link))
        texts = []
        for node in container.find_all(["dt", "dd", "p", "span", "li"]) if container else []:
            label = node.get_text(" ", strip=True)
            if label and not is_action_control_label(label):
                texts.append(label)
        for btn in container.select(".keywordSkill button, .keywordBox button") if container else []:
            label = btn.get_text(" ", strip=True)
            if label and not is_action_control_label(label):
                texts.append(label)
        text_join = " | ".join(dict.fromkeys(texts))

        name = ""
        meta = ""
        name_scope = container.select_one(".nameAge") if container else None
        dt = (name_scope or container).find("dt") if container else None
        if dt:
            name = dt.get_text(" ", strip=True)
            dd = dt.find_next("dd")
            if dd:
                meta = dd.get_text(" ", strip=True)
        if not name:
            m_name = re.search(r"([가-힣A-Za-z]OO)\s*\(([^)]*)\)", raw)
            if m_name:
                name = m_name.group(1)
                meta = "(" + m_name.group(2) + ")"

        skills = []
        for btn in container.select(".keywordSkill button, .keywordBox button") if container else []:
            label = btn.get_text(" ", strip=True)
            if label and not is_action_control_label(label):
                skills.append(label)

        candidates.append(
            Candidate(
                rno=rno,
                url=urllib.parse.urljoin(BASE_URL, href),
                name=name,
                meta=meta,
                career=(container.select_one(".career").get_text(" ", strip=True) if container and container.select_one(".career") else ""),
                skills=", ".join(skills[:25]),
                raw_summary=filter_action_control_text(text_join[:1000] or raw[:1000]),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def parse_with_regex(markup: str, limit: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="(?P<href>/corp/person/find/resume/view\?rNo=(?P<rno>\d+))"', markup):
        rno = m.group("rno")
        if rno in seen:
            continue
        seen.add(rno)
        raw_markup = extract_regex_candidate_markup(markup, m, rno)
        raw = clean_text(raw_markup)
        name = ""
        meta = ""
        nm = re.search(r"([가-힣A-Za-z]OO)\s*\(([^)]*)\)", raw)
        if nm:
            name = nm.group(1)
            meta = "(" + nm.group(2) + ")"
        candidates.append(
            Candidate(
                rno=rno,
                url=urllib.parse.urljoin(BASE_URL, m.group("href")),
                name=name,
                meta=meta,
                raw_summary=filter_action_control_text(raw[:1000]),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def parse_candidates(markup: str, limit: int) -> list[Candidate]:
    parsed = parse_with_bs4(markup, limit)
    if parsed is not None:
        return parsed
    return parse_with_regex(markup, limit)


def print_markdown(candidates: list[Candidate], matched: dict[str, Any], args: argparse.Namespace) -> None:
    print(f"# 잡코리아 인재검색 결과\n")
    print(f"- 검색어: {', '.join(args.keyword + args.and_keyword + args.or_keyword) or '(없음)'}")
    print(f"- 제외어: {', '.join(args.exclude_keyword) or '(없음)'}")
    if any(matched.values()):
        print(f"- 매칭된 필터: {json.dumps(matched, ensure_ascii=False)}")
    print(f"- 결과 수: {len(candidates)}")
    print("- 주의: 이름/회사명은 잡코리아 공개 화면 기준으로 마스킹되어 있으며, 상세 이력서 확인·포지션 제안은 기업회원 로그인/권한/사용자 확인이 필요합니다.\n")
    for idx, c in enumerate(candidates, 1):
        bits = [c.name, c.meta, c.career]
        title = " ".join(x for x in bits if x).strip() or f"rNo={c.rno}"
        print(f"## {idx}. {title}")
        print(f"- URL: {c.url}")
        if c.skills:
            print(f"- 키워드/스킬: {c.skills}")
        summary = c.raw_summary.replace("\n", " ")
        if summary:
            print(f"- 요약: {summary[:500]}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Search public JobKorea talent summaries")
    parser.add_argument("--keyword", "-k", action="append", default=[], help="통합검색 키워드. 여러 번 지정 가능")
    parser.add_argument("--and-keyword", action="append", default=[], help="AND 키워드")
    parser.add_argument("--or-keyword", action="append", default=[], help="OR 키워드")
    parser.add_argument("--exclude-keyword", action="append", default=[], help="제외 키워드")
    parser.add_argument("--job-category", action="append", default=[], help="직무 대분류명 예: AI·개발·데이터")
    parser.add_argument("--work-area", action="append", default=[], help="희망 근무지역 예: 서울, 강남구, 경기")
    parser.add_argument("--residential-area", action="append", default=[], help="거주지역 예: 서울, 성남시 분당구")
    parser.add_argument("--career-min", type=int, help="최소 경력 연수")
    parser.add_argument("--career-max", type=int, help="최대 경력 연수")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--limit", type=int, default=20, choices=[10, 20, 30, 50, 100])
    parser.add_argument("--sort", default="0", help="잡코리아 sf 정렬 코드. 기본 0")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args()

    if not (args.keyword or args.and_keyword or args.or_keyword or args.job_category or args.work_area or args.residential_area):
        parser.error("최소 하나 이상의 --keyword, --job-category, --work-area 등을 지정하세요")

    sc, matched = build_search_condition(args)
    markup = post_search(sc)
    if "로그인" in clean_text(markup)[:500] and "인재" not in clean_text(markup)[:2000]:
        raise RuntimeError("잡코리아가 로그인/차단 화면을 반환했습니다")
    candidates = parse_candidates(markup, args.limit)

    if args.json:
        print(json.dumps({"matched_filters": matched, "candidates": [asdict(c) for c in candidates]}, ensure_ascii=False, indent=2))
    else:
        print_markdown(candidates, matched, args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
