import json
import logging
import os

from browser_use.browser.browser import Browser
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import BrowserContext as PlaywrightBrowserContext

logger = logging.getLogger(__name__)


class CustomBrowserContext(BrowserContext):
    def __init__(
        self,
        browser: "Browser",
        config: BrowserContextConfig = BrowserContextConfig()
    ):
        super(CustomBrowserContext, self).__init__(browser=browser, config=config)

    async def _initialize_session(self):
        """Инициализация сессии. При CDP-подключении переиспользует открытые вкладки."""
        from browser_use.browser.context import BrowserSession
        from browser_use.dom.views import DOMElementNode
        from browser_use.browser.views import BrowserState

        playwright_browser = await self.browser.get_playwright_browser()
        context = await self._create_context(playwright_browser)
        self._add_new_page_listener(context)

        # При CDP-подключении берём существующую страницу вместо новой
        if self.browser.config.cdp_url and context.pages:
            # Берём последнюю активную вкладку (не about:blank если есть другие)
            page = context.pages[-1]
            for p in context.pages:
                if p.url not in ("about:blank", ""):
                    page = p
                    break
        else:
            page = await context.new_page()

        initial_state = BrowserState(
            element_tree=DOMElementNode(
                tag_name='root',
                is_visible=True,
                parent=None,
                xpath='',
                attributes={},
                children=[],
            ),
            selector_map={},
            url=page.url,
            title='',
            screenshot=None,
            tabs=[],
            pixels_above=0,
            pixels_below=0,
        )

        self.session = BrowserSession(
            context=context,
            current_page=page,
            cached_state=initial_state,
        )
        return self.session