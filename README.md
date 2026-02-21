# discord-news-bot

Qiita、Zenn、GIGAZINE の RSS フィードを毎朝 7:00 JST に Discord に自動投稿するボット

## 概要

毎日7時にプログラミング・テック関連の最新記事をまとめて Discord チャンネルに投稿します。

- **Qiita トレンド**: プログラミング知識共有プラットフォーム
- **Zenn トレンド**: エンジニア向けメディア
- **GIGAZINE**: テック・ニュースサイト

各サイトから最新5件まで取得し、重複は自動的に排除します。

## 機能

- ⏰ **定時投稿**: 毎朝 7:00 JST に自動実行
- ��� **起動時実行**: ボット起動時にも即座にニュースを投稿
- ��� **複数フィード対応**: Qiita / Zenn / GIGAZINE を同時配信
- ��� **重複排除**: 既読記事は自動的にスキップ
- ��� **見やすい形式**: Discord Embed で整形して投稿

## 必要な環境

- Python 3.10+
- discord.py >= 2.3.0
- feedparser >= 6.0.0
- aiohttp >= 3.9.0
- python-dotenv >= 1.0.0

## インストール

1. リポジトリをクローン

```bash
git clone https://github.com/yourusername/discord-news-bot.git
cd discord-news-bot
```

2. 依存パッケージをインストール

```bash
pip install -r requirements.txt
```

3. `.env` ファイルを作成して設定

```
DISCORD_TOKEN=your_discord_bot_token
DISCORD_CHANNEL_ID=your_channel_id
```

## 使い方

### ボット起動

```bash
python bot.py
```

### コマンド

- `!news` - 手動でニュースをチェック・投稿
- `!status` - ボットの状態確認
- `!reset` - 既読データをリセット（所有者のみ）

## 設定

[bot.py](bot.py) の以下の部分で調整可能：

- `MORNING_TIME`: 投稿時刻（デフォルト: 7:00 JST）
- `max_per_feed`: 各サイトの投稿件数上限（デフォルト: 5件）
- `MORNING_FEEDS`: 投稿対象のフィード
