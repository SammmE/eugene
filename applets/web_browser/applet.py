from __future__ import annotations
from typing import Any
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition
from duckduckgo_search import DDGS
import requests
from bs4 import BeautifulSoup

class WebBrowserApplet(AppletBase):
    name = "web_browser"
    description = "Allows Eugene to search the web and fetch url content."
    load = "lazy"
    inject = "never"
    can_disable = True

    class Config:
        fields = {}

    async def on_load(self) -> None:
        self.logger.info("Web Browser applet loaded")

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_web",
                description="Search the web for a given query.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 5}
                    },
                    "required": ["query"]
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="fetch_url_content",
                description="Fetch the text content of a given URL.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"}
                    },
                    "required": ["url"]
                },
                applet_name=self.name,
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "search_web":
            query = arguments.get("query")
            max_results = arguments.get("max_results", 5)
            
            try:
                with DDGS() as ddgs:
                    results = [r for r in ddgs.text(query, max_results=max_results)]
                    if not results:
                        return "No results found."
                    
                    output = "Search Results:\n\n"
                    for i, res in enumerate(results):
                        title = res.get('title', 'No Title')
                        body = res.get('body', 'No snippet')
                        href = res.get('href', '')
                        output += f"{i+1}. {title}\nURL: {href}\nSnippet: {body}\n\n"
                    return output
            except Exception as e:
                return f"Search error: {e}"
                
        elif name == "fetch_url_content":
            url = arguments.get("url")
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.extract()
                    
                text = soup.get_text(separator='\n', strip=True)
                
                # truncate to max 4000 characters
                if len(text) > 4000:
                    text = text[:4000] + "\n...[Content Truncated]..."
                    
                return f"Content of {url}:\n\n{text}"
            except Exception as e:
                return f"Error fetching URL: {e}"
                
        raise ValueError(f"Unknown tool: {name}")
