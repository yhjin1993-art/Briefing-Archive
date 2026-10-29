# coaching (Coaching 앱 문제 저장소)

Cloudflare Worker 앱(Coaching)이 이 폴더를 읽는다. 브리핑 사이트와 무관하며 브리핑 스크립트는 이 폴더를 건드리지 않는다.

| 경로 | 내용 |
|---|---|
| index.json | 주간 시간표(weeks)와 카드 목록(cards: date, title, kind, tracks) |
| cards/YYYY-MM-DD.json | 그날 카드 전체 (문제, 데이터, 채점 기준, 모범답안). 두 버전이면 tracks 배열 |
| assets/YYYY-MM-DD/ | 템플릿 이미지(tpl-*.png), 템플릿 PPT, 모범 이미지/PPT(파일명에 model 포함 - 앱이 채점 전에는 숨김) |

쓰는 쪽: 클로드 예약 작업 (프로젝트 문서 claude/session-procedure.md). push 전에 반드시 git fetch + rebase.
