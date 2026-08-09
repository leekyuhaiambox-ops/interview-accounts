# Interview Copilot

**Free English-interview practice for non-native speakers — answers built from *your* resume, with pronunciation written in your own script.**

🔗 **Try it (free during beta):** https://geoinfomatic.pythonanywhere.com/interview/en.html
🇰🇷 한국어: https://geoinfomatic.pythonanywhere.com/interview/ · 🇯🇵 [日本語](https://geoinfomatic.pythonanywhere.com/interview/ja.html) · 🇨🇳 [中文](https://geoinfomatic.pythonanywhere.com/interview/zh.html) · 🇪🇸 [Español](https://geoinfomatic.pythonanywhere.com/interview/es.html)

---

## What it does

Non-native speakers rarely fail English interviews on grammar. They fail on the **3-second freeze** — the answer exists in your head, but retrieval fails under pressure.

Interview Copilot is built around that freeze:

- 🎙 An **AI interviewer asks questions by voice** (TTS), with an answer timer and a 10-second countdown — real-interview pressure included.
- 📄 Answers are generated **from your resume**, not from templates. Your projects, your numbers.
- 🈶 Every English sentence gets **pronunciation written underneath in your native script** — hangul (애즈 어 솔로 빌더), katakana (アズ・ア・ソロ・ビルダー), hanzi, phonetic Spanish. When you blank, you read.
- 💬 **Coach feedback in your native language** after each answer.
- 🔁 Expected-question lists + AI follow-ups, live mic Q&A, and a beta Meet/Zoom tab-share assist.

## Privacy by architecture (why this repo exists)

The tool is **BYO-API-key**: the browser calls Claude/OpenAI **directly** with your own key.

- Your resume and every conversation **never touch our server**.
- The key lives in your browser's localStorage only.
- This repo is the entire backend — a tiny Flask API that handles **optional** Google login and subscription state. Nothing else. Read it yourself; that's the point of it being public.

## Stack

Vanilla JS + Web Speech API front end on static hosting · Flask + SQLAlchemy accounts API (this repo) on a free tier · Postgres · Ko-fi webhook for Pro grants (expiring, since Ko-fi has no cancel webhook).

## Guides

- [English interview questions for non-native speakers — 20 questions + frameworks](https://geoinfomatic.pythonanywhere.com/interview/guide-esl-questions.html)
- [한국어 가이드: 1분 자기소개 · 예상질문 30 · 화상면접](https://geoinfomatic.pythonanywhere.com/interview/guide.html)

## Honest limitations

You need a free Anthropic/OpenAI account and an API key (~5 minutes). Free-tier hosting is slow on cold start. The live-interview assist is beta, and whether you use it in a real interview is your call under the company's rules.

---

Contact: geostaticss@gmail.com
