<div align="center">

<img src="frontend/public/icon-192.png" alt="ViralMint" width="96" height="96" />

# ViralMint

### クリエイターのためのオープンソース・ローカルファーストな動画パイプライン

**トレンド発掘 → 長尺動画をクリップ → AI ショート生成 → YouTube と TikTok へ自動投稿。**
すべて自分のマシン上で。自分の API キーを使用。間に SaaS なし。テレメトリなし。

<!-- Activity badges (top row) — auto-update from GitHub, so they reflect
     real maintenance signal at a glance for awesome-list reviewers and new
     visitors. -->
[![Stars](https://img.shields.io/github/stars/openclaw-easy/ViralMint?style=for-the-badge&logo=github&color=yellow)](https://github.com/openclaw-easy/ViralMint/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/openclaw-easy/ViralMint?style=for-the-badge&color=brightgreen)](https://github.com/openclaw-easy/ViralMint/commits/main)
[![Release](https://img.shields.io/github/v/release/openclaw-easy/ViralMint?style=for-the-badge&color=blue&label=latest)](https://github.com/openclaw-easy/ViralMint/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/openclaw-easy/ViralMint/ci.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI)](https://github.com/openclaw-easy/ViralMint/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue?style=for-the-badge)](LICENSE)

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Platform](https://img.shields.io/badge/macOS%20%7C%20Windows%20%7C%20Linux-lightgrey?style=for-the-badge)](#-クイックスタート)

[クイックスタート](#-クイックスタート) • [機能](#-機能) • [無料で使えるもの](#-api-キーなしで動くもの) • [BYOK](#-自分のキーを使う-byok) • [コントリビュート](CONTRIBUTING.md)

[English](README.md) · [简体中文](README.zh-CN.md) · **日本語**

<br/>

<img src="docs/screenshots/chat.webp" alt="ViralMint Chat — streaming AI agent that scouts trending videos, analyzes channels, and orchestrates the full pipeline" width="900" />

<sub><i>AI エージェントとチャット — URL を貼る、ニッチの発掘を頼む、ワークフローを起動する。適切なパイプラインをバックグラウンドで実行します。</i></sub>

</div>

---

> **手動のクリエイターが十数個のタブとアプリを横断してやっていることを、ViralMint は1つのローカルワークフローとして実行します。**
> YouTube・TikTok・Douyin を横断してトレンド動画を見つけ、ローカルの Whisper で文字起こし・分析し、長尺動画を公開できるショートに切り分け、好みの AI でオリジナル台本を書き、キャプション付きストック映像動画をレンダリングし、YouTube と TikTok へ直接投稿する。ブラウザから操作するのも、Telegram・WhatsApp・Discord・Slack でチャットするのも自由自在です。

## ✨ ViralMint を選ぶ理由

|   |   |
|---|---|
| 🔒 **100% ローカル** | SQLite、ローカル Whisper、ローカル FFmpeg。台本・文字起こし・ダウンロード・生成動画はマシンの外に出ません。 |
| 🔑 **BYOK、仲介者なし** | 自分の Anthropic / OpenAI / OpenRouter / YouTube / Pexels キーを使用。AES-256 で暗号化して保存し、プロバイダーへ直接送信 — 間に ViralMint のサーバーはありません。 |
| 🤖 **チャットのラッパーではなくエージェント** | 目的特化型の6つのエージェント — Planner、Scout、Download、Analyzer、Generator、そして **Uploader** — を、実際に処理を実行するストリーミング AI チャットがオーケストレーションします。 |
| 📤 **投稿まで代行** | AI が下書きしたタイトル・説明文・タグ・サムネイル付きで、YouTube と TikTok へ直接アップロード。生成だけでなくループ全体をカバーします。 |
| 📱 **スマホから操作** | Telegram・WhatsApp・Discord・Slack でプランナーと双方向チャット — 同じスレッドでジョブ通知も届きます。 |
| 🆓 **すぐに無料で使える** | ローカル Whisper、Edge TTS（400以上の音声）、ロイヤリティフリー音楽、Pexels ストック、18の FFmpeg ツール — 重い処理はすべて $0。プラグインする AI のぶんだけ課金されます。 |

<sub>実戦仕込み: 毎コミットで **約1,000テストの pytest スイート**（999個、まだ増加中）が走ります。AGPL-3.0 — フォークして、改変して、その上でビジネスを構築できます。</sub>

---

## 🎯 機能

<table>
<tr>
<td width="50%" valign="top">

### 🔍 Scout
**YouTube、TikTok、Douyin** を横断するマルチプラットフォームのトレンド発掘（さらに動的検索で yt-dlp 対応の任意のサイトにも対応）。AI によるバイラリティスコアリング、Google トレンドの需要シグナル、再生速度分析、外れ値検出（チャンネル基準の3×〜20×）を備えます。

</td>
<td width="50%" valign="top">

### 🧠 Analyze
ローカル Whisper による文字起こし（長尺もクリーンに処理）に加え、AI によるインサイト抽出 — フック、構成、トーン、離脱リスク、推奨タイトル、そのまま実行できる再現プロンプト — をセグメント単位で採点し、具体的な改善提案を添えます。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🎬 Generate
フルパイプライン: AI 台本 → TTS 音声 → キーワードに合わせた Pexels ストック映像 → フレーズ単位のアニメーションキャプション（CJK / アラビア語 / タイ語対応）→ バランス調整された BGM → AI サムネイル → 完成した MP4。

</td>
<td width="50%" valign="top">

### ✂️ Clip Studio
1本の長尺動画 → 公開できる多数のショート。AI がベストな瞬間を見つけ、**フック・流れ・価値・トレンド適合・シェアされやすさ** でそれぞれを採点し、カットを文の切れ目にスナップし、繰り返された話は除外します。**プラットフォームやジャンル** でピックを偏らせたり、欲しいものを記述したり（*「ウケたジョークすべて」*）、**手動で時間範囲** を選んだりできます — オプションで無音トリミング、絵文字キャプション、焼き込みのフックオーバーレイも。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 📤 Publish
**YouTube**（OAuth）と **TikTok**（OAuth またはセッションクッキー）へ、プラットフォーム最適化されたタイトル・説明文・タグ・サムネイル付きで直接アップロード — 完成した動画が実際に投稿されます。

</td>
<td width="50%" valign="top">

### 💬 Chat
すべてのエージェントをオーケストレーションするストリーミング WebSocket チャット。*「料理動画を発掘して」* や *「この URL をダウンロードして」* と言えばそのまま実行。タップできるクイックリプライのチップ、コンポーザーをロックしない追加質問、リロードをまたいで残るリッチな結果カード。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 📲 Messaging
**Telegram・WhatsApp・Discord・Slack** でスマホから双方向チャット。どこからでもプランナーに指示し、ジョブが完了したら通知を受け取る — 同じエージェント、異なるトランスポート。

</td>
<td width="50%" valign="top">

### ⬇️ ユニバーサルダウンローダー
内部は yt-dlp — YouTube・TikTok・Bilibili・Instagram・Twitter/X・SoundCloud・Vimeo、その他 **1,800以上のサイト** に対応。ウォーターマークなし、広告なし、上限なし。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧰 18の内蔵ツール
単機能ユーティリティ — キャプション、リフレーム、GIF、速度、トリム、字幕、ウォーターマーク、結合、オートズーム、ミュージックビジュアライザー、ボイスオーバー、さらに AI ヘルパー（翻訳、メタデータ、フック分析、自動チャプター）。**ほとんどが FFmpeg + Whisper で100% ローカルに動作 — API キー不要。** それぞれにインラインの結果プレビューが付いています。

</td>
<td width="50%" valign="top">

### ✨ プロアクティブアシスタント
チャットはあなたのライブなパイプラインを読み取り — *ダウンロード済みだが未クリップ*、*生成済みだが未アップロード*、*発掘済みだが未ダウンロード* — 頼まれるのを待たずに、最も価値の高い次の一手を1つ提案します。

</td>
</tr>
</table>

---

## 🆓 API キーなしで動くもの

高価な部分は無料でローカルに動きます。プラグインする AI のぶんだけ課金されます。

| 機能 | 使用技術 | コスト |
|:--------|:-----------|:-----|
| 1,800以上のサイトからの動画ダウンロード | yt-dlp | $0 |
| 音声の文字起こし | ローカル faster-whisper | $0 |
| ボイスオーバー（400以上の音声、70以上の言語） | Edge TTS | $0 |
| 単語ごとのアニメーションキャプション | FFmpeg + ASS 字幕 | $0 |
| BGM ライブラリ | ロイヤリティフリーのローカルライブラリ | $0 |
| 効果音の自動配置 | FFmpeg 合成 | $0 |
| Tools: リフレーム、GIF、速度、トリム、ウォーターマーク、結合、オートズーム、ミュージックビジュアライザー、字幕… | FFmpeg + Whisper | $0 |

YouTube / TikTok / Pexels には引き続き無料の API キーが必要です — リンクは [BYOK セクション](#-自分のキーを使う-byok) に。

---

## 🚀 クイックスタート

### 前提条件

| ツール | macOS | Linux | Windows |
|:-----|:-----|:------|:--------|
| **Python 3.11+** | `brew install python` | `apt install python3.11` | [python.org](https://www.python.org/downloads/) |
| **Node.js 18+** | `brew install node` | `apt install nodejs npm` | [nodejs.org](https://nodejs.org/) |
| **FFmpeg** | `brew install ffmpeg` | `apt install ffmpeg` | [ffmpeg.org](https://ffmpeg.org/download.html) |
| **ImageMagick** | `brew install imagemagick` | `apt install imagemagick` | [imagemagick.org](https://imagemagick.org/) |

### インストールと実行

```bash
git clone https://github.com/openclaw-easy/ViralMint.git
cd ViralMint

python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                                  # optional — keys can also be set in the UI
python run.py
```

初回起動時に、フロントエンドの依存関係をインストールし、SPA をビルドし、API を起動して、ブラウザで **http://localhost:16888** を開きます。

> 💡 **まだ API キーがない？** 起動後に **Settings → AI Provider** を開き、Anthropic・OpenAI・OpenRouter のキーを貼り付けてください。OpenRouter は300以上のモデルへの統一ゲートウェイで、1つのキーで Claude・GPT・Gemini・Llama・Mistral を利用できます。Edge TTS・Whisper・FFmpeg・yt-dlp はすべて設定不要でオフライン動作します。

### ソースからデスクトップアプリをビルド（任意）

ターミナルコマンドよりクリックできるアプリが欲しい場合、自己完結型の PyInstaller パイプラインがこのソースから macOS `.dmg`・Linux `.tar.gz`・Windows `.zip` をビルドします — UI は引き続きブラウザです。

```bash
PYTHON_BIN=./venv/bin/python VIRALMINT_VERSION=0.1.0-dev \
  bash desktop/scripts/build-app.sh
```

出力は `desktop/release/` に置かれます。初回ビルドは約10〜15分（PyInstaller のバンドルが最も時間のかかる部分です）。スキップ用フラグ、署名・公証の環境変数、スモークテストの手順は **[`desktop/README.md`](desktop/README.md)** にあります。

---

## 🔑 自分のキーを使う (BYOK)

各キーは `.env` *または* アプリ内の **Settings** でユーザーごとに設定できます — 設定されているほうが優先されます。ユーザーごとのキーは保存前に **AES-256 で暗号化** されます。キーはプロバイダーへ直接送られ、間に入る ViralMint のバックエンドサーバーはありません。

| 用途 | プロバイダー | 場所 | コスト |
|:----|:---------|:------|:-----|
| AI チャット、台本作成、分析 | **Anthropic** · **OpenAI** · **OpenRouter** | [console.anthropic.com](https://console.anthropic.com) · [platform.openai.com](https://platform.openai.com/api-keys) · [openrouter.ai/keys](https://openrouter.ai/keys) — Settings → AI Provider | 従量課金 |
| YouTube 発掘・コメント・My Channels | YouTube Data API v3 | [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials) — Settings → Service API Keys | 無料 1日1万ユニット |
| ストック映像 | Pexels | [pexels.com/api](https://www.pexels.com/api/) | 無料 |
| プレミアムボイスオーバー（任意） | OpenAI TTS | [platform.openai.com](https://platform.openai.com/api-keys) | 従量課金 |
| TikTok / Douyin 発掘 | **TikHub API**（推奨） | [tikhub.io](https://tikhub.io) | 無料枠あり |
| YouTube / TikTok アップロード | OAuth | Settings でワンクリック | 無料 |
| Telegram / Discord / Slack | Bot トークン | Settings → Messaging | 無料 |
| WhatsApp | QR スキャンでペアリング | Settings → Messaging | 無料 |

> ⚠️ **TikTok / Douyin のセッションクッキーによる発掘** も Settings に高度なフォールバックとして用意されていますが、これは各プラットフォームの利用規約に違反しており、そのクッキーのアカウントが、プラットフォームから見て実際に動作しているアカウントとみなされます。**そのリスクを明確に受け入れた場合を除き、TikHub API 経路を使用してください。** 詳細は [LEGAL.md](LEGAL.md#tiktok) を参照してください。

---

## 🏗️ アーキテクチャ

```
                     ┌────────────────────────────────────────────────┐
                     │            React 18 + MUI 7 SPA                │
                     │       (served by FastAPI in production)        │
                     │  Chat · Channels · Library · Stock Video       │
                     │  Clip Studio · Messaging · Tools · Settings    │
                     └─────────────────┬──────────────────────────────┘
                                       │  HTTP + WebSocket
                                       ▼
                     ┌────────────────────────────────────────────────┐
                     │           FastAPI · localhost:16888            │
                     ├────────────────────────────────────────────────┤
                     │  Planner Agent ─── streaming chat + actions    │
                     │  Scout Agent ───── YouTube · TikTok · Douyin   │
                     │                    (+ yt-dlp dynamic search)   │
                     │  Download Agent ── yt-dlp (1,800+ sites)       │
                     │  Analyzer Agent ── Whisper + AI insights       │
                     │  Generator Agent ─ Script → TTS → Stock →      │
                     │                    Captions → Music → MP4      │
                     │  Uploader Agent ── YouTube + TikTok OAuth      │
                     │  Messaging ─────── Telegram · WhatsApp ·       │
                     │                    Discord · Slack             │
                     └─────────────────┬──────────────────────────────┘
                                       │
              ┌────────────────────────┼─────────────────────────┐
              ▼                        ▼                         ▼
    ┌─────────────────┐    ┌──────────────────────┐    ┌──────────────────┐
    │  SQLite local   │    │  storage/ on disk    │    │  External APIs   │
    │  (encrypted     │    │  videos · audio ·    │    │  (BYOK, direct)  │
    │   credentials)  │    │  thumbnails · sfx    │    │                  │
    └─────────────────┘    └──────────────────────┘    └──────────────────┘
```

### 技術スタック

| レイヤー | スタック |
|:------|:------|
| **バックエンド** | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · SQLite · WebSockets |
| **フロントエンド** | React 18 · Vite · MUI 7 · Zustand · React Router 6 |
| **AI (BYOK)** | Anthropic Claude SDK · OpenAI SDK · OpenRouter (1つのキーで300以上のモデル) |
| **文字起こし** | faster-whisper（ローカル、多言語、GPU 対応） |
| **TTS** | Edge TTS（無料）· OpenAI TTS |
| **動画** | Pexels ストック · FFmpeg · Ken Burns 画像フォールバック |
| **キャプション** | FFmpeg + ASS（単語ごとのハイライトアニメーション） |
| **ダウンロード** | yt-dlp (1,800+ sites) |
| **メッセージング** | python-telegram-bot · discord.py · slack-sdk · neonize (WhatsApp) |
| **セキュリティ** | 保存時の認証情報に Fernet (AES-256) |

---

## 📸 スクリーンショット

<table>
<tr>
<td width="50%" align="center" valign="top">
  <a href="docs/screenshots/library.webp"><img src="docs/screenshots/library.webp" alt="Library — Scout results with virality scores" /></a>
  <sub><b>Library — 発掘結果</b><br/>プラットフォームを横断して発見した動画を AI バイラリティスコア順にソート、ワンクリックでダウンロード可能。</sub>
</td>
<td width="50%" align="center" valign="top">
  <a href="docs/screenshots/clip-studio.webp"><img src="docs/screenshots/clip-studio.webp" alt="Clip Studio — extract viral shorts from a long-form video" /></a>
  <sub><b>Clip Studio — バイラルクリップ抽出</b><br/>AI が長尺動画からベストな30〜60秒を選び、採点し、自動でキャプションを焼き込みます。</sub>
</td>
</tr>
<tr>
<td width="50%" align="center" valign="top">
  <a href="docs/screenshots/messaging.webp"><img src="docs/screenshots/messaging.webp" alt="Messaging — Telegram, WhatsApp, Discord, Slack" /></a>
  <sub><b>Messaging — スマホからチャット</b><br/>Telegram・WhatsApp・Discord・Slack を接続してプランナーを操作し、ジョブ通知を受け取れます。</sub>
</td>
<td width="50%" align="center" valign="top">
  <a href="docs/screenshots/channel-analysis.webp"><img src="docs/screenshots/channel-analysis.webp" alt="My Channels — channel analytics" /></a>
  <sub><b>My Channels — チャンネル分析</b><br/>任意の YouTube / TikTok チャンネルを URL で接続。再生数・エンゲージメント・中央値再生数・外れ値検出を表示。</sub>
</td>
</tr>
<tr>
<td width="50%" align="center" valign="top">
  <a href="docs/screenshots/smart-video.webp"><img src="docs/screenshots/smart-video.webp" alt="Smart Video studio" /></a>
  <sub><b>Smart Video スタジオ</b><br/>自分のクリップとストック映像をミックス。単語ごとのキャプション、BGM、リアルタイムのコスト見積もり。</sub>
</td>
<td width="50%" align="center" valign="top">
  <a href="docs/screenshots/tools.webp"><img src="docs/screenshots/tools.webp" alt="Tools — single-purpose utilities" /></a>
  <sub><b>Tools — 18のユーティリティ</b><br/>リフレーム、ウォーターマーク、GIF、速度、トリム、字幕、ボイスオーバー、フック分析 — ほとんどが API キー不要でローカルに動作。</sub>
</td>
</tr>
</table>

---

## 📁 プロジェクト構成

```
ViralMint/
├── run.py                          # 🚀 Single entry point
├── launcher.py                     # System-tray launcher (optional)
│
├── backend/
│   ├── agents/                     # Planner, Scout, Download, Analyzer, Generator, Uploader
│   ├── api/                        # REST + WebSocket endpoints
│   ├── core/                       # AI client, BYOK key resolver, crypto, WebSocket manager
│   ├── messaging/                  # Telegram / WhatsApp / Discord / Slack channels
│   ├── models/                     # SQLAlchemy models
│   └── services/                   # TTS, video gen, captions, music, yt-dlp, Whisper, …
│
├── frontend/
│   └── src/
│       ├── pages/                  # Chat · Channels · Library · Stock Video · Clip Studio · …
│       ├── components/             # Reusable UI (chat, settings, videos, …)
│       ├── hooks/                  # WebSocket, settings, jobs, source video
│       └── store/                  # Zustand global state
│
├── tests/                          # pytest suite (~1,000 tests)
├── storage/                        # Downloaded videos, audio, generated output (gitignored)
│
├── requirements.txt
├── .env.example
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE                         # AGPL-3.0
```

---

## 🤝 コントリビュート

プルリクエスト歓迎 — バグ修正、新プラットフォーム、追加のメッセージングチャンネル、パフォーマンス改善、ドキュメント、何でも。ワークフローとハウススタイルは [CONTRIBUTING.md](CONTRIBUTING.md) を、最初の issue を立てる前に [行動規範](CODE_OF_CONDUCT.md) をご確認ください。

- 📋 **最近の変更:** [CHANGELOG.md](CHANGELOG.md)
- 🐛 **バグ報告:** [issue を作成](https://github.com/openclaw-easy/ViralMint/issues/new?template=bug_report.md)
- 💡 **機能リクエスト:** [issue を作成](https://github.com/openclaw-easy/ViralMint/issues/new?template=feature_request.md)
- 🔐 **セキュリティ脆弱性:** [SECURITY.md](SECURITY.md) — 公開 issue は **立てないでください**。

## 📜 ライセンスと利用条件

ViralMint は **GNU Affero General Public License v3.0**（[LICENSE](LICENSE)）の下でライセンスされています。

- ✅ 個人利用・商用利用・改変・再配布が無料
- ✅ その上に SaaS を運営してよい
- ⚠️ 配布する場合（または公開ネットワークサービスとして運用する場合）、改変したソースを同じ AGPL-3.0 の条件で共有する必要があります

**ViralMint は自分のマシンで動かすツールです。** メンテナーはあなたのコンテンツをホストせず、API 呼び出しをプロキシもしません — すべての操作は、あなた自身のプラットフォームとキーで、あなたが行うものです。発掘やダウンローダーの機能を使う前に [LEGAL.md](LEGAL.md) を読み、何が公認されており（YouTube Data API、OAuth アップロード、Pexels）、何が自己責任で（TikTok / Douyin のセッションクッキー発掘）、各プラットフォームの利用規約の下で何に対して責任を負うのかを理解してください。

---

### 🙋 セルフホストしたくない？

**[viralmint.net](https://viralmint.net)** にホスト版のビルドもあります — 発掘・分析・生成のエンジンは同じで、署名・公証済み、設定する API キーもありません（BYOK の代わりにプリペイドクレジット）。クローズドソースで自動アップロードはしません — 自分でキーやインストールを管理したくない人向けの、異なるトレードオフです。詳しい比較と FAQ: **[docs/hosted-vs-self-hosted.md](docs/hosted-vs-self-hosted.md)**。それ以外は、必要なものはすべてここにあります — 読み進めて `python run.py` を実行してください。

---

<div align="center">

## ⭐ ViralMint が役に立ったら、スターを

スターはこのプロジェクトを助ける最大の要素です — コントリビューターを引き寄せ、awesome リストへの掲載資格を解き、他のクリエイターに一見の価値があると伝えます。ワンクリックで完了します。

**[⭐ openclaw-easy/ViralMint にスター](https://github.com/openclaw-easy/ViralMint)**

<!-- Star-history is a LINK, not an embedded <img>, on purpose: GitHub
     restricted the stargazers API on 2026-06-30 (star data is readable
     only by a repo's own admins/collaborators), which broke star-history's
     server-side rendering for public README embeds — the inline SVG now
     returns "rate-limited / not available" for anonymous visitors. A link
     always works and never shows a broken image. If star-history's
     encrypted-token embed becomes viable again, this can go back to a
     <picture> block — but NEVER embed a raw github_pat_ token in the URL
     (it would be public + abused); only star-history's encrypted token. -->
**[📈 ViralMint のスター履歴を見る →](https://star-history.com/#openclaw-easy/ViralMint&Date)**

<br/>

**FastAPI、React、Whisper、FFmpeg、そして大量の非同期 Python で構築。**

ウェブサイト: **[viralmint.net](https://viralmint.net)** · ソース: **[github.com/openclaw-easy/ViralMint](https://github.com/openclaw-easy/ViralMint)** · ライセンス: **[AGPL-3.0](LICENSE)**

</div>
