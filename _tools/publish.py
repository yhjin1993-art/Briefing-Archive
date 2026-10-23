# -*- coding: utf-8 -*-
"""publish.py - 브리핑을 GitHub Pages 저장소에 게시하고 메일 본문(이미지)을 만든다.

구조 (저장소 루트 = Pages 루트)
  index.html            목록 페이지 (자동 재생성, noindex)
  b/<id>/index.html     브리핑 원문 (DB briefings.html 그대로 + noindex 메타)
  b/<id>/p1.jpg ...     페이지별 캡처 (메일 본문용, 폭 720px, 1.5배)
  b/<id>/meta.json      {id, date, kind, title, pages:[{file, w, h, ref}]}
  _tools/               이 스크립트와 폰트 캐시

사용 (저장소 루트에서)
  python3 publish.py add  <briefing.json>  [--push]   # ArtifactData out_dir로 받은 briefings/<id>.json
  python3 publish.py mail <id> --out mail.html [--full] [--extra extra.html]   # 메일 본문 HTML 생성
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
def add(json_path, push=False):
    d = json.load(open(json_path, encoding='utf-8'))
    bid = d.get('id') or os.path.splitext(os.path.basename(json_path))[0]
    html = d['html']
    date, kind = d.get('date', bid[-10:]), d.get('kind', 'daily')
    m = re.search(r'<title>(.*?)</title>', html, re.S)
    title = H.unescape(m.group(1)).strip() if m else bid
    out = os.path.join(ROOT, 'b', bid)
    os.makedirs(out, exist_ok=True)
    pub = html.replace('<head>', '<head>' + NOINDEX, 1)
    open(os.path.join(out, 'index.html'), 'w', encoding='utf-8').write(pub)
    rendered = render_html(html)  # ensure_fonts()가 FONT_DIR을 만든다
    rp = os.path.join(FONT_DIR, f'_render_{bid}.html')
    open(rp, 'w', encoding='utf-8').write(rendered)
    pages = asyncio.run(_shoot(rp, out))
    os.remove(rp)
    meta = {'id': bid, 'date': date, 'kind': kind, 'title': title, 'pages': pages,
            'publishedAt': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='minutes')}
    json.dump(meta, open(os.path.join(out, 'meta.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    build_index()
    if push:
        git_push(f'{bid} 게시')
    print('published', bid, len(pages), 'pages')
    return meta

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
.d{font-weight:700;white-space:nowrap}.k{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:#e8f0f8;color:#0f3b66;text-align:center}.k.weekly{background:#e3f3f1;color:#0f766e}.k.monthly,.k.quarterly,.k.annual{background:#fdf1e0;color:#b45309}
.t{color:#374151;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.n{color:#9ca3af;font-size:12px;white-space:nowrap}
@media(max-width:600px){.row{grid-template-columns:84px 48px 1fr}.n{display:none}}
.ft{margin-top:20px;color:#9ca3af;font-size:12px}"""

def build_index():
    ms = all_meta()
    by = {}
    for m in ms:
        by.setdefault(m['date'][:7], []).append(m)
    body = ''
    for mon in sorted(by, reverse=True):
        rows = ''.join(
            f'<a class="row" href="b/{H.escape(m["id"])}/"><span class="d">{H.escape(m["date"])}</span>'
            f'<span class="k {H.escape(m["kind"])}">{KIND.get(m["kind"], m["kind"])}</span>'
            f'<span class="t">{H.escape(m["title"].replace("전략기획팀 ", "").split(" | ")[0])}</span>'
            f'<span class="n">{len(m["pages"])}쪽</span></a>' for m in by[mon])
        body += f'<div class="m">{mon}</div><div class="card">{rows}</div>'
    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{NOINDEX}
<title>Strategy Daily Briefing Archive</title><style>{INDEX_CSS}</style></head><body><div class="wrap">
<h1>Daily Briefing Archive</h1><div class="sub">전략기획팀 딜 / PE / 제약 CDMO 브리핑 {len(ms)}건. 날짜를 누르면 원문(인쇄 가능)이 열립니다.</div>
{body}<div class="ft">요약과 링크만 제공, 기사 원문 복사 없음. 계산치는 작성자 산출, 추정 및 보도 기준 수치는 미확인.</div></div></body></html>"""
    open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8').write(doc)

# ---------------------------------------------------------------- mail body
F = "font-family:'Malgun Gothic','Apple SD Gothic Neo',Arial,sans-serif;"

def mail_body(bid, full=False, extra_html=''):
    """이미지 본문. full=False 면 참조 페이지(8 언급 기업, 9 기본 개념)는 빼고 링크로 안내.
    extra_html: 과거 미발송분 요약, 리포트 요약 등 덧붙일 HTML (발송 작업이 만들어 넘김)."""
    m = json.load(open(os.path.join(ROOT, 'b', bid, 'meta.json'), encoding='utf-8'))
    url = f'{BASE_URL}b/{bid}/'
    pages = m['pages'] if full else [p for p in m['pages'] if not p['ref']]
    imgs = ''.join(
        f'<div style="margin:0 0 10px"><a href="{url}" style="text-decoration:none"><img src="{url}{p["file"]}" width="680" alt="{H.escape(m["title"])} {i}/{len(pages)}쪽" '
        f'style="display:block;width:680px;max-width:100%;height:auto;border:1px solid #e5e7eb;border-radius:6px"></a></div>'
        for i, p in enumerate(pages, 1))
    skipped = len(m['pages']) - len(pages)
    note = (f'8 언급 기업, 9 기본 개념(용어 정리, 계산 예시, 문제) {skipped}쪽은 원문에서: ' if skipped else '인쇄용 원문: ')
    top = (f'<div style="{F}font-size:13px;color:#374151;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px;margin:0 0 12px">'
           f'<b>{H.escape(m["date"])} {KIND.get(m["kind"], m["kind"])}</b> {"전체판" if full else "요약판"} &nbsp;|&nbsp; '
           f'<a href="{url}" style="color:#0f3b66;font-weight:700">원문 열기(인쇄)</a> &nbsp;|&nbsp; <a href="{BASE_URL}" style="color:#0f3b66">지난 브리핑</a>'
           f'<div style="font-size:12px;color:#6b7280;margin-top:2px">이미지가 안 보이면 메일 상단의 "이미지 표시"를 누르거나 원문 열기를 눌러주세요. 첨부 zip은 원문 html(오프라인용).</div></div>')
    foot = (f'<div style="{F}font-size:12px;color:#6b7280;margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb">{note}'
            f'<a href="{url}" style="color:#0f3b66">{H.escape(url)}</a><br>요약과 링크만 제공, 기사 원문 복사 없음. 계산치=작성자 산출, 추정/보도 기준=미확인.</div>')
    return f'<div style="max-width:680px;margin:0 auto">{top}{imgs}{extra_html}{foot}</div>'

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
        add(a[1], push='--push' in a)
    elif a[0] == 'index':
        build_index()
        if '--push' in a: git_push('목록 재생성')
    elif a[0] == 'mail':
        bid = a[1]; out = a[a.index('--out') + 1] if '--out' in a else 'mail.html'
        extra = open(a[a.index('--extra') + 1], encoding='utf-8').read() if '--extra' in a else ''
        open(out, 'w', encoding='utf-8').write(mail_body(bid, full='--full' in a, extra_html=extra))
        print('mail', out)
