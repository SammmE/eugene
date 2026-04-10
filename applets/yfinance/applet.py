from __future__ import annotations

from typing import Any

from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition


class YFinanceApplet(AppletBase):
    name = "yfinance"
    description = "Fetch Yahoo Finance market data, price history, and company metadata."
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "default_history_period": FieldSpec(
                default="1mo",
                description="Default history period for price-history lookups, for example 5d, 1mo, 6mo, 1y.",
            ),
            "default_history_interval": FieldSpec(
                default="1d",
                description="Default history interval for price-history lookups, for example 1m, 5m, 1h, 1d, 1wk.",
            ),
            "default_news_count": FieldSpec(
                default=5,
                description="Default number of Yahoo Finance news items to return.",
            ),
            "summary_max_chars": FieldSpec(
                default=1200,
                description="Maximum number of characters to keep from long company summaries.",
            ),
        }

    async def on_load(self) -> None:
        self.logger.info("YFinance applet loaded")

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="get_yfinance_quote",
                description="Fetch a Yahoo Finance quote snapshot for a ticker symbol.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Ticker symbol such as AAPL, MSFT, SPY, BTC-USD, or EURUSD=X.",
                        }
                    },
                    "required": ["symbol"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_yfinance_history",
                description="Fetch Yahoo Finance historical OHLCV data for a ticker symbol.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Ticker symbol such as AAPL, MSFT, SPY, BTC-USD, or EURUSD=X.",
                        },
                        "period": {
                            "type": "string",
                            "description": "History period such as 1d, 5d, 1mo, 6mo, 1y, 5y, max.",
                        },
                        "interval": {
                            "type": "string",
                            "description": "Sampling interval such as 1m, 5m, 1h, 1d, 1wk, 1mo.",
                        },
                        "include_actions": {
                            "type": "boolean",
                            "description": "When true, include dividends and stock splits when available.",
                            "default": False,
                        },
                    },
                    "required": ["symbol"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_yfinance_company_info",
                description="Fetch Yahoo Finance company and market metadata for a ticker symbol.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Ticker symbol such as AAPL, MSFT, SPY, BTC-USD, or EURUSD=X.",
                        }
                    },
                    "required": ["symbol"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_yfinance_news",
                description="Fetch recent Yahoo Finance news items for a ticker symbol.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Ticker symbol such as AAPL, MSFT, SPY, BTC-USD, or EURUSD=X.",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Maximum number of news items to return.",
                        },
                    },
                    "required": ["symbol"],
                },
                applet_name=self.name,
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "get_yfinance_quote":
            return self._get_quote(arguments)
        if name == "get_yfinance_history":
            return self._get_history(arguments)
        if name == "get_yfinance_company_info":
            return self._get_company_info(arguments)
        if name == "get_yfinance_news":
            return self._get_news(arguments)
        raise ValueError(f"Unknown tool: {name}")

    def _get_quote(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ticker = self._ticker(arguments["symbol"])
        info = self._safe_dict(getattr(ticker, "fast_info", {}))
        base_info = self._safe_dict(getattr(ticker, "info", {}))

        return {
            "symbol": self._symbol(arguments["symbol"]),
            "currency": info.get("currency") or base_info.get("currency"),
            "exchange": info.get("exchange") or base_info.get("exchange"),
            "timezone": info.get("timezone") or base_info.get("exchangeTimezoneName"),
            "last_price": self._coalesce(info, base_info, "lastPrice", "currentPrice", "regularMarketPrice"),
            "previous_close": self._coalesce(info, base_info, "previousClose", "regularMarketPreviousClose"),
            "open": self._coalesce(info, base_info, "open", "regularMarketOpen"),
            "day_high": self._coalesce(info, base_info, "dayHigh", "regularMarketDayHigh"),
            "day_low": self._coalesce(info, base_info, "dayLow", "regularMarketDayLow"),
            "volume": self._coalesce(info, base_info, "lastVolume", "volume", "regularMarketVolume"),
            "market_cap": self._coalesce(info, base_info, "marketCap"),
            "fifty_day_average": self._coalesce(info, base_info, "fiftyDayAverage"),
            "two_hundred_day_average": self._coalesce(info, base_info, "twoHundredDayAverage"),
            "year_high": self._coalesce(info, base_info, "yearHigh", "fiftyTwoWeekHigh"),
            "year_low": self._coalesce(info, base_info, "yearLow", "fiftyTwoWeekLow"),
            "quote_type": base_info.get("quoteType"),
            "short_name": base_info.get("shortName"),
            "long_name": base_info.get("longName"),
        }

    def _get_history(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ticker = self._ticker(arguments["symbol"])
        period = str(arguments.get("period") or self.config.get("default_history_period", "1mo"))
        interval = str(arguments.get("interval") or self.config.get("default_history_interval", "1d"))
        include_actions = bool(arguments.get("include_actions", False))

        history = ticker.history(period=period, interval=interval, actions=include_actions, auto_adjust=False)
        if history is None or history.empty:
            return {
                "symbol": self._symbol(arguments["symbol"]),
                "period": period,
                "interval": interval,
                "rows": [],
                "count": 0,
            }

        history = history.reset_index()
        rows: list[dict[str, Any]] = []
        for _, row in history.iterrows():
            item: dict[str, Any] = {}
            for key, value in row.items():
                item[str(key)] = self._normalize_value(value)
            rows.append(item)

        return {
            "symbol": self._symbol(arguments["symbol"]),
            "period": period,
            "interval": interval,
            "count": len(rows),
            "rows": rows,
        }

    def _get_company_info(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ticker = self._ticker(arguments["symbol"])
        info = self._safe_dict(getattr(ticker, "info", {}))
        summary_max_chars = int(self.config.get("summary_max_chars", 1200) or 1200)
        summary = str(info.get("longBusinessSummary") or "")
        if len(summary) > summary_max_chars:
            summary = summary[:summary_max_chars].rstrip() + "..."

        return {
            "symbol": self._symbol(arguments["symbol"]),
            "short_name": info.get("shortName"),
            "long_name": info.get("longName"),
            "quote_type": info.get("quoteType"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "website": info.get("website"),
            "country": info.get("country"),
            "city": info.get("city"),
            "full_time_employees": info.get("fullTimeEmployees"),
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "currency": info.get("currency"),
            "exchange": info.get("exchange"),
            "business_summary": summary,
        }

    def _get_news(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ticker = self._ticker(arguments["symbol"])
        requested = arguments.get("count")
        fallback = int(self.config.get("default_news_count", 5) or 5)
        count = max(1, min(int(requested if requested not in (None, "") else fallback), 20))

        news_items = getattr(ticker, "news", None) or []
        normalized: list[dict[str, Any]] = []
        for item in news_items[:count]:
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, dict):
                normalized.append(
                    {
                        "title": content.get("title"),
                        "summary": content.get("summary"),
                        "publisher": content.get("provider", {}).get("displayName") if isinstance(content.get("provider"), dict) else None,
                        "published_at": content.get("pubDate"),
                        "url": content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else None,
                    }
                )
                continue
            normalized.append(self._normalize_generic(item))

        return {
            "symbol": self._symbol(arguments["symbol"]),
            "count": len(normalized),
            "items": normalized,
        }

    def _ticker(self, symbol: Any) -> Any:
        try:
            import yfinance as yf  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "The yfinance package is not installed. Install project dependencies before using the yfinance applet."
            ) from exc

        normalized = self._symbol(symbol)
        if not normalized:
            raise RuntimeError("A ticker symbol is required.")
        return yf.Ticker(normalized)

    def _symbol(self, value: Any) -> str:
        return str(value or "").strip().upper()

    def _safe_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            return dict(value)
        except Exception:
            return {}

    def _coalesce(self, first: dict[str, Any], second: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in first and first.get(key) is not None:
                return self._normalize_value(first.get(key))
            if key in second and second.get(key) is not None:
                return self._normalize_value(second.get(key))
        return None

    def _normalize_generic(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._normalize_generic(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._normalize_generic(item) for item in value]
        return self._normalize_value(value)

    def _normalize_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            try:
                return isoformat()
            except Exception:
                pass

        item = getattr(value, "item", None)
        if callable(item):
            try:
                return item()
            except Exception:
                pass

        if isinstance(value, dict):
            return {str(key): self._normalize_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._normalize_value(item) for item in value]
        return repr(value)
