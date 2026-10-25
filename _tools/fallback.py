#!/usr/bin/env python3
"""fallback.py - Pages 게시가 안 된 문서를 DB(JSON)만으로 메일 본문 + 첨부 zip으로 만든다.

publish.py mail 과 같은 역할이지만 저장소의 b/<id>/ 대신 ArtifactData out_dir 로 받은 JSON을 읽는다.
  <db>/briefings/<id>.json  {html, mailHtml?, date, kind}
  <db>/index/<id>.json      {title, headlines[], insight, kind, date, window?, exitRelated?}

사용
  python3 _tools/fallback.py <main_id> [extra_id ...] --db /home/claude/db [--full] [--note 문구] --out body.html [--zip out.zip]

출력
  body.html : 메인 문서 = mailHtml(섹션 1~7 인라인, 없으면 index 기반 표).
              extras   = 날짜별 "한눈에 + 시사점" 표 + 첨부 안내. 맨 아래 아카이브/관리자 링크.
              --full   = 본문은 같고, 상단 띠에 "전체 본문(8, 9 포함)은 첨부 html" 안내.
  out.zip   : 메인 + extras 전부의 원문 html (briefing_<id>.html). 메일 크기 문제와 무관하게 전체 본문이 항상 전달된다.
"""
import json, os, re, sys, html as H, zipfile

BASE_URL = 'https://yhjin1993-art.github.io/Briefing-Archive/'
ADMIN_URL = 'https://claude.ai/artifact/HdKe1ai7JaQeDULyWEYANG'
KIND = {'daily': '일간', 'weekly': '주간', 'monthly': '월간', 'quarterly': '분기', 'annual': '연간'}
F = "font-family:'Malgun Gothic','Apple SD Gothic Neo',Arial,sans-serif;"

def load(db, coll, bid):
    p = os.path.join(db, coll, f'{bid}.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}

def esc(s):
    return H.escape(str(s or ''))

def table(idx):
    """index 문서의 headlines + insight 를 간단한 표로."""
    rows = ''.join(
        f'<tr><td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;font-weight:700;white-space:nowrap;vertical-align:top">{esc(h.get("who"))}</td>'
        f'<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top">{esc(h.get("act"))} {esc(h.get("target"))}'
        f'{(" <span style=\"color:#6b7280\">(" + esc(h.get("cond")) + ")</span>") if h.get("cond") else ""}'
        f'<div style="color:#374151;font-size:12px;margin-top:2px">{esc(h.get("meaning"))}</div></td></tr>'
        for h in idx.get('headlines', []))
    t = f'<table width="100%" cellpadding="0" cellspacing="0" style="{F}font-size:13px;border-top:1px solid #111827">{rows}</table>' if rows else ''
    ins = idx.get('insight')
    if ins:
        t += f'<div style="{F}font-size:13px;margin-top:8px;padding:8px 10px;background:#f1f5f9;border-left:3px solid #0f3b66"><b>제뉴원 시사점</b> {esc(ins)}</div>'
    return t

def strip_full(html):
    """원문 html 을 첨부용으로 가볍게: script, fab 버튼 제거."""
    html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S | re.I)
    html = re.sub(r'<div class="fab"[^>]*>.*?</div>\s*', '', html, flags=re.S)
    return html

def body(db, bid, extras=(), full=False, note=''):
    b, i = load(db, 'briefings', bid), load(db, 'index', bid)
    kind = KIND.get(i.get('kind') or b.get('kind') or 'daily', '일간')
    date = i.get('date') or b.get('date') or bid
    title = i.get('title') or f'전략기획팀 Briefing {date}'
    top = (f'<div style="{F}font-size:13px;color:#374151;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:8px 12px;margin:0 0 12px">'
           f'<b>{esc(date)} {kind}</b> {"전체판" if full else "요약판"} &nbsp;|&nbsp; '
           f'<a href="{BASE_URL}" style="color:#0f3b66">지난 브리핑</a> &nbsp;|&nbsp; <a href="{BASE_URL}glossary.html" style="color:#0f3b66">용어집</a>'
           f'<div style="font-size:12px;color:#6b7280;margin-top:2px">웹 게시가 늦어져 본문을 인라인으로 보냅니다. '
           + ('8 언급 기업, 9 기본 개념을 포함한 전체 본문은 첨부 zip 안의 html 파일을 브라우저로 열어주세요.' if full else '첨부 zip 안에 원문 html(오프라인용)이 있습니다.')
           + '</div>'
           + (f'<div style="font-size:12px;color:#b45309;margin-top:2px">{esc(note)}</div>' if note else '') + '</div>')
    main = b.get('mailHtml') or (f'<div style="{F}font-size:18px;font-weight:800;margin:0 0 8px">{esc(title)}</div>' + table(i))
    ex = ''
    for eid in extras:
        ei, eb = load(db, 'index', eid), load(db, 'briefings', eid)
        ek = KIND.get(ei.get('kind') or eb.get('kind') or 'daily', '일간')
        ed = ei.get('date') or eb.get('date') or eid
        ex += (f'<div style="{F}font-size:14px;font-weight:800;margin:22px 0 6px;padding-top:12px;border-top:2px solid #111827">'
               f'{esc(ed)} {ek} <span style="font-size:12px;color:#6b7280;font-weight:500">'
               f'{"리포트" if ek != "일간" else "비영업일 생성분"} 요약, 전체는 첨부 briefing_{esc(eid)}.html</span></div>'
               f'<div style="{F}font-size:13px;color:#374151;margin:0 0 6px">{esc(ei.get("title"))}</div>' + table(ei))
    foot = (f'<div style="{F}font-size:12px;color:#6b7280;margin-top:14px;padding-top:8px;border-top:1px solid #e5e7eb">'
            f'아카이브 <a href="{BASE_URL}" style="color:#0f3b66">{BASE_URL}</a> &nbsp;|&nbsp; 관리자 페이지 <a href="{ADMIN_URL}" style="color:#0f3b66">열기</a><br>'
            f'요약과 링크만 제공, 기사 원문 복사 없음. 계산치=작성자 산출, 추정/보도 기준=미확인.</div>')
    return f'<div style="max-width:700px;margin:0 auto">{top}{main}{ex}{foot}</div>'

def make_zip(db, ids, out_path):
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for bid in ids:
            b = load(db, 'briefings', bid)
            if b.get('html'):
                z.writestr(f'briefing_{bid}.html', strip_full(b['html']))
    return out_path

if __name__ == '__main__':
    a = sys.argv[1:]
    def opt(k, d=None):
        return a[a.index(k) + 1] if k in a else d
    vals = {opt('--db'), opt('--out'), opt('--zip'), opt('--note')}
    ids = [x for x in a if not x.startswith('--') and x not in vals]
    if not ids or not opt('--db'):
        print(__doc__); sys.exit(1)
    db, out = opt('--db'), opt('--out', 'body.html')
    open(out, 'w', encoding='utf-8').write(body(db, ids[0], ids[1:], full='--full' in a, note=opt('--note', '')))
    print('body', out, os.path.getsize(out), 'bytes')
    if opt('--zip'):
        zp = make_zip(db, ids, opt('--zip')); print('zip', zp, os.path.getsize(zp), 'bytes')
