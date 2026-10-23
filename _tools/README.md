# _tools - 예약 작업이 쓰는 게시/메일 도구

저장소 루트 = GitHub Pages 루트 (https://yhjin1993-art.github.io/Briefing-Archive/)

## 06:30 생성 작업 (DB 저장 후)
1. add_repo(owner yhjin1993-art, repo Briefing-Archive, access push) → git clone --depth 1 (경로 /home/claude/briefing-archive)
2. DB에 저장한 것과 같은 내용으로 로컬 json 2개 준비: briefings/<id>.json ({id, html, mailHtml, date, kind}) 와 index/<id>.json
3. python3 _tools/publish.py add <briefings json> --index <index json> --push
   → b/<id>/index.html, p1..pN.png, meta.json 생성, index.html / glossary.html 재생성, commit, push
4. 실패하면(권한, 네트워크) 브리핑 요약에 "Pages 게시 실패"라고 적고 종료. 발송 작업이 알아서 인라인 HTML로 대체 발송한다.

## 07:50 발송 작업
1. add_repo(access read면 충분) → git clone --depth 1
2. python3 _tools/publish.py mail <main_id> [extra_id ...] [--full] [--note 문구] --out mail.html --zip briefing_<id>.zip
   main_id = 가장 최근 일간, extra = 미발송 비영업일분 + 오늘 생성된 리포트. --full 은 전체판(8, 9 포함).
3. Gmail send_message: htmlBody = mail.html 내용, attachments = [{filename, mimeType application/zip, content = zip base64}]
4. b/<main_id>/meta.json 이 없으면(게시 실패) 기존 방식: briefings.mailHtml 인라인 발송.

## 폰트
캡처용 Nanum Gothic은 처음 실행 때 npm pack @fontsource/nanum-gothic 으로 _tools/fnt/ 에 받음(저장소에 안 올림, .gitignore).
