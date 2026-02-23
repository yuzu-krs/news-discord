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
from datetime import datetime, timezone, timedelta, time
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
        # dc:subject でIT系カテゴリのみに絞る
        "categories": {"AI", "ソフトウェア", "ハードウェア", "セキュリティ", "ネットサービス", "ウェブアプリ"},
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
MORNING_TIME = time(hour=7, minute=0, tzinfo=JST)   # 毎朝 7:00 JST
WEEKLY_TIME  = time(hour=9, minute=0, tzinfo=JST)   # 毎週日曜 9:00 JST

# ── ホットキーワードスコアリング ───────────────────────────
HOT_KEYWORDS = [
    "AI", "ChatGPT", "GPT", "LLM", "生成AI", "Claude", "Gemini", "エージェント",
    "セキュリティ", "脆弱性", "ゼロデイ", "サイバー攻撃",
    "TypeScript", "Python", "Rust", "React", "Next.js", "Vue", "Go",
    "クラウド", "AWS", "Azure", "GCP", "Kubernetes", "Docker",
    "GitHub", "OSS", "オープンソース",
    "スタートアップ", "資金調達", "割悹",
]


def score_entry(entry: dict, feed_meta: dict) -> int:
    """タイトルのホットキーワード数 + フィードボーナスでスコアを返す"""
    title = entry.get("title", "")
    score = sum(1 for kw in HOT_KEYWORDS if kw.lower() in title.lower())
    # Qiita/Zennはエンジニア向け特化なのでボーナス
    if feed_meta["name"] in ("Qiita トレンド", "Zenn トレンド"):
        score += 1
    return score


def pick_spotlight(
    results: list[tuple[dict, list[tuple[str, dict]]]],
) -> tuple[dict, dict] | None:
    """収集記事の中から最高スコアの1本を返す"""
    best: tuple[int, dict, dict] | None = None
    for feed_meta, entries in results:
        for _, entry in entries:
            s = score_entry(entry, feed_meta)
            if best is None or s > best[0]:
                best = (s, feed_meta, entry)
    if best is None:
        return None
    return best[1], best[2]  # (feed_meta, entry)


# ── フィードチェック共通処理 ──────────────────────────────
async def _check_feeds(channel, feeds: list[dict], max_per_feed: int | None = None, shuffle: bool = False) -> int:
    """指定されたフィード一覧をチェックし新着記事を投稿する。投稿件数を返す。

    Args:
        max_per_feed: 1フィードあたりの最大投稿件数。None の場合は無制限。
        shuffle: True の場合、新着記事をランダムに並び替えて投稿する。
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

            # カテゴリフィルター（feedメタに"categories"が指定されている場合のみ絞り込む）
            allowed_categories = feed_meta.get("categories")

            # 新着を古い順に並べて投稿
            new_entries = []
            for entry in feed.entries:
                # カテゴリフィルタリング
                if allowed_categories:
                    subject = entry.get("tags", [])
                    # feedparserはdc:subjectをtagsに格納する（カンマ区切り文字列の場合あり）
                    entry_cats = set()
                    for t in subject:
                        for part in t.get("term", "").split(","):
                            entry_cats.add(part.strip())
                    if not entry_cats & allowed_categories:
                        continue
                aid = article_id(entry)
                if aid not in seen[feed_name]:
                    new_entries.append((aid, entry))

            # ランダム取得の場合はシャッフル
            if shuffle:
                import random
                random.shuffle(new_entries)

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


# ── 朝ニュース用: 記事を収集する（投稿なし） ───────────────────
async def _collect_morning_articles(max_per_feed: int = 2) -> tuple[list[tuple], dict]:
    """朝のフィードから新着記事を収集し、(feed_meta, entries)のリストとupdated seenを返す。"""
    seen = load_seen()
    results: list[tuple[dict, list[tuple[str, dict]]]] = []

    async with aiohttp.ClientSession() as session:
        for feed_meta in MORNING_FEEDS:
            feed_name = feed_meta["name"]
            feed = await fetch_feed(session, feed_meta["url"])

            if not feed or not feed.get("entries"):
                print(f"[WARN] {feed_name}: エントリなし")
                continue

            if feed_name not in seen:
                seen[feed_name] = []

            allowed_categories = feed_meta.get("categories")

            new_entries = []
            for entry in feed.entries:
                if allowed_categories:
                    entry_cats = set()
                    for t in entry.get("tags", []):
                        for part in t.get("term", "").split(","):
                            entry_cats.add(part.strip())
                    if not entry_cats & allowed_categories:
                        continue
                aid = article_id(entry)
                if aid not in seen[feed_name]:
                    new_entries.append((aid, entry))

            # 初回起動時は最新件のみ（大量投稿防止）
            if not seen[feed_name] and len(new_entries) > max_per_feed:
                for aid, _ in new_entries[:-max_per_feed]:
                    seen[feed_name].append(aid)
                new_entries = new_entries[-max_per_feed:]

            if len(new_entries) > max_per_feed:
                for aid, _ in new_entries[:-max_per_feed]:
                    seen[feed_name].append(aid)
                new_entries = new_entries[-max_per_feed:]

            # 既読に追加
            for aid, _ in new_entries:
                seen[feed_name].append(aid)

            if len(seen[feed_name]) > 500:
                seen[feed_name] = seen[feed_name][-300:]

            if new_entries:
                results.append((feed_meta, new_entries))

    save_seen(seen)
    return results


# ── 朝の定時ニュース (Qiita / Zenn / GIGAZINE) ───────────
async def _post_morning_news(channel) -> None:
    """朝のテックニュースを1つのEmbedにまとめて投稿する"""
    results = await _collect_morning_articles(max_per_feed=2)

    if not results:
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] 🌅 朝のニュース: 新着なし")
        return

    today = datetime.now(JST).strftime("%Y/%m/%d")

    # 今日の注目1本をdescriptionに入れる
    spotlight = pick_spotlight(results)
    spotlight_link = ""
    description = ""
    if spotlight:
        sp_feed, sp_entry = spotlight
        sp_title = sp_entry.get("title", "タイトルなし")
        sp_link  = sp_entry.get("link", "")
        spotlight_link = sp_link
        description = f"⭐ **今日の注目**\n[🔗 {sp_title}]({sp_link})\n\n━━━━━━━━"

    embed = discord.Embed(
        title=f"☀️ {today} 朝のテックニュース",
        description=description if description else None,
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )

    total = 0
    for feed_meta, entries in results:
        lines = []
        for _, entry in entries:
            link = entry.get("link", "")
            # 注目記事と同じURLはフィード一覧から除外（重複防止）
            if spotlight_link and link == spotlight_link:
                continue
            title = entry.get("title", "タイトルなし")
            lines.append(f"[🔗 {title}]({link})")
            total += 1
        if not lines:
            continue
        embed.add_field(
            name=f"\n{feed_meta['name']}",
            value="\n".join(lines),
            inline=False,
        )

    embed.set_footer(text=f"計 {total} 件 | 毎朝 7:00 JST 配信")

    try:
        await channel.send(embed=embed)
    except discord.HTTPException as e:
        print(f"[ERROR] 朝ニュース送信失敗: {e}")
        return

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 🌅 朝のニュースチェック完了 - 新着 {total} 件をまとめて投稿")


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


@morning_news.error
async def morning_news_error(error):
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] ❌ morning_news エラー: {error}")
    import traceback
    traceback.print_exc()
    # タスクが停止した場合は再起動
    if not morning_news.is_running():
        morning_news.restart()


# ── Bot 本体 ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ ログイン完了: {bot.user} (ID: {bot.user.id})")
    print(f"📡 チャンネルID: {CHANNEL_ID}")
    print(f"🌅 朝のニュース: 毎日 {MORNING_TIME.strftime('%H:%M')} JST")

    # 起動時に朝のニュースを投稿
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        print("📰 起動時のニュースを投稿中...")
        await _post_morning_news(channel)
    else:
        print(f"[ERROR] チャンネル {CHANNEL_ID} が見つかりません")

    if not morning_news.is_running():
        morning_news.start()
        print("✅ morning_news タスク開始")
    if not weekly_ranking.is_running():
        weekly_ranking.start()
        print("✅ weekly_ranking タスク開始")


# ── 週刊ランキング (毎週日曜 9:00 JST) ────────────────────
async def _post_weekly_ranking(channel) -> None:
    """はてナBM ITホットエントリー TOP5 をランキング形式で投稿する"""
    async with aiohttp.ClientSession() as session:
        feed = await fetch_feed(session, "https://b.hatena.ne.jp/hotentry/it.rss")

    if not feed or not feed.get("entries"):
        print("[WARN] 週刊ランキング: エントリなし")
        return

    entries = feed.entries[:5]
    week_start = (datetime.now(JST) - timedelta(days=6)).strftime("%m/%d")
    week_end   = datetime.now(JST).strftime("%m/%d")

    embed = discord.Embed(
        title=f"🏆 今週のITニュース ランキング TOP5",
        description=f"{week_start} 〜 {week_end}　|　はてなブックマーク ITホットエントリーより",
        color=0x00A4DE,
        timestamp=datetime.now(timezone.utc),
    )

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, entry in enumerate(entries):
        title  = entry.get("title", "タイトルなし")
        link   = entry.get("link", "")
        bcount = getattr(entry, "hatena_bookmarkcount", "") or ""
        count_str = f"　🔖 {bcount}件" if bcount else ""
        embed.add_field(
            name=f"{medals[i]}　{title}",
            value=f"[{link}]({link}){count_str}",
            inline=False,
        )

    embed.set_footer(text="毎週日曜 9:00 JST 配信")
    try:
        await channel.send(embed=embed)
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] 🏆 週刊ランキング投稿完了")
    except discord.HTTPException as e:
        print(f"[ERROR] 週刊ランキング送信失敗: {e}")


@tasks.loop(time=WEEKLY_TIME)
async def weekly_ranking():
    """毎週日曜 9:00 JST に週刊ランキングを投稿する"""
    if datetime.now(JST).weekday() != 6:  # 6 = 日曜
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return
    await _post_weekly_ranking(channel)


@weekly_ranking.before_loop
async def before_weekly_ranking():
    await bot.wait_until_ready()


# ── コマンド ──────────────────────────────────────────────
@bot.command(name="ranking")
async def cmd_ranking(ctx):
    """手動で週刊ランキングを表示する"""
    await ctx.send("📥 ランキングを取得中…")
    await _post_weekly_ranking(ctx.channel)


@bot.command(name="news")
async def cmd_news(ctx):
    """手動で最新記事をランダムに取得して投稿する"""
    await ctx.send("🔄 フィードをチェック中…")
    channel = ctx.channel
    new_count = await _check_feeds(channel, MORNING_FEEDS, max_per_feed=5, shuffle=True)
    if new_count == 0:
        await ctx.send("⚠️ 新着記事がありません（既読済み）")
    else:
        await ctx.send(f"✅ {new_count} 件の記事を投稿しました！")


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
    embed.set_footer(text=f"morning_news タスク稼働中={'✅' if morning_news.is_running() else '❌'}")
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
