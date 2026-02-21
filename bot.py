"""
IT News Discord Bot
- Zenn トレンド記事
- はてなブックマーク IT ホットエントリー
- CodeZine 新着記事
のRSSフィードを定期取得し、Discordチャンネルに自動投稿する。
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
import discord
import feedparser
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ── 設定読み込み ──────────────────────────────────────────
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

# RSSフィード定義
RSS_FEEDS = [
    # ── 開発者向けメディア ──
    {
        "name": "Zenn トレンド",
        "url": "https://zenn.dev/feed",
        "color": 0x3EA8FF,   # Zenn ブルー
        "icon": "https://zenn.dev/images/logo-transparent.png",
    },
    {
        "name": "Qiita トレンド",
        "url": "https://qiita.com/popular-items/feed",
        "color": 0x55C500,   # Qiita グリーン
        "icon": "https://cdn.qiita.com/assets/favicons/public/icon-plain.ico",
    },
    {
        "name": "はてなブックマーク IT",
        "url": "https://b.hatena.ne.jp/hotentry/it.rss",
        "color": 0x00A4DE,   # はてなブルー
        "icon": "https://b.hatena.ne.jp/favicon.ico",
    },
    {
        "name": "CodeZine 新着記事",
        "url": "https://codezine.jp/rss/new/20/index.xml",
        "color": 0x0A7E07,   # CodeZine グリーン
        "icon": "https://codezine.jp/lib/img/common/cz_logo_black.svg",
    },
    # ── テック系ニュース ──
    {
        "name": "Publickey",
        "url": "https://www.publickey1.jp/atom.xml",
        "color": 0xDD4814,   # Publickey オレンジ
        "icon": "https://www.publickey1.jp/favicon.ico",
    },
    {
        "name": "ITmedia NEWS",
        "url": "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
        "color": 0xEB0000,   # ITmedia レッド
        "icon": "https://www.itmedia.co.jp/favicon.ico",
    },
    {
        "name": "@IT",
        "url": "https://rss.itmedia.co.jp/rss/2.0/ait.xml",
        "color": 0x0078D4,   # @IT ブルー
        "icon": "https://atmarkit.itmedia.co.jp/favicon.ico",
    },
    {
        "name": "Gihyo.jp",
        "url": "https://gihyo.jp/feed/rss2",
        "color": 0x2B2B2B,   # Gihyo ダーク
        "icon": "https://gihyo.jp/favicon.ico",
    },
    {
        "name": "GIGAZINE",
        "url": "https://gigazine.net/news/rss_2.0/",
        "color": 0x333333,   # GIGAZINE ブラック
        "icon": "https://gigazine.net/favicon.ico",
    },
]

# 既読管理ファイル
SEEN_FILE = Path(__file__).parent / "seen_articles.json"


# ── 既読管理 ──────────────────────────────────────────────
def load_seen() -> dict[str, list[str]]:
    """既に投稿済みの記事IDを読み込む"""
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_seen(seen: dict[str, list[str]]) -> None:
    """投稿済みの記事IDを保存する"""
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def article_id(entry: dict) -> str:
    """フィードエントリから一意IDを生成する"""
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── フィード取得 ──────────────────────────────────────────
async def fetch_feed(session: aiohttp.ClientSession, url: str) -> feedparser.FeedParserDict:
    """非同期でRSSフィードを取得してパースする"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
            return feedparser.parse(text)
    except Exception as e:
        print(f"[ERROR] フィード取得失敗: {url} - {e}")
        return feedparser.FeedParserDict()


# ── Embed作成 ─────────────────────────────────────────────
JST = timezone(timedelta(hours=9))


def make_embed(entry: dict, feed_meta: dict) -> discord.Embed:
    """フィードエントリからDiscord Embedを作成する"""
    title = entry.get("title", "タイトルなし")
    link = entry.get("link", "")
    summary = entry.get("summary", entry.get("description", ""))

    # HTMLタグを簡易除去
    import re
    summary = re.sub(r"<[^>]+>", "", summary)
    if len(summary) > 200:
        summary = summary[:200] + "…"

    embed = discord.Embed(
        title=title,
        url=link,
        description=summary if summary else None,
        color=feed_meta["color"],
    )

    # 公開日時
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        try:
            dt = datetime(*published[:6], tzinfo=timezone.utc)
            embed.timestamp = dt
        except Exception:
            pass

    # 著者
    author = entry.get("author")
    if author:
        embed.set_author(name=author)

    # フッター: フィード名
    embed.set_footer(text=feed_meta["name"])

    # サムネイル (はてブにはenclosureが含まれることがある)
    if "media_thumbnail" in entry and entry["media_thumbnail"]:
        embed.set_thumbnail(url=entry["media_thumbnail"][0].get("url", ""))
    elif "enclosures" in entry and entry["enclosures"]:
        enc = entry["enclosures"][0]
        if enc.get("type", "").startswith("image"):
            embed.set_thumbnail(url=enc.get("href", ""))

    return embed


# ── 朝のフィード定義 ──────────────────────────────────────
MORNING_FEED_NAMES = {"Qiita トレンド", "Zenn トレンド", "GIGAZINE"}
MORNING_FEEDS = [f for f in RSS_FEEDS if f["name"] in MORNING_FEED_NAMES]
MORNING_TIME = datetime.time(hour=7, minute=0, tzinfo=JST)  # 毎朝 7:00 JST


# ── フィードチェック共通処理 ──────────────────────────────
async def _check_feeds(channel, feeds: list[dict], max_per_feed: int | None = None) -> int:
    """指定されたフィード一覧をチェックし新着記事を投稿する。投稿件数を返す。

    Args:
        max_per_feed: 1フィードあたりの最大投稿件数。None の場合は無制限。
    """
    seen = load_seen()
    new_count = 0

    async with aiohttp.ClientSession() as session:
        for feed_meta in feeds:
            feed_name = feed_meta["name"]
            feed = await fetch_feed(session, feed_meta["url"])

            if not feed or not feed.get("entries"):
                print(f"[WARN] {feed_name}: エントリなし")
                continue

            if feed_name not in seen:
                seen[feed_name] = []

            # 新着を古い順に並べて投稿
            new_entries = []
            for entry in feed.entries:
                aid = article_id(entry)
                if aid not in seen[feed_name]:
                    new_entries.append((aid, entry))

            # 初回起動時は最新5件だけ投稿（大量投稿防止）
            init_limit = max_per_feed if max_per_feed is not None else 5
            if not seen[feed_name] and len(new_entries) > init_limit:
                skipped = new_entries[:-init_limit]
                for aid, _ in skipped:
                    seen[feed_name].append(aid)
                new_entries = new_entries[-init_limit:]

            # 件数上限を適用（最新の記事を優先）
            if max_per_feed is not None and len(new_entries) > max_per_feed:
                skipped = new_entries[:-max_per_feed]
                for aid, _ in skipped:
                    seen[feed_name].append(aid)
                new_entries = new_entries[-max_per_feed:]

            for aid, entry in new_entries:
                embed = make_embed(entry, feed_meta)
                try:
                    await channel.send(embed=embed)
                    new_count += 1
                except discord.HTTPException as e:
                    print(f"[ERROR] 送信失敗: {e}")
                    continue

                seen[feed_name].append(aid)
                await asyncio.sleep(1)  # レートリミット対策

            # 既読リストが大きくなりすぎないよう制限
            if len(seen[feed_name]) > 500:
                seen[feed_name] = seen[feed_name][-300:]

    save_seen(seen)
    return new_count


# ── Bot 本体 ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"\u2705 ログイン完了: {bot.user} (ID: {bot.user.id})")
    print(f"📡 チャンネルID: {CHANNEL_ID}")
    print(f"🌅 朝のニュース: 毎日 {MORNING_TIME.strftime('%H:%M')} JST")

    # 起動時に即座実行
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        print("🔄 起動時チェックを実行中…")
        await _post_morning_news(channel)

    if not morning_news.is_running():
        morning_news.start()


# ── 朝の定時ニュース (Qiita / Zenn / GIGAZINE) ───────────
async def _post_morning_news(channel) -> None:
    """朝のテックニュースを投稿する共通処理"""
    await channel.send("☀️ **おはようございます！朝のテックニュースをお届けします**")
    new_count = await _check_feeds(channel, MORNING_FEEDS, max_per_feed=5)
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 🌅 朝のニュースチェック完了 - 新着 {new_count} 件投稿")


@tasks.loop(time=MORNING_TIME)
async def morning_news():
    """毎朝 7:00 JST に Qiita・Zenn・GIGAZINE の最新記事を投稿する"""
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] チャンネル {CHANNEL_ID} が見つかりません")
        return
    await _post_morning_news(channel)


@morning_news.before_loop
async def before_morning_news():
    await bot.wait_until_ready()


# ── コマンド ──────────────────────────────────────────────
@bot.command(name="news")
async def cmd_news(ctx):
    """手動で最新記事を取得して投稿する"""
    await ctx.send("🔄 フィードをチェック中…")
    channel = ctx.channel
    await _check_feeds(channel, MORNING_FEEDS, max_per_feed=3)
    await ctx.send("✅ チェック完了！")


@bot.command(name="status")
async def cmd_status(ctx):
    """Botの状態を表示する"""
    seen = load_seen()
    embed = discord.Embed(
        title="📊 Bot ステータス",
        color=0x5865F2,
    )
    for feed_meta in RSS_FEEDS:
        name = feed_meta["name"]
        count = len(seen.get(name, []))
        embed.add_field(name=name, value=f"既読: {count} 件", inline=True)

    embed.add_field(
        name="チェック間隔",
        value=f"{CHECK_INTERVAL_MINUTES} 分",
        inline=False,
    )
    embed.set_footer(text=f"次回チェック: check_feeds タスク稼働中={'✅' if check_feeds.is_running() else '❌'}")
    await ctx.send(embed=embed)


@bot.command(name="reset")
@commands.is_owner()
async def cmd_reset(ctx):
    """既読データをリセットする（Bot所有者のみ）"""
    if SEEN_FILE.exists():
        SEEN_FILE.unlink()
    await ctx.send("🗑 既読データをリセットしました。")


# ── 起動 ──────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN が設定されていません。.env ファイルを確認してください。")
        exit(1)
    if CHANNEL_ID == 0:
        print("❌ DISCORD_CHANNEL_ID が設定されていません。.env ファイルを確認してください。")
        exit(1)

    bot.run(TOKEN)
