# -*- coding: utf-8 -*-
"""publish.py - 브리핑을 GitHub Pages 저장소에 게시하고 메일 본문(이미지)을 만든다.

구조 (저장소 루트 = Pages 루트)
  index.html            목록 페이지 (자동 재생성, noindex)
  latest/index.html     최신 일간 브리핑으로 바로 이동 (PC 시작 팝업용, 자동 재생성)
  search.html, search.json  아카이브 내부 검색 (본문 전문)
  glossary.html         용어집 (모든 브리핑 termDefs 누적, 검색)
  b/<id>/index.html     브리핑 원문 (DB briefings.html 그대로 + noindex 메타)
  b/<id>/p1.jpg ...     페이지별 캡처 (메일 본문용, 폭 720px, 1.5배)
  b/<id>/meta.json      {id, date, kind, title, pages:[{file, w, h, ref}]}
  _tools/               이 스크립트와 폰트 캐시

사용 (저장소 루트에서)
  python3 publish.py add  <briefing.json> [--index <index.json>] [--push]   # ArtifactData out_dir로 받은 briefings/<id>.json (+ index/<id>.json: 용어집/시사점)
  python3 publish.py mail <id> [extra_id ...] [--full] [--note 문구] --out mail.html [--zip out.zip]   # 메일 본문(이미지) + 첨부 zip
  python3 publish.py index [--push]                    # 목록만 재생성
<id> = index 문서 id (일간은 YYYY-MM-DD, 리포트는 weekly-YYYY-MM-DD 등)
"""
import json, os, re, sys, subprocess, html as H, asyncio, glob, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = 'https://yhjin1993-art.github.io/Briefing-Archive/'
KIND = {'daily': '일간', 'weekly': '주간', 'monthly': '월간', 'quarterly': '분기', 'annual': '연간'}
NOINDEX = '<meta name="robots" content="noindex,nofollow">'
FONT_DIR = os.path.join(ROOT, '_tools', 'fnt')

# ---------------------------------------------------------------- fonts (이미지 렌더링용, 저장소에는 안 올림)
def ensure_fonts():
    """Nanum Gothic woff2(fontsource)를 npm pack으로 받아 _tools/fnt/package 에 둔다. 캐시 있으면 생략.
    400/700/800을 별도 패밀리명(NGR/NGB)으로 선언해 합성 볼드를 피한다(= 캡처 선명도)."""
    css = os.path.join(FONT_DIR, 'package', 'ng.css')
    if os.path.exists(css):
        return css
    os.makedirs(FONT_DIR, exist_ok=True)
    subprocess.run(['npm', 'pack', '@fontsource/nanum-gothic'], cwd=FONT_DIR, check=True, capture_output=True)
    tgz = glob.glob(os.path.join(FONT_DIR, '*.tgz'))[0]
    subprocess.run(['tar', 'xzf', tgz], cwd=FONT_DIR, check=True)
    p = os.path.join(FONT_DIR, 'package')
    def mk(src, fam):
        c = open(os.path.join(p, src), encoding='utf-8').read()
        c = c.replace("font-family: 'Nanum Gothic'", f"font-family: '{fam}'")
        return re.sub(r'font-weight: \d+;', 'font-weight: 100 900;', c)
    open(css, 'w', encoding='utf-8').write(mk('400.css', 'NGR') + mk('700.css', 'NGB') + mk('800.css', 'NGX'))
    return css

def render_html(html, width_css='720px'):
    """캡처용 HTML: 웹폰트 import 제거, 로컬 Nanum 폰트, 폭 720px, 굵기는 별도 패밀리."""
    css = ensure_fonts()
    h = re.sub(r"@import url\([^)]*\);", '', html)
    h = h.replace('font-family:"Pretendard Variable",Pretendard,', 'font-family:"NGR",')
    h = re.sub(r'font-weight:(800|700)', lambda m: f'font-weight:{m.group(1)};font-family:"NGB"', h)
    h = h.replace('.page{background:var(--bg);width:210mm;', f'.page{{background:var(--bg);width:{width_css};')
    h = h.replace('padding:12mm 14mm 12mm;', 'padding:20px 24px 14px;')
    extra = 'b,strong,th,h1,h2,h3,h4,h5{font-family:"NGB"} .fab{display:none!important} html,body{background:#fff}'
    return h.replace('<head>', f'<head><link rel="stylesheet" href="file://{css}"><style>{extra}</style>', 1)

def _shrink_png(path):
    """텍스트 위주 캡처는 팔레트 PNG가 JPEG보다 훨씬 작고 선명. 128색 양자화 + 최대 압축."""
    from PIL import Image
    im = Image.open(path).convert('RGB')
    im = im.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    im.save(path, optimize=True)

async def _shoot(render_path, out_dir):
    from playwright.async_api import async_playwright
    pages = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 760, 'height': 1200}, device_scale_factor=1.5)
        await pg.goto('file://' + render_path, wait_until='load')
        await pg.evaluate('document.fonts.ready')
        els = await pg.query_selector_all('.page')
        for i, el in enumerate(els, 1):
            f = f'p{i}.png'
            path = os.path.join(out_dir, f)
            await el.screenshot(path=path, type='png')
            _shrink_png(path)
            box = await el.bounding_box()
            ref = await el.evaluate("e => !!(e.querySelector('section.co, section.long, .refband'))")
            pages.append({'file': f, 'w': round(box['width']), 'h': round(box['height']), 'ref': ref})
        await b.close()
    return pages

# ---------------------------------------------------------------- add
def add(json_path, push=False, index_json=None):
    d = json.load(open(json_path, encoding='utf-8'))
    idx = json.load(open(index_json, encoding='utf-8')) if index_json and os.path.exists(index_json) else {}
    bid = d.get('id') or os.path.splitext(os.path.basename(json_path))[0]
    html = d['html']
    date, kind = d.get('date', bid[-10:]), d.get('kind', 'daily')
    m = re.search(r'<title>(.*?)</title>', html, re.S)
    title = H.unescape(m.group(1)).strip() if m else bid
    out = os.path.join(ROOT, 'b', bid)
    os.makedirs(out, exist_ok=True)
    pub = html.replace('<head>', '<head>' + NOINDEX, 1)
    pub = with_nav(pub, '../../', kind)
    open(os.path.join(out, 'index.html'), 'w', encoding='utf-8').write(pub)
    rendered = render_html(html)  # ensure_fonts()가 FONT_DIR을 만든다
    rp = os.path.join(FONT_DIR, f'_render_{bid}.html')
    open(rp, 'w', encoding='utf-8').write(rendered)
    pages = asyncio.run(_shoot(rp, out))
    os.remove(rp)
    terms = idx.get('termDefs') or [{'term': t, 'en': '', 'def': '', 'note': '', 'ex': ''} for t in idx.get('terms', [])]
    meta = {'id': bid, 'date': date, 'kind': kind, 'title': title, 'pages': pages, 'terms': terms,
            'insight': idx.get('insight', ''), 'headlines': idx.get('headlines', []),
            'publishedAt': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='minutes')}
    json.dump(meta, open(os.path.join(out, 'meta.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    build_index(); build_glossary(); build_search()
    if push:
        git_push(f'{bid} 게시')
    print('published', bid, len(pages), 'pages')
    return meta


# ---------------------------------------------------------------- nav (목록 탭: 일간/주간/월간/용어집)
NAV_CSS = (".bnav{position:sticky;top:0;z-index:50;display:flex;gap:4px;align-items:center;flex-wrap:wrap;padding:8px 12px;background:#fff;"
           "border-bottom:1px solid #e5e7eb;font-family:\"Pretendard Variable\",Pretendard,-apple-system,\"Apple SD Gothic Neo\",\"Malgun Gothic\",sans-serif;font-size:13px}"
           ".bnav a{padding:5px 12px;border-radius:999px;border:none!important;box-shadow:none!important;background:none;text-decoration:none;color:#374151;font-weight:700}.bnav a:hover{background:#f3f4f6}"
           ".bnav a.on{background:#0f3b66!important;color:#fff!important}.bnav .home{color:#111827;font-weight:800;margin-right:6px}.bnav .sp{flex:1}"
           ".bnav .td{color:#0f3b66}.bnav form{margin:0 4px 0 8px}.bnav input{font:inherit;font-size:13px;width:220px;max-width:46vw;padding:6px 12px;border:1px solid #d1d5db;border-radius:999px;background:#f9fafb;color:#111827;outline:none}"".bnav input:focus{border-color:#0f3b66;background:#fff}@media print{.bnav{display:none!important}}")
NAV_TABS = [('daily', '일간'), ('weekly', '주간'), ('monthly', '월간'), ('quarterly', '분기'), ('annual', '연간'), ('glossary', '용어집')]

def nav_html(prefix='', on=''):
    """prefix: 루트까지 상대경로('' 또는 '../../'). on: 강조할 탭."""
    tabs = ''.join(f'<a class="{"on" if on == k else ""}" href="{prefix}{"glossary.html" if k == "glossary" else "#" + k}">{v}</a>'
                   for k, v in NAV_TABS)
    q = '<form action="' + prefix + 'search.html" method="get"><input name="q" type="search" placeholder="지난 브리핑 검색" autocomplete="off"></form>'
    return (f'<!--bnav--><style>{NAV_CSS}</style><nav class="bnav"><a class="home" href="{prefix or "./"}">Briefing Archive</a>{tabs}{q}'
            f'<span class="sp"></span><a class="td" href="{prefix}latest/">오늘 브리핑</a></nav><!--/bnav-->')

def with_nav(html, prefix, on=''):
    html = re.sub(r'<!--bnav-->.*?<!--/bnav-->', '', html, flags=re.S)
    return re.sub(r'(<body[^>]*>)', lambda m: m.group(1) + nav_html(prefix, on), html, count=1)

def nav_all():
    """기존 b/*/index.html 전부에 탭 바 적용(멱등)."""
    for f in glob.glob(os.path.join(ROOT, 'b', '*', 'index.html')):
        try:
            k = json.load(open(os.path.join(os.path.dirname(f), 'meta.json'), encoding='utf-8')).get('kind', 'daily')
        except Exception:
            k = 'daily'
        h = open(f, encoding='utf-8').read()
        open(f, 'w', encoding='utf-8').write(with_nav(h, '../../', k))

# ---------------------------------------------------------------- index
def all_meta():
    ms = []
    for f in glob.glob(os.path.join(ROOT, 'b', '*', 'meta.json')):
        try:
            ms.append(json.load(open(f, encoding='utf-8')))
        except Exception:
            pass
    return sorted(ms, key=lambda x: (x['date'], x['kind'] != 'daily'), reverse=True)

INDEX_CSS = """*{box-sizing:border-box}body{margin:0;background:#f3f4f6;color:#111827;font-family:"Pretendard Variable",Pretendard,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;font-size:14px;line-height:1.5;word-break:keep-all}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px}h1{margin:0 0 4px;font-size:20px;font-weight:800;letter-spacing:-0.02em}.sub{color:#6b7280;font-size:12px;margin-bottom:16px}
.m{margin:18px 0 6px;font-size:13px;font-weight:800;color:#6b7280}.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden}
.row{display:grid;grid-template-columns:96px 52px 1fr auto;gap:10px;align-items:center;padding:10px 14px;border-top:1px solid #e5e7eb;text-decoration:none;color:inherit}.row:first-child{border-top:none}.row:hover{background:#f9fafb}
.d{font-weight:700;white-space:nowrap}.k{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:#e8f0f8;color:#0f3b66;text-align:center}.k.weekly{background:#e3f3f1;color:#0f766e}.k.monthly{background:#fdf1e0;color:#b45309}.k.quarterly{background:#f3e8ff;color:#7e22ce}.k.annual{background:#fde8e8;color:#b91c1c}
.t{color:#374151;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.n{color:#9ca3af;font-size:12px;white-space:nowrap}
@media(max-width:600px){.row{grid-template-columns:84px 48px 1fr}.n{display:none}}
.ft{margin-top:20px;color:#9ca3af;font-size:12px}.grp:first-of-type .m{margin-top:4px}"""

def build_index():
    ms = all_meta()
    by = {}
    for m in ms:
        by.setdefault(m['date'][:7], []).append(m)
    body = ''
    for mon in sorted(by, reverse=True):
        rows = ''.join(
            f'<a class="row" data-k="{H.escape(m["kind"])}" href="b/{H.escape(m["id"])}/"><span class="d">{H.escape(m["date"])}</span>'
            f'<span class="k {H.escape(m["kind"])}">{KIND.get(m["kind"], m["kind"])}</span>'
            f'<span class="t">{H.escape(m["title"].replace("전략기획팀 ", "").split(" | ")[0])}</span>'
            f'<span class="n">{len(m["pages"])}쪽</span></a>' for m in by[mon])
        body += f'<div class="grp"><div class="m">{mon}</div><div class="card">{rows}</div></div>'
    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{NOINDEX}
<title>Strategy Daily Briefing Archive</title><style>{INDEX_CSS}</style></head><body>{nav_html('', 'daily')}<div class="wrap">
<h1>Daily Briefing Archive</h1><div class="sub">전략기획팀 딜 / PE / 제약 CDMO 브리핑 {len(ms)}건. 날짜를 누르면 원문(인쇄 가능)이 열립니다. &nbsp;<a href="setup/briefing_popup_on.reg" download style="color:#6b7280">PC 켤 때 자동으로 열기(설치 파일)</a></div>
{body}<div class="empty" id="empty" style="display:none;color:#9ca3af;padding:24px 4px">아직 발행된 문서가 없습니다.</div><div class="ft">요약과 링크만 제공, 기사 원문 복사 없음. 계산치는 작성자 산출, 추정 및 보도 기준 수치는 미확인.</div></div>
<script>(function(){{function f(){{var k=(location.hash||'#daily').slice(1);if(['daily','weekly','monthly','quarterly','annual'].indexOf(k)<0)k='daily';
document.querySelectorAll('.bnav a').forEach(function(a){{var h=a.getAttribute('href');a.classList.toggle('on',h==='#'+k);}});
var n=0;document.querySelectorAll('.row').forEach(function(r){{var s=r.getAttribute('data-k')===k;r.style.display=s?'':'none';if(s)n++;}});
document.querySelectorAll('.grp').forEach(function(g){{g.style.display=[].some.call(g.querySelectorAll('.row'),function(r){{return r.style.display!=='none';}})?'':'none';}});
document.getElementById('empty').style.display=n?'none':'';}}window.addEventListener('hashchange',f);f();}})();</script></body></html>"""
    open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8').write(doc)
    build_latest(ms)

def build_latest(ms=None):
    """latest/ = 가장 최근 일간 브리핑으로 즉시 이동. 시작 팝업 바로가기는 이 주소를 연다."""
    ms = ms if ms is not None else all_meta()
    d = [m for m in ms if m['kind'] == 'daily']
    if not d:
        return
    href = '../b/' + d[0]['id'] + '/'
    doc = (f'<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">{NOINDEX}'
           f'<meta http-equiv="refresh" content="0;url={href}"><title>Latest Briefing</title>'
           f'<script>location.replace("{href}")</script></head>'
           f'<body><a href="{href}">최신 브리핑 열기</a></body></html>')
    os.makedirs(os.path.join(ROOT, 'latest'), exist_ok=True)
    open(os.path.join(ROOT, 'latest', 'index.html'), 'w', encoding='utf-8').write(doc)


GLOSS_CSS = INDEX_CSS + """
.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
input,select{font:inherit;font-size:13px;padding:7px 10px;border:1px solid #e5e7eb;border-radius:8px;background:#fff;color:#111827}
input{min-width:240px}
.tr{display:grid;grid-template-columns:92px 1fr;gap:4px 12px;padding:10px 14px;border-top:1px solid #e5e7eb}.tr:first-child{border-top:none}
.tr .dt{color:#9ca3af;font-size:12px;white-space:nowrap;padding-top:2px}
.tr .k{font-weight:800;font-size:14px}.tr .k span{font-weight:500;color:#6b7280;font-size:12px;margin-left:6px}
.tr .df{color:#374151;font-size:13px;margin-top:2px}.tr .nt{color:#0f3b66;font-size:12px;margin-top:2px}.tr .ex{color:#6b7280;font-size:12px;margin-top:2px}
.tr .go{font-size:11px;color:#9ca3af;text-decoration:none;margin-left:6px}
@media(max-width:600px){.tr{grid-template-columns:1fr}.tr .dt{padding:0}}
"""

def build_glossary():
    """용어집 페이지: 모든 브리핑 meta.terms 를 모아 날짜순(최신 먼저). 검색/월 필터는 브라우저에서(정적)."""
    rows = []
    for m in all_meta():
        if m.get('kind') != 'daily':
            continue
        for t in m.get('terms', []):
            rows.append({'date': m['date'], 'id': m['id'], 'term': t.get('term', ''), 'en': t.get('en', ''),
                         'def': t.get('def', ''), 'note': t.get('note', ''), 'ex': t.get('ex', '')})
    months = sorted({r['date'][:7] for r in rows}, reverse=True)
    data = json.dumps(rows, ensure_ascii=False).replace('</', '<\\/')
    opts = ''.join(f'<option>{m}</option>' for m in months)
    js = """
const R=%s;const q=document.getElementById('q'),mo=document.getElementById('mo'),box=document.getElementById('list'),cnt=document.getElementById('cnt');
const esc=s=>String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function render(){const k=q.value.trim().toLowerCase(),m=mo.value;const rows=R.filter(r=>(!m||r.date.startsWith(m))&&(!k||[r.term,r.en,r.def,r.note,r.ex].join(' ').toLowerCase().includes(k)));
cnt.textContent=rows.length+'개 (누적 '+R.length+'개)';
box.innerHTML=rows.length?rows.map(r=>'<div class="tr"><div class="dt">'+esc(r.date)+'</div><div><div class="k">'+esc(r.term)+(r.en?'<span>'+esc(r.en)+'</span>':'')+'<a class="go" href="b/'+encodeURIComponent(r.id)+'/">브리핑</a></div>'+(r.def?'<div class="df">'+esc(r.def)+'</div>':'')+(r.note?'<div class="nt">당사 관점: '+esc(r.note)+'</div>':'')+(r.ex?'<div class="ex">예: '+esc(r.ex)+'</div>':'')+'</div></div>').join(''):'<div class="tr"><div></div><div style="color:#9ca3af">조건에 맞는 용어가 없습니다.</div></div>'}
q.addEventListener('input',render);mo.addEventListener('change',render);render();
""" % data
    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{NOINDEX}
<title>Briefing Glossary</title><style>{GLOSS_CSS}</style></head><body>{nav_html('./', 'glossary')}<div class="wrap">
<h1>용어집</h1><div class="sub">브리핑 [오늘의 용어] 누적 {len(rows)}개. 매일 자동 갱신. &nbsp;<a href="./" style="color:#0f3b66;font-weight:700">브리핑 목록</a></div>
<div class="tools"><input id="q" placeholder="용어, 영문, 풀이 검색"><select id="mo"><option value="">전체 기간</option>{opts}</select><span class="n" id="cnt"></span></div>
<div class="card" id="list"></div>
<div class="ft">풀이는 비전공자 기준, 당사 관점은 작성 시점 의견. 정식 정의는 각 브리핑 원문과 프로젝트 glossary.md 기준.</div></div>
<script>{js}</script></body></html>"""
    open(os.path.join(ROOT, 'glossary.html'), 'w', encoding='utf-8').write(doc)

# ---------------------------------------------------------------- search (아카이브 내부 전문 검색, 정적)
def _plain(h):
    h = re.sub(r'<!--bnav-->.*?<!--/bnav-->', ' ', h, flags=re.S)
    h = re.sub(r'<(script|style|head)\b.*?</\1>', ' ', h, flags=re.S | re.I)
    h = re.sub(r'<br\s*/?>|</(p|div|li|tr|h\d|section)>', ' \n ', h, flags=re.I)
    h = H.unescape(re.sub(r'<[^>]+>', ' ', h))
    return re.sub(r'[ \t\u00a0]+', ' ', re.sub(r'\s*\n\s*', '\n', h)).strip()

SEARCH_CSS = INDEX_CSS + """
.tools{display:flex;gap:8px;align-items:center;margin:0 0 12px;flex-wrap:wrap}
.tools input{font:inherit;font-size:14px;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;min-width:280px;flex:1;max-width:460px}
.tools select{font:inherit;font-size:13px;padding:7px 10px;border:1px solid #d1d5db;border-radius:8px;background:#fff}
.hit{display:block;padding:12px 14px;border-top:1px solid #e5e7eb;text-decoration:none;color:inherit}.hit:first-child{border-top:none}.hit:hover{background:#f9fafb}
.hit .h{display:flex;gap:8px;align-items:center;margin-bottom:4px}.hit .c{color:#9ca3af;font-size:12px;margin-left:auto}
.hit .sn{color:#374151;font-size:13px;margin-top:2px}.hit mark{background:#fef08a;color:inherit;padding:0 1px;border-radius:2px}
"""

def build_search():
    docs = []
    for m in all_meta():
        f = os.path.join(ROOT, 'b', m['id'], 'index.html')
        if not os.path.exists(f):
            continue
        docs.append({'id': m['id'], 'date': m['date'], 'kind': m['kind'],
                     'title': m['title'].replace('전략기획팀 ', '').split(' | ')[0],
                     'text': _plain(open(f, encoding='utf-8').read())})
    json.dump(docs, open(os.path.join(ROOT, 'search.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    kinds = ''.join(f'<option value="{k}">{v}</option>' for k, v in KIND.items())
    js = """
var K=%s,D=[],q=document.getElementById('q'),kd=document.getElementById('kd'),box=document.getElementById('list'),cnt=document.getElementById('cnt');
var esc=function(s){return String(s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
var rx=function(s){return s.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&');};
function snip(t,ws){var lines=t.split('\\n'),out=[];for(var i=0;i<lines.length&&out.length<3;i++){var l=lines[i],lo=l.toLowerCase();if(ws.some(function(w){return lo.indexOf(w)>=0;})){
var p=lo.indexOf(ws.find(function(w){return lo.indexOf(w)>=0;}));var a=Math.max(0,p-50);out.push((a>0?'… ':'')+l.slice(a,a+160)+(l.length>a+160?' …':''));}}
var h=esc(out.join('\\n')).replace(/\\n/g,'<br>');ws.forEach(function(w){h=h.replace(new RegExp('('+rx(esc(w))+')','gi'),'<mark>$1</mark>');});return h;}
function run(){var v=q.value.trim(),k=kd.value;var ws=v.toLowerCase().split(/\\s+/).filter(Boolean);
try{history.replaceState(null,'','?q='+encodeURIComponent(v));}catch(e){}
if(!ws.length){cnt.textContent='';box.innerHTML='<div class="hit" style="color:#9ca3af">검색어를 입력하세요. 회사명, 용어, 딜 이름 등 브리핑 본문 전체에서 찾습니다.</div>';return;}
var r=D.filter(function(d){if(k&&d.kind!==k)return false;var t=(d.title+' '+d.text).toLowerCase();return ws.every(function(w){return t.indexOf(w)>=0;});})
.map(function(d){var t=d.text.toLowerCase(),n=0;ws.forEach(function(w){var i=-1;while((i=t.indexOf(w,i+1))>=0)n++;});return [d,n];});
cnt.textContent=r.length+'건';
box.innerHTML=r.length?r.map(function(x){var d=x[0];return '<a class="hit" href="b/'+encodeURIComponent(d.id)+'/#:~:text='+encodeURIComponent(ws[0])+'"><div class="h"><span class="d">'+esc(d.date)+'</span><span class="k '+esc(d.kind)+'">'+(K[d.kind]||d.kind)+'</span><span class="t">'+esc(d.title)+'</span><span class="c">'+x[1]+'회 언급</span></div><div class="sn">'+snip(d.text,ws)+'</div></a>';}).join(''):'<div class="hit" style="color:#9ca3af">결과가 없습니다.</div>';}
var u=new URLSearchParams(location.search);q.value=u.get('q')||'';
fetch('search.json').then(function(r){return r.json();}).then(function(j){D=j;run();});
q.addEventListener('input',run);kd.addEventListener('change',run);q.focus();
""" % json.dumps(KIND, ensure_ascii=False)
    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{NOINDEX}
<title>Briefing Search</title><style>{SEARCH_CSS}</style></head><body>{nav_html('./', '')}<div class="wrap">
<h1>검색</h1><div class="sub">아카이브에 게시된 브리핑과 리포트 본문 전체에서 찾습니다. 여러 단어는 모두 포함된 문서만 표시.</div>
<div class="tools"><input id="q" type="search" placeholder="예: 셀트리온, 블록딜, 동결건조"><select id="kd"><option value="">전체 종류</option>{kinds}</select><span class="n" id="cnt"></span></div>
<div class="card" id="list"></div></div>
<script>{js}</script></body></html>"""
    open(os.path.join(ROOT, 'search.html'), 'w', encoding='utf-8').write(doc)

# ---------------------------------------------------------------- mail body
F = "font-family:'Malgun Gothic','Apple SD Gothic Neo',Arial,sans-serif;"

def _meta(bid):
    return json.load(open(os.path.join(ROOT, 'b', bid, 'meta.json'), encoding='utf-8'))

def _img(url, file, alt):
    return (f'<div style="margin:0 0 10px"><a href="{url}" style="text-decoration:none"><img src="{url}{file}" width="680" alt="{H.escape(alt)}" '
            f'style="display:block;width:680px;max-width:100%;height:auto;border:1px solid #e5e7eb;border-radius:6px"></a></div>')

def mail_body(bid, full=False, extras=(), note_html=''):
    """이미지 본문.
    bid: 메인 문서(가장 최근 일간). full=False 면 참조 페이지(8 언급 기업, 9 기본 개념)는 빼고 링크로 안내.
    extras: 같은 메일에 묶는 다른 문서 id들(지난 비영업일 생성분, 주간/월간 리포트). 각각 1쪽(헤더 + 한눈에) 이미지 + 원문 링크.
    note_html: 맨 위 안내 띠 아래 한 줄(예: '오늘분 생성 실패, 미발송분 발송')."""
    m = _meta(bid)
    url = f'{BASE_URL}b/{bid}/'
    pages = m['pages'] if full else [p for p in m['pages'] if not p['ref']]
    imgs = ''.join(_img(url, p['file'], f"{m['title']} {i}/{len(pages)}쪽") for i, p in enumerate(pages, 1))
    skipped = len(m['pages']) - len(pages)
    top = (f'<div style="{F}font-size:13px;color:#374151;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px;margin:0 0 12px">'
           f'<b>{H.escape(m["date"])} {KIND.get(m["kind"], m["kind"])}</b> {"전체판" if full else "요약판"} &nbsp;|&nbsp; '
           f'<a href="{url}" style="color:#0f3b66;font-weight:700">원문 열기(인쇄)</a> &nbsp;|&nbsp; <a href="{BASE_URL}" style="color:#0f3b66">지난 브리핑</a> &nbsp;|&nbsp; <a href="{BASE_URL}glossary.html" style="color:#0f3b66">용어집</a>'
           f'<div style="font-size:12px;color:#6b7280;margin-top:2px">이미지가 안 보이면 메일 상단의 "이미지 표시"를 누르거나 원문 열기를 눌러주세요. 첨부 zip은 원문 html(오프라인용).</div>'
           + (f'<div style="font-size:12px;color:#b45309;margin-top:2px">{note_html}</div>' if note_html else '') + '</div>')
    ex = ''
    for eid in extras:
        try:
            em = _meta(eid)
        except Exception:
            continue
        eu = f'{BASE_URL}b/{eid}/'
        ex += (f'<div style="{F}font-size:14px;font-weight:800;margin:22px 0 6px;padding-top:12px;border-top:2px solid #111827">'
               f'{H.escape(em["date"])} {KIND.get(em["kind"], em["kind"])} <span style="font-size:12px;color:#6b7280;font-weight:500">'
               f'{"리포트" if em["kind"] != "daily" else "비영업일 생성분"} 요약, 전체는 <a href="{eu}" style="color:#0f3b66">원문</a></span></div>'
               + _img(eu, em['pages'][0]['file'], f"{em['title']} 1쪽"))
    note = (f'8 언급 기업, 9 기본 개념(용어 정리, 계산 예시, 문제) {skipped}쪽은 원문에서: ' if skipped else '인쇄용 원문: ')
    foot = (f'<div style="{F}font-size:12px;color:#6b7280;margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb">{note}'
            f'<a href="{url}" style="color:#0f3b66">{H.escape(url)}</a><br>요약과 링크만 제공, 기사 원문 복사 없음. 계산치=작성자 산출, 추정/보도 기준=미확인.</div>')
    return f'<div style="max-width:680px;margin:0 auto">{top}{imgs}{ex}{foot}</div>'

def make_zip(bid, out_path):
    """원문 html을 zip으로 (메일 첨부용, 약 20~25KB). 반환: zip 경로."""
    import zipfile
    src = os.path.join(ROOT, 'b', bid, 'index.html')
    h = open(src, encoding='utf-8').read()
    h = re.sub(r'<!--bnav-->.*?<!--/bnav-->', '', h, flags=re.S)
    h = re.sub(r'\n\s+', '\n', h); h = re.sub(r'/\*.*?\*/', '', h, flags=re.S); h = re.sub(r'\n+', '\n', h)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(f'briefing_{bid}.html', h)
    return out_path

# ---------------------------------------------------------------- git
def git_push(msg):
    env = dict(os.environ, GIT_AUTHOR_NAME='Genie', GIT_AUTHOR_EMAIL='yhjin1993@gmail.com',
               GIT_COMMITTER_NAME='Genie', GIT_COMMITTER_EMAIL='yhjin1993@gmail.com')
    subprocess.run(['git', 'add', '-A'], cwd=ROOT, check=True)
    r = subprocess.run(['git', 'commit', '-q', '-m', msg + '\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'], cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0 and 'nothing to commit' in (r.stdout + r.stderr):
        print('nothing to commit'); return
    subprocess.run(['git', 'fetch', '-q', 'origin', 'main'], cwd=ROOT, check=True)
    subprocess.run(['git', 'rebase', '-q', 'origin/main'], cwd=ROOT, check=True)
    subprocess.run(['git', 'push', '-q', 'origin', 'HEAD:main'], cwd=ROOT, check=True)
    print('pushed')

if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] not in ('add', 'mail', 'index'):
        print(__doc__); sys.exit(1)
    if a[0] == 'add':
        ij = a[a.index('--index') + 1] if '--index' in a else None
        add(a[1], push='--push' in a, index_json=ij)
    elif a[0] == 'index':
        nav_all(); build_index(); build_glossary(); build_search()
        if '--push' in a: git_push('목록/용어집 재생성')
    elif a[0] == 'mail':
        # publish.py mail <id> [extra_id ...] [--full] [--note "문구"] [--out mail.html] [--zip out.zip]
        ids = [x for x in a[1:] if not x.startswith('--') and x not in (a[a.index('--out') + 1] if '--out' in a else '', a[a.index('--note') + 1] if '--note' in a else '', a[a.index('--zip') + 1] if '--zip' in a else '')]
        bid, extras = ids[0], ids[1:]
        out = a[a.index('--out') + 1] if '--out' in a else 'mail.html'
        note = a[a.index('--note') + 1] if '--note' in a else ''
        open(out, 'w', encoding='utf-8').write(mail_body(bid, full='--full' in a, extras=extras, note_html=note))
        print('mail', out)
        if '--zip' in a:
            zp = make_zip(bid, a[a.index('--zip') + 1]); print('zip', zp, os.path.getsize(zp), 'bytes')
