#!/usr/bin/env python3
"""
나라장터 사전규격 규격서 의견 조회/감시 스크립트

사용법:
  python g2b_bfspec_watch.py dump  R26BD00287517              # 1) 응답 구조 확인 (최초 1회)
  python g2b_bfspec_watch.py list  R26BD00287517              # 2) 의견 제목·등록일시 출력
  python g2b_bfspec_watch.py watch R26BD00287517 --interval 30   # 3) 30분마다 새 의견 감시

필요 패키지: pip install requests
"""
import argparse
import hashlib
import html
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE = "https://www.g2b.go.kr"
API_URL = BASE + "/pn/pnz/pnza/BfSpec/selectBfSpec.do"
PAGE_URL = BASE + "/link/PRVA004_02/single/?bfSpecRegNo={no}"

# ─────────────────────────────────────────────────────────────
# ① 요청 본문 (등록번호 자리는 {no})
BODY_TEMPLATE = '{"dlParamM":{"bfSpecRegNo":"{no}","prcmBsneSeCd":"","opnnSqno":"","jobType":""}}'

# ② dump 실행 결과를 보고 채울 값
LIST_PATH = "dlBfSpecOpnnL"           # 규격서 의견 목록
TITLE_KEY = "opnnNm"                  # 의견 제목
DATE_KEY = "inptDt"                   # 등록 일시
ID_KEYS = ("opnnSqno", "ansSqno")     # 의견 순번 + 답변 순번으로 각 글을 식별
INCLUDE_ANSWERS = True                # False 로 바꾸면 발주기관 답변은 제외하고 의견만 표시

# ③ 알림 (선택): Slack 또는 Discord 웹훅 URL. 비워 두면 콘솔 출력만 합니다.
WEBHOOK_URL = ""
# ─────────────────────────────────────────────────────────────

STATE_DIR = Path(".g2b_state")

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": BASE,
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"),
    "Menu-Info": ('{"menuNo":"01141","menuCangVal":"PRVA004_02",'
                  '"bsneClsfCd":"%EC%97%85130025","scrnNo":"05929"}'),
    "submissionid": "mf_wfm_container_sbmSrch",
    "Usr-Id": "null",
}


def fetch(no: str) -> dict:
    """공고 페이지에 먼저 접속해 새 세션 쿠키를 받은 뒤 API를 호출한다."""
    s = requests.Session()
    s.headers.update(HEADERS)
    page = PAGE_URL.format(no=no)
    s.get(page, timeout=20)
    s.cookies.set("infoSysCd", "%EC%A0%95010029", domain="www.g2b.go.kr", path="/")
    body = BODY_TEMPLATE.replace("{no}", no)
    r = s.post(API_URL, data=body.encode("utf-8"), headers={"Referer": page}, timeout=20)
    if r.status_code != 200:
        sys.exit(f"HTTP {r.status_code} 응답:\n{r.text[:1000]}\n\n보낸 본문: {body}")
    try:
        return r.json()
    except ValueError:
        sys.exit(f"JSON이 아닌 응답입니다 (HTTP {r.status_code}):\n{r.text[:500]}")


def find_lists(obj, path=""):
    """응답 안의 '딕셔너리 리스트'를 모두 찾아 (경로, 리스트)로 돌려준다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from find_lists(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        yield path, obj


def get_path(data, path):
    for key in path.split("."):
        data = data.get(key) if isinstance(data, dict) else None
    return data or []


def extract(data: dict) -> list:
    items = []
    for row in get_path(data, LIST_PATH):
        is_answer = int(row.get("ansSqno") or 0) > 0
        if is_answer and not INCLUDE_ANSWERS:
            continue
        title = html.unescape(str(row.get(TITLE_KEY, ""))).strip()
        date = str(row.get(DATE_KEY, "")).strip()
        uid = "-".join(str(row.get(k)) for k in ID_KEYS)
        if uid.replace("-", "").replace("None", "") == "":
            uid = hashlib.sha1(f"{title}|{date}".encode()).hexdigest()
        items.append({"id": uid, "title": title, "date": date,
                      "kind": "답변" if is_answer else "의견",
                      "seq": row.get("opnnSqno")})
    return sorted(items, key=lambda x: x["date"])


def deadline(data: dict) -> str:
    m = data.get("dlBfSpecM") or {}
    return f"{m.get('bfSpecNm', '')} / 의견 등록 마감: {m.get('opnnRegDdlnDt', '-')}"


def cmd_dump(no):
    data = fetch(no)
    out = Path(f"dump_{no}.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"전체 응답 저장: {out}\n")
    found = False
    for path, rows in find_lists(data):
        found = True
        print(f"[목록] {path}  ({len(rows)}건)")
        for k, v in rows[0].items():
            print(f"    {k:<25} {str(v)[:50]}")
        print()
    if not found:
        print("목록 형태의 데이터가 없습니다. 이 요청은 공고 상세용일 수 있으니, "
              "'규격서 의견' 탭을 눌렀을 때 발생하는 다른 요청을 찾아보세요.")


def cmd_list(no):
    data = fetch(no)
    items = extract(data)
    print(deadline(data) + "\n")
    if not items:
        print("등록된 의견이 없습니다.")
    for it in items:
        print(f"{it['date']}  [{it['kind']} {it['seq']}]  {it['title']}")


def notify(msg):
    print(msg)
    if WEBHOOK_URL:
        try:
            requests.post(WEBHOOK_URL, json={"text": msg, "content": msg}, timeout=10)
        except requests.RequestException as e:
            print(f"  (웹훅 전송 실패: {e})")


def cmd_watch(no, interval):
    STATE_DIR.mkdir(exist_ok=True)
    state_file = STATE_DIR / f"{no}.json"
    seen = set(json.loads(state_file.read_text())) if state_file.exists() else None

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            items = extract(fetch(no))
            if seen is None:
                seen = {it["id"] for it in items}
                print(f"[{now}] 기준 상태 저장: 기존 의견 {len(seen)}건")
            else:
                new = [it for it in items if it["id"] not in seen]
                for it in new:
                    notify(f"[새 규격서 {it['kind']}] {no}\n{it['date']}  {it['title']}\n{PAGE_URL.format(no=no)}")
                    seen.add(it["id"])
                if not new:
                    print(f"[{now}] 변동 없음 (총 {len(items)}건)")
            state_file.write_text(json.dumps(sorted(seen)))
        except Exception as e:
            print(f"[{now}] 조회 실패: {e}")
        time.sleep(interval * 60)


def main():
    p = argparse.ArgumentParser(description="나라장터 사전규격 규격서 의견 조회/감시")
    p.add_argument("command", choices=["dump", "list", "watch"])
    p.add_argument("reg_no", help="사전규격 등록번호 (예: R26BD00287517)")
    p.add_argument("--interval", type=int, default=30, help="watch 조회 간격(분), 최소 5")
    a = p.parse_args()

    if a.command == "dump":
        cmd_dump(a.reg_no)
    elif a.command == "list":
        cmd_list(a.reg_no)
    else:
        cmd_watch(a.reg_no, max(a.interval, 5))


if __name__ == "__main__":
    main()
