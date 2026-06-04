"""
cogs/invest.py — Happy Stock Market
  - Virtual stocks with live prices
  - Prices fluctuate every 30 minutes automatically
  - Buy / Sell stocks with HC
  - Portfolio tracking
  - Market trends (bull/bear)
"""

import discord
from discord.ext import commands
from discord.ext import tasks
import random
import asyncio
from datetime import datetime, timezone

from utils.db import db
from utils.helpers import BOT_OWNER_ID, ctx_owner

# ── Collections ───────────────────────────────────────────────────────────────
market_col    = db["market_stocks"]    # {symbol, name, price, change_pct, trend, history}
portfolio_col = db["portfolios"]       # {user_id, holdings: {symbol: qty}, invested: {symbol: total_cost}}

# ── Starting stocks ───────────────────────────────────────────────────────────
DEFAULT_STOCKS = [
    {"symbol": "HAPPY",  "name": "Happy Corp",        "price": 100,  "sector": "Tech"},
    {"symbol": "MEME",   "name": "Meme Industries",   "price": 50,   "sector": "Media"},
    {"symbol": "CHAI",   "name": "Chai Holdings",     "price": 200,  "sector": "Food"},
    {"symbol": "DANK",   "name": "Dank Ventures",     "price": 75,   "sector": "Gaming"},
    {"symbol": "MOON",   "name": "Moon Capital",      "price": 500,  "sector": "Finance"},
    {"symbol": "CRINGE", "name": "Cringe Labs",       "price": 25,   "sector": "Tech"},
    {"symbol": "VIBE",   "name": "Vibe Solutions",    "price": 150,  "sector": "Lifestyle"},
    {"symbol": "GRIND",  "name": "Grind Co.",         "price": 300,  "sector": "Finance"},
]

TREND_EMOJI = {"bull": "📈", "bear": "📉", "neutral": "➡️"}


# ── Helpers ───────────────────────────────────────────────────────────────────
async def get_stock(symbol: str) -> dict | None:
    return await market_col.find_one({"symbol": symbol.upper()})

async def get_portfolio(user_id: int) -> dict:
    doc = await portfolio_col.find_one({"user_id": str(user_id)})
    if not doc:
        doc = {"user_id": str(user_id), "holdings": {}, "invested": {}}
        await portfolio_col.insert_one(doc)
    return doc

def _pct_color(pct: float) -> int:
    if pct > 0:   return 0x57F287
    if pct < 0:   return 0xED4245
    return 0x2B2D31

def _fmt_pct(pct: float) -> str:
    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "—")
    return f"{arrow} {abs(pct):.2f}%"


class Invest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Startup ────────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_ready(self):
        await self._ensure_stocks()
        if not self.price_update.is_running():
            self.price_update.start()

    async def _ensure_stocks(self):
        """Insert default stocks if market is empty."""
        count = await market_col.count_documents({})
        if count == 0:
            for s in DEFAULT_STOCKS:
                s["change_pct"] = 0.0
                s["trend"]      = "neutral"
                s["history"]    = [s["price"]]
                s["volume"]     = 0
            await market_col.insert_many(DEFAULT_STOCKS)

    # ── Price update loop (every 30 min) ──────────────────────────────────────
    @tasks.loop(minutes=30)
    async def price_update(self):
        await self.bot.wait_until_ready()
        stocks = await market_col.find({}).to_list(50)

        for stock in stocks:
            old_price = stock["price"]
            trend     = stock.get("trend", "neutral")

            # Trend bias
            if trend == "bull":
                bias = random.uniform(0.0, 0.06)
            elif trend == "bear":
                bias = random.uniform(-0.06, 0.0)
            else:
                bias = 0.0

            # Random fluctuation + trend bias
            change_pct = random.uniform(-0.08, 0.08) + bias
            new_price  = max(1, round(old_price * (1 + change_pct), 2))

            # Randomly flip trend occasionally
            if random.random() < 0.15:
                new_trend = random.choice(["bull", "bear", "neutral", "neutral"])
            else:
                new_trend = trend

            # Keep last 24 price points (48 hours of history)
            history = stock.get("history", [old_price])[-23:]
            history.append(new_price)

            await market_col.update_one(
                {"symbol": stock["symbol"]},
                {"$set": {
                    "price":      new_price,
                    "change_pct": round(change_pct * 100, 2),
                    "trend":      new_trend,
                    "history":    history,
                    "last_update": datetime.now(timezone.utc),
                }}
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  MARKET OVERVIEW
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["mrkt", "stocks"])
    async def market(self, ctx):
        """View the Happy Stock Market — all stocks and current prices."""
        stocks = await market_col.find({}).sort("symbol", 1).to_list(20)
        if not stocks:
            return await ctx.reply("Market not initialized yet. Try again in a moment.")

        embed = discord.Embed(
            title="Happy Stock Market",
            color=0xF0C040,
            timestamp=datetime.now(timezone.utc)
        )

        lines = []
        for s in stocks:
            emoji = TREND_EMOJI.get(s.get("trend", "neutral"), "➡️")
            pct   = s.get("change_pct", 0)
            arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "—")
            lines.append(
                f"{emoji} `{s['symbol']:<6}` **{s['price']:,.2f} HC**  "
                f"{arrow} {abs(pct):.1f}%  —  {s['name']}"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(text="Prices update every 30 min · ,stock <SYMBOL> for details · ,buy <SYMBOL> <qty> to invest")
        await ctx.reply(embed=embed)

    # ── Stock detail ───────────────────────────────────────────────────────────
    @commands.command(aliases=["stock"])
    async def stockinfo(self, ctx, symbol: str = None):
        """View detailed info for a stock. Usage: ,stock HAPPY"""
        if not symbol:
            return await ctx.reply("Usage: `,stock <SYMBOL>`  e.g. `,stock HAPPY`")

        s = await get_stock(symbol)
        if not s:
            return await ctx.reply(f"Stock `{symbol.upper()}` not found. Use `,market` to see all stocks.")

        pct    = s.get("change_pct", 0)
        trend  = s.get("trend", "neutral")
        hist   = s.get("history", [s["price"]])
        high   = max(hist)
        low    = min(hist)

        # Mini ASCII chart from last 8 points
        chart_pts = hist[-8:]
        if len(chart_pts) > 1:
            mn, mx = min(chart_pts), max(chart_pts)
            rng    = mx - mn or 1
            bars   = ["▁","▂","▃","▄","▅","▆","▇","█"]
            chart  = "".join(bars[int((p - mn) / rng * 7)] for p in chart_pts)
        else:
            chart = "—"

        embed = discord.Embed(
            title=f"{TREND_EMOJI[trend]} {s['name']} ({s['symbol']})",
            color=_pct_color(pct)
        )
        embed.add_field(name="Price",   value=f"**{s['price']:,.2f} HC**",  inline=True)
        embed.add_field(name="Change",  value=_fmt_pct(pct),                inline=True)
        embed.add_field(name="Trend",   value=trend.title(),                inline=True)
        embed.add_field(name="24h High",value=f"{high:,.2f} HC",            inline=True)
        embed.add_field(name="24h Low", value=f"{low:,.2f} HC",             inline=True)
        embed.add_field(name="Sector",  value=s.get("sector", "—"),         inline=True)
        embed.add_field(name="Chart",   value=f"`{chart}`",                 inline=False)
        embed.set_footer(text="Use ,buy SYMBOL qty · ,sell SYMBOL qty")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  BUY
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    async def buy(self, ctx, symbol: str = None, qty: int = None):
        """
        Buy shares of a stock.
        Usage: ,buy HAPPY 10
        """
        if not symbol or not qty:
            return await ctx.reply("Usage: `,buy <SYMBOL> <quantity>`\nExample: `,buy HAPPY 10`")
        if qty <= 0:
            return await ctx.reply("Quantity must be at least 1.")

        s = await get_stock(symbol)
        if not s:
            return await ctx.reply(f"Stock `{symbol.upper()}` not found. Use `,market` to see all.")

        total_cost = round(s["price"] * qty, 2)

        # Check wallet (global economy)
        acc = await db["economy_global"].find_one({"user_id": str(ctx.author.id)})
        wallet = acc["wallet"] if acc else 0
        if total_cost > wallet:
            return await ctx.reply(
                f"Not enough HC.\n"
                f"Cost: **{total_cost:,.2f} HC** | Wallet: **{wallet:,} HC**"
            )

        # Deduct from wallet
        await db["economy_global"].update_one(
            {"user_id": str(ctx.author.id)},
            {"$inc": {"wallet": -int(total_cost)}},
            upsert=True
        )

        # Add to portfolio
        sym = s["symbol"]
        await portfolio_col.update_one(
            {"user_id": str(ctx.author.id)},
            {
                "$inc": {
                    f"holdings.{sym}":  qty,
                    f"invested.{sym}":  total_cost,
                }
            },
            upsert=True
        )

        # Update stock volume
        await market_col.update_one(
            {"symbol": sym}, {"$inc": {"volume": qty}}
        )

        embed = discord.Embed(
            title="Purchase Successful",
            color=0x57F287
        )
        embed.add_field(name="Stock",    value=f"{s['name']} (`{sym}`)", inline=True)
        embed.add_field(name="Shares",   value=f"{qty}",                 inline=True)
        embed.add_field(name="Price",    value=f"{s['price']:,.2f} HC",  inline=True)
        embed.add_field(name="Total",    value=f"**{total_cost:,.2f} HC**", inline=True)
        embed.add_field(name="Wallet",   value=f"{wallet - total_cost:,.2f} HC", inline=True)
        embed.set_footer(text="Use ,portfolio to track your investments · ,sell to cash out")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  SELL
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    async def sell(self, ctx, symbol: str = None, qty: str = None):
        """
        Sell shares of a stock.
        Usage: ,sell HAPPY 5   or   ,sell HAPPY all
        """
        if not symbol or not qty:
            return await ctx.reply("Usage: `,sell <SYMBOL> <quantity/all>`\nExample: `,sell HAPPY all`")

        s = await get_stock(symbol)
        if not s:
            return await ctx.reply(f"Stock `{symbol.upper()}` not found.")

        portfolio = await get_portfolio(ctx.author.id)
        sym       = s["symbol"]
        owned     = portfolio.get("holdings", {}).get(sym, 0)

        if owned <= 0:
            return await ctx.reply(f"You don't own any `{sym}` shares.")

        sell_qty = owned if qty.lower() == "all" else (int(qty) if qty.isdigit() else None)
        if sell_qty is None:
            return await ctx.reply("Enter a valid quantity or `all`.")
        if sell_qty <= 0 or sell_qty > owned:
            return await ctx.reply(f"You own **{owned}** shares of `{sym}`.")

        # Calculate P&L
        total_invested = portfolio.get("invested", {}).get(sym, 0)
        avg_cost       = total_invested / owned if owned else 0
        sell_revenue   = round(s["price"] * sell_qty, 2)
        cost_basis     = round(avg_cost * sell_qty, 2)
        profit         = round(sell_revenue - cost_basis, 2)

        # Update portfolio
        new_qty        = owned - sell_qty
        new_invested   = total_invested - cost_basis

        if new_qty == 0:
            await portfolio_col.update_one(
                {"user_id": str(ctx.author.id)},
                {"$unset": {f"holdings.{sym}": "", f"invested.{sym}": ""}}
            )
        else:
            await portfolio_col.update_one(
                {"user_id": str(ctx.author.id)},
                {"$set": {
                    f"holdings.{sym}": new_qty,
                    f"invested.{sym}": max(0, new_invested),
                }}
            )

        # Add to wallet
        await db["economy_global"].update_one(
            {"user_id": str(ctx.author.id)},
            {"$inc": {"wallet": int(sell_revenue)}},
            upsert=True
        )

        color = 0x57F287 if profit >= 0 else 0xED4245
        embed = discord.Embed(title="Sale Complete", color=color)
        embed.add_field(name="Stock",     value=f"{s['name']} (`{sym}`)",  inline=True)
        embed.add_field(name="Shares",    value=f"{sell_qty}",             inline=True)
        embed.add_field(name="Price",     value=f"{s['price']:,.2f} HC",   inline=True)
        embed.add_field(name="Revenue",   value=f"**{sell_revenue:,.2f} HC**", inline=True)
        embed.add_field(
            name="Profit / Loss",
            value=f"{'▲' if profit >= 0 else '▼'} **{profit:,.2f} HC**",
            inline=True
        )
        if new_qty > 0:
            embed.add_field(name="Still Holding", value=f"{new_qty} shares", inline=True)
        embed.set_footer(text="Use ,portfolio to see your full holdings")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  PORTFOLIO
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["port", "holdings", "invest"])
    async def portfolio(self, ctx, member: discord.Member = None):
        """View your investment portfolio."""
        member    = member or ctx.author
        portfolio = await get_portfolio(member.id)
        holdings  = portfolio.get("holdings", {})

        if not holdings:
            return await ctx.reply(
                embed=discord.Embed(
                    description="No investments yet. Use `,buy <SYMBOL> <qty>` to get started.",
                    color=0x2B2D31
                )
            )

        total_value    = 0
        total_invested = 0
        lines          = []

        for sym, qty in holdings.items():
            if qty <= 0:
                continue
            s           = await get_stock(sym)
            if not s:
                continue
            cur_value   = round(s["price"] * qty, 2)
            invested    = portfolio.get("invested", {}).get(sym, 0)
            pnl         = round(cur_value - invested, 2)
            pnl_str     = f"{'▲' if pnl >= 0 else '▼'} {abs(pnl):,.2f}"
            total_value    += cur_value
            total_invested += invested
            lines.append(
                f"`{sym}` x{qty} — **{cur_value:,.2f} HC** ({pnl_str} HC)"
            )

        total_pnl   = round(total_value - total_invested, 2)
        color       = _pct_color(total_pnl)

        embed = discord.Embed(
            title=f"{member.display_name}'s Portfolio",
            description="\n".join(lines),
            color=color
        )
        embed.add_field(name="Total Value",    value=f"**{total_value:,.2f} HC**",    inline=True)
        embed.add_field(name="Total Invested", value=f"{total_invested:,.2f} HC",     inline=True)
        embed.add_field(
            name="Total P&L",
            value=f"{'▲' if total_pnl >= 0 else '▼'} **{abs(total_pnl):,.2f} HC**",
            inline=True
        )
        embed.set_footer(text="Use ,sell <SYMBOL> all to cash out · ,market to see prices")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  TOP INVESTORS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["investors", "topinvest"])
    async def investlist(self, ctx):
        """See the top investors by portfolio value."""
        portfolios = await portfolio_col.find({}).to_list(100)
        if not portfolios:
            return await ctx.reply("No investors yet.")

        # Calculate each user's portfolio value
        valuations = []
        stocks_cache = {}
        for p in portfolios:
            holdings = p.get("holdings", {})
            if not holdings:
                continue
            total = 0
            for sym, qty in holdings.items():
                if sym not in stocks_cache:
                    stocks_cache[sym] = await get_stock(sym)
                s = stocks_cache[sym]
                if s and qty > 0:
                    total += s["price"] * qty
            if total > 0:
                valuations.append((p["user_id"], total))

        if not valuations:
            return await ctx.reply("No active portfolios yet.")

        valuations.sort(key=lambda x: x[1], reverse=True)
        medals = ["🥇","🥈","🥉"]
        lines  = []

        for i, (uid, val) in enumerate(valuations[:10], 1):
            try:
                user = await self.bot.fetch_user(int(uid))
                name = user.display_name
            except:
                name = f"User {uid}"
            pos = medals[i-1] if i <= 3 else f"`{i}.`"
            lines.append(f"{pos} **{name}** — {val:,.2f} HC")

        embed = discord.Embed(
            title="Top Investors",
            description="\n".join(lines),
            color=0xF0C040
        )
        embed.set_footer(text="Sorted by current portfolio value")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  OWNER CONTROLS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_owner()
    async def setprice(self, ctx, symbol: str = None, price: float = None):
        """Manually set a stock price (owner only)."""
        if not symbol or not price:
            return await ctx.reply("Usage: `,setprice <SYMBOL> <price>`")
        s = await get_stock(symbol)
        if not s:
            return await ctx.reply(f"Stock `{symbol.upper()}` not found.")
        await market_col.update_one(
            {"symbol": symbol.upper()},
            {"$set": {"price": price, "change_pct": 0.0}}
        )
        await ctx.reply(f"`{symbol.upper()}` price set to **{price:,.2f} HC**.")

    @commands.command()
    @ctx_owner()
    async def settrend(self, ctx, symbol: str = None, trend: str = None):
        """Set a stock's trend: bull/bear/neutral (owner only)."""
        if not symbol or trend not in ("bull","bear","neutral"):
            return await ctx.reply("Usage: `,settrend <SYMBOL> bull/bear/neutral`")
        await market_col.update_one(
            {"symbol": symbol.upper()},
            {"$set": {"trend": trend}}
        )
        await ctx.reply(f"`{symbol.upper()}` trend set to **{trend}** {TREND_EMOJI[trend]}.")

    @commands.command()
    @ctx_owner()
    async def addstock(self, ctx, symbol: str = None, price: float = None, *, name: str = None):
        """Add a new stock to the market (owner only)."""
        if not symbol or not price or not name:
            return await ctx.reply("Usage: `,addstock <SYMBOL> <price> <Company Name>`")
        if await get_stock(symbol):
            return await ctx.reply(f"`{symbol.upper()}` already exists.")
        await market_col.insert_one({
            "symbol":     symbol.upper(),
            "name":       name,
            "price":      price,
            "change_pct": 0.0,
            "trend":      "neutral",
            "history":    [price],
            "volume":     0,
            "sector":     "Custom",
        })
        await ctx.reply(f"Stock `{symbol.upper()}` — **{name}** added at **{price:,.2f} HC**.")


async def setup(bot):
    await bot.add_cog(Invest(bot))