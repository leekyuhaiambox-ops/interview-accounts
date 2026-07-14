# 계정 백엔드 배포 (Render) — 런북

이 폴더(`accounts/`)는 **구글 로그인 + 이메일 인증번호 가입 + 회원 DB + 회원관리**
API입니다. Render(무료)에 올리고, 프런트(현재 PythonAnywhere의 정적 사이트)가 이 API를
크로스오리진으로 호출합니다.

> 제가 못 하는 건 **계정 생성/로그인**(Render·GitHub·Google·Gmail)뿐입니다. 그 토큰·키만
> 주시면 프런트 연결(구글 버튼·로그인 UI)은 제가 마저 합니다.

---

## 0. 준비물 (당신이 만드는 것)
- **GitHub 계정** (코드 올릴 곳) · **Render 계정** (서버) · **Google Cloud 계정** (구글 로그인용 Client ID)
- (선택, 이메일 인증번호 쓰려면) **Gmail 앱 비밀번호**

---

## 1. 구글 로그인용 Client ID 발급 (5분)
1. https://console.cloud.google.com → 프로젝트 생성
2. **APIs & Services → OAuth consent screen** → External → 앱 이름만 채우고 저장
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Web application**
   - **Authorized JavaScript origins** 에 추가:
     `https://geoinfomatic.pythonanywhere.com`
   - 만들면 **Client ID**(`xxxx.apps.googleusercontent.com`) 복사 → 나중에 Render env에 넣음

## 2. (선택) 이메일 인증번호용 Gmail 앱 비밀번호
1. Gmail → 계정 → **2단계 인증 켜기**
2. **앱 비밀번호** 생성 → 16자리 복사 (SMTP_PASS로 씀)
   - 안 할 거면 이 단계 건너뛰세요. **구글 로그인만으로도 회원 인증·관리는 됩니다**
     (구글이 이메일을 이미 검증하니까요). 이메일 인증번호 가입은 메일 발송이 필요해서만 씁니다.

## 3. GitHub에 이 폴더 올리기
- 새 repo 생성 → 이 `accounts/` 폴더의 파일(`app.py`, `requirements.txt`, `render.yaml`,
  `.gitignore`)을 그 repo **루트**에 넣고 push.
  (이 폴더만 따로 올리세요. 다른 파일은 필요 없습니다.)

## 4. Render 배포
1. https://render.com 가입 → **New → Blueprint**
2. 위 GitHub repo 선택 → Render가 `render.yaml`을 읽어 **웹서비스 + 무료 Postgres**를 자동 생성
3. 배포 후 **Environment** 에서 `sync:false` 값들 입력:
   - `GOOGLE_CLIENT_ID` = 1번에서 복사한 값 (필수)
   - (이메일 쓸 때만) `SMTP_HOST`=`smtp.gmail.com`, `SMTP_USER`=내 Gmail,
     `SMTP_PASS`=앱 비밀번호, `SMTP_FROM`=내 Gmail
   - `JWT_SECRET`·`ADMIN_KEY`·`DATABASE_URL`은 Render가 자동 생성/연결
4. 재배포 → **서비스 URL** 확인 (예: `https://interview-accounts.onrender.com`)
5. `https://interview-accounts.onrender.com/healthz` 열어 `{"ok":true,...}` 뜨면 성공

> ⚠️ Render 무료는 15분 미사용 시 잠들어, 첫 접속이 30초쯤 느릴 수 있습니다(콜드스타트). 정상입니다.

## 5. 나한테 주세요 → 프런트 연결은 내가
- **Render URL** (`https://....onrender.com`)
- **Google Client ID**
→ 그러면 제가 사이트에 **구글 로그인 버튼 + 로그인 상태 표시 + (원하면)이메일 인증번호 모달**을
붙이고, `app.js`가 이 API를 호출하도록 배포합니다.

---

## Ko-fi 결제 → Pro 자동 반영 (웹훅)
1. https://ko-fi.com/manage/webhooks 접속
2. **Webhook URL**: `https://interview-accounts.onrender.com/api/webhook/kofi`
3. 같은 페이지의 **Verification Token** 복사
4. Render → interview-accounts → Environment → `KOFI_VERIFICATION_TOKEN` = 복사한 값 → 저장(재배포)
- 멤버십 결제가 오면 그 이메일 회원이 자동 **Pro(35일)**, 매달 갱신 때마다 연장. Ko-fi엔 해지 웹훅이 없어 **만료형** — 해지하면 35일 뒤 자동 Free.
- admin.html 플랜 토글(`/api/admin/set-plan`)의 수동 변경은 **영구**(만료 없음).
- Ko-fi "Send Test" 버튼은 Donation 형식이라 `ignored` 응답이 정상(실결제/테스트 티어로 검증).
- ⚠️ 구매자가 **로그인과 같은 이메일**로 결제해야 자동 매칭(요금제 페이지에 안내됨). 불일치 시 admin에 `kofi` 경로 stub가 보임 → 수동 조정.

## 회원 관리 (당신용)
- **관리자 페이지(권장)**: https://geoinfomatic.pythonanywhere.com/interview/admin.html — 관리자 구글 로그인만으로 가입자 표 + 플랜 Free↔Pro 토글.
- API 직접: `GET /api/admin/members`, `POST /api/admin/set-plan {email, plan}` — 헤더 `X-Admin-Key` 또는 관리자 세션 Bearer.

## API 요약
| 엔드포인트 | 용도 |
|---|---|
| `POST /api/auth/google` `{id_token}` | 구글 로그인 → 우리 JWT 발급 |
| `POST /api/auth/email/start` `{email}` | 인증번호 메일 발송 |
| `POST /api/auth/email/verify` `{email,code,name?}` | 인증번호 확인 → 가입/로그인 |
| `GET /api/me` (Bearer 토큰) | 내 회원정보 |
| `GET /api/admin/members` (X-Admin-Key) | 가입자 목록 |

토큰은 응답의 `token`(JWT). 프런트는 localStorage에 저장하고 `Authorization: Bearer <token>`로 호출.
